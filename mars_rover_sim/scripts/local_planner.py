#!/usr/bin/env python3
import heapq
import math

import rospy
from geometry_msgs.msg import PoseStamped, Twist
from nav_msgs.msg import Odometry, Path
from sensor_msgs.msg import LaserScan


class HybridAStarLocalPlanner:
    """Grid A* + local obstacle-avoidance fusion planner."""

    def __init__(self):
        self.goal_x = rospy.get_param("~goal_x", 10.0)
        self.goal_y = rospy.get_param("~goal_y", 0.0)
        self.safe_distance = rospy.get_param("~safe_distance", 1.0)
        self.max_linear = rospy.get_param("~max_linear", 0.5)
        self.max_angular = rospy.get_param("~max_angular", 1.2)

        self.grid_size = rospy.get_param("~grid_size", 80)
        self.grid_res = rospy.get_param("~grid_resolution", 0.20)
        self.local_range = self.grid_size * self.grid_res / 2.0
        self.inflation_cells = rospy.get_param("~inflation_cells", 2)

        self.pose_x = 0.0
        self.pose_y = 0.0
        self.yaw = 0.0
        self.scan_msg = None

        self.cmd_pub = rospy.Publisher("/cmd_vel", Twist, queue_size=10)
        self.path_pub = rospy.Publisher("/planner/local_path", Path, queue_size=1)

        rospy.Subscriber("/odom", Odometry, self.odom_cb, queue_size=1)
        rospy.Subscriber("/scan", LaserScan, self.scan_cb, queue_size=1)

    def odom_cb(self, msg):
        q = msg.pose.pose.orientation
        siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        self.yaw = math.atan2(siny_cosp, cosy_cosp)
        self.pose_x = msg.pose.pose.position.x
        self.pose_y = msg.pose.pose.position.y

    def scan_cb(self, msg):
        self.scan_msg = msg

    def _world_to_grid(self, x, y):
        gx = int((x + self.local_range) / self.grid_res)
        gy = int((y + self.local_range) / self.grid_res)
        return gx, gy

    def _grid_to_world(self, gx, gy):
        x = (gx + 0.5) * self.grid_res - self.local_range
        y = (gy + 0.5) * self.grid_res - self.local_range
        return x, y

    def _build_grid(self):
        grid = [[0 for _ in range(self.grid_size)] for _ in range(self.grid_size)]
        if self.scan_msg is None:
            return grid

        scan = self.scan_msg
        for i, r in enumerate(scan.ranges):
            if not math.isfinite(r):
                continue
            if r < scan.range_min or r > min(scan.range_max, self.local_range):
                continue

            a = scan.angle_min + i * scan.angle_increment
            x = r * math.cos(a)
            y = r * math.sin(a)
            gx, gy = self._world_to_grid(x, y)
            if 0 <= gx < self.grid_size and 0 <= gy < self.grid_size:
                for dx in range(-self.inflation_cells, self.inflation_cells + 1):
                    for dy in range(-self.inflation_cells, self.inflation_cells + 1):
                        nx, ny = gx + dx, gy + dy
                        if 0 <= nx < self.grid_size and 0 <= ny < self.grid_size:
                            grid[ny][nx] = 1
        return grid

    def _astar(self, grid, start, goal):
        motions = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]
        open_set = []
        heapq.heappush(open_set, (0.0, start))

        g_cost = {start: 0.0}
        came = {}

        def h(a, b):
            return math.hypot(a[0] - b[0], a[1] - b[1])

        while open_set:
            _, cur = heapq.heappop(open_set)
            if cur == goal:
                path = [cur]
                while cur in came:
                    cur = came[cur]
                    path.append(cur)
                path.reverse()
                return path

            for mx, my in motions:
                nxt = (cur[0] + mx, cur[1] + my)
                if not (0 <= nxt[0] < self.grid_size and 0 <= nxt[1] < self.grid_size):
                    continue
                if grid[nxt[1]][nxt[0]] == 1:
                    continue

                step = math.hypot(mx, my)
                ng = g_cost[cur] + step
                if nxt not in g_cost or ng < g_cost[nxt]:
                    g_cost[nxt] = ng
                    came[nxt] = cur
                    f = ng + h(nxt, goal)
                    heapq.heappush(open_set, (f, nxt))
        return []

    def _publish_path(self, cells):
        path_msg = Path()
        path_msg.header.stamp = rospy.Time.now()
        path_msg.header.frame_id = "base_link"
        for gx, gy in cells:
            x, y = self._grid_to_world(gx, gy)
            p = PoseStamped()
            p.header = path_msg.header
            p.pose.position.x = x
            p.pose.position.y = y
            p.pose.orientation.w = 1.0
            path_msg.poses.append(p)
        self.path_pub.publish(path_msg)

    def _obstacle_repulsion(self):
        if self.scan_msg is None:
            return 0.0
        turn = 0.0
        for i, r in enumerate(self.scan_msg.ranges):
            if not math.isfinite(r):
                continue
            if r > self.safe_distance * 1.6:
                continue
            a = self.scan_msg.angle_min + i * self.scan_msg.angle_increment
            weight = max(0.0, (self.safe_distance * 1.6 - r)) / (self.safe_distance * 1.6)
            turn += -math.sin(a) * weight
        return max(-0.8, min(0.8, turn))

    def run(self):
        rate = rospy.Rate(10)
        while not rospy.is_shutdown():
            cmd = Twist()

            dx_w = self.goal_x - self.pose_x
            dy_w = self.goal_y - self.pose_y
            goal_dist = math.hypot(dx_w, dy_w)
            if goal_dist < 0.35:
                self.cmd_pub.publish(Twist())
                rospy.loginfo_throttle(2.0, "Goal reached.")
                rate.sleep()
                continue

            # Goal in base_link frame
            gx_b = math.cos(-self.yaw) * dx_w - math.sin(-self.yaw) * dy_w
            gy_b = math.sin(-self.yaw) * dx_w + math.cos(-self.yaw) * dy_w

            grid = self._build_grid()
            start = self._world_to_grid(0.0, 0.0)
            clipped_gx = max(-self.local_range + 0.1, min(self.local_range - 0.1, gx_b))
            clipped_gy = max(-self.local_range + 0.1, min(self.local_range - 0.1, gy_b))
            goal = self._world_to_grid(clipped_gx, clipped_gy)

            path_cells = self._astar(grid, start, goal)
            if path_cells:
                self._publish_path(path_cells)
                look_idx = min(len(path_cells) - 1, 8)
                lx, ly = self._grid_to_world(path_cells[look_idx][0], path_cells[look_idx][1])
                heading = math.atan2(ly, lx)
                path_turn = max(-self.max_angular, min(self.max_angular, 1.8 * heading))
                repulsion_turn = self._obstacle_repulsion()

                cmd.angular.z = max(-self.max_angular, min(self.max_angular, path_turn + 0.7 * repulsion_turn))
                front_penalty = min(1.0, abs(cmd.angular.z) / self.max_angular)
                cmd.linear.x = self.max_linear * (1.0 - 0.6 * front_penalty)
            else:
                # Fallback: rotate slowly to re-search path
                cmd.linear.x = 0.0
                cmd.angular.z = 0.5
                rospy.logwarn_throttle(1.0, "A* local path not found, rotating to recover.")

            self.cmd_pub.publish(cmd)
            rate.sleep()


if __name__ == "__main__":
    rospy.init_node("hybrid_astar_local_planner")
    planner = HybridAStarLocalPlanner()
    planner.run()
