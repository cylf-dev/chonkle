# Pipeline JSON Schema

A pipeline is a DAG of codec steps defined in a JSON file. A pipeline JSON is
an *implementation* — it describes the DAG, the wiring, and the baked-in
constants. It is not a codec signature, and its schema is not the same as a
signature's.

A pipeline is conceptually a codec: a codec signature for the pipeline can be
derived from the pipeline JSON. The input names and types come directly from the
`inputs` field, and the output types can be inferred by tracing each output
wiring reference back through the step signatures.

A pipeline can therefore appear as a step inside another pipeline. When a step
`src` resolves to a pipeline JSON rather than a `.wasm`, the executor recurses
into the nested pipeline rather than calling a Wasm component. See
`docs/PROTOSPEC_NOTES.md` for analysis of this decision.

## Top-level fields

```json
{
  "codec_id": "zstd-linear",
  "direction": "encode",
  "inputs": {
    "bytes": {"type": "bytes"},
    "dtype": {"type": "string", "encode_only": true}
  },
  "constants": {
    "level": {"type": "int", "value": 3}
  },
  "outputs": {
    "bytes": "zstd.bytes"
  },
  "steps": [...]
}
```

### `codec_id` (string, required)
The canonical identifier for this pipeline. Used in any derived codec signature
and in a future registry for resolving this pipeline as a step in outer pipelines.

### `direction` (string, required)
`"encode"` or `"decode"`. Declares which direction this pipeline definition
represents. Pipeline inversion (DAG reversal for the opposite direction) is not
yet implemented.

### `inputs` (object, required)
The pipeline's public input ports — what callers must supply. Each key is a port
name; each value is a descriptor:

