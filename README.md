# Chonkle

A chunk decoding demonstrator, currently applied to chunks from common geospatial data stores like Zarr and COG. Decoding is driven by metadata-described codec pipelines that can mix Python codecs (via [numcodecs](https://numcodecs.readthedocs.io/)) and sandboxed [WASM codecs](WASM.md).

## Install

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
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

Produces output like:

```text
Shape: (1024, 1024), dtype: uint16
First 5x5:
[[1236 1386 1405 1372 1491]
 [1246 1350 1271 1310 1303]
 [1348 1194 1154 1324 1256]
 [1172 1148 1251 1369 1376]
 [1174 1176 1219 1371 1420]]
```

### COG operations

Utility commands for fetching and inspecting COG files and creating test chunks from them:

```bash
# Download a COG from a public URL
chonkle cog download https://example.com/image.tif

# Convert a local COG to Zarr v3
chonkle cog to-zarr image.tif image.zarr

# Print TIFF metadata
chonkle cog metadata image.tif

# Extract a single tile as raw bytes
chonkle cog extract-tile image.tif 0
```

## How decoding works

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
      "configuration": {}
    }
  ]
}
```

The decode pipeline applies codecs in **reverse order**, unwinding the encoding. Each codec entry has:

- `"name"` — codec identifier (for numcodecs lookup and human readability)
- `"type"` — `"numcodecs"` or `"wasm"`
- `"configuration"` — codec-specific parameters
- `"uri"` — (WASM only) URI of the `.wasm` module

Python and WASM codec steps can be freely mixed in any order. For information on how WASM codecs work, see [WASM.md](WASM.md).
