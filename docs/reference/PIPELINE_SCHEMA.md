# Pipeline JSON Schema

A pipeline is a DAG of codec steps defined in a JSON file. A pipeline JSON is an *implementation* -- it describes the DAG, the wiring, and the baked-in constants. It is not a codec signature, and its schema is not the same as a signature's.

However, a pipeline is conceptually a codec: a codec signature for the pipeline can be derived from the pipeline JSON. The input names and types come directly from the `inputs` field; constants appear as optional inputs with their baked-in value as the default; and output types are inferred by tracing each output wiring reference back through the step signatures.

## Pipeline Object

```json
{
  "codec_id": "cog-decode",
  "direction": "decode",
  "sources": {
    "tiff-predictor-2": "oci://ghcr.io/nasa-impact/codec-tiff-predictor-2-c:v1.0"
  },
  "inputs": {"bytes": {"type": "bytes"}},
  "constants": {
    "bytes_per_sample": {"type": "int", "value": 2},
    "width": {"type": "int", "value": 1024}
  },
  "outputs": {"bytes": "predictor2.bytes"},
  "steps": {
    "zlib": {
      "codec_id": "zlib",
      "inputs": {"bytes": "input.bytes"}
    },
    "predictor2": {
      "codec_id": "tiff-predictor-2",
      "inputs": {
        "bytes": "zlib.bytes",
        "bytes_per_sample": "constant.bytes_per_sample",
        "width": "constant.width"
      }
    }
  }
}
```

### `codec_id` (string, required)

The canonical identifier for this pipeline. A pipeline is itself a codec per protospec. Used in any derived codec signature and in a future registry for resolving this pipeline as a step in outer pipelines.

### `direction` (string, required)

`"encode"` or `"decode"`. Declares which direction this pipeline definition represents. Pass a `direction` to `run()` that differs from `pipeline.direction` to execute the pipeline in the opposite direction (inverted step order, opposite encode/decode function).

### `sources` (object, optional)

Maps codec_ids to download URIs for codec artifacts. Supported URI schemes: `file://`, `https://`, `oci://` (`http://` raises `ValueError`). These are advisory fetch hints for the `Resolver`: if a codec_id is not found in the local store or via other resolution mechanisms, the resolver downloads from the URI listed here.

### `inputs` (object, required)

The pipeline's public input ports -- what callers must supply. Each key is a port name; each value is a descriptor:

