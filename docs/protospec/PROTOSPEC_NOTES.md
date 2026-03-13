# Protospec Notes

Commentary and clarifications on `PROTOSPEC.md`. That document is an exact copy
of an upstream reference and is not modified here.

---

## Codec signatures are written in decode direction by convention

The protospec states "Individual codecs do not have a direction — every codec is
bidirectional by definition." This is defensible but misleading without
qualification.

All codec signatures in the catalog are written in **decode direction by
convention**. By this convention, inputs represent the encoded/compressed form
and outputs represent the decoded/raw form. The encode direction is the implied
inverse. For `bytes → bytes` codecs this convention is not visible in the
signature itself — both sides have the same type — it is only apparent from the
semantic descriptions in the catalog entries.

This convention is invisible for pure-bytes codecs like `zstd` because both
sides of the transform are `bytes` — the type signature is symmetric even though
the semantic content differs. The only structural asymmetry the signature model
captures is the `encode_only` property on inputs, which marks parameters that
are active only during encoding (e.g. a compression level).

"Bidirectional" means: a codec implements both encode and decode operations. It
does not mean the signature is direction-neutral. The signature describes the
decode interface; the encode interface is implicitly the reverse.

For pipelines, the `direction` field makes the decode-first convention explicit:
it tells the executor which way the DAG was written so it can traverse or invert
it correctly. Individual codec signatures have no equivalent field — the
decode-first convention is implicit and unstated in the protospec. The protospec
should state it explicitly, since the claim that "codecs do not have a
direction" is only coherent if you already know that signatures describe the
decode interface by convention.

---

## Asymmetric codec signatures

Several codecs in the catalog have signatures that are structurally asymmetric —
their decode interface has a different number of `bytes` ports than their encode
interface would. These cannot be described as direction-neutral even at the type
level.

### 2→1: plain-dictionary

```
inputs:  indices (bytes), dictionary (bytes), physical_type (string)
outputs: bytes
```

Decode: index stream + dictionary → values (a lookup).
Encode: values → index stream + dictionary (dictionary construction + reverse lookup).

In the pipeline that uses this codec, `dictionary` is wired from a pipeline-level
input (`input.dict_page`). Under DAG inversion, pipeline inputs become pipeline
outputs, so the encode direction correctly produces `dict_page` as a pipeline
output — the format driver writes it as the dictionary page. The step-level
asymmetry is handled by the pipeline boundary inversion, not by the codec
signature itself.

### 1→3: parquet-page-v1-split and parquet-page-v2-split

```
# v1
inputs:  bytes
outputs: rep_bytes (bytes), def_bytes (bytes), value_bytes (bytes)

# v2
inputs:  bytes, rep_length (uint), def_length (uint)
outputs: rep_bytes (bytes), def_bytes (bytes), value_bytes (bytes)
```

Decode: one page (after any prior decompression step) splits into three streams
(repetition levels, definition levels, values).
Encode: three streams concatenate into one page.

In v2, `rep_length` and `def_length` tell the decoder where to cut the byte
stream. They are decode-only: in the encode direction these lengths are not
needed — they follow naturally from the sizes of the three input buffers. The
signature model has `encode_only` but no `decode_only` property, so this
asymmetry cannot be expressed. The signature does not mark them at all, which
is a gap in both the model and the catalog entry.

### 1→variable: arrow-ipc-buffer

```
inputs:  bytes, arrow_type (string), nullable (bool)
outputs: validity (bytes), offsets (bytes), values (bytes)
           [output count varies by arrow_type and nullable]
```

The output structure is type-dependent: non-nullable primitives produce only
`values`; nullable types add `validity`; variable-width types add both
`validity` and `offsets`. The signature model has no mechanism for
variable-arity outputs. This codec cannot be cleanly described within the
model as written.

### bytes→bytes with length change: fletcher32, crc32c, crc32

```
inputs:  bytes, error_on_fail (bool, optional)
outputs: bytes
```

The signature shape is symmetric (`bytes → bytes`) but the byte count differs
by direction: encode appends a 4-byte checksum, decode verifies and strips it.
The asymmetry is hidden by the type system. The `error_on_fail` parameter
controls whether to raise an error on checksum mismatch; it is meaningful only
during decode. Like `rep_length`/`def_length` above, it is a decode-only
parameter that the model cannot express — there is no `decode_only` property.
It is not marked `encode_only` in the catalog (which would be wrong — it is the
opposite), and the gap is simply unaddressed.

---

## "A pipeline is a codec" — behavioral contract

The protospec statement "a pipeline is a codec" is a behavioral and interface
contract, not a compilation claim. It has two components:

### Interface compatibility

