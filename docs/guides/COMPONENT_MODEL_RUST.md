# Building a Component Model Codec in Rust

This guide walks through building a Component Model codec using Rust and `cargo-component`, following the structure of the [zlib-rs](../../codec/zlib-rs/) codec.

For the contract specification, see [reference/codec-contract/COMPONENT_MODEL.md](../reference/codec-contract/COMPONENT_MODEL.md).

## Prerequisites

- Rust (edition 2024) with the `wasm32-wasip2` target: `rustup target add wasm32-wasip2`
- [cargo-component](https://github.com/bytecodealliance/cargo-component): `cargo install cargo-component`
- chonkle CLI (for signature embedding): `pip install chonkle`

## Project structure

```
codec/my-codec-rs/
  Cargo.toml        # crate config: cdylib + component metadata
  src/lib.rs        # codec implementation
  wit/world.wit     # WIT interface definition (same for all codecs)
  signature.json    # port/parameter declarations embedded into the .wasm
```

## Step 1: Create the WIT world file

Create `wit/world.wit` with the canonical codec interface. This file is identical for every Component Model codec:

```wit
package chonkle:codec@0.1.0;

interface transform {
    /// A named byte buffer — one port in the port map.
    type port-name = string;
    type port-map = list<tuple<port-name, list<u8>>>;

    encode: func(inputs: port-map) -> result<port-map, string>;
    decode: func(inputs: port-map) -> result<port-map, string>;
}

world codec {
    export transform;
}
```

This declares two functions, `encode` and `decode`, that take a list of named byte buffers (the port-map) and return a transformed port-map or an error string. See [docs/wasm/WIT.md](../wasm/WIT.md) for WIT syntax background.

## Step 2: Configure Cargo.toml

```toml
[package]
name = "my-codec"
version = "0.1.0"
edition = "2024"

[lib]
crate-type = ["cdylib"]

[dependencies]
wit-bindgen-rt = { version = "0.44.0", features = ["bitflags"] }
# Add your codec library here, e.g.:
# flate2 = { version = "1", default-features = false, features = ["rust_backend"] }

[package.metadata.component]
package = "chonkle:codec"

[package.metadata.component.dependencies]
```

Key points:
- `crate-type = ["cdylib"]` produces a WebAssembly dynamic library.
- `wit-bindgen-rt` provides the runtime support for the generated WIT bindings.
- `[package.metadata.component]` tells `cargo-component` which WIT package this crate implements.
- Use `default-features = false` and pure-Rust backends for dependencies where possible, since native C libraries won't cross-compile to wasm32.

## Step 3: Implement encode and decode

`cargo-component` auto-generates a `bindings` module from your WIT file. Your code imports types from it and implements the `Guest` trait.

Here is the complete `src/lib.rs` from the zlib codec:

```rust
#[allow(warnings)]
mod bindings;

use bindings::exports::chonkle::codec::transform::{Guest, PortMap};
use flate2::read::ZlibDecoder;
use flate2::write::ZlibEncoder;
use flate2::Compression;
use std::io::{Read, Write};

struct Component;

fn find_port<'a>(inputs: &'a PortMap, name: &str) -> Option<&'a Vec<u8>> {
    inputs.iter().find(|(k, _)| k == name).map(|(_, v)| v)
}

impl Guest for Component {
    fn encode(inputs: PortMap) -> Result<PortMap, String> {
        let bytes = find_port(&inputs, "bytes")
            .ok_or_else(|| "missing port: bytes".to_string())?;

        let level = find_port(&inputs, "level")
            .and_then(|v| std::str::from_utf8(v).ok())
            .and_then(|s| s.trim().parse::<u32>().ok())
            .unwrap_or(6)
            .min(9);

        let mut enc = ZlibEncoder::new(Vec::new(), Compression::new(level));
        enc.write_all(bytes).map_err(|e| e.to_string())?;
        let compressed = enc.finish().map_err(|e| e.to_string())?;

        Ok(vec![("bytes".to_string(), compressed)])
    }

    fn decode(inputs: PortMap) -> Result<PortMap, String> {
        let bytes = find_port(&inputs, "bytes")
            .ok_or_else(|| "missing port: bytes".to_string())?;

        let mut dec = ZlibDecoder::new(bytes.as_slice());
        let mut out = Vec::new();
        dec.read_to_end(&mut out).map_err(|e| e.to_string())?;

        Ok(vec![("bytes".to_string(), out)])
    }
}

bindings::export!(Component with_types_in bindings);
```

The pattern:

1. **Boilerplate**: `mod bindings`, `struct Component`, `bindings::export!()` — same for every codec.
2. **`find_port` helper**: Looks up a named port in the input port-map. You'll reuse this pattern in every codec.
3. **`encode`**: Extract the required `bytes` port. Read optional parameters (here, `level`). Parameters arrive as UTF-8 byte strings and need parsing. Do the transformation. Return the output port-map.
4. **`decode`**: Extract `bytes`, reverse the transformation, return.
5. **Error handling**: Return `Err(String)` to signal failure. The host surfaces this as a codec error.

## Step 4: Write the signature

Create `signature.json` declaring the codec's ports and parameters:

```json
{
  "codec_id": "my-codec",
  "implementation": "my-codec-rs",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "level": {"type": "int", "required": false, "default": 6, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```

- `codec_id` — logical name used in pipeline JSON to reference this codec.
- `implementation` — identifies this specific build. Used as the filename in the codec store.
- `inputs` — declares each input port. `required: false` ports can be omitted by pipeline steps; the host uses `default` when they are. `encode_only: true` ports are ignored during decode.
- `outputs` — declares each output port.

See [reference/codec-contract/README.md](../reference/codec-contract/README.md) for the full signature specification.

## Step 5: Build

```bash
cd codec/my-codec-rs
cargo component build --target wasm32-wasip2 --release
```

This compiles the Rust code, generates WIT bindings, and produces a Component Model `.wasm` binary. The output path is printed by cargo; for zlib-rs it lands at `target/wasm32-wasip1/release/zlib.wasm`.

## Step 6: Embed the signature

```bash
cp target/wasm32-wasip1/release/my-codec.wasm my-codec.wasm
chonkle embed-signature my-codec.wasm signature.json
```

This writes `signature.json` into the `.wasm` binary as a `chonkle:signature` custom section. The host reads this section at runtime to discover the codec's ports and parameters.

## Step 7: Verify

Install the codec into the local codec store and confirm it appears:

```bash
chonkle install my-codec.wasm
chonkle list-codecs
```

To test a roundtrip, create a minimal pipeline JSON and run it:

```json
{
  "steps": [
    {
      "codec_id": "my-codec",
      "inputs": {"bytes": "input"},
      "outputs": {"bytes": "output"}
    }
  ]
}
```

## Common patterns

**Optional parameters with defaults**: Declare the port with `required: false` and a `default` value in the signature. In the Rust code, use `unwrap_or()` when parsing the port value.

**Encode-only parameters**: Parameters like compression level only apply during encoding. Mark them `encode_only: true` in the signature; the host strips them from the port-map during decode, so your decode function never sees them.

**Multiple output ports**: Return multiple entries in the output `Vec`. Each `(name, bytes)` pair becomes a named output port that downstream steps can wire to.
