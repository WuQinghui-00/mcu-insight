# MCU-Insight

[English](README.md) · **中文**

[![tests](https://github.com/WuQinghui-00/mcu-insight/actions/workflows/tests.yml/badge.svg)](https://github.com/WuQinghui-00/mcu-insight/actions/workflows/tests.yml)

嵌入式固件的资源可观测性与 AI 诊断（ESP-IDF / FreeRTOS）。

**直接看指南：** [中文](https://wuqinghui-00.github.io/mcu-insight/index.zh.html) · [English](https://wuqinghui-00.github.io/mcu-insight/)

它回答的是**看代码看不出来**的那类问题：

* 这次构建到底占了多少 Flash 和静态 RAM，还剩多少
* 两次构建之间，哪个组件涨了、涨了多少
* 堆和任务栈还能撑多久，哪个任务快不够用了
* 板子跑起来之后：控制周期抖了多少、推理慢了多少、睡眠占比掉没掉
* 出问题时，把"所有已知事实"整理成**可核对的证据包**交给 AI，并且能核对它有没有编造证据

## 状态

端到端可用：构建期资源核算、TinyML 模型分析、设备端运行时遥测、阈值与基线检查（含故障注入验证），以及诊断层的离线部分——证据包、由它生成的提问、以及核对答案的 audit。

**调用模型这一步故意留给你**：`diagnose` 只写 prompt，你自己贴给任何模型，`audit` 再核对拿回来的答案。这条路径不需要联网、不需要 API key，也因此可以测试。

* [`docs/index.html`](docs/index.html) / [`docs/index.zh.html`](docs/index.zh.html) —— 一页版操作指南，中英双语，页面上的每段输出都是真跑出来的
* [`docs/using-it.md`](docs/using-it.md) —— 参考手册：设备端接口、配置字段、单位与语义、全部命令、以及它做不到什么
* [`docs/diagnosis-case-study.md`](docs/diagnosis-case-study.md) —— 五轮证据包的对照记录

## 快速开始

宿主端不需要任何依赖、不需要账号、不需要联网。

```powershell
git clone https://github.com/WuQinghui-00/mcu-insight
cd mcu-insight
python -m venv .venv
.venv\Scripts\python -m pip install -e . --no-build-isolation
.venv\Scripts\python -m pip install pyserial      # 只有直接从板子采集时才需要
```

不想安装也行，在仓库目录里用 `python -m mcu_insight <命令>` 就能跑。

**想先练手**：[`examples/leaky-sensor-node/`](examples/leaky-sensor-node/) 是一个**故意写坏**的小固件，里面两个真实缺陷，README 里有命令也有答案。先拿它走一遍指南，再动你自己的工程。

| 命令 | 回答什么问题 | 需要什么 |
|---|---|---|
| `analyze` | 这个构建占多少 | 一个 `.map` |
| `compare` | 两次构建之间谁涨了 | 两个 `.map` |
| `model` | TinyML 模型占多少 | 一个 `.tflite` |
| `collect` | 把设备报的东西存下来 | 板子，或一份日志 |
| `summary` | 这台设备刚才在干什么 | 一份采集 |
| `check` | 达不达标 | 采集 + 预算文件 |
| `report` | 一页看全 | 采集 |
| `diagnose` | 把"为什么坏"的证据打包 | 采集 |
| `audit` | 那个解释站不站得住 | prompt + 答案 |
| `simulate` | 没有硬件也能开发宿主端 | 什么都不用 |

`--json` 输出机器可读结果。`check` 在违规**以及规则无法判定**时都返回非零，所以可以直接卡 CI。

## 它怎么工作

输入分三层，缺一层就会得出错误结论：

1. **静态产物**：链接脚本映射文件 `.map`、固件镜像 `.bin`、模型 `.tflite`。用来回答"占了多少"，不需要板子。
2. **运行时遥测**：设备端 agent 每 5 秒打一行 JSON，包含堆、每个任务的栈高水位、每核空闲占比、延迟分位数、睡眠占比，以及你自己加的指标。
3. **项目描述文件**：一个 JSON，写明红线、每个指标的单位与含义、基线在哪。

输出也有三层：**给人看的 HTML 报告**、**给机器看的退出码**、**给 AI 看的证据包**（外加一份可核对的 audit 结果）。

## 验证

* **204 个测试**，在 Python 3.9 和 3.12 上跑，CI 每次推送都跑。
* **静态核算与真实镜像对照**：估算 1,065,624 B，真实 `.bin` 1,065,728 B，差 **104 字节**。
* **故障注入矩阵**：基线不报，三类注入的故障全部报出——

  | 注入 | 现象 | 检出 |
  |---|---|---|
  | `FAULT off`（基线） | 无 | 否（正确） |
  | `FAULT heap` | 每轮泄漏 2 KB，`heap.min` 从 144,560 掉到 50,168 | 是 |
  | `FAULT stack` | 2048 字节任务里放 1800 字节栈帧，可用栈剩 32 | 是 |
  | `FAULT busy` | 高优先级死循环，`idle0` 从 90% 掉到 0% | 是 |

* **示例工程（真实缺陷，不是注入）**：每 200 ms 分配 128 字节且从不释放，实测 **-660 B/s**（斜率规则抓到；当时堆还有 185 KB，底线规则静默）；另一个 1,800 字节数组放在 3,072 字节栈上，只剩 **56 字节**。

## 它为什么值得信

三个真实案例，都是先出错、再定位、最后变成测试：

**串口丢字节 87% → 0%。** ESP-IDF 的控制台默认**不装 UART 驱动**，逐字节写 FIFO 会丢；现在 agent 自己装驱动、用 `uart_write_bytes` 带返回值重试，并把它数成 `custom.uart_tx_retries`。

**模型准确率 61% → 99%。** 根因是训练用的仿真数据太干净（正弦三次谐波占比 0.1%，真机 5.3%，差 53 倍），换成真机采集的数据重训；另外还有一个结构性错误——标签在波形轮换**之后**才读，白送 17 个点。

**推理延迟 7.3 ms 是假的。** 软件 DAC 每 50 µs 推一次波形表，每秒两万次中断抢走 CPU。改成 DMA 连续模式后，同一个模型的真实延迟是 **P50 1.0 ms / P99 1.25 ms**；同一处修改也把输出频率从"请求 200 Hz、实测 78 Hz"变成 203 Hz。

教训值得单独说：**一个资源指标可能被邻居而不是被测代码支配**，而**自己站在关键路径上的仪器会说谎**。

## 已知的限制

* 没有调用图，也不做逐函数归因。堆在往下走，只说明有东西在吃内存，不说明是哪个调用点。
* 没有功耗测量。睡眠占比是它的代理指标，不是电流表。
* **未跟踪的文件不在 diff 里。** 全新工程的源码就是未跟踪的，所以诊断前先 `git add`，否则证据包里只有数字、没有代码。
* **证据包超过体积上限会被裁剪**，被裁的指标会注明（现在是先把序列减半，减到两个点才丢弃）。

## 设备端

`firmware/esp-idf/mcu_telemetry` 是权威副本，`tools/sync_firmware.py` 把它拷进各个工程（`<工程>/components/mcu_telemetry`）。先拷组件再编译；拷之前那行 `EXTRA_COMPONENT_DIRS` 要加 `if(EXISTS ...)` 判断，否则报错指向的是目录不存在，而不是"你漏了拷贝"。

三行代码就够了：

```c
#include "mcu_telemetry.h"

static const mcu_telemetry_config_t telemetry = { .device = "my-board" };

void app_main(void)
{
    ESP_ERROR_CHECK(mcu_telemetry_start(&telemetry));
}
```

堆、每个任务的栈高水位、每核空闲占比、睡眠占比、串口重试次数**都自动上报**，不用你指定盯什么。任务名不用手工注册（开了 `CONFIG_FREERTOS_USE_TRACE_FACILITY` 就连 httpd / mqtt / wifi 都会自动出现）；想算某个任务的栈**总量**，或加自己的数值，才需要多写一行。

一组测量的开销：监控任务 **0.6% CPU**、**2.3 KB 栈**，`collect` 侧 **0.2% CPU**、**12 MB RSS**。

## 诊断与核对

```powershell
cd <工具仓库>
python -m mcu_insight diagnose --db captures/board.db --config budgets/my-board.json --project <你的工程> --lang zh --out prompt.txt
python -m mcu_insight audit --pack prompt.txt --answer answer.md
```

证据包把违规项、规则原文、指标时序、构建数字、能影响资源的 sdkconfig 项、以及产出这个固件的 git 状态，一条条编号列出来；提问模板要求 **每条事实都引用编号**、**分不清就直说**、**不许编造**，并且**全文不超过 400 字**。

`audit` 再核对回来：引用了不存在的编号 → 硬失败（退出码 1）；提到包里没有的指标名 → 警告；没有引用的论断 → 列出来。第五轮实测：同一份采集、同一个模型，答案从 3,236 字压到 **695 字**，两处缺陷和算术一个没少。

## 目录

```
mcu_insight/     工具本体（宿主端，纯标准库）
tools/           同步组件、故障矩阵、页面生成
firmware/        设备端 agent 的权威副本
budgets/         项目描述文件与基线
captures/        采集数据（SQLite）
examples/        一个故意写坏的工程，用来验证工具
docs/            指南页面、案例记录、证据归档、协议说明
tests/           204 个测试
training/        TinyML 训练脚本与模型
```