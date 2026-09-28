# MCU-Insight

Resource observability for embedded firmware (ESP-IDF / FreeRTOS).

The tool answers questions a code review cannot answer:

* how much flash and static RAM does this build actually consume, and what is left;
* which component grew between two builds, and by how much;
* how much of the RAM budget the heap and task stacks can still use.

## Status

The tool is usable end to end: build-time resource accounting, TinyML model
analysis, runtime telemetry from the device, threshold and baseline checks with
fault injection evidence, and the diagnosis layer in its offline form -- an
evidence pack, the prompt generated from it, and the audit that checks an
answer against it.

Calling a model is deliberately left to the user: `diagnose` writes the prompt,
you paste it wherever you like, and `audit` checks what comes back. Nothing in
that path needs a network connection or an API key, which is also what makes it
testable.

* `docs/using-it.md` -- how to put this on a new project.
* `docs/diagnosis-case-study.md` -- one capture, three generations of evidence
  pack, and what each one got right.

## Getting started

The host side needs no dependencies, no account and no network. Starting from a
new ESP-IDF project, [`docs/using-it.md`](docs/using-it.md) walks from
installing the device agent to reading the HTML report.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e . --no-build-isolation   # optional: the `mcu-insight` command
.venv\Scripts\python -m pip install pyserial                    # only to read a board directly
```

Every command also runs without installing, as `python -m mcu_insight <command>`
from this directory:

```powershell
python -m mcu_insight analyze build/my_app.map --bin build/my_app.bin --partition 1500K
```

| command | question it answers | needs |
|---|---|---|
| `analyze` | what does this build cost | a `.map` |
| `compare` | what grew between two builds | two `.map` files |
| `model` | what does the TinyML model cost | a `.tflite` |
| `collect` | store what the device reports | a board, or a saved log |
| `summary` | what has this device been doing | a capture |
| `check` | does it meet the budgets | a capture and a budget file |
| `report` | one self-contained page with all of it | a capture |
| `diagnose` | assemble the evidence that explains a failure | a capture |
| `audit` | is an explanation actually backed by evidence | a pack and an answer |
| `simulate` | develop the host side without hardware | nothing |

`--json` emits machine-readable output for regression tracking. `check` exits
non-zero on a violation and also when a rule could not be judged, so CI can gate
on it.

## How it works

The tool parses the GNU ld `.map` file that every ESP-IDF build produces, so it
does not need a connected board and does not depend on the ESP-IDF Python
environment.

Three definitions drive the numbers:

| Quantity | Definition |
|---|---|
| Flash image | every output section with a real address, except `.bss` / `.noinit` (which occupy RAM only) and `.debug_*` / `.comment` / `.xt*` (which are not loaded) |
| Static RAM | `.dram0.*`, `.iram0.*` and `.rtc.*` output sections, compared against the matching `Memory Configuration` regions |
| Attributed size | input sections clipped to non-overlapping address ranges, because linker relaxation and COMDAT folding place several input sections at the same address |

## Validation

Against the light-sensor reference build (flash partition 1500K):

| Check | Result |
|---|---|
| Estimated flash image | 973,016 B |
| Actual `.bin` size | 973,136 B |
| Delta | +120 B (+0.01%), image header and segment alignment |
| `esp-idf/esp_app_format` | 85,508 B, identical to `idf.py size-components` |
| `esp-idf/wpa_supplicant` | 62,572 B vs 62,806 B from the IDF tool |
| `esp-idf/lwip` | 99,309 B vs 99,172 B |

## Known linker-map quirks

1. **Relaxation duplicates.** With Xtensa relaxation the linker can emit several
   input sections at the same address, annotated `(size before relaxing)`.
   Summing raw sizes over-counts (262 KB of "contributions" inside a 169 KB
   section); the tool clips them to the real address ranges instead.

2. **The first contribution of a wildcard group can be over-attributed.** In the
   reference build the first `.flash.rodata` contribution is reported as
   84,827 B while the object file only holds 301 B there, and the bytes in that
   address range actually contain log strings from many components. The ESP-IDF
   `size-components` tool reports the same number, so this is a linker map
   artefact rather than a parser bug. Treat per-component numbers for the first
   entry of a large wildcard group with suspicion.

3. **Flash windows look like RAM.** On ESP32 `iram0_2_seg` spans 0x400d0020 and
   holds `.flash.text`; it is the instruction-cache window onto flash, not
   3.3 MB of SRAM. Regions that contain `.flash.*` sections are therefore
   excluded from the RAM capacity calculation.

## Tests

```powershell
python -m unittest discover -s tests -v
```

## Model analysis

```powershell
python -m mcu_insight.cli model model.tflite
```

Reports the tensor arena requirement (peak live bytes, derived from tensor
lifetimes), the weight bytes held in the flatbuffer, the operator histogram,
tensor types and the largest tensors. Custom operators and operator codes
outside the curated table are flagged.

Limitations to keep in mind:

* The built-in operator table is a curated subset. Codes it does not know are
  printed as `OP_<n>` so that an outdated table can never silently mislabel an
  operator; the numeric code is always shown next to the name.
* The arena figure is a lifetime-based estimate, not a byte-exact replay of the
  TFLite Micro arena planner.
* The test fixture is generated by `tests/fbwrite.py`, because no real `.tflite`
  model and no `flatbuffers` package are available offline. Validating against a
  model produced by the real TensorFlow converter is still outstanding.
## Runtime telemetry

```powershell
# store frames arriving on a serial port (or stdin, or a captured file)
python -m mcu_insight.cli collect --db telemetry.db --source serial:COM3@115200

