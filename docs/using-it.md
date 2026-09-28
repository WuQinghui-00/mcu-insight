# Using MCU-Insight on a new project

MCU-Insight answers three questions about an ESP-IDF firmware, from
measurements rather than from reading the code.

1. **What does this build cost?** Flash, static DRAM, IRAM and the app
   partition, per component, taken from the linker map and the binary. No board
   needed.
2. **What does the device actually do?** Stack high-water marks per task, the
   smallest free heap and the largest free block, per-core idle share, latency
   percentiles, light sleep residency: whatever the firmware publishes.
3. **Did this change make it worse, and why?** Budget rules that can bound a
   value or a slope, a comparison against a stored baseline, a self-contained
   HTML report, and an evidence pack plus an audit for an AI diagnosis.

It is not a profiler, a debugger, a power meter or a replacement for an
oscilloscope. It reads build artefacts and what the firmware prints, and it
says plainly what it cannot see.

Everything except the model call runs offline on the Python standard library.
There is no account, no cloud, and no telemetry of its own.

## The short version

```powershell
python tools/sync_firmware.py ..\My-Project            # 1. vendor the agent
python -m mcu_insight collect --db captures/board.db --source serial:COM19
python -m mcu_insight check --db captures/board.db --config budgets/my-project.json
python -m mcu_insight report --db captures/board.db --config budgets/my-project.json --out report.html
```

The rest of this document is what to put in each of those steps and how to read
what comes back.

## 0. Requirements

* Python 3.9 or newer.
* An ESP-IDF project (v5.x) and the toolchain that builds it.
* A serial port to the board, for runtime telemetry.
* `git`, optional: the diagnosis pack includes a diff when it can reach one.

## 1. Install

```powershell
git clone <your fork> mcu-insight
cd mcu-insight
python -m venv .venv
.venv\Scripts\python -m pip install -e . --no-build-isolation
.venv\Scripts\python -m pip install pyserial
```

`--no-build-isolation` reuses the setuptools already in the venv, so the
install needs no network. Every command also runs without installing:
`python -m mcu_insight <command>` from the repository root.

`pyserial` is needed for exactly one thing, reading frames straight off a
board. The ESP-IDF Python environment already ships it, so the capture step
below can also be run with that interpreter instead.

## 2. Add the device agent to the firmware

The agent is one small ESP-IDF component that prints a JSON line per report
interval. ESP-IDF needs components inside the project tree, so copy it in:

```powershell
python tools/sync_firmware.py ..\My-Project
```

That writes `..\My-Project\components\mcu_telemetry`. Re-run it after editing
the canonical copy under `firmware/esp-idf/`; it reports what it copied and
what was already current.

In `main.c`:

```c
#include "mcu_telemetry.h"

static const mcu_telemetry_config_t telemetry = {
    .device = "my-board",          /* stable name; series are grouped by it */
    .firmware = FW_REVISION,       /* optional, but it is the identity you diff */
    .report_period_ms = 5000,
};
ESP_ERROR_CHECK(mcu_telemetry_start(&telemetry));

/* after starting each task */
mcu_telemetry_register_task("control", TASK_STACK_CONTROL);

/* anything the project wants to watch, by name */
mcu_telemetry_set_custom_int("loop_jitter_us", jitter);

/* a latency histogram publishes <prefix>_min_us, _max_us, _mean_us, _p50_us,
 * _p99_us and _samples in one call */
mcu_telemetry_histogram_add("infer", latency_us);
```

Wi-Fi or MQTT statistics go in through a weak hook, so the component does not
have to know about your stack:

```c
void mcu_telemetry_extra_fields(char *out, size_t size)
{
    snprintf(out, size, "\"net\":{\"rssi\":%d}", rssi);
}
```

### sdkconfig options worth turning on

