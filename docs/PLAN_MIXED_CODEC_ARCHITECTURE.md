# Plan: Mixed-Codec DAG Architecture with Single-Copy Core Wasm Transfer

## Goal

Unify three codec backends — Component Model Wasm, Core Wasm, and native Python
(numcodecs) — under the existing DAG pipeline schema, while enabling a single-copy
data transfer optimization between sequential core wasm modules.

## Codec Type Taxonomy

| Type | Binary | Interface | Data transfer speed (Python host) | Copies per invocation |
|---|---|---|---|---|
| Component Model | `.wasm` (version `0d 00 01 00`) | WIT `port-map` → `result<port-map, string>` | ~1.7 MB/s (canonical ABI) | 2 |
| Core Wasm | `.wasm` (version `01 00 00 00`) | Serialized port-map via `Memory.read/write` | ~10 GB/s | 2 (or 1 with single-copy) |
| Native (numcodecs) | none | `codec.encode(bytes)` / `codec.decode(bytes)` | Python object reference | 0 |

## Codec Wrapper Classes

A `Codec` abstract base class normalizes the three backends. The executor
interacts only with this interface.

```
Codec (ABC)
├── ComponentCodec    — wraps a Component Model instance
├── CoreWasmCodec     — wraps a Core module instance, exposes Memory
└── NativeCodec       — wraps a numcodecs codec object
```

### Base class

```python
class Codec(ABC):
    name: str
    codec_type: Literal["component", "core", "native"]

    @abstractmethod
    def signature(self) -> dict:
        """Return the codec's signature (loaded at instantiation)."""
        ...

    @abstractmethod
    def call(self, direction: Direction, port_map: PortMap) -> DeferredPortMap:
        """Execute encode or decode."""
        ...
```

### ComponentCodec

- Instantiates a Wasmtime Component Model component (current `_call_component` logic)
- Passes the port-map directly to the WIT interface
- Returns `list[tuple[str, bytes]]`
- Signature loaded from `chonkle:signature` custom section in `.wasm` at init

### CoreWasmCodec

