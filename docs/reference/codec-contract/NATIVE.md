# Native (numcodecs)

Native codecs wrap [numcodecs](https://numcodecs.readthedocs.io/) codec objects. For a step-by-step guide, see [Adding a Native Codec](../../guides/NATIVE.md) — there is no `.wasm` binary. Using native codecs requires `numcodecs` and `numpy`: `pip install chonkle[native]`.

## Signature source

Signatures are bundled as JSON files in `src/chonkle/signatures/numcodecs/`, one file per supported codec (e.g. `zlib.json`, `delta.json`). Adding support for a new numcodecs codec requires adding a JSON file to this directory.

Native signatures use the same port-map interface as wasm codec signatures (`codec_id`, `implementation`, `inputs`, `outputs`), with an additional `native` block that controls the calling convention.

## The `native` block

The `native` block is a required top-level field in native signature JSON files. It has three fields:

- **`constructor_ports`** — list of input port names whose JSON-decoded values become keyword arguments to `numcodecs.get_codec()`. Ports not in this list are not passed to the constructor. Ports listed here but absent from the port-map at call time (e.g. encode-only ports during decode) are silently skipped.
- **`encode`** — calling recipe for the encode direction.
- **`decode`** — calling recipe for the decode direction.

Each recipe has:

- **`mode`** — `"bytes"` or `"ndarray"`.
- **`dtype_port`** — (required when mode is `"ndarray"`) name of the input port supplying the numpy dtype string for `np.frombuffer`.

### Bytes mode

The `bytes` port value is passed directly to `codec.encode()` / `codec.decode()`. Used by compression codecs (zlib, gzip, bz2, lzma, zstd, lz4, blosc) and checksum codecs (adler32, crc32, crc32c, fletcher32, jenkins_lookup3).

```json
"native": {
    "constructor_ports": ["level"],
    "encode": {"mode": "bytes"},
    "decode": {"mode": "bytes"}
}
```

### Ndarray mode (symmetric dtype)

The `bytes` port is converted to a numpy ndarray via `np.frombuffer(data, dtype=...)` before being passed to the codec. The dtype comes from the port named by `dtype_port`. Both directions use the same dtype port.

```json
"native": {
    "constructor_ports": ["dtype"],
    "encode": {"mode": "ndarray", "dtype_port": "dtype"},
    "decode": {"mode": "ndarray", "dtype_port": "dtype"}
}
```

### Ndarray mode (asymmetric dtype)

Some codecs use different dtypes for encode and decode. Each direction references a different port for the `np.frombuffer` dtype. The `dtype_port` should reference the port whose value matches the *input* dtype for that direction.

```json
"native": {
    "constructor_ports": ["encode_dtype", "decode_dtype"],
    "encode": {"mode": "ndarray", "dtype_port": "decode_dtype"},
    "decode": {"mode": "ndarray", "dtype_port": "encode_dtype"}
}
```

In this example (astype), `encode_dtype` is the output type of encode and `decode_dtype` is the output type of decode (matching the numcodecs constructor args). The `dtype_port` references are swapped because `np.frombuffer` needs the *input* type: during encode, input data has the decode dtype; during decode, input data has the encode dtype.

## Parameter handling

Port-map entries other than `bytes` are JSON-decoded. Ports listed in `constructor_ports` become keyword arguments to `numcodecs.get_codec()`. For example, a zlib codec receiving `("level", b"6")` in its port-map and `constructor_ports: ["level"]` is instantiated as `numcodecs.get_codec({"id": "zlib", "level": 6})`.

## Direction-aware ports

Input ports can be marked `encode_only: true` or `decode_only: true` in the signature. The executor omits `encode_only` ports from the port-map during decode and `decode_only` ports during encode. This is useful for codecs like packbits where `encode_dtype` is only needed during encode and `decode_dtype` only during decode.
