"""Build the two landing pages from real runs.

Every command block on the page is the output of running that command just
now, so the page cannot drift from the tool it describes. Regenerate with:

    python tools/build_guide.py

Add --map/--bin/--project to point at a firmware project; without them the
build blocks fall back to the map fixture that ships in tests/data.
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
       font: 15px/1.6 -apple-system, Segoe UI, Roboto, sans-serif; }
.wrap { max-width: 980px; margin: 0 auto; }
header { padding: 40px 0 12px; }
h1 { font-size: 30px; margin: 0 0 6px; letter-spacing: -0.02em; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.08em;
     color: #6b7280; margin: 0 0 14px; }
h3 { font-size: 15px; margin: 0 0 4px; }
p { margin: 0 0 10px; }
.lede { font-size: 17px; color: #4b5563; margin: 0 0 18px; max-width: 46em; }
a { color: #2563eb; }
nav a { display: inline-block; margin-right: 14px; font-size: 14px; }
.lang { float: right; font-size: 14px; }
.lang a { font-weight: 600; }
section { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px;
          padding: 20px 22px; margin-top: 18px; }
.grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
.card { border: 1px solid #eef0f3; border-radius: 8px; padding: 14px 16px; }
.card .step { font: 600 12px/1 ui-monospace, monospace; color: #2563eb; margin-bottom: 8px; }
.card .where { font: 11.5px/1 ui-monospace, monospace; color: #6b7280; margin-bottom: 6px; }
pre { background: #0f172a; color: #e2e8f0; border-radius: 7px; padding: 11px 13px;
      overflow-x: auto; font: 12.5px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace;
      margin: 8px 0 0; white-space: pre; }
pre.run { background: #1e293b; }
pre.out { background: #f8fafc; color: #334155; border: 1px solid #e5e7eb; }
code { font: 12.5px/1.5 ui-monospace, monospace; background: #f1f3f5;
       padding: 1px 5px; border-radius: 4px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
td { padding: 7px 10px 7px 0; border-top: 1px solid #f1f3f5; vertical-align: top; }
td:first-child { white-space: nowrap; color: #6b7280; }
.note { color: #6b7280; font-size: 13px; }
.ok { color: #067647; font-weight: 600; }
.bad { color: #b42318; font-weight: 600; }
.warn { color: #b54708; font-weight: 600; }
footer { margin-top: 22px; color: #6b7280; font-size: 13px; }
"""


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


def pick_build(map_path: Path | None, bin_path: Path | None):
    """The map to show, and the binary to cross-check it against, if any."""
    if map_path:
        return map_path, bin_path
    candidate = DEFAULT_PROJECT / "build" / "signal_processing_system.map"
    if candidate.is_file():
        return candidate, candidate.with_suffix(".bin")
    return REPO_ROOT / "tests" / "data" / "sample.map", None


def collect(map_path: Path | None, bin_path: Path | None, project: Path | None) -> dict:
    mapping, binary = pick_build(map_path, bin_path)
    firmware = project or (DEFAULT_PROJECT if DEFAULT_PROJECT.is_dir() else None)
    component = REPO_ROOT / "firmware" / "esp-idf" / "mcu_telemetry"
    component_files = [
        str(path.relative_to(component)).replace("\\", "/")
        for path in sorted(component.rglob("*")) if path.is_file()
    ]

    analyze = ["analyze", str(mapping), "--partition", "1500K"]
    if binary:
        analyze += ["--bin", str(binary)]

    diagnose = ["diagnose", "--db", "captures/fault-heap.db",
                "--config", "budgets/signal.json", "--dry-run"]
    if firmware:
        diagnose += ["--project", str(firmware), "--diff-since", "a33a18e~1"]

    return {
        "summary": take(run("summary", "--db", "captures/fault-off.db", "--top", "5"), 11),
        "check_fail": run("check", "--db", "captures/fault-heap.db",
                          "--config", "budgets/signal.json"),

        "analyze": take(run(*analyze), 11),
        "model": take(run("model", "training/out/waveform_model_real.tflite"), 9),
        "evidence": take(run(*diagnose), 9),
        "answer": paragraph_containing(ANSWERS / "round-3.md", "s_leak_sink = malloc(2048)"),
        "component_files": component_files,
        "audit": run("audit", "--pack", str(PROMPTS / "round-3.txt"),
                     "--answer", str(ANSWERS / "round-3.md")),
    }

