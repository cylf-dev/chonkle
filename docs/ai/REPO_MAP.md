# Repository Map

## WIT — `wit/`

- **codec.wit** — WIT interface definition for all codec components:
  `chonkle:codec@0.1.0`, world `codec`, interface `transform` with `encode`
  and `decode` functions

## Codecs — `codecs/`

Not yet built (separate session). Planned:

- **zstd-rs/** — zstd encode/decode, Rust, 1-in/1-out (`bytes`)
- **page-split-rs/** — byte-range splitter, Rust, fan-out (1 input, 3 outputs)
- **identity-py/** — passthrough codec, Python via componentize-py

Each codec will have a `.wasm` binary and a `.signature.json` sidecar declaring
its input and output port names.

## Source — `src/chonkle/`

Core library. All public API is re-exported from `__init__.py`.

- **pipeline.py** — DAG pipeline parsing and validation. Entry point: `parse()`.
  Parses pipeline JSON into a `Pipeline` dataclass, validates all wiring
  references, and produces a topologically sorted `execution_order`.
  Key types: `Pipeline`, `StepSpec`, `WiringRef`.
- **executor.py** — DAG execution via Wasmtime Component Model. Entry point:
  `run(pipeline, inputs)`. Resolves codec URIs, validates signature sidecars,
  builds port-maps, calls each step's Wasm component in order.
- **wasm_download.py** — codec URI resolution and fetch cache. `resolve_uri()`
  handles `file://` and `https://`. `download_https()` and `download_oci()`
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
  - **OVERVIEW.md**, **WASI.md**, **MEMORY.md**, **WIT.md**

## Docs — `docs/ai/`

Persistent AI context files. Read by Claude Code at conversation start.

## CI — `.github/workflows/`

- **ci.yml** — lint job (pre-commit on Python 3.14) + test matrix (3.13, 3.14)
