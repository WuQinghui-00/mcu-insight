# MCU-Insight

Resource observability for embedded firmware (ESP-IDF / FreeRTOS).

The tool answers questions a code review cannot answer:

* how much flash and static RAM does this build actually consume, and what is left;
* which component grew between two builds, and by how much;
* how much of the RAM budget the heap and task stacks can still use.

## Status

Stage 1 of the project: build-time (static) resource accounting.
Runtime telemetry and TinyML model analysis are planned next.

## Usage

```powershell
python -m mcu_insight.cli analyze build/your_app.map `
    --bin build/your_app.bin `
    --partition 1500K
```

`--json` emits machine-readable output for regression tracking.

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
