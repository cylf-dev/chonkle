"""Investigate fn() call timing vs data size for COG WASM codecs.

Run with:
    uv run python scripts/time_codec.py

Produces per-step timing for multiple input sizes to determine whether
the 2s per call is fixed overhead or scales with data volume.
"""

from __future__ import annotations

import json
import logging
import zlib
from pathlib import Path

from chonkle.executor import run
from chonkle.pipeline import Pipeline

logging.basicConfig(level=logging.WARNING, format="%(message)s")

REPO_ROOT = Path(__file__).parent.parent
CODEC_DIR = REPO_ROOT / "codec"
CHUNKS_DIR = REPO_ROOT / "tests" / "fixtures" / "chunks"

ZLIB_SRC = f"file://{CODEC_DIR / 'zlib-rs' / 'zlib.wasm'}"
PRED2_SRC = f"file://{CODEC_DIR / 'tiff-predictor-2-c' / 'tiff-predictor-2.wasm'}"


def _make_zlib_only_pipeline() -> dict:
    return {
        "codec_id": "zlib-only",
        "direction": "decode",
        "inputs": {"bytes": {"type": "bytes"}},
        "constants": {"level": {"type": "int", "value": 9}},
        "outputs": {"bytes": "zlib.bytes"},
        "steps": [
            {
                "name": "zlib",
                "codec_id": "zlib",
                "src": ZLIB_SRC,
                "inputs": {"bytes": "input.bytes", "level": "constant.level"},
                "outputs": ["bytes"],
                "encode_only_inputs": ["level"],
            }
        ],
    }


def _make_pred2_only_pipeline(width: int) -> dict:
    return {
        "codec_id": "pred2-only",
        "direction": "decode",
        "inputs": {"bytes": {"type": "bytes"}},
        "constants": {
            "bytes_per_sample": {"type": "int", "value": 2},
            "width": {"type": "int", "value": width},
        },
        "outputs": {"bytes": "predictor2.bytes"},
        "steps": [
            {
                "name": "predictor2",
                "codec_id": "tiff-predictor-2",
                "src": PRED2_SRC,
                "inputs": {
                    "bytes": "input.bytes",
                    "bytes_per_sample": "constant.bytes_per_sample",
                    "width": "constant.width",
                },
                "outputs": ["bytes"],
            }
        ],
    }


def run_zlib(data: bytes, label: str) -> bytes:
    print(f"\n--- zlib decode: {label} ({len(data):,} bytes compressed) ---")
    pipeline = Pipeline.parse(_make_zlib_only_pipeline())
    result = run(pipeline, {"bytes": data}, "decode")
    return result["bytes"]


def run_pred2(data: bytes, width: int, label: str) -> bytes:
    print(f"\n--- predictor2 decode: {label} ({len(data):,} bytes) width={width} ---")
    pipeline = Pipeline.parse(_make_pred2_only_pipeline(width))
    result = run(pipeline, {"bytes": data}, "decode")
    return result["bytes"]


def main() -> None:
    print("=" * 70)
    print("CODEC TIMING INVESTIGATION")
    print("Each [TIMING] line shows from_file / store+linker / instantiate / fn")
    print("=" * 70)

    # --- zlib at various sizes ---
    tiny_raw = bytes(range(64))
    small_raw = bytes(range(256)) * 4   # 1 KB
    medium_raw = bytes(range(256)) * 256  # 64 KB
    large_raw = bytes(range(256)) * 4096  # 1 MB

    tiny_z = zlib.compress(tiny_raw, level=9)
    small_z = zlib.compress(small_raw, level=9)
    medium_z = zlib.compress(medium_raw, level=9)
    large_z = zlib.compress(large_raw, level=9)
    real_z = (CHUNKS_DIR / "cog-chunk-0").read_bytes()

    run_zlib(tiny_z, "64 B raw")
    run_zlib(small_z, "1 KB raw")
    run_zlib(medium_z, "64 KB raw")
    run_zlib(large_z, "1 MB raw")
    run_zlib(real_z, "real COG chunk → 2 MB raw")

    # --- predictor2 at various sizes ---
    # width=4 means each row is 4 samples → 8 bytes; rows = len/8
    run_pred2(bytes(8), width=4, label="8 B (1 row)")
    run_pred2(bytes(1024), width=4, label="1 KB")
    run_pred2(bytes(65536), width=256, label="64 KB, width=256")
    run_pred2(bytes(1048576), width=512, label="1 MB, width=512")
    run_pred2(bytes(2097152), width=1024, label="2 MB (real tile size), width=1024")

    print("\n" + "=" * 70)
    print("DONE")


if __name__ == "__main__":
    main()
