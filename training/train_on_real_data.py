"""Retrain the waveform classifier on data captured from the board.

The first model was trained on an idealised simulation. On hardware its sine
accuracy collapsed because the real signal chain (DAC quantisation, ADC noise,
sampling) leaves far more harmonic content than the simulation did: the
simulated sine was 0.1% third harmonic, the measured one 5.3%.

This script trains on features captured from the device instead, and reports
both the old and the new model on the same held-out real test set so the
improvement is measured rather than assumed.

Run:
    .venv\\Scripts\\python.exe training\\train_on_real_data.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import tensorflow as tf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from train_waveform_classifier import CLASSES, N_FEATURES, build_model, quantise  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "out"


def load_captures(paths: list[Path]) -> tuple[np.ndarray, np.ndarray]:
    features: list[list[float]] = []
    labels: list[int] = []
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            parts = line.split(",")
            if len(parts) != N_FEATURES + 1:
                continue
            labels.append(int(parts[0]))
            features.append([float(v) for v in parts[1:]])
    return np.array(features, dtype=np.float32), np.array(labels, dtype=np.int32)


def stratified_split(
    features: np.ndarray, labels: np.ndarray, test_fraction: float, seed: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    train_idx: list[int] = []
    test_idx: list[int] = []
    for cls in range(len(CLASSES)):
        idx = np.flatnonzero(labels == cls)
        rng.shuffle(idx)
        cut = int(round(len(idx) * test_fraction))
        test_idx.extend(idx[:cut].tolist())
        train_idx.extend(idx[cut:].tolist())
    rng.shuffle(train_idx)
    rng.shuffle(test_idx)
    return (
        features[train_idx],
        labels[train_idx],
        features[test_idx],
        labels[test_idx],
    )


def tflite_accuracy(model_path: Path, features: np.ndarray, labels: np.ndarray) -> float:
    interpreter = tf.lite.Interpreter(model_content=model_path.read_bytes())
    interpreter.allocate_tensors()
    detail = interpreter.get_input_details()[0]
    output = interpreter.get_output_details()[0]
    scale = detail["quantization_parameters"]["scales"][0]
    zero_point = detail["quantization_parameters"]["zero_points"][0]
    correct = 0
    for row, label in zip(features, labels):
        quantised = np.clip(np.rint(row / scale) + zero_point, -128, 127).astype(np.int8)
        interpreter.set_tensor(detail["index"], quantised.reshape(1, -1))
        interpreter.invoke()
        prediction = int(np.argmax(interpreter.get_tensor(output["index"])))
        correct += prediction == int(label)
    return correct / len(labels)


def per_class_accuracy(predictions: np.ndarray, labels: np.ndarray) -> dict[str, float]:
    result: dict[str, float] = {}
    for cls, name in enumerate(CLASSES):
        mask = labels == cls
        if mask.any():
            result[name] = float((predictions[mask] == labels[mask]).mean())
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", nargs="+", help="CSV files captured from the board")
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--seed", type=int, default=20260927)
    parser.add_argument("--test-fraction", type=float, default=0.25)
    parser.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args()

    features, labels = load_captures([Path(p) for p in args.captures])
    counts = {name: int((labels == i).sum()) for i, name in enumerate(CLASSES)}
    print(f"captured samples: {len(labels)}  {counts}")

    x_train, y_train, x_test, y_test = stratified_split(
        features, labels, args.test_fraction, args.seed
    )
    print(f"train {len(y_train)}  test {len(y_test)}")

    previous = OUT_DIR / "waveform_model.tflite"
    if previous.is_file():
        before = tflite_accuracy(previous, x_test, y_test)
        print(f"\nprevious model on the real test set: {before:.2%}")
        interpreter = tf.lite.Interpreter(model_content=previous.read_bytes())
        interpreter.allocate_tensors()
        detail = interpreter.get_input_details()[0]
        output = interpreter.get_output_details()[0]
        scale = detail["quantization_parameters"]["scales"][0]
        zero_point = detail["quantization_parameters"]["zero_points"][0]
        preds = []
        for row in x_test:
            q = np.clip(np.rint(row / scale) + zero_point, -128, 127).astype(np.int8)
            interpreter.set_tensor(detail["index"], q.reshape(1, -1))
            interpreter.invoke()
            preds.append(int(np.argmax(interpreter.get_tensor(output["index"]))))
        print(
            "  per class: "
            + ", ".join(
                f"{k} {v:.1%}"
                for k, v in per_class_accuracy(np.array(preds), y_test).items()
            )
        )
    else:
        before = None
        print("no previous model to compare against")

    tf.random.set_seed(args.seed)
    model = build_model()
    model.fit(
        x_train,
        y_train,
        epochs=args.epochs,
        batch_size=32,
        verbose=2,
        validation_split=0.15,
    )
    loss, float_accuracy = model.evaluate(x_test, y_test, verbose=0)

    tflite_model = quantise(model, x_train)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    candidate = out_dir / "waveform_model_real.tflite"
    candidate.write_bytes(tflite_model)
    after = tflite_accuracy(candidate, x_test, y_test)

    interpreter = tf.lite.Interpreter(model_content=tflite_model)
    interpreter.allocate_tensors()
    detail = interpreter.get_input_details()[0]
    output = interpreter.get_output_details()[0]
    scale = detail["quantization_parameters"]["scales"][0]
    zero_point = detail["quantization_parameters"]["zero_points"][0]
    preds = []
    for row in x_test:
        q = np.clip(np.rint(row / scale) + zero_point, -128, 127).astype(np.int8)
        interpreter.set_tensor(detail["index"], q.reshape(1, -1))
        interpreter.invoke()
        preds.append(int(np.argmax(interpreter.get_tensor(output["index"]))))
    after_per_class = per_class_accuracy(np.array(preds), y_test)

    print(f"\nfloat model, real test set : {float_accuracy:.2%}")
    print(f"int8  model, real test set : {after:.2%}")
    print("  per class: " + ", ".join(f"{k} {v:.1%}" for k, v in after_per_class.items()))
    if before is not None:
        print(f"\nbefore {before:.2%}  ->  after {after:.2%}  ({after - before:+.1%})")

    (out_dir / "model_info_real.json").write_text(
        json.dumps(
            {
                "captured_samples": int(len(labels)),
                "class_counts": counts,
                "train": int(len(y_train)),
                "test": int(len(y_test)),
                "before_accuracy": before,
                "after_accuracy": after,
                "float_accuracy": float(float_accuracy),
                "per_class": after_per_class,
                "tflite_bytes": len(tflite_model),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nmodel -> {candidate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
