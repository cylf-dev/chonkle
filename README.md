# chonkle

A Python host for Wasm codec pipelines. Pipelines are directed acyclic graphs (DAGs) of codec steps defined in JSON. The orchestrator parses the DAG, validates wiring against codec signatures, and executes the pipeline via [Wasmtime](https://wasmtime.dev/).

Status: proof of concept.

## Codec backends

chonkle supports three codec backends. Each implements the same `Codec` ABC (`call(direction, port_map)` and `signature()`), so backends can be mixed freely within a single pipeline.

**Component Model Wasm** — `.wasm` components implementing the `chonkle:codec/transform@0.1.0` WIT interface. Any language with a Component Model toolchain (Rust, C, Python via componentize-py) can produce a conforming component. Data transfer uses the canonical ABI. The Wasmtime sandbox isolates each component from the host.

**Core Wasm** — wasm32-wasi reactor modules using a binary port-map wire format via `Memory.read`/`Memory.write`. When consecutive pipeline steps are both core wasm, data transfers between their linear memories use `ctypes.memmove` (single-copy, no serialization round-trip).

**Native (numcodecs)** — Python codecs from the [numcodecs](https://numcodecs.readthedocs.io/) library. No Wasm overhead. `numcodecs` and `numpy` are optional dependencies, imported lazily. Adding a new numcodecs codec requires only adding a signature file.

The `Resolver` selects among available implementations using a configurable backend preference list. The default preference order is `["native", "core", "component"]`.

## Usage

### CLI

```bash
# Run a pipeline
chonkle run pipeline.json --input bytes=chunk.bin --output bytes=out.bin

# With resolver options
chonkle run pipeline.json --input bytes=chunk.bin \
  --direction decode \
  --codec-store ./codec/ \
  --preference core,component,native \
  --override zlib=zlib-rs \
  --source zlib=https://example.com/zlib.wasm

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

## Documentation

- [docs/OVERVIEW.md](docs/OVERVIEW.md) — Architecture, design rationale, and execution model
- [docs/reference/PIPELINE_SCHEMA.md](docs/reference/PIPELINE_SCHEMA.md) — Pipeline JSON schema
- [docs/reference/codec-contract/](docs/reference/codec-contract/) — Codec interface specs (Component Model, Core Wasm, Native)
- [docs/reference/CODEC_RESOLUTION.md](docs/reference/CODEC_RESOLUTION.md) — Codec resolution chain and backend preference

See [docs/README.md](docs/README.md) for the full index.

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

# Include native (numcodecs) backend
uv sync --extra native

# Run tests
uv run pytest

# Run linter
uv run ruff check

# Network tests (downloads codecs from OCI registries)
uv run pytest --run-network
```

## Acknowledgements

Partially supported by NASA-IMPACT VEDA project.
