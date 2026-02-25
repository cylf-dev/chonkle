# WASM Codecs

## What is WebAssembly?

WebAssembly (WASM) is a binary instruction format originally designed to run compiled code in web browsers alongside JavaScript. But the same properties that make it useful in browsers turned out to be valuable outside of them too. Standalone runtimes like [Wasmtime](https://wasmtime.dev/) can execute `.wasm` modules as ordinary programs or libraries, with no browser involved. This project uses WASM in that non-browser context: Python loads and runs `.wasm` codec modules through Wasmtime.

Two properties make WASM useful for codecs:

- **Portable** — The same `.wasm` binary runs on any OS and architecture. No need to compile separate binaries for Linux/macOS/Windows or x86/ARM.
- **Sandboxed** — A WASM module gets its own private block of memory and cannot access the host's memory, filesystem, or network unless the host explicitly allows it.

## Why run WASM from Python?

Python is a attractive choice for the host layer because it gives us direct access to Zarr's numcodecs library for standard codecs. But custom codecs that aren't part of numcodecs pose two problems: pure Python implementations are often slow, and running untrusted native code safely is difficult. The traditional options for speed — C extensions or Cython — require platform-specific compilation, complex build tooling, and grant native code full access to your process memory.

WASM solves both problems. Compiled C/Rust code runs at near-native speed inside the sandbox, and because WASM modules are portable, custom codecs can be distributed and executed securely on any platform without recompilation.

## How this project uses WASM

This project uses the **Core WASM** approach (see [Why Core WASM?](#why-core-wasm-over-the-component-model) for the rationale). WASM codecs are loaded and executed from Python using Wasmtime. Custom codec source code and build tooling live in a separate repositories; this project only contains the host-side runner.

### Codec pipelines

The decode pipeline (`pipeline.py`) applies a sequence of codec steps to decode a chunk. Each step can be a **Python codec** (via numcodecs) or a **WASM codec**. They can be freely mixed in any order. For example, a COG tile might be decoded as:

```text
compressed bytes  ──[zlib (Python)]──►  differenced bytes  ──[tiff_predictor_2 (WASM)]──►  raw bytes  ──[bytes (Python)]──►  numpy array
```

Codecs are applied in **reverse order**, unwinding the encoding chain. Each step receives the output of the previous step.

### Pipeline metadata format

Each chunk has a sidecar JSON file that specifies its codec pipeline. WASM codec steps use `"type": "wasm"` and include a URI pointing to the `.wasm` file:

```json
{
  "codecs": [
    {
      "name": "bytes",
      "type": "numcodecs",
      "configuration": {
        "endian": "little",
        "data_type": "uint16",
        "shape": [1024, 1024]
      }
    },
    {
      "name": "tiff_predictor_2",
      "type": "wasm",
      "uri": "file:///path/to/tiff-predictor-2-c.wasm",
      "configuration": {
        "bytes_per_sample": 2,
        "width": 1024
      }
    },
    {
      "name": "zlib",
      "type": "numcodecs",
      "configuration": {}
    }
  ]
}
```

Currently only `file://` URIs are supported. Other schemes (e.g. `oci://`) will be added as necessary.

### The codec ABI

An ABI (Application Binary Interface) defines how two separately compiled pieces of code talk to each other at the binary level — what functions exist, what types their arguments and return values have, and how memory is laid out. Here, the ABI is the contract between chonkle (the host) and a `.wasm` codec module: it specifies the exact functions the host expects to find and how data is passed back and forth through linear memory.

Every WASM codec module must export a `memory` and three functions. The `memory` export is the module's linear memory — a resizable byte array that the host reads from and writes to in order to pass data across the WASM boundary. It is automatically provided by the compiler when you target `wasm32`; codec authors do not need to define it.

The three functions that codec authors must implement:

| Export | Signature | Purpose |
| --- | --- | --- |
| `alloc` | `(size: i32) -> i32` | Allocate `size` bytes, return a pointer |
| `dealloc` | `(ptr: i32, size: i32)` | Free a previously allocated buffer |
| `decode` | `(input_ptr: i32, input_len: i32, config_ptr: i32, config_len: i32) -> i64` | Decode data; return packed result |

#### Calling convention

The host (`wasm_runner.py`) performs the following sequence for each decode call:

1. **Allocate and write input** — Call `alloc(len(data))`, then write the raw input bytes into linear memory at the returned pointer.
2. **Allocate and write config** — Serialize the configuration dict as a JSON string, call `alloc(len(json_bytes))`, then write the JSON bytes into linear memory.
3. **Call `decode`** — Pass the four arguments: `input_ptr`, `input_len`, `config_ptr`, `config_len`.
4. **Unpack the result** — The return value is a single `i64` encoding both the output pointer and length: `out_ptr = (result >> 32) & 0xFFFFFFFF`, `out_len = result & 0xFFFFFFFF`.
5. **Read output** — Copy `out_len` bytes from linear memory starting at `out_ptr`.
6. **Deallocate** — Call `dealloc` three times to free the input, config, and output buffers.

#### Error signaling

If `decode` returns `out_ptr = 0` and `out_len = 0`, the host treats this as a decode failure and raises a `RuntimeError`.

## Why Core WASM over the Component Model?

WASM has two approaches to host-module communication. The **Core** approach passes only numbers (`i32`, `i64`, `f32`, `f64`) across the boundary — to exchange bytes or strings, the host manually writes data into linear memory and passes pointers. The **Component Model** uses WIT (Wasm Interface Type) definitions and a Canonical ABI to automatically marshal rich types like `list<u8>` and `string`.

In the Component Model, you define your interface in a `.wit` file using high-level types — `list<u8>` for a byte array, `string` for text, `record` for structs, etc. The **Canonical ABI** is the specification that tells both sides (host and module) exactly how to serialize and deserialize those types across the WASM boundary. The tooling generates glue code that handles all the pointer/length/memory management, so you'd just call `decode(data, config)` passing a byte list and a string directly, rather than doing `alloc` → `memory.write` → pass pointer and length → `memory.read` → `dealloc` yourself.

### The codec ABI is simple enough that the Component Model doesn't pay for itself

Our codec interface is essentially `decode(bytes, string) -> bytes`. The Component Model's strengths — rich type marshaling, formal interface contracts, cross-language interoperability — shine when interfaces have many types and complex signatures. For a function that takes bytes and returns bytes, the Canonical ABI adds machinery without proportional benefit.

### Codec author friction matters more than host-side elegance

The Component Model makes the host code cleaner (about 5 lines vs 30), but pushes complexity onto codec authors through extra build steps and generated files for certain languages (e.g., C). Thus, we chose the Core approach to minimize friction when authoring custom codecs, while accepting some additional complexity in the host code, which is an internal implementation detail.

### Stability and migration

The WASM Component Model spec continues to evolve, and committing to it now means tracking upstream changes in `wit-bindgen`, `wasm-tools`, and the Canonical ABI. The Core approach gives us a stable, fully self-controlled ABI with no external specification dependencies. If the Component Model matures and becomes the dominant standard, migrating would require revising `wasm_runner.py` on the host side and updating codec build instructions for authors.

## Memory and the copy cost

A natural question when passing potentially large chunks through WASM codecs is: *can we avoid copying data into WASM linear memory?*

The answer appears to be **no** — at least not today. WASM's sandbox model means a module can only read and write its own linear memory — it cannot dereference a host pointer or access the Python heap. The host *can* read and write the module's linear memory, but not the reverse. So data needs to be copied in before processing and copied out after. Each call to `wasm_decode()` performs two copies:

```text
   Python bytes
         │
     memcpy in           (copy 1: input)
         │
         ▼
WASM linear memory
         │
    decode runs
         │
         ▼
WASM linear memory
         │
     memcpy out          (copy 2: output)
         │
         ▼
   Python bytes
```

It is worth noting that in a mixed pipeline, native-to-native (from one numcodec codec to another) steps may also involve allocation overhead (e.g. returning a new `bytes` object), though WASM codecs have the additional cost of copying input into linear memory. Also, the cost of the copies could be small relative to decode computation for certain codecs (decompression, perhaps), and is likely very small relative to the network I/O required to fetch a custom WASM codec. This would need to be verified through profiling, but it may not be worth optimizing further until we have evidence that the copies are a bottleneck.

### Approaches explored

Several approaches to eliminating copies were explored and are noted here in case they are worth following up on in the future.

- **Shared memory (threads proposal)** — WASM's `SharedMemory` allows multiple WASM *instances* to share memory, but it appears to be designed for multi-threaded WASM execution, not host-guest data sharing. The host would still copy data in.
- **Sharing memory between pipeline steps** — Adjacent WASM codecs run as separate instances with separate linear memories. Linking them to a shared memory would require `--import-memory` and tightly couple independently authored modules. It would also break when a native codec sits between two WASM codecs.
- **WASM-first architecture** — Allocating in WASM memory and having native codecs operate on it in place doesn't seem to work because numcodecs implementations generally return new `bytes` or `ndarray` objects rather than writing into a caller-supplied buffer.
- **Custom memory backing** — Wasmtime's Rust API has a `MemoryCreator` trait for custom allocation, but it does not appear to be exposed in wasmtime-py.

### Future possibilities

Links to relevant zero-copy discussions:

- Caller-supplied buffers in the [WASI roadmap](https://wasi.dev/roadmap).
- An [open Component Model proposal](https://github.com/WebAssembly/component-model/issues/398) exploring zero-copy shared views.