# inspect what has been stored
python -m mcu_insight.cli summary --db telemetry.db
```

Frames are JSON Lines; the format is specified in `docs/telemetry-schema.md`.
The collector ignores anything that is not a frame, so a device can share one
channel between `ESP_LOGI` output and telemetry, and a captured log file can be
replayed later (`--source file:capture.log`).

No hardware is needed to work on the host side. `simulate` emits synthetic
frames, including scripted faults, so the whole pipeline can be developed and
tested before a board is on the desk:

```powershell
python -m mcu_insight.cli simulate --scenario heap-leak --count 20 | python -m mcu_insight.cli collect --db telemetry.db --source stdin
```

Scenarios: `steady`, `heap-leak`, `stack-creep`, `latency-jitter`.
## Device agent

`firmware/esp-idf/mcu_telemetry` is a reusable ESP-IDF component that prints one
telemetry frame per report period. `tools/sync_firmware.py` vendors it into an
ESP-IDF project:

```powershell
python tools/sync_firmware.py ..\ESP32-Light-Sensor-Monitor-github
```

The application then starts it and hands over the task handles:

```c
mcu_telemetry_config_t telemetry = { .device = "my-board" };
mcu_telemetry_start(&telemetry);
mcu_telemetry_register_task("sensor", sensor_handle, 4096);
mcu_telemetry_set_custom_int("loop_period_ms", 200);
```

An overridable weak hook adds project specific JSON members (WiFi RSSI, model
latency, and so on).

### Measured cost

| Project | Flash | Static DRAM |
|---|---|---|
| ESP32 light sensor | +2,528 B | +3,144 B |
| ESP32 signal processing | +4,400 B | +3,520 B |

Plus 4 KiB of heap for the telemetry task stack at run time. The frame buffer is
static on purpose: the agent must not consume the stack head-room it exists to
measure.

The agent compiles and links in both projects but has not been run on hardware
yet; the first board session still has to confirm the frame format on the wire.
## Hardware bring-up

First run of the device agent on a real board (ESP32-D0WD-V3, 4 MB flash, COM19).
Two defects showed up that no amount of host-side testing would have caught, and
both were visible from the telemetry stream itself:

| Symptom | Cause | Fix |
|---|---|---|
| Every task listed twice (`Sensor` and `sensor`) | the agent registered `sensor` while the scheduler calls the task `Sensor`; the duplicate check compared names case-sensitively | match on the handle, compare names case-insensitively |
| `Guru Meditation Error (LoadStoreError)` about 10 s after every boot, inside `prvTaskCheckFreeStackSpace` | the agent cached `TaskHandle_t` values, but the ESP-IDF `main` task is deleted when `app_main` returns; the next report dereferenced a stale handle | store names, re-resolve with `xTaskGetHandle()` on every report, skip tasks that have exited |

The second one is the interesting one: the board rebooted every ~10 s, which the
collector saw as a reboot signature in the recovered frames, and `addr2line`
against the ELF pinned the fault to the agent itself.

Also found while measuring: the firmware was configured for 2 MB of flash while
the chip has 4 MB. `esptool flash_id` settled it, and both projects now build
with `CONFIG_ESPTOOLPY_FLASHSIZE_4MB`.

### Reference capture

71 s of a healthy run (light sensor project, no peripherals attached):

```
heap.free                  240,440 -> 205,272   (WiFi init costs ~35 KB)
heap.min                   239,832 -> 200,120
task.main.stack_free_min     2,076 ->   1,404   <- tightest non-idle task
task.Monitor.stack_free_min  1,692 ->   1,596
task.telemetry.stack_free_min 2,456 ->  2,152
```
## Validation against a real converter output

The offline fixture could not catch every class of mistake, so the parser was
also run against a model produced by the real TensorFlow converter:
`training/train_waveform_classifier.py` trains a 3-class waveform classifier,
quantises it to int8 and writes `training/out/waveform_model.tflite`.

| Check | Result |
|---|---|
| Operators | 3 x FULLY_CONNECTED + 1 x SOFTMAX, matching the Keras model |
| Model size / weights | 6,544 B total, 2,924 B of weights, 2,659 parameters |
| Tensor arena peak | 96 B, against a reuse-free upper bound of 118 B |

That run immediately found a parser bug: it treated `buffer_index != 0` as
"constant". Converters assign a buffer to *every* tensor and only the ones that
carry weights have data in them, so every activation was misclassified and the
arena came out as 0 B. A tensor is constant when its buffer actually holds data.

This supersedes the earlier note that validation against a real model was still
outstanding.

## Training a model

```powershell
.venv\Scripts\python.exe training\train_waveform_classifier.py
```

The script synthesises waveforms, applies the same DSP the firmware uses
(integer mean removal, Hann window, 128-point FFT, magnitude), normalises the
spectrum and trains a small MLP. 64 features -> 32 -> 16 -> 3 classes.
## The console UART is not driver-backed by default

Symptom: telemetry frames arrived with one or two bytes missing roughly half
the time, while ESP_LOG lines were never damaged. The device-side checksum
proved the bytes left the chip intact, so they were being lost on the way out.

Cause, found by reading `esp_vfs_console` and `uart_vfs.c`:

* ESP-IDF's default console installs **no UART driver**. Console writes go
  through a ROM path one byte at a time (`uart_tx_char` busy-waits for FIFO
  space); `uart_write_bytes()` simply fails with `uart driver error` until a
  driver exists.
* A frame of several hundred bytes therefore races every other console writer
  for FIFO slots. Log lines are short, which is why they never suffered.

Fix, entirely inside the component:

1. install the UART driver for the console port and route stdio through it
   (`uart_driver_install` + `esp_vfs_dev_uart_use_driver`), so every writer
   shares one interrupt-driven TX path;
2. write frames with `uart_write_bytes()` in a retry loop that respects the
   returned count, instead of a VFS write that discards it.

Measured on the signal-processing project with WiFi and MQTT connected:

| | corrupted frames |
|---|---|
| before | 87% |
| after | 0% (20 of 20 frames over 105 s) |

Cost: about 12 KB of flash and 2 KB of TX ring-buffer RAM. The agent falls back
to the VFS path if the driver cannot be installed, so it never regresses to
silence.
## Case study: closing a 17 point accuracy gap

The first waveform classifier was trained on an idealised simulation. On device
its steady-state accuracy was 73% overall and 29% for the sine class. Two
separate causes, both found from telemetry rather than by guessing:

1. **Distribution shift.** A harmonic-profile diagnostic (fundamental plus
   harmonics 2..5, printed by the board) showed the real signal chain leaves far
   more harmonic content than the simulation: third harmonic 5.3% measured
   against 0.1% simulated for a sine, a factor of 53. The model had learned that
   a sine is almost pure, so a real sine looked like a triangle to it.
2. **A label-ordering bug in the demo.** The demo rotated the DAC before the
   inference ran, so that frame was labelled with the new waveform while still
   carrying the old one. One frame in six was therefore always wrong.

Fixes: capture 735 real feature vectors from the board and retrain on them, and
read the label before rotating the source.

| | simulated training | board-captured training |
|---|---|---|
| overall accuracy | 61% | 99% |
| sine / square / triangle | 29% / 83% / 76% | 97% / 100% / 100% |
| inference P50 / P99 | 10.3 / 13.5 ms | 7.3 / 10.5 ms |

A caution from the same session: adding 26 KB of diagnostic code (the harmonic
dump and a floating-point feature dump) moved the measured P50 from 7.5 ms to
18.8 ms on an identical model. Latency is layout sensitive on this chip, so only
compare builds that differ in the thing under test.

A later measurement changed the latency number in that table. The 7.3 ms P50 was
not the model. The software DAC pushed one table sample per `esp_timer` callback,
about 20 000 interrupts per second, and that stole CPU from the inference task.
Moving the DAC to continuous mode on the DMA engine removed the interference: the
same model then measures **P50 1.0 ms / P99 1.25 ms**. The same change fixed the
output frequency, which had been 78 Hz for a requested 200 Hz and now tracks the
request (203 Hz measured). Two lessons: a resource metric can be dominated by a
neighbour rather than by the code under test, and an instrument that sits on the
critical path will lie.

## HTML report

`report` turns one capture into a single self-contained page: checks, baseline
changes, metric trends, build resources and the fault injection matrix. The data
is inlined and the charts are hand-written SVG, so the file opens from disk with
no server and no CDN, and it can be committed or published as-is.

```powershell
python -m mcu_insight report `
    --db captures/fault-off.db `
    --config budgets/signal.json `
    --map build/signal_processing_system.map `
    --bin build/signal_processing_system.bin `
    --partition 1500K `
    --faults captures `
    --out docs/report-demo.html
```

