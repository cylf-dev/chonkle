# Running Custom Codecs with WebAssembly

## What is WebAssembly?

WebAssembly (Wasm) is a specification for a CPU that doesn't physically exist. It defines an instruction set — the operations this CPU can perform (things like "add two integers" or "load a byte from memory") — along with how it executes them and how its memory is organized. A `.wasm` file is a program composed of these operations — bytecode targeting this virtual CPU rather than any real hardware. Languages like C, C++, and Rust can compile to Wasm instead of to a physical CPU's instruction set, producing `.wasm` files.

A program that implements the virtual CPU is called a "runtime." The simplest approach would be for the runtime to interpret the `.wasm` bytecode instruction by instruction, but that would be slow. Instead, modern runtimes like [Wasmtime](https://wasmtime.dev/) compile the bytecode to native machine code for the host CPU before running it. The actual execution runs native instructions, which is why Wasm achieves near-native speed. This compilation step takes time (it is the most expensive overhead when loading a module), but it only needs to happen once per `.wasm` file — runtimes can cache the compiled output on disk and reuse it across runs (see [CACHING.md](CACHING.md)). The compiled output is specific to the host CPU architecture, but the `.wasm` bytecode itself remains portable.

Because `.wasm` operations target a virtual CPU rather than a specific physical one, the same `.wasm` file is portable across any OS and architecture. And because execution goes through the runtime, the runtime can enforce strict safety guarantees like memory isolation.

Wasm is often called a "virtual machine," but it's a different kind than the hypervisor kind (VMware, VirtualBox, EC2). Those virtualize an entire computer — CPU, memory, disk, network — so a full guest OS can boot inside. Wasm virtualizes just a CPU — the virtual CPU described above — with no OS, no virtual disk, and no boot process. Python works the same way — `.pyc` files are bytecode for CPython's virtual machine. The JVM (Java Virtual Machine) is another example.

Wasm was originally created for web browsers, where the runtime is built into the browser itself. But the same properties turned out to be valuable outside of browsers too. Standalone runtimes like [Wasmtime](https://wasmtime.dev/) can run `.wasm` modules outside the browser — either as standalone programs (the module has a `main` entry point and the runtime runs it) or as libraries (the host program loads the module and calls its exported functions on demand). This project uses Wasm in the library sense: Python loads a `.wasm` codec module through Wasmtime and calls its exported functions (`alloc`, `encode` or `decode`, `dealloc`) as needed.

## Why Wasm and Python?

### Why Wasm for custom codecs?

Custom codecs need to be fast, portable, and safe. The traditional options for fast codecs — C extensions or Cython — require platform-specific compilation and complex build tooling. Worse, native code loaded into your process (via C extensions, shared libraries, etc.) runs with the same privileges as your program: it can read and write any of your process's memory, access the filesystem, and make network calls. There is no built-in boundary between your code and the native extension.

Wasm gives us all three properties:

- **Fast:** Compiled C/Rust code runs at near-native speed.
- **Portable:** The same `.wasm` binary runs on any OS and architecture without recompilation.
- **Safe:** The runtime's sandbox ensures a codec module can only access its own memory — it cannot touch the host process, the filesystem, or the network unless explicitly permitted.

### Why Python as the host?

Python is widely used in the geospatial community (our initial target audience), which means more people can read, understand, and contribute to the host code than if it were written in a compiled language like Rust or C++. It also gives us direct access to Zarr's numcodecs library, so standard codecs work out of the box alongside custom Wasm codecs.

## How this project uses Wasm

This project uses the **Core Wasm** approach (see [Why Core Wasm?](#why-core-wasm-over-the-component-model) for the rationale). Wasm codecs are loaded and executed from Python using Wasmtime. Custom codec source code and build tooling live in a separate repositories; this project only contains the host-side runner.

### Codec pipelines

The codec pipeline (`pipeline.py`) applies a sequence of codec steps to encode or decode a chunk. Each step can be a **Python codec** (via numcodecs) or a **Wasm codec**. They can be freely mixed in any order. For example, a COG tile pipeline might look like:

```text
numpy array  ──[bytes (Python)]──►  raw bytes  ──[tiff_predictor_2 (Wasm)]──►  differenced bytes  ──[zlib (Python)]──►  compressed bytes
```

**Encoding** applies codecs in forward order (as shown above). **Decoding** applies them in reverse order, unwinding the encoding. Each step receives the output of the previous step.

### Pipeline metadata format

Each chunk has a sidecar JSON file that specifies its codec pipeline. Wasm codec steps use `"type": "wasm"` and include a URI pointing to the `.wasm` file:

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

Supported URI schemes: `file://`, `https://`, and `oci://`.

### The codec ABI

An ABI (Application Binary Interface) defines how two separately compiled pieces of code talk to each other at the binary level — what functions exist, what types their arguments and return values have, and how memory is laid out. Here, the ABI is the contract between chonkle (the host) and a `.wasm` codec module: it specifies the exact functions the host expects to find and how data is passed back and forth through linear memory.

Every Wasm codec module must export a `memory` and three functions. The `memory` export is the module's linear memory — a resizable byte array that the host reads from and writes to in order to pass data across the Wasm boundary. It is automatically provided by the compiler when you target `wasm32`; codec authors do not need to define it.

The functions that codec authors must implement:

| Export | Signature | Purpose |
| --- | --- | --- |
| `alloc` | `(size: i32) -> i32` | Allocate `size` bytes, return a pointer |
| `dealloc` | `(ptr: i32, size: i32)` | Free a previously allocated buffer |
| `decode` | `(input_ptr: i32, input_len: i32, config_ptr: i32, config_len: i32) -> i64` | Decode data; return packed result |
| `encode` | `(input_ptr: i32, input_len: i32, config_ptr: i32, config_len: i32) -> i64` | Encode data; return packed result |

A module may export `decode`, `encode`, or both. The host calls whichever function matches the pipeline direction.

#### Calling convention

The host (`wasm_runner.py`) performs the following sequence for each codec call (`decode` or `encode`):

1. **Allocate and write input** — Call `alloc(len(data))`, then write the raw input bytes into linear memory at the returned pointer.
2. **Allocate and write config** — Serialize the configuration dict as a JSON string, call `alloc(len(json_bytes))`, then write the JSON bytes into linear memory.
3. **Call the codec function** (`decode` or `encode`) — Pass the four arguments: `input_ptr`, `input_len`, `config_ptr`, `config_len`.
4. **Unpack the result** — The return value is a single `i64` encoding both the output pointer and length: `out_ptr = (result >> 32) & 0xFFFFFFFF`, `out_len = result & 0xFFFFFFFF`.
5. **Read output** — Copy `out_len` bytes from linear memory starting at `out_ptr`.
6. **Deallocate** — Call `dealloc` three times to free the input, config, and output buffers.

#### Error signaling

If the codec function returns `out_ptr = 0` and `out_len = 0`, the host treats this as a failure and raises a `RuntimeError`.

## Why Core Wasm over the Component Model?

Wasm has two approaches to host-module communication. The **Core** approach passes only numbers (`i32`, `i64`, `f32`, `f64`) across the boundary — to exchange bytes or strings, the host manually writes data into linear memory and passes pointers. The **Component Model** uses WIT (Wasm Interface Type) definitions and a Canonical ABI to automatically marshal rich types like `list<u8>` and `string`.

In the Component Model, you define your interface in a `.wit` file using high-level types — `list<u8>` for a byte array, `string` for text, `record` for structs, etc. The **Canonical ABI** is the specification that tells both sides (host and module) exactly how to serialize and deserialize those types across the Wasm boundary. The tooling generates glue code that handles all the pointer/length/memory management, so you'd just call `decode(data, config)` passing a byte list and a string directly, rather than doing `alloc` → `memory.write` → pass pointer and length → `memory.read` → `dealloc` yourself.

### The codec ABI is simple enough that the Component Model doesn't pay for itself

Our codec interface is essentially `decode(bytes, string) -> bytes`. The Component Model's strengths — rich type marshaling, formal interface contracts, cross-language interoperability — shine when interfaces have many types and complex signatures. For a function that takes bytes and returns bytes, the Canonical ABI adds machinery without proportional benefit.

### Codec author friction matters more than host-side elegance

The Component Model makes the host code cleaner (about 5 lines vs 30), but pushes complexity onto codec authors through extra build steps and generated files for certain languages (e.g., C). Thus, we chose the Core approach to minimize friction when authoring custom codecs, while accepting some additional complexity in the host code, which is an internal implementation detail.

### Stability and migration

The Wasm Component Model spec continues to evolve, and committing to it now means tracking upstream changes in `wit-bindgen`, `wasm-tools`, and the Canonical ABI. The Core approach gives us a stable, fully self-controlled ABI with no external specification dependencies. If the Component Model matures and becomes the dominant standard, migrating would require revising `wasm_runner.py` on the host side and updating codec build instructions for authors.

## Memory and the copy cost

A natural question when passing potentially large chunks through Wasm codecs is: *can we avoid copying data into Wasm linear memory?*

The answer appears to be **no** — at least not today. Wasm's sandbox model means a module can only read and write its own linear memory — it cannot dereference a host pointer or access the Python heap. The host *can* read and write the module's linear memory, but not the reverse. So data needs to be copied in before processing and copied out after. Each call to `wasm_decode()` or `wasm_encode()` performs two copies:

```text
   Python bytes
         │
     memcpy in           (copy 1: input)
         │
         ▼
Wasm linear memory
         │
    decode runs
         │
         ▼
Wasm linear memory
         │
     memcpy out          (copy 2: output)
         │
         ▼
   Python bytes
```

It is worth noting that in a mixed pipeline, native-to-native (from one numcodec codec to another) steps may also involve allocation overhead (e.g. returning a new `bytes` object), though Wasm codecs have the additional cost of copying input into linear memory. Also, the cost of the copies could be small relative to decode computation for certain codecs (decompression, perhaps), and is likely very small relative to the network I/O required to fetch a custom Wasm codec. This would need to be verified through profiling, but it may not be worth optimizing further until we have evidence that the copies are a bottleneck.

### Approaches explored

Several approaches to eliminating copies were explored and are noted here in case they are worth following up on in the future.

- **Shared memory (threads proposal)** — Wasm's `SharedMemory` allows multiple Wasm *instances* to share memory, but it appears to be designed for multi-threaded Wasm execution, not host-guest data sharing. The host would still copy data in.
- **Sharing memory between pipeline steps** — Adjacent Wasm codecs run as separate instances with separate linear memories. Linking them to a shared memory would require `--import-memory` and tightly couple independently authored modules. It would also break when a native codec sits between two Wasm codecs.
- **Wasm-first architecture** — Allocating in Wasm memory and having native codecs operate on it in place doesn't seem to work because numcodecs implementations generally return new `bytes` or `ndarray` objects rather than writing into a caller-supplied buffer.
- **Custom memory backing** — Wasmtime's Rust API has a `MemoryCreator` trait for custom allocation, but it does not appear to be exposed in wasmtime-py.

### Future possibilities

Links to relevant zero-copy discussions:

- Caller-supplied buffers in the [WASI roadmap](https://wasi.dev/roadmap).
- An [open Component Model proposal](https://github.com/WebAssembly/component-model/issues/398) exploring zero-copy shared views.
