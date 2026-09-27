"""Copy a trained model into an ESP-IDF project.

The project embeds the flatbuffer with ``EMBED_FILES``, so it only has to sit
next to the component that uses it. Run this after retraining.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = REPO_ROOT / "training" / "out" / "waveform_model.tflite"
TARGET_NAME = "model.tflite"


def install(model: Path, project: Path, destination: str = "main") -> Path:
    target_dir = project / destination
    if not target_dir.is_dir():
        raise FileNotFoundError(f"{target_dir} does not exist")
    target = target_dir / TARGET_NAME
    shutil.copy2(model, target)
    return target


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        print("usage: install_model.py PROJECT_DIR [--model PATH] [--dest main]")
        return 2

    project = Path(argv[0]).resolve()
    model = DEFAULT_MODEL
    destination = "main"
    index = 1
    while index < len(argv):
        if argv[index] == "--model":
            model = Path(argv[index + 1]).resolve()
            index += 2
        elif argv[index] == "--dest":
            destination = argv[index + 1]
            index += 2
        else:
            print(f"error: unknown argument {argv[index]!r}", file=sys.stderr)
            return 2

    if not model.is_file():
        print(f"error: model not found: {model}", file=sys.stderr)
        return 2

    target = install(model, project, destination)
    print(f"{target} ({target.stat().st_size} bytes) installed from {model.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