A change is only coloured when the worrying direction is known: free heap, stack
headroom, idle share and accuracy falling, latency, jitter and drift rising.
Metrics whose direction depends on context (uptime, sample counts, the classified
label) are listed without a verdict rather than guessed at. The fault captures are
re-checked against `fault_matrix.json` instead of the application budget, because
the accuracy rule reads low during the first seconds after a boot and would
otherwise flag the clean baseline as a regression.

A capture can span a reboot, which is normal while a board is being reflashed or
reset. The trend cards therefore draw a continuous time axis, mark every reset
with a dashed line, and compute their statistics from the newest boot session
only, skipping the first ten seconds after it because start-up is still
allocating Wi-Fi and MQTT buffers. That matters most for high-water-mark
metrics: `heap.min` is at its post-boot peak right after a reset, so comparing
the first sample with the last would report a 25 KB "leak" that is really the
first second of the run. A capture whose newest session is shorter than a rate rule's `min_span_ms`
renders as INCOMPLETE rather than PASS. `captures/signal-reboot.db` does
exactly that: its second session holds fifty five seconds of steady state, and
fifty five seconds is not enough to measure a slope in.
The checks are unaffected and still cover every frame,
so nothing is hidden from the pass/fail verdict.

## Thresholds and rates

A rule can bound a value or a slope:

