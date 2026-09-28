/*
 * A deliberately flawed sensor node.
 *
 * Everything here is the shape of an ordinary small firmware, except one
 * thing: sample_task() allocates PAYLOAD_BYTES for every sample and never
 * releases them. One sample every 200 ms is 128 bytes every 200 ms, which is
 * 640 bytes per second, and that is the number the free heap slope should
 * measure. The leak is kept alive by handing the pointer to a volatile, so the
 * optimizer cannot delete the allocation the way it deletes an unused one.
 *
 * The second task is not a bug, but it is tight on purpose: a 1800 byte array
 * on a 3072 byte stack leaves roughly a kilobyte. A rule with a 256 byte floor
 * will not fire, and the trend should still show it.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "mcu_telemetry.h"

#define SAMPLE_PERIOD_MS 200
#define PAYLOAD_BYTES    128     /* allocated per sample, never freed */
#define SAMPLE_STACK     3072
#define FORMAT_STACK     3072
#define FORMAT_SCRATCH   1800    /* large, and on the stack */

static const char *TAG = "sensor";

static volatile int32_t s_raw;
static volatile void *s_retained;      /* what "publishing" keeps */
static int32_t s_alloc_failures;

static int32_t read_sensor(void)
{
    s_raw = (int32_t)((s_raw * 1103515245 + 12345) & 0xffff);
    return s_raw;
}

static void sample_task(void *arg)
{
    (void)arg;
    for (;;) {
        int32_t raw = read_sensor();

        char *payload = malloc(PAYLOAD_BYTES);
        if (payload != NULL) {
            snprintf(payload, PAYLOAD_BYTES, "{\"raw\":%d}", (int)raw);
            s_retained = payload;                  /* published, never released */
            mcu_telemetry_set_custom_int("payload_first_byte", payload[0]);
        } else {
            s_alloc_failures++;
            mcu_telemetry_set_custom_int("alloc_failures", s_alloc_failures);
        }

        mcu_telemetry_set_custom_int("sample_raw", raw);
        vTaskDelay(pdMS_TO_TICKS(SAMPLE_PERIOD_MS));
    }
}

static void format_task(void *arg)
{
    (void)arg;
    for (;;) {
        char scratch[FORMAT_SCRATCH];
        snprintf(scratch, sizeof scratch, "lux=%ld", (long)read_sensor());
        mcu_telemetry_set_custom_int("formatted_len", (int32_t)strlen(scratch));
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

static const mcu_telemetry_config_t telemetry = {
        .device = "leaky-sensor-node",
        .report_period_ms = 5000,
    };

void app_main(void)
{
    
    ESP_ERROR_CHECK(mcu_telemetry_start(&telemetry));

    xTaskCreate(sample_task, "sample", SAMPLE_STACK, NULL, 5, NULL);
    mcu_telemetry_register_task("sample", SAMPLE_STACK);
    xTaskCreate(format_task, "format", FORMAT_STACK, NULL, 4, NULL);
    mcu_telemetry_register_task("format", FORMAT_STACK);

    ESP_LOGI(TAG, "sensor node up");
}