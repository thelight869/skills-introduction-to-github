# mars_rover_sim (ROS1)

这是一个完整的 ROS1 + Gazebo 仿真项目，包含三部分：

1. **类火星地表环境**（障碍物 + 不同颜色和尺寸岩石 + 岩石屏障）。
2. **机器小车模型**（差速底盘 + IMU + 激光雷达 + RGB相机 + 深度相机 + GPS + 超声测距）。
3. **路径规划与避障**（栅格 A* 全局局部路径 + 近场局部避障融合控制）。

## 目录结构

- `worlds/mars_surface.world.xacro`：可编辑 xacro 世界描述。
- `worlds/mars_surface.world`：Gazebo 直接加载世界。
- `urdf/rover.urdf.xacro`：小车模型与传感器定义。
- `scripts/local_planner.py`：A* + 局部避障融合规划器。
- `launch/sim.launch`：仿真主启动。
- `launch/demo.launch`：一键 Demo（仿真 + RViz）。
- `scripts/demo.sh`：一键启动脚本。
- `config/mars_demo.rviz`：RViz 预设（激光/路径/模型显示）。
- `launch/orbslam.launch`：ORB-SLAM2 启动封装。

## 运行要求

- ROS1（推荐 Noetic）
- `gazebo_ros`
- `xacro`
- `orb_slam2_ros`（可选，启用 ORB-SLAM 时需要）

## 编译

```bash
cd ~/catkin_ws/src
# 将 mars_rover_sim 放到这里
cd ~/catkin_ws
catkin_make
source devel/setup.bash
```

## 一键 Demo（推荐）

```bash
rosrun mars_rover_sim demo.sh
```

可追加参数，例如：

```bash
rosrun mars_rover_sim demo.sh enable_orbslam:=false x:=0.0 y:=-2.0
```

## 常规启动

```bash
roslaunch mars_rover_sim sim.launch
```

```bash
roslaunch mars_rover_sim demo.launch
```

## 关键话题

- 控制：`/cmd_vel`
- 里程计：`/odom`
- IMU：`/imu/data`
- 激光：`/scan`
- RGB 相机：`/rgb/image_raw`
- 深度图：`/depth/depth/image_raw`
- 点云：`/depth/depth/points`
- GPS：`/gps/fix`
- 超声测距：`/ultrasonic/range`
- 规划路径：`/planner/local_path`

## 规划器说明

- 将激光数据投影到局部栅格地图并膨胀障碍。
- 在 `base_link` 局部地图上用 A* 搜索到局部目标路径。
- 融合近场障碍排斥转向，提升窄通道/贴障绕行稳定性。
- 目标点通过 `sim.launch` 参数设置（默认 `goal_x=10.0 goal_y=0.0`）。
