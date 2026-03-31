# Adding a Native (numcodecs) Codec

Native codecs wrap existing [numcodecs](https://numcodecs.readthedocs.io/) codec objects. You write a JSON signature file; chonkle handles the rest. No build step, no Wasm.

For the contract specification, see [reference/codec-contract/NATIVE.md](../reference/codec-contract/NATIVE.md).

## When to use native codecs

When numcodecs already implements the codec you need and you don't need the portability or sandboxing of Wasm. Native codecs run as regular Python/C code in-process.

## Prerequisites

- chonkle with the native extra: `pip install chonkle[native]` (brings `numcodecs` and `numpy`)

## What you create

A single JSON file at `src/chonkle/signatures/numcodecs/<codec_id>.json`. That is all.

## Step 1: Determine the calling convention for each direction

The `native` block in the signature JSON specifies a calling recipe for each direction (`encode` and `decode`) independently. Each recipe has a `mode`:

**`"bytes"`** — The `bytes` port value is passed directly to `codec.encode()` / `codec.decode()` as raw bytes. Use this for compression codecs that operate on byte streams (zlib, gzip, bz2, lzma, zstd, lz4, blosc, checksum codecs).

**`"ndarray"`** — The `bytes` port is converted to a numpy ndarray using a dtype from a named port before being passed to the codec. The result is converted back to bytes afterward so it conforms to the port-map interface shared by all codec backends (`list[tuple[str, bytes]]`). When two native ndarray codecs are chained, this means a redundant `tobytes()` / `frombuffer()` round-trip at each step boundary; `frombuffer` is zero-copy but `tobytes()` allocates and copies. In practice this is cheap relative to the codec work itself. Use this mode for array-level codecs whose numcodecs `encode()` expects an ndarray (delta, shuffle, bitround, astype, packbits, categorize).

Most codecs use the same mode for both directions, but this is not required — a codec can use `"bytes"` for one direction and `"ndarray"` for the other. Each direction can also reference a different dtype port, which is how asymmetric-dtype codecs (astype, packbits, categorize) work.

To decide the mode for each direction: check the numcodecs documentation for your codec. If its `encode()` / `decode()` method expects `bytes` or `bytearray`, use `"bytes"` for that direction. If it expects an `ndarray`, use `"ndarray"`.

## Step 2: Write the signature JSON

Every signature has two parts: the standard port-map interface (`codec_id`, `implementation`, `inputs`, `outputs`) shared with wasm codecs, and the `native` block that controls the calling convention.

### Signature fields

Standard fields:

- **`codec_id`** — logical codec identifier used in pipeline JSON (e.g. `"zlib"`, `"delta"`).
- **`implementation`** — must be `"numcodecs.<id>"` where `<id>` is the numcodecs codec ID passed to `numcodecs.get_codec()`.
- **`inputs`** — must include a `"bytes"` port (`required: true`). Additional ports become parameters available to the `native` block.
- **`outputs`** — must include a `"bytes"` port.

Port descriptor fields: `type` (required), `required` (default `true`), `default`, `encode_only` (default `false`), `decode_only` (default `false`). See [reference/codec-contract/README.md](../reference/codec-contract/README.md) for the full specification.

Native block fields:

- **`constructor_ports`** — list of input port names whose JSON-decoded values become kwargs to `numcodecs.get_codec()`. Ports not in this list are not passed to the constructor. Ports listed here but absent from the port-map (e.g. encode-only ports during decode) are silently skipped.
- **`encode`** / **`decode`** — calling recipe per direction. `mode` is `"bytes"` or `"ndarray"`. When mode is `"ndarray"`, `dtype_port` (required) names the input port supplying the numpy dtype string for `np.frombuffer`.

### Examples

#### Bytes-mode codec (zlib)

`src/chonkle/signatures/numcodecs/zlib.json`:

```json
{
  "codec_id": "zlib",
  "implementation": "numcodecs.zlib",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "level": {"type": "int", "required": false, "default": 1, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  },
  "native": {
    "constructor_ports": ["level"],
    "encode": {"mode": "bytes"},
    "decode": {"mode": "bytes"}
  }
}
```

`constructor_ports` lists the input port names whose values become kwargs to `numcodecs.get_codec()`. Here, `level` is passed as `numcodecs.get_codec({"id": "zlib", "level": 6})`. The `bytes` port is never a constructor kwarg.

#### Symmetric ndarray-mode codec (delta)

`src/chonkle/signatures/numcodecs/delta.json`:

```json
{
  "codec_id": "delta",
  "implementation": "numcodecs.delta",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "dtype": {"type": "string", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  },
  "native": {
    "constructor_ports": ["dtype"],
    "encode": {"mode": "ndarray", "dtype_port": "dtype"},
    "decode": {"mode": "ndarray", "dtype_port": "dtype"}
  }
}
```

`dtype_port` names the input port supplying the numpy dtype string for `np.frombuffer`. Both directions use the same dtype, so both point to `"dtype"`.

#### Ndarray-mode codec where dtype is not a constructor arg (shuffle)

`src/chonkle/signatures/numcodecs/shuffle.json`:

```json
{
  "codec_id": "shuffle",
  "implementation": "numcodecs.shuffle",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "dtype": {"type": "string", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  },
  "native": {
    "constructor_ports": [],
    "encode": {"mode": "ndarray", "dtype_port": "dtype"},
    "decode": {"mode": "ndarray", "dtype_port": "dtype"}
  }
}
```

`Shuffle()` takes no constructor args — `constructor_ports` is empty. The `dtype` port is used only for the `np.frombuffer` conversion, not passed to the constructor.

#### Asymmetric-dtype codec (astype)

`src/chonkle/signatures/numcodecs/astype.json`:

```json
{
  "codec_id": "astype",
  "implementation": "numcodecs.astype",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "encode_dtype": {"type": "string", "required": true},
    "decode_dtype": {"type": "string", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  },
  "native": {
    "constructor_ports": ["encode_dtype", "decode_dtype"],
    "encode": {"mode": "ndarray", "dtype_port": "decode_dtype"},
    "decode": {"mode": "ndarray", "dtype_port": "encode_dtype"}
  }
}
```

AsType converts between dtypes. `encode_dtype` is the output dtype of encode; `decode_dtype` is the output dtype of decode (matching the numcodecs constructor args). The `dtype_port` references are swapped: during encode, the input buffer has the decode dtype (the original type); during decode, the input buffer has the encode dtype (the encoded type).

## Step 3: Verify

```bash
chonkle codecs
```

The new codec should appear in the output. To test a roundtrip, use the Python API directly:

```python
from chonkle.codecs.native import NativeCodec

codec = NativeCodec("zlib")
encoded = codec.call("encode", [("bytes", b"hello world"), ("level", b"6")])
decoded = codec.call("decode", encoded)
assert decoded[0][1] == b"hello world"
```
