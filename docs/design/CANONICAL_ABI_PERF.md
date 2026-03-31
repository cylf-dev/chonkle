# Component Model Canonical ABI Performance Investigation

## Background

Integration tests that run the full executor against real compiled Component Model
codecs take 4–9 seconds each. To find where the time is going, `time.perf_counter()`
splits were added around each operation in `ComponentCodec.call()`:

```
from_file   store+linker   instantiate   fn      post_return   normalize
0.003s      0.000s         0.000s        2.239s  0.000s        0.000s
```

Everything except `fn(store, port_map)` is essentially free once wasmtime's
compiled-code disk cache is warm.

## ABI Traffic Is the Bottleneck

`fn()` time scales linearly with **total bytes through the ABI** (input + output)
at approximately **1.4–1.9 MB/s**:

| Codec      | in       | out    | abi total | fn()   | throughput |
|------------|----------|--------|-----------|--------|------------|
| zlib       | 4 KB     | 1 MB   | 1.00 MB   | 0.529s | 1.90 MB/s  |
| identity   | 1 MB     | 1 MB   | 2.00 MB   | 1.320s | 1.52 MB/s  |
| predictor2 | 1 MB     | 1 MB   | 2.00 MB   | 1.295s | 1.54 MB/s  |
| zlib (COG) | 1.4 MB   | 2 MB   | 3.38 MB   | 2.053s | 1.65 MB/s  |
| identity   | 2 MB     | 2 MB   | 4.00 MB   | 2.771s | 1.44 MB/s  |
| predictor2 | 2 MB     | 2 MB   | 4.00 MB   | 2.507s | 1.60 MB/s  |

## Codec Computation Is Negligible

A minimal identity codec (`codec/identity-c/`) performs only a `malloc` +
`memcpy` inside WASM with no other computation. At equal ABI traffic, its
`fn()` times are nearly identical to predictor2:

| Codec      | abi total | fn()   | throughput |
|------------|-----------|--------|------------|
| identity   | 2.00 MB   | 1.320s | 1.52 MB/s  |
| predictor2 | 2.00 MB   | 1.295s | 1.54 MB/s  |
| identity   | 4.00 MB   | 2.771s | 1.44 MB/s  |
| predictor2 | 4.00 MB   | 2.507s | 1.60 MB/s  |

The `fn()` cost is ABI lifting/lowering, not codec computation.

## Lowering vs Lifting

To determine whether input (lowering, Python→WASM) or output (lifting,
WASM→Python) dominates, two zlib calls were constructed with the same total
ABI traffic (~2 MB) but opposite distributions:

| Case              | in      | out     | abi total | fn()   | throughput |
|-------------------|---------|---------|-----------|--------|------------|
| encode (lowering) | 2.00 MB | 2 KB    | 2.00 MB   | 1.197s | 1.67 MB/s  |
| decode (lifting)  | 2 KB    | 2.00 MB | 2.00 MB   | 1.024s | 1.96 MB/s  |

The direction of data flow does not materially affect throughput.

## Python Binding Is the Root Cause

To determine whether the 1.4–1.9 MB/s rate is inherent to the canonical ABI or
specific to the Python binding layer, two minimal CLIs call the same identity
codec with the same 2 MB buffer:

- `bench/rust-host/` — Rust binary using `wasmtime-rs 41` typed bindings
- `bench/python-host/time_abi_raw.py` — Python script using `wasmtime-py 41`
  raw function call, bypassing the chonkle executor entirely

Both warm up the compilation cache before timing, and run 3 iterations at 2 MB.

| Host                    | Size | abi total | fn() run 1 | fn() run 2 | fn() run 3 | throughput |
|-------------------------|------|-----------|------------|------------|------------|------------|
| Python (wasmtime-py 41) | 2 MB | 4 MB      | 2.287s     | 2.284s     | 2.349s     | 1.7 MB/s   |
| Rust (wasmtime-rs 41)   | 2 MB | 4 MB      | 0.001s     | 0.001s     | 0.000s     | 9,113 MB/s |

Rust throughput is ~5,000× faster at the same WASM binary and buffer size.
The bottleneck is the Python binding layer, not the canonical ABI itself.

Critically, **the Component Model hides its linear memory from the host**:

```python
mem_idx = instance.get_export_index(store, "memory")
# → None. Memory is not accessible from a component instance.
```

There is no way to bypass the canonical ABI and use `Memory.write()` from a
component instance in Python.

## Architectural Implications

Every step in the current Python executor requires two passes through Python:

```
Python bytes ── step N input
  ↓ lower  (copy 1, ~1.7 MB/s)
WASM linear memory ── step N runs
  ↓ lift   (copy 2, ~1.7 MB/s)
Python bytes ── step N output
```