- Instantiates a Wasmtime core module; keeps `Instance`, `Store`, and `Memory` alive
- Serializes the input port-map into the module's linear memory using the core
  port-map wire format (see [Core ABI](#core-abi))
- Calls `encode`/`decode`, reads the output port-map from linear memory
- Returns `list[tuple[str, bytes | CoreWasmRef]]` — bulk data can remain in
  linear memory as a deferred reference for single-copy transfer
- Signature loaded from `chonkle:signature` custom section in `.wasm` at init

### NativeCodec

- Instantiated dynamically from any `codec_id` in the numcodecs registry
- Extracts `"bytes"` port as data input; passes remaining port-map entries as
  constructor kwargs to the numcodecs codec
- Handles bytes-to-bytes vs ndarray-to-ndarray calling convention based on the
  `data_format` field in the signature file
- Returns `list[tuple[str, bytes]]`
- Signature loaded from `src/chonkle/signatures/native/{codec_id}.json` at init
- `numcodecs` is an optional dependency — imported lazily at `NativeCodec`
  instantiation. Users who only use wasm codecs do not need it installed.
  `prepare()` raises a clear error if a pipeline contains a native step and
  numcodecs is not available

### Lifecycle

All codec instances are created during preparation (by the resolver) and remain
alive for the duration of pipeline execution. This is required
for single-copy: the source module's `Memory` must be readable when the downstream
module's input is being written.

### New module

`src/chonkle/codecs.py` — contains the `Codec` protocol and all three wrapper classes.
The existing `executor.py` `_call_component` / `_get_function` logic moves into
`ComponentCodec`.

## Signatures

### Format

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

The `implementation` field identifies the specific build/project that produced this
binary. It is set by the implementer at build time and embedded in the wasm
binary. The resolver uses it to distinguish multiple implementations of the same
codec_id in the local store.

### Source of truth

Each codec type has exactly one source of truth for its signature:

| Codec type | Signature source | Authored where |
|---|---|---|
| Component Model | `chonkle:signature` custom section in `.wasm` | `signature.json` in codec source dir (build input) |
| Core Wasm | `chonkle:signature` custom section in `.wasm` | `signature.json` in codec source dir (build input) |
| Native (numcodecs) | JSON file distributed with chonkle | `src/chonkle/signatures/numcodecs/{codec_id}.json` |

### Runtime access

Every wrapper class loads its signature at instantiation and exposes it via
`codec.signature()`. The executor calls this uniformly during validation:

```python
for step_name in execution_order:
    codec = codecs[step_name]
    sig = codec.signature()
    validate(step, sig, direction, pipeline)
```

### Wasm custom section: `chonkle:signature`

- Embedded in both core and component model `.wasm` binaries
- Added as a post-build step (after `wasm-tools component new` for C+Zig codecs,
  after `cargo component build` for Rust codecs)
- Read with a pure-Python binary parser (~30 lines, no wasmtime needed, no
  instantiation)
- ~130 bytes overhead per binary

Build integration:
```bash
python -m chonkle.tools.embed_signature codec.wasm signature.json
```

The `signature.json` in each codec's source directory is a build input (like a
Cargo.toml or header file), not a distribution artifact. It is version-controlled
with the codec source. The `.signature.json` sidecar files currently alongside
`.wasm` outputs are eliminated.

### Native codec signature files

A directory of individual JSON files, one per supported numcodecs codec:

```
src/chonkle/signatures/numcodecs/
├── blosc.json
├── zstd.json
├── lz4.json
├── gzip.json
├── zlib.json
├── bz2.json
├── lzma.json
├── delta.json
├── shuffle.json
└── ...
```

Each file is a complete signature in the standard format, plus an additional
`data_format` field (`"bytes"` or `"ndarray"`) that tells the `NativeCodec`
wrapper how to call the codec.

Adding support for a new numcodecs codec means authoring one JSON file. No Python
code changes needed. `NativeCodec("zstd")` loads the corresponding file from
`signatures/numcodecs/`; if the file does not exist, instantiation fails with a
clear error.

### What this eliminates

- `.signature.json` sidecar files alongside `.wasm` outputs
- `_derive_signature_url()` in `wasm_download.py`
- Dual-file downloads (only the `.wasm` is fetched)
- Any WIT change (WIT stays at `@0.1.0`)

## Core ABI

A formalized contract for core wasm modules, documented alongside the existing
Component Model contract in `docs/reference/`.

### Required exports

```
memory:   Memory
alloc:    func(size: i32) -> i32
dealloc:  func(ptr: i32, size: i32)
encode:   func(port_map_ptr: i32, port_map_len: i32) -> i64
decode:   func(port_map_ptr: i32, port_map_len: i32) -> i64
```

### Port-map wire format (little-endian)

```
u32: entry_count
For each entry:
  u32: name_len
  u8[name_len]: name (UTF-8)
  u32: data_len
  u8[data_len]: data
```

The host serializes the input port-map into the module's linear memory via
`Memory.write()`, calls `encode`/`decode`, and parses the output port-map from
the returned pointer.

### Return value

`i64 = (output_ptr << 32) | output_len` — pointer and length of the serialized
output port-map in linear memory.

### Error signaling

Return `(ptr=0, len=0)` on error.

### Port-map helpers

The wire format is the contract. It is simple enough to implement in any language
(~20 lines). We provide reference implementations for the languages used in our
codecs:

- C header/source in `codec/shared/` (alongside existing WIT-generated bindings)
- Rust crate (small library crate in `codec/shared/` or as a published crate)

These are convenience, not requirements. Any language that compiles to wasm32 can
implement the format from the spec.

### Relationship to Component Model WIT

The WIT interface (`chonkle:codec/transform@0.1.0`) is unchanged. Component Model
codecs continue to use WIT port-maps via the canonical ABI. Core Wasm codecs use
the binary port-map wire format via `Memory.read/write`. The executor's codec
wrapper classes bridge the difference — the executor always works with
`list[tuple[str, bytes]]` regardless of backend.

Both core and component model codecs support multi-port inputs and outputs.

## Pipeline Schema Changes

### `src` and `outputs` removed from steps

Steps are identified solely by `codec_id`. The `src` field is removed from
`StepSpec` — a step declares *what* codec to run and how it is wired, not *where*
to find the implementation. Implementation selection is the resolver's
responsibility (see [Codec Resolution](#codec-resolution)).

The `outputs` field is also removed from steps. Output port information is
available in the codec's signature (with full type descriptors), making the
step-level `outputs` list (bare port names, no types) duplicative. Wiring
reference validation (e.g., confirming that `zlib.bytes` is a real output port)
moves from parse time to `prepare()` time, where signatures are loaded. The
topological sort is unaffected — it works from input dependencies, not outputs.

A step now contains only `codec_id` and `inputs`.

### `encode_only_inputs` removed from steps

The `encode_only_inputs` field is removed from `StepSpec`. The codec's signature
already marks individual ports with `"encode_only": true`. During `prepare()`,
the executor reads each codec's signature and derives which inputs to omit in
decode direction — there is no need for the step to repeat this information. This
eliminates a redundant second source of truth and removes the ambiguity of what
should happen when the step-level list disagrees with the signature.

### `sources` field at pipeline level

An optional top-level `sources` object maps codec_ids to download URIs. This
allows software that generates pipelines to embed fetch hints for the codecs it
expects, making the pipeline self-contained for distribution without coupling
implementation details into individual steps.

- Keys are codec_ids (matching step `codec_id` values)
- Values are URI strings (`https://`, `oci://`, `file://`)
- The field is optional — all, some, or none of the codec_ids may have entries
- Sources are advisory — the resolver may ignore them if a preferred
  implementation is already available locally

### Codec type detection

Detected from the resolved binary, not from the pipeline schema:

- `.wasm` file with core version header (`01 00 00 00`) → `CoreWasmCodec`
- `.wasm` file with component version header (`0d 00 01 00`) → `ComponentCodec`
- No wasm binary (resolved via numcodecs registry) → `NativeCodec`

No `type` field in the schema. Backend type is a property of the resolved
implementation.

### Example: mixed pipeline

```json
{
  "codec_id": "cog-decode",
  "direction": "decode",
  "sources": {
    "tiff-predictor-2": "oci://ghcr.io/nasa-impact/codec-tiff-predictor-2-c:v1.0"
  },
  "inputs": {"bytes": {"type": "bytes"}},
  "constants": {
    "bytes_per_sample": {"type": "int", "value": 2},
    "width": {"type": "int", "value": 1024},
    "level": {"type": "int", "value": 9}
  },
  "outputs": {"bytes": "predictor2.bytes"},
  "steps": {
    "zlib": {
      "codec_id": "zlib",
      "inputs": {"bytes": "input.bytes", "level": "constant.level"}
    },
    "predictor2": {
      "codec_id": "tiff-predictor-2",
      "inputs": {
        "bytes": "zlib.bytes",
        "bytes_per_sample": "constant.bytes_per_sample",
        "width": "constant.width"
      }
    }
  }
}
```

Neither step has a `src` field or `encode_only_inputs`. `zlib` might resolve to
a native numcodecs codec, a core wasm binary, or a component model binary — the
resolver decides.
`tiff-predictor-2` has a source hint in the pipeline-level `sources` field; the
resolver uses it as a fetch fallback if no local implementation is available.

## Codec Resolution

### Resolver interface

The resolver maps a `codec_id` to a concrete `Codec` instance. It is passed to
`prepare()`:

```python
prepare(pipeline_json, direction, resolver=None) → PreparedPipeline
```

When `resolver` is `None`, a default resolver is constructed from environment
configuration. The CLI builds a resolver from `--codec-path`, env vars, and the
pipeline's `sources` field. Tests inject custom resolvers.

### Resolution chain

For each codec_id in the pipeline, the resolver tries sources in order:

1. **Local store** — scan the local codec store for installed implementations
   matching this codec_id. If multiple exist, select based on the preference
   system (see below).
2. **Native** — check if a signature file exists at
   `signatures/numcodecs/{codec_id}.json` and numcodecs is importable. If so,
   return a `NativeCodec`.
3. **Pipeline sources** — if the pipeline's `sources` field has an entry for
   this codec_id, fetch the artifact, install it in the local store, and
   resolve from there.
4. **Not found** — `prepare()` raises with a clear error listing the codec_id
   and what was tried.

The ordering of steps 1 and 2 is controlled by the preference system.

### Local codec store

A directory of wasm binaries organized by codec_id:

```
~/.chonkle/codecs/
├── zlib/
│   ├── a1b2c3d4.wasm    ← signature: codec_id=zlib, implementation=zlib-rs, component
│   └── e5f6g7h8.wasm    ← signature: codec_id=zlib, implementation=zlib-c, core
├── tiff-predictor-2/
│   └── f9g0h1i2.wasm    ← signature: codec_id=tiff-predictor-2, implementation=tiff-predictor-2-c, core
```

Filenames are content-addressed (hash of file contents). All identity metadata
lives in the embedded `chonkle:signature` custom section — the store imposes no
naming conventions on implementers. Backend type (core vs component) is detected
from the binary header.

The resolver scans the store directory, reads embedded signatures, and builds an
in-memory index. The scan is lightweight (custom sections are at known offsets,
the reader is ~30 lines, installed codec count is small) and can be cached by
mtime if needed.

The existing fetch cache (`CHONKLE_CACHE_DIR`, keyed by URL hash) evolves into
this store, re-keyed by codec_id and content-addressed within each codec_id
directory. The wasmtime compilation cache (`.cwasm`) remains separate and
unchanged.

### Preference system

Two levels of preference control which implementation the resolver selects:

**Global backend preference** — an ordered list of backend types. When the local
store contains multiple implementations of a codec_id, the resolver prefers
implementations whose backend type appears earlier in this list:

```python
resolver = Resolver(preference=["native", "core", "component"])
```

**Per-codec implementation override** — selects a specific implementation by its
`implementation` field value, bypassing the global backend preference:

```python
resolver = Resolver(
    preference=["native", "core", "component"],
    overrides={"zlib": "zlib-rs"},
)
```

When an override is set, the resolver looks for an implementation with that exact
`implementation` value in the local store. If not found, resolution fails — the
override is a hard constraint, not a preference.

### Discoverability

Users need to know what implementations are available to make informed override
choices.

**CLI**: `chonkle codecs` scans the local store and lists installed
implementations:

```
$ chonkle codecs
codec_id             implementation       backend
zlib                 zlib-rs              component
zlib                 zlib-c               core
zlib                 (native)             native
tiff-predictor-2     tiff-predictor-2-c   core

$ chonkle codecs zlib
codec_id: zlib
implementations:
  zlib-rs       component   ~/.chonkle/codecs/zlib/a1b2c3d4.wasm
  zlib-c        core        ~/.chonkle/codecs/zlib/e5f6g7h8.wasm
  (native)      native      numcodecs.zlib
```

**Programmatic**: `PreparedPipeline` exposes the resolved codec for each step:

```python
prepared = prepare(pipeline, direction, resolver)
for step, codec in prepared.codecs.items():
    print(f"{step}: {codec.codec_id} → {codec.implementation} ({codec.codec_type})")
```

### Configuration

Three layers with standard precedence: CLI flags > env vars > config file > defaults.

| Setting | CLI flag | Env var | Config file key | Default |
| --- | --- | --- | --- | --- |
| Store path | `--codec-store PATH` | `CHONKLE_CODEC_STORE` | `codec_store` | `~/.chonkle/codecs` |
| Backend preference | `--preference LIST` | `CHONKLE_PREFERENCE` | `preference` | `["core", "component", "native"]` |
| Per-codec override | `--override ID=IMPL` (repeatable) | — | `overrides` | `{}` |

Config file at `~/.chonkle/config.json`:

```json
{
  "codec_store": "~/.chonkle/codecs",
  "preference": ["core", "component", "native"],
  "overrides": {
    "zlib": "zlib-rs"
  }
}
```

The default preference is wasm-first (`core` > `component` > `native`) since the
purpose of chonkle is wasm codec pipelines. Users who want native-first for
development set it in their config file.

Per-codec overrides are not exposed as env vars — codec_ids can contain hyphens
which makes `CHONKLE_OVERRIDE_*` naming awkward, and the use case (benchmarking,
comparison) is interactive rather than CI-driven. CLI flags and config file
cover it.

The CLI builds the resolver by layering these sources:

```python
def default_resolver(pipeline_sources: dict[str, str] | None = None) -> Resolver:
    config = load_config()  # ~/.chonkle/config.json
    # Layer: defaults < config < env < cli
    return Resolver(
        codec_store=...,          # CLI --codec-store > CHONKLE_CODEC_STORE > config > default
        preference=...,           # CLI --preference > CHONKLE_PREFERENCE > config > default
        overrides=...,            # config overrides, then CLI --override on top
        pipeline_sources=pipeline_sources,
    )
```

## Single-Copy Transfer

### Deferred values in the value store

Currently `value_store: dict[str, bytes]` stores materialized Python bytes for
every inter-step value. For single-copy optimization, core wasm codec outputs can
remain in linear memory as deferred references.

```python
@dataclass
class CoreWasmRef:
    """Deferred reference to data in a core wasm module's linear memory."""
    codec: CoreWasmCodec
    ptr: int
    length: int

    def materialize(self) -> bytes:
        return self.codec.memory.read(self.codec.store, self.ptr, self.ptr + self.length)
```

Value store type becomes: `dict[str, bytes | CoreWasmRef]`

### Resolution rules

When building a downstream step's input port-map:

| Downstream type | Value is `bytes` | Value is `CoreWasmRef` |
|---|---|---|
| CoreWasmCodec | `Memory.write()` (1 copy) | Single-copy via `ctypes.memmove` (1 copy) |
| ComponentCodec | Canonical ABI lower (1 copy, slow) | Materialize + canonical ABI lower (2 copies) |
| NativeCodec | Use directly (0 copies) | Materialize (1 copy) |

### Single-copy mechanics

When both source and destination are core wasm modules, the host transfers data
directly between linear memories using `Memory.data_ptr()` (confirmed available
in wasmtime-py 41):

```python
def _single_copy_transfer(src_ref: CoreWasmRef, dst: CoreWasmCodec, size: int) -> CoreWasmRef:
    # Allocate in destination (does NOT grow source memory)
    dst_ptr = dst.alloc(size)

    # Get raw ctypes pointers to both memories
    src_raw = src_ref.codec.memory.data_ptr(src_ref.codec.store)
    dst_raw = dst.memory.data_ptr(dst.store)

    src_addr = ctypes.addressof(src_raw.contents) + src_ref.ptr
    dst_addr = ctypes.addressof(dst_raw.contents) + dst_ptr

    # One memcpy at C speed
    ctypes.memmove(dst_addr, src_addr, size)

    return CoreWasmRef(dst, dst_ptr, size)
```

Safety: the source pointer is obtained before the destination allocation. Since
`alloc` is called on the destination module (not the source), the source module's
memory is not grown and the source pointer remains valid.

### Lazy output port-map parsing

When a core wasm codec returns, the host parses only the output port-map metadata
(names + offsets + lengths) from linear memory. Bulk data is recorded as
`CoreWasmRef` entries in the value store, not materialized. Materialization happens
only when a non-core downstream step consumes the value.

### Fan-out

A `CoreWasmRef` can be read multiple times — `data_ptr` is a view, not a
destructive read. Each downstream step independently reads from the same memory
region. Allocation cleanup is deferred to pipeline completion.

### Fallback

If `Memory.data_ptr()` does not work as expected in practice, the fallback is
`Memory.read()` + `Memory.write()` — 2 copies at ~10 GB/s (ctypes speed). Still
orders of magnitude faster than the canonical ABI path.

## Copy Count Matrix (per inter-step edge)

| Source ↓ / Dest → | Core Wasm | Component Model | Native |
|---|---|---|---|
| **Core Wasm** | **1** (single-copy, ~10 GB/s) | 2 (materialize + canonical ABI) | 1 (materialize, ~10 GB/s) |
| **Component Model** | 2 (canonical ABI lift + Memory.write) | 2 (canonical ABI lift + lower) | 1 (canonical ABI lift) |
| **Native** | 1 (Memory.write, ~10 GB/s) | 1 (canonical ABI lower) | **0** (Python object ref) |

Notes:
- "Canonical ABI" copies are slow in Python (~1.7 MB/s)
- "Materialize" and "Memory.write/read" copies are fast (~10 GB/s)
- These are inter-step edge copies, not per-invocation totals

## Executor Refactoring

### Phases

Preparation and execution are separate entry points. A pipeline DAG must be fully
validated before any step executes.

```
prepare(pipeline_json, direction, resolver=None) → PreparedPipeline:
  1. Parse pipeline                          (pipeline.py — unchanged)
  2. Resolve codec_ids via resolver          (resolver chain: local store, native, pipeline sources)
  3. Read signatures and validate            (reads from codec.signature(), direction-aware)

run(prepared, inputs) → dict[str, bytes]:
  4. Execute DAG                             (uses codec.call(), deferred values)
  5. Cleanup                                 (dealloc core wasm memory)
```

`prepare()` raises on any validation failure: unresolvable codec_ids, missing
dependencies (e.g., numcodecs not installed for a native codec), signature
mismatches, or type incompatibility. Encode-only port filtering is derived from
the signature's `encode_only` field on each port descriptor. If `prepare()`
returns successfully, the pipeline is guaranteed executable for the given
direction.

`run()` performs no validation — it assumes a valid `PreparedPipeline`. Callers
can use `prepare()` independently to verify a pipeline without executing it (e.g.,
in CI, dry-run tooling, or editor integration).

### Execution loop

```python
for step_name in execution_order:
    codec = codecs[step_name]
    port_map = _resolve_port_map(step, value_store, codec)
    output = codec.call(direction, port_map)
    for port_name, value in output:
        value_store[f"{step_name}.{port_name}"] = value
```

`_resolve_port_map` handles deferred value resolution: detects `CoreWasmRef`
entries and applies single-copy transfer or materialization based on the
downstream codec type.

### What moves out of executor.py

- `_call_component`, `_get_function` → `ComponentCodec`
- Signature file reading → `read_custom_section()` utility
- URI resolution → resolver (replaces `wasm_download.py` for codec lookup)
- Signature validation logic stays in `executor.py`, exposed through `prepare()` (operates on `codec.signature()` dicts)

## Implementation Phases

### Phase 1: Signature embedding infrastructure

- Add `implementation` field to signature format
- Implement `chonkle:signature` custom section writer (`chonkle.tools.embed_signature`)
- Implement pure-Python custom section reader
- Embed signatures into existing codec `.wasm` files (move `signature.json` to
  source dirs, add post-build embed step)
- Remove `.signature.json` sidecar files from codec output directories
- Update executor signature validation to read from custom section via a utility function

### Phase 2: Codec wrapper classes and `prepare()`/`run()` split

- Create `src/chonkle/codecs.py` with `Codec` ABC
- Implement `ComponentCodec` (refactor from current `_call_component`)
- Add binary header detection for core-vs-component routing
- Split executor into `prepare()` (validation) and `run()` (execution)
- Existing Component Model tests continue to pass (no behavioral change)

### Phase 3: Codec resolution and local store

- Remove `src` and `outputs` from `StepSpec` in `pipeline.py`
- Add `sources` field to pipeline schema (optional, pipeline-level)
- Implement `Resolver` with resolution chain (local store → native → pipeline sources)
- Implement local codec store (content-addressed, indexed by embedded signature)
- Implement global backend preference and per-codec implementation overrides
- Replace `wasm_download.py` with local codec store (clean break, no migration)
- Add `chonkle codecs` CLI command for discoverability
- Expose resolved codec metadata on `PreparedPipeline`
- Update all pipeline fixture JSONs to remove `src` fields

### Phase 4: Core ABI

- Define port-map wire format specification (reference doc)
- Implement `CoreWasmCodec` (core ABI with serialized port-map)
- Provide C reference implementation in `codec/shared/`
- Provide Rust reference implementation
- Build a test core wasm codec (adapt identity-c to export core ABI without
  lifting to Component Model)
- Add tests for core wasm steps in DAG pipelines

### Phase 5: Single-copy optimization

- Introduce `CoreWasmRef` and widen value store type
- Implement lazy output port-map parsing for core codecs
- Implement `_single_copy_transfer` (ctypes path, with Memory.read/write fallback)
- Implement `_resolve_port_map` with type-aware deferred value resolution
- Add tests with sequential core wasm steps verifying single-copy behavior
- Add benchmarks comparing transfer strategies

### Phase 6: Native (numcodecs) integration

- Create `src/chonkle/signatures/numcodecs/` with signature files for supported codecs
- Implement `NativeCodec` (dynamic instantiation from numcodecs registry)
- Handle bytes-to-bytes and ndarray-to-ndarray calling conventions via `data_format`
- Add `numcodecs` as an optional dependency (lazy import in `NativeCodec`)
- Add tests for native codec steps and mixed pipelines

### Phase 7: Documentation

- New reference doc: Core ABI contract (port-map wire format, exports, error signaling)
- Rewrite `README.md` (no longer component-only, architecture diagram updated for three backends, pipeline example updated for new schema, "Why the Component Model" section revised, sidecar and `src`/`outputs`/`encode_only_inputs` references removed)
- Update `docs/CODEC_RUNTIME.md` (no longer component-only, core ABI reintroduced, sidecar references removed, copy count claims updated for single-copy transfer)
- Update `docs/README.md` (add entry for new Core ABI reference doc)
- Update `docs/reference/PIPELINE_SCHEMA.md` (`src`, `outputs`, and `encode_only_inputs` removed from steps, `sources` added, codec type detection)
- Update `docs/reference/CODEC_CONTRACT.md` (both Component Model and Core ABI)
- Update `docs/internals/CACHING.md` (fetch cache replaced by content-addressed local codec store)
- Update `docs/internals/DATA_COPIES.md` (new copy count matrix)
- Update `docs/ai/SYSTEM_OVERVIEW.md` (codec classes, resolver, signatures)
- Update `docs/ai/REPO_MAP.md` (new files and directories)
- Update `docs/wasm/MULTI_MEMORY.md` ("current design" section and comparison table reflect separate-memory single-copy approach, not shared-memory zero-copy)
- Update `codec/README.md` (core ABI build process, signature embedding)
- Remove all references to sidecar files and `src` in steps

## Open Questions

**Core ABI error handling**: `(ptr=0, len=0)` return is minimal. A future revision
could add an `error_message` export or encode errors in the output port-map. For
now, the simple convention matches the existing main-branch behavior.

**ndarray codecs**: Native codecs that operate on ndarray (delta, shuffle, quantize)
require the wrapper to convert `bytes → ndarray → codec → ndarray → bytes`. The
`data_format` field in the signature file controls this. The dtype must flow through
the port-map as a config parameter. This is workable but adds complexity to the
NativeCodec wrapper.

**`data_ptr()` reliability**: Confirmed available in wasmtime-py 41. Needs empirical
testing under the single-copy transfer pattern. If it doesn't work as expected,
the fallback (Memory.read + Memory.write at ctypes speed) is still a large
improvement over the canonical ABI path.

**Custom section preservation**: Custom sections added to the `.wasm` must be added
after the Component Model lift step (`wasm-tools component new`), not before.
Sections added to the core module get nested inside the component and are not
visible to a top-level parser.

**WIT interface stability**: The WIT stays at `@0.1.0`. No changes are needed for
any part of this plan. A future bump would only be needed if the WIT interface
itself changes (e.g., adding resource types for streaming).

**Remote resolution details**: The pipeline-level `sources` field provides fetch
hints, but there is no automatic mapping from codec_id to a remote URI. Codec
artifacts are currently stored as GitHub release artifacts and GHCR OCI artifacts
with repo names that do not follow a derivable convention from codec_id. For now,
remote fetching requires explicit `sources` entries. A future registry service or
organizational naming convention could enable automatic remote resolution, but
this is deferred.