| option | what it adds |
|---|---|
| `CONFIG_FREERTOS_USE_TRACE_FACILITY=y` | discovers httpd, mqtt, wifi and other tasks automatically, so you only register the ones the project created |
| `CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS=y` | per-core idle share, `custom.idle0_pct` and `custom.idle1_pct`, which is how a stray busy loop shows up |
| `CONFIG_PM_ENABLE=y` | light sleep residency, `custom.light_sleep_pct`, which answers "did this break low power" without a current meter |

Two things the component handles so the project does not have to. It installs
the console UART driver and writes frames with `uart_write_bytes` in a retry
loop, because the console VFS discards bytes when the FIFO is full and a
truncated frame is worse than no frame. And it counts those retries into
`custom.uart_tx_retries`, so a serial link that is losing bytes is visible
instead of mysterious.

Build and flash as usual, then watch for lines that start with `{`:

```powershell
idf.py -p COM19 flash monitor
```

## 3. Describe the project

One JSON file per project is the whole configuration. It carries the budgets,
what the metrics mean, and where the baseline lives.

```json
{
  "rules": [
    {"metric": "heap.min", "min": 100000, "stat": "min"},
    {"metric": "heap.free", "min_rate_per_s": -64, "min_span_ms": 60000},
    {"metric": "task.*.stack_free_min", "min": 256, "stat": "min"},
    {"metric": "task.control.stack_free_min", "min": 512, "stat": "min"},
    {"metric": "custom.idle*_pct", "min": 40, "stat": "min"},
    {"metric": "custom.infer_p99_us", "max": 5000, "stat": "max"},
    {"metric": "custom.model_accuracy_pct", "min": 85, "min_samples": 20}
  ],
  "metrics": {
    "custom.model_accuracy_pct": {
      "unit": "%",
      "description": "rolling accuracy; it reads low right after a boot while the window refills"
    }
  },
  "baseline": {
    "path": "my-project-baseline.json",
    "max_change_pct": 20
  }
}
```

| key | meaning |
|---|---|
| `metric` | which metric the rule applies to, with `*` as a wildcard |
| `min` / `max` | a bound on the value |
| `stat` | which observation to bound: `last` (default), `min` or `max` |
| `min_rate_per_s` / `max_rate_per_s` | a bound on the slope, in units per second |
| `min_span_ms` | refuse to judge a slope until the capture holds this much steady state |
| `min_samples` | skip the metric until it has this many samples, for an average that restarts at boot |

There are three verdicts, not two. A rule that could not be judged reports
**INCOMPLETE** and the exit code is non-zero, because a build cannot be called
green on the strength of a check nobody carried out.

Use a slope for anything that is a high-water mark. `heap.min` and
`stack_free_min` only ever fall, so a floor on them is a statement about how
long the capture ran as much as about the code: a 100 B/s leak from 145 KB
needs 450 s to cross a 100 KB floor, so a two minute capture passes the level
rule with the leak plainly present.

### The `metrics` block

Units and kinds are inferred from the name, so most projects need nothing here:
`_us` → microseconds, `_ms` → milliseconds, `_hz` → hertz, `_pct` → percent,
`_bytes`, `heap.*`, `stack_free` and `stack_total` → bytes, `_samples`,
`_count`, `_retries` and `_total` → counts. `heap.min` and `stack_free_min` are
labelled high-water marks, which is the difference between "this number fell"
and "this number cannot rise".

The block is for the names a convention cannot describe, and for saying what a
number means to a reader who has never seen the project.

## 4. Capture

```powershell
python -m mcu_insight collect --db captures/board.db --source serial:COM19
python -m mcu_insight collect --db captures/board.db --source serial:COM19@921600
python -m mcu_insight collect --db captures/board.db --source file:saved.log
python -m mcu_insight collect --db captures/board.db --source stdin
```

The default rate is 115200. The collector ignores every line that is not a
frame, so the ESP-IDF log stream can share the port. Frames are appended to a
SQLite file, so a capture can be interrupted and resumed, and every command
after this reads the file rather than the board.

## 5. Judge

