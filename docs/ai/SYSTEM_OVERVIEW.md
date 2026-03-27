# System Overview

## Purpose

chonkle is a Wasm codec pipeline library. Pipelines are DAGs of codec steps,
each implemented as a Wasm Component Model component. The Python orchestrator
parses pipeline JSON, validates wiring, topologically sorts steps, and drives
execution via wasmtime-py's Component Model API.

Part of the NASA-IMPACT VEDA / cylf-dev ecosystem for satellite imagery processing.

## Architecture

Pipeline DAG design where each execution is driven by a pipeline JSON file:

- **Parsing and preparation**: `pipeline.py` parses JSON into a validated
  `Pipeline` dataclass with topologically sorted steps, resolves codec_ids
  via `Resolver`, validates wiring against codec signatures, and returns a
  `PreparedPipeline`. Single entry point: `prepare(source, direction)`
- **Codec wrapper classes** (`codecs/`): `Codec` ABC normalizes different
  backends. `ComponentCodec` wraps a Component Model component.
  `CoreWasmCodec` wraps a core wasm32-wasi reactor module using the binary
  port-map wire format. `NativeCodec` wraps a numcodecs codec object,
  loading its signature from bundled JSON files in
  `src/chonkle/signatures/numcodecs/`. `numcodecs` and `numpy` are optional
  dependencies, imported lazily at `NativeCodec` instantiation.
  `NativeCodec` supports two calling conventions via the `data_format`
  signature field: `"bytes"` (pass-through) and `"ndarray"` (buffer
  conversion using a `dtype` port). Non-`bytes` ports are JSON-decoded and
  passed as constructor kwargs to the numcodecs codec.
  `CoreWasmCodec.call()` returns lazy output port-maps
  with `CoreWasmRef` entries (deferred references to data in linear memory).
  When a downstream codec is also a `CoreWasmCodec`, data transfers use
  `ctypes.memmove` via `Memory.data_ptr()` for single-copy between linear
  memories. Non-core downstream codecs receive materialized bytes.
  `detect_codec_type()` reads the 8-byte wasm header to distinguish core vs
  component model binaries. Port-map serialization helpers
  (`_serialize_port_map`, `_deserialize_port_map`, `_deserialize_port_map_lazy`)
  implement the core ABI wire format. `_copy_between_memories` and
  `_single_copy_transfer` handle cross-module data transfer.
- **Resolution** (`resolver.py`): `Resolver` maps codec_ids to `Codec` instances.
  Resolution chain: explicit paths → per-codec overrides → local codec store
  and native (combined, selected by preference) → pipeline sources (download
  and install). Default preference: `["core", "component", "native"]`.
  Native codecs are discovered from bundled signature files. Backend
  preference system selects among multiple implementations. Key types:
  `Resolver`, `CodecEntry`.
- **Execution** (`executor.py`): `run()` takes a `PreparedPipeline` and inputs,
  executes the DAG by calling `codec.call(direction, port_map)` for each step.
  Data flows between steps through `value_store: dict[str, bytes | CoreWasmRef]`
  — core wasm codec outputs remain as deferred references in linear memory
  until consumed. Port-map builders materialize `CoreWasmRef` for non-core
  downstream codecs and pass them through for core downstream codecs (enabling
  single-copy transfer). Final pipeline outputs are always materialized to
  bytes.
- **Codec interface** (Component Model): each component implements
  `chonkle:codec/transform@0.1.0` (defined in `wit/codec.wit`), exporting
  `encode` and `decode` functions that take a `port-map` (list of named byte
  buffers) and return a `result<port-map, string>`.
- **Port routing** (v1): each codec step has exactly one data input port and one
  data output port, both named `"bytes"`. Constants are JSON-encoded and passed
  through port-maps alongside data ports. `value_store` maps wiring refs to
  `bytes` values. Fan-out (a step producing multiple distinct data ports) is
  deferred.
- **Direction**: `pipeline.direction` records which direction the DAG was authored
  in (by convention, `"decode"`). The caller always specifies a runtime direction
  when calling `prepare()` / `run()`. When `direction == pipeline.direction`, execution is
  forward (current order, `encode`/`decode` function). When they differ, execution
  is inverted: steps run in reversed topological order, the opposite function is
  called, `value_store` is seeded from `pipeline.outputs`, and results are
  routed back through `step.inputs` wiring refs.

## Subsystems

