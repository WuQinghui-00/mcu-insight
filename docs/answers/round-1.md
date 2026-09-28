# ESP32 heap.min budget violation — diagnosis

Answered from the MCU-INSIGHT evidence pack [E1]–[E91] only. Note: the shell in this session
cannot launch its sandboxed helper, so every command run needed escalation; that was used only to
confirm the two repositories exist, and nothing below comes from them.

## Root cause

A steady, one-directional heap loss that starts at boot and never recovers — memory is allocated and
not released during the run. Confidence: **high** that this is unbounded growth rather than
placement; **low** on the specific call site.

- `heap.min` is the only violated rule: [E4] against [E5]. Every other budget passes ([E6] vs 444 B
  [E29]; [E7] vs the smallest task value 496 B [E53]; [E8] vs 90–94% [E63] and 99–100% [E61]; [E9]
  vs 1,250 µs [E69]; [E10] vs 100 [E57]). So this is memory-only, not a latency or accuracy
  regression — [E16] even improves.
- `heap.min` declines monotonically over all 21 samples, 169,500 → 50,168 B, and `last == min`
  [E21], [E22] — no plateau, no recovery. `heap.free` tracks it [E23], [E24], so real free bytes are
  disappearing (also visible as [E12] vs [E11]).
- Shape of the drain: ~24,920 B in the first ~5 s (3,378 ms → 8,380 ms), then ~94,412 B over the
  following ~100 s [E22] — roughly 0.94 KB/s steady, 1.14 KB/s averaged over the capture. The
  48,410 → 58,420 ms pair spans 10 s and drops 10,260 B [E22], the same rate, which supports a
  constant-rate leak rather than bursty growth.
- `heap.largest` is flat at 110,592 B for the first nine samples and only then falls to 49,152 B
  [E26], [E25]; that is the signature of a growing live footprint, not a fixed layout change.
- The budget is missed in less than half the baseline's window: 108,420 ms now vs 248,337 ms
  baseline [E14], [E87], with the same rule [E5] in force.

## Alternatives

- **Fragmentation with no net leak.** Ruled out: `heap.free` falls in lockstep with `heap.min`
  ([E24] vs [E22]), and the gap between `heap.free` and `heap.largest` is nearly constant —
  107,856 − 106,496 = 1,360 B at 48,410 ms ([E24], [E26]) vs 50,372 − 49,152 = 1,220 B at
  108,420 ms ([E23], [E25]).
- **A one-time increase in boot/static allocations.** Can only explain the ~24,920 B first-step drop
  [E22]; it cannot produce the decline across the remaining twenty samples [E22], [E24]. Ruled out
  as the whole cause.
- **Stack exhaustion pushing work onto the heap.** Ruled out: every stack rule passes, including the
  tightest, [E6] vs 444 B [E29], and [E7] against 496–7,284 B across tasks [E53], [E41], [E49],
  [E31], [E33], [E39], [E51], [E55], [E47].
- **Model arena growth.** Ruled out: `custom.arena_used_bytes` is constant at 1,316 B across 20
  samples [E59], [E60].
- **Retained network-stack buffers.** Cannot be separated from this pack. `esp_wifi` 206,644 B,
  `mbedtls` 161,627 B and `lwip` 103,216 B are the largest components [E89], and sustained
  TLS/MQTT buffering is a classic source at this rate. The separating measurement is per-call-site
  heap attribution over the same capture [E2]; note `custom.uart_tx_retries` stayed 0 [E84], so UART
  backpressure is not implicated.
- **A per-iteration allocation in the path the commit touched.** The inference path ran only 49 times
  [E78] while ~94 KB drained [E22], i.e. ~1.9 KB per inference — consistent, not decisive. Two things
  would settle it: the diff, and a trace keyed to sample count. Related inconsistency: the commit
  says the demo should run at 100 Hz [E91] but `custom.sample_rate_hz` reads a constant 1,000 [E83]
  — either that metric is a configured constant rather than the effective rate, or the change is not
  in effect. The pack cannot tell which.

## Fix

The pack names no allocation site, so the only change justifiable from it is structural: make the
per-iteration allocation in the path changed by d8f452b either freed each iteration or hoisted to a
one-time init allocation. The exact call to change is not derivable from [E1]–[E91]; send the diff
between the baseline and d8f452b and it can be named. Note also that available headroom is only
~169.5 KB at 3.4 s [E22], so if the first-step ~24,920 B [E22] turns out to be permanent rather than
leaked, it needs to be reclaimed too.

## Verification

Re-run the capture that produced `captures\fault-heap.db` [E2] — the pack does not include the
command that generated it, so send it or reuse the existing harness — and run it for at least the
baseline's 248,337 ms [E14], not the 108 s used now.

- Pass condition: `heap.min` [E21] stays ≥ 100,000 for the whole run ([E4], [E5]), and the
  `heap.free` series [E23], [E24] is flat instead of losing 4,104–6,156 B per 5 s [E22].
- Do not accept a short test: at the current rate `heap.min` crosses 100,000 roughly 55 s into the
  boot — between the 48,410 ms sample at 107,624 B and the 58,420 ms sample at 97,364 B [E22].
- Separately confirm whether `custom.sample_rate_hz` [E83] reports 100 after the change, given
  [E91].

Still missing from the pack: the baseline build id/number, the baseline series, and the diff between
baseline and d8f452b. With those, the "high confidence in the shape, low confidence in the site"
split can become a named root cause.
