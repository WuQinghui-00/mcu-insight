# ESP32 heap.min budget violation — diagnosis (v3 evidence pack)

Evidence ids below are the **v3** pack numbering ([E1]–[E99]). v3 adds the diff ([E95]–[E99]) and
corrects two metric semantics, so this supersedes answer2.md.

## Root cause

The deliberately injected leak in `main/main.c`: `s_leak_sink = malloc(2048)` executed once per main
loop iteration while `s_fault == FAULT_HEAP` [E98]. The pack's own provenance states the fault is
injected on purpose [E3], the capture is taken by `tools/fault_matrix.py --port COM19` [E4], and the
fault is armed at run time over the console, where `FAULT heap` means "leak 2 KB per loop" [E98].
Confidence: **high**, because the leak arithmetic lands on 2,048 B per loop iteration.

Derivation:

- `heap.free` (instantaneous sample [E26]) falls from 144,820 B at 8,380 ms to 50,372 B at
  108,420 ms [E27] — 94,448 B over 100,040 ms.
- `custom.infer_samples` (counter since boot [E81]) goes from 3 to 49 over the same capture, i.e. 46
  loop iterations [E81].
- 94,448 B ÷ 46 = 2,053 B per iteration against the 2,048 B allocation in the patch [E98] — a 0.2%
  match, the residual being ordinary churn. Equivalently, 46 × 2,048 B = 94,208 B, 99.75% of the
  observed drop [E27], [E81], [E98].
- The step quantisation says the same thing: `heap.free` never rises and drops by ~4,104 B or
  ~6,156 B per 5 s frame, with the one 10 s frame at exactly the sum, 10,260 B [E27]. Those are
  2 × 2,048 and 3 × 2,048 B [E98] — 2.5 leaks per frame, which is the beat between a ~2 s loop
  (30 loops = 60 s [E98]) and a 5 s telemetry cadence.
- The injected allocation is the only heap-growing change in the diff: the telemetry additions in
  `components/mcu_telemetry/mcu_telemetry.c` are `static` arrays (`s_idle[2]`,
  `static TaskStatus_t statuses[...]`) [E97], which consume static DRAM, not heap. Static DRAM is
  47,952/180,736 B [E91].
- Everything else stays healthy. The only violated rule is [E7] against [E8]: [E9] vs 444 B [E33],
  [E10] vs the smallest task value 496 B [E57], [E11] vs 90–94% [E67] and 99–100% [E65], [E12] vs
  1,250 µs [E73], [E13] vs 100 [E60].
- `heap.largest` tracks `heap.free` down for the same reason: 107,856 vs 106,496 B at 48,410 ms
  [E27], [E29], and 50,372 vs 49,152 B at 108,420 ms [E26], [E28].
- The ~24,680 B lost in the first frame (3,378 → 8,380 ms) [E27] is about six times one loop's leak
  [E98]; that frame is boot-window allocation and is **not** attributable from this pack.

## Alternatives

- **A real application regression.** Ruled out twice: by the provenance statement [E3], and by the
  diff, where the only heap-growing addition is the injected `malloc(2048)` [E98]. The separating
  observation if you distrust the file name: capture with `FAULT off` [E98] and compare slopes.
- **Growth inside the model/interpreter path.** Ruled out: `custom.arena_used_bytes` is constant at
  1,316 B across 20 samples [E62], [E63], and inference latency is flat (`infer_p50_us` 1,000 µs
  [E70], [E71]; `infer_max_us` 1,347–1,348 µs [E68], [E69]).
- **Transient dips rather than a one-way decline.** Ruled out: the instantaneous series [E27]
  decreases at every one of the 20 frames, and `heap.free`'s minimum 50,372 B [E26] sits only 204 B
  above the monotonic minimum 50,168 B [E24]. Because `heap.min` is defined as a high-water mark
  that is monotonic since boot [E24], `last == min` on that metric proves nothing by itself; the
  argument rests on [E27].
- **Fragmentation without net byte loss.** Ruled out by the free-vs-largest pairs above ([E27] vs
  [E29], [E26] vs [E28]): the gap stays ~1.2–1.4 KB while free bytes genuinely leave the pool.
