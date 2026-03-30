# Overview

This document covers why chonkle uses Wasm, why the host is written in Python, and how the three codec backends fit together. For general Wasm background, see the [wasm/](wasm/) knowledge base. For specs and reference material, see [reference/](reference/).

## Why Wasm for custom codecs?

Custom codecs should be fast, portable, and safe. Native code (C, C++, Rust compiled to shared libraries) is fast but requires platform-specific compilation, and it runs with the same privileges as the host process — it can read and write arbitrary memory, access the filesystem, and make network calls. There is no built-in boundary between the host and a native codec.

Wasm gives us all three properties:

- **Fast:** Compiled C/Rust code runs at near-native speed.
- **Portable:** The same `.wasm` binary runs on any OS and architecture — the runtime compiles it to native code, so codec authors distribute one file instead of per-platform builds.
- **Safe:** The runtime's sandbox ensures a codec module can only access its own memory — it cannot touch the host process, the filesystem, or the network unless explicitly permitted.

## Why Python as the host?

Python is widely used in the geospatial and scientific data communities (our target audience), which means more people can read, understand, and contribute to the host code than if it were written in a compiled language like Rust or C++.

The tradeoff is performance for Component Model codecs: the wasmtime Python bindings implement canonical ABI lifting and lowering through Python object allocation rather than direct memory operations, running at approximately 1.7 MB/s. Core Wasm codecs bypass this bottleneck entirely by using `Memory.read()`/`Memory.write()`, which are single C-level calls achieving approximately 10 GB/s. A native-language host (Rust, C++) would eliminate the Python overhead for all codec types but limit accessibility. See [design/CANONICAL_ABI_PERF.md](design/CANONICAL_ABI_PERF.md) for measurements.

## Codec backends

chonkle supports three codec backends. All three implement the `Codec` ABC with `call(direction, port_map)` and `signature()` methods. See the [codec contract](reference/codec-contract/) for full interface specifications.

**Component Model Wasm** — `.wasm` components implementing the `chonkle:codec/transform@0.1.0` WIT interface. Uses the canonical ABI for data transfer. Throughput is limited by the Python canonical ABI binding (approximately 1.7 MB/s measured). See [codec-contract/COMPONENT_MODEL.md](reference/codec-contract/COMPONENT_MODEL.md).

**Core Wasm** — wasm32-wasi reactor modules using a binary port-map wire format via `Memory.read()`/`Memory.write()`. Achieves approximately 10 GB/s throughput. `CoreWasmCodec` keeps module instances alive for single-copy transfer between sequential core wasm steps via `ctypes.memmove`. See [codec-contract/CORE.md](reference/codec-contract/CORE.md).

**Native (numcodecs)** — Python codecs from the numcodecs library. Zero Wasm overhead. `numcodecs` and `numpy` are optional dependencies, imported lazily. Supports `"bytes"` and `"ndarray"` calling conventions. See [codec-contract/NATIVE.md](reference/codec-contract/NATIVE.md).

## Execution model

`prepare(pipeline, direction)` parses the pipeline JSON, resolves codec_ids, validates wiring against codec signatures, and returns a `PreparedPipeline`. `run(prepared, inputs)` executes the DAG by calling `codec.call(direction, port_map)` for each step in topological order.

Data flows between steps through `value_store: dict[str, bytes | CoreWasmRef]`. Port-map builders materialize `CoreWasmRef` for non-core downstream codecs and pass them through for core downstream codecs (enabling single-copy transfer). Final pipeline outputs are always materialized to bytes.

See [design/DATA_COPIES.md](design/DATA_COPIES.md) for copy-count accounting across all backend combinations.
