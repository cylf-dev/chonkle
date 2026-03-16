# Repository Map

## WIT — `wit/`

- **codec.wit** — WIT interface definition for all codec components:
  `chonkle:codec@0.1.0`, world `codec`, interface `transform` with `encode`
  and `decode` functions

## Codecs — `codec/`

Each codec directory contains a `.wasm` binary, a `.signature.json` sidecar,
and its build source.

### Built

- **tiff-predictor-2-c/** — TIFF horizontal differencing, C + Zig build,
  ports: `bytes` + `bytes_per_sample` + `width` in, `bytes` out
- **zlib-rs/** — zlib compress/decompress, Rust (`cargo-component`), ports:
  `bytes` in (+ optional `level` encode-only), `bytes` out.
  Build: `cargo component build --release` in `codec/zlib-rs/`.
  Output copied to `codec/zlib-rs/zlib.wasm`.

### Planned

- **zstd-rs/** — zstd encode/decode, Rust
- **page-split-rs/** — byte-range splitter, Rust, fan-out (1 input, 3 outputs)
- **identity-py/** — passthrough codec, Python via componentize-py

## Source — `src/chonkle/`

Core library. All public API is re-exported from `__init__.py`.

- **pipeline.py** — DAG pipeline parsing and validation. Entry point: `parse()`.
  Parses pipeline JSON into a `Pipeline` dataclass, validates all wiring
  references, and produces a topologically sorted `execution_order`.
  Key types: `Pipeline`, `StepSpec`, `WiringRef`.
- **executor.py** — DAG execution via Wasmtime Component Model. Entry point:
  `run(pipeline, inputs, direction)`. Resolves codec URIs, validates signature
  sidecars, builds port-maps, calls each step's Wasm component in order.
- **wasm_download.py** — codec URI resolution and fetch cache. `resolve_uri()`
  handles `file://`, `https://`, and `oci://`. `download_https()` and `download_oci()`
  store downloaded `.wasm` files locally keyed by SHA-256 or OCI reference.
  Respects `CHONKLE_CACHE_DIR` and `CHONKLE_FORCE_DOWNLOAD` env vars.
- **cli.py** — `chonkle run` command. Accepts `--pipeline`, `--input NAME=FILE`,
  `--output NAME=FILE`, `--force-download`.

## Tests — `tests/`

pytest-based. Network tests require `--run-network` flag.

- **test_pipeline.py** — pipeline parsing, wiring validation, topological sort
- **test_executor.py** — executor wiring logic (mocked components), signature
  validation, URI resolution integration, CODEC_REQUIRED codec round-trip tests
- **test_wasm_download.py** — cache behavior, HTTPS and OCI download mocking,
  force-redownload
- **conftest.py** — pytest fixtures (`raw_chunk`, `page_split_input`,
  `zstd_pipeline_json`, `page_split_pipeline_json`), `--run-network` option

## Test Fixtures — `tests/fixtures/pipelines/`

Pipeline JSON files for testing and demonstration:

- **zstd-linear.json** — single-step encode pipeline using the zstd codec
- **page-split-dag.json** — fan-out DAG: page-split + three identity codec steps

## Demo — `demo/`

- **chonkle-pipeline.ipynb** — end-to-end demo notebook (pre-dates DAG branch)
- **tiff-predictor-2-c.wasm** — pre-compiled legacy Wasm module
- **README.md** — setup and run instructions

## Docs — `docs/`

- **wasm/** — general Wasm knowledge base (not project-specific):
  - **OVERVIEW.md**, **WASI.md**, **MEMORY.md**, **WIT.md**, **COMPONENT_MODEL.md**,
    **WIT_RESOURCES.md** (handles, own vs borrow, handle tables, resource-sharing
    patterns, and why resources don't reduce copy counts in chonkle's pipeline)
- **internals/** — architectural decision records and internals:
  - **DISTRIBUTION.md** — remote storage options for `.wasm` files
  - **DATA_COPIES.md** — copy-count accounting, edge-type table, and analysis of copy-reduction approaches
  - **CANONICAL_ABI_PERF.md** — measured throughput of the Python canonical ABI binding vs. native host
  - **CACHING.md** — fetch cache and wasmtime compilation cache design

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
