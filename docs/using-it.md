# MCU-Insight: the reference

New here? Read the one page guide instead: `docs/index.html` (English) or
`docs/index.zh.html` (中文). It walks a new project through the whole thing in
order, showing the command and its output at every step.

Better still, start on a project that is already broken.
`examples/leaky-sensor-node/` is a small firmware with two deliberate defects,
and its README has the answers. Walk the guide through that before touching your
own project.

This file is the reference: the device agent, the configuration schema, what the
metrics mean, and what the tool does not do.

## The two directories

| name | what it is |
|---|---|
| `<tool-repo>` | the directory you cloned this into; it holds `mcu_insight/`, `tools/`, `docs/`, `budgets/`, `captures/` |
| `<your-project>` | your ESP-IDF project; the directory holding `CMakeLists.txt`, the one `idf.py` runs in |

Two rules, and they are the ones people get wrong:

* `idf.py` runs in `<your-project>`.
* `mcu_insight` and `tools/sync_firmware.py` run in `<tool-repo>`.

Every command below starts with its own `cd`, so it works from wherever your
shell happens to be.

## Install

```powershell
cd <tool-repo>
python -m venv .venv
.venv\Scripts\python -m pip install -e . --no-build-isolation
.venv\Scripts\python -m pip install pyserial
```

`--no-build-isolation` reuses the setuptools already in the venv, so the install
needs no network. Every command also runs without installing, as
`python -m mcu_insight <command>` from `<tool-repo>`.

`pyserial` is needed for exactly one thing, reading frames straight off a board.
The ESP-IDF Python environment already ships it, so the capture step can also be
run with that interpreter.

## Add the device agent

```powershell
cd <tool-repo>
python tools/sync_firmware.py <your-project>
```

That writes `<your-project>\components\mcu_telemetry`. The directory does not
have to exist first; the command creates it. The argument must be the project
root, the directory holding `CMakeLists.txt`; given anything else it stops and
says so.

Then three lines in `main.c`: the include at the top, the config outside
`app_main`, and the start call after the hardware is up.

```c
#include "mcu_telemetry.h"

static const mcu_telemetry_config_t telemetry = {
    .device = "my-board",      /* a name for this board */
    .report_period_ms = 5000,  /* report every 5 seconds */
};

void app_main(void)
{
    /* ... your own initialisation ... */
    ESP_ERROR_CHECK(mcu_telemetry_start(&telemetry));
    /* the rest of your code does not change */
}
```

And one name in `<your-project>\main\CMakeLists.txt`, or the header will not
resolve:

```cmake
idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "."
    REQUIRES driver nvs_flash freertos mcu_telemetry)
```

If you also set `EXTRA_COMPONENT_DIRS` in the project's own `CMakeLists.txt`,
guard it. Unguarded, a build attempted before the copy fails with
"Directory specified in EXTRA_COMPONENT_DIRS doesn't exist", which mentions
neither the missing component nor the step that was skipped:

```cmake
cmake_minimum_required(VERSION 3.16)

if(EXISTS "${CMAKE_CURRENT_LIST_DIR}/components")
    set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/components")
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(my_app)
```

### What the agent reports on its own

The three lines are enough for all of this; none of it needs code from you.

| metric | needs |
|---|---|
| heap: free now, the smallest it has been, the largest allocatable block | nothing |
| every task's stack high-water mark | `CONFIG_FREERTOS_USE_TRACE_FACILITY=y`, which also discovers httpd, mqtt and wifi without registering them |
| idle share per core | `CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS=y` |
| light sleep share | `CONFIG_PM_ENABLE=y` |
| UART retries | nothing; the agent counts them |

Register a task by hand only when you want its stack *total*, since the RTOS
does not expose it:

```c
xTaskCreate(control_task, "control", 4096, NULL, 2, NULL);
mcu_telemetry_register_task("control", 4096);   /* same name as xTaskCreate */
```

Anything else is a line where the number is computed:

```c
mcu_telemetry_set_custom_int("loop_jitter_us", jitter);
mcu_telemetry_histogram_add("infer", latency_us);   /* gives p50, p99, min, max */
```

## The project file

One JSON file per project. It says what the budgets are, what the metrics mean,
and where the baseline lives.

```json
{
  "rules": [
    {"metric": "heap.min", "min": 100000, "stat": "min"},
    {"metric": "heap.free", "min_rate_per_s": -64, "min_span_ms": 60000},
    {"metric": "task.*.stack_free_min", "min": 256, "stat": "min"},
    {"metric": "custom.idle*_pct", "min": 40, "stat": "min"},
    {"metric": "custom.infer_p99_us", "max": 5000, "stat": "max"},
    {"metric": "custom.model_accuracy_pct", "min": 85, "min_samples": 20}
  ],
  "metrics": {
    "custom.model_accuracy_pct": {
      "unit": "%",
      "description": "rolling accuracy; it reads low right after a boot"
    }
  },
  "baseline": {"path": "my-board-baseline.json", "max_change_pct": 20}
}
```

| rule key | meaning |
|---|---|
| `metric` | which metric, with `*` as a wildcard. The pattern also makes its prefix a name the audit will accept |
| `min` / `max` | a bound on the value |
| `stat` | which observation to bound: `last` (default), `min`, `max` |
| `min_rate_per_s` / `max_rate_per_s` | a bound on the slope, in units per second |
| `min_span_ms` | refuse to judge a slope until the capture holds this much steady state |
| `min_samples` | skip the metric until it has this many samples |

