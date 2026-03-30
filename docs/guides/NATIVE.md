# Adding a Native (numcodecs) Codec

Native codecs wrap existing [numcodecs](https://numcodecs.readthedocs.io/) codec objects. You write a JSON signature file; chonkle handles the rest. No build step, no Wasm.

For the contract specification, see [reference/codec-contract/NATIVE.md](../reference/codec-contract/NATIVE.md).

## When to use native codecs

When numcodecs already implements the codec you need and you don't need the portability or sandboxing of Wasm. Native codecs run as regular Python/C code in-process.

## Prerequisites

- chonkle with the native extra: `pip install chonkle[native]` (brings `numcodecs` and `numpy`)

## What you create

A single JSON file at `src/chonkle/signatures/numcodecs/<codec_id>.json`. That's it.

## Step 1: Choose the data format

The `data_format` field in the signature controls how the `bytes` port is passed to the numcodecs codec:

**`"bytes"`** (default) — The `bytes` port value is passed directly to `codec.encode()` / `codec.decode()` as raw bytes. Use this for compression codecs that operate on byte streams.

Codecs using this format: zlib, gzip, bz2, lzma, zstd, lz4, blosc.

**`"ndarray"`** — The `bytes` port is converted to a numpy ndarray using the `dtype` port before being passed to the codec. The result is converted back to bytes afterward. Use this for array-level codecs whose numcodecs `encode()` expects an ndarray.

Codecs using this format: delta, shuffle.

To decide: check the numcodecs documentation for your codec. If its `encode()` method expects `bytes` or `bytearray`, use `"bytes"`. If it expects an `ndarray`, use `"ndarray"`.

## Step 2: Write the signature JSON

### Example: bytes-format codec (zlib)

`src/chonkle/signatures/numcodecs/zlib.json`:

```json
{
  "codec_id": "zlib",
  "implementation": "numcodecs.zlib",
  "data_format": "bytes",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "level": {"type": "int", "required": false, "default": 1, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```

### Example: ndarray-format codec (delta)

`src/chonkle/signatures/numcodecs/delta.json`:

```json
{
  "codec_id": "delta",
  "implementation": "numcodecs.delta",
  "data_format": "ndarray",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "dtype": {"type": "string", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```

### Field reference

- **`codec_id`** — logical codec identifier used in pipeline JSON (e.g. `"zlib"`, `"delta"`).
- **`implementation`** — must be `"numcodecs.<id>"` where `<id>` is the numcodecs codec ID passed to `numcodecs.get_codec()`.
- **`data_format`** — `"bytes"` or `"ndarray"`. Controls the calling convention as described above.
- **`inputs`** — must include a `"bytes"` port (`required: true`). Additional ports become constructor kwargs for the numcodecs codec. For ndarray-format codecs, `dtype` is required and used for the `np.frombuffer` conversion.
- **`outputs`** — must include a `"bytes"` port.

Port descriptor fields: `type` (required), `required` (default `true`), `default`, `encode_only` (default `false`). See [reference/codec-contract/README.md](../reference/codec-contract/README.md) for the full specification.

### How parameters become constructor kwargs

Port-map entries other than `bytes` (and `dtype` for ndarray codecs) are JSON-decoded and passed as keyword arguments to `numcodecs.get_codec()`. For example, a zlib codec receiving `("level", b"6")` in its port-map is instantiated as:

```python
numcodecs.get_codec({"id": "zlib", "level": 6})
```

## Step 3: Verify

```bash
chonkle list-codecs
```

The new codec should appear in the output. To test a roundtrip, create a minimal pipeline JSON and run it, or use the Python API directly:

```python
from chonkle.codecs.native import NativeCodec

codec = NativeCodec("zlib")
encoded = codec.call("encode", [("bytes", b"hello world"), ("level", b"6")])
decoded = codec.call("decode", encoded)
assert decoded[0][1] == b"hello world"
```
