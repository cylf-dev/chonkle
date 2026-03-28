# chonkle

chonkle is a Python host for running data codec pipelines. Pipelines are DAGs of codec steps backed by Component Model Wasm, Core Wasm, or native Python (numcodecs) implementations.

## Architecture

- [CODEC_RUNTIME.md](CODEC_RUNTIME.md) — Codec backends, data transfer costs, Python host tradeoffs, and execution model

## Reference

Specs for codec and pipeline authors.

- [reference/CODEC_CONTRACT.md](reference/CODEC_CONTRACT.md) — Codec contract: what each backend must implement (Component Model WIT, Core ABI, native)
- [reference/CORE_ABI.md](reference/CORE_ABI.md) — Core ABI contract: port-map wire format, required exports, calling convention
- [reference/PIPELINE_SCHEMA.md](reference/PIPELINE_SCHEMA.md) — Pipeline JSON schema: DAG structure, wiring references, step fields
- [reference/protospec/](reference/protospec/) — Codec inventory: named codecs with typed signatures
- [reference/comparisons/](reference/comparisons/) — Codec pipeline tradeoff analysis and F3 comparison

## Internals

Details for maintaining or more thoroughly understanding chonkle.

- [internals/CACHING.md](internals/CACHING.md) — Local codec store, download cache, and wasmtime compilation cache
- [internals/CANONICAL_ABI_PERF.md](internals/CANONICAL_ABI_PERF.md) — Performance investigation: where the time goes, Python vs. native host throughput, mitigations
- [internals/DATA_COPIES.md](internals/DATA_COPIES.md) — Copy counts per inter-step edge across all backend combinations, and approaches to reduce them
- [internals/DISTRIBUTION.md](internals/DISTRIBUTION.md) — Options for distributing `.wasm` codec artifacts (GitHub Releases, GHCR/OCI, warg)

## Wasm background

Foundational Wasm concepts referenced throughout these docs.

- [wasm/OVERVIEW.md](wasm/OVERVIEW.md) — What is WebAssembly? Virtual CPU, runtimes, portability
- [wasm/WASI.md](wasm/WASI.md) — System interface, `wasm32-wasi` target, command vs reactor
- [wasm/MEMORY.md](wasm/MEMORY.md) — Linear memory, memory growth, `dlmalloc`, buffer strategies
- [wasm/COMPONENT_MODEL.md](wasm/COMPONENT_MODEL.md) — Components vs Core modules, composition, toolchain
- [wasm/WIT.md](wasm/WIT.md) — WIT interface definition language
- [wasm/WIT_RESOURCES.md](wasm/WIT_RESOURCES.md) — WIT resource types: handles, ownership, handle tables, and why resources don't reduce copy counts in chonkle's pipeline
