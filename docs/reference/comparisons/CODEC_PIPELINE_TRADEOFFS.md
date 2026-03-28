# Codec Pipeline Architecture: Trade-off Analysis

This document analyzes the trade-offs across two related design axes for codec
pipelines that support WebAssembly:

1. **Mixed native + Wasm codecs vs. all-Wasm codecs** — whether to allow
   non-Wasm codecs (Python C extensions, shared libraries, etc.) alongside Wasm
   codecs in the same execution graph.

2. **Core module ABI vs. Component Model vs. both** — which Wasm interface style
   to use for the Wasm codecs: raw integer pointer/length conventions (Core), the
   typed WIT/Canonical ABI layer (Component Model), or a mix of the two.

These axes interact. The combination chosen affects performance, portability,
security, testability, and operational complexity. Both axes are analyzed
separately, then the interactions are summarized.

---

## Background: Data Movement at Step Boundaries

Understanding copy counts is foundational to both analyses. Wasm's sandbox model
means a module can read and write only its own linear memory — it cannot
dereference a host pointer. The host can read and write the module's linear
memory but not the reverse. This isolation is why data copies at step boundaries
are currently unavoidable.

For a Component Model codec invocation, two copies occur at each step boundary
regardless of host language — one to lower data into the component's linear
memory, one to lift output back to the host:

```
     host bytes
         │
      copy in   (lower: host → component linear memory)
         │
         ▼
component linear memory
         │
    codec runs
         │
         ▼
component linear memory
         │
      copy out  (lift: component linear memory → host)
         │
         ▼
     host bytes
```

Core Wasm codecs differ: the output stays in linear memory as a `CoreWasmRef`
deferred reference. When the next step is also a Core Wasm codec, the executor
does one `ctypes.memmove` between linear memories — one copy total for the
edge, not two. When the next step is Native, the output is read out of linear memory into
Python bytes — one copy. When the next step is Component Model, that read is
followed by a second copy to lower the bytes into the component's linear memory
via the canonical ABI.

The number of copies per edge, and their cost, depend on both source and
destination backend:

| Step A → Step B                               | Copies | Transfer mechanism            | Speed (Python host)  |
|-----------------------------------------------|--------|-------------------------------|----------------------|
| Native → Native                               | 0      | Python object reference       | —                    |
| Native → Core module                          | 1      | `Memory.write()` (ctypes)     | ~10 GB/s             |
| Core module → Native                          | 1      | `Memory.read()` (ctypes)      | ~10 GB/s             |
| Core module → Core module (Python host)       | 1      | `ctypes.memmove` (CoreWasmRef)| ~10 GB/s             |
| Component → Component (Python host)           | 2      | Canonical ABI lift + lower    | 1.4–1.9 MB/s †       |
| Component → Component (Rust/Go host)          | 2      | Single memcpy per direction   | ~10 GB/s             |

† Measured on wasmtime-py 41 against the `identity` and `predictor2` codecs with
1–2 MB buffers. The Python binding (`wasmtime._component._types.ListType`) iterates
every byte individually in Python: one `isinstance()` check and one ctypes field
write per byte. At 2 MB, this is ~2 million Python loop iterations per copy
direction. The Rust binding for the same WASM binary does a single `memory.write()`
call (one memcpy). Measured throughput: 1.7 MB/s (Python raw bench) vs. 9,113 MB/s
(Rust bench) at identical buffer sizes. This bottleneck is in the Python binding
layer; it is not a property of the Component Model architecture. The correct fix is
a Rust or Go orchestrator.

---

## Part 1: Mixed Native + Wasm vs. All-Wasm Pipelines

### Definitions

A **native codec** runs directly in the host process: Python C extensions
(numcodecs, Cython, ctypes, cffi), shared libraries, or pure Python functions.
No sandbox, no runtime overhead, full access to host memory and hardware.

A **Wasm codec** is compiled to a `.wasm` binary and executed inside a Wasm
runtime (e.g., Wasmtime). The runtime's sandbox enforces memory isolation.

