# Codec Contract

This document specifies the interface contract between chonkle (the host) and a `.wasm` codec module. All codecs are Wasm components implementing the `chonkle:codec/transform` interface defined in `wit/codec.wit`.

## Component interface

Component codecs use the WIT/Canonical ABI; the toolchain generates all memory management glue. Codec authors work only with high-level types.

### WIT interface

A component must export the `chonkle:codec/transform` interface. The full WIT definition:

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

Codec parameters (constants) arrive as named port entries serialized as UTF-8 JSON bytes. A codec that accepts a compression level, for example, would read a `"level"` port entry and deserialize its bytes as JSON.

### Export location

A compiled codec component must export the `transform` interface under the key `"chonkle:codec/transform"`. This is the key that the Component Model toolchain (`cargo-component`, `componentize-py`, etc.) embeds when a component is built against `codec.wit`.

`executor.py` looks up this exact key in the component's exports (via the `CODEC_TRANSFORM_IFACE` constant in `executor.py`) and retrieves `encode` or `decode` from within it. If the key is absent the component is rejected with a `RuntimeError`. Components not compiled against `codec.wit` will not have this key and will fail at this point.

### Runtime requirements

Components must be **WASIp2-compatible**. The host calls `linker.add_wasip2()` unconditionally; components that do not target WASIp2 will fail to instantiate.

### Error signaling

Return the `Err` variant of `result<port-map, string>` to signal failure. The host raises `RuntimeError` with the error string as the message. Return `Ok(port-map)` on success.
