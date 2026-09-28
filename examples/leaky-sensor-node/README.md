# leaky-sensor-node: the project used to check that the tool finds real bugs

An ordinary looking ESP-IDF firmware with **two real defects** in it. Neither is
a fault injected through a switch; both are the kind of code someone writes by
accident.

## What is wrong with it

**1. The heap leak** -- `main.c`, `sample_task`. Every sample allocates
`PAYLOAD_BYTES` and never releases them. At one sample per 200 ms the comment
claims 640 bytes per second; the measurement says 660, and the difference is the
allocator's four bytes per block.

**2. The stack margin** -- `main.c`, `format_task`. A 1,800 byte array lives on
a 3,072 byte stack. The subtraction looks safe until `snprintf` is counted: the
task ends up with **56 bytes** of headroom against a 256 byte rule. This one was
not planted, the tool found it.

## Run it

```powershell
cd <tool-repo>
python tools/sync_firmware.py examples/leaky-sensor-node
cd examples/leaky-sensor-node
idf.py -p COM19 flash
cd ../..
python -m mcu_insight collect --db captures/leaky-sensor-node.db --source serial:COM19 --limit 30
python -m mcu_insight check --db captures/leaky-sensor-node.db --config budgets/leaky-sensor-node.json
```

## What the tool says

```
Violations (2)
  ! leaky-sensor-node heap.free: falls at -660.0 per second, slope >= -64/s ...
  ! leaky-sensor-node task.format.stack_free_min: 56 is below 256
RESULT: FAIL
```

Note which rule caught the leak: the heap was still at 185 KB, well above the
100 KB floor, so a level rule alone would have called this capture healthy.

## The run, kept

The capture and the report from one run are committed, so all of this can be
checked without a board:

| file | what it is |
|---|---|
| `captures/leaky-sensor-node.db` | the 30 frame capture |
| `docs/report-leaky-sensor-node.html` | the report, five blocks, red FAIL |
| `docs/prompts/round-4.txt` | the evidence pack handed to a model |
| `docs/answers/round-4.md` | the diagnosis that came back, checked by `audit` |