# Data Copy Accounting for the Wasm Codec Path

This document covers why data copies are unavoidable in the Wasm codec path,
how many occur per call, and what approaches have been explored to eliminate
them. For performance measurements and mitigations that apply while the Python
orchestrator remains in use, see [CANONICAL_ABI_PERF.md](CANONICAL_ABI_PERF.md).

## Fundamental Constraint

Wasm's sandbox model means a module can only read and write its own linear memory — it
cannot dereference a host pointer or access the Python heap. The host can read and write
the module's linear memory, but not the reverse. This is why copies are currently
unavoidable: data must be copied in before processing and copied out after.

## Per-Call Copy Anatomy

Each Wasm codec invocation requires two copies regardless of host language:

```text
     host bytes
         │
      copy in            (copy 1: lower — host → Wasm linear memory)
         │
         ▼
Wasm linear memory
         │
    codec runs
         │
         ▼
Wasm linear memory
         │
      copy out           (copy 2: lift — Wasm linear memory → host)
         │
         ▼
     host bytes
```

In a native host (Rust/Go) both copies run at C memcpy speed (~10 GB/s)
regardless of whether the codec is a Core module or a Component Model component,
making them very small — under 1 ms total for a 2-step pipeline with 2 MB
chunks. In Python with a Core module, `Memory.write()` and `Memory.read()` are
also single C-level memcpy calls and are similarly fast. In Python with a
Component Model component (which is what all codecs are in chonkle v2), the canonical
ABI lifting and lowering goes through Python object allocation at approximately
1.7 MB/s, making each copy approximately 1.2 s for a 2 MB chunk. See
[CANONICAL_ABI_PERF.md](CANONICAL_ABI_PERF.md) for throughput
measurements.

## Copy Counts by Edge Type

The table below shows per-inter-step-edge copy counts for different combinations
of adjacent codec types, using the Python executor.

| Edge type | Copies per inter-step edge |
| --- | --- |
| numcodecs → numcodecs | 0 (Python passes object reference; codec B reads directly from codec A's output buffer) |
| numcodecs → Wasm | 1 (Wasmtime writes Python bytes into Wasm linear memory) |
| Wasm → numcodecs | 1 (Python reads Wasm linear memory into a Python `bytes` object) |
| Wasm → Wasm (Core module, Python executor) | 2 (read from linear memory into Python bytes via `Memory.read()` at ctypes speed; write Python bytes via `Memory.write()` at ctypes speed) |
| Wasm → Wasm (Component Model, Python executor) | 2 (lift to Python bytes + lower from Python bytes; both copies through canonical ABI at ~1.7 MB/s) |

Both Wasm→Wasm rows have the same copy count (2). The difference is cost: Core
module copies use `Memory.read()`/`Memory.write()`, which are single C-level
calls; Component Model copies go through the canonical ABI's Python object
allocation path at ~1.7 MB/s. In a native (Rust/Go) orchestrator both rows
have 2 copies at C memcpy speed — the cost difference between Core modules and
Component Model disappears.

The numcodecs rows describe the original chonkle architecture, which mixed numcodecs
Python codecs with both Core and Component Model Wasm codecs in a linear pipeline.
Chonkle v2 uses Component Model exclusively and drops numcodecs, so only the Component
Model row applies today. Compared to all-numcodecs pipelines, the Wasm path incurs a
copy overhead at each step boundary.

## Copy Reduction

The two copies per codec invocation are a property of Wasm's sandbox model.
Several approaches have been explored.

### Shared memory (threads proposal)

The Wasm threads proposal adds `SharedMemory`: a linear memory instance that
both the host and one or more Wasm instances can access simultaneously, without
each having their own private copy. The appeal is that if host and module share
the same memory backing, data written on one side is immediately visible on the
other — no explicit transfer required.

This does not apply to Component Model components, which do not support
`SharedMemory`. WIT interfaces are defined in terms of owned value types
(`list<u8>`, etc.); there is no mechanism to pass a reference into a shared
region across the component boundary.

For core modules it is technically possible, but does not eliminate copies.
Data still needs to travel from its origin — the previous step's output buffer,
a file read, a network receive — into the shared region. That transfer is itself
a copy. `SharedMemory` moves where the copy happens, not whether it happens.

### Custom memory backing

Wasmtime's Rust API exposes a `MemoryCreator` trait that allows customizing how
linear memory is allocated. This controls where memory lives, not how many times
data crosses the host-guest boundary. The canonical ABI still requires a copy in
and a copy out regardless of the backing allocator.

### Pipeline composition

`wasm-tools compose` can merge multiple components into one. Intra-component
calls share a single linear memory, so the N-1 inter-step copies disappear —
the composed pipeline pays only 2 copies total regardless of step count. This
works today but requires a static pipeline known at build time, which is
incompatible with the dynamic DAG model used here.

### Caller-supplied buffers (not yet available)

The [WASI roadmap](https://wasi.dev/roadmap) includes caller-supplied buffers,
which would allow a host to pass a pre-allocated buffer for the module to write
into, eliminating the copy-out step. This is not yet part of the spec.
