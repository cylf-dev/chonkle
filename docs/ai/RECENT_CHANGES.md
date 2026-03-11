# Recent Changes

Only architectural, structural, and workflow changes. Not bug fixes or minor tweaks.

## March 2026

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

- **Pipeline JSON schema refined** (`docs/PIPELINE_SCHEMA.md`): schema diverges
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
