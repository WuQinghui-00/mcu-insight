# The same shape as a normal ESP-IDF project: the telemetry agent is vendored
# into components/ by tools/sync_firmware.py, which is why that directory is
# not committed here.
#
# The guard is the point. Unconditional, a build attempted before step 1 stops
# with "Directory specified in EXTRA_COMPONENT_DIRS doesn't exist", which says
# nothing about the step that was skipped. Guarded, the build gets far enough
# to report the component it cannot find, which names it.
cmake_minimum_required(VERSION 3.16)

if(EXISTS "${CMAKE_CURRENT_LIST_DIR}/components")
    set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/components")
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(leaky_sensor_node)