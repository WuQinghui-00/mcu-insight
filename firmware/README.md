# Firmware side

`esp-idf/mcu_telemetry` is the device agent: a small ESP-IDF component that
prints one telemetry frame per report interval (schema v1, see
`../docs/telemetry-schema.md`).

## Canonical copy and vendored copies

This directory holds the canonical source.  ESP-IDF projects need the component
inside their own tree, so `tools/sync_firmware.py` copies it into each project's
`components/` directory:

```powershell
python tools/sync_firmware.py ..\ESP32-Light-Sensor-Monitor-github ..\ESP32-Signal-Processing-System-github
```

Keeping the canonical copy here and vendoring it out means both projects still
build on their own, while there is exactly one file to edit.

## Usage

```c
#include "mcu_telemetry.h"

mcu_telemetry_config_t telemetry = {
    .device = "esp32-light-monitor",
    .firmware = "a1b2c3d",
    .report_period_ms = 5000,
};
ESP_ERROR_CHECK(mcu_telemetry_start(&telemetry));

/* after creating a task */
mcu_telemetry_register_task("sensor", sensor_task_handle, TASK_STACK_SENSOR);

/* project specific metric */
mcu_telemetry_set_custom_int("loop_period_ms", 200);
```

Override the weak hook to add extra JSON members such as WiFi statistics:

```c
void mcu_telemetry_extra_fields(char *out, size_t size)
{
    snprintf(out, size, "\"net\":{\"rssi\":%d}", rssi);
}
```
