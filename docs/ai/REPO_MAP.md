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

Core library. `__init__.py` files are docstring-only; import from the
defining module directly (e.g. `from chonkle.pipeline import prepare`).

- **pipeline.py** — DAG pipeline parsing, validation, and preparation. Entry
  point: `prepare(source, direction, *, resolver=None)` → `PreparedPipeline`.
  Parses pipeline JSON into a `Pipeline` dataclass, validates wiring
  references, topologically sorts steps (stored as `dict[str, StepSpec]` in
  topo order), resolves codec_ids via `Resolver`, validates wiring against
  codec signatures, and validates all step signatures. `PreparedPipeline`
  precomputes `encode_only_inputs` and `output_ports` per step.
  Key types: `Pipeline`, `PreparedPipeline`, `StepSpec`, `WiringRef`,
  `InputDescriptor`, `ConstantDescriptor`.
- **codecs/** — Codec wrapper package. `__init__.py` is docstring-only;
  import from submodules directly. Submodules:
  - **codecs/_base.py** — `Codec` ABC with `call(direction, port_map)` and
    `signature()`. `Signature` and `PortDescriptor` frozen dataclasses for
    typed codec signatures. `PortMap` type alias, `SIGNATURES_DIR`,
    `CODEC_TRANSFORM_IFACE`, `detect_codec_type()`.
  - **codecs/component.py** — `ComponentCodec`: wraps a Component Model
    component. Caches `Component.from_file()` at init time.
  - **codecs/core.py** — `CoreWasmCodec`: wraps a core wasm32-wasi reactor
    module (binary port-map serialization, `alloc`/`dealloc` calls). Returns
    `CoreWasmRef` entries (lazy output) and accepts them as input (single-copy
    via `ctypes.memmove`). `close()` releases the store and linear memory.
    `OutputPortMap` type alias. Port-map wire format helpers. Cross-module
    transfer: `_copy_between_memories`, `_single_copy_transfer`.
  - **codecs/native.py** — `NativeCodec`: wraps a numcodecs codec (lazy import,
    signature from bundled JSON, bytes and ndarray calling conventions).
  Key types: `Codec`, `ComponentCodec`, `CoreWasmCodec`, `NativeCodec`,
  `CoreWasmRef`, `PortMap`, `OutputPortMap`, `Signature`, `PortDescriptor`.
- **resolver.py** — Codec resolution and local store. `Resolver` maps
  codec_ids to `Codec` instances via: explicit paths → per-codec overrides →
  local store and native (selected by preference) → pipeline sources
  download. Default preference: `["core", "component", "native"]`.
  `CodecEntry` holds store metadata. `_scan_store()` indexes the local store.
  `_has_native_signature()` checks for bundled native signatures.
  `list_codecs()` includes both wasm and native entries.
  Key types: `Resolver`, `CodecEntry`.
- **executor.py** — DAG execution via `Codec` wrappers. Entry point:
  `run(prepared, inputs)` → output dict. Executes the DAG via
  `_execute_forward(prepared, inputs)` / `_execute_inverted(prepared, inputs)`.
  `value_store` holds `bytes | CoreWasmRef`; port-map builders materialize
  refs for non-core codecs, pass through for core codecs. Encode-only sets
  and output ports come from precomputed `PreparedPipeline` fields.
  `_materialize()` resolves final outputs. Key internals: `value_store`,
  `_forward_port_map`, `_inverted_port_map`, `_materialize`.
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
  `chonkle embed-signature <wasm> <sig.json>` embeds a signature into a
  `.wasm` binary (build-time tool).
- **signatures/numcodecs/** — Bundled signature JSON files for native
  (numcodecs) codecs. One file per supported codec (e.g., `zlib.json`,
  `gzip.json`, `delta.json`). Each includes the standard signature fields
  plus `data_format` (`"bytes"` or `"ndarray"`). Adding support for a new
  numcodecs codec requires only adding a JSON file here.

## Tests — `tests/`

pytest-based. Network tests require `--run-network` flag.

- **test_pipeline.py** — pipeline parsing, wiring validation, topological sort
- **test_codecs.py** — `detect_codec_type()` binary header detection tests
- **test_executor.py** — executor wiring logic (fake codec instances), signature
  validation via `prepare()`, URI resolution integration,
  CODEC_REQUIRED codec round-trip tests via `prepare()` + `run()`,
  single-copy wiring tests (`TestSingleCopyWiring` with mock refs,
  `TestSingleCopyCorePipeline` with real core identity codec)
- **test_native_codec.py** — `NativeCodec` instantiation, bytes-format codecs
  (zlib, gzip, bz2, lzma round-trips), ndarray-format codecs (delta, shuffle
  round-trips), native steps in DAG pipelines, mixed native+wasm pipelines,
  encode-only parameter handling, resolver native integration
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
