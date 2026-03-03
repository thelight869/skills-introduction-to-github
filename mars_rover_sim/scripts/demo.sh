#!/usr/bin/env bash
set -e

if [ -z "${ROS_DISTRO:-}" ]; then
  echo "[demo] ROS environment not sourced. Please source /opt/ros/<distro>/setup.bash and catkin workspace setup.bash"
fi

roslaunch mars_rover_sim demo.launch "$@"
