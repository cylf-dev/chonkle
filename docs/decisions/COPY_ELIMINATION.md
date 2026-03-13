# Copy Elimination and Copy-Cost Reduction in the Wasm Codec Path

This document records approaches explored for eliminating or reducing the cost
of data copies when passing chunk data through Wasm codecs. No approach
eliminates copies entirely within the current Component Model specification. The
analysis is preserved here in case circumstances change.

## Background

Each Wasm codec call requires copying data into the module's linear memory
before execution and copying the result back out after. See the
[memory and copy cost](../CODEC_RUNTIME.md#memory-and-the-copy-cost) section of
CODEC_RUNTIME.md for the full accounting.

The sandbox model is the fundamental constraint: a Wasm module can only read
and write its own linear memory — it cannot dereference a host pointer or
access the Python heap. The host can read and write the module's linear
memory, but not the reverse.

For a measured analysis of why the Python canonical ABI binding is slow and why
a native orchestrator is the correct fix, see
[CANONICAL_ABI_PERF.md](CANONICAL_ABI_PERF.md).

## Approaches That Do Not Eliminate Copies

### Shared memory (threads proposal)

Wasm's `SharedMemory` allows multiple Wasm *instances* to share memory, but
it is designed for multi-threaded Wasm execution, not host-guest data sharing.
The host would still need to copy data into the shared region to make it
visible to the module.

### Sharing memory between pipeline steps

Adjacent Wasm codecs run as separate instances with separate linear memories.
Linking them to a shared memory would require `--import-memory` at build time
and tightly couple independently authored modules. It would also break as soon
as any other codec step sits between two Wasm codecs. Not viable for a
general-purpose pipeline.

### Wasm-first architecture

The idea: allocate buffers in Wasm memory from the start and pass them between
steps without touching the Python heap. Not viable because codecs in a pipeline
are independent modules — each has its own linear memory, and there is no
mechanism to transfer ownership of a region from one module's linear memory to
another's.

### Custom memory backing

Wasmtime's Rust API has a `MemoryCreator` trait that allows customizing how
linear memory is allocated, which could in principle allow a shared backing
region. However, this interface does not appear to be exposed in wasmtime-py.

### Dual core-module path

**Rejected.** The idea: ship a `*-core.wasm` alongside each `*.wasm` component.
Core modules export `memory` as a plain wasmtime `Memory` object, so
`Memory.write()` and `Memory.read()` — single C-level memcpy calls — are
available. The Python executor would use the core path for speed and fall back
to the component path otherwise.

Rejected for two reasons:

1. **Eliminates Python-authored codecs.** `componentize-py` produces Component
   Model components, not core modules. There is no toolchain path from Python
   source to a core module with `alloc`/`dealloc`/`memory` exports. Python
   codecs must use the Component Model.

2. **Cannot represent the port-map.** The core module ABI from the old
   `wasm_runner.py` was hardcoded to two ports: one data buffer and one JSON
   config buffer. The current `port-map` (`list<tuple<port-name, list<u8>>>`)
   with a variable port count and potentially large data on any port cannot be
   represented without adding a serialization layer on top of the core ABI, at
   which point the simplicity advantage is lost.

### Per-component-exported resource buffer

**Rejected.** An earlier idea: define `resource byte-buf` inside the component's
exported `transform` interface; the codec returns a handle pointing to its
output buffer; the host writes the next input via `Memory.write()` using the
returned pointer.

Rejected because the Component Model hides linear memory from the host in Python
— `instance.get_export_index(store, "memory")` returns `None`. Even if a
resource constructor returns a pointer into the component's linear memory, there
is no `Memory` object available to write to it.

A broader version of this idea — a shared Wasm blob store — is analysed below.

### Native Python fallbacks

**Rejected.** Routing well-known codec operations (e.g. zlib, predictor-2) to
Python built-ins. Rejected because it reintroduces the exact platform and driver
dependency problem the Wasm architecture was designed to eliminate.

## Approaches That Reduce Python-Speed Copy Count (Not Copy Elimination)

### Wasm blob store

A Wasm component (written in a compiled language such as Rust) acts as a shared
buffer store. All codecs import the blob-store interface and pass data as opaque
resource handles rather than `list<u8>` values. Python passes handles (small
integers) between steps instead of byte buffers.

#### Required WIT architecture

The `blob` resource must be defined in a shared *imported* interface, not
exported by each codec component. If each component exports its own `blob`
resource, those types are scoped to the exporting component instance — passing
Component A's blob handle to Component B's `encode` is a type error at runtime,
not a warning. The correct design:

```wit
// chonkle:buffers@0.1.0
interface buffer-store {
    resource blob {
        static create: func(data: list<u8>) -> blob;
        as-bytes: func() -> list<u8>;
    }
}

world codec {
    import chonkle:buffers/buffer-store;
    export transform;
}
```

Both components import `chonkle:buffers/buffer-store`, so they share the same
`blob` type. Python can receive a handle from Component A and pass it to
Component B without materializing the bytes.

#### What it achieves (Python orchestrator)

For an N-step pipeline, the current approach has 2N Python-speed copies
(~1.7 MB/s each). With a Wasm blob store, only 2 copies cross the Python
boundary (initial ingestion and final extraction). The 2(N-1) intermediate
copies become Wasm-to-Wasm transfers at C memcpy speed. Python-speed copy count
scales as O(1) in pipeline length instead of O(N).

| Architecture | Python-speed copies | Wasm-speed copies | Total copies |
| --- | --- | --- | --- |
| Python + `list<u8>` (current) | 2N | 0 | 2N |
| Python + Wasm blob store | 2 | 2N | 2N+2 |
| Rust + `list<u8>` | 0 | 2N | 2N |
| Rust + Wasm blob store | 0 | 2N+2 | 2N+2 |

#### What it does not achieve

The blob store does not reduce the total number of memory copies — it increases
them. Every cross-component data access (`blob.as_bytes()`, `blob.create()`)
involves the canonical ABI copying bytes between separate linear memories, because
each component has its own isolated linear memory. Tracing through a 2-step
pipeline with a Rust orchestrator makes this concrete:

Rust orchestrator + current `list<u8>`:

```text
Rust → Step 1 linear memory    (copy 1: lower)
Step 1 runs
Step 1 linear memory → Rust    (copy 2: lift)
Rust → Step 2 linear memory    (copy 3: lower)
Step 2 runs
Step 2 linear memory → Rust    (copy 4: lift)
```

Total: 2N copies, all at ~10 GB/s.

Rust orchestrator + Wasm blob store:

```text
Rust → BlobStore linear memory          (copy 1)
BlobStore → Step 1 linear memory        (copy 2: as_bytes())
Step 1 runs
Step 1 linear memory → BlobStore        (copy 3: blob.create())
BlobStore → Step 2 linear memory        (copy 4: as_bytes())
Step 2 runs
Step 2 linear memory → BlobStore        (copy 5: blob.create())
BlobStore → Rust                        (copy 6)
```

Total: 2N+2 copies, all at ~10 GB/s.

For a Rust orchestrator the blob store is strictly worse. The "zero-copy" claim
sometimes made for resource handles applies only if the borrowing component can
read directly from the resource owner's linear memory. The current Component
Model specification does not permit this. An open proposal explores zero-copy
shared views, but it is not yet implemented:
<https://github.com/WebAssembly/component-model/issues/398>

#### Estimated performance (Python orchestrator, 2 MB chunks)

The 2 Python-speed copies dominate regardless of pipeline length
(~1.2 s each at 1.7 MB/s for 2 MB). Wasm-to-Wasm copy time is negligible
(~0.4 ms total at ~10 GB/s for a 2-step pipeline).

| Pipeline length | Python + `list<u8>` | Python + Wasm blob store | Rust + `list<u8>` |
| --- | --- | --- | --- |
| 2 steps | ~4.8 s | ~2.4 s | ~0.8 ms |
| 5 steps | ~12 s | ~2.4 s | ~2 ms |
| 10 steps | ~24 s | ~2.4 s | ~4 ms |

#### Stop-gap trade-offs

The Wasm blob store could serve as a stop-gap while the Python orchestrator
remains in use, particularly for pipelines longer than a few steps where the
O(N) growth of Python-speed copies becomes the dominant cost. The cost of doing
so:

- Every codec must be updated to import `chonkle:buffers/buffer-store` and
  switch from returning `list<u8>` to calling `blob.create()` for outputs and
  `blob.as_bytes()` for inputs. This is a breaking WIT change requiring source
  changes and recompilation for all codecs.

- When the orchestrator moves to Rust (the correct long-term fix, and the
  direction this project is headed), those codec changes provide no benefit —
  the Rust orchestrator with the original `list<u8>` WIT is faster than a Rust
  orchestrator with the blob store (2N copies vs 2N+2 copies at the same speed).
  All codecs would then need to be reverted to the `list<u8>` interface.

Given that the native orchestrator requires no codec changes at all, the blob
store stop-gap trades a large, reversible codec migration for a reduction from
~24 s to ~2.4 s on a 10-step pipeline, with the 2.4 s floor set by the two
remaining Python-speed copies. Whether that trade-off is worthwhile depends on
how long the Python orchestrator will remain in use and how many pipeline steps
production workloads require.

## Future Possibilities

- Caller-supplied buffers in the [WASI roadmap](https://wasi.dev/roadmap) could
  allow a host to pass a pre-allocated buffer for the module to write into,
  eliminating the copy-out step.
- An [open Component Model proposal](https://github.com/WebAssembly/component-model/issues/398)
  explores zero-copy shared views between components and the host. If adopted,
  it would make the blob store genuinely zero-copy for Wasm-to-Wasm transfers
  and would change the analysis above significantly.
