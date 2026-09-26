"""Copy the canonical device agent into each ESP-IDF project.

ESP-IDF projects need their components inside their own tree, so the canonical
source under ``firmware/esp-idf`` is vendored into ``<project>/components``.
Run this after editing the agent.
"""

from __future__ import annotations

import filecmp
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "firmware" / "esp-idf" / "mcu_telemetry"
COMPONENT_NAME = "mcu_telemetry"

SKIP_DIRS = {"build", ".git", "managed_components"}


def files_under(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_file() and not any(part in SKIP_DIRS for part in path.parts):
            yield path


def sync(project: Path) -> tuple[int, int]:
    """Return (copied, unchanged)."""
    destination = project / "components" / COMPONENT_NAME
    copied = unchanged = 0

    for source_file in files_under(SOURCE):
        relative = source_file.relative_to(SOURCE)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and filecmp.cmp(source_file, target, shallow=False):
            unchanged += 1
            continue
        shutil.copy2(source_file, target)
        copied += 1
    return copied, unchanged


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        print("usage: sync_firmware.py PROJECT_DIR [PROJECT_DIR ...]")
        return 2
    if not SOURCE.is_dir():
        print(f"error: canonical component not found: {SOURCE}", file=sys.stderr)
        return 2

    status = 0
    for argument in argv:
        project = Path(argument).resolve()
        if not (project / "CMakeLists.txt").is_file():
            print(f"error: {project} does not look like an ESP-IDF project", file=sys.stderr)
            status = 1
            continue
        copied, unchanged = sync(project)
        print(f"{project.name}: {copied} file(s) copied, {unchanged} already up to date")
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
