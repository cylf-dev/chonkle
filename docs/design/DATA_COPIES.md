# Data Copy Accounting

This document covers the number of data copies at each inter-step edge in a chonkle pipeline, broken down by source and destination codec backend. For canonical ABI throughput measurements, see [CANONICAL_ABI_PERF.md](CANONICAL_ABI_PERF.md).

## Codec Backends

chonkle has three codec backends:

- **Core Wasm** (`CoreWasmCodec`) — core wasm32-wasi reactor modules. The host accesses linear memory directly via `Memory.data_ptr()` and `Memory.read()`/`Memory.write()`. Outputs are returned as `CoreWasmRef` (a deferred reference to data in the module's linear memory).
- **Component Model** (`ComponentCodec`) — Component Model components. Linear memory is hidden from the host; all data crosses the boundary through the canonical ABI.
- **Native** (`NativeCodec`) — numcodecs Python codecs. Data lives on the Python heap as `bytes` objects.

## Fundamental Constraints

Each backend holds data in a different memory space. Wasm modules (core and component) each have their own isolated linear memory. Native codecs hold Python `bytes` on the Python heap. No backend can access another's memory directly, so moving data between steps requires at least one copy — except native-to-native, where Python passes the `bytes` object by reference.

The cost of a copy depends on the mechanism. Core module transfers use `Memory.read()`/`Memory.write()` and `ctypes.memmove`, which are single C-level calls running at ~10 GB/s. Component Model transfers go through the canonical ABI, which allocates Python objects at ~1.7 MB/s in the Python host. In a native (Rust/Go) orchestrator, the canonical ABI bottleneck disappears and all Wasm copies run at memcpy speed.

## Inter-Step Copy Counts

The table below shows the number of copies per inter-step edge in the Python executor. These are inter-step copies only, not per-invocation totals.

| Source / Dest | Core Wasm | Component Model | Native |
| --- | --- | --- | --- |
| Core Wasm | 1 | 2 | 1 |
| Component Model | 2 | 2 | 1 |
| Native | 1 | 1 | 0 |

### Edge-by-edge breakdown

**Core Wasm to Core Wasm (1 copy):** The source codec returns a `CoreWasmRef` pointing into its linear memory. The port-map builder detects a core-to-core edge, allocates in the destination module via `alloc()`, obtains raw pointers to both memories via `Memory.data_ptr()`, and performs `ctypes.memmove`. The source pointer is obtained before destination allocation so the source memory is not invalidated. One copy at ~10 GB/s.

**Core Wasm to Component Model (2 copies):** The `CoreWasmRef` is materialized to Python `bytes` via `Memory.read()` (~10 GB/s). The bytes are then lowered into the component's linear memory through the canonical ABI (~1.7 MB/s). Two copies; the second is the bottleneck.

**Core Wasm to Native (1 copy):** The `CoreWasmRef` is materialized to Python `bytes` via `Memory.read()` (~10 GB/s). The native codec reads from the `bytes` object directly. One copy.

**Component Model to Core Wasm (2 copies):** The component's output is lifted to Python `bytes` through the canonical ABI (~1.7 MB/s). The bytes are then written into the core module's linear memory via `Memory.write()` (~10 GB/s). Two copies; the first is the bottleneck.

**Component Model to Component Model (2 copies):** Output is lifted to Python `bytes` via the canonical ABI, then lowered into the next component's memory via the canonical ABI. Two copies, both at ~1.7 MB/s.

**Component Model to Native (1 copy):** Output is lifted to Python `bytes` via the canonical ABI (~1.7 MB/s). The native codec reads from the `bytes` object directly. One copy.

**Native to Core Wasm (1 copy):** The native codec returns Python `bytes`. The executor writes these into the core module's linear memory via `Memory.write()` (~10 GB/s). One copy.

**Native to Component Model (1 copy):** The native codec returns Python `bytes`. These are lowered into the component's memory through the canonical ABI (~1.7 MB/s). One copy.

**Native to Native (0 copies):** Python passes the `bytes` object by reference. No copy.

### Cost summary

Copies involving the canonical ABI are the bottleneck. At ~1.7 MB/s, a single canonical ABI copy of 2 MB takes ~1.2 s. All other copies (materialize, `Memory.read`/`Memory.write`, `ctypes.memmove`) run at ~10 GB/s and are negligible in comparison.

## Copy Reduction

### Single-copy transfer (implemented)

When both source and destination are core wasm modules, the executor uses `_single_copy_transfer`: allocate in the destination module, get raw pointers to both memories, `ctypes.memmove`. This avoids materializing to Python `bytes` and achieves one copy at ~10 GB/s instead of two copies through `Memory.read()` then `Memory.write()`.

### Shared memory between core modules

Each core wasm module has its own isolated linear memory. An earlier design used a single shared `wasmtime.Memory` for all modules, enabling zero-copy transfers. This was abandoned because every module's data segments target the same low addresses — when `linker.instantiate()` writes a module's data segments, it overwrites whatever a previous module left there. This prevents multiple module instances from coexisting simultaneously, which is required for single-copy transfer and eventual parallel execution of independent DAG branches.

The Wasm multi-memory proposal (part of Wasm 3.0) could in theory solve this: each module keeps a private memory for data segments and heap, and all modules share a second memory for the data plane. In practice, C and Rust compilers (LLVM) can only emit load/store instructions targeting a module's default memory — there is no way to compile code that reads from or writes to a second memory. Library-wrapping codecs (zlib, zstd, etc.) would need to copy data between shared and private memory on every call, which is worse than the current design. See [../wasm/MULTI_MEMORY.md](../wasm/MULTI_MEMORY.md) for the full toolchain analysis.

### Pipeline composition

`wasm-tools compose` can merge multiple Component Model components into a single component. Intra-component calls share linear memory, so inter-step copies disappear. This requires a static pipeline known at build time, which is incompatible with the dynamic DAG model. Even with a static DAG, composition links function calls rather than data flow: in a fan-out, each downstream component independently calls the upstream component, causing it to run once per consumer instead of once for the graph.

### Component Model resources

Component Model resources are handle-based types: a component holds an integer handle to an object owned by another component, and the underlying data does not move when the handle is passed. See [../wasm/WIT_RESOURCES.md](../wasm/WIT_RESOURCES.md) for the general concepts.

There are two patterns for sharing a resource across component boundaries. Pattern 1 (direct composition) requires B's WIT to name A's package at compile time — `use a:comp/blobs@0.1.0.{blob}`. This achieves 1 copy per edge but requires static coupling. chonkle pipelines are assembled at runtime from JSON; no codec knows its neighbors when it is compiled, so Pattern 1 is incompatible with the dynamic DAG model.

Pattern 2 (shared buffer-store component) lets both A and B import from a shared third component. The orchestrator only exchanges handles, but data still crosses two component boundaries: A copies into the buffer-store via `blob.constructor()`, and B copies out via `blob.as-bytes()`. This gives 2 copies per edge — the same as `list<u8>` value passing.

Additionally, wasmtime-py 41 does not support Component Model resource types. The Python API does not expose handles, resource tables, or destructors. wasmtime-rs supports resources fully, so they become available as a design option when the orchestrator moves to Rust — but the analysis above shows they offer no copy-count advantage for an orchestrated pipeline with dynamically assembled steps.

### Caller-supplied buffers (not yet available)

The [WASI roadmap](https://wasi.dev/roadmap) includes caller-supplied buffers, which would allow a host to pass a pre-allocated buffer for the module to write into, eliminating the copy-out step. This is not yet part of the spec.

### Native Python extension for canonical ABI

A Rust extension (PyO3 + wasmtime-rs) could replace the Python canonical ABI path with typed bindings that use `memory.write()` per buffer instead of per-byte Python iteration. This would bring Component Model copies to memcpy speed (~10 GB/s), making all Wasm-involving edges equivalent in cost to core module edges. This does not reduce the number of copies — Component Model edges would still be 2 copies — but it eliminates the Python overhead that makes those copies slow. See [CANONICAL_ABI_PERF.md](CANONICAL_ABI_PERF.md) for details.