- **pipeline** (`pipeline.py`): parse, validate, and prepare pipelines for
  execution. Entry point: `prepare(source, direction, *, resolver=None)` →
  `PreparedPipeline`. Internally: `Pipeline.parse()` (structural validation,
  wiring refs parsed into `WiringRef` at construction, topological sort via
  Kahn's algorithm), codec resolution via `Resolver`, wiring validation
  against codec signatures, signature validation. `StepSpec.inputs` and
  `Pipeline.outputs` hold pre-parsed `WiringRef` objects (not raw strings).
  `Pipeline.steps` is a `dict[str, StepSpec]` with keys in topological
  execution order (no separate `execution_order` field).
  `PreparedPipeline` precomputes `encode_only_inputs` and `output_ports`
  per step, eliminating `codec.signature()` calls from the executor.
  Validation uses `Signature` attributes directly. Key types: `Pipeline`,
  `PreparedPipeline`, `StepSpec`, `WiringRef`, `InputDescriptor`,
  `ConstantDescriptor`
- **codecs** (`codecs/`): codec wrapper package. `Codec` ABC defines the
  `call(direction, port_map)` and `signature()` interface. `ComponentCodec`
  wraps a Wasmtime Component Model component (instantiation, WIT function
  lookup, encode/decode call). `CoreWasmCodec` wraps a core wasm32-wasi
  reactor module (instantiation, binary port-map serialization via
  `Memory.read`/`Memory.write`, `alloc`/`dealloc`/`encode`/`decode` calls).
  `NativeCodec` wraps a numcodecs codec object (lazy import, signature from
  bundled JSON, bytes and ndarray calling conventions via `data_format`).
  The core wasm instance is kept alive for the codec's lifetime so downstream
  core codecs can single-copy transfer from its linear memory. `CoreWasmRef`
  is a deferred reference to data in a core module's linear memory — bulk
  data stays in-place until materialized or single-copied. `CoreWasmCodec`
  returns `CoreWasmRef` entries from `call()` (lazy output parsing) and
  accepts them as input (single-copy via `ctypes.memmove`).
  `detect_codec_type()` reads the wasm binary header to distinguish core
  (`01 00 00 00`) from component (`0d 00 01 00`).
  `Signature` and `PortDescriptor` frozen dataclasses provide typed codec
  signatures; `Signature.from_dict()` converts raw JSON at codec
  instantiation. `Codec.signature()` returns `Signature`.
  Key types: `Codec`, `ComponentCodec`, `CoreWasmCodec`, `NativeCodec`,
  `CoreWasmRef`, `PortMap`, `Signature`, `PortDescriptor`
- **resolver** (`resolver.py`): codec resolution and local store. `Resolver`
  maps codec_ids to `Codec` instances via a resolution chain: explicit paths →
  per-codec overrides → local store and native (selected by preference) →
  pipeline sources download. Default preference:
  `["core", "component", "native"]`. `_scan_store()` indexes the local codec
  store directory. `_has_native_signature()` checks for bundled native
  signatures. `list_codecs()` includes both wasm and native entries.
  `CodecEntry` holds store metadata. Key types: `Resolver`, `CodecEntry`.
- **executor** (`executor.py`): execute a prepared pipeline. Entry point:
  `run(prepared, inputs)` → outputs. `_execute_forward(prepared, inputs)` /
  `_execute_inverted(prepared, inputs)` (split by direction, both use
  `value_store: dict[str, bytes | CoreWasmRef]`; encode-only sets and
  output ports come from precomputed `PreparedPipeline` fields, not
  `codec.signature()` calls). `_forward_port_map()` /
  `_inverted_port_map()` (build port-maps from value_store, materializing
  `CoreWasmRef` for non-core codecs, passing through for core codecs).
  `_materialize()` resolves deferred refs to bytes for final outputs
- **wasm_download** (`wasm_download.py`): resolve codec URIs to local paths and
  cache remote downloads. `resolve_uri()` handles `file://`, `https://`, and
  `oci://`. Downloads only the `.wasm` file (signatures are embedded as custom
  sections). Cache keyed by SHA-256 of URL (HTTPS) or reference path (OCI).
  Respects `CHONKLE_CACHE_DIR` and `CHONKLE_FORCE_DOWNLOAD` env vars.
- **wasm_signature** (`wasm_signature.py`): pure-Python reader and writer for the
  `chonkle:signature` custom section in Wasm binaries. `read_signature()` reads
  from a file path, `read_signature_bytes()` from in-memory bytes,
  `embed_signature()` appends or replaces the section. Works with both core and
  Component Model binaries. No wasmtime dependency.
- **cli** (`cli.py`): `chonkle run --pipeline pipeline.json --input name=file`
  command with `--codec-store`, `--preference`, `--override` flags for
  resolver configuration. `chonkle codecs [codec_id]` subcommand lists
  installed codec implementations. `chonkle embed-signature <wasm> <sig.json>`
  embeds a signature JSON file into a `.wasm` binary as a `chonkle:signature`
  custom section (build-time tool).

## Codec Interface (Component Model WIT)

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

Defined in `wit/codec.wit`. Component Model codecs implement this interface.
The executor locates functions via the `chonkle:codec/transform@0.1.0`
interface key in the component's exports.

## Codec Interface (Core ABI)

Core wasm codecs are wasm32-wasi reactor modules exporting:

- `memory: Memory` — linear memory
- `alloc: func(size: i32) -> i32` — allocate bytes
- `dealloc: func(ptr: i32, size: i32)` — free bytes
- `encode: func(ptr: i32, len: i32) -> i64` — encode operation
- `decode: func(ptr: i32, len: i32) -> i64` — decode operation

Port-maps are serialized to a binary wire format (little-endian u32 lengths,
UTF-8 names, raw data). Return value packs `(output_ptr << 32) | output_len`.
Error is signaled by returning `0`. Full spec in `docs/reference/CORE_ABI.md`.

## Pipeline JSON Schema

```json
{
  "codec_id": "my-pipeline",
  "direction": "encode",
  "inputs": {"bytes": {"type": "bytes"}},
  "constants": {"level": {"type": "int", "value": 3}},
  "sources": {"some-codec": "oci://ghcr.io/example/some-codec:v1"},
  "outputs": {"bytes": "step_name.port_name"},
  "steps": {
    "step_name": {
      "codec_id": "some-codec",
      "inputs": {"bytes": "input.bytes", "level": "constant.level"}
    }
  }
}
```

Field notes:

- `codec_id` at the pipeline level is required (a pipeline is itself a codec per protospec)
- Each step's key in the `steps` object is the unique DAG node identifier; used in wiring references; must be unique within the pipeline
- `codec_id` within each step is the logical codec identifier; the same codec may appear as multiple steps with different keys
- `sources` (pipeline level, optional) maps codec_ids to download URIs; used as advisory fetch hints by the `Resolver` when a codec is not in the local store
- Wiring reference forms: `input.<name>`, `constant.<name>`, `<step_name>.<port>`
- `inputs` (pipeline level) are typed dicts: `{"port": {"type": "..."}}`. Step inputs are `port_name → wiring_ref_string`. Step output ports are not declared in the pipeline; they come from codec signatures and are validated at `prepare()` time.
- `constants` are typed descriptors: `{"name": {"type": "...", "value": ...}}`; type info is stored but not validated (PoC scope)
- `encode_only` is a property of codec signature input ports, not the pipeline; the executor derives encode-only sets from signatures at `prepare()` time
- `configuration` field is absent; all codec parameters flow through port-maps via constants (JSON-encoded as bytes in the port-map)

## Codec Signature (Embedded Custom Section)

Each `.wasm` binary contains a `chonkle:signature` custom section with the
protospec codec signature as JSON. **Required** — `_validate_signature()` raises
`ValueError` if absent. The signature is embedded at build time using
`chonkle embed-signature`. Source `signature.json` files in each
codec's source directory are build inputs (not distribution artifacts).

```json
{
  "codec_id": "zstd",
  "implementation": "zstd-rs",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "level": {"type": "int", "required": false, "default": 3, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```

The `implementation` field identifies the specific build/project that produced
the binary. It is set by the implementer at build time.

The `.signature.json` sidecar convention is eliminated — signatures are read
directly from the `.wasm` binary via `read_signature()` in `wasm_signature.py`.
Downloads (`https://`, `oci://`) fetch only the `.wasm` file.

Validation rules:

- Both inputs and outputs use **subset checks** — the step need not use every port
  the codec declares
- Input validation is **direction-aware**: ports marked `encode_only: true` are
  excluded from the valid input set when running in decode direction
- Active step inputs = `step.inputs.keys() - encode_only_inputs` (the executor
  already skips encode_only_inputs during decode)

## Codec Cache

Two distinct layers:

1. **Fetch cache** (`wasm_download.py`): stores downloaded `.wasm` files locally.
   Keyed on SHA-256 of URL. Only the `.wasm` is fetched (signatures are
   embedded). Controlled by `CHONKLE_CACHE_DIR` and `CHONKLE_FORCE_DOWNLOAD`.
2. **Compilation cache**: wasmtime's built-in disk cache for compiled `.cwasm`
   artifacts. Enabled via `wasmtime.Config().cache = True`. Managed by wasmtime.

## Major Rules

- Three codec backends are implemented: Component Model Wasm
  (`ComponentCodec`), Core Wasm (`CoreWasmCodec`), and native Python
  (`NativeCodec`). All use the `Codec` ABC. Core wasm codecs use the binary
  port-map wire format (see `docs/reference/CORE_ABI.md`) instead of the
  Component Model canonical ABI. Native codecs wrap numcodecs objects;
  `numcodecs` and `numpy` are optional dependencies imported lazily.
- Each codec step has exactly one data input port and one data output port,
  both named `"bytes"` (v1 scope; write fan-out deferred)
- `encode_only` is derived from codec signatures at `prepare()` time, not
  declared in pipeline JSON. Encode-only inputs are omitted from port-maps on
  `decode` calls; included on `encode` calls (even when inverted).
- Constants are JSON-encoded as bytes and passed through port-maps alongside
  data ports
- Wiring validation is split: step existence and input/constant refs are
  checked at parse time; step output port refs are validated at `prepare()` time
  against codec signatures. Signature validation runs in a pre-execution pass
  (all steps validated before any component is called).
- Signatures are embedded in `.wasm` binaries as `chonkle:signature` custom
  sections; execution raises `ValueError` if absent
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
