# Multi-Memory and Zero-Copy Pipelines

This document summarizes the feasibility of using the WASM multi-memory feature to allow multiple codec modules to operate simultaneously on a shared data plane without copying.

## The current design

Each core wasm codec module has its own linear memory (created at instantiation via `CoreWasmCodec`). The host serializes input port-maps into the destination module's memory via `Memory.write()`, and reads output port-maps after the codec call.

Between sequential core wasm steps, data transfers use single-copy: the host allocates in the destination module via `alloc()`, obtains raw pointers to both memories via `Memory.data_ptr()`, and performs `ctypes.memmove` — one copy at native speed (~10 GB/s). Bulk data stays in linear memory as `CoreWasmRef` deferred references until consumed.

This design replaced an earlier shared-memory approach where all modules operated on a single `wasmtime.Memory`. The separate-memory design enables module instances to coexist (required for single-copy transfer) and avoids the data segment collision problem described below.

## The data segment collision problem

Every module's data segments target the same low addresses in the shared memory. When `linker.instantiate()` is called, it unconditionally writes the module's data segments into memory, overwriting whatever a previous module left there. This is harmless for sequential execution (the current design), but prevents:

- Multiple module instances existing simultaneously
- Parallel execution of independent DAG branches
- Any architecture where a module needs persistent internal state across calls

## Multi-memory: how it works at the spec level

The WASM multi-memory feature (part of WASM 3.0, enabled by default in wasmtime) allows a module to declare and access more than one linear memory. Each memory is addressed by index in load/store instructions. The natural design for chonkle:

| Memory | Role | Contents |
|--------|------|----------|
| memory 0 | **private** (each module defines its own) | data segments, shadow stack, malloc heap |
| memory 1 | **shared** (imported from host, same object for all modules) | data plane — inputs, outputs, config |

The WASM spec guarantees that imports receive indices before locally defined entities, so a module that imports a memory and also defines its own will always have the import as memory 0 and the local definition as memory 1 (or vice versa, depending on declaration order).

## Multi-memory works in hand-written WAT

Wasmtime's [multi-memory example](https://docs.wasmtime.dev/examples-multimemory.html) demonstrates this feature using hand-written WAT. The memory index is a literal in each load/store instruction:

```wat
(func (export "load0") (param i32) (result i32)
  (i32.load (memory 0) (local.get 0)))

(func (export "load1") (param i32) (result i32)
  (i32.load (memory 1) (local.get 0)))
```

This works. A hand-written codec that explicitly targets memory 1 for data plane access can coexist with other modules — each has its own memory 0, and all share memory 1.

## The toolchain gap: C and Rust cannot generate multi-memory code

LLVM's WASM backend cannot emit load/store instructions targeting a non-zero memory index from C or Rust source code. When clang compiles `*ptr = value`, it emits `i32.store` targeting memory 0 unconditionally. There is no source-level annotation that changes this.

Specifically:

- **`__attribute__((address_space(N)))` is silently ignored** by the WASM backend. All load/store instructions target memory 0 regardless of the annotation. This was confirmed in [WebAssembly/multi-memory#45](https://github.com/WebAssembly/multi-memory/issues/45).
- **No one is actively working on this in LLVM.** A WebAssembly contributor confirmed this in the same issue.
- **Rust has the same limitation.** It compiles through LLVM and inherits the constraint.
- **`--import-memory` in wasm-ld** controls whether memory 0 is imported or defined. It has no concept of a second memory.
- **`--global-base`** controls the offset within a single memory, not which memory index to target.

The runtime (wasmtime) fully supports multi-memory. The spec fully defines it. The gap is in code generation: no mainstream compiler can produce WASM that uses multiple memories from C or Rust source.

## Consequence: library-wrapping codecs cannot use multi-memory transparently

A codec that wraps an existing C library (zlib, zstd, lz4, etc.) compiles that library to WASM. The library's pointer dereferences all target memory 0. If the shared data plane is memory 1, the library cannot read from or write to it.

The codec would need to:

1. Copy input from shared memory (memory 1) → local buffer (memory 0)
2. Call the library (operates on memory 0 pointers)
3. Copy output from local buffer (memory 0) → shared memory (memory 1)

This is worse than the current design, which has zero intra-step copies because the library operates directly on the only memory.

## The ideal arrangement and why it is not achievable

The ideal layout would make the shared data plane memory 0 (so normal pointer dereferences access it) and put private module state in memory 1. Third-party library code would work on shared data without modification.

This requires the toolchain to redirect data segments, shadow stack, and heap to memory 1. Standard LLVM cannot do this. There is no `--data-memory-index` flag in wasm-ld. Data segments always target memory 0. The shadow stack pointer (`__stack_pointer`) always addresses memory 0. Allocators (`malloc`, `memory.grow`) always operate on memory 0.

Achieving this would require custom toolchain work or binary post-processing of every compiled module — not practical for third-party library code.

## Alternative: `--global-base` coordination

Each codec could be compiled with a unique `--global-base` value so their data segments occupy non-overlapping regions of the single shared memory. This avoids multi-memory entirely and preserves zero-copy.

Downsides:

- Requires compile-time coordination — you must know what other modules will share the space and assign non-overlapping regions
- Fragile if a codec's heap grows beyond its reserved region
- Adding a new codec to the pipeline could require recompiling existing ones

This is the most practical path to simultaneous instantiation if it becomes necessary, but it sacrifices the property that codecs are independently compiled and composed at runtime.

## Where the current design stands

The current separate-memory design with single-copy transfer is a practical middle ground:

- 1 copy between core wasm steps (ctypes.memmove at ~10 GB/s)
- 0 copies within a step (the library reads/writes its own memory 0 directly)
- No toolchain constraints (standard `wasm32-wasip1` compilation works)
- Module instances can coexist (each has its own linear memory)
- No data segment collision (each module writes to its own memory)

Multi-memory would only become worth the complexity if zero-copy between steps is needed and the intra-step copy penalty (for library-wrapping codecs) or compile-time `--global-base` coordination is acceptable. The current single-copy approach already achieves ~10 GB/s throughput per edge, which is orders of magnitude faster than the canonical ABI path.

## Comparison across approaches

Each approach has different copy characteristics for a pipeline of N codec steps:

| Approach | Copies between steps | Copy speed |
|----------|----------------------|------------|
| Core modules, separate memories (current chonkle) | 1 per edge (~10 GB/s) | ctypes.memmove |
| Core modules, shared memory (former chonkle design) | 0 | n/a |
| Component Model, sync `list<u8>` (WASI 0.2) | 2 per edge (~1.7 MB/s) | canonical ABI |
| Component Model, `stream<u8>` (WASI 0.3) | 2 per edge per chunk | canonical ABI |

Features that could reduce Component Model copy overhead (caller-supplied buffers, copy-on-write blob resources) are post-0.3.0 or post-MVP with no committed timeline. See [COMPONENT_MODEL.md](COMPONENT_MODEL.md) for more on the Component Model.
