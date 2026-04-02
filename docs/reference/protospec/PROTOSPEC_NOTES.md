# Protospec Notes

Commentary and clarifications on `PROTOSPEC.md`. That document is an exact copy of an upstream reference and is not modified here.

## Codec signatures are written in decode direction by convention

The protospec states "Individual codecs do not have a direction — every codec is bidirectional by definition." This is defensible but incomplete without qualification.

"Bidirectional" means: a codec implements both encode and decode operations. It does not mean the signature is direction-neutral. All codec signatures in the catalog are written in **decode direction by convention**. By this convention, inputs represent the encoded/compressed form and outputs represent the decoded/raw form. The encode direction is the implied inverse. For `bytes → bytes` codecs this convention is not visible in the signature itself — both sides have the same type — it is only apparent from the semantic descriptions in the catalog entries. The only structural asymmetry the signature model captures is the `encode_only` property on inputs, which marks parameters that are active only during encoding (e.g. a compression level).

For pipelines, the `direction` field makes the decode-first convention explicit: it tells the executor which way the DAG was written so it can traverse or invert it correctly. Individual codec signatures have no equivalent field — the decode-first convention is implicit and unstated in the protospec. The protospec should state that codec signatures describe the decode interface by convention. Without that, the claim that "codecs do not have a direction" is only coherent if the reader already knows the convention.

## Signature model gaps

### No `decode_only` property

The protospec signature model has `encode_only` to mark inputs active only during encoding, but no `decode_only` counterpart. At least two codecs in the catalog have inputs meaningful only during decode:

- **parquet-page-v2-split**: `rep_length` and `def_length` tell the decoder where to split the byte stream into three output streams. In the encode direction these lengths are not needed — they follow from the sizes of the three input buffers. The signature does not mark them as decode-only, and the model has no way to express this.

- **fletcher32, crc32c, crc32**: `error_on_fail` controls whether to raise on checksum mismatch during decode. It is meaningless during encode (which unconditionally appends the checksum). It is not marked `encode_only` in the catalog — that would be wrong, it is the opposite — and the gap is unaddressed.

Chonkle implements `decode_only` as the symmetric counterpart to `encode_only`. It is used by native codecs like packbits, where `encode_dtype` is encode-only and `decode_dtype` is decode-only. The executor omits `decode_only` ports from the port-map during encode, mirroring the existing `encode_only` handling during decode. See the `decode_only` divergence in [PIPELINE_SCHEMA.md](../PIPELINE_SCHEMA.md#decode_only-on-codec-signature-and-pipeline-inputs).

### No variable-arity outputs

`arrow-ipc-buffer` produces a different number of output buffers depending on `arrow_type` and `nullable`: non-nullable primitives produce only `values`; nullable types add `validity`; variable-width types add `offsets`. The signature declares all three outputs statically, but not all are produced for every call. The signature model has no mechanism to express conditional or variable-arity outputs.

## Implications of "a pipeline is a codec"

The protospec states that a pipeline is a codec, but does not spell out what that requires. Three things follow:

**Derived signatures.** A pipeline's boundary must satisfy the same contract as a leaf codec signature: typed ports in, typed ports out. The protospec does not specify how to derive this signature from the pipeline JSON. In particular, it does not say how constants appear in the derived signature, and pipeline outputs are wiring references (`step_name.port_name`) rather than type descriptors — output types must be traced through step signatures.

**Bidirectionality.** Since every codec implements both `encode` and `decode`, a pipeline must also be runnable in either direction. A pipeline that can only run in one direction is not a codec. This is why the executor must support DAG inversion.

**Nesting.** A pipeline can appear as a step inside another pipeline. Real cases exist in the catalog (`orc-varint` is a two-step pipeline; `parquet-page-v2-split` appears as a step in the Parquet v2 dictionary-decode pipeline). Zarr v3 sharding is a case not in the catalog: the sharding codec wraps an inner codec pipeline (e.g. shuffle + zstd) applied per-chunk within the shard. The executor currently assumes every `codec_id` resolves to a leaf codec. Supporting nesting means handling the case where the resolver finds a pipeline JSON rather than a leaf codec for a given `codec_id`, and recursing into `run()`. No new concepts are required — the same DAG inversion and step execution mechanisms apply at both levels.