There are three verdicts, not two. **PASS**, **FAIL**, and **INCOMPLETE** for a
rule that could not be judged; the exit code is non-zero for the last two,
because a build cannot be called green on the strength of a check nobody carried
out.

Use a slope for anything that is a high-water mark. `heap.min` and
`stack_free_min` only ever fall, so a floor on them says as much about how long
the capture ran as about the code: a 100 B/s leak from 145 KB needs 450 s to
cross a 100 KB floor, which means a two minute capture passes the level rule
with the leak plainly present.

### Units and kinds

Both are inferred from the name, so most projects need nothing. `_us` →
microseconds, `_ms` → milliseconds, `_hz` → hertz, `_pct` → percent, `_bytes`,
`heap.*`, `stack_free` and `stack_total` → bytes, `_samples`, `_count`,
`_retries` and `_total` → counts, `net.rssi` → dBm.

The kind matters as much as the unit. `heap.min` and `stack_free_min` are
labelled **high-water marks**: they only ever fall, so a drop is the deepest
point reached since boot and not a trend. Others are instantaneous samples,
counters since boot, clocks, configured values, or build-time constants. The
`metrics` block is for the names a convention cannot describe.

## Capture

```powershell
cd <tool-repo>
python -m mcu_insight collect --db captures/board.db --source serial:COM19 --limit 30
```

| source | what it reads |
|---|---|
| `serial:COM19` | a board; add `@921600` for a different rate, the default is 115200 |
| `file:saved.log` | a capture someone else took |
| `stdin` | a pipe |

It prints a line per frame and says what it is listening on, so a running capture
is never mistaken for a hang, and `Ctrl+C` is safe: every frame is committed as
it arrives, so stopping early keeps what came in. Lines that are not frames are
ignored, which is why the ESP-IDF log stream can share the port.

## Judge, baseline, report

```powershell
cd <tool-repo>
python -m mcu_insight check --db captures/board.db --config budgets/my-board.json
python -m mcu_insight check --db captures/good.db --config budgets/my-board.json --save-baseline budgets/my-board-baseline.json --project <your-project>
python -m mcu_insight report --db captures/board.db --config budgets/my-board.json --out report.html
```

`--json` emits machine-readable output for regression tracking.

The baseline records the revision it came from, how many frames and boot sessions
it covers, how long its steady state lasted, and per metric the last value with
its extremes, its sample count and its steady slope. That is what lets a later
comparison say whether a change is real or just a shorter soak, and the
diagnosis pack passes those slopes to the model.

The report is one self-contained HTML file: data inlined, charts hand-written
SVG, no server, no CDN, no script. Five blocks: checks, baseline changes, metric
trends (each reboot marked), build resources, and a fault matrix when you pass
`--faults`.

## Diagnosis and audit

```powershell
cd <tool-repo>
python -m mcu_insight diagnose --db captures/board.db --config budgets/my-board.json --project <your-project> --lang zh --out prompt.txt
python -m mcu_insight audit --pack prompt.txt --answer answer.md
```

`diagnose` collects what is known into a numbered evidence pack and stops there;
it never calls a model itself. `--lang zh` only changes the language the pack
asks the answer to be written in -- metric names and `[E12]` ids stay as the tool
writes them, because they are identifiers.

`audit` checks the answer back against the pack: a cited id that does not exist
fails, a metric name the pack does not contain warns, and an uncited claim is
listed. Exit code 1 means the answer leans on evidence that is not there.

## Without a board

```powershell
cd <tool-repo>
python -m mcu_insight analyze <your-project>\build\<name>.map --bin <your-project>\build\<name>.bin --partition 1500K
python -m mcu_insight compare build/before.map build/after.map --partition 1500K
python -m mcu_insight model <your-project>\build\model.tflite
```

The project name is the one inside `project(...)` in `CMakeLists.txt`. To see
what is actually there:

```powershell
Get-ChildItem <your-project>\build\*.map, <your-project>\build\*.bin
```

## In CI

```yaml
- run: cd <tool-repo> && python -m mcu_insight check --db captures/board.db --config budgets/my-board.json
```

`check` fails the job on a violation and also when a rule could not be judged, so
a capture too short to measure a slope cannot pass as green.

## What it does not do

Worth knowing before you trust it:

* no call graphs or per-function attribution. A heap slope says memory is going
  somewhere, not which call site takes it;
* no power measurement. Light sleep residency is a proxy for it, not a meter;
* **untracked files are not in the diff.** In a brand new project the source is
  untracked, so `git add` before diagnosing or the pack will contain the metrics
  without the code;
* **a pack that is too large is trimmed**, and a metric whose series was dropped
  says so. The corresponding evidence is genuinely gone from that pack.

## Errors a reader actually hits

| what you see | what it means |
|---|---|
| `CMakeLists.txt not found in project directory` | you are not in `<your-project>`; `cd` there first |
| `Failed to resolve component 'mcu_telemetry'` | the component was never copied; run `sync_firmware.py` |
| `reading a serial port needs pyserial` | `pip install pyserial`, or capture with the ESP-IDF python |
| `Directory specified in EXTRA_COMPONENT_DIRS doesn't exist` | likewise, or guard that line as shown above |

## Where to go next

* `docs/index.html`, `docs/index.zh.html` -- the one page walkthrough.
* `docs/diagnosis-case-study.md` -- five rounds of evidence packs, and what each
  one got right.
* `examples/leaky-sensor-node/` -- a project with real defects to practise on.
* `docs/telemetry-schema.md` -- the frame format, if you would rather publish it
  from something other than the C agent.