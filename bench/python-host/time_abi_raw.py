# ruff: noqa: T201
# /// script
# requires-python = ">=3.13"
# dependencies = ["wasmtime==41.*"]
# ///
"""Minimal wasmtime-py direct call — bypasses chonkle executor.

Mirrors bench/rust-host/src/main.rs for apples-to-apples comparison.
Run with:
    uv run bench/python-host/time_abi_raw.py
"""

from __future__ import annotations

import time
from pathlib import Path

import wasmtime
import wasmtime.component

REPO_ROOT = Path(__file__).parent.parent.parent
IDENTITY_WASM = REPO_ROOT / "codec" / "identity-c" / "identity.wasm"
IFACE = "chonkle:codec/transform@0.1.0"

engine = wasmtime.Engine()
component = wasmtime.component.Component.from_file(engine, str(IDENTITY_WASM))

store = wasmtime.Store(engine)
store.set_wasi(wasmtime.WasiConfig())
linker = wasmtime.component.Linker(engine)
linker.add_wasip2()
instance = linker.instantiate(store, component)

iface_idx = instance.get_export_index(store, IFACE)
if iface_idx is None:
    raise RuntimeError(f"interface {IFACE!r} not found in component")
fn_idx = instance.get_export_index(store, "decode", iface_idx)
if fn_idx is None:
    raise RuntimeError(f"'decode' not found in {IFACE!r}")
fn = instance.get_func(store, fn_idx)
if fn is None:
    raise RuntimeError("'decode' export is not a function")

# Warm up
fn(store, [("bytes", bytes(64))])
fn.post_return(store)

for size, label in [(1 << 20, "1 MB"), (2 << 20, "2 MB")]:
    data = bytes(size)
    port_map = [("bytes", data)]
    for run in range(1, 4):
        t0 = time.perf_counter()
        result = fn(store, port_map)
        elapsed = time.perf_counter() - t0
        fn.post_return(store)
        out = sum(len(v) for _, v in result)
        total = size + out
        throughput = total / elapsed / 1_048_576
        print(
            f"[TIMING] identity.wasm decode: fn={elapsed:.3f}s  in={size}B "
            f"out={out}B abi_total={total}B throughput={throughput:.1f}MB/s  "
            f"({label} run {run}/3, Python raw)"
        )