- `type` (string, required): the semantic type of the port. Valid values follow the protospec type vocabulary (see the [Types section of the codec inventory](https://github.com/cylf-dev/protospec/blob/main/codec-inventory.md#types)): `"bytes"`, `"string"`, `"int"`, `"uint"`, `"float"`, `"bool"`, `"uint[]"`, `"dtype_desc"`.
- `encode_only` (bool, optional): if true, this input is only meaningful during encoding. It is omitted from the port-map during decode, and any derived codec signature will carry this annotation so that outer pipelines can handle it correctly.
- `decode_only` (bool, optional): if true, this input is only meaningful during decoding. It is omitted from the port-map during encode. The symmetric counterpart to `encode_only`.

`required` and `default` are absent (unlike codec signature inputs, which carry both). All pipeline inputs are caller-supplied -- there is no fallback at the pipeline boundary. Parameters with baked-in values belong in `constants`.

### `constants` (object, optional)

Values baked into the pipeline definition. Constants are never supplied by the caller. Each key is a constant name; each value is a descriptor:

- `type` (string, required): determines how the value is serialized into the port-map. Valid values: `"bytes"`, `"string"`, `"int"`, `"uint"`, `"float"`, `"bool"`, `"uint[]"`, `"dtype_desc"`.
- `value` (required): the constant's value.

Constants use the `constant.<name>` wiring namespace, which is distinct from `input.<name>`. During pipeline inversion, pipeline inputs become pipeline outputs (and vice versa), but constants remain constants -- available as configuration in both directions.

### `outputs` (object, required)

Maps each pipeline output port name to the step port that produces it. The key is the name callers use to retrieve results from the returned port-map; the value is a wiring reference of the form `step_name.port_name`. An output port may be renamed relative to the source step's port name. Types are not declared here -- they are derived from the source step's codec signature during validation.

### `steps` (object, required)

A named map of step objects. Keys are step names (unique DAG node identifiers used in wiring references). Order in JSON does not matter -- topological sort determines execution order. See [Steps](#steps) below.

## Steps

Each step invokes a codec. The step's `codec_id` resolves to a codec signature, which defines the port names, types, and encode-only annotations that the step's wiring must satisfy.

```json
"steps": {
  "zlib": {
    "codec_id": "zlib",
    "inputs": {"bytes": "input.bytes"}
  },
  "predictor2": {
    "codec_id": "tiff-predictor-2",
    "inputs": {
      "bytes": "zlib.bytes",
      "bytes_per_sample": "constant.bytes_per_sample",
      "width": "constant.width"
    }
  }
}
```

Each key in the `steps` object is the step name -- the unique DAG node identifier used in wiring references (`step_name.port_name`). Multiple steps may share a `codec_id` but must have distinct keys.

### `codec_id` (string, required)

The logical codec identifier. The `Resolver` maps this to a `Codec` instance. Matched against the codec signature's `codec_id` during validation.

### `inputs` (object, required)

Maps each codec input port name to a wiring reference. The key is the port name as declared in the codec signature; the value is a wiring reference (see [Wiring References](#wiring-references)). No type information is included—types come from the codec signature.

## Wiring References

A wiring reference is a dot-notation string identifying the source of a value:

| Form | Meaning |
|---|---|
| `input.<port>` | A pipeline-level input port |
| `constant.<name>` | A pipeline-level constant |
| `<step_name>.<port>` | An output port of a named step |

Wiring references are parsed into `WiringRef` objects at pipeline construction time. Step existence and input/constant refs are validated at parse time. Step output port refs (references to `<step_name>.<port>`) are validated at `prepare()` time against codec signatures.

## Codec Type Detection

The codec type for each step is not declared in the pipeline schema. It is detected from the resolved binary by reading the 8-byte wasm header:

- `.wasm` with core version header (`01 00 00 00`) -- `CoreWasmCodec`
- `.wasm` with component version header (`0d 00 01 00`) -- `ComponentCodec`
- No wasm binary (numcodecs registry) -- `NativeCodec`

## Divergences from the Protospec

The protospec pipeline format is the upstream reference. The following changes were made deliberately.

### `sources` (chonkle addition)

**Protospec**: no equivalent (codec resolution is implicit via registry)

**Ours**: `"sources": {"codec_id": "oci://..."}` at the pipeline level

The protospec assumes codecs are resolved from a registry by `codec_id`. Chonkle doesn't have a registry yet, so `sources` gives the resolver advisory download hints for codecs not available locally.

### `"codec"` renamed to `"codec_id"` in steps

**Protospec**: `"codec": "zstd"`

**Ours**: `"codec_id": "zstd"`

`codec_id` is the consistent term for a codec identifier throughout the rest of the schema (top-level `codec_id`, codec signatures). Using `codec` in steps would be the only place the shorter form appeared.

### No `outputs` on steps

**Protospec**: steps have an `"outputs"` field that aliases output port names, e.g. `"outputs": {"bytes": "raw_uints"}`. Other steps reference the alias (`decode_varint.raw_uints`), not the codec signature name.

**Ours**: steps have only `inputs`. Output ports are referenced by their codec signature names directly (e.g., `decode_varint.bytes`). Output port names are validated at `prepare()` time against the codec signature.

### `encode_only` on pipeline inputs

**Protospec**: pipeline inputs have only `type`; `encode_only` does not appear on pipeline inputs in any protospec example

**Ours**: `encode_only` is permitted on pipeline inputs

A pipeline's derived codec signature needs `encode_only` annotations so that outer pipelines know which inputs to omit during decode. The protospec does not show this on pipeline inputs, but it is a logical extension of the same property on codec signature inputs.

### `decode_only` on codec signature and pipeline inputs

**Protospec**: no `decode_only` property exists in the signature model

**Ours**: `decode_only` is supported on codec signature inputs and pipeline inputs

The protospec has `encode_only` but no symmetric counterpart for decode. Chonkle adds `decode_only` to handle codecs with inputs meaningful only during decoding (e.g. packbits, where `decode_dtype` is only needed during decode). The executor omits `decode_only` ports from the port-map during encode, mirroring the `encode_only` behavior during decode. See the [signature model gap analysis](protospec/PROTOSPEC_NOTES.md#no-decode_only-property) for catalog examples where the protospec lacks this capability.