A pipeline's top-level inputs and outputs form a signature that satisfies the
same contract as a leaf codec signature. The internal constructs — `direction`,
`constants`, and output-as-wiring-reference rather than typed descriptor — are
implementation details of the executor. At the boundary, a pipeline looks like
any other codec: typed ports in, typed ports out.

### Behavioral compatibility

Since every codec implements both `encode` and `decode`, a pipeline must also be
runnable in either direction. This is why the executor must be able to invert a
pipeline: the bidirectionality guarantee of a leaf codec must hold for composed
pipelines as well. A pipeline that can only run in one direction is not a codec.

### Implication: nested pipelines

These two properties together imply that a pipeline can appear as a step inside
another pipeline. The executor currently assumes every step `src` resolves to a
leaf Wasm component. Supporting nested pipelines requires detecting when a `src`
resolves to a pipeline JSON and either flattening the nested DAG at parse time
or recursing at execution time.

### Why the executor, not the format driver, handles inversion

A format driver typically knows the codec pipeline for a chunk from file
metadata, and it calls the same codec for both read and write paths. By
convention, pipeline specs are written in decode direction. If the executor
could not invert, the format driver would need two pipeline specs per codec (one
encode, one decode) and keep them in sync. Inversion eliminates that: declare
the pipeline once, and the executor drives it in either direction based on the
call.

---

## Nested pipelines — concrete cases

The protospec catalog defines some codecs as pipelines rather than leaf
implementations. When a format driver constructs a decode pipeline for a file
format, it may reference one of these pipeline-valued codecs as a step —
producing a nested pipeline.

### ORC varint

ORC is a columnar file format for tables. One of its integer encoding schemes
operates in two stages: zigzag encoding (which maps signed integers to unsigned
so that small-magnitude negatives encode compactly) followed by varint encoding
(which encodes small numbers in fewer bytes than large ones). These two
transforms are applied in sequence. The format driver calls `orc-varint` as a
single named codec, but `orc-varint` is itself a two-step pipeline internally.

ORC columns can also be compressed at a higher level (e.g. Snappy or zstd). A
format driver building a decode pipeline for a compressed ORC integer column
would construct:

```
snappy → orc-varint
```

The second step resolves to a pipeline JSON rather than a leaf `.wasm`. That is
a nested pipeline.

### Parquet v2 pages

Parquet stores columnar data in pages. A Parquet v2 page concatenates three
streams into one byte sequence — repetition levels, definition levels, and
values — with length fields at the front indicating where each section ends.
Decoding requires splitting that byte sequence into the three streams before any
further per-stream decoding (e.g. delta decoding on the values stream).

The splitting step is `parquet-page-v2-split` — a DAG with fan-out: one byte
stream in, three streams out. A format driver for a Parquet file might construct
an outer pipeline:

```
decompress → parquet-page-v2-split → delta-decode (values stream)
```

Where `parquet-page-v2-split` resolves to a pipeline JSON. The outer pipeline
handles column-level concerns; the inner pipeline handles page-level splitting.
The step-level asymmetry of `parquet-page-v2-split` (1 input, 3 outputs) is
structurally sound under DAG inversion — the three streams fan back in to one
on the encode path.

### Zarr v3 sharding

Zarr stores N-dimensional arrays as chunked tiles. Zarr v3 introduced a
sharding codec that groups multiple small chunks into one larger shard file to
reduce file count on object storage. The sharding codec operates at two levels:
at the outer level it locates each inner chunk within the shard byte stream
using an embedded index; at the inner level it applies the chunk's own codec
pipeline (e.g. shuffle followed by zstd).

The inner codec chain is itself a pipeline — the same one that would apply to
chunks without sharding. The sharding codec wraps it. The full decode pipeline
for a sharded Zarr array is therefore an outer pipeline that invokes an inner
pipeline for each chunk.

---

## Nested pipeline steps: pipeline JSON vs compiled Wasm

Two approaches exist for how a step whose `src` resolves to a pipeline-defined
codec is handled at execution time. Neither is decided.

### Approach A: store and fetch pipeline JSON

The step `src` points to a pipeline JSON file (via `file://`, `https://`, or
`oci://`). The executor detects that the resolved artifact is a pipeline JSON
rather than a `.wasm`, then either flattens the nested DAG into the outer
pipeline at parse time or recurses into it at execution time.

Advantages:

- No new build step. Pipeline JSON is already the authoring format.
- The pipeline structure remains inspectable and human-readable.
- Individual step components are fetched and cached independently; the same
  leaf `.wasm` is reused across all pipelines that reference it.
- Inversion is handled by the executor using the DAG structure — the same
  mechanism already needed for top-level pipeline inversion. No new concept
  is required.
