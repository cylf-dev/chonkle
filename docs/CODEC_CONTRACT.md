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

The `transform` interface must be exported from the `codec` world. `executor.py` discovers `encode`/`decode` at runtime by introspecting the component's type, searching world-level exports first then interface-level exports. The WIT package name does not need to be known in advance.

### Runtime requirements

- Components must be **WASIp2-compatible**. The host calls `linker.add_wasip2()` unconditionally; components that do not target WASIp2 will fail to instantiate.

### Error signaling

Return the `Err` variant of `result<port-map, string>` to signal failure. The host raises `RuntimeError` with the error string as the message. Return `Ok(port-map)` on success.
