# Building a Core Wasm Codec

This guide walks through building a Core Wasm codec using C and the Zig build system, following the structure of the [identity-core-c](../../codec/identity-core-c/) codec.

For the contract specification, see [reference/codec-contract/CORE.md](../reference/codec-contract/CORE.md).

## When to use Core Wasm

Core Wasm bypasses the Component Model canonical ABI. In the Python host, this means ~10 GB/s host-to-module transfer vs ~1.7 MB/s for Component Model codecs, because data is written directly into linear memory instead of going through the canonical ABI's lifting and lowering. The trade-off is a lower-level interface: you manage linear memory allocation and work with a binary port-map wire format.

Choose Core Wasm when transfer throughput matters (large buffers, hot paths). Choose Component Model when development simplicity matters. See [design/DATA_COPIES.md](../design/DATA_COPIES.md) and [design/CANONICAL_ABI_PERF.md](../design/CANONICAL_ABI_PERF.md) for the full performance analysis.

## Prerequisites

- [Zig](https://ziglang.org/) (provides a C cross-compiler targeting `wasm32-wasi`)
- chonkle CLI (for signature embedding): `pip install chonkle`

Note: no `wasm-tools` needed — Core Wasm codecs are not lifted to Component Model.

## Project structure

```
codec/my-codec-core-c/
  build.zig          # single-step build: compile C to wasm32-wasi reactor
  codec_impl.c       # your alloc/dealloc/encode/decode exports
  signature.json     # port/parameter declarations embedded into the .wasm
```

Your codec depends on shared infrastructure in `codec/shared/`:

- `core_abi.h` / `core_abi.c` — port-map parse, serialize, find, and pack helpers

## Step 1: Understand the required exports

A Core Wasm codec must export five symbols:

| Export | Signature | Description |
|---|---|---|
| `memory` | Memory | Linear memory (provided by the wasm toolchain) |
| `alloc` | `(size: i32) -> i32` | Allocate `size` bytes, return pointer (or 0) |
| `dealloc` | `(ptr: i32, size: i32)` | Free a buffer |
| `encode` | `(port_map_ptr: i32, port_map_len: i32) -> i64` | Encode operation |
| `decode` | `(port_map_ptr: i32, port_map_len: i32) -> i64` | Decode operation |

The host calls `alloc` to place input data in the module's memory. `encode`/`decode` take ownership of the input buffer (must free it) and return a packed `i64` of `(output_ptr << 32) | output_len`. The host reads the output, then calls `dealloc` to free it.

## Step 2: Understand the port-map wire format

Inputs and outputs are serialized as a binary port-map:

```
u32: entry_count
For each entry:
  u32: name_len
  u8[name_len]: name (UTF-8)
  u32: data_len
  u8[data_len]: data
```

All integers are little-endian. No padding between fields.

The `core_abi.h` helpers handle this for you:

```c
// Parse a serialized port-map. Returned entries point into the input buffer
// (zero-copy). Returns count=0 on error.
core_abi_port_map_t core_abi_parse_port_map(const uint8_t *buf, uint32_t len);

// Look up a port by name.
const core_abi_port_t *core_abi_find_port(const core_abi_port_map_t *pm, const char *name);

// Serialize a port-map to a new malloc'd buffer.
uint8_t *core_abi_serialize_port_map(const core_abi_port_map_t *pm, uint32_t *out_len);

// Pack (ptr, len) into a single i64 return value.
int64_t core_abi_pack_result(uint32_t ptr, uint32_t len);

// Free a parsed port-map's entries array.
void core_abi_free_port_map(core_abi_port_map_t *pm);
```

## Step 3: Write codec_impl.c

Here is the complete `codec_impl.c` from the identity-core codec:

```c
#include "../shared/core_abi.h"
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

__attribute__((__export_name__("alloc")))
int32_t codec_alloc(int32_t size) {
    void *ptr = malloc((size_t)size);
    return (int32_t)(uintptr_t)ptr;
}

__attribute__((__export_name__("dealloc")))
void codec_dealloc(int32_t ptr, int32_t size) {
    (void)size;
    free((void *)(uintptr_t)ptr);
}

static int64_t transform(int32_t pm_ptr, int32_t pm_len) {
    uint8_t *input_buf = (uint8_t *)(uintptr_t)pm_ptr;
    core_abi_port_map_t pm = core_abi_parse_port_map(input_buf, (uint32_t)pm_len);

    if (pm.count == 0) {
        free(input_buf);
        return CORE_ABI_ERROR;
    }

    const core_abi_port_t *bytes_port = core_abi_find_port(&pm, "bytes");
    if (!bytes_port) {
        core_abi_free_port_map(&pm);
        free(input_buf);
        return CORE_ABI_ERROR;
    }

    /* Copy input bytes before freeing the input buffer. */
    uint32_t out_data_len = bytes_port->data_len;
    uint8_t *out_data = malloc(out_data_len);
    if (!out_data) {
        core_abi_free_port_map(&pm);
        free(input_buf);
        return CORE_ABI_ERROR;
    }
    memcpy(out_data, bytes_port->data, out_data_len);

    core_abi_free_port_map(&pm);
    free(input_buf);

    /* Build output port-map with a single "bytes" port. */
    core_abi_port_t out_entry;
    out_entry.name = "bytes";
    out_entry.name_len = 5;
    out_entry.data = out_data;
    out_entry.data_len = out_data_len;

    core_abi_port_map_t out_pm;
    out_pm.entries = &out_entry;
    out_pm.count = 1;

    uint32_t ser_len;
    uint8_t *ser_buf = core_abi_serialize_port_map(&out_pm, &ser_len);
    free(out_data);

    if (!ser_buf) return CORE_ABI_ERROR;

    return core_abi_pack_result((uint32_t)(uintptr_t)ser_buf, ser_len);
}

__attribute__((__export_name__("encode")))
int64_t encode(int32_t pm_ptr, int32_t pm_len) {
    return transform(pm_ptr, pm_len);
}

__attribute__((__export_name__("decode")))
int64_t decode(int32_t pm_ptr, int32_t pm_len) {
    return transform(pm_ptr, pm_len);
}
```

The pattern:

1. **`alloc` / `dealloc`**: Thin wrappers around `malloc`/`free` with the `__export_name__` attribute. Copy verbatim.
2. **Parse input**: Cast the pointer, call `core_abi_parse_port_map`, look up ports with `core_abi_find_port`.
3. **Memory ownership**: The codec owns the input buffer — copy any data you need, then `free(input_buf)` and `core_abi_free_port_map`. The parsed port entries point into the input buffer (zero-copy parse), so you must copy data before freeing.
4. **Do the transformation**: Replace the `memcpy` with your codec logic.
5. **Build output**: Construct a `core_abi_port_map_t`, serialize it with `core_abi_serialize_port_map`, free intermediate buffers, and return `core_abi_pack_result(ptr, len)`.
6. **Error handling**: Return `CORE_ABI_ERROR` (0) on any failure.

## Step 4: Write build.zig

The build is a single step — no Component Model lifting:

```zig
const std = @import("std");

pub fn build(b: *std.Build) void {
    const compile = b.addSystemCommand(&.{
        b.graph.zig_exe,
        "cc",
        "--target=wasm32-wasi",
        "-mexec-model=reactor",
        "-fvisibility=hidden",
        "-lc",
        "-O2",
    });

    compile.addArg("-o");
    const core_wasm = compile.addOutputFileArg("my-codec-core.wasm");
    compile.addFileArg(b.path("../shared/core_abi.c"));
    compile.addFileArg(b.path("codec_impl.c"));

    b.getInstallStep().dependOn(
        &b.addInstallFile(core_wasm, "my-codec-core.wasm").step,
    );
}
```

Compared to the Component Model C build: no `codec.c`, no `codec_component_type.o`, no `wasm-tools component new` step, no WASI adapter. Just `core_abi.c` + your codec.

## Step 5: Write the signature

```json
{
  "codec_id": "my-codec",
  "implementation": "my-codec-core-c",
  "inputs": {"bytes": {"type": "bytes", "required": true}},
  "outputs": {"bytes": {"type": "bytes"}}
}
```

Same format as Component Model signatures.

## Step 6: Build and embed

```bash
cd codec/my-codec-core-c
zig build
cp zig-out/my-codec-core.wasm my-codec-core.wasm
chonkle embed-signature my-codec-core.wasm signature.json
```

## Step 7: Verify

```bash
chonkle install my-codec-core.wasm
chonkle list-codecs
```
