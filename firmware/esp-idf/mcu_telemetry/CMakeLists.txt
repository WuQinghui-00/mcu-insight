idf_component_register(
    SRCS "mcu_telemetry.c"
    INCLUDE_DIRS "include"
    REQUIRES freertos esp_system heap esp_timer
)
