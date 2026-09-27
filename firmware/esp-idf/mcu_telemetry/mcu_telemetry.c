#include "mcu_telemetry.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>

#include "esp_heap_caps.h"
#include "esp_system.h"
#include "esp_timer.h"

#ifndef MCU_TELEMETRY_BUFFER_SIZE
/* The frame buffer is static rather than on the task stack: the whole point of
 * this agent is to watch stack head-room, so it must not consume it. */
#define MCU_TELEMETRY_BUFFER_SIZE 2048
#endif

#define MCU_TELEMETRY_DEFAULT_PERIOD_MS 5000
#define MCU_TELEMETRY_DEFAULT_STACK 4096

/*
 * Tasks are tracked by name rather than by handle. A handle goes stale as soon
 * as the task exits - the ESP-IDF main task is deleted when app_main returns -
 * and uxTaskGetStackHighWaterMark() on a stale handle reads freed memory and
 * panics the chip. Names are re-resolved on every report instead, and tasks
 * that have gone away are skipped.
 */
typedef struct {
    char name[16];
    uint32_t stack_size;
} task_entry_t;

typedef struct {
    char key[24];
    int32_t value;
    bool used;
} custom_entry_t;

static task_entry_t s_tasks[MCU_TELEMETRY_MAX_TASKS];
static size_t s_task_count;
static custom_entry_t s_custom[MCU_TELEMETRY_MAX_CUSTOM];
static char s_device[32];
static char s_firmware[32];
static uint32_t s_period_ms = MCU_TELEMETRY_DEFAULT_PERIOD_MS;
static TaskHandle_t s_task;
static uint32_t s_seq;
static char s_buffer[MCU_TELEMETRY_BUFFER_SIZE];

/* ------------------------------------------------------------------ */
/* weak hook                                                          */
/* ------------------------------------------------------------------ */

__attribute__((weak)) void mcu_telemetry_extra_fields(char *out, size_t size)
{
    if (size > 0) {
        out[0] = '\0';
    }
}

/* ------------------------------------------------------------------ */
/* helpers                                                            */
/* ------------------------------------------------------------------ */

/* Keep generated JSON valid even if a name contains a quote or a backslash. */
static void copy_sanitised(char *destination, size_t size, const char *source)
{
    if (size == 0) {
        return;
    }
    size_t written = 0;
    for (const char *p = source; p != NULL && *p != '\0' && written + 1 < size; ++p) {
        char c = *p;
        if (c == '"' || c == '\\' || (unsigned char)c < 0x20) {
            c = '_';
        }
        destination[written++] = c;
    }
    destination[written] = '\0';
}

static void json_append(size_t *used, const char *format, ...)
{
    if (*used + 1 >= sizeof(s_buffer)) {
        return;
    }
    va_list args;
    va_start(args, format);
    int written = vsnprintf(s_buffer + *used, sizeof(s_buffer) - *used, format, args);
    va_end(args);
    if (written < 0) {
        return;
    }
    *used += (size_t)written;
    if (*used >= sizeof(s_buffer)) {
        *used = sizeof(s_buffer) - 1;
    }
}

static task_entry_t *find_task(const char *name)
{
    for (size_t i = 0; i < s_task_count; ++i) {
        if (name != NULL && strcasecmp(s_tasks[i].name, name) == 0) {
            return &s_tasks[i];
        }
    }
    return NULL;
}

static void add_task(const char *name, uint32_t stack_size)
{
    if (name == NULL || name[0] == '\0' || s_task_count >= MCU_TELEMETRY_MAX_TASKS) {
        return;
    }
    task_entry_t *existing = find_task(name);
    if (existing != NULL) {
        if (stack_size != 0) {
            existing->stack_size = stack_size;
        }
        return;
    }
    task_entry_t *entry = &s_tasks[s_task_count++];
    copy_sanitised(entry->name, sizeof(entry->name), name);
    entry->stack_size = stack_size;
}

#if CONFIG_FREERTOS_USE_TRACE_FACILITY
/* Add every task the scheduler knows about that was not registered by hand. */
static void discover_system_tasks(void)
{
    static TaskStatus_t statuses[MCU_TELEMETRY_MAX_TASKS];
    UBaseType_t count = uxTaskGetSystemState(statuses, MCU_TELEMETRY_MAX_TASKS, NULL);
    /*
     * The return value is the total number of tasks in the system, not the
     * number of entries that were filled in. Clamp it, otherwise the loop
     * below reads past the array.
     */
    if (count > MCU_TELEMETRY_MAX_TASKS) {
        count = MCU_TELEMETRY_MAX_TASKS;
    }
    for (UBaseType_t i = 0; i < count; ++i) {
        add_task(statuses[i].pcTaskName, 0);
    }
}
#else
static void discover_system_tasks(void)
{
}
#endif

/* ------------------------------------------------------------------ */
/* frame construction                                                 */
/* ------------------------------------------------------------------ */

