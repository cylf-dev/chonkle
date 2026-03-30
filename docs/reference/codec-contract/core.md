# Core Wasm

Core Wasm codecs are wasm32-wasi reactor modules. The host and module communicate through direct reads and writes to the module's linear memory, using a binary serialization format for port-maps.

## Required Exports

| Export | Type | Description |
|---|---|---|
| `memory` | Memory | Linear memory (provided automatically by the wasm toolchain) |
| `alloc` | `func(size: i32) -> i32` | Allocate `size` bytes; return pointer or 0 on failure |
| `dealloc` | `func(ptr: i32, size: i32)` | Free a buffer previously returned by `alloc` or by `encode`/`decode` |
| `encode` | `func(port_map_ptr: i32, port_map_len: i32) -> i64` | Encode operation |
| `decode` | `func(port_map_ptr: i32, port_map_len: i32) -> i64` | Decode operation |

## Port-Map Binary Format

All multi-byte integers are little-endian.

```
u32: entry_count
For each entry:
  u32: name_len
  u8[name_len]: name (UTF-8, not null-terminated)
  u32: data_len
  u8[data_len]: data
```

No padding or alignment between fields. Entries are serialized in order.

### Example

A port-map with one entry `("bytes", [0xDE, 0xAD])`:

```
01 00 00 00    entry_count = 1
05 00 00 00    name_len = 5
62 79 74 65 73 name = "bytes"
02 00 00 00    data_len = 2
DE AD          data
```

Total: 19 bytes.

## Calling Convention

The host allocates the input buffer in the module's linear memory via `alloc()`, then writes the serialized port-map. The codec takes ownership of this buffer and is responsible for freeing it.

`encode` and `decode` return a packed `i64`: `(output_ptr << 32) | output_len`. The host reads the output port-map from linear memory, then calls `dealloc(output_ptr, output_len)` to free it. Return `0` to signal an error.

```
Host                              Module
  |                                  |
  |-- alloc(input_size) ------------>|
  |<------------ input_ptr ----------|
  |                                  |
  |-- Memory.write(input_ptr, data)->|
  |                                  |
  |-- encode(input_ptr, input_size)->|
  |           [module frees input]   |
  |           [module allocates out] |
  |<------ (output_ptr, output_len) -|
  |                                  |
  |-- Memory.read(output_ptr, ...) ->|
  |                                  |
  |-- dealloc(output_ptr, out_len) ->|
  |                                  |
```

## Signature Embedding

The codec's signature must be embedded in the `.wasm` binary as a `chonkle:signature` custom section at build time. The `chonkle` CLI provides a helper command to do this:

```bash
chonkle embed-signature codec.wasm signature.json
```
