# Case study: the same model, the same capture, three evidence packs

Measuring an embedded device produces numbers. Being *believed* about it needs
evidence that can be checked, and this is a record of three model answers
finding holes in that evidence that reading the code had not.

## The question

An ESP32 runs a 1 kS/s waveform classifier with a telemetry agent. One capture
reports:

```
[E4] VIOLATION heap.min: 50168 is below 100000
```

The interesting part is not that a budget was missed. It is why, and whether
the answer deserves trust.

## The method

One capture throughout: `captures/fault-heap.db`, 21 frames, 100 seconds. The
only thing that changes is the evidence pack handed to the model, which the
tool generates rather than a human writing it.

| round | evidence | what the pack added |
|---|---|---|
| 1 | 91 lines | the violation next to the rule text, the series of the metrics that matter, the build numbers, the sdkconfig keys, the git state |
| 2 | 93 lines | what the capture *is*, and a unit and a kind for every metric |
| 3 | 99 lines and a 289 line patch | the change itself, one evidence entry per file |

Each round's prompt and answer are kept: `docs/prompts/round-N.txt` and
`docs/answers/round-N.md`.

## Round one: right shape, wrong cause

The answer read the series correctly.

> A steady, one-directional heap loss that starts at boot and never recovers.
> Confidence: high that this is unbounded growth rather than placement; low on
> the specific call site.

It then found the number that names the cause, and blamed the wrong code for it.

> The inference path ran only 49 times [E78] while ~94 KB drained [E22], i.e.
> ~1.9 KB per inference — consistent, not decisive.

1.9 KB per inference. The injected fault allocates 2,048 bytes per loop
iteration, so the measurement was right to within seven percent and the
conclusion — an application buffer in the inference path is never freed — was
wrong.

What the pack never said is where the capture came from. The model asked for it
("the pack does not include the command that generated it"), which is the
honest answer to an ambiguous pack, but the ambiguity belonged to the tool.

## Round two: say what the capture is

Two things changed: a provenance line, and a unit and a kind for every metric.

```
[E3] provenance FAULT heap, a heap leak is injected on purpose...
[E23] metric heap.min (B, high-water mark, monotonic since boot)
[E25] metric heap.free (B, instantaneous sample)
```

The same model, on the same data, threw out its own earlier reasoning.

> An intentionally injected heap leak, not an application regression.
> Confidence: high — the pack states the provenance directly [E3].

> Because `heap.min` is defined in the pack as a high-water mark that is
> monotonic since boot [E23], `last == min` on that metric proves nothing by
> itself. The claim above rests on `heap.free`, which is an instantaneous
> sample [E25].

In round one it had used `last == min` as evidence of "no recovery". In round
two it explained why that inference is worthless on a high-water mark, and
moved the argument to a metric that can carry it.

## Round three: the line itself

With the patch in the pack, the answer became specific.

> The deliberately injected leak in `main/main.c`: `s_leak_sink = malloc(2048)`
> executed once per main loop iteration while `s_fault == FAULT_HEAP` [E98].

It then checked the line against the measurements rather than the other way
round. `heap.free` falls 94,448 B over the capture [E27]; `custom.infer_samples`
runs 3 to 49, so 46 iterations [E81]; 94,448 ÷ 46 = 2,053 B per iteration
against the 2,048 B allocation, a 0.2% match. It also decoded the step
quantisation: the per-frame drops are 4,104 and 6,156 B, which are 2 × 2,048
and 3 × 2,048 — the beat between a two second loop and a five second telemetry
cadence.

Citations per round, as counted by `mcu-insight audit`: 41 distinct of 74, then
43 of 96, then 50 of 126. No fabricated ids in any of the three.

## What the answers changed in the tool

Every finding below came from an answer, not from reviewing the code.

| the answer said | what changed |
|---|---|
| "the pack does not include the command that generated it" | captures named after a fault matrix case declare themselves on the first lines, and `--note` carries the rest |
| "the pack cannot tell whether `custom.sample_rate_hz` is a constant or a measurement" | every metric carries a unit and a kind, from a naming convention that the project file overrides |
| "the pack names no allocation site" | `--diff-since` puts the patch in the pack, one entry per file, and a baseline records the revision it came from |
| "the rule is applied to a since-boot monotonic minimum, so a shorter soak can pass" | rate rules in units per second, and `min_span_ms`, which produces INCOMPLETE rather than a pass |
| "the deltas cannot be separated from the capture-length difference" | baselines record their shape and their slopes, and the pack states whether two durations are comparable |

## Auditing the answer, and three false positives

`mcu-insight audit` checks an answer against the pack instead of trusting it:
every cited id has to exist, a dotted name in a metric family the pack knows
has to be a metric the pack contains, and every claim with no citation is
listed.

The name check has been wrong three times, and each time a real answer found it
rather than a test: a capture path (`captures/fault-heap.db` read as the metric
`heap.db`), a unit label leaking into the parsed name, and a task name
(`task.fault_busy` reported as invented, because the pack defines
`task.fault_busy.stack_free_min`).

That settled the balance between the two checks. A cited id either exists or it
does not, which is a fact, and it fails the audit. A dotted name is a
heuristic, so it warns. The exit code follows the fact, not the heuristic.

## Reproducing it

Both commands run from this repository. The map and the project are the
firmware under test, one directory up.

```powershell
python -m mcu_insight diagnose --db captures/fault-heap.db --config budgets/signal.json `
    --map ../ESP32-Signal-Processing-System-github/build/signal_processing_system.map `
    --bin ../ESP32-Signal-Processing-System-github/build/signal_processing_system.bin `
    --partition 1500K `
    --project ../ESP32-Signal-Processing-System-github `
    --note "captured by tools/fault_matrix.py --port COM19" `
    --diff-since a33a18e~1 --out prompt.txt

python -m mcu_insight audit --pack docs/prompts/round-3.txt --answer docs/answers/round-3.md
```

## What it demonstrates

The tool's value is not that it reads the code; a model can read the code. The
value is that it produces a bounded, numbered, provenance-aware body of
evidence about a device the model cannot see, and then checks the answer that
comes back against it.

The three rounds are also the argument for building the evidence layer first.
Nothing in the diagnosis path calls a model on its own, so the pack and the
audit could be tested offline — which is why every finding above became a test
rather than a note.