# Multi-Memory

The multi-memory feature (part of WASM 3.0, enabled by default in wasmtime) allows a module to declare and access more than one linear memory. Each memory is addressed by index in load/store instructions.

A natural use case is separating private module state from a shared data plane:

| Memory | Role | Contents |
|--------|------|----------|
| memory 0 | **private** (each module defines its own) | data segments, shadow stack, malloc heap |
| memory 1 | **shared** (imported from host, same object for all modules) | data plane -- inputs, outputs, config |

The WASM spec guarantees that imports receive indices before locally defined entities, so a module that imports a memory and also defines its own will always have the import as memory 0 and the local definition as memory 1 (or vice versa, depending on declaration order).

## Multi-memory works in hand-written WAT

Wasmtime's [multi-memory example](https://docs.wasmtime.dev/examples-multimemory.html) demonstrates this feature using hand-written WAT. The memory index is a literal in each load/store instruction:

```wat
(func (export "load0") (param i32) (result i32)
  (i32.load (memory 0) (local.get 0)))

(func (export "load1") (param i32) (result i32)
  (i32.load (memory 1) (local.get 0)))
```

A hand-written codec that explicitly targets memory 1 for data plane access can coexist with other modules -- each has its own memory 0, and all share memory 1.

## The toolchain gap: C and Rust cannot generate multi-memory code

LLVM's WASM backend cannot emit load/store instructions targeting a non-zero memory index from C or Rust source code. When clang compiles `*ptr = value`, it emits `i32.store` targeting memory 0 unconditionally. There is no source-level annotation that changes this.

Specifically:

- **`__attribute__((address_space(N)))` is silently ignored** by the WASM backend. All load/store instructions target memory 0 regardless of the annotation. This was confirmed in [WebAssembly/multi-memory#45](https://github.com/WebAssembly/multi-memory/issues/45).
- **No one is actively working on this in LLVM.** A WebAssembly contributor confirmed this in the same issue.
- **Rust has the same limitation.** It compiles through LLVM and inherits the constraint.
- **`--import-memory` in wasm-ld** controls whether memory 0 is imported or defined. It has no concept of a second memory.
- **`--global-base`** controls the offset within a single memory, not which memory index to target.

The runtime (wasmtime) fully supports multi-memory. The spec fully defines it. The gap is in code generation: no mainstream compiler can produce WASM that uses multiple memories from C or Rust source.

## Consequence for library-wrapping modules

A module that wraps an existing C library (zlib, zstd, lz4, etc.) compiles that library to WASM. The library's pointer dereferences all target memory 0. If a shared data plane is memory 1, the library cannot read from or write to it.

The module would need to:

1. Copy input from shared memory (memory 1) to a local buffer (memory 0)
2. Call the library (operates on memory 0 pointers)
3. Copy output from local buffer (memory 0) to shared memory (memory 1)

This adds intra-step copies that would not exist if the library operated directly on its only memory.

## The ideal arrangement and why it is not achievable

The ideal layout would make the shared data plane memory 0 (so normal pointer dereferences access it) and put private module state in memory 1. Third-party library code would work on shared data without modification.

This requires the toolchain to redirect data segments, shadow stack, and heap to memory 1. Standard LLVM cannot do this. There is no `--data-memory-index` flag in wasm-ld. Data segments always target memory 0. The shadow stack pointer (`__stack_pointer`) always addresses memory 0. Allocators (`malloc`, `memory.grow`) always operate on memory 0.

Achieving this would require custom toolchain work or binary post-processing of every compiled module -- not practical for third-party library code.

## `--global-base` coordination

An alternative to multi-memory: each module is compiled with a unique `--global-base` value so their data segments occupy non-overlapping regions of a single shared memory. This avoids multi-memory entirely and preserves zero-copy.

Downsides:

- Requires compile-time coordination -- you must know what other modules will share the space and assign non-overlapping regions
- Fragile if a module's heap grows beyond its reserved region
- Adding a new module to the pipeline could require recompiling existing ones

This is the most practical path to simultaneous instantiation on a single shared memory, but it sacrifices the property that modules are independently compiled and composed at runtime.