- A pipeline JSON is effectively its own signature (top-level `inputs` and
  `outputs` are present), so it may not need a separate `.signature.json`
  sidecar.

Disadvantages:

- The executor must distinguish `.wasm` from pipeline JSON when resolving a
  `src` URI. The `src` field description and validation logic need updating.
- Each step boundary involves a Python/wasmtime context switch; nested
  pipelines add more boundaries.

### Approach B: compile pipeline to a leaf Wasm component

A build step produces a single `.wasm` component from a pipeline JSON. The
component exports both `encode` and `decode` per the `chonkle:codec/transform`
WIT interface, with the correct step ordering wired for each direction:
`decode` chains A → B → C in pipeline order; `encode` chains C → B → A in
reverse. The executor cannot distinguish the result from a leaf codec.

The Wasm Component Model does not prevent this. A component that implements
both functions with different internal data-flow topologies is valid — the WIT
interface requires both functions, and nothing in the Component Model
constrains how they are wired internally. A hand-authored component implementing
a specific pipeline in both directions would be entirely legal.

The gap is tooling. Current composition tools (`wac`, `wasm-tools compose`) do
static single-topology linking: they wire one component's exports to another's
imports in a single fixed data-flow graph. They have no mechanism to generate
two distinct wiring topologies within one component, nor to generate the
dispatch logic that routes a call to `encode` vs `decode` through the
appropriate topology. To produce a composed pipeline `.wasm` correctly, a
pipeline compiler would need to be written — a code generator that takes a
pipeline JSON and emits either a Wasm component directly or source code (e.g.
Rust, WAT) that implements both directions. For DAGs with fan-out and fan-in,
the generated code would also need to handle port-map routing at each branch
point. That tool does not exist.

Advantages:

- The executor requires no changes — every step `src` resolves to a `.wasm`.
- Distribution, caching, and signing are uniform across all codecs.
- Nested pipelines are resolved at build time; the executor sees only flat
  leaf components.

Disadvantages:

- Requires building a pipeline compiler that does not exist. This is non-trivial
  tooling, particularly for non-linear DAGs.
- Individual leaf components are duplicated inside each composed artifact rather
  than shared across pipelines.
- Composed components are opaque — the internal pipeline structure is not
  recoverable without a separate decompilation step.

### Decision

Approach A (pipeline JSON) is the chosen approach. Approach B is theoretically
sound but requires a pipeline compiler that does not exist and is out of scope.
Approach A requires only executor changes: detecting when a resolved `src`
artifact is a pipeline JSON rather than a `.wasm`, and recursing into `run()`
rather than calling `_call_component()`.

---

## Summary

These notes cover two themes: gaps in the codec signature model, and the
semantics of the pipeline model.

**Decode-direction convention**: all codec signatures are written in decode
direction by convention, but the protospec does not state this. The claim that
"codecs do not have a direction" is only coherent if you already know the
convention. The protospec should state it explicitly.

**Signature model gaps**: the model works cleanly for the common case —
`bytes → bytes` codecs with scalar encode-only parameters. It strains in three
ways:

- *Structural port-count asymmetry* (`plain-dictionary`, parquet splits): encode
  and decode interfaces have different numbers of ports. For `plain-dictionary`,
  pipeline-level DAG inversion resolves this correctly. For parquet v2 splits,
  the 1→3 / 3→1 inversion is structurally sound, but the `rep_length` /
  `def_length` inputs become outputs under inversion — awkward and unaddressed
  in the protospec.
- *Decode-only parameters* (parquet-page-v2-split, fletcher32/crc32): the model
  has `encode_only` but no `decode_only`. Parameters meaningful only during
  decode cannot be annotated as such. The catalog leaves these gaps unaddressed.
- *Variable-arity outputs* (`arrow-ipc-buffer`): output structure depends on
  runtime type information. The signature model has no mechanism for this.

**Pipeline-as-codec contract**: "a pipeline is a codec" is a behavioral and
interface contract. Interface compatibility means a pipeline's boundary looks
like a leaf codec signature. Behavioral compatibility means a pipeline must be
runnable in either direction, which is why the executor must support inversion.
A pipeline that can only run in one direction is not a codec.

**Nested pipelines**: the two properties above together imply that a pipeline
can appear as a step inside another pipeline. Real cases exist in the catalog
(`orc-varint`) and in formats like Parquet v2 and Zarr v3 sharding. The
executor does not yet support this; every step `src` is currently assumed to
resolve to a leaf Wasm component.

**Inversion responsibility**: the executor handles inversion, not the format
driver. Format drivers declare one pipeline spec (by convention in decode
direction) and call it for both read and write paths. The executor inverts the
DAG as needed. This keeps pipeline specs non-redundant.