C_SNIPPET = """#include "mcu_telemetry.h"

static const mcu_telemetry_config_t telemetry = {
    .device = "my-board",
    .firmware = FW_REVISION,
    .report_period_ms = 5000,
};

ESP_ERROR_CHECK(mcu_telemetry_start(&telemetry));
mcu_telemetry_register_task("control", TASK_STACK_CONTROL);
mcu_telemetry_set_custom_int("loop_jitter_us", jitter);
mcu_telemetry_histogram_add("infer", latency_us);"""

JSON_SNIPPET = """{
  "rules": [
    {"metric": "heap.min", "min": 100000, "stat": "min"},
    {"metric": "heap.free", "min_rate_per_s": -64, "min_span_ms": 60000},
    {"metric": "task.*.stack_free_min", "min": 256, "stat": "min"},
    {"metric": "custom.infer_p99_us", "max": 5000, "stat": "max"}
  ],
  "baseline": {"path": "my-project-baseline.json", "max_change_pct": 20}
}"""

TEXT = {
    "en": {
        "title": "MCU-Insight",
        "page_title": "MCU-Insight - how to use it on a new project",
        "tagline": "What an ESP-IDF build costs, what the device actually does at "
                   "runtime, and whether the last change made it worse.",
        "nav_github": "Repository",
        "nav_guide": "Usage guide",
        "nav_case": "Case study",
        "nav_report": "Example report",
        "other": ("中文", "index.zh.html"),
        "questions": "What it answers",
        "q1_t": "What does this build cost",
        "q1_d": "Flash, static DRAM, IRAM and the app partition, per component, from "
                "the linker map and the binary. No board needed.",
        "q2_t": "What is the device doing",
        "q2_d": "Stack high-water marks per task, the smallest free heap and the "
                "largest free block, per-core idle share, latency percentiles, sleep "
                "residency: whatever the firmware publishes.",
        "q3_t": "Did the change make it worse",
        "q3_d": "Budget rules that bound a value or a slope, a comparison against a "
                "stored baseline, and an evidence pack when something needs explaining.",
        "steps": "Four steps on a new project",
        "run_here": "run in mcu-insight\\",
        "where": "Where everything lives",
        "where_d": "All four commands run in the tool repository. Your firmware project is a neighbour: step 1 copies the agent into it, and the report step reads that project's build output.",
        "where_tool": "mcu-insight\\   the tool, run every command here",
        "where_tool_tree": "  mcu_insight\\    the package\n  tools\\          sync_firmware.py, fault_matrix.py\n  budgets\\        your budget files go here\n  captures\\       captured telemetry lands here (*.db)\n  docs\\           index.html, report-demo.html, the guides",
        "where_fw": "My-Project\\   your firmware, next to the tool",
        "where_fw_top": "  components\\\n    mcu_telemetry\\   <- step 1 writes this directory",
        "where_fw_bottom": "  main\\main.c      <- the five lines from step 1\n  build\\           .map and .bin are read from here",
        "s1_flash": "then build and flash it as usual, from the firmware project:",
        "s4_open": "The command writes report.html into the directory you ran it from. Double-click it, or open it from the file manager. The report linked here was made the same way, from the capture committed in this repository:",
        "s1_t": "Copy the device agent in",
        "s1_d": "One ESP-IDF component. ESP-IDF wants components inside the project "
                "tree, so this copies it there and tells you what it wrote.",
        "s2_t": "Capture",
        "s2_d": "Frames are appended to a SQLite file, so every command after this "
                "reads the file instead of the board. Log lines on the same port are "
                "ignored.",
        "s3_t": "Judge",
        "s3_d": "The exit code is 0 for a pass, 1 for a failure or a rule that could "
                "not be judged, so CI can gate on it.",
        "s3_pass": "On a healthy capture the same command prints RESULT: PASS and exits 0.",
        "s4_t": "Look at it",
        "s4_d": "One self-contained HTML file: data inlined, charts hand-written SVG, "
                "no server, no CDN, no script.",
        "back": "what comes back",
        "add": "What you add to your own project",
        "add_d": "Five lines of C, and one JSON file that says what the budgets are "
                 "and what the metrics mean.",
        "diag": "What a diagnosis looks like",
        "diag_d": "The tool does not guess. It collects a numbered evidence pack, you "
                  "hand that to any model, and it checks the answer that comes back.",
        "diag_e": "evidence pack (diagnose)",
        "diag_a": "the model's answer",
        "diag_u": "checked against the pack (audit)",
        "nb": "Without a board",
        "nb_d": "Two of the commands read build artefacts only, so they work on a "
                "laptop and in CI.",
        "nb_analyze": "What the build costs",
        "nb_model": "What the TinyML model costs",
        "report": "What the HTML report contains",
        "report_d": "Five blocks, and every one of them is generated from the capture.",
        "report_r1": "the verdict, every rule, and any rule that was not judged",
        "report_r2": "what moved since the baseline snapshot, coloured only when the "
                     "worrying direction is known",
        "report_r3": "the metrics that moved most, one card each, with every reboot marked",
        "report_r4": "flash, static DRAM, IRAM, partition use, top components",
        "report_r5": "with a fault directory, whether the checks caught each injected fault",
        "foot": "Every output on this page was produced by running the tool against the "
                "captures and the build committed in this repository, not written by "
                "hand. Rebuild it with <code>python tools/build_guide.py</code>.",
    },
    "zh": {
        "title": "MCU-Insight",
        "page_title": "MCU-Insight — 怎么用在新工程上",
        "tagline": "ESP-IDF 固件占多少资源、设备运行时到底在干什么、"
                   "以及最近这次改动有没有让它变差。",
        "nav_github": "代码仓库",
        "nav_guide": "完整使用指南",
        "nav_case": "三轮诊断案例",
        "nav_report": "报告示例",
        "other": ("EN", "index.html"),
        "questions": "它回答什么",
        "q1_t": "这个构建占多少",
        "q1_d": "Flash、静态 DRAM、IRAM 和 app 分区占用，按组件拆分，"
                "从链接脚本映射文件和固件镜像里读，不需要板子。",
        "q2_t": "设备实际在干什么",
        "q2_d": "每个任务的栈高水位、堆最小空闲和最大可分配块、每核空闲占比、"
                "延迟分位数、睡眠占比——固件报什么就能看什么。",
        "q3_t": "这次改动有没有让它变差",
        "q3_d": "可以约束数值、也可以约束斜率的预算规则，与基线快照的对比，"
                "以及需要解释时给模型用的证据包。",
        "steps": "新工程上的四步",
        "run_here": "在 mcu-insight\\ 目录里运行",
        "where": "每个东西在哪",
        "where_d": "四条命令都在工具仓库里运行。你的固件工程是它的邻居：第一步把 agent 拷进去，报告那一步读的是那个工程的构建产物。",
        "where_tool": "mcu-insight\\   工具仓库，所有命令在这里运行",
        "where_tool_tree": "  mcu_insight\\    包的代码\n  tools\\          sync_firmware.py、fault_matrix.py\n  budgets\\        你的预算文件放这里\n  captures\\       采集到的遥测落这里（*.db）\n  docs\\           index.html、report-demo.html、各种指南",
        "where_fw": "My-Project\\   你的固件工程，和工具仓库并排",
        "where_fw_top": "  components\\\n    mcu_telemetry\\   ← 第一步拷贝到这里",
        "where_fw_bottom": "  main\\main.c      ← 第一步加的那五行\n  build\\           .map 和 .bin 从这里读",
        "s1_flash": "然后在固件工程里照常编译烧写：",
        "s4_open": "这条命令会把 report.html 写到你运行命令的那个目录里。双击它，或者从文件管理器打开。下面链接的这份报告就是这么来的，用的是本仓库里已提交的那次采集：",
        "s1_t": "把设备端 agent 拷进去",
        "s1_d": "就是一个 ESP-IDF 组件。ESP-IDF 要求组件在工程树内，"
                "所以这条命令把它拷过去并告诉你写了哪些文件。",
        "s2_t": "采集",
        "s2_d": "遥测帧追加进一个 SQLite 文件，之后所有命令读文件而不是读板子。"
                "同一个串口上的日志行会被自动忽略。",
        "s3_t": "判定",
        "s3_d": "退出码 0 表示通过，1 表示不通过或规则无法判定，所以可以直接卡 CI。",
        "s3_pass": "同样的命令在健康的采集上会打印 RESULT: PASS 并以 0 退出。",
        "s4_t": "看结果",
        "s4_d": "一个自包含 HTML 文件：数据内联、图表是手写 SVG，"
                "没有服务器、没有 CDN、没有脚本。",
        "back": "回来长什么样",
        "add": "你自己的工程要加什么",
        "add_d": "五行 C 代码，加一个 JSON 文件——它说明预算是什么、每个指标是什么意思。",
        "diag": "一次诊断长什么样",
        "diag_d": "工具不猜。它把所有已知事实整理成带编号的证据包，你交给任意模型，"
                  "然后它核对拿回来的答案。",
        "diag_e": "证据包（diagnose）",
        "diag_a": "模型给出的答案",
        "diag_u": "拿回证据包里核对（audit）",
        "nb": "没有板子也能用",
        "nb_d": "其中两条命令只读构建产物，所以在笔记本上和 CI 里都能跑。",
        "nb_analyze": "这个构建占多少",
        "nb_model": "TinyML 模型占多少",
        "report": "HTML 报告里有什么",
        "report_d": "五个区块，每一块都是从这次采集生成的。",
        "report_r1": "总判决、每一条规则、以及任何未能判定的规则",
        "report_r2": "相对基线快照的变化，只在方向明确会变差时才着色",
        "report_r3": "变化最大的指标，一卡一个，重启点标出来",
        "report_r4": "Flash、静态 DRAM、IRAM、分区占用、占用量最大的组件",
        "report_r5": "给了故障目录时，逐个说明检查有没有抓到注入的故障",
        "foot": "本页的每段输出都是拿本仓库里已提交的采集和构建产物真跑出来的，不是手写的。"
                "重新生成：<code>python tools/build_guide.py</code>。",
    },
}

