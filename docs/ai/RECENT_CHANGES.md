# Recent Changes

Only architectural, structural, and workflow changes. Not bug fixes or minor tweaks.

## March 2026

- **Native (numcodecs) integration** (Phase 6 of mixed-codec architecture):
  native Python codecs are now the third backend alongside Component Model
  and Core Wasm.
  - Added `NativeCodec` to `codecs.py`: wraps a numcodecs codec object.
    Signature loaded from bundled JSON files in
    `src/chonkle/signatures/numcodecs/`. `numcodecs` and `numpy` are optional
    dependencies, imported lazily at `NativeCodec` instantiation. Two calling
    conventions via `data_format` signature field: `"bytes"` (direct
    pass-through) and `"ndarray"` (buffer conversion using `dtype` port).
    Non-`bytes` ports are JSON-decoded and passed as constructor kwargs.
    For ndarray codecs, `dtype` is tried as a constructor arg first (needed
    by `Delta`) with fallback for codecs that do not accept it (`Shuffle`).
  - Added `src/chonkle/signatures/numcodecs/` with signature files for:
    zlib, gzip, bz2, lzma, zstd, lz4, blosc (bytes-format), delta, shuffle
    (ndarray-format).
  - Updated `Resolver`: default preference changed from
    `("core", "component")` to `("core", "component", "native")`. Resolution
    chain step 2 now combines local store entries and native signatures,
    selecting by preference. `list_codecs()` includes native entries.
    Per-codec overrides can select native implementations.
  - Exported `NativeCodec` from `chonkle.__init__`.
  - Added `tests/test_native_codec.py`: instantiation tests, bytes-format
    round-trips (zlib, gzip, bz2, lzma), ndarray-format round-trips (delta,
    shuffle), pipeline integration (single step, chained, mixed with fake
    wasm, encode-only handling), resolver native integration tests.

- **Single-copy optimization** (Phase 5 of mixed-codec architecture): data
  transfer between sequential core wasm codec steps now uses a single
  `ctypes.memmove` between linear memories instead of two copies through Python.
  - Added `CoreWasmRef` dataclass to `codecs.py`: deferred reference to data in
    a core wasm module's linear memory. `materialize()` copies to Python bytes
    only when needed (e.g., for non-core downstream codecs or final output).
  - `CoreWasmCodec.call()` now returns lazy output port-maps: metadata (port
    names, lengths) is parsed from linear memory but bulk data stays as
    `CoreWasmRef` entries. Input port-maps with `CoreWasmRef` entries are
    serialized into the destination module's memory using `_copy_between_memories`
    (ctypes.memmove via `Memory.data_ptr()`, with `Memory.read`/`Memory.write`
    fallback).
  - Added `_deserialize_port_map_lazy()`, `_copy_between_memories()`, and
    `_single_copy_transfer()` to `codecs.py`.
  - Executor `value_store` widened to `dict[str, bytes | CoreWasmRef]`.
    Port-map builders (`_forward_port_map`, `_inverted_port_map`) pass
    `CoreWasmRef` values through for core wasm codecs and materialize them for
    other backends. `_materialize()` helper resolves final pipeline outputs.
  - Exported `CoreWasmRef` from `chonkle.__init__`.
  - Added `TestSingleCopyWiring` (mock-based executor materialization tests) and
    `TestSingleCopyCorePipeline` (integration tests with real core identity
    codec: two-step, three-step chain, inverted execution, lazy output
    verification).

- **Core ABI** (Phase 4 of mixed-codec architecture): core wasm codecs are now
  supported as a second backend alongside Component Model codecs.
  - Added `CoreWasmCodec` to `codecs.py`: wraps a wasm32-wasi reactor module,
    serializes/deserializes port-maps using a binary wire format via
    `Memory.read`/`Memory.write`, calls `alloc`/`dealloc`/`encode`/`decode`
    exports. Module instance is kept alive for the codec's lifetime so
    downstream core codecs can single-copy transfer from its linear memory.
  - Added port-map wire format helpers (`_serialize_port_map`,
    `_deserialize_port_map`) in `codecs.py`.
  - Added `docs/reference/CORE_ABI.md`: normative spec for the core ABI
    contract (wire format, required exports, calling convention, error
    signaling). Includes C and Rust reference implementations.
  - Added `codec/shared/core_abi.h` and `core_abi.c`: C reference
    implementation for port-map parse/serialize/find operations.
  - Added `codec/identity-core-c/`: identity passthrough codec using the core
    ABI (no Component Model lift). C + Zig build producing a core wasm module.
  - Updated `Resolver._instantiate_path()` and `_instantiate()` to create
    `CoreWasmCodec` for core wasm binaries (was `ValueError`).
  - Exported `CoreWasmCodec` from `chonkle.__init__`.
  - Added `TestCoreWasmPipeline` in `test_executor.py`: single-step
    encode/decode, two-step routing, inverted execution, resolver type
    detection. Added `TestPortMapSerialization` in `test_codecs.py`.