> Note: `.wasm` files are not directly executable. The runtime compiles them to
> native machine code (`.cwasm`) on first load and can cache the result on disk.
> The `.wasm` artifact is portable; the compiled `.cwasm` is platform-specific.

---

### Mixed Native and Wasm Pipelines

**Pros**

- **Incremental adoption.** Existing native codecs can be used immediately
  without rewriting. Teams can introduce Wasm codecs alongside existing Python
  packages without a full migration.

- **Zero-copy at native-to-native boundaries.** When two adjacent pipeline steps
  are both native (e.g., two numcodecs transforms), data passes as a Python object
  reference. No serialization or memory copy occurs at that edge.

- **Full hardware access.** Native codecs have unrestricted access to GPUs,
  hardware crypto accelerators, architecture-specific SIMD (AVX, NEON), and any
  OS-level facility. Wasm does not support GPU offload and relies on WASI for
  system interfaces, which does not yet cover all hardware access patterns.

- **Mature toolchain.** Native codecs use standard package managers (pip, conda)
  and standard build tools. No Wasm-specific toolchain is required for the native
  steps.

- **Native debugging.** Stack traces from native codecs are standard Python/C
  frames. Debuggers and profilers (pdb, py-spy, gdb) attach directly without
  Wasm frame indirection.

**Cons**

- **Non-uniform interface contract.** Native and Wasm codecs have different
  calling conventions. The orchestrator must implement at least two code paths.
  Load-time interface verification applies only to the Wasm subset; native codecs
  have no equivalent mechanism.

- **Inconsistent security boundary.** Wasm codecs are sandboxed; native codecs
  run in the host process without isolation. A bug or supply-chain compromise in
  a native codec has full access to the host process, its memory, and its
  filesystem. The overall pipeline's security posture is determined by the
  least-sandboxed codec.

- **Heterogeneous distribution.** Native codecs are Python packages with
  platform-specific builds (`.so`, `.pyd`). Wasm codecs are `.wasm` binaries with
  different registries and verification schemes. Versioning, pinning, auditing,
  and caching use two distinct mechanisms.

- **Portability limited by native codecs.** A pipeline is only as portable as its
  least-portable native codec. A native codec that depends on a platform-specific
  shared library cannot run in a containerized or cross-platform environment
  without platform-specific builds or layers.

- **No static composition.** `wasm-tools compose` and `wac` (the WebAssembly
  composition tools) operate only on Component Model components. A pipeline that
  includes native steps cannot be statically merged into a single artifact.

- **Test strategy fragmentation.** Unit-testing a native codec requires different
  mocking and isolation strategies than testing a Wasm codec. Test infrastructure
  must account for both codec types.

---

### All-Wasm Pipelines

**Pros**

- **Single orchestrator code path.** The orchestrator deals only with Wasm
  codecs; there is no native dispatch branch to maintain in parallel. All
  steps share the same artifact format, distribution infrastructure, and
  signature-reading machinery.

- **Portable artifacts.** The same `.wasm` binary runs on any OS and architecture
  that the runtime supports (Linux x86_64, macOS arm64, cloud serverless
  environments) without changes to the artifact. Platform-specific work is handled
  by the runtime's compilation step.

- **Uniform security boundary.** Every codec is sandboxed. A buggy or compromised
  codec cannot access host memory, the filesystem, or the network beyond what the
  runtime explicitly grants via WASI. The trust model is consistent across all
  pipeline steps.

- **Uniform distribution.** All codecs are `.wasm` artifacts carrying embedded
  `chonkle:signature` custom sections. They use the same registry and caching
  infrastructure.

- **Language-agnostic authoring.** Any language with a Wasm toolchain — Rust
  (`cargo-component`), C/C++ (`wit-bindgen-c`), Python (`componentize-py`), Go
  (`wit-bindgen-go`) — can implement the same codec interface. The host does not
  care what language the codec was written in.

