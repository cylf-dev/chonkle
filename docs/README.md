# chonkle

chonkle is a Python host for running data codec pipelines. Pipelines are DAGs of codec steps backed by Component Model Wasm, Core Wasm, or native Python (numcodecs) implementations.

## Overview

- [OVERVIEW.md](OVERVIEW.md) — Why Wasm, why Python, codec backends summary, and execution model

## Reference

Specs for codec and pipeline authors.

- [reference/codec-contract/](reference/codec-contract/) — Codec contract: common requirements, plus per-backend specs ([COMPONENT_MODEL.md](reference/codec-contract/COMPONENT_MODEL.md), [CORE.md](reference/codec-contract/CORE.md), [NATIVE.md](reference/codec-contract/NATIVE.md))
- [reference/CODEC_RESOLUTION.md](reference/CODEC_RESOLUTION.md) — Codec resolution chain, backend preference, binary detection
- [reference/PIPELINE_SCHEMA.md](reference/PIPELINE_SCHEMA.md) — Pipeline JSON schema: DAG structure, wiring references, step fields
- [reference/protospec/](reference/protospec/) — Codec inventory: named codecs with typed signatures

## Design

Design rationale, analysis, and comparisons.

- [design/DATA_COPIES.md](design/DATA_COPIES.md) — Copy counts per inter-step edge across all backend combinations, and approaches to reduce them
- [design/CANONICAL_ABI_PERF.md](design/CANONICAL_ABI_PERF.md) — Performance investigation: where the time goes, Python vs. native host throughput, mitigations
- [design/CACHING.md](design/CACHING.md) — Local codec store, download cache, and wasmtime compilation cache
- [design/CODEC_DISTRIBUTION.md](design/CODEC_DISTRIBUTION.md) — Options for distributing `.wasm` codec artifacts (GitHub Releases, GHCR/OCI, warg)
- [design/MULTI_MEMORY.md](design/MULTI_MEMORY.md) — Feasibility of multi-memory for zero-copy pipelines
- [design/PIPELINE_TRADEOFFS.md](design/PIPELINE_TRADEOFFS.md) — Codec pipeline architecture trade-off analysis
- [design/F3_COMPARISON.md](design/F3_COMPARISON.md) — Comparison with F3

## Wasm background

Foundational Wasm concepts referenced throughout these docs.

- [wasm/OVERVIEW.md](wasm/OVERVIEW.md) — What is WebAssembly? Virtual CPU, runtimes, portability
- [wasm/WASI.md](wasm/WASI.md) — System interface, `wasm32-wasi` target, command vs reactor
- [wasm/MEMORY.md](wasm/MEMORY.md) — Linear memory, memory growth, `dlmalloc`, buffer strategies
- [wasm/COMPONENT_MODEL.md](wasm/COMPONENT_MODEL.md) — Components vs Core modules, composition, toolchain
- [wasm/WIT.md](wasm/WIT.md) — WIT interface definition language
- [wasm/WIT_RESOURCES.md](wasm/WIT_RESOURCES.md) — WIT resource types: handles, ownership, handle tables
- [wasm/EXECUTION_MODEL.md](wasm/EXECUTION_MODEL.md) — Compilation, instantiation, and invocation lifecycle
- [wasm/MULTI_MEMORY.md](wasm/MULTI_MEMORY.md) — Multi-memory feature, toolchain limitations
