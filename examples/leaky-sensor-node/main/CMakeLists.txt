idf_component_register(SRCS "main.c"
    INCLUDE_DIRS "."
    REQUIRES driver esp_timer freertos mcu_telemetry)

target_compile_options(${COMPONENT_TARGET} PRIVATE -Wno-error)