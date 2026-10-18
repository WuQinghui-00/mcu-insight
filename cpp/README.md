# fwres —— 嵌入式固件资源分析器（C++17）

从 ESP32 构建产物里算出**真实的 Flash / RAM 占用**，并和运行期遥测对照。

## 为什么需要它

`xtensa-esp32-elf-size` 按 ELF 标志把节分成 text/data/bss。问题在于：
ESP32 的 `.flash.rodata` 带 `AW`（可写）标志，会被算进 `data`，
看上去像占了 200 KB 数据 RAM——但它物理上在 Flash 里，一个字节 RAM 都不占。

fwres 按**硬件地址区间**分区，给出的是真实的 Flash / RAM 归属。

## 构建

```bash
cd cpp
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
```

## 用法

```bash
# 基本分析
./build/fwres app.elf

# 列出全部节（A=占内存 W=可写 X=可执行 B=不占文件）
./build/fwres app.elf --sections

# 机器可读
./build/fwres app.elf --json

# 预算门禁：超了返回 3
./build/fwres app.elf --flash-kb 1024 --ram-kb 200

# 从 .map 看"谁占了这些字节"
./build/fwres --map app.map

# 构建期 vs 运行期
./build/fwres app.elf --runtime telemetry.csv
```

遥测 CSV 格式：

```csv
metric,min,max
heap.min,185568,283908
task.format.stack_free_min,56,56
```

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 通过 |
| 1 | 文件解析失败 |
| 2 | 参数错误 |
| 3 | 超出 Flash/RAM 预算 |
| 4 | 运行期指标低于阈值 |

## 实测数据

分析 `signal_processing_system.elf`（15 MB，带调试信息，41 个节）：

| 区域 | 字节数 |
|---|---|
| FLASH 指令段 (IROM) | 736,786 |
| FLASH 只读数据 (DROM) | 220,442 |
| IRAM | 103,295 |
| DRAM | 47,952 |
| RTC | 24 |
| **FLASH 合计** | **957,228 B** |
| **RAM 合计** | **151,271 B** |

与官方 `xtensa-esp32-elf-size` 的 `dec` 值 **1,108,499 B 完全一致**，
说明 ELF 解析没有漏节、没有重复计数。

## 已知边界

- `.map` 里的合并段（如 `.rodata.*.str1.4`）所有输入共享同一地址，
  逐行相加会重复计数。工具按地址区间取并集规避，但合并段内部的
  单个文件归因无法精确拆分，单文件排名仅供参考。
- 目前只内置了 ESP32（原版）的地址映射表。

## 测试

```bash
cmake -S . -B build -DBUILD_TESTING=ON
cmake --build build -j
cd build && ctest --output-on-failure
```