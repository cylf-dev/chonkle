# Component Model Wasm

Component Model codecs implement the `chonkle:codec/transform@0.1.0` WIT interface. For build guides, see [Rust](../../guides/COMPONENT_MODEL_RUST.md) or [C](../../guides/COMPONENT_MODEL_C.md). The toolchain (e.g. `cargo-component`, `wit-bindgen`, `wasm-tools component new`) generates all memory management and canonical ABI glue. Codec authors work only with high-level types.

## WIT interface

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

The toolchain embeds this under the key `"chonkle:codec/transform@0.1.0"` — the host looks up this exact key in the component's exports. Components must target **WASIp2**. Return `Err(string)` to signal failure; return `Ok(port-map)` on success.

## Signature embedding

The codec's signature must be embedded in the `.wasm` binary as a `chonkle:signature` custom section at build time. The `chonkle` CLI provides a helper command to do this:

```bash
chonkle embed-signature codec.wasm signature.json
```
