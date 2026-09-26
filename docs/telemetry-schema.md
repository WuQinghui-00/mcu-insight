# Telemetry frame schema (v1)

One frame is one line of JSON (JSON Lines). A device prints a frame per report
interval; the host collector reads the stream, ignores everything that is not a
frame, and stores it.

## Why JSON Lines

* the ESP-IDF log stream already carries text, so frames can share the same
  channel as `ESP_LOGI` output and the collector can skip log noise;
* a human can read the stream with any serial monitor;
* the format stays readable when the toolchain changes.

A compact binary framing is a later optimisation, not an MVP requirement.

## Frame

```json
{
  "v": 1,
  "device": "esp32-light-monitor",
  "fw": "a1b2c3d",
  "seq": 42,
  "uptime_ms": 123456,
  "heap": {"free": 145320, "min": 140000, "largest": 110000},
  "net": {"rssi": -52, "disconnects": 3, "reconnects": 3, "mqtt_online": true},
  "tasks": [
    {"name": "sensor", "prio": 3, "stack_total": 4096, "stack_free_min": 2048}
  ],
  "custom": {"loop_period_ms": 200, "jitter_us": 180}
}
```

### Required fields

| Field | Type | Meaning |
|---|---|---|
| `v` | int | schema version, currently `1` |
| `device` | string | stable device or build name, used to group series |
| `uptime_ms` | number | milliseconds since boot, the device-side clock |

### Optional fields

| Field | Type | Meaning |
|---|---|---|
| `fw` | string | firmware revision, e.g. a short git hash |
| `seq` | int | monotonic frame counter, detects dropped frames |
| `heap` | object | `free`, `min` (lowest since boot), `largest` (largest free block) |
| `net` | object | `rssi`, `disconnects`, `reconnects`, `mqtt_online` |
| `tasks` | array | one entry per task: `name`, `prio`, `stack_total`, `stack_free_min` |
| `custom` | object | project specific numeric metrics |

`stack_free_min` is the *high-water mark*: the smallest amount of stack the task
has ever had free. It is the number that predicts a stack overflow.

## Collector-side flattening

Nested values become flat metric names so that every series lives in one table:

| Frame path | Metric name |
|---|---|
| `heap.free` | `heap.free` |
| `net.rssi` | `net.rssi` |
| `tasks[0].name = "sensor"`, `stack_free_min` | `task.sensor.stack_free_min` |
| `custom.jitter_us` | `custom.jitter_us` |

## Adding metrics

Add fields to `custom` (or a new top-level object) without bumping `v`: readers
ignore unknown keys and store them. Bump `v` only for changes that break the
fields above.
