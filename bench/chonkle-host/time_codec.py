# ruff: noqa: T201
# /// script
# requires-python = ">=3.13"
# dependencies = ["chonkle"]
#
# [tool.uv.sources]
# chonkle = { path = "../.." }
# ///
"""Investigate fn() call timing vs data size for COG WASM codecs.

Run with:
    uv run bench/chonkle-host/time_codec.py

Produces per-step timing for multiple input sizes to determine whether
the 2s per call is fixed overhead or scales with data volume.
"""

from __future__ import annotations

import logging
import zlib
from pathlib import Path

from chonkle.executor import prepare, run
from chonkle.pipeline import Pipeline
from chonkle.resolver import Resolver

logging.basicConfig(level=logging.WARNING, format="%(message)s")

REPO_ROOT = Path(__file__).parent.parent.parent
CODEC_DIR = REPO_ROOT / "codec"
CHUNKS_DIR = REPO_ROOT / "tests" / "fixtures" / "chunks"

RESOLVER = Resolver(
    paths={
        "zlib": CODEC_DIR / "zlib-rs" / "zlib.wasm",
        "tiff-predictor-2": CODEC_DIR / "tiff-predictor-2-c" / "tiff-predictor-2.wasm",
        "identity": CODEC_DIR / "identity-c" / "identity.wasm",
    }
)


def _make_zlib_only_pipeline() -> dict:
    return {
        "codec_id": "zlib-only",
        "direction": "decode",
        "inputs": {"bytes": {"type": "bytes"}},
        "constants": {"level": {"type": "int", "value": 9}},
        "outputs": {"bytes": "zlib.bytes"},
        "steps": {
            "zlib": {
                "codec_id": "zlib",
                "inputs": {"bytes": "input.bytes", "level": "constant.level"},
            }
        },
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
        "steps": {
            "predictor2": {
                "codec_id": "tiff-predictor-2",
                "inputs": {
                    "bytes": "input.bytes",
                    "bytes_per_sample": "constant.bytes_per_sample",
                    "width": "constant.width",
                },
            }
        },
    }


def _make_identity_pipeline() -> dict:
    return {
        "codec_id": "identity",
        "direction": "decode",
        "inputs": {"bytes": {"type": "bytes"}},
        "constants": {},
        "outputs": {"bytes": "identity.bytes"},
        "steps": {
            "identity": {
                "codec_id": "identity",
                "inputs": {"bytes": "input.bytes"},
            }
        },
    }


def run_identity(data: bytes, label: str) -> bytes:
    print(f"\n--- identity: {label} ({len(data):,} bytes) ---")
    pipeline = Pipeline.parse(_make_identity_pipeline())
    prepared = prepare(pipeline, "decode", resolver=RESOLVER)
    result = run(prepared, {"bytes": data})
    return result["bytes"]


def _make_zlib_encode_pipeline() -> dict:
    return {
        "codec_id": "zlib-encode-only",
        "direction": "encode",
        "inputs": {"bytes": {"type": "bytes"}},
        "constants": {"level": {"type": "int", "value": 9}},
        "outputs": {"bytes": "zlib.bytes"},
        "steps": {
            "zlib": {
                "codec_id": "zlib",
                "inputs": {"bytes": "input.bytes", "level": "constant.level"},
            }
        },
    }


def run_zlib(data: bytes, label: str) -> bytes:
    print(f"\n--- zlib decode: {label} ({len(data):,} bytes compressed) ---")
    pipeline = Pipeline.parse(_make_zlib_only_pipeline())
    prepared = prepare(pipeline, "decode", resolver=RESOLVER)
    result = run(prepared, {"bytes": data})
    return result["bytes"]


def run_zlib_encode(data: bytes, label: str) -> bytes:
    print(f"\n--- zlib encode: {label} ({len(data):,} bytes raw) ---")
    pipeline = Pipeline.parse(_make_zlib_encode_pipeline())
    prepared = prepare(pipeline, "encode", resolver=RESOLVER)
    result = run(prepared, {"bytes": data})
    return result["bytes"]


def run_pred2(data: bytes, width: int, label: str) -> bytes:
    print(f"\n--- predictor2 decode: {label} ({len(data):,} bytes) width={width} ---")
    pipeline = Pipeline.parse(_make_pred2_only_pipeline(width))
    prepared = prepare(pipeline, "decode", resolver=RESOLVER)
    result = run(prepared, {"bytes": data})
    return result["bytes"]


def main() -> None:
    print("=" * 70)
    print("CODEC TIMING INVESTIGATION")
    print("Each [TIMING] line shows from_file / store+linker / instantiate / fn")
    print("=" * 70)

    # --- zlib at various sizes ---
    tiny_raw = bytes(range(64))
    small_raw = bytes(range(256)) * 4  # 1 KB
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

    # --- identity at various sizes (ABI isolation: pure memcpy in WASM) ---
    identity_wasm = CODEC_DIR / "identity-c" / "identity.wasm"
    if identity_wasm.exists():
        run_identity(bytes(64), "64 B")
        run_identity(bytes(1024), "1 KB")
        run_identity(bytes(65536), "64 KB")
        run_identity(bytes(1048576), "1 MB")
        run_identity(bytes(2097152), "2 MB")
    else:
        print(f"\n[identity codec not found — skipping: {identity_wasm}]")

    # --- predictor2 at various sizes ---
    # width=4 means each row is 4 samples → 8 bytes; rows = len/8
    run_pred2(bytes(8), width=4, label="8 B (1 row)")
    run_pred2(bytes(1024), width=4, label="1 KB")
    run_pred2(bytes(65536), width=256, label="64 KB, width=256")
    run_pred2(bytes(1048576), width=512, label="1 MB, width=512")
    repeat = 3
    for i in range(repeat):
        run_pred2(
            bytes(2097152),
            width=1024,
            label=f"2 MB (real tile size), width=1024 [run {i + 1}/{repeat}]",
        )

    # --- lowering vs lifting asymmetry (Experiment 4) ---
    # Same ~2 MB total ABI traffic, but split differs: one is mostly lowering
    # (large input, tiny output) and the other is mostly lifting (tiny input,
    # large output).
    zeros_2mb = bytes(2 * 1024 * 1024)
    compressed_zeros = zlib.compress(zeros_2mb, level=9)
    # lowering-heavy: 2 MB in → tiny out
    run_zlib_encode(zeros_2mb, "2 MB zeros → tiny (lowering heavy)")
    # lifting-heavy: tiny in → 2 MB out
    run_zlib(compressed_zeros, "tiny → 2 MB zeros (lifting heavy)")

    print("\n" + "=" * 70)
    print("DONE")


if __name__ == "__main__":
    main()
