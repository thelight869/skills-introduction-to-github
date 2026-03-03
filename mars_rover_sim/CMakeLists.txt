cmake_minimum_required(VERSION 3.0.2)
project(mars_rover_sim)

find_package(catkin REQUIRED COMPONENTS
  roscpp
  rospy
  std_msgs
  sensor_msgs
  geometry_msgs
  nav_msgs
  visualization_msgs
  tf2_ros
  xacro
  gazebo_ros
  robot_state_publisher
  joint_state_publisher
)

catkin_package()

catkin_install_python(PROGRAMS
  scripts/local_planner.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)

install(PROGRAMS
  scripts/demo.sh
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)

install(DIRECTORY launch urdf worlds config
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}
)
