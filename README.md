# chonkle

A codec pipeline library for decoding (and encoding) chunked array data from formats like Zarr and COG. Pipelines can mix standard Python codecs (via [numcodecs](https://numcodecs.readthedocs.io/)) with custom codecs compiled to WebAssembly, which run at near-native speed inside a sandbox — portable, safe, and free from platform-specific build tooling. See [WASM.md](docs/WASM.md) for details on how Wasm codecs work.

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
      "name": "tiff_predictor_2",
      "type": "numcodecs",
      "configuration": {}
    },
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
      "name": "zlib",
      "type": "numcodecs",
      "configuration": {
        "level": 9
      }
    }
  ]
}
```

**Encoding** applies codecs in forward order (top to bottom): array → tiff_predictor_2 → bytes → zlib → compressed bytes.

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

## CLI

After installing, the `chonkle` command is available:

```bash
chonkle --help
```

### Decode a chunk

Each chunk requires a sidecar `.json` file describing its codec pipeline. `chonkle decode` applies the pipeline and prints a small excerpt of the result:

```bash
chonkle decode tests/fixtures/chunks/cog/0
```

```text
Shape: (1024, 1024), dtype: uint16
First 5x5:
[[1236 1386 1405 1372 1491]
 [1246 1350 1271 1310 1303]
 [1348 1194 1154 1324 1256]
 [1172 1148 1251 1369 1376]
 [1174 1176 1219 1371 1420]]
```

Save the decoded array to a `.npy` file:

```bash
chonkle decode tests/fixtures/chunks/cog/0 -o decoded.npy
```

### Encode a .npy file

Encode a `.npy` file through a codec pipeline:

```bash
chonkle encode decoded.npy --pipeline tests/fixtures/chunks/cog/0.json -o reencoded
```

## Configuration

Wasm codecs downloaded from HTTPS or OCI sources are cached locally to avoid redundant network requests.

| Variable | Description | Default |
| --- | --- | --- |
| `CHONKLE_CACHE_DIR` | Override the Wasm module cache directory | `$TMPDIR/chonkle/wasm/` |
| `CHONKLE_FORCE_DOWNLOAD` | Set to `1` to re-download cached Wasm modules, bypassing the local cache. Primarily useful for testing and development | unset |

`$TMPDIR` is the OS temporary directory (e.g. `/tmp` on Linux, `/var/folders/...` on macOS). Run `echo $TMPDIR` to see the value on your system.

## Acknowledgements

Partially supported by NASA-IMPACT VEDA project