Each copy is charged at the rate for its own data size (input of step N for
copy 1, output of step N for copy 2). For a 2-step Component Model codec pipeline with 2 MB tiles,
total ABI traffic is ~7.5 MB across 4 copies, taking ~4–5 s.

The result is **2 copies per step edge**. Both copies pass through Python's
heap. In a native host (Rust/Go), the same 2 copies occur but at C memcpy
speed — the Python binding is the amplifier, not the architecture itself.

For the full edge-type accounting, see [DATA_COPIES.md](DATA_COPIES.md).

## Mitigation for the Python Orchestrator

The correct long-term fix is a native orchestrator. While the Python executor remains in
use, there is one potential approach to reduce the impact of the Python-speed
bottleneck.

### Native Python extension for canonical ABI

#### The exact mechanism causing 1.7 MB/s

wasmtime-py's canonical ABI type adaptation runs in Python. The source
(`wasmtime/_component/_types.py`, `ListType.convert_to_c()`) iterates every
element individually:

```python
for e in val:                                         # O(N) Python loop
    element.convert_to_c(store, e, pointer(raw.data[i]))

# U8.convert_to_c() — called once per byte:
if not isinstance(val, int):                          # isinstance per byte
    raise TypeError(...)
ptr.contents.kind = ffi.WASMTIME_COMPONENT_U8
ptr.contents.of.u8 = val                              # ctypes field set per byte
```

A 2 MB buffer triggers ~2M Python loop iterations, ~2M `isinstance()` checks,
and ~2M ctypes field writes per copy direction. There is no bulk memcpy path.
The C FFI layer (`wasmtime_component_func_call`) receives a fully pre-marshalled
`wasmtime_component_val_t` array built entirely by Python.

wasmtime-rs's `Lower` trait for `list<u8>`, by contrast, writes the byte slice
into Wasm linear memory via a single `memory.write()` — one memcpy, no
per-element dispatch. The bench results demonstrate the gap: 1.7 MB/s (Python)
vs 9,113 MB/s (Rust) at the same Wasm binary and buffer size.

#### What the extension does

A Rust crate (`chonkle-wasmtime`) using [PyO3](https://pyo3.rs/) and
[maturin](https://github.com/PyO3/maturin) wraps wasmtime-rs's component model
APIs directly. Since `codec/wit/codec.wit` is fixed, `wasmtime::component::bindgen!`
generates fully typed Rust bindings at compile time:

```rust
wasmtime::component::bindgen!({ path: "codec/wit/codec.wit", world: "codec" });
```

The generated `Lower`/`Lift` impls handle `port-map` with one `memory.write()`
per buffer, not per byte. PyO3 exposes `Engine`, `Component`, and `Store` as
`#[pyclass]` types. The Python→Rust boundary cost for `bytes` → `Vec<u8>` is a
single C-level memcpy (~44 ns total PyO3 call overhead), not an O(N) Python
operation.

`ComponentCodec.call()` replaces its wasmtime-py function call with the extension.
No other executor logic changes. No codec changes.

#### Expected outcome

All 2N copies that currently run at ~1.7 MB/s run at memcpy speed (~10 GB/s).
For a 2-step Component Model codec pipeline at 2 MB tiles, per-step call time drops
from ~2.5 s to the range shown in `bench/rust-host/` (~1 ms). The 4–9 s test times become
sub-10 ms.

#### Engineering cost

- Build infrastructure: maturin required; the chonkle package gains a Rust
  compilation step and per-platform wheels (macOS arm64/x86_64, Linux
  aarch64/x86_64).
- wasmtime-py and the extension cannot share internal state; the extension owns
  its own `Engine`/`Store`/`Linker` lifecycle, replacing wasmtime-py for
  component calls.

#### Upstream path

wasmtime-py maintainers intend to eventually move canonical ABI adaptation into
the C API layer, which would fix this without a standalone extension
([wasmtime-py #309](https://github.com/bytecodealliance/wasmtime-py/issues/309)).
As of wasmtime-py 41 that has not happened; no timeline is set.

#### Migration

When the orchestrator moves to Rust, the extension is superseded: the same
wasmtime-rs APIs it uses become direct calls in the orchestrator binary. No
codec changes at any point.

## Conclusion

The root cause is entirely in the Python binding layer, not in the Component
Model architecture. The correct fix is a native (Rust) orchestrator. No WIT
changes are required. All existing codecs, including those authored in Python via
`componentize-py`, continue to work through the same Component Model interface.
The Python executor should be treated as a development and test host, not a
production runtime.

See [DATA_COPIES.md](DATA_COPIES.md) for the copy-count accounting and why copy
elimination within the current Component Model spec is not achievable at this time.
