# Core ABI Contract

Core Wasm codecs are wasm32-wasi reactor modules that implement the chonkle core ABI. They use a binary port-map wire format instead of the Component Model canonical ABI, enabling direct memory access at host-native speed (~10 GB/s vs ~1.7 MB/s for the canonical ABI path).

## Required Exports

| Export | Type | Description |
|---|---|---|
| `memory` | Memory | Linear memory accessible to the host |
| `alloc` | `func(size: i32) -> i32` | Allocate `size` bytes; return pointer or 0 on failure |
| `dealloc` | `func(ptr: i32, size: i32)` | Free a buffer previously returned by `alloc` or by `encode`/`decode` |
| `encode` | `func(port_map_ptr: i32, port_map_len: i32) -> i64` | Encode operation |
| `decode` | `func(port_map_ptr: i32, port_map_len: i32) -> i64` | Decode operation |

## Port-Map Wire Format

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

### Input ownership

The host allocates the input buffer in the module's linear memory via `alloc()`, then writes the serialized port-map via `Memory.write()`. The codec takes ownership of this buffer and is responsible for freeing it.

### Return value

`encode` and `decode` return a packed `i64`:

```
i64 = (output_ptr << 32) | output_len
```

- `output_ptr` (upper 32 bits): pointer to the serialized output port-map in linear memory
- `output_len` (lower 32 bits): byte length of the serialized output

### Output ownership

The host reads the output port-map from linear memory, then calls `dealloc(output_ptr, output_len)` to free it.

### Error signaling

Return `0` (i.e., `ptr=0, len=0`) to signal an error. A future revision may add structured error reporting.

## Call Sequence

```
Host                              Module
  |                                  |
  |-- alloc(input_size) ------------>|
  |<------------ input_ptr ---------|
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

## Signature

Core wasm codecs embed a `chonkle:signature` custom section in the `.wasm` binary, identical in format to Component Model codecs. The signature is embedded as a post-build step:

```bash
chonkle embed-signature codec.wasm signature.json
```

## Relationship to Component Model WIT

The WIT interface (`chonkle:codec/transform@0.1.0`) is unchanged and applies only to Component Model codecs. Core wasm codecs use the binary port-map wire format via `Memory.read`/`Memory.write`. The executor's codec wrapper classes bridge the difference: the executor always works with `list[tuple[str, bytes]]` regardless of backend.

## Reference Implementations

### C

Reference C implementation is provided in `codec/shared/core_abi.h` and `codec/shared/core_abi.c`. See `codec/identity-core-c/` for a complete codec using these helpers.

### Rust

```rust
/// Parse a port-map from the wire format.
fn parse_port_map(data: &[u8]) -> Vec<(&str, &[u8])> {
    let mut offset = 0;
    let count = u32::from_le_bytes(data[offset..offset + 4].try_into().unwrap()) as usize;
    offset += 4;
    let mut entries = Vec::with_capacity(count);
    for _ in 0..count {
        let name_len =
            u32::from_le_bytes(data[offset..offset + 4].try_into().unwrap()) as usize;
        offset += 4;
        let name = std::str::from_utf8(&data[offset..offset + name_len]).unwrap();
        offset += name_len;
        let data_len =
            u32::from_le_bytes(data[offset..offset + 4].try_into().unwrap()) as usize;
        offset += 4;
        let port_data = &data[offset..offset + data_len];
        offset += data_len;
        entries.push((name, port_data));
    }
    entries
}

/// Serialize a port-map to the wire format.
fn serialize_port_map(entries: &[(&str, &[u8])]) -> Vec<u8> {
    let mut buf = Vec::new();
    buf.extend_from_slice(&(entries.len() as u32).to_le_bytes());
    for (name, data) in entries {
        buf.extend_from_slice(&(name.len() as u32).to_le_bytes());
        buf.extend_from_slice(name.as_bytes());
        buf.extend_from_slice(&(data.len() as u32).to_le_bytes());
        buf.extend_from_slice(data);
    }
    buf
}

/// Pack a (ptr, len) pair into a single i64 return value.
fn pack_result(ptr: u32, len: u32) -> i64 {
    ((ptr as i64) << 32) | (len as i64)
}
```
