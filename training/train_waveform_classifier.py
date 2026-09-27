"""Train and quantise the waveform classifier used by the signal project.

The device captures 128 ADC samples, removes the DC level, applies a Hann
window and runs a 128-point FFT (see ``main/fft_process.c``).  The model sees
the same thing: the magnitude spectrum of bins 1..64, normalised so that the
bins sum to one.  Normalising makes the classifier independent of amplitude,
which is what we want - the shape of the spectrum is what identifies the
waveform.

Run:
    .venv\\Scripts\\python.exe training\\train_waveform_classifier.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import tensorflow as tf

FFT_SIZE = 128
SAMPLE_RATE = 1000
N_FEATURES = FFT_SIZE // 2
CLASSES = ("sine", "square", "triangle")
ADC_MIN, ADC_MAX = 0, 4095
ADC_CENTRE = 2048


def make_waveform(kind: str, phase: np.ndarray) -> np.ndarray:
    if kind == "sine":
        return np.sin(phase)
    if kind == "square":
        return np.sign(np.sin(phase))
    if kind == "triangle":
        return (2.0 / np.pi) * np.arcsin(np.sin(phase))
    raise ValueError(f"unknown waveform {kind!r}")


def sample_block(kind: str, rng: np.random.Generator) -> np.ndarray:
    """One 128-sample ADC block, as int16 counts."""
    freq = rng.uniform(20.0, 460.0)          # below the 500 Hz Nyquist limit
    amplitude = rng.uniform(250.0, 1500.0)
    centre = ADC_CENTRE + rng.uniform(-300.0, 300.0)
    phase = 2.0 * np.pi * freq * np.arange(FFT_SIZE) / SAMPLE_RATE + rng.uniform(0, 2 * np.pi)
    signal = centre + amplitude * make_waveform(kind, phase)
    signal += rng.normal(0.0, rng.uniform(0.0, 12.0), FFT_SIZE)   # ADC noise
    return np.clip(np.rint(signal), ADC_MIN, ADC_MAX).astype(np.int16)


def features(block: np.ndarray) -> np.ndarray:
    """Mirror the device DSP: integer mean removal, Hann window, FFT, magnitude."""
    work = block.astype(np.int32)
    mean = int((work.sum() + FFT_SIZE // 2) // FFT_SIZE)
    work = (work - mean).astype(np.float64)
    hann = 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(FFT_SIZE) / (FFT_SIZE - 1))
    spectrum = np.abs(np.fft.fft(work * hann))[: FFT_SIZE // 2 + 1]
    bins = spectrum[1:N_FEATURES + 1]
    total = bins.sum()
    if total <= 0:
        return np.zeros(N_FEATURES, dtype=np.float32)
    return (bins / total).astype(np.float32)


def build_dataset(count: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    rows = np.zeros((count, N_FEATURES), dtype=np.float32)
    labels = np.zeros((count,), dtype=np.int32)
    for index in range(count):
        kind = CLASSES[index % len(CLASSES)]
        rows[index] = features(sample_block(kind, rng))
        labels[index] = CLASSES.index(kind)
    order = rng.permutation(count)
    return rows[order], labels[order]


def build_model() -> tf.keras.Model:
    model = tf.keras.Sequential(
        [
            tf.keras.layers.Input(shape=(N_FEATURES,), name="spectrum"),
            tf.keras.layers.Dense(32, activation="relu"),
            tf.keras.layers.Dense(16, activation="relu"),
            tf.keras.layers.Dense(len(CLASSES), activation="softmax", name="class_prob"),
        ]
    )
    model.compile(
        optimizer=tf.keras.optimizers.Adam(0.005),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    return model


def quantise(model: tf.keras.Model, representative: np.ndarray) -> bytes:
    def generator():
        for row in representative:
            yield [row.reshape(1, N_FEATURES)]

    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = generator
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8
    return converter.convert()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-count", type=int, default=9000)
    parser.add_argument("--test-count", type=int, default=1500)
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--seed", type=int, default=20260927)
    parser.add_argument("--out", default=str(Path(__file__).parent / "out"))
    args = parser.parse_args()

    tf.random.set_seed(args.seed)
    x_train, y_train = build_dataset(args.train_count, args.seed)
    x_test, y_test = build_dataset(args.test_count, args.seed + 1)

    model = build_model()
    model.fit(x_train, y_train, epochs=args.epochs, batch_size=64, verbose=2,
              validation_split=0.1)
    loss, accuracy = model.evaluate(x_test, y_test, verbose=0)
    print(f"\nfloat model test accuracy: {accuracy:.4%}")

    tflite_model = quantise(model, x_train[:400])
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "waveform_model.tflite"
    model_path.write_bytes(tflite_model)
    print(f"tflite size: {len(tflite_model)} bytes -> {model_path}")

    interpreter = tf.lite.Interpreter(model_content=tflite_model)
    interpreter.allocate_tensors()
    in_detail = interpreter.get_input_details()[0]
    out_detail = interpreter.get_output_details()[0]

    correct = 0
    for row, label in zip(x_test, y_test):
        interpreter.set_tensor(in_detail["index"], quantise_row(row, in_detail))
        interpreter.invoke()
        prediction = int(np.argmax(interpreter.get_tensor(out_detail["index"])))
        correct += prediction == int(label)
    int8_accuracy = correct / len(y_test)
    print(f"int8 model test accuracy: {int8_accuracy:.4%}")

    summary = {
        "classes": list(CLASSES),
        "features": N_FEATURES,
        "float_accuracy": float(accuracy),
        "int8_accuracy": int8_accuracy,
        "tflite_bytes": len(tflite_model),
        "input": {
            "dtype": str(in_detail["dtype"]),
            "scale": float(in_detail["quantization_parameters"]["scales"][0]),
            "zero_point": int(in_detail["quantization_parameters"]["zero_points"][0]),
        },
        "output": {
            "dtype": str(out_detail["dtype"]),
            "scale": float(out_detail["quantization_parameters"]["scales"][0]),
            "zero_point": int(out_detail["quantization_parameters"]["zero_points"][0]),
        },
    }
    (out_dir / "model_info.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


def quantise_row(row: np.ndarray, detail: dict) -> np.ndarray:
    scale = detail["quantization_parameters"]["scales"][0]
    zero_point = detail["quantization_parameters"]["zero_points"][0]
    quantised = np.rint(row / scale) + zero_point
    return np.clip(quantised, -128, 127).astype(np.int8).reshape(1, -1)


if __name__ == "__main__":
    raise SystemExit(main())