- **Static composition (Component Model, fixed topology).** `wasm-tools compose`
  can merge a fixed pipeline topology into a single component. Intra-component
  calls share a single linear memory, so inter-step copies are eliminated. A
  2-step composed pipeline pays 2 copies total (one into the composed component,
  one out) rather than 4. This requires the pipeline topology to be known at build
  time; it is not applicable to dynamic DAG pipelines.

**Cons**

- **At least 1 copy per step boundary.** Wasm's sandbox requires data to cross
  the host–codec boundary at each step edge. Component Model codecs incur 2
  copies per edge (lower + lift through the Canonical ABI). Core Wasm codecs
  incur 1 copy per edge (a `memmove` between linear memories for adjacent Core
  steps, or a single read-out for non-Core downstream codecs). Neither matches
  the 0-copy path available when two native codecs share process memory.
  (Static composition reduces Component Model pipeline cost to 2 total, but
  only for fixed topologies.)

- **Canonical ABI overhead under Python orchestrators (Component Model only).**
  With wasmtime-py, each copy direction through the Component Model Canonical
  ABI runs at 1.4–1.9 MB/s for `list<u8>` data. A 2-step pipeline processing
  2 MB tiles takes approximately 4–5 s. Core Wasm codecs do not use the
  Canonical ABI; their copy path runs at `memmove` speed. A native (Rust/Go)
  orchestrator eliminates the Canonical ABI overhead entirely: both copy
  directions run at memcpy speed (~10 GB/s). The Wasm architecture is not at
  fault; the bottleneck is the Python binding implementation.

- **Limited hardware access.** GPU compute, architecture-specific SIMD beyond
  what the Wasm SIMD proposal covers, and OS-level I/O not exposed through WASI
  are not available inside a codec without explicit runtime host function
  bindings. Codecs that need hardware accelerators are constrained by what the
  runtime exposes.

- **Toolchain requirements for codec authors.** Codec authors need a
  Wasm-capable toolchain and must maintain `.wasm` build artifacts alongside
  source. Component Model toolchains (cargo-component, componentize-py) are
  newer than general Wasm toolchains and vary in maturity by language.

- **Runtime scope.** Not every execution environment supports the same Wasm
  features. Component Model requires a runtime that implements it (Wasmtime,
  Wasmer 4+). Browser-native Component Model support requires polyfills as of
  2025. IoT-targeted runtimes (wasm3, WAMR) have limited or no Component Model
  support.

- **Debugging requires runtime-aware tools.** Useful stack traces from Wasm
  frames require DWARF debug info embedded in the binary (optional and larger)
  and runtime support for source-mapped frames (e.g., Wasmtime's `--debug`
  mode). Standard Python debuggers and profilers do not see inside Wasm frames.

---

### Summary: Mixed vs. All-Wasm

| Property                    | Mixed Native + Wasm                  | All-Wasm                              |
|-----------------------------|--------------------------------------|---------------------------------------|
| Interface uniformity        | No — dual calling convention         | ABI-dependent: WIT or binary port-map |
| Security isolation          | Inconsistent (weakest codec wins)    | Uniform sandbox per codec             |
| Portability                 | Limited by native codecs             | Platform-agnostic `.wasm` artifact    |
| Step boundary copies        | 0–2; varies by step backend pair     | 1 (Core→Core); 2 (Component Model)    |
| Static composition          | Not applicable                       | Available (Component Model, fixed topology) |
| Distribution uniformity     | No — packages + `.wasm` files        | Yes — `.wasm` artifacts only          |
| Migration barrier           | Low — existing codecs reusable       | Higher — requires Wasm toolchain      |
| Hardware access             | Full (native steps)                  | Limited to WASI + host functions      |
| Load-time verification      | Wasm steps only                      | Component Model steps only            |
| Debugging                   | Native steps: standard tools; Wasm steps: runtime-aware | Runtime-aware throughout |
| Orchestrator code paths     | Multiple (by codec type)             | One                                   |

---

## Part 2: Core Module ABI vs. Component Model

This section covers the interface style used for the Wasm codecs — how data
crosses the host–codec boundary. This choice is orthogonal to Part 1 but
interacts with it (discussed in Part 3).