- `type` (string, required): the semantic type of the port. Valid values follow
  the protospec type vocabulary (see the [Types section of the codec inventory](https://github.com/cylf-dev/protospec/blob/main/codec-inventory.md#types)): `"bytes"`, `"string"`, `"int"`, `"uint"`, `"float"`,
  `"bool"`, `"uint[]"`, `"dtype_desc"`.
- `encode_only` (bool, optional): if true, this input is only meaningful during
  encoding. It is omitted from the port-map during decode, and any derived codec
  signature will carry this annotation so that outer pipelines can handle it
  correctly.

`required` and `default` are intentionally absent. Those properties belong to a
codec's internal contract — the pipeline layer has no fallback mechanism. Optional
parameters with defaults should be expressed as `constants`.

### `constants` (object, optional)
Values baked into the pipeline definition. Constants are never supplied by the
caller. Each key is a constant name; each value is a descriptor:

- `type` (string, required): determines how the value is serialized into the
  port-map. Valid values: `"bytes"`, `"string"`, `"int"`, `"uint"`, `"float"`,
  `"bool"`, `"uint[]"`, `"dtype_desc"`.
- `value` (required): the constant's value.

Constants are always present; `required` and `encode_only` do not apply.
Encode-only routing for constant-sourced ports is handled at the step level via
`encode_only_inputs`.

Constants are a convenience for pipeline authors: they are a way of saying "I
have made this decision for the caller." Conceptually, a constant is just an
input whose value the pipeline author has pre-decided. If a codec signature were
derived from the pipeline, each constant would appear as an optional input with
the constant's value as its default — invisible to callers who don't care, but
overridable by callers who do.

### `outputs` (object, required)
Maps each pipeline output port name to the step port that produces it. The key
is the name callers use to retrieve results from the returned port-map; the value
is a wiring reference of the form `step_name.port_name`. An output port may be
renamed relative to the source step's port name. Types are not declared here —
they are derived from the source step's codec signature during validation.

### `steps` (array, required)
An ordered array of step objects. See [Steps](#steps) below.

---

## Steps

```json
{
  "name": "zstd",
  "codec_id": "zstd",
  "src": "file:///path/to/zstd_rs.wasm",
  "inputs": {
    "bytes": "input.bytes",
    "level": "constant.level"
  },
  "outputs": ["bytes"],
  "encode_only_inputs": ["level"]
}
```

### `name` (string, required)
The unique DAG node identifier for this step. Used in wiring references by
downstream steps and pipeline outputs (`step_name.port_name`). Multiple steps
may share a `codec_id` but must have distinct `name` values.

### `codec_id` (string, required)
The logical codec identifier. Matched against the codec signature's `codec_id`
during validation.

### `src` (string, required)
URI of the codec artifact. Supported schemes: `file://`, `https://`, `oci://`.
The artifact is either a `.wasm` component (leaf codec) or a pipeline JSON
(pipeline-valued codec). For `.wasm` artifacts, a `.signature.json` sidecar is
expected at the same location (or as a layer in the same OCI artifact). For
pipeline JSON artifacts, the signature is derived from the pipeline's own
`inputs` and `outputs` fields; no sidecar is required.

### `inputs` (object, required)
Maps each codec input port name to a wiring reference. The key is the port name
as declared in the codec signature; the value is a wiring reference (see
[Wiring References](#wiring-references)). No type information is included —
types are in the codec signature.

### `outputs` (array of strings, required)
The output port names this step exposes for downstream wiring. Port names must
match ports declared in the codec signature. Types are not included — they are in
the codec signature. This field is retained (rather than being omitted entirely) to enable parse-time
wiring validation: a parser can confirm that every downstream reference to
`step_name.port_name` points to a declared output without loading any signature.

### `encode_only_inputs` (array of strings, optional)
Input port names that are excluded from the port-map when running in the decode
direction. Entries must be present in `inputs`. Typically used for tuning
parameters (compression level, acceleration factor) that are only meaningful
during encoding.

---

## Wiring References

A wiring reference is a dot-notation string identifying the source of a value:

| Form | Meaning |
|---|---|
| `input.<port>` | A pipeline-level input port |
| `constant.<name>` | A pipeline-level constant |
| `<step_name>.<port>` | An output port of a named step |

All wiring references are validated at parse time against declared inputs,
constants, and step outputs. Signature-level type compatibility (checking that
the source type matches the destination type) is validated in a separate
pre-execution pass once all codec signatures are loaded.

---

## Divergences from the Protospec

The protospec pipeline format is the upstream reference. The following changes
were made deliberately.

### `steps` is an array, not an object

**Protospec**:
`"steps": { "step_name": { ... } }`

**Ours**:
`"steps": [ { "name": "step_name", ... } ]`

JSON objects do not have a guaranteed key order in all parsers and languages.
Using an array makes execution order explicit and unambiguous without relying
on insertion order. The step name is moved into the object as the `name` field.

### `"codec"` renamed to `"codec_id"` in steps

**Protospec**:
`"codec": "zstd"`

**Ours**:
`"codec_id": "zstd"`

`codec_id` is the consistent term for a codec identifier throughout the rest
of the schema (top-level `codec_id`, codec signatures). Using `codec` in steps
would be the only place the shorter form appeared.

### `src` added to steps

**Protospec**:
no equivalent

**Ours**:
`"src": "file:///path/to/codec.wasm"`

The protospec assumes a registry from which codecs are resolved by `codec_id`.
No such registry exists yet. `src` provides a direct URI to the `.wasm` file
and will become optional once a registry is in place.

### `name` added to steps

**Protospec**:
step name is the object key

**Ours**:
`"name": "step_name"` inside the step object

A direct consequence of switching `steps` to an array. The name must live
somewhere inside the object; `name` is the natural field for it.

### Step `outputs` is an array of port names, not an object

**Protospec**:
`"outputs": { "bytes": "raw_uints" }` — an object mapping codec
port names to local wiring aliases

**Ours**:
`"outputs": ["bytes"]` — a list of port names

The protospec's alias mechanism adds a rename layer between a step's codec port
name and how downstream steps refer to it (`step_name.alias`). Our wiring
references use the codec port name directly (`step_name.port_name`), making the
alias layer unnecessary. Removing it also removes the only reason for the value
side of the object. A list of port names is sufficient: it keeps the pipeline
human-readable without signatures, and enables parse-time wiring validation.

### `encode_only` on pipeline inputs

**Protospec**:
pipeline inputs have only `type`; `encode_only` does not appear
on pipeline inputs in any protospec example

**Ours**:
`encode_only` is permitted on pipeline inputs

A pipeline's derived codec signature needs `encode_only` annotations so that
outer pipelines know which inputs to omit during decode. The protospec does not show this on pipeline
inputs, but it is a logical extension of the same property on codec signature
inputs.

### `encode_only_inputs` added to steps

**Protospec**:
`encode_only` is a property of codec signature inputs, not pipeline
steps

**Ours**:
`"encode_only_inputs": ["level"]` on the step

The protospec records `encode_only` in the codec signature. Our executor needs
to know which inputs to skip during decode at parse time, before signatures are
loaded. Surfacing this on the step makes the pipeline self-contained for routing
purposes and allows validation at parse time. It is expected to be consistent
with the codec signature; a future validation pass may enforce this.
