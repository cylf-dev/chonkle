# System Overview

## Purpose

chonkle is a Wasm Component Model codec pipeline library. Pipelines are DAGs of
codec steps, each implemented as a Wasm component that exports the
`chonkle:codec/transform` WIT interface. The Python orchestrator parses pipeline
JSON, validates wiring, topologically sorts steps, and drives execution via
wasmtime-py.

Part of the NASA-IMPACT VEDA / cylf-dev ecosystem for satellite imagery processing.

## Architecture

Pipeline DAG design where each execution is driven by a pipeline JSON file:

- **Parsing**: `pipeline.py` parses JSON into a validated `Pipeline` dataclass
  with topologically sorted steps
- **Execution**: `executor.py` resolves codec URIs, builds port-maps, and calls
  each step's Wasm component in topological order
- **Codec interface**: all codecs implement `chonkle:codec/transform` (WIT in
  `wit/codec.wit`): `encode` and `decode` take a `port-map`, return
  `result<port-map, string>`
- **Port routing**: named ports (`list<tuple<port-name, list<u8>>>`) enable
  fan-out and fan-in. The executor routes step outputs to downstream step inputs
  by wiring reference
- **Direction**: `pipeline.direction` records which direction the DAG was authored
  in (by convention, `"decode"`). The caller always specifies a runtime direction
  when calling `run()`. When `direction == pipeline.direction`, execution is
  forward (current order, current WIT function). When they differ, execution is
  inverted: steps run in reversed topological order, the opposite WIT function is
  called, port-maps are built from `step.outputs` plus constant-wired non-encode_only
  inputs, and results are routed back through `step.inputs` wiring refs.

## Subsystems

