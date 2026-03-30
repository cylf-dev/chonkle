# Native (numcodecs)

Native codecs wrap [numcodecs](https://numcodecs.readthedocs.io/) codec objects — there is no `.wasm` binary. Using native codecs requires `numcodecs` and `numpy`: `pip install chonkle[native]`.

## Signature source

Signatures are bundled as JSON files in `src/chonkle/signatures/numcodecs/`, one file per supported codec (e.g. `zlib.json`, `delta.json`). Adding support for a new numcodecs codec requires adding a JSON file to this directory.

Native signatures use the same format as wasm codec signatures, with one additional field: `data_format`.

## Data format and calling convention

The `data_format` field in the signature controls how the `bytes` port is handled:

- **`"bytes"`** (default): the `bytes` port value is passed directly to `codec.encode()` / `codec.decode()`. Used by compression codecs (zlib, gzip, bz2, lzma, zstd, lz4, blosc).
- **`"ndarray"`**: the `bytes` port is interpreted as a raw buffer and converted to a numpy ndarray using the `dtype` port (required for ndarray-format codecs). The result is converted back to bytes after processing. Used by array-level codecs (delta, shuffle).

## Parameter handling

The host deserializes parameters before passing them to the numcodecs codec. Port-map entries other than `bytes` are JSON-decoded and used as constructor kwargs via `numcodecs.get_codec()`. For example, a zlib codec receiving `("level", b"6")` in its port-map would be instantiated as `numcodecs.get_codec({"id": "zlib", "level": 6})`.

For ndarray-format codecs, the `dtype` port is used to convert the `bytes` buffer to a numpy array via `np.frombuffer(data, dtype=dtype)`.
