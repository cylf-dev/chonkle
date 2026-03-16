# F3 vs. chonkle: Comparison and Tradeoff Analysis

References:
- [F3: The Open-Source Data File Format for the Future (CMU SIGMOD 2025)](https://db.cs.cmu.edu/papers/2025/zeng-sigmod2025.pdf)
- [github.com/future-file-format/F3](https://github.com/future-file-format/F3)

---

## What F3 proposes

F3 is a replacement for Parquet/ORC — a self-describing columnar file format for analytics workloads.

- **IOUnit/EncUnit hierarchy**: IOUnit is the I/O parallelism unit (multiple pages); EncUnit is an atomic byte buffer encoded by a single algorithm. Stacking EncUnits within a column composes encodings hierarchically.
- **Wasm embedded in the data file**: When a new encoding is used, its Wasm decoder is embedded inside the file itself so any reader can decode it without pre-installed libraries. The codec travels with the data.
- **Vortex encodings**: 15+ built-in columnar encodings (bit-packing, ALP, dictionary, etc.) composable as linear chains per column.
- **FlatBuffers** for zero-copy metadata deserialization, replacing Parquet's Thrift.
- No explicit WIT or Component Model usage — a "general-purpose API" for Wasm decoders without a formal interface type system.

---

## Key differences

| Dimension | F3 | chonkle |
|---|---|---|
| **Domain** | Structured tabular analytics (Parquet replacement) | Geospatial raster imagery (COG tiles, satellite data) |
| **Data model** | Typed columnar schema, FlatBuffer metadata | Opaque named byte buffers (`port-map`), schema-agnostic |
| **Codec composition** | Linear stack within a column (EncUnit chain) | Arbitrary DAG with named ports, fan-out/fan-in |
| **Where Wasm lives** | Embedded inside the data file as a portable fallback decoder | Fetched from external registry (`file://`, `https://`, `oci://`) at pipeline execution time |
| **Interface contract** | Informal "general-purpose API" — no WIT types | Formal WIT interface: `chonkle:codec/transform` with Component Model |
| **Bidirectionality** | Separate write (encode) and read (decode) paths | Single pipeline definition, invertible at runtime via DAG reversal |
| **Self-description** | Data files are self-describing (decoder embedded) | Pipelines are separate from data; data is not self-describing |
| **Metadata format** | FlatBuffers for column schema and statistics | `.signature.json` sidecar per codec (port descriptors, direction constraints) |
| **Codec distribution** | Embedded in file; central repo proposed | OCI artifacts with signature verification |

---

## F3's advantages over chonkle's approach

**Self-describing data is a strong archival guarantee.** If you encode a COG tile today with a chonkle pipeline, the pipeline JSON is the only thing that knows how to decode it. Lose the pipeline, lose the data. F3's "decoder travels with the data" property is directly relevant for NASA data that must be readable in 20 years without external dependencies.

**Schema awareness enables query pushdown.** F3 knows column types, shapes, and statistics. It can skip I/O for irrelevant columns, push down predicates, and support partial decoding without reading the whole column. Chonkle treats everything as opaque bytes — any schema understanding lives outside the pipeline.

**Random access within EncUnits.** Because F3 tracks structure, it can decode a row range without touching the rest of the column. Chonkle pipelines are all-or-nothing: a step receives a full port-map and emits a full port-map.

**Ecosystem integration target.** F3 aims to plug into Spark, DuckDB, and Pandas by replacing Parquet. Chonkle has no query engine integration story.

---

## Chonkle's advantages over F3's approach

**Arbitrary DAG composition.** F3's EncUnit chain is linear per column. Chonkle can express fan-out (one output routed to multiple inputs), fan-in (multiple outputs merged), and cross-step data routing via named wiring refs. That is the right model for multi-channel imagery where you might split byte planes, process them in parallel, and recombine — something a linear stacking model cannot express without significant contortion.

**Formal interface contract.** WIT and the Component Model give chonkle a verifiable, language-agnostic codec contract. The signature sidecar extends that to runtime port-level validation with direction awareness. F3's "general-purpose API" is informal by comparison — nothing prevents a codec from violating its contract until runtime.

**Bidirectional pipelines.** F3 has no concept of inverting a pipeline. For imagery processing, encode and decode are two sides of the same transform. A single chonkle DAG runs in both directions, which eliminates the risk of encode/decode asymmetry bugs and halves the pipeline authoring cost.

**Codec sandboxing.** Each chonkle codec is an isolated Wasm component. A buggy or malicious codec cannot corrupt the host process. F3 embeds Wasm in data files from potentially untrusted sources, which is a meaningful attack surface. F3 acknowledges a 10–30% performance penalty for the sandboxed path.

**Registry-first distribution.** OCI artifacts with signature verification is a production-ready codec distribution model. F3 notes "central repository verification proposed" without specifying it. For a satellite processing system pulling codecs on demand, a concrete distribution story matters.

---

## The deeper tension

F3 is optimizing for **reading existing data efficiently and portably** — a file format problem. Chonkle is optimizing for **composing transforms flexibly** — a pipeline problem. These are different problem shapes, and the approaches are not competing so much as operating at different levels of the stack.

**The gap chonkle does not address:** if you run a chonkle pipeline to encode data, how does a reader know which pipeline to use to decode it? Currently the answer is "the caller knows," which is appropriate for a tightly controlled system but fragile at archival or interoperability scale.

One synthesis worth considering: a content-addressed pipeline URI could be written as a sidecar alongside encoded data, giving chonkle's compositional power with something closer to F3's self-description guarantee. That is structurally what F3 does with embedded Wasm, but at pipeline granularity rather than individual codec granularity. The protospec's `codec_id` field already points in this direction — a stable identifier that could resolve to a pipeline definition in a registry.