```json
{"metric": "heap.min", "min": 100000, "stat": "min"},
{"metric": "heap.free", "min_rate_per_s": -64, "min_span_ms": 60000}
```

The first is a floor. The second says the free heap may not fall faster than 64
bytes per second, measured by least squares over the steady part of the newest
boot session.

Both are needed, for a reason worth stating plainly. `heap.min` is a high-water
mark: it only ever falls, so a floor rule's verdict depends on how long the
capture ran. A 100 B/s leak starting from 145 KB needs 450 s to cross a 100 KB
floor, which means a two minute capture passes the level rule with the leak
plainly present. A slope does not care about duration, which is why the fault
matrix can catch an injected leak from a hundred second capture.

`min_span_ms` is the other half. A slope measured over ten seconds is noise, so
a rule can refuse to judge until it has enough steady state behind it. Refusing
produces `INCOMPLETE`, not a pass, and `ok` is false: a build cannot be called
green on the strength of a check that was never carried out.

Slopes are measured after the boot warm-up and inside the newest boot session,
the same way the report reads its trend cards.
## Baselines carry their shape

```powershell
python -m mcu_insight check --db clean.db --config budgets/signal.json `
    --save-baseline budgets/signal_baseline.json --project ../firmware
```

The snapshot records the revision it came from, how many frames and boot
sessions it covers, how long its steady state lasted, and per metric the last
value together with its extremes, its sample count and its steady slope.

A flat value could say that something changed. It could not say whether the
change was real or just a shorter soak, because a high-water mark keeps falling
the longer you watch it. With the slope on both sides the question answers
itself, and the length of each capture is in the file:

```
baseline capture: 21 frames, 1 boot session(s), 90 s of steady state
baseline rates per second, then now: heap.free +0.1/s -> -956.9/s; heap.min -0.1/s -> -958.2/s
note the baseline ran 90 s of steady state against 95 s here, so the level deltas are comparable
```

When the two durations differ by more than a factor of two the pack says so and
points at the rates instead. A baseline written before this existed is lifted
into the new shape without inventing anything: its capture block stays empty,
and the pack falls back to the one duration the file does carry, the
`uptime_ms` the device had reached when the snapshot was taken.
## Diagnosis: the evidence pack

Detection is arithmetic; explaining a detection is not. `diagnose` collects
what is *known* into one bounded, numbered pack and stops there. It never
invents a fact and never calls a model on its own, which is what makes the
whole step testable offline:

```powershell
python -m mcu_insight diagnose `
    --db captures/fault-heap.db `
    --config budgets/signal.json `
    --map build/signal_processing_system.map `
    --project ../ESP32-Signal-Processing-System-github `
    --partition 1500K `
    --dry-run --out prompt.txt