- **Codec resolution and local store** (Phase 3 of mixed-codec architecture):
  codec resolution is decoupled from pipeline JSON via a `Resolver` class.
  - Added `resolver.py` with `Resolver` class, `CodecEntry` dataclass, and
    `_scan_store()`. Resolution chain: explicit paths → per-codec overrides →
    local codec store (`~/.chonkle/codecs/{codec_id}/{hash}.wasm`) → pipeline
    sources download. Backend preference system (`core` vs `component`).
  - `StepSpec` in `pipeline.py` reduced to `name`, `codec_id`, `inputs` only.
    Removed `src`, `outputs`, and `encode_only_inputs` from steps.
  - `Pipeline` gained `sources: dict[str, str]` field (optional, pipeline-level
    codec_id → URI mapping used as advisory fetch hints).
  - `executor.py`: `prepare()` now takes `resolver: Resolver | None` instead of
    `force_download: bool`. Added `_validate_wiring_against_signatures()` for
    deferred output port validation. `_get_encode_only_inputs()` derives
    encode-only from codec signatures instead of step declarations.
  - CLI: added `chonkle codecs [codec_id]` subcommand and `--codec-store`,
    `--preference`, `--override` flags. Removed `--force-download`.
  - `Codec` ABC gained `codec_id` and `implementation` abstract properties.
  - All pipeline fixture JSONs and test inline dicts updated to remove `src`,
    `outputs`, `encode_only_inputs` from steps. Test fixtures use `Resolver(paths={...})`
    for codec resolution.
  - `__init__.py` exports `Resolver`, `CodecEntry`; removed `resolve_uri`.

- **Codec wrapper classes and prepare/run split** (Phase 2 of mixed-codec
  architecture): the executor is refactored into preparation and execution
  phases with a codec abstraction layer.
  - Added `codecs.py` with `Codec` ABC, `ComponentCodec`, `detect_codec_type()`,
    and `PortMap` type alias. `ComponentCodec` wraps the Component Model
    instantiation logic previously in `executor.py` (`_call_component`,
    `_get_function`). `detect_codec_type()` reads the 8-byte wasm header to
    distinguish core (`01 00 00 00`) from component (`0d 00 01 00`).
  - Split `executor.py` into `prepare(pipeline, direction)` →
    `PreparedPipeline` and `run(prepared, inputs)` → outputs. `prepare()`
    handles URI resolution, codec instantiation, and signature validation.
    `run()` performs only DAG execution — no validation. Callers can use
    `prepare()` independently to validate without executing.
  - `_validate_signature()` now reads from `codec.signature()` (loaded at
    codec instantiation) instead of from the wasm file during validation.
  - Exported `Codec`, `ComponentCodec`, `PortMap`, `detect_codec_type`,
    `PreparedPipeline`, `prepare` from `chonkle.__init__`.
  - Updated CLI (`cli.py`) to use `prepare()` + `run()`.
  - Added `tests/test_codecs.py` for `detect_codec_type`. Updated
    `test_executor.py`: wiring tests use `_FakeCodec` + `PreparedPipeline`
    directly (no mocking), signature tests call `prepare()`, COG codec
    round-trip tests use `prepare()` + `run()`.