- **pipeline** (`pipeline.py`): parse pipeline JSON, validate wiring references,
  topological sort (Kahn's algorithm). Entry points: `parse()`. Key types:
  `Pipeline`, `StepSpec`, `WiringRef`
- **executor** (`executor.py`): execute a `Pipeline` via Wasmtime Component
  Model. Entry point: `run(pipeline, inputs, direction)`. Internal phases:
  URI resolution (inline dict comprehension in `run()`), `_validate_signatures()`
  (validate all signatures before any execution, collecting all errors),
  `_execute_forward()` / `_execute_inverted()` (split by direction),
  `_forward_port_map()` / `_inverted_port_map()` (port-map builders),
  `_call_component()` (calls one Wasm codec step), `_validate_signature()`
  (per-step signature checker)
- **wasm_download** (`wasm_download.py`): resolve codec URIs to local paths and
  cache remote downloads. `resolve_uri()` handles `file://`, `https://`, and
  `oci://`. `download_https()` downloads the `.wasm` and its `.signature.json`
  sidecar together. `download_oci()` pulls the OCI artifact and verifies both
  files are present. Cache keyed by SHA-256 of URL (HTTPS) or reference path
  (OCI). Respects `CHONKLE_CACHE_DIR` and `CHONKLE_FORCE_DOWNLOAD` env vars.
- **cli** (`cli.py`): `chonkle run --pipeline pipeline.json --input name=file`
  command

## WIT Interface

```wit
package chonkle:codec@0.1.0;

interface transform {
  type port-name = string;
  type port-map = list<tuple<port-name, list<u8>>>;

  encode: func(inputs: port-map) -> result<port-map, string>;
  decode: func(inputs: port-map) -> result<port-map, string>;
}

world codec {
  export transform;
}
```

## Pipeline JSON Schema

```json
{
  "codec_id": "my-pipeline",
  "direction": "encode",
  "inputs": {"bytes": {"type": "bytes"}},
  "constants": {"level": {"type": "int", "value": 3}},
  "outputs": {"bytes": "step_name.port_name"},
  "steps": [
    {
      "name": "step_name",
      "codec_id": "some-codec",
      "src": "file:///path/to/codec.wasm",
      "inputs": {"bytes": "input.bytes", "level": "constant.level"},
      "outputs": ["bytes"],
      "encode_only_inputs": ["level"]
    }
  ]
}
```

Field notes:

- `codec_id` at the pipeline level is required (a pipeline is itself a codec per protospec)
- `name` within each step is the unique DAG node identifier; used in wiring references; must be
  unique within the pipeline
- `codec_id` within each step is the logical codec identifier; the same codec may appear as
  multiple steps with different `name` values
- `src` is the URI of the codec implementation (will become optional once a registry exists)
- Wiring reference forms: `input.<name>`, `constant.<name>`, `<step_name>.<port>`
- `inputs` (pipeline level) are typed dicts: `{"port": {"type": "..."}}`; step `outputs` are arrays of port name strings: `["port1", "port2"]`
- `constants` are typed descriptors: `{"name": {"type": "...", "value": ...}}`; type info is
  stored but not validated (PoC scope)
- `configuration` field is absent; all codec parameters flow through port-map via constants

## Codec Signature Sidecar

A `.signature.json` file alongside each `.wasm` containing the protospec codec
signature. **Required** — `_validate_signature()` raises `FileNotFoundError` if
absent. Naming convention: `{stem}.signature.json` (e.g. `zstd.wasm` →
`zstd.signature.json`). Ports are dict keys; values are descriptors:

```json
{
  "codec_id": "zstd",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "level": {"type": "int", "required": false, "default": 3, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```

For `https://` URIs the signature URL is derived by replacing `.wasm` with
`.signature.json` in the path; both are downloaded together. For `oci://` URIs
the signature must be a layer in the same OCI artifact. For `file://` URIs the
sidecar must already exist on disk. Validation rules:

- Both inputs and outputs use **subset checks** — the step need not use every port
  the codec declares
- Input validation is **direction-aware**: ports marked `encode_only: true` are
  excluded from the valid input set when running in decode direction
- Active step inputs = `step.inputs.keys() - encode_only_inputs` (the executor
  already skips encode_only_inputs during decode)

## Codec Cache

Two distinct layers:

1. **Fetch cache** (`wasm_download.py`): stores downloaded `.wasm` files locally.
   Keyed on SHA-256 of URL. Controlled by `CHONKLE_CACHE_DIR` and
   `CHONKLE_FORCE_DOWNLOAD`.
2. **Compilation cache**: wasmtime's built-in disk cache for compiled `.cwasm`
   artifacts. Enabled via `wasmtime.Config().cache = True`. Managed by wasmtime.

## Major Rules

- All codecs are Wasm components (Component Model); no Python/numcodecs codecs
- Port names inside port-maps are runtime conventions, not WIT type guarantees —
  port name mismatches surface as runtime errors
- `encode_only_inputs` are omitted from the port-map on `decode` calls; included on `encode` calls (even when inverted)
- Constants are serialized as UTF-8 JSON bytes when placed in a port-map entry
- Wiring validation is done at parse time; signature validation in a pre-execution
  pass (all steps validated before any component is called)
- Signatures are required; execution raises `FileNotFoundError` if absent
- `http://` URIs raise `ValueError`; `file://`, `https://`, `oci://` are supported
- Pipeline inversion (encode ↔ decode DAG reversal) is implemented: pass `direction` opposite to `pipeline.direction` to `run()`

## Development

- **Package manager**: uv (lock file: `uv.lock`)
- **Build backend**: hatchling
- **Python**: >= 3.13, CI tests on 3.13 and 3.14
- **Linting/formatting**: ruff (extensive rule set)
- **Type checking**: mypy (`ignore_missing_imports = true`)
- **Testing**: pytest
- **Pre-commit**: ruff check, ruff format, mypy, yaml/toml validation
- **CI**: GitHub Actions — lint job (Python 3.14) + test matrix (3.13, 3.14)
- **Runtime deps**: wasmtime, oras

## Testing

- Tests that require compiled codec `.wasm` files are marked `CODEC_REQUIRED`
  (`skipif(True, ...)`) and will not pass until codecs are built
- Network tests gated behind `--run-network` / `@pytest.mark.network`
- Test data is generated programmatically in `conftest.py` (no binary blobs)

## Writing Style

- Avoid qualitative claims without data: do not use "significant", "dramatic",
  "substantial", "greatly" unless backed by measurements or statistical analysis
- State facts directly without editorializing
- Skip self-congratulatory language in commit messages, code comments, and docs
- Use as few contractions as possible

## Maintenance

- Update this file when subsystems change or new patterns are established
- Update `REPO_MAP.md` when adding/removing directories or modules
- Update `RECENT_CHANGES.md` when making architectural changes (not bug fixes).
  Prune entries older than ~3 months or when the file exceeds ~15 items.
- Keep all docs/ai/ files scannable: bullets over paragraphs, minimal code blocks