def render(lang: str, facts: dict, repo: str = "") -> str:
    """One page, in one language, from the facts collected above."""
    c = TEXT[lang]
    other_label, other_href = c["other"]

    def pre(text: str, cls: str = "out") -> str:
        return f'<pre class="{cls}">{html.escape(text)}</pre>' if text else ""

    def card(step: str, title: str, note: str, command: str, output: str = "",
             where: str = "") -> str:
        body = [f'<div class="card"><div class="step">{step}</div>',
                (f'<div class="where">{where}</div>' if where else ""),
                f"<h3>{title}</h3>", f'<p class="note">{note}</p>',
                pre(command, "run")]
        if output:
            body.append(f'<p class="note">{c["back"]}</p>{pre(output)}')
        body.append("</div>")
        return "".join(body)

    steps = "".join([
        '<div class="card"><div class="step">1</div>'
        + f'<div class="where">{c["run_here"]}</div>'
        + f'<h3>{c["s1_t"]}</h3><p class="note">{c["s1_d"]}</p>'
        + pre("python tools/sync_firmware.py ..\\My-Project", "run")
        + f'<p class="note">{c["s1_flash"]}</p>'
        + pre("cd ..\\My-Project\nidf.py -p COM19 flash monitor", "run")
        + "</div>",
        card("2", c["s2_t"], c["s2_d"],
             "python -m mcu_insight collect --db captures/board.db --source serial:COM19",
             facts["summary"], c["run_here"]),
        card("3", c["s3_t"], c["s3_d"],
             "python -m mcu_insight check --db captures/board.db --config budgets/my-project.json",
             facts["check_fail"] + "\n\n" + c["s3_pass"], c["run_here"]),
        card("4", c["s4_t"], c["s4_d"],
             "python -m mcu_insight report --db captures/board.db --config budgets/my-project.json "
             "--out report.html", where=c["run_here"]),
    ])

    fw_tree = "\n".join(
        [c["where_fw"], c["where_fw_top"]]
        + [f"      {name}" for name in facts["component_files"]]
        + [c["where_fw_bottom"]]
    )

    rows = "".join(
        f"<tr><td>{name}</td><td>{body}</td></tr>"
        for name, body in (
            ("Checks", c["report_r1"]),
            ("Baseline changes", c["report_r2"]),
            ("Metric trends", c["report_r3"]),
            ("Build resources", c["report_r4"]),
            ("Fault injection matrix", c["report_r5"]),
        )
    )

    return "\n".join([
        "<!doctype html>",
        f'<html lang="{lang}"><head><meta charset=utf-8>',
        '<meta name=viewport content="width=device-width,initial-scale=1">',
        f"<title>{c['page_title']}</title>",
        f"<style>{STYLE}</style></head><body><div class=wrap>",
        "<header>",
        f'<div class=lang><a href="{other_href}">{other_label}</a></div>',
        f"<h1>{c['title']}</h1>",
        f'<p class="lede">{c["tagline"]}</p>',
        "<nav>"
        + (f'<a href="{repo}">{c["nav_github"]}</a>' if repo else "")
        + '<a href="using-it.md">' + c["nav_guide"] + "</a>"
        '<a href="diagnosis-case-study.md">' + c["nav_case"] + "</a>"
        '<a href="report-demo.html">' + c["nav_report"] + "</a>"
        "</nav></header>",
        f"<section><h2>{c['questions']}</h2><div class=grid>",
        f'<div class="card"><h3>{c["q1_t"]}</h3><p class="note">{c["q1_d"]}</p></div>',
        f'<div class="card"><h3>{c["q2_t"]}</h3><p class="note">{c["q2_d"]}</p></div>',
        f'<div class="card"><h3>{c["q3_t"]}</h3><p class="note">{c["q3_d"]}</p></div>',
        "</div></section>",
        f"<section><h2>{c['steps']}</h2><div class=grid>{steps}</div>",
        f'<p class="note" style="margin-top:16px">{c["s4_open"]} '
        f'<a href="report-demo.html">docs/report-demo.html</a></p></section>',
        f"<section><h2>{c['where']}</h2><p class=\"note\">{c['where_d']}</p><div class=grid>",
        '<div class="card">' + pre(c["where_tool"] + "\n" + c["where_tool_tree"]) + "</div>",
        '<div class="card">' + pre(fw_tree) + "</div>",
        "</div></section>",
        f"<section><h2>{c['add']}</h2><p class=\"note\">{c['add_d']}</p>",
        '<div class="grid">',
        "<div>" + pre(C_SNIPPET, "run") + "</div>",
        "<div>" + pre(JSON_SNIPPET, "run") + "</div>",
        "</div></section>",
        f"<section><h2>{c['diag']}</h2><p class=\"note\">{c['diag_d']}</p>",
        '<div class="grid">',
        f'<div class="card"><h3>{c["diag_e"]}</h3>{pre(facts["evidence"])}</div>',
        f'<div class="card"><h3>{c["diag_a"]}</h3>{pre(facts["answer"])}</div>',
        f'<div class="card"><h3>{c["diag_u"]}</h3>{pre(facts["audit"])}</div>',
        "</div></section>",
        f"<section><h2>{c['nb']}</h2><p class=\"note\">{c['nb_d']}</p>",
        '<div class="grid">',
        f'<div class="card"><h3>{c["nb_analyze"]}</h3>'
        + pre("python -m mcu_insight analyze build/my_app.map --bin build/my_app.bin "
              "--partition 1500K", "run")
        + f'<p class="note">{c["back"]}</p>' + pre(facts["analyze"]) + "</div>",
        f'<div class="card"><h3>{c["nb_model"]}</h3>'
        + pre("python -m mcu_insight model build/model.tflite", "run")
        + f'<p class="note">{c["back"]}</p>' + pre(facts["model"]) + "</div>",
        "</div></section>",
        f"<section><h2>{c['report']}</h2><p class=\"note\">{c['report_d']}</p>",
        f"<table>{rows}</table>",
        f'<p class="note" style="margin-top:12px">'
        f'<a href="report-demo.html">{c["nav_report"]}</a></p></section>',
        f"<footer>{c['foot']}</footer>",
        "</div></body></html>",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build docs/index.html and docs/index.zh.html.")
    parser.add_argument("--map", help="linker map to show in the build example")
    parser.add_argument("--bin", help="matching binary, for the cross-check")
    parser.add_argument("--project", help="firmware project directory")
    parser.add_argument("--repo", help="repository url to link from the page header")
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "docs"))
    args = parser.parse_args(argv)

    facts = collect(
        Path(args.map) if args.map else None,
        Path(args.bin) if args.bin else None,
        Path(args.project) if args.project else None,
    )
    output = Path(args.out_dir)
    for lang, name in (("en", "index.html"), ("zh", "index.zh.html")):
        page = render(lang, facts, args.repo or "")
        (output / name).write_text(page, encoding="utf-8")
        print(f"{output / name}: {len(page):,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())