- **Signature embedding infrastructure** (Phase 1 of mixed-codec architecture):
  signatures are now embedded in `.wasm` binaries as `chonkle:signature` custom
  sections instead of distributed as `.signature.json` sidecar files.
  - Added `wasm_signature.py` — pure-Python reader/writer for the custom section.
    `read_signature()`, `read_signature_bytes()`, and `embed_signature()`.
  - Added `tools/embed_signature.py` — CLI tool to embed signatures at build time.
  - Added `implementation` field to signature format (identifies the specific
    build/project, e.g. `"zlib-rs"`, `"tiff-predictor-2-c"`).
  - Renamed codec sidecar files from `{name}.signature.json` to `signature.json`
    (build input convention, not a distribution artifact).
  - Executor `_validate_signature()` now reads from the embedded custom section
    via `read_signature()` instead of looking for a sidecar file.
  - `wasm_download.py` no longer downloads sidecar files — only the `.wasm` is
    fetched. Removed `_derive_signature_url()`.
  - Exported `read_signature` and `embed_signature` from `chonkle.__init__`.

- **Bench and codec infrastructure**: added `bench/` directory with Rust host,
  Python host, and chonkle host benchmarks. Added `codec/README.md` documenting
  the build process for each codec and shared infrastructure. Added
  `codec/identity-c/` passthrough codec for benchmarking.

- **Codec pipeline tradeoff analysis**: added `docs/reference/comparisons/` with
  `CODEC_PIPELINE_TRADEOFFS.md` and `F3_COMPARISON.md`.

- **`steps` changed from array to named map**: pipeline JSON `steps` field changed
  from an array of objects with a `name` field (`[{"name": "step_name", ...}]`) to a
  named map keyed by step name (`{"step_name": {...}}`), aligning with protospec.
  Updated `pipeline.py` parser, all test inline dicts, and all fixture JSONs.

- **COG chunk pipeline and tests**: added `codec/zlib-rs/` (Rust zlib codec via
  `cargo-component`, `flate2` rust_backend). Added `tests/fixtures/chunks/cog-chunk-0`
  (real 1024x1024 uint16 Sentinel-2 tile). Added
  `tests/fixtures/pipelines/cog-decode-pipeline.json` (two-step decode: zlib →
  tiff-predictor-2) and `cog-encode-pipeline.json` (two-step encode: tiff-predictor-2
  → zlib); removed `zstd-linear.json`. Replaced `zstd_pipeline_json` fixture in
  conftest with `cog_chunk`, `cog_decode_pipeline_json`, and `cog_encode_pipeline_json`
  fixtures (with real `file://` URIs). Added `TestCogChunkPipeline` in
  `test_executor.py` with three tests (decode size, decode type, encode/decode
  roundtrip via DAG inversion) gated on `COG_CODECS_REQUIRED` (checks file
  existence). Updated `TestParsePipeline` fixture refs.