### Definitions

**Core module ABI** (hereafter "Core ABI"): The codec is compiled as a Core
WebAssembly module. Its exported functions use only Wasm's numeric primitives
(`i32`, `i64`, `f32`, `f64`). Data exchange uses manually agreed
pointer/length conventions; the host reads and writes the module's linear
memory directly using `Memory.write()` and `Memory.read()`.

**Component Model**: The codec is compiled as a Component Model component, a
distinct binary format (different magic bytes: `0d 00 01 00` vs. `01 00 00 00`
for Core modules). Its interface is declared in WIT; the Canonical ABI handles
all type marshalling automatically. The component's linear memory is hidden
from the host — there is no `Memory.write()` path available.

---

### Core Module ABI

**Pros**

- **Fast host↔codec data transfer in all host languages, including Python.**
  The host exchanges data with the module via `Memory.write()` and
  `Memory.read()`, which are single C-level calls (one memcpy each) regardless
  of host language. In Python, this is the same speed as numpy memory access.
  There is no per-byte Python loop. This is the primary performance advantage
  over the Component Model's Python binding.

- **Broadest runtime compatibility.** Core WebAssembly is the foundational spec.
  Every Wasm runtime — Wasmtime, Wasmer, wasm3, browser WebAssembly, WAMR —
  supports Core modules. Component Model support is a superset of capabilities
  that not all runtimes implement.

- **Simpler binary format.** Core modules have less format overhead than
  Component Model wrappers. Inspection with tools like `wasm2wat` or
  `wasm-objdump` is more direct, which simplifies debugging and auditing.

- **Minimal toolchain requirements.** Any language that can target
  `wasm32-wasi` or `wasm32-unknown-unknown` produces a Core module without
  needing Component Model toolchain support. Component Model toolchains are
  newer and not uniformly available across all languages.

- **No ABI evolution risk from the WIT/Canonical ABI layer.** The calling
  convention is agreed between the codec author and the host; it does not change
  unless one side explicitly changes it. There is no shared specification layer
  that can introduce a breaking change in a new runtime version.

**Cons**

- **No machine-readable interface.** There is no formal description of what the
  module expects or produces. The host and codec author agree on a calling
  convention out-of-band (documentation, header files). Violations — wrong
  pointer size, off-by-one in a length, mismatched buffer layout — surface as
  silent data corruption or incorrect output, not as a load-time error.

- **No automated code generation.** Every host language must independently
  implement the same pointer/length marshalling protocol. There is no toolchain
  equivalent of `wit-bindgen` to generate correct glue from a shared definition.

- **No load-time interface verification.** The runtime validates that exported
  function type signatures use the right numeric primitive types. It cannot
  verify that the codec interprets those integers as a port-map in any
  particular way.

- **No ecosystem composition.** `wasm-tools compose` and `wac` operate on
  Component Model components only. Core modules cannot be statically composed
  into a single-binary pipeline.

- **Manual memory management.** The host must call the module's exported
  allocator to reserve memory, write data in, call the codec function, and then
  free the allocation. Incorrect management leaks memory within the module's
  linear memory or causes use-after-free during a codec invocation.

- **No rich type system at the boundary.** All values must be encoded as
  integers or floats. Strings, lists, records, and variant types require a
  manually defined serialization convention, which must be independently
  implemented on both sides and is not self-describing.

---

### Component Model

**Pros**

- **Interface verification at load time.** The runtime checks that the
  component's WIT exports match the expected interface before any code runs.
  Type and name mismatches are caught immediately as a hard error — not as
  silent runtime data corruption.

- **Automated code generation.** WIT toolchains generate all marshalling glue
  from the `.wit` file: `wit-bindgen` for Rust and C, `cargo-component` for
  Rust, `componentize-py` for Python, `wit-bindgen-go` for Go. Codec authors
  work with native types in their language; the generated code handles memory
  management and type adaptation.

