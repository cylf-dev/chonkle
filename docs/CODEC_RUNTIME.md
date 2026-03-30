# Codec Runtime

This document covers how chonkle loads and executes codecs. For general Wasm background, see the [knowledge base](wasm/) or the [docs index](README.md).

## Why Wasm for custom codecs?

Custom codecs should be fast, portable, and safe. The traditional options for fast codecs -- C extensions or Cython -- require platform-specific compilation and complex build tooling. Furthermore, native code loaded into your process (via C extensions, shared libraries, etc.) runs with the same privileges as your program: it can read and write any of your process's memory, access the filesystem, and make network calls. There is no built-in boundary between your code and the native extension.

Wasm gives us all three properties:

- **Fast:** Compiled C/Rust code runs at near-native speed.
- **Portable:** The same `.wasm` binary runs on any OS and architecture -- the runtime compiles it to native code, so codec authors distribute one file instead of per-platform builds.
- **Safe:** The runtime's sandbox ensures a codec module can only access its own memory -- it cannot touch the host process, the filesystem, or the network unless explicitly permitted.

## Why Python as the host?

Python is widely used in the geospatial and scientific data communities (our target audience), which means more people can read, understand, and contribute to the host code than if it were written in a compiled language like Rust or C++.

The tradeoff is performance for Component Model codecs: the wasmtime Python bindings implement canonical ABI lifting and lowering through Python object allocation rather than direct memory operations, running at approximately 1.7 MB/s. Core Wasm codecs bypass this bottleneck entirely by using `Memory.read()`/`Memory.write()`, which are single C-level calls achieving approximately 10 GB/s. A native-language host (Rust, C++) would eliminate the Python overhead for all codec types but limit accessibility. See [CANONICAL_ABI_PERF.md](internals/CANONICAL_ABI_PERF.md) for measurements.

## Codec backends

chonkle supports three codec backends. All three implement the `Codec` ABC with `call(direction, port_map)` and `signature()` methods.

### Component Model Wasm

`.wasm` components implementing the `chonkle:codec/transform@0.1.0` WIT interface (defined in `wit/codec.wit`). Uses the canonical ABI for data transfer. The executor discovers `encode`/`decode` functions by introspecting the component's type via the `chonkle:codec/transform@0.1.0` interface key. Components must be WASIp2-compatible.

`ComponentCodec` caches `Component.from_file()` at init time (not per-call). If the component returns the `Err` variant, chonkle raises `RuntimeError` with the error string.

Throughput is limited by the Python canonical ABI binding (approximately 1.7 MB/s measured).

See [codec-contract/component-model.md](reference/codec-contract/component-model.md) for the full interface specification.

### Core Wasm

wasm32-wasi reactor modules using a binary port-map wire format via `Memory.read()`/`Memory.write()`. These modules export `memory`, `alloc`, `dealloc`, `encode`, and `decode`. The binary wire format (little-endian u32 lengths, UTF-8 names, raw data) replaces the canonical ABI, achieving approximately 10 GB/s throughput.

`CoreWasmCodec` keeps the module instance alive for the codec's lifetime. This is required for single-copy transfer: when sequential steps are both core wasm codecs, data transfers use `ctypes.memmove` between linear memories via `Memory.data_ptr()`, avoiding any round-trip through Python objects.

`CoreWasmCodec.call()` returns `CoreWasmRef` deferred values -- bulk data stays in linear memory until consumed by a downstream codec. Non-core downstream codecs receive materialized bytes.

See [codec-contract/core.md](reference/codec-contract/core.md) for the wire format specification.

### Native (numcodecs)

Python codecs from the numcodecs library. Zero Wasm overhead. `numcodecs` and `numpy` are optional dependencies, imported lazily at `NativeCodec` instantiation.

`NativeCodec` supports two calling conventions via the `data_format` signature field:

- `"bytes"` -- data passed through as Python bytes objects
- `"ndarray"` -- buffer conversion using a `dtype` port

Non-`bytes` ports are JSON-decoded and passed as constructor kwargs to the numcodecs codec.

## Codec detection and resolution

### Binary detection

`detect_codec_type()` reads the 8-byte wasm header to route between backends:

- Core module: layer field `01 00 00 00`
- Component: layer field `0d 00 01 00`

### Resolution