- **`_inverted_port_map` fix**: inverted execution now includes constant-wired non-encode_only
  inputs in the port map. Previously, codecs with required configuration parameters wired from
  constants (e.g. tiff-predictor-2's `bytes_per_sample`, `width`) would receive an empty
  port-map in the inverted direction, causing a missing-port error.

- **Module-level function idiom applied**: in `executor.py`, inlined
  `_resolve_uris` and `_store_inverted_outputs` (single-caller helpers) and
  dropped the redundant `_build_` prefix from the port-map builders. In
  `pipeline.py`, extracted `_validate_pipeline`, `_check_ref`, and
  `_topological_sort` from the `Pipeline` class to module-level private
  functions. `Pipeline` is now a pure dataclass (fields + `parse()` classmethod
  factory); no instance methods or `@staticmethod`s remain on the class.

- **Pipeline direction inversion**: `run()` gains required positional `direction:
  Direction` parameter. `pipeline.direction` is now metadata (authoring direction)
  not an execution constraint. Forward execution (`direction == pipeline.direction`)
  is unchanged. Inverted execution (`direction != pipeline.direction`) reverses
  `execution_order`, calls the opposite WIT function per step, builds port-maps from
  `step.outputs` (plus `encode_only_inputs` when calling encode), and routes results
  back through `step.inputs` wiring refs. Inverted inputs are keyed by
  `pipeline.outputs` names; inverted outputs by `pipeline.inputs` names. CLI gains
  optional `--direction` flag (defaults to `pipeline.direction`). `Direction` type
  exported from `__init__.py`.

- **Pipeline JSON schema refined** (`docs/reference/PIPELINE_SCHEMA.md`): schema diverges
  from protospec in two newly-implemented ways. (1) Step `outputs` is now an
  array of port name strings (`["bytes"]`) instead of a typed object; `pipeline.py`
  parser changed from `.keys()` extraction to direct list parsing; all fixture
  JSONs and inline test dicts updated. (2) Pipeline-level inputs now support
  `encode_only: true`; `executor.py` skips requiring and loading such inputs
  when `direction == "decode"`. New tests cover both directions for pipeline-level
  `encode_only`.

- **Pipeline JSON schema aligned with protospec**: four structural
  inconsistencies resolved. `Pipeline.inputs` changed from `list[str]` to
  `dict[str, Any]` (typed port descriptors). Step `outputs` changed from
  `list[str]` in JSON to a typed object; parser extracts keys internally so
  `StepSpec.outputs` remains `list[str]`. `constants` values changed from flat
  scalars to typed descriptors `{"type": ..., "value": ...}`; executor
  serializes `descriptor["value"]`. `configuration` field removed from steps;
  `cfg` passed to codec components is always `b"{}"`. All fixture JSONs, tests,
  README, and docs/ai/ files updated.

- **Signature format and validation**: codec signature sidecar format changed from a
  flat list format to the protospec codec signature (ports as dict keys with `type`,
  `required`, `default`, `encode_only` descriptors). Validation now covers both inputs
  and outputs using subset checks. Input validation is direction-aware: `encode_only`
  ports are excluded from valid inputs during decode. `_validate_signature` receives
  `direction`. `pipeline.py` `_validate()` now checks that every `encode_only_inputs`
  entry is declared in `step.inputs`.

- **DAG pipeline orchestrator**: replaced the linear pipeline model with a full
  DAG pipeline. New `pipeline.py` parses pipeline JSON into a `Pipeline`
  dataclass, validates wiring references at parse time, and produces a
  topologically sorted `execution_order` (Kahn's algorithm). New `executor.py`
  drives execution via Wasmtime Component Model, routing named port-map outputs
  between steps. Old `pipeline.py` (linear, numcodecs-based) removed.

- **WIT interface**: added `wit/codec.wit` defining `chonkle:codec@0.1.0` with
  the `transform` interface (`encode`/`decode` taking `port-map` and `config`
  as `list<u8>`). All codecs must implement this interface. Supersedes the ad-hoc
  per-codec WIT used by `tiff-predictor-2-python`.

- **`wasm_download.py` extended**: added `resolve_uri()` consolidating URI
  resolution previously scattered in `wasm_runner.py`. Supports `file://` and
  `https://`; raises `NotImplementedError` for `oci://` (not yet implemented).

- **Signatures required + auto-downloaded**: codec signatures (`.signature.json`
  sidecar alongside `.wasm`) are now required. `_validate_signature()` raises
  `FileNotFoundError` if the sidecar is absent. Naming convention changed from
  `{stem}.json` to `{stem}.signature.json`. `download_https()` now derives the
  signature URL from the `.wasm` URL and downloads both atomically. `download_oci()`
  verifies that the OCI artifact contains a `.signature.json` layer and raises
  `ValueError` if absent. OCI support implemented in `resolve_uri()` (was
  `NotImplementedError`); `_derive_signature_url()` and `_download_url_to()` helpers
  added to `wasm_download.py`.

- **Removed numcodecs/numpy**: `codecs.py` (TiffPredictor2, BytesCodec) and
  `wasm_runner.py` removed. All codecs are now Wasm components; no Python/
  numcodecs codecs remain. `numcodecs` and `numpy` removed from dependencies.

- **Test fixtures**: replaced binary chunk fixtures (`tests/fixtures/chunks/`)
  with programmatically generated test data in `conftest.py`. Pipeline JSON
  fixtures added at `tests/fixtures/pipelines/` (zstd-linear, page-split-dag).

- **CLI replaced**: `chonkle encode`/`chonkle decode` commands (array-centric)
  replaced by `chonkle run --pipeline … --input name=file` (format-agnostic).

## February 2026

- **COG extraction** (98741d4): removed entire `cog/` subpackage. COG
  functionality now lives in the external `chunk-utils` repo.
- **Module flattening** (db43fd0): moved `chonkle.decode.*` to top-level
  `chonkle.*`. The `decode/` subdirectory no longer exists.
- **Encode support** (db43fd0): added `encode()` to the pipeline (now removed).
- **WASM download system** (e459be5): added `wasm_download.py`.
- **Python version** (3ebacbb): minimum lowered from 3.14 to 3.13.
