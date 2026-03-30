# Codec Contract

These documents specify the interface contracts between chonkle (the host) and codec implementations. Three codec backends are supported:

- [Core Wasm](core.md)
- [Component Model Wasm](component-model.md)
- [Native (numcodecs)](native.md)

All three are wrapped by the `Codec` ABC and present a uniform interface to the executor: `call(direction, port_map)` and `signature()`.

## Call interface

Every codec exposes an `encode` and a `decode` operation. The `direction` argument to `call()` determines which is invoked. Both operations receive named inputs and produce named outputs, corresponding to the `inputs` and `outputs` declared in the codec's signature. Concretely, the inputs and outputs are passed as a **port-map**: a list of `(name, bytes)` pairs.

## Signature

Every codec must have a signature, returned by `signature()`: a JSON object declaring `codec_id`, `implementation`, `inputs`, and `outputs`. For wasm codecs, the signature is embedded in the `.wasm` binary as a `chonkle:signature` custom section. For native codecs, the signature is a bundled JSON file in `src/chonkle/signatures/numcodecs/`.

### Format

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
- `data_format` (native codecs only) — `"bytes"` or `"ndarray"`, controls the calling convention. See [native.md](native.md).

### Validation

The host validates signatures during pipeline preparation:

- Both inputs and outputs use **subset checks** — a step need not wire every port the codec declares. For example, a codec may declare an optional `level` input with a default value; a pipeline step that is happy with the default simply omits it from its `inputs`.
- Input validation is **direction-aware**: when running in decode direction, ports marked `encode_only: true` are ignored during validation and omitted from the port-map passed to the codec