static void append_tasks(size_t *used)
{
    json_append(used, ",\"tasks\":[");
    bool first = true;
    for (size_t i = 0; i < s_task_count; ++i) {
        /*
         * Resolve on every report: a task that exited since the last frame has
         * no handle, and querying one would fault.
         */
        TaskHandle_t handle = xTaskGetHandle(s_tasks[i].name);
        if (handle == NULL) {
            continue;
        }
        uint32_t free_bytes = (uint32_t)uxTaskGetStackHighWaterMark(handle);
        json_append(used, "%s{\"name\":\"%s\",\"stack_free_min\":%u",
                    first ? "" : ",", s_tasks[i].name, (unsigned)free_bytes);
        if (s_tasks[i].stack_size != 0) {
            json_append(used, ",\"stack_total\":%u", (unsigned)s_tasks[i].stack_size);
        }
        json_append(used, "}");
        first = false;
    }
    json_append(used, "]");
}

static void append_custom(size_t *used)
{
    bool any = false;
    for (size_t i = 0; i < MCU_TELEMETRY_MAX_CUSTOM; ++i) {
        if (s_custom[i].used) {
            any = true;
            break;
        }
    }
    if (!any) {
        return;
    }
    json_append(used, ",\"custom\":{");
    bool first = true;
    for (size_t i = 0; i < MCU_TELEMETRY_MAX_CUSTOM; ++i) {
        if (!s_custom[i].used) {
            continue;
        }
        json_append(used, "%s\"%s\":%d", first ? "" : ",", s_custom[i].key,
                    (int)s_custom[i].value);
        first = false;
    }
    json_append(used, "}");
}

static void report(void)
{
    discover_system_tasks();

    size_t used = 0;
    uint32_t uptime_ms = (uint32_t)(esp_timer_get_time() / 1000);

    json_append(&used, "{\"v\":%d,\"device\":\"%s\"", MCU_TELEMETRY_SCHEMA_VERSION, s_device);
    if (s_firmware[0] != '\0') {
        json_append(&used, ",\"fw\":\"%s\"", s_firmware);
    }
    json_append(&used, ",\"seq\":%u,\"uptime_ms\":%u", (unsigned)++s_seq, (unsigned)uptime_ms);
    json_append(&used,
                ",\"heap\":{\"free\":%u,\"min\":%u,\"largest\":%u}",
                (unsigned)esp_get_free_heap_size(),
                (unsigned)esp_get_minimum_free_heap_size(),
                (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));
    append_tasks(&used);
    append_custom(&used);

    char extra[256];
    mcu_telemetry_extra_fields(extra, sizeof(extra));
    if (extra[0] != '\0') {
        json_append(&used, ",%s", extra);
    }
    json_append(&used, "}\n");

    fputs(s_buffer, stdout);
    fflush(stdout);
}

static void telemetry_task(void *argument)
{
    (void)argument;
    TickType_t period = pdMS_TO_TICKS(s_period_ms);
    if (period == 0) {
        period = 1;
    }
    for (;;) {
        report();
        /* Returns early when mcu_telemetry_report_now() pokes the task. */
        ulTaskNotifyTake(pdTRUE, period);
    }
}

/* ------------------------------------------------------------------ */
/* public API                                                         */
/* ------------------------------------------------------------------ */

esp_err_t mcu_telemetry_start(const mcu_telemetry_config_t *config)
{
    if (config == NULL || config->device == NULL || config->device[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_task != NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    copy_sanitised(s_device, sizeof(s_device), config->device);
    copy_sanitised(s_firmware, sizeof(s_firmware), config->firmware ? config->firmware : "");
    s_period_ms = config->report_period_ms ? config->report_period_ms
                                           : MCU_TELEMETRY_DEFAULT_PERIOD_MS;

    uint32_t stack_size = config->stack_size ? config->stack_size
                                             : MCU_TELEMETRY_DEFAULT_STACK;
    UBaseType_t priority = config->priority ? config->priority : (tskIDLE_PRIORITY + 1);
    if (priority > configMAX_PRIORITIES - 1) {
        priority = configMAX_PRIORITIES - 1;
    }

    if (xTaskCreate(telemetry_task, "telemetry", stack_size, NULL, priority, &s_task) != pdPASS) {
        s_task = NULL;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t mcu_telemetry_register_task(const char *name, uint32_t stack_size)
{
    if (name == NULL || name[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }
    if (find_task(name) == NULL && s_task_count >= MCU_TELEMETRY_MAX_TASKS) {
        return ESP_ERR_NO_MEM;
    }
    add_task(name, stack_size);
    return ESP_OK;
}

void mcu_telemetry_set_custom_int(const char *key, int32_t value)
{
    if (key == NULL || key[0] == '\0') {
        return;
    }
    for (size_t i = 0; i < MCU_TELEMETRY_MAX_CUSTOM; ++i) {
        if (s_custom[i].used && strcasecmp(s_custom[i].key, key) == 0) {
            s_custom[i].value = value;
            return;
        }
    }
    for (size_t i = 0; i < MCU_TELEMETRY_MAX_CUSTOM; ++i) {
        if (!s_custom[i].used) {
            copy_sanitised(s_custom[i].key, sizeof(s_custom[i].key), key);
            s_custom[i].value = value;
            s_custom[i].used = true;
            return;
        }
    }
}

void mcu_telemetry_report_now(void)
{
    if (s_task != NULL) {
        xTaskNotifyGive(s_task);
    }
}