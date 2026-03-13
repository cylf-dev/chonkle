# Running Custom Codecs with WebAssembly

This document covers how chonkle uses WebAssembly (Wasm) for custom codecs. For general Wasm knowledge, see the knowledge base in `docs/wasm/`:

- [What is WebAssembly?](wasm/OVERVIEW.md) — virtual CPU, runtimes, portability
- [WASI](wasm/WASI.md) — system interface, `wasm32-wasi` target, command vs reactor
- [Linear memory](wasm/MEMORY.md) — memory growth, `dlmalloc`, buffer strategies
- [Component Model](wasm/COMPONENT_MODEL.md) — components vs Core modules, composition, toolchain
- [WIT](wasm/WIT.md) — WIT interface definition language

## Why Wasm and Python?

### Why Wasm for custom codecs?

Custom codecs should be fast, portable, and safe. The traditional options for fast codecs — C extensions or Cython — require platform-specific compilation and complex build tooling. Furthermore, native code loaded into your process (via C extensions, shared libraries, etc.) runs with the same privileges as your program: it can read and write any of your process's memory, access the filesystem, and make network calls. There is no built-in boundary between your code and the native extension.

Wasm gives us all three properties:

- **Fast:** Compiled C/Rust code runs at near-native speed.
- **Portable:** The same `.wasm` binary runs on any OS and architecture — the runtime compiles it to native code, so codec authors distribute one file instead of per-platform builds.
- **Safe:** The runtime's sandbox ensures a codec module can only access its own memory — it cannot touch the host process, the filesystem, or the network unless explicitly permitted.

### Why Python as the host?

Python is widely used in the geospatial community (our initial target audience), which means more people can read, understand, and contribute to the host code than if it were written in a compiled language like Rust or C++.

## How this project uses Wasm

Wasm codec components are loaded and executed from Python using Wasmtime. All codecs must be Wasm components implementing the `chonkle:codec/transform` WIT interface (defined in `wit/codec.wit`). Custom codec source code and build tooling live in separate repositories; this project only contains the host-side runner.

### The codec contract

See [CODEC_CONTRACT.md](CODEC_CONTRACT.md) for the full specification — the Component WIT interface that all codec components must implement.

## Component Model

The Component Model uses WIT (WebAssembly Interface Types) definitions and a Canonical ABI to automatically marshal rich types like `list<u8>` and named port maps across the host-component boundary.

All chonkle codecs export the `chonkle:codec/transform` interface. See [CODEC_CONTRACT.md](CODEC_CONTRACT.md) for the full WIT definition.

`executor.py` discovers the `encode`/`decode` functions at runtime by introspecting the component's type. Before any component is called, the executor validates each step's declared ports against a `.signature.json` sidecar file located alongside the `.wasm` file; if any step fails validation, a `ValueError` listing all problems is raised before execution begins. Components must be WASIp2-compatible. If the component returns the `Err` variant, chonkle raises `RuntimeError` with the error string.

See [Component Model](wasm/COMPONENT_MODEL.md) for background on components vs. Core modules, composition, and language toolchains.

## Memory and the copy cost

A natural question when passing potentially large chunks through Wasm codecs is: *can we avoid copying data into Wasm linear memory?*

The answer appears to be **no** — at least not today. Wasm's sandbox model means a module can only read and write its own linear memory — it cannot dereference a host pointer or access the Python heap. The host *can* read and write the module's linear memory, but not the reverse. So data needs to be copied in before processing and copied out after. Each codec call performs two copies:

```text
   Python bytes
         │
      copy in            (copy 1: input)
         │
         ▼
Wasm linear memory
         │
    decode runs
         │
         ▼
Wasm linear memory
         │
      copy out           (copy 2: output)
         │
         ▼
   Python bytes
```

The cost of the copies could be small relative to decode computation for certain codecs (decompression, perhaps), and is likely very small relative to the network I/O required to fetch a custom Wasm codec. This would need to be verified through profiling, but it may not be worth optimizing further until we have evidence that the copies are a bottleneck.

Several approaches to eliminating copies were explored; none were viable. See [Copy elimination approaches](decisions/COPY_ELIMINATION.md) for the analysis and pointers to future possibilities.