- **The other injected faults being active.** Ruled out: the stack rules pass ([E9] vs 444 B [E33];
  [E10] vs 496–7,284 B across tasks [E57], [E45], [E53], [E35], [E37], [E43], [E55], [E59], [E51]),
  and idle share passes [E11] at 90–94% [E67] and 99–100% [E65]. `task.fault_busy` is present in the
  task table with a constant 1,544 B minimum [E42], [E43], consistent with the 2,048 B stack that
  `xTaskCreate(fault_busy_task, ...)` requests [E98], but it is not burning CPU, so `FAULT busy` is
  not selected [E98].
- **A harness artefact of capture length.** Not the cause here, but it does distort the reported
  comparison: [E7] is applied to a since-boot monotonic minimum [E24], while the capture ran
  108,420 ms [E90] and the baseline ran 248,337 ms [E17]. Part of the "worse" deltas is duration,
  not rate. The separating measurement is the leak slope in bytes/s from [E27], not the minimum.
  Supporting check: `custom.infer_samples` is 49 [E81] over 108.4 s [E90], i.e. ~0.45/s, and the
  baseline's 114 [E16] over 248.3 s [E17] is also ~0.46/s — the cadence is unchanged, so the lower
  count is just the shorter capture.
- **The baseline comparison itself.** Unresolved by construction: the baseline file records no
  revision [E6], and the supplied diff is stated as "since a33a18e~1" [E95], which the pack does not
  establish as the baseline revision. So the "changed" deltas [E14]–[E23] cannot be attributed to a
  known change set.
- **The earlier 100 Hz vs 1,000 Hz question is now closed**: `custom.sample_rate_hz` is the ADC rate
  fixed at build time and "does not follow the demo waveform frequency" [E86], while the patch moves
  the DAC demo to `DEMO_WAVE_FREQ_HZ 100` [E98]. Cosmetically the log strings still say otherwise in
  the same patch — `"DAC: 1kHz sine wave on GPIO25"` and `"Demo source switched to %s at 200 Hz"`
  [E98].

## Fix

The smallest change that addresses the root cause is to stop arming the fault: either capture with
`FAULT off` [E98], or build the regression image with `ENABLE_FAULT_INJECTION 0` [E98]. No
application code needs to change — the diff's only heap-growing line is the injected allocation
[E98], and the checker behaved correctly by flagging it [E7], [E8]. If the intent was to verify that
the observability tooling notices a leak, there is nothing to fix and this capture is a passing test
of the tool, not a failing build [E3].

One caveat worth fixing separately: because the rule is on a monotonic since-boot minimum [E24],
pass/fail depends on capture length; a rate rule (bytes/s derived from `heap.free` [E26]) would
compare runs of different durations fairly.

## Verification

Re-run `tools/fault_matrix.py --port COM19` [E4] and watch `heap.free` [E26] rather than `heap.min`
[E24].

- With `FAULT heap` armed, the violation must reproduce: `heap.min` below 100,000 [E7], [E8], with
  the crossing between the 48,410 ms frame at 107,624 B and the 58,420 ms frame at 97,364 B [E25].
  Expect the per-5 s steps to stay in the 4,104–6,156 B band [E27], i.e. ~1,024 B/s at the 2 s loop
  period [E98].
- With `FAULT off` [E98], the per-5 s steps should collapse from ~4–6 KB [E27] to ~0, the total
  94,448 B seen between 8,380 ms and 108,420 ms [E27] should not accrue at all (46 iterations ×
  2,048 B = 94,208 B [E81], [E98]), and `heap.min` should stay above 100,000 [E8] for at least
  248,337 ms [E17], ending near the baseline's 147,956 B [E14] instead of 50,168 B [E24].
- Run the full ≥248 s. Starting headroom is only 169,500 B at 3,378 ms [E27] against a 100,000 B
  floor [E8], so a short capture can pass while the leak is still present.
- Two things to ask for rather than infer: the baseline revision so the diff range can be pinned with
  `--diff-since` [E6], [E95], and the baseline series, without which the deltas [E14]–[E23] cannot be
  separated from the capture-length difference [E17], [E90].
