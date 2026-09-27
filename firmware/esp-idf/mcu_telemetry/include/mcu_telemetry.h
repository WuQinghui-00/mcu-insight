#pragma once

/*
 * MCU-Insight telemetry agent.
 *
 * Periodically prints one JSON line (schema v1, see docs/telemetry-schema.md)
 * to stdout.  The agent deliberately avoids ESP_LOG for the frame itself: the
 * host collector looks for lines that start with '{', so adding a log prefix
 * would make the frame invisible.
 */

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define MCU_TELEMETRY_SCHEMA_VERSION 1

/* Capacity of the registered task table and the custom metric table. */
#define MCU_TELEMETRY_MAX_TASKS 16
#define MCU_TELEMETRY_MAX_CUSTOM 8

typedef struct {
    /* Required: stable name used to group series on the host. */
    const char *device;
    /* Optional: firmware revision, e.g. a short git hash. */
    const char *firmware;
    /* Report period; 0 uses 5000 ms. */
    uint32_t report_period_ms;
    /* Stack for the telemetry task; 0 uses 4096 bytes. */
    uint32_t stack_size;
    /* Task priority; 0 uses tskIDLE_PRIORITY + 1. */
    UBaseType_t priority;
} mcu_telemetry_config_t;

/*
 * Start the agent.  The config strings are copied, so temporaries are fine.
 */
esp_err_t mcu_telemetry_start(const mcu_telemetry_config_t *config);

/*
 * Add a task to every report.  `name` must match the name passed to
 * xTaskCreate; the agent resolves the handle on every report so that a task
 * which exits later is skipped instead of being dereferenced.  `stack_size` is
 * the size passed to xTaskCreate (the RTOS does not expose it), pass 0 when
 * unknown.
 *
 * When CONFIG_FREERTOS_USE_TRACE_FACILITY is enabled any remaining task is
 * discovered automatically, which is how httpd / mqtt / wifi tasks show up
 * without being registered by hand.
 */
esp_err_t mcu_telemetry_register_task(const char *name, uint32_t stack_size);

/*
 * Attach a project specific integer metric, e.g. loop period in milliseconds
 * or jitter in microseconds.  Values are reported under "custom".
 *
 * Integers only, on purpose: printing floats pulls a large amount of
 * formatting code into the image, and scaled integers cover the same ground.
 */
void mcu_telemetry_set_custom_int(const char *key, int32_t value);

/* Report immediately instead of waiting for the next period. */
void mcu_telemetry_report_now(void);

/*
 * Weak hook: override it in the application to add extra JSON members, for
 * example "\"net\":{\"rssi\":-52}".  `out` must hold a complete fragment
 * without the leading comma; leave it empty to add nothing.
 */
void mcu_telemetry_extra_fields(char *out, size_t size);