```

The pack carries the violations next to the rule text, the full series for the
metrics that matter, a summary of every other metric, the build's flash and RAM
numbers, the sdkconfig keys that can move a resource figure, and the git state
of the tree that produced the firmware. Every line is numbered `[E12]` so an
answer can be checked rather than trusted.

The pack opens with what the capture *is*. A database named after a fault
matrix case says so on its first lines, because a drain that is a test case is
not a regression, and a model that does not know the difference will name a
call site that is doing exactly what it was told to do. `--note` carries
anything the file name cannot.
Every metric line carries its unit and how the number behaves:
`heap.min (B, high-water mark, monotonic since boot)` next to
`heap.free (B, instantaneous sample)`. That is not decoration. A high-water mark
only ever gets worse, so a falling `heap.min` is the deepest point reached since
boot rather than a trend, and reading it as one turns start-up into a leak. The
suffix convention covers the usual cases, and the project file overrides
anything the name cannot say:

```json
"metrics": {
  "custom.sample_rate_hz": {
    "unit": "Hz",
    "kind": "build-time constant",
    "description": "ADC rate fixed at build time; it does not follow the demo waveform frequency"
  }
}
```
The pack also carries the change itself, because a shape can be described
without it but a call site cannot be named. `--diff-since REV` diffs from that
revision to the working tree, capped and split into one evidence entry per
file. With no revision given it diffs the working tree against `HEAD`, and if
the tree is clean it falls back to the most recent commit and labels it as a
candidate, since a clean tree says nothing about which change is under test.
The cap is 300 lines, 160 per file, and `--patch-lines` moves it: a patch that
stops before the changed line is worse than no patch.

A baseline can record where it came from, so the range is known rather than
guessed:

```powershell
python -m mcu_insight check --db capture.db --config budgets/signal.json `
    --save-baseline budgets/signal_baseline.json --project ../firmware
```

