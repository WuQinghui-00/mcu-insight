#include "mcu_telemetry.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <ctype.h>
#include <stdlib.h>
#include <strings.h>
#include <unistd.h>

#include "esp_heap_caps.h"
#include "esp_system.h"
#include "esp_timer.h"

#if CONFIG_PM_ENABLE
#include "esp_private/pm_impl.h"
#endif

#if CONFIG_ESP_CONSOLE_UART
#include "driver/uart.h"
#include "esp_vfs_dev.h"
#endif

#ifndef MCU_TELEMETRY_BUFFER_SIZE
/* The frame buffer is static rather than on the task stack: the whole point of
 * this agent is to watch stack head-room, so it must not consume it. */
#define MCU_TELEMETRY_BUFFER_SIZE 3072
#endif

#define MCU_TELEMETRY_DEFAULT_PERIOD_MS 5000
#define MCU_TELEMETRY_DEFAULT_STACK 4096
#define MCU_TELEMETRY_TX_BUFFER 2048
#define MCU_TELEMETRY_RX_BUFFER 256

typedef struct {
    char name[16];
    uint32_t stack_size;
} registered_task_t;

typedef struct {
    char key[24];
    int32_t value;
    bool used;
} custom_entry_t;

static registered_task_t s_registered[MCU_TELEMETRY_MAX_TASKS];
static size_t s_registered_count;
static custom_entry_t s_custom[MCU_TELEMETRY_MAX_CUSTOM];
static char s_device[32];
static char s_firmware[32];
static uint32_t s_period_ms = MCU_TELEMETRY_DEFAULT_PERIOD_MS;
static size_t s_link_selftest_bytes;
static TaskHandle_t s_task;
static uint32_t s_seq;
static char s_buffer[MCU_TELEMETRY_BUFFER_SIZE];
static bool s_uart_ready;
static uint32_t s_uart_retries;

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

/*
 * Write a whole buffer to the console.
 *
 * Without a driver the console writes one byte at a time straight into the
 * UART FIFO, competing with every other writer, and a frame longer than a few
 * hundred bytes loses bytes. With the driver installed the whole frame goes
 * into the TX ring buffer and the interrupt handler feeds the FIFO, which is
 * both faster and safe.
 */
static void write_all(const char *data, size_t size)
{
#if CONFIG_ESP_CONSOLE_UART
    if (s_uart_ready) {
        size_t sent = 0;
        while (sent < size) {
            int written = uart_write_bytes(CONFIG_ESP_CONSOLE_UART_NUM, data + sent, size - sent);
            if (written > 0) {
                sent += (size_t)written;
            } else {
                /* Ring buffer full: let it drain, then continue with the
                 * remainder. The console VFS discards the count instead. */
                s_uart_retries++;
                uart_wait_tx_done(CONFIG_ESP_CONSOLE_UART_NUM, pdMS_TO_TICKS(100));
            }
        }
        mcu_telemetry_set_custom_int("uart_tx_retries", (int32_t)s_uart_retries);
        return;
    }
#endif
    size_t sent = 0;
    while (sent < size) {
        ssize_t written = write(STDOUT_FILENO, data + sent, size - sent);
        if (written <= 0) {
            break;
        }
        sent += (size_t)written;
    }
}

static uint32_t stack_size_for(const char *name)
{
    for (size_t i = 0; i < s_registered_count; ++i) {
        if (name != NULL && strcasecmp(s_registered[i].name, name) == 0) {
            return s_registered[i].stack_size;
        }
    }
    return 0;
}

static bool snapshot_has(const TaskStatus_t *statuses, UBaseType_t count, const char *name)
{
    for (UBaseType_t i = 0; i < count; ++i) {
        if (statuses[i].pcTaskName != NULL &&
            strcasecmp(statuses[i].pcTaskName, name) == 0) {
            return true;
        }
    }
    return false;
}