The `Resolver` maps codec_ids to `Codec` instances via a resolution chain:

1. **Explicit paths** -- a `--codec-path` CLI flag or `paths` dict mapping a codec_id directly to a `.wasm` file. Useful for testing.
2. **Force sources** -- a `--source ID=URI` CLI flag or `force_sources` dict. Downloads the binary from the URI unconditionally, installs it into the store (overwriting any existing entry with the same implementation name), and returns the newly installed codec. Use this to fetch a specific remote build regardless of what is already in the store.
3. **Per-codec overrides** -- an `overrides` dict mapping a codec_id to a specific implementation name, bypassing the preference system entirely.
4. **Local codec store and native** -- all implementations for the codec_id are collected from the store and from bundled native signatures, then selected by backend preference.
5. **Pipeline sources** -- if no local match is found, a `sources` URI from the pipeline JSON is downloaded, installed into the store, and instantiated.

#### Backend preference

When step 4 finds candidates, the resolver walks an ordered preference list (default: `["native", "core", "component"]`) and returns the first candidate whose backend matches. This selects between backend *types*, not between implementations of the same type.

#### Multiple implementations of the same backend

If the store contains more than one implementation of the same backend for a given codec_id (e.g. two different core wasm builds), the resolver returns whichever appears first in the store index. The store index is built by scanning `.wasm` files in sorted filename order, so the implementation whose filename sorts first alphabetically wins. This is a deterministic but arbitrary tiebreak -- use an explicit override to select a specific implementation when it matters.

## Signatures

Signatures are embedded in `.wasm` binaries as `chonkle:signature` custom sections containing JSON. They are read by a pure-Python parser (`wasm_signature.py`) at codec instantiation -- no wasmtime dependency required for reading. Native codecs load signatures from bundled JSON files in `src/chonkle/signatures/numcodecs/`.

Signatures are embedded at build time using `chonkle embed-signature`. Source `signature.json` files in each codec's source directory are build inputs (not distribution artifacts). Downloads (`https://`, `oci://`) fetch only the `.wasm` file.

Validation rules:

- Both inputs and outputs use subset checks -- the step need not use every port the codec declares
- Input validation is direction-aware: ports marked `encode_only: true` are excluded from the valid input set when running in decode direction
- `prepare()` validates everything before execution; `run()` just executes the DAG

## Data copies

### Copy counts per inter-step edge

| Source backend | Dest backend | Copies | Mechanism |
| --- | --- | --- | --- |
| Core Wasm | Core Wasm | 1 | single-copy via `ctypes.memmove` between linear memories (~10 GB/s) |
| Core Wasm | Component Model | 2 | materialize from linear memory + canonical ABI lower |
| Core Wasm | Native | 1 | materialize from linear memory |
| Component Model | Core Wasm | 2 | canonical ABI lift + `Memory.write()` |
| Component Model | Component Model | 2 | canonical ABI lift + canonical ABI lower |
| Component Model | Native | 1 | canonical ABI lift |
| Native | Core Wasm | 1 | `Memory.write()` |
| Native | Component Model | 1 | canonical ABI lower |
| Native | Native | 0 | Python reference pass-through |

Final pipeline outputs are always materialized to bytes.

For a deeper analysis of copy-reduction approaches, see [DATA_COPIES.md](internals/DATA_COPIES.md).

### Sandbox constraint

Wasm's sandbox model means a module can only read and write its own linear memory -- it cannot dereference a host pointer or access the Python heap. The host can read and write the module's linear memory, but not the reverse. Every Wasm codec call therefore requires at least one copy in and one copy out.

The exception is core-to-core edges: because `CoreWasmCodec` keeps module instances alive and exposes `Memory.data_ptr()`, the executor can `ctypes.memmove` directly between two modules' linear memories, reducing two copies to one.

## Execution model

`prepare(source, direction)` parses the pipeline JSON, resolves codec_ids, validates wiring against codec signatures, and returns a `PreparedPipeline`. `run(prepared, inputs)` executes the DAG by calling `codec.call(direction, port_map)` for each step in topological order.

Data flows between steps through `value_store: dict[str, bytes | CoreWasmRef]`. Port-map builders materialize `CoreWasmRef` for non-core downstream codecs and pass them through for core downstream codecs (enabling single-copy transfer).