Comparison ignores that `_meta` block, so baselines saved before it still work.
Without a recorded revision the pack says the range is unknown instead of
quietly diffing against whatever `HEAD` happens to be.
Two rules keep it useful. The pack is trimmed to a size budget, dropping series
for metrics nothing flagged, because evidence that cannot be read in one go is
evidence that gets ignored. And a series that never moves collapses to one
line, since twenty-one identical readings cost the same as one and say less.

A change is labelled `worse`, `better` or `moved` using the same direction
rules as the HTML report, so a shorter uptime or a lower sample count is not
presented to the model as a regression. The prompt that `--out` writes asks for
an evidence id behind every claim, for an explicit "the evidence cannot tell
these apart" when that is the honest answer, and for the smallest change plus
the command that would verify it.
## Auditing the answer

The manual path has one obvious hole: nothing checks the answer. `audit` closes
it mechanically and without a model:

```powershell
python -m mcu_insight audit --pack prompt.txt --answer answer.md
```

Three things are checked. Every `[E12]` the answer cites has to exist in the
pack, so a fabricated citation is caught instead of believed. A dotted name
belonging to a metric family the pack knows has to be a metric the pack
contains, which catches an invented metric while leaving `main.c` alone. And
every claim with no citation is listed as a warning, counting a wrapped bullet
as one claim rather than one per line. Exit code 1 means the answer leans on
evidence that does not exist.
## Fault injection matrix

`tools/fault_matrix.py` injects one fault at a time through the firmware switch
(built with `ENABLE_FAULT_INJECTION = 1`), records telemetry and asserts that
the budget checks notice:

| injection | what changes | detected |
|---|---|---|
| baseline (`FAULT off`) | nothing | no violation (correct) |
| `FAULT heap` | leaks 2 KB per loop: heap.min 144,560 -> 50,176 | yes |
| `FAULT stack` | 1800 byte frame in a 2048 byte task: stack free 1536 -> 32 | yes |
| `FAULT busy` | high priority busy loop: idle0 90% -> 0% | yes |

The runner exists because a naive script gets three things wrong, each of which
cost a measurement cycle to find:

* **the first console command after a reset is eaten by the bootloader**, so a
  throwaway command is sent first;
* **a leak is cumulative**, so every case starts from a fresh boot;
* **a running average restarted by that boot is noise**, which the rules handle
  with `min_samples`.

Two of the injected faults were initially invisible because the compiler
deleted them: an unused `malloc` was elided, and a `memset` through a `void *`
cast dropped the `volatile` qualifier so the whole stack frame vanished. A
fault that the optimiser removes proves nothing.

One more lesson the matrix encoded: the **stack high-water mark is a historical
worst case**, so a regression on a shallow path is invisible if start-up already
went deeper. The stack fault therefore runs in a task whose deepest point is
the fault itself, which is also how a real handler with a large scratch buffer
behaves.