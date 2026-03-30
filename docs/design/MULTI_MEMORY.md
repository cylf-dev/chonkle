# Multi-Memory and Zero-Copy Pipelines

This document analyzes the feasibility of using the WASM multi-memory feature to allow multiple codec modules in a chonkle pipeline to operate simultaneously on a shared data plane without copying. For general background on multi-memory, see [wasm/MULTI_MEMORY.md](../wasm/MULTI_MEMORY.md).

## The current design

Each core wasm codec module has its own linear memory (created at instantiation via `CoreWasmCodec`). The host serializes input port-maps into the destination module's memory via `Memory.write()`, and reads output port-maps after the codec call.

Between sequential core wasm steps, data transfers use single-copy: the host allocates in the destination module via `alloc()`, obtains raw pointers to both memories via `Memory.data_ptr()`, and performs `ctypes.memmove` — one copy at native speed (~10 GB/s). Bulk data stays in linear memory as `CoreWasmRef` deferred references until consumed.

This design replaced an earlier shared-memory approach where all modules operated on a single `wasmtime.Memory`. The separate-memory design enables module instances to coexist (required for single-copy transfer) and avoids the data segment collision problem described below.

## The data segment collision problem

Every module's data segments target the same low addresses in the shared memory. When `linker.instantiate()` is called, it unconditionally writes the module's data segments into memory, overwriting whatever a previous module left there. This is harmless for sequential execution (the current design), but prevents:

- Multiple module instances existing simultaneously
- Parallel execution of independent DAG branches
- Any architecture where a module needs persistent internal state across calls

## Why multi-memory does not help

Library-wrapping codecs (zlib, zstd, lz4, etc.) compile C code to WASM. All pointer dereferences target memory 0 — the LLVM toolchain cannot emit multi-memory instructions from C or Rust source. If the shared data plane is memory 1, the library cannot read from or write to it. Each codec would need to copy input from shared memory to local memory and back, which is worse than the current design where the library operates directly on its only memory.

See [wasm/MULTI_MEMORY.md](../wasm/MULTI_MEMORY.md) for the full toolchain analysis.

## Where the current design stands

The current separate-memory design with single-copy transfer is a practical middle ground:

- 1 copy between core wasm steps (ctypes.memmove at ~10 GB/s)
- 0 copies within a step (the library reads/writes its own memory 0 directly)
- No toolchain constraints (standard `wasm32-wasip1` compilation works)
- Module instances can coexist (each has its own linear memory)
- No data segment collision (each module writes to its own memory)

Multi-memory would only become worth the complexity if zero-copy between steps is needed and the intra-step copy penalty (for library-wrapping codecs) or compile-time `--global-base` coordination is acceptable. The current single-copy approach already achieves ~10 GB/s throughput per edge, which is orders of magnitude faster than the canonical ABI path.

## Comparison across approaches

| Approach | Copies between steps | Copy speed |
|----------|----------------------|------------|
| Core modules, separate memories (current chonkle) | 1 per edge (~10 GB/s) | ctypes.memmove |
| Core modules, shared memory (former chonkle design) | 0 | n/a |
| Component Model, sync `list<u8>` (WASI 0.2) | 2 per edge (~1.7 MB/s) | canonical ABI |
| Component Model, `stream<u8>` (WASI 0.3) | 2 per edge per chunk | canonical ABI |

Features that could reduce Component Model copy overhead (caller-supplied buffers, copy-on-write blob resources) are post-0.3.0 or post-MVP with no committed timeline. See [wasm/COMPONENT_MODEL.md](../wasm/COMPONENT_MODEL.md) for more on the Component Model.