- **Language-agnostic interoperability.** Any language with a Component Model
  toolchain can implement the same WIT interface. A Rust host can call a Python
  codec and a C codec through one uniform code path. No manual coordination
  between codec authors and host developers is needed beyond the shared `.wit`
  file.

- **Rich type system.** WIT supports strings, `list<T>`, `tuple<T, U>`,
  `option<T>`, `result<T, E>`, `record` (named struct), `variant` (tagged
  union), and `resource` (opaque handle). These types are expressed directly in
  the interface and require no manual serialization convention.

- **Memory isolation by design.** The component's linear memory is not
  accessible to the host. The host cannot read or write it outside the WIT
  interface. This is both a security property (host code cannot corrupt component
  memory) and a correctness property (no accidental aliasing).

- **Self-describing binary.** Components embed their WIT interface declarations
  in the binary. Tooling can inspect a component's interface without consulting
  out-of-band documentation.

- **Static composition.** `wasm-tools compose` and `wac` can merge a fixed
  pipeline topology into one component. Intra-component calls share a single
  linear memory, eliminating the Canonical ABI crossing (and therefore the copy)
  at each inter-step boundary. For an N-step pipeline, this reduces total copies
  from 2N to 2 regardless of step count.

- **Ecosystem alignment.** The Wasm ecosystem — WASI P2, warg/wa.dev component
  registry, Bytecode Alliance tooling, wasmCloud, Spin — is converging on the
  Component Model as the standard interface for Wasm artifacts. Components are
  first-class citizens in this ecosystem; Core modules are a lower-level
  substrate.

**Cons**

- **Canonical ABI cost under Python hosts.** wasmtime-py's Canonical ABI
  implementation for `list<u8>` iterates each byte individually in Python:
  one `isinstance()` check and one ctypes field write per byte, plus Python
  loop overhead. At 2 MB, this is approximately 2 million Python iterations per
  copy direction. Measured throughput: ~1.7 MB/s. The Rust binding for the
  same Wasm binary: ~9,100 MB/s. A Rust or Go orchestrator eliminates this
  discrepancy entirely; both directions run at memcpy speed.

- **Linear memory hidden from host.** Unlike Core modules, there is no
  `Memory.write()` path available from a component instance in Python:

  ```python
  mem_idx = instance.get_export_index(store, "memory")
  # → None. Memory is not accessible from a component instance.
  ```

  There is no performance shortcut available from a Python host. The Canonical
  ABI is the only data exchange path.

- **Narrower runtime support.** Full Component Model support requires a runtime
  that implements it — Wasmtime, Wasmer 4+. Browser-native Component Model
  support requires polyfills as of 2025. IoT-focused runtimes (wasm3, WAMR)
  have limited or no Component Model support.

- **WIT versioning coordination.** WIT interfaces are versioned (e.g.,
  `chonkle:codec@0.1.0`). A breaking WIT change requires updating every codec
  and every consumer. This is good discipline but adds coordination overhead
  compared to Core modules where calling convention changes are unilateral and
  unversioned.

- **Toolchain maturity varies by language.** Rust (`cargo-component`) and Python
  (`componentize-py`) have relatively mature Component Model support. Go and Zig
  have partial or evolving support as of 2025. Some languages have no Component
  Model toolchain at all.

- **Binary size overhead.** Component wrappers add format overhead (WIT metadata,
  component section, adapter modules). The absolute difference is typically small
  (kilobytes to tens of kilobytes) but not zero.

---

### Hybrid: Both Core ABI and Component Model in One Pipeline

Using both Core modules and Component Model components in the same pipeline —
typically Core ABI for steps where Python-host data transfer speed matters,
Component Model for steps that need interface verification or are authored in
multiple languages.

**Pros**

- **Per-codec optimization.** High-throughput, large-buffer codecs that must
  remain in a Python-orchestrated pipeline can use Core ABI for fast
  `Memory.write()`/`Memory.read()` transfers. Metadata-handling or
  complex-interface codecs can use Component Model for interface verification and
  code generation.

