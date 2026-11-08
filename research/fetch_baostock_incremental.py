from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterable

import baostock as bs
import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

DB_PATH = Path(os.environ.get("DB_PATH", "ashare_pit.duckdb"))
OUT_DIR = Path(os.environ.get("INCREMENTAL_DIR", "incremental"))
START_DATE = os.environ.get("INCREMENTAL_START", "2026-09-25")
END_DATE = os.environ.get("INCREMENTAL_END", "2026-11-06")
OUT_DIR.mkdir(parents=True, exist_ok=True)

FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,adjustflag,"
    "turn,tradestatus,pctChg,isST"
)

BAR_SCHEMA = pa.schema(
    [
        ("security_id", pa.string()),
        ("trade_date", pa.date32()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("preclose", pa.float64()),
        ("volume", pa.int64()),
        ("amount", pa.float64()),
        ("adjustflag", pa.string()),
        ("turn", pa.float64()),
        ("tradestatus", pa.int32()),
        ("pctChg", pa.float64()),
        ("isST", pa.int32()),
    ]
)


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def fetch_rows(result) -> list[list[str]]:
    rows: list[list[str]] = []
    while result.error_code == "0" and result.next():
        rows.append(result.get_row_data())
    return rows


def to_float(value: str) -> float | None:
    value = str(value).strip()
    return None if value == "" else float(value)


def to_int(value: str) -> int | None:
    value = str(value).strip()
    return None if value == "" else int(float(value))


def is_a_share_code(code: str) -> bool:
    code = str(code).lower()
    if code.startswith("sh."):
        symbol = code[3:]
        return symbol.startswith(("600", "601", "603", "605", "688", "689"))
    if code.startswith("sz."):
        symbol = code[3:]
        return symbol.startswith(("000", "001", "002", "003", "300", "301"))
    return False


def security_id_from_code(code: str) -> str:
    code = str(code).lower()
    exchange = "SSE" if code.startswith("sh.") else "SZSE"
    return f"CN:{exchange}:{code[3:]}"


def normalize_bar(row: list[str], security_id: str) -> dict:
    values = dict(zip(FIELDS.split(","), row))
    return {
        "security_id": security_id,
        "trade_date": pd.Timestamp(values["date"]).date(),
        "open": to_float(values["open"]),
        "high": to_float(values["high"]),
        "low": to_float(values["low"]),
        "close": to_float(values["close"]),
        "preclose": to_float(values["preclose"]),
        "volume": to_int(values["volume"]),
        "amount": to_float(values["amount"]),
        "adjustflag": values["adjustflag"] or "3",
        "turn": to_float(values["turn"]),
        "tradestatus": to_int(values["tradestatus"]),
        "pctChg": to_float(values["pctChg"]),
        "isST": to_int(values["isST"]),
    }


def write_buffer(writer: pq.ParquetWriter, buffer: list[dict]) -> int:
    if not buffer:
        return 0
    frame = pd.DataFrame(buffer)
    table = pa.Table.from_pandas(frame, schema=BAR_SCHEMA, preserve_index=False, safe=False)
    writer.write_table(table, row_group_size=100_000)
    count = len(buffer)
    buffer.clear()
    return count


def main() -> None:
    log(f"Reading baseline universe from {DB_PATH}")
    con = duckdb.connect(str(DB_PATH), read_only=True)
    master = con.execute(
        """
        SELECT security_id, exchange, symbol, baostock_code, name,
               list_date, delist_date, status
        FROM security_master
        ORDER BY security_id
        """
    ).fetchdf()
    con.close()

    baseline_codes = set(master["baostock_code"].astype(str))
    eligible_baseline = master[
        master["delist_date"].isna()
        | (pd.to_datetime(master["delist_date"]) >= pd.Timestamp(START_DATE))
    ].copy()

    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_code} {login.error_msg}")

    try:
        trade_rs = bs.query_trade_dates(start_date=START_DATE, end_date=END_DATE)
        if trade_rs.error_code != "0":
            raise RuntimeError(f"query_trade_dates failed: {trade_rs.error_code} {trade_rs.error_msg}")
        trade_rows = fetch_rows(trade_rs)
        trade_dates = pd.DataFrame(trade_rows, columns=trade_rs.fields)
        trade_dates.to_csv(OUT_DIR / "trade_dates.csv", index=False, encoding="utf-8-sig")
        open_dates = trade_dates.loc[trade_dates["is_trading_day"] == "1", "calendar_date"].tolist()
        if not open_dates or open_dates[-1] != END_DATE:
            raise RuntimeError(
                f"BaoStock does not yet expose {END_DATE} as a completed trading day; latest={open_dates[-1] if open_dates else None}"
            )

        basic_rs = bs.query_stock_basic()
        if basic_rs.error_code != "0":
            raise RuntimeError(f"query_stock_basic failed: {basic_rs.error_code} {basic_rs.error_msg}")
        basic_rows = fetch_rows(basic_rs)
        basic = pd.DataFrame(basic_rows, columns=basic_rs.fields)
        current_a = basic[(basic["type"] == "1") & basic["code"].map(is_a_share_code)].copy()
        current_a.to_csv(OUT_DIR / "stock_basic_current.csv", index=False, encoding="utf-8-sig")

        new_current = current_a[~current_a["code"].isin(baseline_codes)].copy()
        candidates: list[dict] = []
        for rec in eligible_baseline.to_dict("records"):
            candidates.append(
                {
                    "code": str(rec["baostock_code"]),
                    "security_id": str(rec["security_id"]),
                    "start_date": START_DATE,
                    "is_new": False,
                }
            )
        for rec in new_current.to_dict("records"):
            ipo = str(rec.get("ipoDate") or "").strip()
            start = max(ipo or "2025-01-01", "2025-01-01")
            candidates.append(
                {
                    "code": str(rec["code"]),
                    "security_id": security_id_from_code(str(rec["code"])),
                    "start_date": start,
                    "is_new": True,
                }
            )

        log(
            f"Incremental candidates={len(candidates):,}; baseline eligible={len(eligible_baseline):,}; new current A-shares={len(new_current):,}"
        )

        parquet_path = OUT_DIR / "incremental_bars.parquet"
        writer = pq.ParquetWriter(parquet_path, BAR_SCHEMA, compression="zstd", use_dictionary=True)
        buffer: list[dict] = []
        total_rows = 0
        successful_queries = 0
        empty_queries = 0
        failures: list[dict] = []

        for index, item in enumerate(candidates, start=1):
            code = item["code"]
            result = None
            for attempt in range(1, 6):
                result = bs.query_history_k_data_plus(
                    code,
                    FIELDS,
                    start_date=item["start_date"],
                    end_date=END_DATE,
                    frequency="d",
                    adjustflag="3",
                )
                if result.error_code == "0":
                    break
                if attempt < 5:
                    time.sleep(1.5 * attempt)
            if result is None or result.error_code != "0":
                failures.append(
                    {
                        "code": code,
                        "error_code": getattr(result, "error_code", "NO_RESULT"),
                        "error_msg": getattr(result, "error_msg", "No result object"),
                    }
                )
                continue

            rows = fetch_rows(result)
            successful_queries += 1
            if not rows:
                empty_queries += 1
            for row in rows:
                buffer.append(normalize_bar(row, item["security_id"]))
            if len(buffer) >= 100_000:
                total_rows += write_buffer(writer, buffer)
            if index % 250 == 0:
                log(
                    f"Queried {index:,}/{len(candidates):,}; rows={total_rows + len(buffer):,}; failures={len(failures)}"
                )
                time.sleep(0.3)

        total_rows += write_buffer(writer, buffer)
        writer.close()

        failures_df = pd.DataFrame(failures)
        failures_df.to_csv(OUT_DIR / "query_failures.csv", index=False, encoding="utf-8-sig")
        if failures:
            raise RuntimeError(f"BaoStock incremental download has {len(failures)} persistent query failures")
        if total_rows == 0:
            raise RuntimeError("BaoStock returned no incremental bars")

        check = duckdb.connect()
        summary = check.execute(
            f"""
            SELECT COUNT(*)::BIGINT AS rows,
                   COUNT(DISTINCT security_id)::BIGINT AS securities,
                   COUNT(DISTINCT trade_date)::BIGINT AS trading_dates,
                   MIN(trade_date) AS first_date,
                   MAX(trade_date) AS last_date,
                   COUNT(DISTINCT CASE WHEN trade_date=DATE '{END_DATE}' AND tradestatus=1 AND volume>0 THEN security_id END)::BIGINT AS traded_on_end
            FROM read_parquet('{parquet_path.as_posix()}')
            """
        ).fetchdf().iloc[0].to_dict()
        check.close()
        summary = {k: (v.isoformat() if hasattr(v, "isoformat") else int(v) if hasattr(v, "item") else v) for k, v in summary.items()}
        summary.update(
            {
                "requested_start": START_DATE,
                "requested_end": END_DATE,
                "expected_open_dates": len(open_dates),
                "candidate_queries": len(candidates),
                "successful_queries": successful_queries,
                "empty_queries": empty_queries,
                "persistent_failures": len(failures),
                "baseline_security_count": len(master),
                "new_current_a_share_count": len(new_current),
            }
        )
        if summary["last_date"] != END_DATE:
            raise RuntimeError(f"Incremental data is stale: latest={summary['last_date']}, expected={END_DATE}")
        if int(summary["traded_on_end"]) < 5000:
            raise RuntimeError(f"Suspiciously low end-date coverage: {summary['traded_on_end']} securities")

        new_master = []
        for rec in new_current.to_dict("records"):
            code = str(rec["code"])
            exchange = "SSE" if code.startswith("sh.") else "SZSE"
            new_master.append(
                {
                    "security_id": security_id_from_code(code),
                    "exchange": exchange,
                    "symbol": code[3:],
                    "baostock_code": code,
                    "name": rec.get("code_name", ""),
                    "list_date": rec.get("ipoDate") or None,
                    "delist_date": rec.get("outDate") or None,
                    "status": int(rec.get("status") or 0),
                }
            )
        pd.DataFrame(new_master).to_csv(OUT_DIR / "new_security_master.csv", index=False, encoding="utf-8-sig")
        (OUT_DIR / "incremental_meta.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        log(json.dumps(summary, ensure_ascii=False))
    finally:
        bs.logout()


if __name__ == "__main__":
    main()
