# Repository Map

## WIT — `wit/`

- **codec.wit** — WIT interface definition for all codec components:
  `chonkle:codec@0.1.0`, world `codec`, interface `transform` with `encode`
  and `decode` functions. All codecs implement this interface.

## Codecs — `codec/`

Each codec directory contains a `.wasm` binary (Component Model or core) with an
embedded `chonkle:signature` custom section, a `signature.json` build input, and
its build source. `codec/README.md` documents the build process for each codec.

### Built

- **tiff-predictor-2-c/** — TIFF horizontal differencing, C + Zig build,
  Component Model Wasm. Ports: `bytes` in (+ `bytes_per_sample` + `width`,
  both required), `bytes` out. Build: `zig cc` → wasm32-wasi core module →
  `wasm-tools component new` with WASI adapter to produce Component Model.
- **zlib-rs/** — zlib compress/decompress, Rust via `cargo-component` with
  `wit-bindgen-rt`. Implements `Guest` trait from WIT bindings. Ports:
  `bytes` in (+ optional `level` encode-only, default 6), `bytes` out.
  Build: `cargo component build --target wasm32-wasip2 --release`.
- **identity-c/** — passthrough codec for benchmarking (Component Model).
  `encode`/`decode` are no-ops: output `bytes` = input `bytes`. Same C + Zig
  toolchain as tiff-predictor-2-c.
- **identity-core-c/** — passthrough codec for benchmarking (Core Wasm).
  Uses the core ABI binary port-map wire format instead of the Component Model
  canonical ABI. C + Zig build, no Component Model lift step. Exports `memory`,
  `alloc`, `dealloc`, `encode`, `decode`.

### Shared infrastructure

- **shared/** — WIT-generated canonical ABI C bindings (`codec.h`, `codec.c`)
  and `codec_component_type.o`; used by Component Model C+Zig codecs.
  Core ABI C helpers (`core_abi.h`, `core_abi.c`) for core wasm codecs;
  provides port-map parse/serialize/find functions. Regenerate WIT bindings
  with `wit-bindgen c wit/ --world codec --out-dir codec/shared/`.
- **wasi_snapshot_preview1.reactor.wasm** — WASI preview1 → preview2 adapter
  for lifting wasm32-wasi core modules to Component Model at build time.
  From wasmtime v41.0.0 GitHub release.

### Planned

- **zstd-rs/** — zstd encode/decode, Rust
- **page-split-rs/** — byte-range splitter, Rust, fan-out (1 input, 3 outputs)

## Source — `src/chonkle/`

Core library. All public API is re-exported from `__init__.py`.

- **pipeline.py** — DAG pipeline parsing and validation. Entry point: `parse()`.
  Parses pipeline JSON into a `Pipeline` dataclass, validates all wiring
  references, and produces a topologically sorted `execution_order`.
  Key types: `Pipeline`, `StepSpec`, `WiringRef`.
- **codecs.py** — Codec wrapper classes. `Codec` ABC with `call(direction,
  port_map)` and `signature()`. `ComponentCodec` wraps a Component Model
  component (Wasmtime instantiation, WIT function lookup, encode/decode call).
  `CoreWasmCodec` wraps a core wasm32-wasi reactor module (binary port-map
  serialization, `Memory.read`/`Memory.write`, `alloc`/`dealloc` calls).
  `CoreWasmCodec.call()` returns `CoreWasmRef` entries (lazy output parsing)
  and accepts them as input (single-copy via `ctypes.memmove`).
  `detect_codec_type()` reads the wasm binary header to route to the correct
  wrapper. Port-map wire format helpers: `_serialize_port_map`,
  `_deserialize_port_map`, `_deserialize_port_map_lazy`. Cross-module transfer:
  `_copy_between_memories`, `_single_copy_transfer`. Key types: `Codec`,
  `ComponentCodec`, `CoreWasmCodec`, `CoreWasmRef`, `PortMap`.
- **resolver.py** — Codec resolution and local store. `Resolver` maps
  codec_ids to `Codec` instances via: explicit paths → per-codec overrides →
  local store (`~/.chonkle/codecs/{codec_id}/{hash}.wasm`) → pipeline sources
  download. `CodecEntry` holds store metadata. `_scan_store()` indexes the
  local store. Key types: `Resolver`, `CodecEntry`.
- **executor.py** — DAG preparation and execution via `Codec` wrappers. Entry
  points: `prepare(pipeline, direction, *, resolver=None)` → `PreparedPipeline`,
  `run(prepared, inputs)` → output dict. `prepare()` resolves codec_ids via
  `Resolver`, validates wiring against signatures (deferred output port checks),
  and validates all signatures. `run()` executes the DAG. `value_store` holds
  `bytes | CoreWasmRef`; port-map builders materialize refs for non-core
  codecs, pass through for core codecs. `_materialize()` resolves final
  outputs. Encode-only inputs are derived from codec signatures. Key internals:
  `PreparedPipeline`, `value_store`, `_forward_port_map`, `_inverted_port_map`,
  `_materialize`, `_get_encode_only_inputs`, `_validate_wiring_against_signatures`.
- **wasm_download.py** — codec URI resolution and fetch cache. `resolve_uri()`
  handles `file://`, `https://`, and `oci://`. Downloads only the `.wasm` file
  (signatures are embedded). `download_https()` and `download_oci()` store
  downloaded files locally keyed by SHA-256 or OCI reference. Respects
  `CHONKLE_CACHE_DIR` and `CHONKLE_FORCE_DOWNLOAD` env vars.
- **wasm_signature.py** — pure-Python reader/writer for the `chonkle:signature`
  custom section in Wasm binaries. `read_signature()` reads from file,
  `read_signature_bytes()` from in-memory bytes, `embed_signature()` appends or
  replaces the section. Works with both core and Component Model binaries.
- **cli.py** — `chonkle run` command. Accepts `--pipeline`, `--input NAME=FILE`,
  `--output NAME=FILE`, `--codec-store`, `--preference`, `--override`.
  `chonkle codecs [codec_id]` subcommand lists installed implementations.
- **tools/embed_signature.py** — CLI tool to embed a `signature.json` file into
  a `.wasm` binary. `python -m chonkle.tools.embed_signature <wasm> <sig.json>`.

## Tests — `tests/`

pytest-based. Network tests require `--run-network` flag.

- **test_pipeline.py** — pipeline parsing, wiring validation, topological sort
- **test_codecs.py** — `detect_codec_type()` binary header detection tests
- **test_executor.py** — executor wiring logic (fake codec instances), signature
  validation via `prepare()`, URI resolution integration,
  CODEC_REQUIRED codec round-trip tests via `prepare()` + `run()`,
  single-copy wiring tests (`TestSingleCopyWiring` with mock refs,
  `TestSingleCopyCorePipeline` with real core identity codec)
- **test_wasm_download.py** — cache behavior, HTTPS and OCI download mocking,
  force-redownload
- **test_wasm_signature.py** — custom section reader/writer tests: round-trip
  embed+read, error handling, real codec file validation
- **conftest.py** — pytest fixtures (`raw_chunk`, `page_split_input`, `cog_chunk`,
  `cog_decode_pipeline_json`, `cog_encode_pipeline_json`, `page_split_pipeline_json`,
  `cog_codec_resolver`), `--run-network` option

## Test Fixtures — `tests/fixtures/pipelines/`

Pipeline JSON files for testing and demonstration:

- **cog-decode-pipeline.json** — two-step COG decode pipeline: zlib → tiff-predictor-2
- **cog-encode-pipeline.json** — two-step COG encode pipeline: tiff-predictor-2 → zlib
- **page-split-dag.json** — fan-out DAG: page-split + three identity codec steps

## Demo — `demo/`

- **chonkle-pipeline.ipynb** — end-to-end demo notebook
- **tiff-predictor-2-c.wasm** — pre-compiled Wasm module for the demo
- **README.md** — setup and run instructions

## Docs — `docs/`

- **README.md** — docs index with links to all sub-sections
- **CODEC_RUNTIME.md** — how the executor loads and calls a Wasm codec component
- **wasm/** — general Wasm knowledge base (not project-specific):
  - **OVERVIEW.md**, **WASI.md**, **MEMORY.md**, **WIT.md**, **COMPONENT_MODEL.md**,
    **WIT_RESOURCES.md** (handles, own vs borrow, handle tables, resource-sharing
    patterns, and why resources don't reduce copy counts in chonkle's pipeline)
- **internals/** — architectural decision records and internals:
  - **DISTRIBUTION.md** — remote storage options for `.wasm` files
  - **DATA_COPIES.md** — copy-count accounting, edge-type table, and analysis of copy-reduction approaches
  - **CANONICAL_ABI_PERF.md** — measured throughput of the Python canonical ABI binding vs. native host
  - **CACHING.md** — fetch cache and wasmtime compilation cache design
- **reference/** — normative reference documents:
  - **CODEC_CONTRACT.md** — codec contract (ports, WIT interface, sidecar requirements)
  - **CORE_ABI.md** — core ABI contract (port-map wire format, required exports, calling convention, C and Rust reference implementations)
  - **PIPELINE_SCHEMA.md** — pipeline JSON schema reference
  - **protospec/PROTOSPEC.md** — upstream protospec (informational)
  - **protospec/PROTOSPEC_NOTES.md** — notes on where chonkle diverges from protospec
  - **comparisons/CODEC_PIPELINE_TRADEOFFS.md** — codec pipeline tradeoff analysis
  - **comparisons/F3_COMPARISON.md** — comparison with F3

## Docs — `docs/ai/`

Persistent AI context files. Read by Claude Code at conversation start.

## Bench — `bench/`

See `bench/README.md` for usage.

- **rust-host/** — standalone Rust crate, `wasmtime-rs 41` typed bindings.
  `cd bench/rust-host && cargo build --release && cargo run --release`
- **python-host/time_abi_raw.py** — raw wasmtime-py call, bypasses chonkle
  executor. PEP 723 inline deps (`wasmtime==41.*`).
  `uv run bench/python-host/time_abi_raw.py`
- **chonkle-host/time_codec.py** — drives the chonkle executor across codec
  types and sizes. PEP 723 inline dep (`chonkle @ ../..`).
  `uv run bench/chonkle-host/time_codec.py`

## CI — `.github/workflows/`

- **ci.yml** — lint job (pre-commit on Python 3.14) + test matrix (3.13, 3.14)
