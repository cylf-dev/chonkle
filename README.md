# chonkle

Chonkle is a demonstrator, built to explore WebAssembly (Wasm) as a codec delivery mechanism for chunked array formats like Zarr and COG. In these formats, each chunk is encoded by a sequence (i.e., a pipeline) of codecs applied in order — bytes, compression, prediction filters, etc. Chonkle pipelines can mix standard Python codecs (via [numcodecs](https://numcodecs.readthedocs.io/)) with custom Wasm codecs that run at near-native speed inside a sandbox — portable, safe, and free from platform-specific build tooling. See [WASM.md](docs/WASM.md) for details on how Wasm codecs work. The library is functional but should be treated as a learning artifact; our understanding of Wasm is still evolving.

## Install

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

## How codec pipelines work

Each chunk has a sidecar JSON file that describes the codec pipeline used to encode it:

```json
{
  "codecs": [
    {
      "name": "bytes",
      "type": "numcodecs",
      "configuration": {
        "endian": "little",
        "data_type": "uint16",
        "shape": [1024, 1024]
      }
    },
    {
      "name": "tiff_predictor_2",
      "type": "wasm",
      "uri": "file://path/to/tiff-predictor-2-c.wasm",
      "configuration": {
        "bytes_per_sample": 2,
        "width": 1024
      }
    },
    {
      "name": "zlib",
      "type": "numcodecs",
      "configuration": {
        "level": 9
      }
    }
  ]
}
```

**Encoding** applies codecs in forward order (top to bottom): array → bytes → tiff_predictor_2 (Wasm) → zlib → compressed bytes.

**Decoding** applies codecs in reverse order, unwinding the encoding.

Each codec entry has:

- `"name"` — codec identifier (for numcodecs lookup and human readability)
- `"type"` — `"numcodecs"` or `"wasm"`
- `"configuration"` — codec-specific parameters
- `"uri"` — (Wasm only) URI of the `.wasm` module: `file://`, `https://`, or `oci://`

Python and Wasm codec steps can be freely mixed in any order. For information on how Wasm codecs work, see [WASM.md](docs/WASM.md).

## Python API

```python
from chonkle import decode, encode, get_codecs
```

### Load codec specs

`get_codecs` extracts the codec spec list from a pipeline JSON file or a dict:

```python
from pathlib import Path
from chonkle import get_codecs

codecs = get_codecs(Path("pipeline.json"))       # from a file
codecs = get_codecs({"codecs": [...]})           # from a dict
```

### Decode

`decode` applies a codec pipeline in reverse order to raw bytes, returning a numpy array:

```python
from chonkle import decode, get_codecs

codecs = get_codecs(Path("chunks/0.json"))
arr = decode(Path("chunks/0").read_bytes(), codecs)
```

### Encode

`encode` applies a codec pipeline in forward order, returning encoded bytes:

```python
from chonkle import encode

encoded = encode(arr, codecs)
```

## Demo

See [demo/](demo/) for a Jupyter notebook demonstrating the full pipeline with a real Sentinel-2 COG tile.

## CLI

A `chonkle` CLI is available for interactive use; run `chonkle --help` for usage.

## Configuration

Wasm codecs downloaded from HTTPS or OCI sources are cached locally to avoid redundant network requests.

| Variable | Description | Default |
| --- | --- | --- |
| `CHONKLE_CACHE_DIR` | Override the Wasm module cache directory | `$TMPDIR/chonkle/wasm/` |
| `CHONKLE_FORCE_DOWNLOAD` | Set to `1` to re-download cached Wasm modules, bypassing the local cache. Primarily useful for testing and development | unset |

`$TMPDIR` is the OS temporary directory (e.g. `/tmp` on Linux, `/var/folders/...` on macOS). Run `echo $TMPDIR` to see the value on your system.

## Acknowledgements

Partially supported by NASA-IMPACT VEDA project
