"""Build the two landing pages from real runs.

Every output block on the page comes from running the tool just now, so the page
cannot drift from what the tool actually prints. Regenerate with:

    python tools/build_guide.py

The pages are meant to be followed by copying and pasting, not read: one small
step at a time, and each step says which directory it belongs in.
"""

from __future__ import annotations

import argparse
import html
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROJECT = REPO_ROOT.parent / "ESP32-Signal-Processing-System-github"
ANSWERS = REPO_ROOT / "docs" / "answers"
PROMPTS = REPO_ROOT / "docs" / "prompts"

STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 0 24px 64px; background: #f6f7f9; color: #1c2024;
       font: 15px/1.65 -apple-system, Segoe UI, Roboto, sans-serif; }
.wrap { max-width: 900px; margin: 0 auto; }
header { padding: 36px 0 8px; }
h1 { font-size: 27px; margin: 0 0 8px; letter-spacing: -0.02em; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.08em;
     color: #6b7280; margin: 0 0 14px; }
h3 { font-size: 16px; margin: 0 0 6px; }
p { margin: 0 0 10px; }
a { color: #2563eb; }
nav a { display: inline-block; margin-right: 14px; font-size: 14px; }
.lang { float: right; font-size: 14px; }
.lang a { font-weight: 600; }
section { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px;
          padding: 20px 22px; margin-top: 16px; }
.step { border: 1px solid #eef0f3; border-radius: 8px; padding: 14px 16px; margin-top: 12px; }
.step .num { font: 600 12px/1 ui-monospace, monospace; color: #2563eb; margin-bottom: 6px; }
.step .where { font: 11.5px/1 ui-monospace, monospace; color: #6b7280; margin-bottom: 8px; }
.grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
pre { background: #0f172a; color: #e2e8f0; border-radius: 7px; padding: 11px 13px;
      overflow-x: auto; font: 12.5px/1.6 ui-monospace, SFMono-Regular, Menlo, monospace;
      margin: 8px 0 0; white-space: pre; }
pre.out { background: #f8fafc; color: #334155; border: 1px solid #e5e7eb; }
.note { color: #6b7280; font-size: 13.5px; margin: 8px 0 0; }
ul { margin: 6px 0 0; padding-left: 20px; }
li { margin-bottom: 4px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
td { padding: 7px 10px 7px 0; border-top: 1px solid #f1f3f5; vertical-align: top; }
td:first-child { white-space: nowrap; color: #6b7280; }
footer { margin-top: 22px; color: #6b7280; font-size: 13px; }
"""

C_MIN_EN = """/* 1. at the very top, with the other includes */
#include "mcu_telemetry.h"

/* 2. outside app_main, anywhere in the file */
static const mcu_telemetry_config_t telemetry = {
    .device = "my-board",      /* a name for this board */
    .report_period_ms = 5000,  /* report every 5 seconds */
};

void app_main(void)
{
    /* ... your own initialisation ... */

    /* 3. after the hardware is up */
    ESP_ERROR_CHECK(mcu_telemetry_start(&telemetry));

    /* the rest of your code does not change */
}"""

C_MIN_ZH = """/* 第 1 行：文件最上面，和其他 #include 放一起 */
#include "mcu_telemetry.h"

/* 第 2 行：app_main 外面，文件里任意位置 */
static const mcu_telemetry_config_t telemetry = {
    .device = "my-board",      /* 给板子起个名字 */
    .report_period_ms = 5000,  /* 每 5 秒上报一次 */
};

void app_main(void)
{
    /* ... 你原本的初始化 ... */

    /* 第 3 行：硬件起来之后 */
    ESP_ERROR_CHECK(mcu_telemetry_start(&telemetry));

    /* 下面还是你原来的业务代码，一行都不用改 */
}"""

C_OPT_EN = """/* optional: how much of a task's stack is used. Same name as xTaskCreate. */
xTaskCreate(control_task, "control", 4096, NULL, 2, NULL);
mcu_telemetry_register_task("control", 4096);

/* optional: a number of your own, next to where it is computed */
mcu_telemetry_set_custom_int("loop_jitter_us", jitter);

/* optional: a latency distribution for any call, one line at the call site */
mcu_telemetry_histogram_add("infer", latency_us);"""

C_OPT_ZH = """/* 可选：想知道某个任务的栈用了百分之多少，任务建好之后加一行 */
xTaskCreate(control_task, "control", 4096, NULL, 2, NULL);
mcu_telemetry_register_task("control", 4096);   /* 名字要和 xTaskCreate 里一致 */

/* 可选：你自己的数值，在它算出来的那一行下面加 */
mcu_telemetry_set_custom_int("loop_jitter_us", jitter);

/* 可选：任何函数的耗时分布，在调用处加一行，自动出 p50 / p99 / 最大 / 最小 */
mcu_telemetry_histogram_add("infer", latency_us);"""
CMAKE_SNIPPET = """# My-Project/main/CMakeLists.txt
idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "."
    REQUIRES driver nvs_flash freertos mcu_telemetry)"""

JSON_EN = """{
  "rules": [
    {"metric": "heap.min", "min": 100000, "stat": "min"},
    {"metric": "heap.free", "min_rate_per_s": -64, "min_span_ms": 60000},
    {"metric": "task.*.stack_free_min", "min": 256, "stat": "min"},
    {"metric": "custom.infer_p99_us", "max": 5000, "stat": "max"}
  ],
  "baseline": {"path": "my-board-baseline.json", "max_change_pct": 20}
}"""

JSON_ZH = """{
  "rules": [
    {"metric": "heap.min", "min": 100000, "stat": "min"},
    {"metric": "heap.free", "min_rate_per_s": -64, "min_span_ms": 60000},
    {"metric": "task.*.stack_free_min", "min": 256, "stat": "min"},
    {"metric": "custom.infer_p99_us", "max": 5000, "stat": "max"}
  ],
  "baseline": {"path": "my-board-baseline.json", "max_change_pct": 20}
}"""

def run(*args: str) -> str:
    """Run one command from the repository root and return its output."""
    done = subprocess.run(
        [sys.executable, "-m", "mcu_insight", *args],
        cwd=str(REPO_ROOT), capture_output=True, encoding="utf-8", errors="replace",
    )
    output = (done.stdout or "").strip()
    if done.returncode not in (0, 1):          # 1 is a failed check, not an error
        return (done.stderr or "").strip() or output
    return output


def take(text: str, lines: int) -> str:
    kept = text.splitlines()[:lines]
    if len(text.splitlines()) > lines:
        kept.append("...")
    return "\n".join(kept)


def paragraph_containing(path: Path, needle: str, limit: int = 4) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if needle in line:
            block = [line]
            for following in lines[index + 1:index + limit]:
                if not following.strip():
                    break
                block.append(following)
            return "\n".join(block).replace("`", "")
    return ""


def tidy(text: str) -> str:
    """Strip this machine's directory names before they reach a public page."""
    for root in (str(REPO_ROOT), str(DEFAULT_PROJECT)):
        text = text.replace(root + "\\", "").replace(root + "/", "")
    return text


def pick_build():
    """The linker map to show in route A, and its binary when one is around."""
    candidate = DEFAULT_PROJECT / "build" / "signal_processing_system.map"
    if candidate.is_file():
        return candidate, candidate.with_suffix(".bin")
    return REPO_ROOT / "tests" / "data" / "sample.map", None


def collect() -> dict:
    component = REPO_ROOT / "firmware" / "esp-idf" / "mcu_telemetry"
    mapping, binary = pick_build()
    analyze = ["analyze", str(mapping), "--partition", "1500K"]
    if binary:
        analyze += ["--bin", str(binary)]
    return {
        "component_files": [
            str(path.relative_to(component)).replace("\\", "/")
            for path in sorted(component.rglob("*")) if path.is_file()
        ],
        "analyze": tidy(take(run(*analyze), 10)),
        "model": tidy(take(run("model", "training/out/waveform_model_real.tflite"), 8)),
        "summary": tidy(take(run("summary", "--db", "captures/fault-off.db", "--top", "4"), 9)),
        "check": tidy(run("check", "--db", "captures/fault-heap.db",
                          "--config", "budgets/signal.json")),
        "evidence": tidy(take(run("diagnose", "--db", "captures/fault-heap.db",
                                  "--config", "budgets/signal.json", "--dry-run"), 8)),
        "answer": paragraph_containing(ANSWERS / "round-3.md", "s_leak_sink = malloc(2048)"),
        "audit": tidy(run("audit", "--pack", str(PROMPTS / "round-3.txt"),
                          "--answer", str(ANSWERS / "round-3.md"))),
    }


TEXT = {
    "en": {
        "title": "MCU-Insight: measure an ESP32 project, then explain it",
        "page_title": "MCU-Insight - do this, one step at a time",
        "story": "What this is",
        "story_intro": "It does four things. That is the whole tool.",
        "s1": "copy one small component into your ESP-IDF project, so the firmware "
              "reports its own state every few seconds",
        "s2": "run one command and get an <b>HTML report a human can read</b>: how much "
              "flash and RAM the build takes, how much stack each task has left, the "
              "smallest free heap, how slow inference gets",
        "s3": "when something is wrong, get an <b>evidence pack</b>: every known fact, "
              "numbered, ready to paste into any AI",
        "s4": "check the AI's answer back against that pack, which catches a citation "
              "that does not exist",
        "story_outro": "No network, no account, no instruments.",
        "tail": "Once there is data: judge it, and get the report",
        "change": "You touch three places, and nothing else",
        "change1": "My-Project/main/main.c - three lines, each one shown below",
        "change2": "My-Project/main/CMakeLists.txt - one name added to REQUIRES",
        "change3": "mcu-insight/budgets/my-board.json - a new file with your limits in it",
        "steps": "B. Add three lines, and see what the board is really doing",
        "here_tool": "run inside mcu-insight\\",
        "here_fw": "run inside My-Project\\",
        "back": "what comes back",
        "step1": "Copy the component in",
        "step1_note": "One command. It copies the agent into your project and tells you "
                      "what it wrote.",
        "before": "your project before",
        "after": "your project after",
        "created": "created by the command",
        "step1_note2": "Nothing is created by hand: if components\\ or mcu_telemetry\\ do "
                       "not exist, the command makes them. The argument must be the "
                       "project root, the directory that holds CMakeLists.txt.",
        "step2": "Change main.c: three lines",
        "b_intro": "One thing to know first: you do not have to decide what to watch. Run it once and the report shows where it is tight; add a line for that place afterwards.",
        "s2_auto_h": "Those three lines are enough. The agent already reports all of this, with no code from you:",
        "s2_auto": [
            "heap: how much is free now, the smallest it has been, the largest allocatable block",
            "how much stack every task has left (with the first switch below, httpd, mqtt and wifi appear without being registered)",
            "idle share per CPU core (second switch)",
            "share of time spent in light sleep (third switch)",
            "UART retries, counted by the agent, which is how lost bytes show up",
        ],
        "s2_cfg_h": "Those three switches are menuconfig settings, not code:",
        "s2_cfg": "CONFIG_FREERTOS_USE_TRACE_FACILITY=y       discover every task automatically\nCONFIG_FREERTOS_GENERATE_RUN_TIME_STATS=y idle share per core\nCONFIG_PM_ENABLE=y                        light sleep share",
        "s2_opt_h": "Want more detail? Add these when you want them (optional):",
        "step2_note": "Each line is marked with where it goes. Nothing else in the file changes.",
        "step3": "Add one name to main/CMakeLists.txt",
        "step3_note": "Without this line the header will not resolve and the build stops.",
        "step4": "Build and flash",
        "step4_note": "From the firmware project, the usual ESP-IDF command. The board "
                      "now prints one JSON line every five seconds.",
        "step5": "Capture",
        "step5_note": "Run this while the board is doing whatever you want to measure. "
                      "Frames go into a SQLite file, so everything after this reads the "
                      "file instead of the board.",
        "step6": "Write your limits",
        "step6_note": "One new file in the tool repository. This is the only thing you "
                      "write by hand: the numbers your project must not break. "
                      "<code>heap.min</code> is the smallest free heap, "
                      "<code>min_rate_per_s</code> is how fast it may fall.",
        "step7": "Judge, and get the report",
        "step6_hint": "This is the only file you write by hand.",
        "step7_note": "Exit code 0 means pass, 1 means fail or a rule that could not be "
                      "judged, so a CI job can gate on it. The report is one file: "
                      "double-click it, no server needed.",
        "step7_link": "the example report, made from the capture committed here:",
        "step8": "Get an evidence pack, and have an AI explain it",
        "step8_note": "The tool does not guess. It writes every known fact into a "
                      "numbered pack and stops. You paste that pack into any AI, then "
                      "check the answer back against it.",
        "s8_lang_h": "To have it answer in Chinese, add one flag:",
        "s8_lang_cmd": "python -m mcu_insight diagnose --db captures/board.db --config budgets/my-board.json --lang zh --out prompt.txt",
        "s8_lang_d": "Evidence ids like [E12] and the metric names stay as the tool writes them, because they are identifiers and translating them would break the audit. Only the instructions and the answer change language.",
        "s8_what_h": "The diagnosis that comes back is always these four parts:",
        "s8_what": [
            "Root cause: the single most likely one, a confidence level, and the evidence ids behind it",
            "Alternatives: what else could produce this, and which observation rules each one out",
            "Fix: the smallest change that addresses the root cause",
            "Verification: which command to run, which metric should move, and roughly how much",
        ],
        "s8_audit": "The last step is the check: every [E12] in the answer has to exist in the pack, and an answer that cites an id which does not exist fails.",
        "value_h": "So what is it worth",
        "value": [
            "<b>It sees what an AI cannot.</b> A model can read your code. It cannot read what is on your board: whether a stack has 512 bytes left or 256, whether the smallest free heap was 24 KB or 4 KB, whether inference P99 is 30 ms or 55 ms. Those exist only if something measures them.",
            "<b>It does not let the AI make things up.</b> Every fact is numbered, the answer has to cite it, and the audit fails an answer that cites an id which does not exist. Without that layer, a diagnosis only sounds reasonable.",
            "<b>It can say why this change made it worse.</b> With a baseline to compare against -- revision, capture length, and the slope of every metric -- it can tell a real regression from a capture that was simply shorter.",
        ],
        "value_close": "In one line: rules and statistics detect, an AI explains. The tool brings the evidence, the AI argues from it, and the citations between the two are checked by machine.",
        "diag_e": "1. the pack it writes (diagnose)",
        "diag_a": "2. what the AI says",
        "diag_u": "3. checked against the pack (audit)",
        "nb": "A. Change nothing",
        "nb_d": "Two commands that read the files your build already made. Use these if the question is only whether it still fits.",
        "result": "What you end up with",
        "result_d": "Run the whole thing and these are what is left over. All of it is on your own machine; nothing is uploaded anywhere.",
        "result1": "<b>report.html</b> - for a human: double-click it, one page on what the build costs and where it is tight.",
        "result2": "<b>captures/board.db</b> - your own data: every later command reads this file, so the board stays unplugged.",
        "result3": "<b>budgets/...-baseline.json</b> - for the next change: it is compared automatically and tells you which metric moved.",
        "result4": "<b>prompt.txt and answer.md</b> - the exchange with the AI: the evidence pack, and the diagnosis it gave.",
        "result5": "<b>the exit code of check</b> - for CI: 0 pass, 1 fail, so a pipeline can gate on it.",
        "foot": "Every output on this page was produced by running the tool against the "
                "captures and the build committed in this repository. Rebuild it with "
                "<code>python tools/build_guide.py</code>.",
    },
    "zh": {
        "title": "MCU-Insight：先把 ESP32 工程测出来，再把它讲明白",
        "page_title": "MCU-Insight — 照着做就行",
        "story": "这东西是干什么的",
        "story_intro": "一共就四件事，没别的。",
        "s1": "把一个小组件拷进你的 ESP-IDF 工程，让固件每隔几秒报一次自己的状态",
        "s2": "跑一条命令，得到一份<b>给人看的 HTML 报告</b>：固件占多少 Flash 和 RAM、"
              "每个任务还剩多少栈、堆最少剩多少、推理慢不慢",
        "s3": "出问题的时候，得到一份<b>证据包</b>：所有已知事实，一条条编号，直接粘给任何 AI",
        "s4": "再把 AI 的诊断拿回来核对：它会指出 AI 引用了根本不存在的证据",
        "story_outro": "不用联网、不用注册、不用仪器。",
        "tail": "有了数据之后：判预算、出报告、让 AI 讲",
        "change": "你只需要动三个地方，别的都不用改",
        "change1": "My-Project/main/main.c —— 加 3 行，下面标了每一行放哪",
        "change2": "My-Project/main/CMakeLists.txt —— 加一个名字 mcu_telemetry",
        "change3": "mcu-insight/budgets/my-board.json —— 新建这个文件，写你的红线",
        "steps": "B. 要你自己粘 3 行，看板子的真实数据",
        "here_tool": "在 mcu-insight\\ 目录里运行",
        "here_fw": "在 My-Project\\ 目录里运行",
        "back": "跑完长这样",
        "step1": "第一步：把组件拷进你的工程",
        "step1_note": "就一条命令。它把 agent 拷进你的工程，并告诉你写了哪些文件。",
        "before": "你的工程，运行前",
        "after": "运行后",
        "created": "由这条命令创建",
        "step1_note2": "没有任何东西要你手工建：components\\ 或 mcu_telemetry\\ 不存在，"
                       "命令会自己创建。参数必须是工程根目录，也就是放 CMakeLists.txt 的那一层。",
        "step2": "第二步：改 main.c，最少 3 行",
        "b_intro": "先说一句：一开始你什么都不用盯。先跑一次，报告会告诉你哪里紧张；紧张的地方再回来加一行。",
        "s2_auto_h": "这 3 行写完，下面这些它自动就报，不用你写代码：",
        "s2_auto": [
            "堆：现在剩多少、历史最少剩多少、最大可分配块是多少",
            "每个任务的栈还剩多少（开了下面第一个开关，连 httpd / mqtt / wifi 这些任务也会自动出现）",
            "每个 CPU 核的空闲占比（开了第二个开关）",
            "睡眠时间占比（开了第三个开关）",
            "串口重试次数（它自己数，用来发现丢数据）",
        ],
        "s2_cfg_h": "那三个开关在 idf.py menuconfig 里打开，是配置，不是代码：",
        "s2_cfg": "CONFIG_FREERTOS_USE_TRACE_FACILITY=y       自动发现工程里所有任务\nCONFIG_FREERTOS_GENERATE_RUN_TIME_STATS=y 每核空闲占比\nCONFIG_PM_ENABLE=y                         睡眠占比",
        "s2_opt_h": "想要更细，再加这几行（可选，不加也能跑）：",
        "step2_note": "三行的位置下面都标了。你原来的业务代码一个字都不用改。",
        "step3": "第三步：在 main/CMakeLists.txt 里加一个名字",
        "step3_note": "不加这一行，头文件找不到，编译直接停。",
        "step4": "第四步：编译烧写",
        "step4_note": "在固件工程里，就是平时的 ESP-IDF 命令。烧完之后，板子每 5 秒会吐一行 JSON。",
        "step5": "第五步：采集",
        "step5_note": "在板子跑着你想测的那个场景时执行这条命令。遥测帧会存进一个 SQLite 文件，"
                      "后面所有命令都读文件，不用再插板子。",
        "step6": "第六步：写下你的红线",
        "step6_note": "在工具仓库里新建一个文件。这是唯一需要你自己写的东西："
                      "你的工程不许越过的数值。<code>heap.min</code> 是最小空闲堆，"
                      "<code>min_rate_per_s</code> 是它每秒最多能掉多少。",
        "step7": "第七步：判预算，顺便出报告",
        "step6_hint": "这是唯一需要你自己写的东西。",
        "step7_note": "退出码 0 是通过，1 是不通过或规则无法判定，所以可以直接卡 CI。"
                      "报告就是一个文件，双击打开，不需要服务器。",
        "step7_link": "这是仓库里真实生成的那份示例报告：",
        "step8": "第八步：出证据包，让 AI 来讲",
        "step8_note": "工具不猜。它把所有已知事实写成一份带编号的证据包就停手。"
                      "你把这份包粘给任何 AI，再把答案拿回来核对。",
        "s8_lang_h": "想让它用中文回答，加一个参数：",
        "s8_lang_cmd": "python -m mcu_insight diagnose --db captures/board.db --config budgets/my-board.json --lang zh --out prompt.txt",
        "s8_lang_d": "证据编号 [E12] 和指标名保持工具里的原样，因为它们是标识符，翻译了就对不上；中文的是说明和回答。",
        "s8_what_h": "它拿回来的诊断固定是这四段：",
        "s8_what": [
            "根因：最可能的那一个，给一个置信度，再列出支撑它的证据编号",
            "其它可能：还有什么会造成同样现象，哪个观察能把它排除",
            "修复：针对根因的最小改动",
            "验证：跑哪条命令、哪个指标该动、大致动多少",
        ],
        "s8_audit": "最后一步是核对：答案里每个 [E12] 必须在证据包里真的存在；引用了不存在的编号，直接判失败。",
        "value_h": "所以它到底值在哪",
        "value": [
            "<b>它看见 AI 看不见的东西。</b>模型能读你的代码，但读不到你板子上：栈剩 512 还是 256 字节、堆最少剩 24KB 还是 4KB、推理 P99 是 30ms 还是 55ms。这些只有量出来才有。",
            "<b>它不让 AI 瞎编。</b>所有事实都带编号，答案必须引用；核对时出现不存在的编号就判失败。没有这一层，诊断只是听起来有道理。",
            "<b>它能回答为什么这次变差了。</b>有基线做对照（版本、采集时长、每个指标的斜率），所以分得清是真的变差了，还是这次只是采得短。",
        ],
        "value_close": "一句话：检测靠规则和统计，解释靠 AI。工具负责拿到证据，AI 负责讲道理，而这两者之间的引用关系可以被机器核对。",
        "diag_e": "1. 它写出的证据包（diagnose）",
        "diag_a": "2. AI 给出的答案",
        "diag_u": "3. 拿回证据包里核对（audit）",
        "nb": "A. 不改代码就能用",
        "nb_d": "两条命令，读你编译好的文件，不用板子。只想确认还装得下吗，就走这条。",
        "result": "最后你手上有什么",
        "result_d": "跑完整条流程，多出来的是这几样。全部在你自己电脑上，没有云端副本。",
        "result1": "<b>report.html</b> —— 给人看的：双击打开，一页看完固件占多少、哪里紧张。",
        "result2": "<b>captures/board.db</b> —— 你自己的数据：以后的分析都读它，不用再插板子。",
        "result3": "<b>budgets/…-baseline.json</b> —— 给下一次改动：自动对比，告诉你哪一项变差了。",
        "result4": "<b>prompt.txt 和 answer.md</b> —— 你和 AI 的一来一回：证据包，加它给出的诊断。",
        "result5": "<b>check 的退出码</b> —— 给 CI：0 通过、1 不通过，可以直接卡流水线。",
        "foot": "本页每段输出都是拿本仓库里已提交的采集和构建产物真跑出来的。"
                "重新生成：<code>python tools/build_guide.py</code>。",
    },
}

LABELS = {
    "en": {"other": ("中文", "index.zh.html"), "repo": "Repository",
           "guide": "Full guide", "case": "Case study", "report": "Example report",
           "nb_a": "What the build costs", "nb_m": "What the model costs"},
    "zh": {"other": ("EN", "index.html"), "repo": "代码仓库",
           "guide": "完整指南", "case": "三轮诊断案例", "report": "报告示例",
           "nb_a": "固件占多少", "nb_m": "模型占多少"},
}


def render(lang: str, facts: dict, repo: str = "") -> str:
    c = TEXT[lang]
    labels = LABELS[lang]
    other_label, other_href = labels["other"]
    snippet = C_MIN_ZH if lang == "zh" else C_MIN_EN
    optional = C_OPT_ZH if lang == "zh" else C_OPT_EN

    def pre(text: str, cls: str = "out") -> str:
        return f'<pre class="{cls}">{html.escape(text)}</pre>' if text else ""

    def block(number: str, title: str, where: str, note: str, body: str) -> str:
        return ("<div class=step>"
                + (f'<div class="num">{number}</div>' if number else "")
                + (f'<div class="where">{where}</div>' if where else "")
                + f"<h3>{title}</h3>"
                + (f'<p class="note">{note}</p>' if note else "")
                + body + "</div>")

    component_tree = "\n".join(f"      {name}" for name in facts["component_files"])
    before_tree = "My-Project\\\n  CMakeLists.txt\n  main\\main.c"
    after_tree = ("My-Project\\\n  CMakeLists.txt\n"
                  f"  components\\            <- {c['created']}\n"
                  f"    mcu_telemetry\\       <- {c['created']}\n"
                  + component_tree + "\n  main\\main.c")

    route_b = "".join([
        block("1", c["step1"], c["here_tool"], c["step1_note"],
              pre("python tools/sync_firmware.py ..\\My-Project", "run")
              + '<div class="grid" style="margin-top:12px">'
              + f'<div><p class="note">{c["before"]}</p>' + pre(before_tree) + "</div>"
              + f'<div><p class="note">{c["after"]}</p>' + pre(after_tree) + "</div>"
              + "</div>"
              + f'<p class="note" style="margin-top:12px">{c["step1_note2"]}</p>'),
        block("2", c["step2"], c["here_fw"], c["step2_note"],
              pre(snippet, "run")
              + f'<p class="note">{c["s2_auto_h"]}</p><ul>'
              + "".join(f"<li>{line}</li>" for line in c["s2_auto"]) + "</ul>"
              + f'<p class="note">{c["s2_cfg_h"]}</p>' + pre(c["s2_cfg"], "run")
              + f'<p class="note">{c["s2_opt_h"]}</p>' + pre(optional, "run")),
        block("3", c["step3"], c["here_fw"], c["step3_note"], pre(CMAKE_SNIPPET, "run")),
        block("4", c["step4"], c["here_fw"], c["step4_note"],
              pre("idf.py -p COM19 flash monitor", "run")),
        block("5", c["step5"], c["here_tool"], c["step5_note"],
              pre("python -m mcu_insight collect --db captures/board.db "
                  "--source serial:COM19", "run")
              + f'<p class="note">{c["back"]}</p>' + pre(facts["summary"])),
    ])

    tail = "".join([
        block("6", c["step6"], c["here_tool"], c["step6_note"], pre(JSON_EN, "run")),
        block("7", c["step7"], c["here_tool"], c["step7_note"],
              pre("python -m mcu_insight check --db captures/board.db "
                  "--config budgets/my-board.json", "run")
              + f'<p class="note">{c["back"]}</p>' + pre(facts["check"])
              + pre("python -m mcu_insight report --db captures/board.db "
                    "--config budgets/my-board.json --out report.html", "run")
              + f'<p class="note">{c["step7_link"]} '
              + '<a href="report-demo.html">docs/report-demo.html</a></p>'),
        block("8", c["step8"], c["here_tool"], c["step8_note"],
              pre("python -m mcu_insight diagnose --db captures/board.db "
                  "--config budgets/my-board.json --out prompt.txt", "run")
              + '<div class="grid" style="margin-top:12px">'
              + f'<div><p class="note">{c["diag_e"]}</p>' + pre(facts["evidence"]) + "</div>"
              + f'<div><p class="note">{c["diag_a"]}</p>' + pre(facts["answer"]) + "</div>"
              + f'<div><p class="note">{c["diag_u"]}</p>' + pre(facts["audit"]) + "</div>"
              + "</div>"
              + f'<p class="note">{c["s8_lang_h"]}</p>'
              + pre(c["s8_lang_cmd"], "run")
              + f'<p class="note">{c["s8_lang_d"]}</p>'
              + f'<p class="note">{c["s8_what_h"]}</p><ul>'
              + "".join(f"<li>{line}</li>" for line in c["s8_what"]) + "</ul>"
              + pre("python -m mcu_insight audit --pack prompt.txt --answer answer.md", "run")
              + f'<p class="note">{c["s8_audit"]}</p>'),
    ])

    return "\n".join([
        "<!doctype html>",
        f'<html lang="{lang}"><head><meta charset=utf-8>',
        '<meta name=viewport content="width=device-width,initial-scale=1">',
        f"<title>{c['page_title']}</title>",
        f"<style>{STYLE}</style></head><body><div class=wrap>",
        "<header>",
        f'<div class=lang><a href="{other_href}">{other_label}</a></div>',
        "<h1>MCU-Insight</h1>",
        "<nav>",
        (f'<a href="{repo}">{labels["repo"]}</a>' if repo else ""),
        f'<a href="using-it.md">{labels["guide"]}</a>',
        f'<a href="diagnosis-case-study.md">{labels["case"]}</a>',
        f'<a href="report-demo.html">{labels["report"]}</a>',
        "</nav></header>",
        f"<section><h2>{c['story']}</h2><p>{c['story_intro']}</p><ul>",
        "".join(f"<li>{c[f's{i}']}</li>" for i in (1, 2, 3, 4)),
        f"</ul><p class=\"note\">{c['story_outro']}</p></section>",
        f"<section><h2>{c['change']}</h2><ul>",
        "".join(f"<li>{c[f'change{i}']}</li>" for i in (1, 2, 3)),
        "</ul></section>",
        f"<section><h2>{c['nb']}</h2><p class=\"note\">{c['nb_d']}</p>",
        block("", labels["nb_a"], c["here_tool"], "",
              pre("python -m mcu_insight analyze build/my_app.map "
                  "--bin build/my_app.bin --partition 1500K", "run")
              + f'<p class="note">{c["back"]}</p>' + pre(facts["analyze"])),
        block("", labels["nb_m"], c["here_tool"], "",
              pre("python -m mcu_insight model build/model.tflite", "run")
              + f'<p class="note">{c["back"]}</p>' + pre(facts["model"])),
        "</section>",
        f"<section><h2>{c['steps']}</h2><p>{c['b_intro']}</p>{route_b}</section>",
        f"<section><h2>{c['tail']}</h2>{tail}</section>",
        f"<section><h2>{c['value_h']}</h2><ul>",
        "".join(f"<li>{line}</li>" for line in c["value"]),
        f"</ul><p class=\"note\">{c['value_close']}</p></section>",
        f"<section><h2>{c['result']}</h2><p class=\"note\">{c['result_d']}</p><ul>",
        "".join(f"<li>{c[f'result{i}']}</li>" for i in (1, 2, 3, 4, 5)),
        "</ul></section>",
        f"<footer>{c['foot']}</footer>",
        "</div></body></html>",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build docs/index.html and docs/index.zh.html.")
    parser.add_argument("--repo", help="repository url to link from the page header")
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "docs"))
    args = parser.parse_args(argv)

    facts = collect()
    output = Path(args.out_dir)
    for lang, name in (("en", "index.html"), ("zh", "index.zh.html")):
        page = render(lang, facts, args.repo or "")
        (output / name).write_text(page, encoding="utf-8")
        print(f"{output / name}: {len(page):,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())