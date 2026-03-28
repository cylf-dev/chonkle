# chonkle

A Python host for Wasm codec pipelines, built for satellite imagery processing as part of the NASA-IMPACT VEDA ecosystem. Pipelines are directed acyclic graphs (DAGs) of codec steps defined in JSON. The orchestrator parses the DAG, validates wiring against codec signatures, and executes the pipeline via [Wasmtime](https://wasmtime.dev/).

Status: proof of concept.

## Codec backends

chonkle supports three codec backends. Each implements the same `Codec` ABC (`call(direction, port_map)` and `signature()`), so backends can be mixed freely within a single pipeline.

**Component Model Wasm** -- `.wasm` components implementing the `chonkle:codec/transform@0.1.0` WIT interface. Data transfer uses the canonical ABI (~1.7 MB/s measured throughput in the Python binding). Any language with a Component Model toolchain (Rust, C, Python via componentize-py) can implement the WIT interface. The Wasmtime sandbox isolates each component from the host.

**Core Wasm** -- wasm32-wasi reactor modules using a binary port-map wire format via `Memory.read`/`Memory.write`. Data transfer throughput is ~10 GB/s. When consecutive pipeline steps are both core wasm, data transfers between their linear memories use `ctypes.memmove` (single-copy, no serialization round-trip). Core wasm modules export `memory`, `alloc`, `dealloc`, `encode`, and `decode`. The wire format is specified in `docs/reference/CORE_ABI.md`.

**Native (numcodecs)** -- Python codecs from the [numcodecs](https://numcodecs.readthedocs.io/) library. No Wasm overhead. `numcodecs` and `numpy` are optional dependencies, imported lazily. Signatures are bundled as JSON files in `src/chonkle/signatures/numcodecs/`. Adding a new numcodecs codec requires only adding a signature file.

The `Resolver` selects among available implementations using a configurable backend preference list. The default preference order is `["native", "core", "component"]`.

## Architecture

```text
+-------------------------------------------------------+
|                      Python Host                       |
|                                                        |
|  +----------------+    +---------------------------+   |
|  |  Codec Cache   |    |       Orchestrator        |   |
|  |                |    |                           |   |
|  |  fetch .wasm   |    |  parse pipeline JSON      |   |
|  |  cache local   |    |  topological sort DAG     |   |
|  |                |    |  wire ports by name       |   |
|  |                |    |  validate signatures      |   |
|  |                |    |  drive execution loop     |   |
|  +----------------+    +---------------------------+   |
|                                                        |
+----------------------------+---------------------------+
                             |
          +------------------+------------------+
          |                  |                  |
  +-------+-------+  +------+------+  +--------+------+
  | Component     |  | Core Wasm   |  | Native        |
  | Model Wasm    |  | Module      |  | (numcodecs)   |
  |               |  |             |  |               |
  | WIT interface |  | Binary      |  | Python codec  |
  | canonical ABI |  | port-map    |  | object        |
  +---------------+  +-------------+  +---------------+
```

The Python host has two responsibilities:

**Codec cache:** Fetches `.wasm` files referenced in the pipeline JSON (via `file://`, `https://`, or `oci://` URIs) and caches them locally to avoid redundant network requests. Signatures are embedded in the `.wasm` binary as `chonkle:signature` custom sections -- no sidecar files. Wasmtime's built-in compilation cache handles compiled `.cwasm` artifacts separately.

**Orchestrator:** Parses the pipeline DAG JSON, resolves codec_ids to `Codec` instances via the `Resolver` (local store, native signatures, or pipeline source URIs), validates wiring against codec signatures, and executes the DAG by calling each codec's `encode` or `decode` in topological order. Pipeline direction inversion is supported: running a pipeline in the opposite direction reverses step order and calls the opposite function.

## Pipeline JSON

A pipeline is a DAG of codec steps. Each step names a codec by `codec_id` and wires its inputs to pipeline inputs, constants, or outputs of other steps.

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
    "width": {"type": "int", "value": 1024}
  },
  "outputs": {"bytes": "predictor2.bytes"},
  "steps": {
    "zlib": {
      "codec_id": "zlib",
      "inputs": {"bytes": "input.bytes"}
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

Key schema points:

- `codec_id` at the pipeline level identifies the pipeline itself (a pipeline is itself a codec)
- Each key in `steps` is the unique DAG node identifier, used in wiring references
- `sources` (pipeline level, optional) maps codec_ids to download URIs, used as advisory fetch hints when a codec is not in the local store
- Wiring reference forms: `input.<name>`, `constant.<name>`, `<step_name>.<port>`
- Step output ports are not declared in the pipeline; they come from codec signatures and are validated at `prepare()` time
- Constants are JSON-encoded as bytes and passed through port-maps alongside data ports

## Codec signatures

Each `.wasm` binary contains a `chonkle:signature` custom section with the codec signature as JSON:

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

Signatures are embedded at build time using `chonkle embed-signature <wasm> <sig.json>`. The `implementation` field identifies the specific build that produced the binary. Ports marked `encode_only` are excluded from the valid input set during decode.

## WIT interface (Component Model)

Defined in `wit/codec.wit`:

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

Component Model codecs implement this interface. Any language with a Component Model toolchain can produce a conforming `.wasm` component.

## Usage

### CLI

```bash
# Run a pipeline
chonkle run --pipeline pipeline.json --input bytes=chunk.bin --output bytes=out.bin

# With resolver options
chonkle run --pipeline pipeline.json --input bytes=chunk.bin \
  --codec-store ./codec/ \
  --preference core,component,native \
  --override zlib=./my-zlib.wasm

# List installed codecs
chonkle codecs

# Show details for a specific codec
chonkle codecs zlib

# Embed a signature into a .wasm binary (build-time tool)
chonkle embed-signature codec.wasm signature.json
```

### Python API

```python
from chonkle.pipeline import prepare
from chonkle.executor import run

prepared = prepare("pipeline.json", direction="decode")
outputs = run(prepared, {"bytes": chunk_bytes})
```

## Format drivers

The executor is format-agnostic. It accepts a pipeline DAG and chunk data, runs the codecs, and returns the result. It has no knowledge of Zarr, Parquet, COG, ORC, or any other file format.

A **format driver** is the layer above the executor that bridges a specific file format and the pipeline executor. It reads format-specific metadata, translates it into a pipeline DAG, supplies metadata-derived inputs, and manages chunk I/O. Format drivers are outside the scope of this repository.

## Development

- **Package manager**: uv
- **Build backend**: hatchling
- **Python**: >= 3.13
- **Linting/formatting**: ruff
- **Type checking**: mypy
- **Testing**: pytest
- **Pre-commit**: ruff check, ruff format, mypy, yaml/toml validation
- **CI**: GitHub Actions (lint on 3.14, test on 3.13 and 3.14)

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest

# Run linter
uv run ruff check

# Network tests (downloads codecs from OCI registries)
uv run pytest --run-network
```

## Acknowledgements

Partially supported by NASA-IMPACT VEDA project.
