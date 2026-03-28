# Pipeline JSON Schema

A pipeline is a DAG of codec steps defined in a JSON file. A pipeline JSON is an *implementation* -- it describes the DAG, the wiring, and the baked-in constants. It is not a codec signature, and its schema is not the same as a signature's.

However, a pipeline is conceptually a codec: a codec signature for the pipeline can be derived from the pipeline JSON. The input names and types come directly from the `inputs` field; constants appear as optional inputs with their baked-in value as the default; and output types are inferred by tracing each output wiring reference back through the step signatures.

## Top-level fields

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

### `inputs` (object, required)

The pipeline's public input ports -- what callers must supply. Each key is a port name; each value is a descriptor:

- `type` (string, required): the semantic type of the port. Valid values follow the protospec type vocabulary (see the [Types section of the codec inventory](https://github.com/cylf-dev/protospec/blob/main/codec-inventory.md#types)): `"bytes"`, `"string"`, `"int"`, `"uint"`, `"float"`, `"bool"`, `"uint[]"`, `"dtype_desc"`.
- `encode_only` (bool, optional): if true, this input is only meaningful during encoding. It is omitted from the port-map during decode, and any derived codec signature will carry this annotation so that outer pipelines can handle it correctly.

`required` and `default` are absent. All pipeline inputs are caller-supplied -- there is no fallback at the pipeline boundary. Parameters with baked-in values belong in `constants`.

### `constants` (object, optional)

Values baked into the pipeline definition. Constants are never supplied by the caller. Each key is a constant name; each value is a descriptor:

- `type` (string, required): determines how the value is serialized into the port-map. Valid values: `"bytes"`, `"string"`, `"int"`, `"uint"`, `"float"`, `"bool"`, `"uint[]"`, `"dtype_desc"`.
- `value` (required): the constant's value.

Constants use the `constant.<name>` wiring namespace, which is distinct from `input.<name>`. During pipeline inversion, pipeline inputs become pipeline outputs (and vice versa), but constants remain constants -- available as configuration in both directions.

### `sources` (object, optional)

Maps codec_ids to download URIs for codec artifacts. Supported URI schemes: `file://`, `https://`, `oci://` (`http://` raises `ValueError`). These are advisory fetch hints for the `Resolver`: if a codec_id is not found in the local store or via other resolution mechanisms, the resolver downloads from the URI listed here.

### `outputs` (object, required)

Maps each pipeline output port name to the step port that produces it. The key is the name callers use to retrieve results from the returned port-map; the value is a wiring reference of the form `step_name.port_name`. An output port may be renamed relative to the source step's port name. Types are not declared here -- they are derived from the source step's codec signature during validation.

### `steps` (object, required)

A named map of step objects. Keys are step names (unique DAG node identifiers used in wiring references). Order in JSON does not matter -- topological sort determines execution order. See [Steps](#steps) below.

---

## Steps

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

Maps each codec input port name to a wiring reference. The key is the port name as declared in the codec signature; the value is a wiring reference (see [Wiring References](#wiring-references)). No type information is included -- types come from the codec signature.

### Fields absent from steps

- **`src`**: removed. Implementation selection is the resolver's responsibility. The pipeline-level `sources` field provides optional download hints, but the resolver controls which backend (core wasm, component model, or native) is used for each codec_id.
- **`outputs`**: removed. Output port names come from codec signatures and are validated at `prepare()` time against wiring references that target a step's outputs.
- **`encode_only_inputs`**: removed. Encode-only ports are derived from codec signature `encode_only` fields at `prepare()` time. `PreparedPipeline` precomputes the encode-only input set for each step.

---

## Wiring References

A wiring reference is a dot-notation string identifying the source of a value:

| Form | Meaning |
|---|---|
| `input.<port>` | A pipeline-level input port |
| `constant.<name>` | A pipeline-level constant |
| `<step_name>.<port>` | An output port of a named step |

Wiring references are parsed into `WiringRef` objects at pipeline construction time. Step existence and input/constant refs are validated at parse time. Step output port refs (references to `<step_name>.<port>`) are validated at `prepare()` time against codec signatures.

---

## Codec Type Detection

The codec type for each step is not declared in the pipeline schema. It is detected from the resolved binary by reading the 8-byte wasm header:

- `.wasm` with core version header (`01 00 00 00`) -- `CoreWasmCodec`
- `.wasm` with component version header (`0d 00 01 00`) -- `ComponentCodec`
- No wasm binary (numcodecs registry) -- `NativeCodec`

---

## Divergences from the Protospec

The protospec pipeline format is the upstream reference. The following changes were made deliberately.

### `sources` at pipeline level instead of `src` per step

**Protospec**: no equivalent (codec resolution is implicit via registry)

**Ours**: `"sources": {"codec_id": "oci://..."}` at the pipeline level

The protospec assumes a registry from which codecs are resolved by `codec_id`. `sources` provides advisory download URIs for the resolver when a codec is not available locally. This is a pipeline-level map rather than a per-step field because multiple steps may share the same codec_id, and implementation selection (which backend to use) is the resolver's concern, not the pipeline's.

### `"codec"` renamed to `"codec_id"` in steps

**Protospec**: `"codec": "zstd"`

**Ours**: `"codec_id": "zstd"`

`codec_id` is the consistent term for a codec identifier throughout the rest of the schema (top-level `codec_id`, codec signatures). Using `codec` in steps would be the only place the shorter form appeared.

### No `configuration` field

**Protospec**: `"configuration": {"level": 3}` on steps

**Ours**: absent

All codec parameters flow through port-maps via pipeline-level constants. A step wires `"level": "constant.level"` in its `inputs`. This keeps the data flow uniform -- every value a codec receives arrives through the same port-map mechanism.

### `encode_only` is a codec signature property

**Protospec**: `encode_only` is a property of codec signature inputs

**Ours**: same, but the pipeline schema does not redeclare it on steps

Encode-only routing is derived from codec signatures at `prepare()` time and precomputed on `PreparedPipeline`. The pipeline JSON does not carry `encode_only_inputs` on steps. Pipeline-level inputs may carry `encode_only: true` for derived codec signature purposes.

### `encode_only` on pipeline inputs

**Protospec**: pipeline inputs have only `type`; `encode_only` does not appear on pipeline inputs in any protospec example

**Ours**: `encode_only` is permitted on pipeline inputs

A pipeline's derived codec signature needs `encode_only` annotations so that outer pipelines know which inputs to omit during decode. The protospec does not show this on pipeline inputs, but it is a logical extension of the same property on codec signature inputs.
