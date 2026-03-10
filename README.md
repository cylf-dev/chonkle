# chonkle — DAG codec pipeline

A next-generation codec pipeline executor for chunked array and columnar data
formats (Zarr, COG, Parquet, ORC, Arrow). Built on the
[WebAssembly Component Model](https://component-model.bytecodealliance.org/)
and executed by a Python orchestrator backed by
[Wasmtime](https://wasmtime.dev/).

This branch is a direct successor to the original chonkle proof of concept,
which demonstrated that WebAssembly is a viable codec delivery mechanism for
chunked array formats. That proof of concept mixed Python (numcodecs) and Wasm
codecs in a simple linear pipeline. This branch takes the next step: all codecs
are Wasm components, pipelines are directed acyclic graphs (DAGs), and the
interface contract is machine-verifiable via WIT.

---

## What chonkle established

chonkle showed that:

- Wasm codecs can be fetched remotely, cached locally, and executed in a
  sandboxed runtime
- Codecs can be authored in multiple languages — Rust, C, and Python (via
  componentize-py), among others — each compiled to Wasm by their respective
  toolchains
- Python and Wasm codecs can be freely mixed in a pipeline

What chonkle did not address: pipelines where codec steps have multiple inputs
or outputs (fan-in / fan-out), and the data copy overhead of moving chunk data
between steps via Python.

---

## Why the Component Model

A real-world codec pipeline is not a simple linear chain. Consider a Parquet
column page: it must be decompressed, then split into three separate streams
(repetition levels, definition levels, and values), each of which is then
decoded independently. That is a 1-to-3 fan-out. Dictionary-encoded columns
require two inputs to a single codec step — the encoded indices and a
separately-stored dictionary page. That is a 2-to-1 fan-in.

Modeling these pipelines correctly requires named, typed ports — not just
`bytes in, bytes out`. The WebAssembly Component Model provides this through
WIT (WebAssembly Interface Types): every codec component must export `encode`
and `decode` functions that accept a named port map and a configuration blob.
Wasmtime verifies the contract at instantiation time, rejecting a component
with wrong signatures immediately. The specific ports a codec expects inside
the port map are declared in a JSON manifest sidecar and validated by the
orchestrator before any component is called.

The Component Model also makes this practical at ecosystem scale. Any language
with a Component Model toolchain — Rust, C, Python via componentize-py, and
others — can implement the WIT interface, so codec authors work in whatever
language suits them and participate in the same ecosystem without needing to
know each other's. Each component runs in the Wasmtime sandbox with no access
to the host filesystem, network, or memory outside its own linear region, so
distributing and executing third-party codecs is safe by construction.

The alternative — core Wasm modules with a bespoke multi-port calling
convention — could support fan-in/fan-out in principle, but would require
every codec author to implement a shared protocol with no tooling support and
no interface verification. The Component Model removes that burden.

---

## Data copies: current approach vs. original chonkle

One of the original motivations for moving all codecs to Wasm was reducing the
number of times chunk data is copied as it passes through a pipeline. The
following is a per-step accounting of data copies for each approach.

### chonkle (hybrid Python + Wasm, linear pipeline)

When two consecutive Wasm codec steps execute in chonkle, the data path is:

1. Wasm codec A writes its output into its own linear memory.
2. **Python reads that output into a Python `bytes` object** — a heap
   allocation in Python's memory. *(Copy 1)*
3. Python passes the `bytes` object into the next Wasm codec call.
4. **Wasmtime writes it into codec B's linear memory.** *(Copy 2)*

Two copies per inter-codec edge, with Python's object model and allocator
involved at each crossing.

### This branch (Component Model, Python orchestrator)

When two consecutive Wasm component steps execute here, the data path is:

1. Component A writes its output into its own linear memory.
2. **Wasmtime's canonical ABI copies it directly into component B's linear
   memory** — entirely within Wasmtime's native memory management, never
   touching Python's heap. *(Copy 1)*

One copy per inter-codec edge. Python's role between steps is to hold a
reference and route it to the next component call — bookkeeping, not data
movement.

### Why this matters

The copy that remains is a `memcpy`-class operation inside Wasmtime, running
at memory bandwidth speeds in native code, with no Python object allocation,
no reference counting, and no GIL involvement. The data involved is likely
still warm in CPU cache from the codec that just wrote it.

This is not zero-copy. Eliminating the remaining copy would require moving the
orchestrator itself into Wasm — which is architecturally possible but not
practical today, as dynamic component linking in the Component Model is still
maturing. The Python orchestrator is an explicit design decision: it keeps the
executor flexible and iterable, and the single-copy cost is acceptable for the
chunk sizes (KB to low MB) typical in chunked array formats, where codec
execution time is expected to dominate over data movement time.

---

## Architecture overview

```
+------------------------------------------------------+
|                     Python Host                      |
|                                                      |
|  +----------------+   +---------------------------+  |
|  |  Codec Cache   |   |       Orchestrator        |  |
|  |                |   |                           |  |
|  |  fetch .wasm   |   |  parse pipeline JSON      |  |
|  |  cache local   |   |  topological sort DAG     |  |
|  |                |   |  wire ports by name       |  |
|  |                |   |  drive execution loop     |  |
|  +----------------+   +---------------------------+  |
|                                                      |
+----------------------------+-------------------------+
                             | Wasmtime
             +---------------+---------------+
             |               |               |
     +-------+-------+ +-----+-------+ +-----+-------+
     | Component A   | | Component B | | Component C |
     | (Rust)        | | (C)         | | (Python)    |
     |               | |             | |             |
     | WIT: encode   | | WIT: encode | | WIT: encode |
     |      decode   | |      decode | |      decode |
     +---------------+ +-------------+ +-------------+
```

The Python host has two responsibilities:

**Codec cache:** Fetches `.wasm` component files and their `.manifest.json`
sidecars referenced in the pipeline JSON (via `file://`, `https://`, or
`oci://` URIs) and caches them locally to avoid redundant network requests.
Wasmtime's built-in compilation cache handles compiled `.cwasm` artifacts
separately and transparently.

**Orchestrator:** Parses the pipeline DAG JSON, resolves wiring between step
outputs and inputs by name, determines execution order via topological sort,
validates each step's port declarations against its manifest before execution,
and drives the Wasmtime execution loop — calling each component's `encode` or
`decode` function in order and routing the named port outputs to the
appropriate next step inputs.

---

## Pipeline JSON

A pipeline is a directed acyclic graph of codec steps. Each step names a codec
component (by URI) and wires its inputs to pipeline inputs, constants, or
outputs of previous steps.

```json
{
  "codec_id": "parquet-page-decode",
  "direction": "decode",
  "inputs": {
    "bytes": {"type": "bytes"},
    "rep_length": {"type": "uint"},
    "def_length": {"type": "uint"}
  },
  "constants": {
    "index_bit_width": {"type": "int", "value": 4},
    "def_bit_width": {"type": "int", "value": 1},
    "rep_bit_width": {"type": "int", "value": 1}
  },
  "outputs": {
    "values": "decode_values.bytes",
    "def": "decode_def.bytes",
    "rep": "decode_rep.bytes"
  },
  "steps": [
    {
      "name": "decompress",
      "codec_id": "zstd",
      "src": "https://example.org/codecs/zstd.wasm",
      "inputs": {
        "bytes": "input.bytes"
      },
      "outputs": {"bytes": {"type": "bytes"}},
      "encode_only_inputs": []
    },
    {
      "name": "split",
      "codec_id": "parquet-page-v2-split",
      "src": "https://example.org/codecs/parquet-page-v2-split.wasm",
      "inputs": {
        "bytes": "decompress.bytes",
        "rep_length": "input.rep_length",
        "def_length": "input.def_length"
      },
      "outputs": {
        "rep_bytes": {"type": "bytes"},
        "def_bytes": {"type": "bytes"},
        "value_bytes": {"type": "bytes"}
      },
      "encode_only_inputs": []
    },
    {
      "name": "decode_values",
      "codec_id": "rle-parquet",
      "src": "https://example.org/codecs/rle-parquet.wasm",
      "inputs": {
        "bytes": "split.value_bytes",
        "bit_width": "constant.index_bit_width"
      },
      "outputs": {"bytes": {"type": "bytes"}},
      "encode_only_inputs": []
    },
    {
      "name": "decode_def",
      "codec_id": "rle-parquet",
      "src": "https://example.org/codecs/rle-parquet.wasm",
      "inputs": {
        "bytes": "split.def_bytes",
        "bit_width": "constant.def_bit_width"
      },
      "outputs": {"bytes": {"type": "bytes"}},
      "encode_only_inputs": []
    },
    {
      "name": "decode_rep",
      "codec_id": "rle-parquet",
      "src": "https://example.org/codecs/rle-parquet.wasm",
      "inputs": {
        "bytes": "split.rep_bytes",
        "bit_width": "constant.rep_bit_width"
      },
      "outputs": {"bytes": {"type": "bytes"}},
      "encode_only_inputs": []
    }
  ]
}
```

> **Note:** This example illustrates a realistic Parquet decode pipeline using
> hypothetical codecs (`rle-parquet.wasm`). The actual fixture in
> `tests/fixtures/pipelines/page-split-dag.json` uses the `identity` codec for
> the downstream steps, since `rle-parquet` is not built in this PoC.

Each step declares both its `inputs` (wiring references) and its `outputs`
(port names). Technically, the orchestrator could derive output port names
entirely from each codec's manifest, making `outputs` in the pipeline JSON
redundant. It is included here deliberately: a pipeline should be readable
and verifiable by a human without cross-referencing external manifests.
The orchestrator validates the declared `outputs` against the codec manifest
at parse time — a mismatch is an error.

---

## Format drivers and the executor boundary

This executor is format-agnostic. It accepts a pipeline DAG and chunk data,
runs the codecs in order, and returns the result. It has no knowledge of
Zarr, Parquet, COG, ORC, or any other file format.

A **format driver** is the layer above the executor that bridges a specific
file format and this executor. Its responsibilities are:

- Read format-specific metadata (Zarr `.zarray`, Parquet page headers, TIFF
  IFDs, etc.) and translate it into a pipeline DAG JSON that this executor
  can run
- Supply pipeline inputs that come from file metadata rather than chunk data
  — for example, `rep_length` and `def_length` from a Parquet page header,
  or `element_size` from a Zarr array descriptor
- Manage chunk I/O — fetching raw chunk bytes from storage and passing them
  to the executor, then storing the results

Format drivers are outside the scope of this repository. The pipeline JSON
examples here are written as a format driver would produce them, not as
something a user would typically author by hand. In production use, pipelines
would be generated programmatically from file metadata, not hand-rolled.

---

## Relationship to the codec inventory

This branch is designed to align with the codec signature and pipeline
model defined in
[protospec/codec-inventory.md](https://github.com/cylf-dev/protospec/blob/main/codec-inventory.md).
The wiring syntax (`input.<name>`, `<step>.<output>`, `constant.<name>`),
the typed port model, and the distinction between `encode_only` and
bidirectional inputs all follow the inventory's definitions.

The inventory is still in progress. Known divergences:

- **`src` is required.** The inventory envisions `codec_id` as sufficient for
  lookup once a codec registry exists; `src` will become optional then.
- **`configuration` is absent by design.** All codec parameters flow through
  the port-map via constants. The `cfg` argument passed to each codec component
  is always `b"{}"`. Type descriptors on inputs, outputs, and constants are
  stored but not yet validated (no type-checking logic — PoC scope).

---

## Status

Proof of concept. Not production ready.

---

## Acknowledgements

Partially supported by NASA-IMPACT VEDA project.
