- 根因：`examples/leaky-sensor-node/main/main.c` 的 `sample_task()` 每次采样分配 128 B 且从不释放，置信度高 [E57]、[E5]。

- 关键数字：空闲堆以 −660 B/s 下降，即每 5 s 掉 3,300 B [E5]、[E12]，一帧 25 次采样，每次 132 B，比 128 B 载荷多 4 B 开销 [E57]。
- 关键数字：`task.format` 配置栈 3,072 B [E43]、栈上数组 1,800 B [E57]，剩余高水位 29 帧恒为 56 B [E13]。

- 排除不掉的可能：碎片化——本包已删掉 `heap.largest` 时序，现有数据分不开 [E18]。
- 排除不掉的可能：泄漏在 vendored `mcu_telemetry` 内部而非 `main.c`——补丁被截断且不含 `main.c`，无法核对分配点 [E53]。

- 修复：在 `examples/leaky-sensor-node/main/main.c` 的 `sample_task()` 里把每帧分配改成循环外一次分配或每次 `free`，并把 `format_task()` 的 1,800 B 数组移出栈（本包无行号，只到函数）。

- 验证：重跑 `python -m mcu_insight check --db captures\leaky-sensor-node.db --config budgets\leaky-sensor-node.json` [E58]、[E2]、[E3]，`heap.free` 斜率应从 −660 B/s 回到 ≥ −64/s [E8]，`task.format.stack_free_min` 应从 56 B 升到 ≥ 256 B [E6]、[E9]。
