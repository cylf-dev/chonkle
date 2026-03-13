# Component Model Canonical ABI Performance Investigation

## Background

The `TestCogChunkPipeline` tests were taking 4–9 seconds each. The hypothesis
going in was that wasmtime initialization overhead (Engine, Linker, Component
deserialization) was the cause. Timing instrumentation was added to
`_call_component()` to measure each operation separately.

## What Was Measured

Added `time.perf_counter()` splits around each operation in `_call_component()`:

```
from_file   store+linker   instantiate   fn      post_return   normalize
0.003s      0.000s         0.000s        2.239s  0.000s        0.000s
```

The initialization hypothesis was wrong. Everything except `fn(store, port_map)`
is essentially free once wasmtime's compiled-code disk cache is warm.

## Root Cause

`fn()` time scales linearly with data size at approximately **1.7 MB/s**:

| Output size | zlib fn() | predictor2 fn() |
|-------------|-----------|-----------------|
| 64 B        | 0.002s    | 0.000s          |
| 64 KB       | 0.033s    | 0.070s          |
| 1 MB        | 0.574s    | 1.270s          |
| 2 MB (COG)  | 2.032s    | 2.313s          |

Recompiling `tiff-predictor-2-c` with `-Doptimize=ReleaseFast` (vs the default
`Debug`) made no meaningful difference, confirming the codec algorithm is not
the bottleneck.

The root cause is the **Component Model canonical ABI** as implemented by
wasmtime's Python bindings. When `fn(store, port_map)` is called with a Python
`list[tuple[str, bytes]]`, the canonical ABI lifting/lowering for `list<u8>`
runs at ~1.7 MB/s rather than at C-level memcpy speed (~10 GB/s). Python's
wasmtime binding marshals the data through Python object allocation rather than
a bulk copy.

Critically, **the Component Model hides its linear memory from the host**:

```python
mem_idx = instance.get_export_index(store, "memory")
# → None. Memory is not accessible from a component instance.
```

This means there is no way to bypass the canonical ABI and use `Memory.write()`
from a component instance in Python.

## Architectural Implications

Every step boundary in the current Python executor bounces data through Python:

```
WASM step N linear memory
  → lift to Python bytes  (copy 1, ~1.7 MB/s in Python)
WASM step N+1 linear memory
  ← lower from Python bytes  (copy 2, ~1.7 MB/s in Python)
```

This is **2 copies per step edge**, not 1. The README claim that the Component
Model "reduces it to one copy" and "never touches Python's heap" is incorrect
for the current design. Both copies pass through Python's heap.

In a **native host (Rust/Go)**, the same 2 copies occur but at C memcpy speed
(~10 GB/s): ~0.4 ms total for a 2-step COG pipeline vs ~8 s in Python. The
Python binding is the amplifier, not the architecture itself.

To reduce to **2 copies for the entire pipeline** (one in, one out) regardless
of step count, the pipeline would need to be composed into a single WASM
component. With `wasm-tools compose`, intra-component calls stay within one
linear memory. However, composition requires a static pipeline known at build
time and is unsuitable for the dynamic DAG model used here.

## History

The pre-Component-Model `wasm_runner.py` used the **core module** (not the
component) and accessed WASM memory directly:

```python
input_ptr = alloc_fn(store, input_len)
memory.write(store, data, input_ptr)    # ctypes — fast
result = codec_fn(store, input_ptr, input_len, config_ptr, config_len)
output = memory.read(store, out_ptr, out_ptr + out_len)  # ctypes — fast
```

Core modules export `memory` as a plain wasmtime `Memory` object, so
`Memory.write()` and `Memory.read()` — which are single C-level memcpy calls —
are available. This was fast. The migration to the Component Model removed this
path.

## Conclusion

The root cause is entirely in the Python binding layer, not in the Component
Model architecture. In a native host (Rust), the same canonical ABI lowering
and lifting for `list<u8>` runs at C memcpy speed (~10 GB/s). The 2 copies per
step edge that currently take ~1.2 s each in Python would take under 1 ms total
for a 2-step COG pipeline.

The correct fix is a native (Rust) orchestrator. No WIT changes are required.
All existing codecs, including those authored in Python via `componentize-py`,
continue to work through the same Component Model interface. The Python executor
should be treated as a development and test host, not a production runtime.

See [COPY_ELIMINATION.md](COPY_ELIMINATION.md) for analysis of all approaches
considered, including the dual core-module path (rejected), the Wasm blob store
(viable stop-gap for the Python orchestrator, but with significant codec-churn
trade-offs), and why copy elimination within the current Component Model spec is
not achievable.
