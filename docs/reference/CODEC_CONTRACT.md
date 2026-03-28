# Codec Contract

This document specifies the interface contracts between chonkle (the host) and codec implementations. Three codec backends are supported: Component Model Wasm, Core Wasm, and Native (numcodecs). All three are wrapped by the `Codec` ABC and present a uniform `call(direction, port_map)` / `signature()` interface to the executor.

## Common requirements

Every codec, regardless of backend, must satisfy:

- **Signature**: a JSON object declaring `codec_id`, `implementation`, `inputs`, and `outputs`. For wasm codecs, the signature is embedded in the `.wasm` binary as a `chonkle:signature` custom section. For native codecs, the signature is a bundled JSON file in `src/chonkle/signatures/numcodecs/`. Preparation (`prepare()`) raises `ValueError` if a codec has no signature.
- **Port-map interface**: codecs receive a port-map (a list of named byte buffers) and return a port-map. The executor routes data between pipeline steps by matching port names.
- **Encode and decode**: every codec exposes both an `encode` and a `decode` operation. The executor selects which to call based on the pipeline direction.

### Signature format

```json
{
  "codec_id": "zstd",
  "implementation": "zstd-rs",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "level": {"type": "int", "required": false, "default": 3, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```

Fields:

- `codec_id` — logical codec identifier (e.g. `"zlib"`, `"tiff-predictor-2"`)
- `implementation` — identifies the specific build or project that produced the codec (e.g. `"zlib-rs"`, `"numcodecs.zlib"`)
- `inputs` — map of input port descriptors. Each has `type` (required), and optional `required` (default `true`), `default`, and `encode_only` (default `false`).
- `outputs` — map of output port descriptors. Each has `type`.
- `data_format` (native codecs only) — `"bytes"` or `"ndarray"`, controls the calling convention

Validation rules:

- Both inputs and outputs use **subset checks** — a step need not use every port the codec declares
- Input validation is **direction-aware**: ports marked `encode_only: true` are excluded from the valid input set when running in decode direction
- Active step inputs = `step.inputs.keys() - encode_only_inputs`

### Codec parameters

Codec parameters (constants) arrive as named port entries with values serialized as UTF-8 JSON bytes. A codec that accepts a compression level, for example, reads a `"level"` port entry and deserializes its bytes as JSON.

### Codec type detection

For wasm binaries, `detect_codec_type()` reads the 8-byte header (4-byte magic + 4-byte version) to distinguish backends:

- `00 61 73 6d` + `0d 00 01 00` → Component Model
- `00 61 73 6d` + `01 00 00 00` → Core Wasm

Native codecs are not `.wasm` files and are identified by the presence of a bundled signature file.

## Component Model Wasm

Component Model codecs implement the `chonkle:codec/transform@0.1.0` WIT interface. The toolchain (e.g. `cargo-component`, `wit-bindgen`, `wasm-tools component new`) generates all memory management and canonical ABI glue. Codec authors work only with high-level types.

### WIT interface

A component must export the `chonkle:codec/transform` interface. The full WIT definition (from `wit/codec.wit`):

```wit
package chonkle:codec@0.1.0;

interface transform {
    type port-name = string;
    type port-map = list<tuple<port-name, list<u8>>>;

    encode: func(inputs: port-map) -> result<port-map, string>;
    decode: func(inputs: port-map) -> result<port-map, string>;
}

world codec {
    export transform;
}
```

Port maps are ordered lists of `(port-name, bytes)` pairs. Port names are runtime conventions used to route data between pipeline steps — the executor matches step outputs to downstream step inputs by name.

### Export location

A compiled component must export the `transform` interface under the key `"chonkle:codec/transform@0.1.0"`. This is the key that the Component Model toolchain embeds when a component is built against `codec.wit`.

The host looks up this exact key in the component's exports (via the `CODEC_TRANSFORM_IFACE` constant) and retrieves `encode` or `decode` from within it. If the key is absent the component is rejected with a `RuntimeError`.

### Runtime requirements

Components must be **WASIp2-compatible**. The host calls `linker.add_wasip2()` unconditionally; components that do not target WASIp2 will fail to instantiate.

### Error signaling

Return the `Err` variant of `result<port-map, string>` to signal failure. The host raises `RuntimeError` with the error string as the message. Return `Ok(port-map)` on success.

### Signature embedding

The signature is embedded in the `.wasm` binary as a `chonkle:signature` custom section at build time:

```bash
chonkle embed-signature codec.wasm signature.json
```

## Core Wasm

Core Wasm codecs are wasm32-wasi reactor modules that use a binary port-map wire format instead of the Component Model canonical ABI. The full specification, including the wire format, calling convention, call sequence diagram, and reference implementations in C and Rust, is in [`CORE_ABI.md`](CORE_ABI.md).

### Required exports

| Export | Type | Description |
| --- | --- | --- |
| `memory` | Memory | Linear memory accessible to the host |
| `alloc` | `func(size: i32) -> i32` | Allocate `size` bytes; return pointer or 0 on failure |
| `dealloc` | `func(ptr: i32, size: i32)` | Free a buffer previously returned by `alloc` or by `encode`/`decode` |
| `encode` | `func(ptr: i32, len: i32) -> i64` | Encode operation |
| `decode` | `func(ptr: i32, len: i32) -> i64` | Decode operation |

### Return value

`encode` and `decode` return a packed `i64`: `(output_ptr << 32) | output_len`. Return `0` to signal an error.

### Signature embedding

Identical to Component Model codecs — a `chonkle:signature` custom section is embedded in the `.wasm` binary:

```bash
chonkle embed-signature codec.wasm signature.json
```

### Single-copy transfer

When two adjacent pipeline steps are both core wasm codecs, the executor transfers data between their linear memories using `ctypes.memmove` via `Memory.data_ptr()`. This avoids copying data out of wasm memory and back in. Non-core downstream codecs receive materialized bytes.

## Native (numcodecs)

Native codecs have no binary artifact. The `NativeCodec` wrapper dynamically instantiates a numcodecs codec object at runtime. `numcodecs` and `numpy` are optional dependencies, imported lazily at `NativeCodec` instantiation — users who only run wasm codecs do not need them installed.

### Signature source

Signatures are bundled as JSON files in `src/chonkle/signatures/numcodecs/`, one file per supported codec (e.g. `zlib.json`, `delta.json`). Adding support for a new numcodecs codec requires adding a JSON file to this directory.

Native signatures use the same format as wasm codec signatures, with one additional field: `data_format`.

### Data format and calling convention

The `data_format` field in the signature controls how the `bytes` port is handled:

- **`"bytes"`** (default): the `bytes` port value is passed directly to `codec.encode()` / `codec.decode()`. Used by compression codecs (zlib, gzip, bz2, lzma, zstd, lz4, blosc).
- **`"ndarray"`**: the `bytes` port is interpreted as a raw buffer and converted to a numpy ndarray using the `dtype` port (required for ndarray-format codecs). The result is converted back to bytes after processing. Used by array-level codecs (delta, shuffle).

### Parameter handling

Non-`bytes` ports in the input port-map (excluding `dtype` for ndarray-format codecs) are JSON-decoded and passed as constructor kwargs to the numcodecs codec via `numcodecs.get_codec()`. For ndarray-format codecs, `dtype` is first attempted as a constructor kwarg; if the codec does not accept it, it is used only for buffer conversion.
