# Codec ABI

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

## Calling convention

The host (`wasm_runner.py`) performs the following sequence for each codec call (`decode` or `encode`):

1. **Allocate and write input** — Call `alloc(len(data))`, then write the raw input bytes into linear memory at the returned pointer.
2. **Allocate and write config** — Serialize the configuration dict as a JSON string, call `alloc(len(json_bytes))`, then write the JSON bytes into linear memory.
3. **Call the codec function** (`decode` or `encode`) — Pass the four arguments: `input_ptr`, `input_len`, `config_ptr`, `config_len`.
4. **Unpack the result** — The return value is a single `i64` encoding both the output pointer and length: `out_ptr = (result >> 32) & 0xFFFFFFFF`, `out_len = result & 0xFFFFFFFF`.
5. **Read output** — Copy `out_len` bytes from linear memory starting at `out_ptr`.
6. **Deallocate** — Call `dealloc` three times to free the input, config, and output buffers.

## Error signaling

If the codec function returns `out_ptr = 0` and `out_len = 0`, the host treats this as a failure and raises a `RuntimeError`.
