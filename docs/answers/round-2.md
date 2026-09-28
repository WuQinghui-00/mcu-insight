# ESP32 heap.min budget violation — diagnosis (v2 evidence pack)

Evidence ids below are the **v2** pack numbering ([E1]–[E93]). The v1 numbering differs, so the two
files are not interchangeable.

## Root cause

An intentionally injected heap leak, not an application regression. Confidence: **high** — the pack
states the provenance directly, and the series is consistent with it.

- The capture's own provenance says the fault was injected on purpose: "FAULT heap, a heap leak is
  injected on purpose … deliberate test behaviour, not a regression in the application code" [E3].
- The capture was taken on 2026-09-28 by `tools/fault_matrix.py --port COM19` against firmware
  d8f452b [E4], the same revision as the working tree [E93].
- Only one rule is violated: [E6] against [E7]. Everything else the budget checks is satisfied —
  [E8] vs 444 B [E31], [E9] vs the smallest task value 496 B [E55], [E10] vs 90–94% [E65] and
  99–100% [E63], [E11] vs 1,250 µs [E71], [E12] vs 100 [E59]. The failure is memory-only; [E18]
  even improves.
- The leak presents as a secular decline of *instantaneous* free heap: `heap.free` [E25] falls at
  every one of the 20 observed intervals [E26], ~24,680 B in the first ~5 s, then ~94,448 B over the
  next ~100 s — about 944 B/s steady, 1,134 B/s averaged over the capture, for −66.0% against the
  baseline [E14].
- The per-interval steps are quantised, never zero and never positive: ~4,104 B or ~6,156 B per 5 s
  snapshot, and the single 10 s gap (48,410 → 58,420 ms) drops 10,260 B = 4,104 + 6,156 [E26]. A
  constant-rate leak, not event-driven bursts.
- `heap.largest` is flat at 110,592 B for the first nine samples and only then declines to 49,152 B
  [E28], while `heap.free` and `heap.largest` stay ~1.2–1.4 KB apart (107,856 vs 106,496 at
  48,410 ms [E26], [E28]; 50,372 vs 49,152 at 108,420 ms [E25], [E27]). The footprint is growing;
  the pool is not merely reshuffling.

Because `heap.min` is defined in the pack as a high-water mark that is monotonic since boot [E23],
`last == min` on that metric proves nothing by itself. The claim above rests on `heap.free`, which
is an instantaneous sample [E25] — hence the emphasis on [E26] rather than [E24].

## Alternatives

- **A real regression in application code.** Ruled out only by the pack's own provenance statement
  [E3], which carries the hedge "unless it was renamed". The separating observation: re-capture with
  the injection disabled using the same command [E4] against the same firmware [E93], then compare
  slopes. A diff between the baseline and d8f452b would settle it too; the pack contains neither.
- **Transient dips rather than a sustained decline.** Ruled out: the instantaneous series [E26]
  decreases at every interval with no recovery, and the instantaneous minimum 50,372 B [E25] is only
  204 B above the monotonic minimum 50,168 B [E23]. If dips dominated, `heap.min` would sit far below
  `heap.free`'s minimum.
- **Fragmentation with no net byte loss.** Ruled out by the paired values above ([E26] vs [E28],
  [E25] vs [E27]): free bytes genuinely leave the pool, and the free-to-largest gap stays roughly
  constant rather than widening.
- **A one-time increase in boot or static allocations.** Can explain only the ~24,680 B first step
  [E26]; it cannot produce the decline across the remaining 19 intervals [E26].
- **Stack exhaustion forcing work onto the heap.** Ruled out: the tightest stack rule passes, [E8]
  vs 444 B [E31], and [E9] against 496–7,284 B across tasks [E55], [E43], [E51], [E33], [E35],
  [E41], [E53], [E57], [E49].
- **Model arena growth.** Ruled out: `custom.arena_used_bytes` is constant at 1,316 B over 20 samples
  [E61], [E62].
- **Network-stack buffers growing at runtime.** Cannot be separated from this pack, and it is moot
  given [E3]. Note that [E91] gives static image sizes (`esp_wifi` 206,644 B, `mbedtls` 161,627 B,
  `lwip` 103,216 B), not runtime growth. `custom.uart_tx_retries` stayed 0 [E86].
- **A harness artefact of capture length.** Not the cause of this violation — the instantaneous
  series already shows the loss [E26] — but it does bias pass/fail: rule [E7] is applied to a
  minimum that is monotonic since boot [E23], so a shorter soak can pass. The baseline ran 248,337 ms
  [E16] while this capture ran 108,420 ms [E89]. Separating measurement: compare the leak *slope*
  (bytes/s from [E26]) instead of the minimum.
- **Whether the 100 Hz change is in this build.** Unresolved. The commit says the demo should run at
  100 Hz [E93], but `custom.sample_rate_hz` is documented as a build-time constant reading 1,000
  [E85], and `custom.infer_samples` counted only 49 windows since boot [E80] over 108 s [E89]. The
  pack cannot tell whether the constant is unrelated to the demo rate, or this build is not what the
  commit message describes. [E3] also leaves open that the injection modified such constants.

## Fix

Since the leak is deliberate [E3], the smallest change that addresses the root cause is not in
application code: disable the injection for regression captures of d8f452b [E4], [E93], and keep the
FAULT-flagged capture out of the pass/fail baseline. If the goal was to validate the checker, no
change is needed at all — [E6] against [E7] is the checker working as intended.

The pack does not name the injection site, its configuration key, or the hook inside
`tools/fault_matrix.py` [E4], so the specific line to change cannot be named from this pack; send the
tool's injection configuration and the diff to make that concrete. If the leak is meant to be a
permanent soak fault rather than test scaffolding, then the fix belongs at the injection site, which
is likewise not in the pack.

## Verification

Re-run `tools/fault_matrix.py --port COM19` [E4] and look at `heap.free` [E25] rather than `heap.min`
[E23]:

- With the injection on, the violation must reproduce: `heap.min` below 100,000 [E6], [E7], with the
  crossing roughly 50–55 s in — between the 48,410 ms sample at 107,856 B and the 58,420 ms sample at
  97,596 B [E26].
- With the injection off, the per-5 s decline in `heap.free` should go from ~4,104–6,156 B [E26] to
  ~0, and `heap.min` should stay above 100,000 [E7] for at least the baseline's 248,337 ms [E16],
  ending near the baseline's 147,956 B [E13] rather than the current 50,168 B [E23].
- Run the full ≥248 s. Because starting headroom is only 169,500 B at 3,378 ms [E26] and the budget
  floor is 100,000 B [E7], a short capture can pass while a real leak is present.
- If a residual decline near the current ~944 B/s ([E26]) survives with the injection off, the cause
  lies outside the injection, and a per-call-site heap trace over the same capture [E2] is the next
  measurement.

Still missing from the pack: the diff between the baseline and d8f452b, the baseline build id/number,
the baseline series, the injection configuration inside `tools/fault_matrix.py` [E4], and
clarification of the 100 Hz change versus the 1,000 Hz build-time constant [E85], [E93].