```powershell
python -m mcu_insight check --db captures/board.db --config budgets/my-project.json
```

```
MCU-Insight - resource checks
database  : captures\board.db
devices   : my-board
rules     : 7   metrics matched: 21

Violations (1)
  ! my-board heap.free: falls at -956.9 per second, slope >= -64/s over >= 60 s of steady state

Changed since baseline (budgets/my-project-baseline.json)
  heap.min      147,956 ->     50,168   -66.1%

RESULT: FAIL
```

`--json` emits the same thing machine-readable, and the exit code is 0 for a
pass, 1 for a failure or an incomplete judgement, 2 for a usage error, so a CI
job can gate on it. `--top N` limits the change list.

## 6. Record a baseline while the build is good

```powershell
python -m mcu_insight check --db captures/good.db --config budgets/my-project.json `
    --save-baseline budgets/my-project-baseline.json --project ..\My-Project
```

The snapshot records the revision it came from, how many frames and boot
sessions it covers, how long its steady state lasted, and per metric the last
value with its extremes, its sample count and its steady slope. That is what
lets a later comparison say whether a change is real or just a shorter soak.

## 7. Look at it

```powershell
python -m mcu_insight report --db captures/board.db --config budgets/my-project.json `
    --map ..\My-Project\build\my_app.map --bin ..\My-Project\build\my_app.bin `
    --partition 1500K --out docs/report.html
```

One self-contained HTML file: data inlined, charts hand-written SVG, no server,
no CDN, no script. It opens from disk and can be published as-is.

| block | what it shows |
|---|---|
| Checks | the verdict, every rule, and any rule that was not judged |
| Baseline changes | what moved since the snapshot, coloured only when the worrying direction is known |
| Metric trends | the metrics that moved most, one card each, with each reboot marked |
| Build resources | flash, static DRAM, IRAM, partition use, top components |
| Fault injection matrix | with `--faults DIR`, whether the checks caught each injected fault |

## 8. The diagnosis path

Detection is arithmetic; explaining a detection is not. `diagnose` collects
what is known into a numbered evidence pack and stops there.

```powershell
python -m mcu_insight diagnose --db captures/board.db --config budgets/my-project.json `
    --map ..\My-Project\build\my_app.map --bin ..\My-Project\build\my_app.bin `
    --partition 1500K --project ..\My-Project --out prompt.txt
```

Paste `prompt.txt` into any model, save its answer, then check the answer
against the pack:

```powershell
python -m mcu_insight audit --pack prompt.txt --answer answer.md
```

`audit` fails on a cited evidence id that does not exist, and warns about a
metric name the pack does not contain and about claims with no citation. The
pack itself is offline and testable, which is the point: the model call is the
one step that needs a network, and it is optional.

## 9. Without a board

Three commands work on build artefacts alone.

```powershell
python -m mcu_insight analyze build/my_app.map --bin build/my_app.bin --partition 1500K
python -m mcu_insight compare build/before.map build/after.map --partition 1500K
python -m mcu_insight model build/model.tflite
```

`analyze` is what to run when the question is "will it still fit"; `compare`
answers "what grew" between two builds; `model` reports a TensorFlow Lite
model's arena, weight size, operator list and quantisation, which is usually
where a TinyML flash budget actually went.

## 10. In CI

```yaml
- run: python -m mcu_insight check --db captures/board.db --config budgets/my-project.json
```

A capture committed with the build, or recorded from a board on a bench, turns
the budget file into a gate. `check` fails the job on a violation and also when
a rule could not be judged, so a capture that is too short to measure a slope
cannot pass as green.

## Where to go next

* `docs/diagnosis-case-study.md`: the same capture diagnosed three times, with
  three generations of evidence pack, and what each one got right.
* `docs/telemetry-schema.md`: the frame format, if you would rather publish it
  from something other than the C agent.
* `README.md`: the engineering record, including the faults that were injected
  to prove the checks work and the ones that were initially invisible.