static void emit_task(size_t *used, bool *first, const char *name)
{
    /*
     * Resolve on every report. A handle stored earlier goes stale as soon as
     * the task exits, and querying a stale handle reads freed memory.
     */
    TaskHandle_t handle = xTaskGetHandle(name);
    if (handle == NULL) {
        return;
    }
    uint32_t free_bytes = (uint32_t)uxTaskGetStackHighWaterMark(handle);
    json_append(used, "%s{\"name\":\"%s\",\"stack_free_min\":%u",
                *first ? "" : ",", name, (unsigned)free_bytes);
    uint32_t total = stack_size_for(name);
    if (total != 0) {
        json_append(used, ",\"stack_total\":%u", (unsigned)total);
    }
    json_append(used, "}");
    *first = false;
}

/*
 * The task list is rebuilt from a live snapshot on every report rather than
 * accumulated: appending across reports produced duplicates when a name was
 * registered with different capitalisation than the scheduler uses.
 */
static void append_tasks(size_t *used)
{
    static TaskStatus_t statuses[MCU_TELEMETRY_MAX_TASKS];
    UBaseType_t count = 0;

#if CONFIG_FREERTOS_USE_TRACE_FACILITY
    count = uxTaskGetSystemState(statuses, MCU_TELEMETRY_MAX_TASKS, NULL);
    /*
     * The return value is the total number of tasks, not the number of entries
     * that were filled in; clamp it before indexing.
     */
    if (count > MCU_TELEMETRY_MAX_TASKS) {
        count = MCU_TELEMETRY_MAX_TASKS;
    }
#endif

    json_append(used, ",\"tasks\":[");
    bool first = true;
    for (UBaseType_t i = 0; i < count; ++i) {
        emit_task(used, &first, statuses[i].pcTaskName);
    }
    /* Registered tasks the snapshot did not cover. */
    for (size_t i = 0; i < s_registered_count; ++i) {
        if (snapshot_has(statuses, count, s_registered[i].name)) {
            continue;
        }
        emit_task(used, &first, s_registered[i].name);
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

    /*
     * Additive checksum over the bytes sent so far, so the host can tell a
     * damaged frame from a valid one: a silently corrupted frame is worse than
     * a dropped one.
     */
    unsigned checksum = 0;
    for (size_t i = 0; i < used; ++i) {
        checksum += (unsigned char)s_buffer[i];
    }
    json_append(&used, ",\"sum\":%u}\n", checksum % 256u);

    write_all(s_buffer, used);
}

#if CONFIG_PM_ENABLE
/*
 * Publish how much time the chip actually spends in light sleep.
 *
 * This answers "did a change break low power?" without a current meter: the
 * 21 mA to 7 mA drop comes from entering light sleep at all, so if sleep
 * residency stays healthy the power profile did not regress.
 *
 * esp_pm_impl_dump_stats() is an IDF internal API, but it is the only way to
 * read those counters; the output is a small fixed table.
 */
static void sample_power_stats(void)
{
    static char buffer[512];
    FILE *stream = fmemopen(buffer, sizeof(buffer) - 1, "w");
    if (stream == NULL) {
        return;
    }
    esp_pm_impl_dump_stats(stream);
    long written = ftell(stream);
    fclose(stream);
    if (written < 0) {
        return;
    }
    if ((size_t)written >= sizeof(buffer)) {
        written = (long)(sizeof(buffer) - 1);
    }
    buffer[written] = '\0';

    /* DIAGNOSTIC: dump the raw table once so the parser can be checked. */
    static bool dumped;
    if (!dumped) {
        dumped = true;
        const char *header = "---PMDUMP---\n";
        write_all(header, strlen(header));
        write_all(buffer, (size_t)written);
    }

    const char *row = strstr(buffer, "SLEEP");
    if (row == NULL) {
        return;                     /* light sleep is not enabled */
    }
    const char *end = strchr(row, '\n');
    if (end == NULL) {
        end = row + strlen(row);
    }
    /*
     * The columns are space padded and the frequency reads "40 M", so parse
     * from the end of the row: the last token is the percentage.
     */
    const char *p = end;
    while (p > row && (p[-1] == ' ' || p[-1] == '%')) {
        --p;
    }
    const char *percent_end = p;
    while (p > row && isdigit((unsigned char)p[-1])) {
        --p;
    }
    const char *percent_start = p;
    while (p > row && p[-1] == ' ') {
        --p;
    }
    const char *time_end = p;
    while (p > row && isdigit((unsigned char)p[-1])) {
        --p;
    }
    if (p == time_end) {
        return;
    }
    unsigned long long micros = strtoull(p, NULL, 10);
    int percent = (int)strtol(percent_start, NULL, 10);
    (void)percent_end;
    mcu_telemetry_set_custom_int("light_sleep_ms", (int32_t)(micros / 1000));
    mcu_telemetry_set_custom_int("light_sleep_pct", percent);
}
#endif

static void telemetry_task(void *argument)
{
    (void)argument;
    TickType_t period = pdMS_TO_TICKS(s_period_ms);
    if (period == 0) {
        period = 1;
    }
    for (;;) {
        if (s_link_selftest_bytes != 0) {
            mcu_telemetry_link_selftest(s_link_selftest_bytes);
        }
#if CONFIG_PM_ENABLE
        sample_power_stats();
#endif
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

#if CONFIG_ESP_CONSOLE_UART
    /*
     * Give the console port a real TX ring buffer. Without it the console
     * writes byte by byte straight into the FIFO and long frames lose bytes.
     * If the install fails the agent falls back to the VFS path.
     */
    if (uart_driver_install(CONFIG_ESP_CONSOLE_UART_NUM, MCU_TELEMETRY_RX_BUFFER,
                            MCU_TELEMETRY_TX_BUFFER, 0, NULL, 0) == ESP_OK) {
        /*
         * Route stdio through the same driver. Logging that keeps using the
         * ROM path writes into the FIFO byte by byte and races the driver for
         * space; sharing one TX path removes the race entirely.
         */
        esp_vfs_dev_uart_use_driver(CONFIG_ESP_CONSOLE_UART_NUM);
        s_uart_ready = true;
    }
#endif

    copy_sanitised(s_device, sizeof(s_device), config->device);
    copy_sanitised(s_firmware, sizeof(s_firmware), config->firmware ? config->firmware : "");
    s_period_ms = config->report_period_ms ? config->report_period_ms
                                           : MCU_TELEMETRY_DEFAULT_PERIOD_MS;
    s_link_selftest_bytes = config->link_selftest_bytes;

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
    for (size_t i = 0; i < s_registered_count; ++i) {
        if (strcasecmp(s_registered[i].name, name) == 0) {
            s_registered[i].stack_size = stack_size;
            return ESP_OK;
        }
    }
    if (s_registered_count >= MCU_TELEMETRY_MAX_TASKS) {
        return ESP_ERR_NO_MEM;
    }
    registered_task_t *entry = &s_registered[s_registered_count++];
    copy_sanitised(entry->name, sizeof(entry->name), name);
    entry->stack_size = stack_size;
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

/*
 * Diagnostic: send a known printable pattern so the host can verify the link
 * byte for byte. Format:
 *     LINKTEST <bytes> <checksum>\n
 *     <bytes of the repeating pattern>\n
 */
void mcu_telemetry_link_selftest(size_t bytes)
{
    static const char pattern[] =
        "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
    const size_t period = sizeof(pattern) - 1;
    static char payload[MCU_TELEMETRY_BUFFER_SIZE];

    if (bytes > sizeof(payload) - 32) {
        bytes = sizeof(payload) - 32;
    }

    unsigned checksum = 0;
    for (size_t i = 0; i < bytes; ++i) {
        checksum += (unsigned char)pattern[i % period];
    }

    size_t used = (size_t)snprintf(payload, sizeof(payload), "LINKTEST %u %u\n",
                                   (unsigned)bytes, checksum % 256u);
    for (size_t i = 0; i < bytes && used + 2 < sizeof(payload); ++i) {
        payload[used++] = pattern[i % period];
    }
    payload[used++] = '\n';

    write_all(payload, used);
}

void mcu_telemetry_report_now(void)
{
    if (s_task != NULL) {
        xTaskNotifyGive(s_task);
    }
}
