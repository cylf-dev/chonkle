# Running Custom Codecs with WebAssembly

This document covers how chonkle uses WebAssembly (Wasm) for custom codecs. For general Wasm background, see the [knowledge base](wasm/) or the [docs index](README.md).

## Why Wasm and Python?

### Why Wasm for custom codecs?

Custom codecs should be fast, portable, and safe. The traditional options for fast codecs — C extensions or Cython — require platform-specific compilation and complex build tooling. Furthermore, native code loaded into your process (via C extensions, shared libraries, etc.) runs with the same privileges as your program: it can read and write any of your process's memory, access the filesystem, and make network calls. There is no built-in boundary between your code and the native extension.

Wasm gives us all three properties:

- **Fast:** Compiled C/Rust code runs at near-native speed.
- **Portable:** The same `.wasm` binary runs on any OS and architecture — the runtime compiles it to native code, so codec authors distribute one file instead of per-platform builds.
- **Safe:** The runtime's sandbox ensures a codec module can only access its own memory — it cannot touch the host process, the filesystem, or the network unless explicitly permitted.

### Why Python as the host?

Python is widely used in the geospatial community (our initial target audience), which means more people can read, understand, and contribute to the host code than if it were written in a compiled language like Rust or C++.

However, Python as a host carries a significant performance cost with the Component Model — the architecture all chonkle codecs use, for reasons explained in the next section. The wasmtime Python bindings implement canonical ABI lifting and lowering through Python object allocation rather than the direct `Memory.write()`/`Memory.read()` calls available with Core modules, running at approximately 1.7 MB/s rather than the ~10 GB/s achievable in a native host. The Python executor should be treated as a development and test host, not a production runtime. The correct production path is a native (most likely Rust) orchestrator, which will require no changes to any codec and will perform the same canonical ABI lifting and lowering at C memcpy speeds. See [CANONICAL_ABI_PERF.md](internals/CANONICAL_ABI_PERF.md) for performance measurements.

## How this project uses Wasm

All chonkle codecs are Component Model components implementing the `chonkle:codec/transform` WIT interface (defined in `wit/codec.wit`). The Component Model uses WIT definitions and a Canonical ABI to marshal rich types like `list<u8>` and named port maps across the host-component boundary; see [CODEC_CONTRACT.md](reference/CODEC_CONTRACT.md) for the full interface specification. Custom codec source code and build tooling live in separate repositories; this project contains only the host-side runner.

`executor.py` loads and executes codec components from Python using Wasmtime. It discovers the `encode`/`decode` functions at runtime by introspecting the component's type. Before any component is called, the executor validates each step's declared ports against a `.signature.json` sidecar file located alongside the `.wasm` file; if any step fails validation, a `ValueError` listing all problems is raised before execution begins. Components must be WASIp2-compatible. If the component returns the `Err` variant, chonkle raises `RuntimeError` with the error string.

## Why the Component Model and not the Core ABI?

An earlier chonkle implementation used Core Wasm modules directly, accessing linear memory via `Memory.write()` and `Memory.read()` — single C-level copies, fast. The migration to the Component Model was made for two reasons that ruled out keeping the Core ABI path:

1. **The port-map cannot be represented cleanly in the Core ABI.** The old runner was hardcoded to two ports (one data buffer, one JSON config). A general-purpose pipeline needs more: a codec step may consume or produce multiple large data streams — a Parquet step, for instance, might emit separate column buffers — and the pipeline needs to route those streams by name to wire steps together correctly. The port-map's `list<tuple<port-name, list<u8>>>` design supports fan-in, fan-out, and named routing naturally. The Core ABI has no equivalent; encoding a variable set of named, potentially large buffers over raw pointers and lengths would require a custom serialisation layer — reintroducing exactly the marshaling complexity the Canonical ABI already handles automatically.

2. **Python-authored codecs require it.** `componentize-py` produces Component Model components; there is no toolchain path from Python source to a core module with the right `alloc`/`memory` exports. Restricting codec authorship to compiled languages would raise the barrier significantly for the scientific and geospatial communities that are our primary audience.

See [Component Model](wasm/COMPONENT_MODEL.md) for background on components vs. Core modules, composition, and language toolchains.

## Memory and the copy cost

A natural question when passing potentially large chunks through Wasm codecs is: *can we avoid copying data into Wasm linear memory?*

The answer is **no** — at least not today. Wasm's sandbox model means a module can only read and write its own linear memory — it cannot dereference a host pointer or access the Python heap. The host can read and write the module's linear memory, but not the reverse. Every codec call therefore requires one copy in (host → Wasm) and one copy out (Wasm → host): 2 copies per invocation, 2N copies for an N-step pipeline.

For the full edge-type accounting and analysis of copy-reduction approaches, see [DATA_COPIES.md](internals/DATA_COPIES.md). For Python vs. native host performance figures and practical mitigations, see [CANONICAL_ABI_PERF.md](internals/CANONICAL_ABI_PERF.md).