- **Migration path.** Existing Core module codecs can coexist with newer
  Component Model codecs during a transition. The pipeline continues to work
  while codecs are migrated incrementally.

- **Broader runtime reach for Core steps.** Core module steps run on any Wasm
  runtime. Only the Component Model steps require a runtime with Component Model
  support.

**Cons**

- **Dual calling convention in the orchestrator.** The executor must implement
  two distinct codec invocation code paths: manual pointer/length exchange for
  Core modules, Canonical ABI dispatch for Component Model components. These are
  not composable and increase orchestrator complexity.

- **No uniform interface contract.** Core module steps have no load-time
  verification. The overall pipeline's type safety is bounded by the unverified
  subset.

- **No static composition across the whole pipeline.** `wasm-tools compose`
  cannot link Core modules to Component Model components at a single boundary.
  Each Core↔Component boundary is a separate copy that cannot be eliminated by
  composition tooling.

- **Security asymmetry.** Core module linear memory is accessible to the host;
  component memory is not. The trust model differs per codec, which complicates
  auditing.

- **Distribution incompatibility.** Component Model registries (warg/wa.dev)
  treat components as first-class artifacts. Core modules require separate
  distribution infrastructure or must be treated as opaque blobs.

- **Increased orchestrator complexity without a clear resolution point.** The
  dual calling convention adds code paths that cannot be unified. Unlike a
  temporary migration, this complexity persists as long as both backend types
  are in use.

---

### Summary: Core ABI vs. Component Model vs. Hybrid

| Property                        | Core Module ABI                       | Component Model                    | Hybrid                             |
|---------------------------------|---------------------------------------|------------------------------------|-------------------------------------|
| Interface verification at load  | No                                    | Yes (WIT)                          | Partial (Component steps only)     |
| Automated code generation       | No                                    | Yes (wit-bindgen et al.)           | Partial                             |
| Data transfer speed (Python)    | ~ctypes speed (~10 GB/s)              | 1.4–1.9 MB/s (Python binding)      | Mixed                               |
| Data transfer speed (Rust/Go)   | ~10 GB/s                              | ~10 GB/s                           | ~10 GB/s                            |
| Linear memory accessible        | Yes (host uses Memory.read/write)     | No (hidden by design)              | Mixed                               |
| Static composition              | No                                    | Yes (fixed topology only)          | No (cannot span both types)         |
| Runtime support breadth         | All runtimes                          | Wasmtime, Wasmer 4+                | All runtimes (Core steps only)      |
| Rich types at boundary          | No (integers/floats only)             | Yes (WIT type system)              | Partial                             |
| Self-describing binary          | No                                    | Yes                                | Partial                             |
| Memory management               | Manual (host manages alloc/free)      | Automated (Canonical ABI)          | Mixed                               |
| Security model                  | Sandboxed; memory readable by host    | Full isolation                     | Inconsistent per codec              |
| Ecosystem alignment             | General Wasm                          | Bytecode Alliance / WASI P2        | Mixed                               |
| Orchestrator code paths         | One                                   | One                                | Two                                 |

---

## Part 3: Interaction of the Two Axes

The two axes are independent choices but they interact in practice.

**A mixed pipeline still requires a Core-or-Component decision for its Wasm
subset.** Allowing native codecs does not resolve the Core ABI vs. Component
Model question for the Wasm steps in the same pipeline. Both choices must be
made.

**The Python canonical ABI performance problem applies only to Component
Model + Python host.** Using Core ABI with a Python orchestrator avoids the
~1.7 MB/s bottleneck entirely, because `Memory.write()` and `Memory.read()` are
single C-level calls in Python. If the orchestrator moves to Rust or Go, the
bottleneck disappears and both Core ABI and Component Model achieve memcpy speed.
The correct fix for a Python orchestrator that uses the Component Model is a
native extension or a rewritten orchestrator — not a switch to Core ABI.

**Static composition is only available for all-Wasm + Component Model +
fixed topology.** It requires all pipeline steps to be Component Model components
and the topology to be known at build time. Mixed pipelines, Core ABI pipelines,
and dynamic DAG pipelines cannot use it.

**The long-term ecosystem trajectory favors Component Model.** WASI P2, warg/wa.dev,
and Bytecode Alliance tooling are converging on the Component Model as the
standard Wasm interface layer. Core ABI remains the foundational substrate but
is not the direction of new development.

**Static composition and native orchestration have different cost profiles.**
Static composition eliminates inter-step copies by merging the pipeline at build
time. A Rust or Go orchestrator eliminates the Python binding cost but keeps the 2N
copies. For large tiles in a many-step pipeline, static composition reduces
memory traffic; a Rust or Go orchestrator reduces per-copy overhead. Both improvements
are independent and composable.

---

## Part 4: chonkle's Architecture and Rationale

chonkle currently supports all three options: Component Model components, Core
Wasm modules, and native Python codecs can coexist in the same pipeline. This
section describes the choices made in this proof-of-concept and the
acknowledged costs.

### What was chosen and why

**Native backend** — the Python numcodecs ecosystem provides a large catalog of
existing codecs with hardware-accelerated implementations (SIMD, GPU, hardware
crypto). Reimplementing them as Wasm would impose a high barrier to incremental
adoption. Native edges also avoid copies entirely for native-to-native steps
and have unrestricted hardware access.

**Core Wasm backend** — under a Python orchestrator, `Memory.write()` and
`Memory.read()` transfers run at ~10 GB/s. The Component Model Canonical ABI
under wasmtime-py runs at ~1.7 MB/s for `list<u8>`. For data-intensive codecs
in the current Python executor, Core ABI offers substantially better throughput.
Core-to-core edges further reduce copies to one via `CoreWasmRef` deferred
references and `ctypes.memmove`.

**Component Model backend** — provides load-time interface verification,
automated code generation via `wit-bindgen`, and language-agnostic authoring.
WIT defines the codec contract for Component Model codecs. When the throughput
constraint is less critical or when the orchestrator is a compiled language,
Component Model is worth considering for new codecs.

### Backend consolidation

The current design does not commit to a single backend long-term. If the
orchestrator moves to Rust, the Canonical ABI bottleneck disappears and both
Wasm backends achieve memcpy throughput; at that point the backend distinction
would become a toolchain and portability question rather than a performance one.
How the three-backend design evolves from here is an open question.

### Acknowledged costs

- Three calling conventions in the orchestrator (`ComponentCodec`,
  `CoreWasmCodec`, `NativeCodec`), each with its own code path.
- No uniform interface contract: Core ABI and native codecs have no load-time
  verification. Interface checking applies only to Component Model steps.
- Inconsistent security boundary: native codecs run in the host process without
  isolation; Core module linear memory is readable by the host; only Component
  Model components have full isolation.
- No static composition across the full pipeline. `wasm-tools compose` requires
  all-Component-Model steps with a fixed topology.

---

## Appendix: Benchmark Data

Performance figures cited in this document are from measurements taken in this
repository using wasmtime-py 41 and wasmtime-rs 41 on the same `.wasm` binary
and same buffer sizes.

| Measurement                                      | Value          | Source                    |
|--------------------------------------------------|----------------|---------------------------|
| wasmtime-py Canonical ABI throughput (`list<u8>`)| 1.4–1.9 MB/s   | CANONICAL_ABI_PERF.md     |
| wasmtime-py raw bench (2 MB, 3 iterations)       | ~1.7 MB/s      | bench/python-host/        |
| wasmtime-rs typed bindings (2 MB, 3 iterations)  | ~9,100 MB/s    | bench/rust-host/          |
| Per-step copies (any Wasm codec, any host)       | 2              | DATA_COPIES.md            |
| Per-step copies after static composition (Component Model, fixed topology) | 2 total (all N steps) | DATA_COPIES.md |

Full benchmark setup, methodology, and per-codec/per-size tables are in
`docs/internals/CANONICAL_ABI_PERF.md` and `docs/internals/DATA_COPIES.md`.
