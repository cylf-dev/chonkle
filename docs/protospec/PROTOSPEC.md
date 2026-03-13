# Codec Invetory

Codec inventory for chunked array and columnar data formats. Each codec is a
named, versioned transformation with typed inputs and outputs. Codecs compose
into pipelines; a pipeline is itself a codec.

Entries flagged ⚠️ have lower confidence and should be verified against primary
sources before finalizing.

Note this document in its current form has been compiled entirely by an LLM,
and is still in progress.

---

## Codec Signature

A codec signature defines the transformation interface: typed inputs and typed
outputs. Every codec in this catalog has a signature. A pipeline is also a codec
and has the same signature structure.

### Types

| Type         | Description                                         |
|--------------|-----------------------------------------------------|
| `bytes`      | A byte stream                                       |
| `uint`       | Unsigned integer scalar                              |
| `int`        | Signed integer scalar                                |
| `float`      | Floating point scalar                                |
| `bool`       | Boolean scalar                                       |
| `string`     | String value (e.g. dtype name, encoding mode)        |
| `uint[]`     | Array of unsigned integers (e.g. shape, permutation) |
| `dtype_desc` | Dtype descriptor (base type, width, signedness)      |

### Input properties

| Property      | Description                                                     |
|---------------|-----------------------------------------------------------------|
| `type`        | One of the types above                                          |
| `required`    | Whether the input must be provided (default: `true`)            |
| `default`     | Default value if not provided                                   |
| `encode_only` | If `true`, only needed during encoding, not decoding            |

### Codec identification

Each codec is identified by a URI that includes a version. The URI scheme is
TBD; identifiers used in this catalog are short slugs (e.g. `zstd`, `shuffle`)
that do not yet conform to the final URI scheme.

### Example signature

```json
{
  "codec_id": "zstd",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "level": {"type": "int", "required": false, "default": 3, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```

`zstd` takes one byte stream and an optional compression level (encode-only),
and produces one byte stream. On decode, only `bytes` is required.

---

## Pipeline Model

A pipeline is a codec. It has typed inputs and outputs, and internally is a
directed acyclic graph (DAG) of steps. Each step invokes a codec and wires its
inputs/outputs to other steps or to the pipeline boundary.

### Wiring rules

- Step inputs reference either `input.<n>` (pipeline input),
  `constant.<n>` (pipeline constant), or `<step_name>.<output_name>`
  (output of a previous step).
- Pipeline outputs reference `<step_name>.<output_name>`.
- Steps execute in dependency order (topological sort of the DAG).
- A value can be referenced by multiple steps (fan-out / reuse).

### Direction and inversion

A pipeline declares a `direction`: `"decode"` or `"encode"`. This tells the
executor which way the DAG was written. The examples in this catalog use the
decode direction. A codec executor must be able to invert a pipeline regardless
of which direction is declared.

Individual codecs do not have a direction — every codec is bidirectional by
definition. A codec encodes and decodes. The direction flag exists only on the
pipeline to specify how to read the DAG.

To run a pipeline in the opposite direction from the one declared:

1. Reverse the DAG edges (each step's outputs become its inputs and vice versa).
2. Pipeline inputs become pipeline outputs and vice versa.
3. Constants remain constants.
4. `encode_only` inputs are active when encoding, ignored when decoding.

The executor calls each codec's encode or decode operation depending on which
direction is needed. No per-step inversion logic is required beyond this — each
codec already knows how to operate in both directions.

### Constants

Constants are values fixed for a given pipeline definition. They differ from
inputs in that they are not supplied by the caller — they are baked into the
pipeline spec. Constants have a type and a value.

### Self-describing vs externally-parameterized codecs

A codec whose decode parameters are embedded in the chunk bytes (self-describing)
is atomic — it cannot be decomposed into a pipeline, because the format driver
would need to parse the codec's internal header to supply parameters to sub-steps.
The chunk goes into the codec as-is.

A codec whose decode parameters come from external metadata (the format driver
supplies them) can be a step in a pipeline, because the format driver provides
the parameters as pipeline inputs.

Example: Blosc embeds its compressor name, shuffle mode, and element size in a
header within the chunk bytes → atomic codec. Zstd does not embed any metadata
in its frame that the format driver needs to extract → pipeline step.

### Pipeline examples

Composite codecs that can be decomposed into pipelines of generic codecs
have their pipeline definition colocated with their catalog entry. See:

- [`orc-varint`](#orc-varint) — linear pipeline: `varint → zigzag`
- [`parquet-page-v2-split`](#parquet-page-v2-split) — DAG with fan-out, multiple inputs, constants

---

## Catalog Fields

Each codec entry includes:

- **canonical-id**: Primary key slug (will become a versioned URI; see above)
- **aliases**: `{format, identifier}` pairs
- **awareness**: `pure-bytes` | `stride-aware` | `type-aware` | `structure-aware`
- **lossiness**: `lossless` | `lossy` | `conditional`
- **signature**: JSON codec signature (typed inputs and outputs)
- **pipeline**: (where applicable) pipeline decomposition into generic codecs
- **conceptual decomposition**: (where applicable) decomposition not directly expressible as a pipeline
- **spec**: Link(s) to canonical specification
- **implementations**: Known implementations
- **licensing**: Any restrictions
- **notes**: Caveats, open questions

---

## Codec Inventory

### Awareness Categories

Awareness describes what a codec needs to know about the data it operates on,
beyond the raw bytes.

#### [Pure-Bytes](#pure-bytes-codecs)

Codec treats input as opaque bytes. No knowledge of element boundaries, data
types, or dimensional structure.

- [`zstd`](#zstd) · [`lz4`](#lz4) · [`deflate`](#deflate) · [`gzip`](#gzip) · [`bzip2`](#bzip2) · [`lzma`](#lzma) · [`snappy`](#snappy) · [`brotli`](#brotli) · [`lzo`](#lzo) · [`lzw`](#lzw) · [`lzw-tiff`](#lzw-tiff) · [`packbits`](#packbits) · [`szip`](#szip) · [`blosc`](#blosc) · [`jpeg-xl`](#jpeg-xl) · [`jpeg`](#jpeg) · [`jpeg2000`](#jpeg2000) · [`webp`](#webp) · [`jpeg-xr`](#jpeg-xr) · [`no-compression`](#no-compression) · [`fletcher32`](#fletcher32) · [`crc32c`](#crc32c) · [`crc32`](#crc32) · [`orc-boolean-rle`](#orc-boolean-rle)

#### [Stride-Aware](#stride-aware-codecs)

Codec needs to know element width (byte stride or bit width) to operate on
element boundaries, but does not need to know the semantic type.

- [`shuffle`](#shuffle) · [`bitshuffle`](#bitshuffle) · [`byte-stream-split`](#byte-stream-split) · [`rle-parquet`](#rle-parquet) · [`rle`](#rle)

#### [Type-Aware](#type-aware-codecs)

Codec needs to know the semantic data type (signed vs unsigned, float vs int,
physical type) to correctly encode or decode values.

- [`bitpack`](#bitpack) · [`fsst`](#fsst) · [`nbit`](#nbit) · [`scale-offset`](#scale-offset) · [`lerc`](#lerc) · [`delta-parquet`](#delta-parquet) · [`delta-length-byte-array`](#delta-length-byte-array) · [`delta-byte-array`](#delta-byte-array) · [`delta`](#delta) · [`fixedscaleoffset`](#fixedscaleoffset) · [`quantize`](#quantize) · [`bitround`](#bitround) · [`astype`](#astype) · [`bytes`](#bytes) · [`plain-parquet`](#plain-parquet) · [`plain-dictionary`](#plain-dictionary) · [`orc-rle-v1`](#orc-rle-v1) · [`orc-rle-v2`](#orc-rle-v2) · [`orc-varint`](#orc-varint) · [`orc-string-direct`](#orc-string-direct) · [`orc-string-dictionary`](#orc-string-dictionary) · [`vlen-utf8`](#vlen-utf8) · [`vlen-bytes`](#vlen-bytes) · [`categorize`](#categorize) · [`zigzag`](#zigzag) · [`varint`](#varint) · [`zfp`](#zfp) · [`sz2`](#sz2) · [`sz3`](#sz3)

#### [Structure-Aware](#structure-aware-codecs)

Codec needs format-specific structural knowledge — dimensional layout, page
structure, image geometry, or format-specific metadata beyond simple type info.

- [`transpose`](#transpose) · [`tiff-predictor-2`](#tiff-predictor-2) · [`tiff-predictor-3`](#tiff-predictor-3) · [`png-filter`](#png-filter) · [`parquet-page-v1-split`](#parquet-page-v1-split) · [`parquet-page-v2-split`](#parquet-page-v2-split) · [`arrow-ipc-buffer`](#arrow-ipc-buffer) · [`tiff-old-jpeg`](#tiff-old-jpeg) · [`ccitt-group3`](#ccitt-group3) · [`ccitt-group4`](#ccitt-group4) · [`grib-grid-packing`](#grib-grid-packing) · [`grib-ccsds`](#grib-ccsds) · [`grib-jpeg2000`](#grib-jpeg2000)

---

## Pure-Bytes Codecs

---

### zstd

- **canonical-id**: `zstd`
- **aliases**: `{zarr-v2, "numcodecs.Zstd"}`, `{zarr-v3, "numcodecs.zstd"}`, `{hdf5, 32015}`, `{parquet, "ZSTD"}`, `{orc, "ZSTD"}`, `{arrow-ipc, "ZSTD"}`, `{tiff, 50000}`
- **awareness**: pure-bytes
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "zstd",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "level": {"type": "int", "required": false, "default": 3, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://facebook.github.io/zstd/zstd_manual.html
- **implementations**: [zstd (C reference)](https://github.com/facebook/zstd), [python-zstd](https://github.com/sergey-dryabzhinsky/python-zstd), [zstd-rs](https://github.com/gyscos/zstd-rs)
- **licensing**: BSD 2-Clause / GPLv2 dual license
- **notes**: Compression level is encode-only; decode does not require it. TIFF tag 50000 is self-assigned by the libtiff project; Adobe's official registration process is defunct, making libtiff the de facto registry for newer TIFF compression IDs.

---

### lz4

- **canonical-id**: `lz4`
- **aliases**: `{zarr-v2, "numcodecs.LZ4"}`, `{hdf5, 32004}`, `{parquet, "LZ4_RAW"}`, `{orc, "LZ4"}`, `{blosc2, "lz4"}`
- **awareness**: pure-bytes
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "lz4",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "acceleration": {"type": "int", "required": false, "default": 1, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://github.com/lz4/lz4/blob/dev/doc/lz4_Frame_format.md
- **implementations**: [lz4 (C reference)](https://github.com/lz4/lz4), [lz4-rs](https://github.com/10xGenomics/lz4)
- **licensing**: BSD 2-Clause
- **notes**: Parquet previously used a framed LZ4 variant (`LZ4`) that was inconsistently implemented; `LZ4_RAW` is the unframed standard and preferred in newer Parquet writers. ⚠️ Verify HDF5 filter 32004 uses framed or raw variant.

---

### deflate

- **canonical-id**: `deflate`
- **aliases**: `{zarr-v2, "numcodecs.Zlib"}`, `{zarr-v3, "numcodecs.zlib"}`, `{hdf5, 1}`, `{tiff, 8}`, `{tiff, 32946}`, `{parquet, "GZIP"}`, `{orc, "ZLIB"}`, `{netcdf-classic, "deflate"}`
- **awareness**: pure-bytes
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "deflate",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "level": {"type": "int", "required": false, "default": 1, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://www.rfc-editor.org/rfc/rfc1951 (DEFLATE), https://www.rfc-editor.org/rfc/rfc1950 (zlib framing)
- **implementations**: [zlib](https://zlib.net), [miniz](https://github.com/richgel999/miniz), [zlib-ng](https://github.com/zlib-ng/zlib-ng)
- **licensing**: zlib license
- **notes**: "deflate" and "zlib" are often conflated. DEFLATE is the algorithm; zlib adds a 2-byte header and Adler-32 checksum. TIFF compression=8 uses zlib framing. NetCDF classic uses zlib framing. Parquet labels this `GZIP` but uses zlib framing, not gzip framing. ⚠️ Verify exact framing used per format alias. NetCDF classic has no codec layer beyond deflate applied per-variable. NetCDF-4 inherits the full HDF5 filter pipeline.

---

### gzip

- **canonical-id**: `gzip`
- **aliases**: `{zarr-v2, "numcodecs.GZip"}`, `{arrow-ipc, "GZIP"}`
- **awareness**: pure-bytes
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "gzip",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "level": {"type": "int", "required": false, "default": 1, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://www.rfc-editor.org/rfc/rfc1952
- **implementations**: [gzip](https://www.gnu.org/software/gzip/), [zlib](https://zlib.net)
- **licensing**: GPL (gzip tool); zlib license (zlib library implementation)
- **notes**: Gzip framing = DEFLATE + gzip header/trailer with CRC-32. Distinct from zlib framing. Not the same as `deflate` despite shared underlying algorithm.

---

### bzip2

- **canonical-id**: `bzip2`
- **aliases**: `{zarr-v2, "numcodecs.BZ2"}`
- **awareness**: pure-bytes
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "bzip2",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "level": {"type": "int", "required": false, "default": 1, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://sourceware.org/bzip2/
- **implementations**: [bzip2](https://sourceware.org/bzip2/), [bzip2-rs](https://github.com/alexcrichton/bzip2-rs)
- **licensing**: BSD-like (bzip2 license)

---

### lzma

- **canonical-id**: `lzma`
- **aliases**: `{zarr-v2, "numcodecs.LZMA"}`, `{tiff, 34925}`
- **awareness**: pure-bytes
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "lzma",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "format": {"type": "int", "required": false, "default": 1, "encode_only": true},
    "check": {"type": "int", "required": false, "default": -1, "encode_only": true},
    "preset": {"type": "int", "required": false, "encode_only": true},
    "filters": {"type": "string", "required": false, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://tukaani.org/xz/xz-file-format.txt
- **implementations**: [xz-utils](https://tukaani.org/xz/), [lzma-rs](https://github.com/gendx/lzma-rs)
- **licensing**: Public domain / 0BSD
- **notes**: TIFF (tag 34925) uses LZMA2 via liblzma. The framing difference (XZ container vs raw LZMA2 stream) is handled transparently by liblzma depending on invocation context.

---

### snappy

- **canonical-id**: `snappy`
- **aliases**: `{parquet, "SNAPPY"}`, `{orc, "SNAPPY"}`, `{arrow-ipc, "SNAPPY"}`
- **awareness**: pure-bytes
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "snappy",
  "inputs": {
    "bytes": {"type": "bytes", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://github.com/google/snappy/blob/main/format_description.txt
- **implementations**: [snappy (C++ reference)](https://github.com/google/snappy), [snap (Rust)](https://github.com/BurntSushi/rust-snappy)
- **licensing**: BSD 3-Clause
- **notes**: No HDF5 registered filter. Prioritizes speed over compression ratio. No configuration parameters.

---

### brotli

- **canonical-id**: `brotli`
- **aliases**: `{parquet, "BROTLI"}`
- **awareness**: pure-bytes
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "brotli",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "quality": {"type": "int", "required": false, "default": 11, "encode_only": true},
    "lgwin": {"type": "int", "required": false, "default": 22, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://www.rfc-editor.org/rfc/rfc7932
- **implementations**: [brotli (C reference)](https://github.com/google/brotli)
- **licensing**: MIT

---

### lzo ⚠️

- **canonical-id**: `lzo`
- **aliases**: `{orc, "LZO"}`
- **awareness**: pure-bytes
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "lzo",
  "inputs": {
    "bytes": {"type": "bytes", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://www.oberhumer.com/opensource/lzo/ ⚠️
- **implementations**: [lzo](https://www.oberhumer.com/opensource/lzo/)
- **licensing**: GPL v2 ⚠️ — licensing may be a concern for some uses
- **notes**: ⚠️ Verify exact LZO variant and framing used in ORC. GPL license may be a concern.

---

### lzw

- **canonical-id**: `lzw`
- **aliases**: none in target formats
- **awareness**: pure-bytes
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "lzw",
  "inputs": {
    "bytes": {"type": "bytes", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: Welch, T. (1984). "A Technique for High-Performance Data Compression". IEEE Computer.
- **implementations**: [compress (Unix)](https://en.wikipedia.org/wiki/Compress), various
- **licensing**: Unisys patent expired; now unencumbered
- **notes**: The canonical LZW algorithm. Included to document the distinction from `lzw-tiff`. TIFF uses an incompatible variant — a generic LZW implementation will not correctly decode TIFF LZW data. See `lzw-tiff`.

---

### lzw-tiff

- **canonical-id**: `lzw-tiff`
- **aliases**: `{tiff, 5}`
- **awareness**: pure-bytes
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "lzw-tiff",
  "inputs": {
    "bytes": {"type": "bytes", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: TIFF 6.0 specification section on LZW compression
- **implementations**: [libtiff](https://libtiff.gitlab.io/libtiff/), [golang/image tiff/lzw](https://github.com/golang/image/tree/master/tiff/lzw)
- **licensing**: Unencumbered
- **notes**: TIFF's LZW is incompatible with standard LZW due to an "off by one" in code length transitions. A generic LZW codec cannot decode TIFF LZW data. Additionally, libtiff includes a backwards-compatibility decode path (`LZW_COMPAT`) for older files with reversed bit order. This is a concrete example of why format libraries cannot simply be replaced by a generic codec — the TIFF format driver must invoke `lzw-tiff` specifically.

---

### packbits

- **canonical-id**: `packbits`
- **aliases**: `{tiff, 32773}`
- **awareness**: pure-bytes
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "packbits",
  "inputs": {
    "bytes": {"type": "bytes", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: TIFF 6.0 specification section 9
- **implementations**: [libtiff](https://libtiff.gitlab.io/libtiff/), [imagecodecs](https://github.com/cgohlke/imagecodecs)
- **licensing**: Unencumbered

---

### szip ⚠️

- **canonical-id**: `szip`
- **aliases**: `{hdf5, 4}`, `{netcdf-4, "szip"}`
- **awareness**: pure-bytes
- **lossiness**: lossless ⚠️ verify for float data
- **signature**:
```json
{
  "codec_id": "szip",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "coding": {"type": "int", "required": true},
    "pixels_per_block": {"type": "int", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://support.hdfgroup.org/doc_resource/SZIP/ ⚠️
- **implementations**: [libaec](https://gitlab.dkrz.de/k202009/libaec) (open-source reimplementation), [szip (original)](https://support.hdfgroup.org/doc_resource/SZIP/)
- **licensing**: ⚠️ Original SZIP has proprietary licensing restrictions. `libaec` is BSD licensed.
- **notes**: Significant licensing history. libaec is a compatible open-source reimplementation. HDF5 bundles libaec in recent versions. Verify losslessness for all dtypes.

---

### blosc

- **canonical-id**: `blosc`
- **aliases**: `{zarr-v2, "numcodecs.Blosc"}`, `{hdf5, 32001}`
- **awareness**: pure-bytes
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "blosc",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "cname": {"type": "string", "required": false, "default": "lz4", "encode_only": true},
    "clevel": {"type": "int", "required": false, "default": 5, "encode_only": true},
    "shuffle": {"type": "int", "required": false, "default": 1, "encode_only": true},
    "blocksize": {"type": "int", "required": false, "default": 0, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://github.com/Blosc/c-blosc/blob/master/README.rst
- **implementations**: [c-blosc](https://github.com/Blosc/c-blosc), [python-blosc](https://github.com/Blosc/python-blosc)
- **licensing**: BSD 3-Clause
- **notes**: Self-describing: the Blosc header embedded in the chunk carries the compressor name, shuffle mode, element size, and block size. All encode parameters are encode-only because decode reads them from the header. This makes Blosc atomic — it cannot be decomposed into a pipeline without the format driver parsing the Blosc header, which violates the principle that the chunk enters the codec pipeline as-is. In Zarr v3, the equivalent operation is decomposed into explicit shuffle + compressor pipeline steps with parameters supplied externally by the format driver.

---

### jpeg-xl

- **canonical-id**: `jpeg-xl`
- **aliases**: `{tiff, 50002}` ⚠️ verify tag number, `{gdal-gtiff, "JXL"}`
- **awareness**: pure-bytes
- **lossiness**: conditional
- **signature**:
```json
{
  "codec_id": "jpeg-xl",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "distance": {"type": "float", "required": false, "default": 1.0, "encode_only": true},
    "effort": {"type": "int", "required": false, "default": 7, "encode_only": true},
    "lossless": {"type": "bool", "required": false, "default": false, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://jpeg.org/jpegxl/ ; ISO/IEC 18181
- **implementations**: [libjxl (C++ reference)](https://github.com/libjxl/libjxl), [jxl-rs](https://github.com/libjxl/jxl-rs), [imagecodecs](https://github.com/cgohlke/imagecodecs)
- **licensing**: BSD 3-Clause (libjxl); royalty-free format
- **notes**: Supports both lossy and lossless compression. Notably supports lossless JPEG recompression. Internal stages (VarDCT, modular prediction, ANS entropy coding) are not decomposed — the intermediate representations are JXL-internal and not usefully recombineable with other codec steps. TIFF tag number needs verification.

---

### jpeg

- **canonical-id**: `jpeg`
- **aliases**: `{tiff, 7}`, `{numcodecs, "imagecodecs.jpeg8"}` ⚠️ verify
- **awareness**: pure-bytes
- **lossiness**: conditional
- **signature**:
```json
{
  "codec_id": "jpeg",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "quality": {"type": "int", "required": false, "default": 75, "encode_only": true},
    "lossless": {"type": "bool", "required": false, "default": false, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: ITU-T T.81 / ISO/IEC 10918-1
- **implementations**: [libjpeg-turbo](https://github.com/libjpeg-turbo/libjpeg-turbo), [imagecodecs](https://github.com/cgohlke/imagecodecs)
- **licensing**: Unencumbered (format); libjpeg-turbo BSD/zlib
- **notes**: Internally applies DCT → quantization → Huffman coding. Internal stages are not decomposed. TIFF compression=7 is new-style JPEG with self-contained strips; see `tiff-old-jpeg` (compression=6) for legacy variant.

---

### jpeg2000

- **canonical-id**: `jpeg2000`
- **aliases**: `{tiff, 34712}`, `{numcodecs, "imagecodecs.jpeg2k"}` ⚠️ verify
- **awareness**: pure-bytes
- **lossiness**: conditional
- **signature**:
```json
{
  "codec_id": "jpeg2000",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "lossless": {"type": "bool", "required": false, "default": false, "encode_only": true},
    "level": {"type": "int", "required": false, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: ISO/IEC 15444-1
- **implementations**: [OpenJPEG](https://github.com/uclouvain/openjpeg), [imagecodecs](https://github.com/cgohlke/imagecodecs)
- **licensing**: BSD 2-Clause (OpenJPEG); royalty-free format
- **notes**: Internally applies DWT → quantization → EBCOT. Lossless mode uses reversible 5/3 wavelet; lossy uses 9/7 wavelet. Stages not decomposed. Used in GRIB2 (see `grib-jpeg2000`).

---

### webp

- **canonical-id**: `webp`
- **aliases**: `{tiff, 50001}`
- **awareness**: pure-bytes
- **lossiness**: conditional
- **signature**:
```json
{
  "codec_id": "webp",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "quality": {"type": "int", "required": false, "default": 75, "encode_only": true},
    "lossless": {"type": "bool", "required": false, "default": false, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://developers.google.com/speed/webp/docs/riff_container
- **implementations**: [libwebp](https://chromium.googlesource.com/webm/libwebp), [imagecodecs](https://github.com/cgohlke/imagecodecs)
- **licensing**: BSD 3-Clause
- **notes**: Lossy mode based on VP8 intra-frame coding. Lossless mode uses spatial prediction + LZ77+Huffman. Internal stages not decomposed. TIFF tag 50001 is self-assigned by libtiff.

---

### jpeg-xr ⚠️

- **canonical-id**: `jpeg-xr`
- **aliases**: `{tiff, 34892}` ⚠️ verify
- **awareness**: pure-bytes
- **lossiness**: conditional
- **signature**:
```json
{
  "codec_id": "jpeg-xr",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "quality": {"type": "float", "required": false, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: ITU-T T.832 / ISO/IEC 29199-2
- **implementations**: [jxrlib](https://github.com/4creators/jxrlib), [imagecodecs](https://github.com/cgohlke/imagecodecs)
- **licensing**: BSD (jxrlib) ⚠️ verify
- **notes**: ⚠️ Low confidence. Microsoft-developed HDR-capable image codec. Internal stages not decomposed.

---

### no-compression

- **canonical-id**: `no-compression`
- **aliases**: `{tiff, 1}`, `{parquet, "UNCOMPRESSED"}`, `{orc, "NONE"}`, `{hdf5, "no filter"}`
- **awareness**: pure-bytes
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "no-compression",
  "inputs": {
    "bytes": {"type": "bytes", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: N/A
- **implementations**: N/A — identity transform
- **notes**: Included for completeness. Zarr v3's `"bytes"` codec is a separate concept (serialization with explicit endianness) — not aliased here.

---

### fletcher32

- **canonical-id**: `fletcher32`
- **aliases**: `{hdf5, 3}`
- **awareness**: pure-bytes
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "fletcher32",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "error_on_fail": {"type": "bool", "required": false, "default": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://support.hdfgroup.org/documentation/hdf5/latest/group___d_a_p_l.html ⚠️ verify
- **implementations**: [HDF5 (C)](https://github.com/HDFGroup/hdf5)
- **licensing**: BSD-like (HDF5 license)
- **notes**: Appends 4-byte Fletcher-32 checksum on encode; verifies and strips on decode. `error_on_fail` is a runtime parameter (not stored in data). Output is 4 bytes shorter on decode.

---

### crc32c

- **canonical-id**: `crc32c`
- **aliases**: `{zarr-v3, "crc32c"}`
- **awareness**: pure-bytes
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "crc32c",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "error_on_fail": {"type": "bool", "required": false, "default": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://zarr-specs.readthedocs.io/en/latest/v3/codecs/crc32c/v1.0.html
- **implementations**: [zarr-python](https://github.com/zarr-developers/zarr-python), [zarrs (Rust)](https://github.com/LDeakin/zarr_tools), [tensorstore](https://github.com/google/tensorstore)
- **licensing**: Unencumbered (algorithm); implementations vary
- **notes**: Appends 4-byte CRC-32C (Castagnoli) checksum on encode; verifies and strips on decode. CRC-32C uses a different polynomial (0x1EDC6F41) than standard CRC-32 (0x04C11DB7). Recommended by the Zarr v3 sharding spec for shard index integrity. No configuration parameters beyond the runtime `error_on_fail`.

---

### crc32

- **canonical-id**: `crc32`
- **aliases**: `{parquet, "page-crc32"}` ⚠️ verify identifier
- **awareness**: pure-bytes
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "crc32",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "error_on_fail": {"type": "bool", "required": false, "default": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://www.rfc-editor.org/rfc/rfc3720#appendix-B (CRC-32 / ISO 3309)
- **implementations**: Widely available in standard libraries (zlib crc32, Python zlib.crc32)
- **licensing**: Unencumbered
- **notes**: ⚠️ Standard CRC-32 (polynomial 0x04C11DB7, ISO 3309). Parquet page headers include an optional CRC-32 checksum for page data integrity. Verify whether Parquet's CRC is appended to the page bytes (making it a codec in the pipeline sense) or stored in the page header metadata (making it a format-driver concern outside the codec layer). If the latter, this entry should be revised or removed.

---

### orc-boolean-rle

- **canonical-id**: `orc-boolean-rle`
- **aliases**: `{orc, "BOOLEAN_RLE"}`
- **awareness**: pure-bytes
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "orc-boolean-rle",
  "inputs": {
    "bytes": {"type": "bytes", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://orc.apache.org/specification/ORCv1/
- **implementations**: [orc (Java)](https://github.com/apache/orc), [arrow (C++)](https://github.com/apache/arrow)
- **licensing**: Apache 2.0
- **notes**: Used exclusively for PRESENT (validity/null) streams. Packs 8 bits per byte.

---


## Stride-Aware Codecs

---

### shuffle

- **canonical-id**: `shuffle`
- **aliases**: `{hdf5, 2}`, `{numcodecs, "numcodecs.Shuffle"}`, `{blosc2, "shuffle"}`
- **awareness**: stride-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "shuffle",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "element_size": {"type": "uint", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://support.hdfgroup.org/documentation/hdf5/latest/_f_i_l_t_e_r.html
- **implementations**: [HDF5 (C)](https://github.com/HDFGroup/hdf5), [numcodecs](https://github.com/zarr-developers/numcodecs)
- **licensing**: Unencumbered (algorithm); HDF5 BSD-like

---

### bitshuffle

- **canonical-id**: `bitshuffle`
- **aliases**: `{hdf5, 32008}`, `{numcodecs, "numcodecs.BitShuffle"}`, `{blosc2, "bitshuffle"}`
- **awareness**: stride-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "bitshuffle",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "element_size": {"type": "uint", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://github.com/kiyo-masui/bitshuffle
- **implementations**: [bitshuffle](https://github.com/kiyo-masui/bitshuffle), [numcodecs](https://github.com/zarr-developers/numcodecs)
- **licensing**: MIT
- **notes**: The HDF5 filter variant (32008) optionally bundles LZ4 compression as an atomic operation. In a decomposed pipeline this should be modeled as `bitshuffle → lz4`.

---

### byte-stream-split

- **canonical-id**: `byte-stream-split`
- **aliases**: `{parquet, "BYTE_STREAM_SPLIT"}`
- **awareness**: stride-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "byte-stream-split",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "element_size": {"type": "uint", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://parquet.apache.org/docs/file-format/data-pages/encodings/#byte-stream-split-byte_stream_split--11
- **implementations**: [parquet-cpp](https://github.com/apache/arrow/tree/main/cpp/src/parquet), [parquet-rs](https://github.com/apache/arrow-rs)
- **licensing**: Apache 2.0
- **notes**: Identical in effect to HDF5 shuffle. Transposes byte matrix: all byte-0s contiguous, then all byte-1s, etc.

---

### rle-parquet

- **canonical-id**: `rle-parquet`
- **aliases**: `{parquet, "RLE"}`, `{parquet, "RLE_DICTIONARY"}` (index stream only)
- **awareness**: stride-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "rle-parquet",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "bit_width": {"type": "uint", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://parquet.apache.org/docs/file-format/data-pages/encodings/#run-length-encoding--bit-packing-hybrid-rle--3
- **implementations**: [parquet-cpp](https://github.com/apache/arrow/tree/main/cpp/src/parquet), [parquet-rs](https://github.com/apache/arrow-rs)
- **licensing**: Apache 2.0
- **notes**: Hybrid RLE and bit-packing. Self-selects per block between RLE runs and bit-packed runs via header byte. Not decomposable into a pipeline of `rle → bitpack` — the per-block adaptive selection is integral to the encoding.

---

### rle

- **canonical-id**: `rle`
- **aliases**: `{numcodecs, "numcodecs.RunLengthEncoding"}` ⚠️
- **awareness**: stride-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "rle",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "element_size": {"type": "uint", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://numcodecs.readthedocs.io/en/stable/ ⚠️ verify
- **implementations**: [numcodecs](https://github.com/zarr-developers/numcodecs) ⚠️
- **licensing**: MIT
- **notes**: ⚠️ Verify numcodecs exposes a general RLE codec.

---


## Type-Aware Codecs

---

### bitpack

- **canonical-id**: `bitpack`
- **aliases**: `{parquet, "BIT_PACKED"}` (deprecated encoding 4), `{lance, "bitpacking"}`, `{tiff, "sub-byte-packing"}` ⚠️ verify TIFF modeling
- **awareness**: type-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "bitpack",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "bit_width": {"type": "uint", "required": true},
    "dtype": {"type": "string", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: No single canonical spec. Parquet encoding spec (encoding 4); Fastlanes paper (Afroozeh & Boncz, 2023).
- **implementations**: [parquet-cpp](https://github.com/apache/arrow/tree/main/cpp/src/parquet), [parquet-rs](https://github.com/apache/arrow-rs), [lance](https://github.com/lancedb/lance) (Fastlanes-based), [bitpacking crate](https://github.com/tantivy-search/bitpacking)
- **licensing**: Unencumbered (algorithm)
- **notes**: Packs integer values into `bit_width` bits per value. Decode produces native-width integers. Parquet's `BIT_PACKED` (encoding 4) is deprecated in favor of the RLE/bit-packing hybrid. `nbit` (HDF5) performs a related operation — strips padding bits based on HDF5 dtype precision and bit offset — but requires full HDF5 dtype context; see `nbit` entry. TIFF sub-byte sample packing (e.g. 12-bit samples) is effectively `bitpack(bit_width=bits_per_sample)`. ⚠️ Verify TIFF modeling.

---

### fsst

- **canonical-id**: `fsst`
- **aliases**: `{lance, "fsst"}`, `{duckdb, "fsst"}` ⚠️ verify DuckDB identifier
- **awareness**: type-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "fsst",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "symbol_table": {"type": "bytes", "required": true},
    "dtype": {"type": "string", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: Boncz, P., Neumann, T., & Raasveldt, M. (2020). "FSST: Fast Random Access String Compression." VLDB 2020. https://www.vldb.org/pvldb/vol13/p2649-boncz.pdf
- **implementations**: [fsst (C++ reference)](https://github.com/cwida/fsst), [lance](https://github.com/lancedb/lance)
- **licensing**: MIT ⚠️ verify
- **notes**: Compresses strings by building a 256-entry symbol table from input data, then replacing matching substrings with 1-byte codes. Escape byte (0xFF) for literals. The `symbol_table` is a data-derived input: built during encoding, required for decoding, stored in codec metadata alongside the chunk. Compare with dictionary encoding (maps whole values to codes) — FSST maps substrings to codes and is effective on high-cardinality columns. ⚠️ Verify framing conventions for symbol table storage across implementations.

---

### nbit

- **canonical-id**: `nbit`
- **aliases**: `{hdf5, 5}`
- **awareness**: type-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "nbit",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "dtype": {"type": "dtype_desc", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://support.hdfgroup.org/documentation/hdf5/latest/
- **implementations**: [HDF5 (C)](https://github.com/HDFGroup/hdf5)
- **licensing**: BSD-like (HDF5 license)
- **notes**: Strips padding bits based on declared precision and offset in the HDF5 dtype. Requires full HDF5 dtype descriptor. Conceptually related to `bitpack` — both reduce values to fewer bits — but `nbit` derives bit width from dtype metadata rather than taking an explicit `bit_width` parameter. An implementation could potentially delegate to `bitpack` internally.

---

### scale-offset

- **canonical-id**: `scale-offset`
- **aliases**: `{hdf5, 6}`
- **awareness**: type-aware
- **lossiness**: conditional
- **signature**:
```json
{
  "codec_id": "scale-offset",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "dtype": {"type": "dtype_desc", "required": true},
    "fill_value": {"type": "float", "required": true},
    "scale_type": {"type": "int", "required": true},
    "scale_factor": {"type": "int", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://support.hdfgroup.org/documentation/hdf5/latest/
- **implementations**: [HDF5 (C)](https://github.com/HDFGroup/hdf5)
- **licensing**: BSD-like (HDF5 license)
- **notes**: Lossless for integer types. Lossy for floating point (quantization). Fill values require special handling.

---

### lerc

- **canonical-id**: `lerc`
- **aliases**: `{tiff, 34887}`, `{numcodecs, "numcodecs.LERC"}` ⚠️
- **awareness**: type-aware
- **lossiness**: conditional
- **signature**:
```json
{
  "codec_id": "lerc",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "dtype": {"type": "string", "required": true},
    "shape": {"type": "uint[]", "required": true},
    "nodata_value": {"type": "float", "required": false},
    "max_z_error": {"type": "float", "required": false, "default": 0.0, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://github.com/Esri/lerc/blob/master/doc/LERC_WhitePaper.pdf
- **implementations**: [lerc (C++, Esri)](https://github.com/Esri/lerc), [imagecodecs](https://github.com/cgohlke/imagecodecs)
- **licensing**: Apache 2.0
- **notes**: Esri geospatial codec. Natively 2D-aware. Lossless when `max_z_error=0`. Handles NoData internally. GDAL's `LERC_DEFLATE` and `LERC_ZSTD` are pipelines: `lerc → deflate` or `lerc → zstd`.

---

### delta-parquet

- **canonical-id**: `delta-parquet`
- **aliases**: `{parquet, "DELTA_BINARY_PACKED"}`
- **awareness**: type-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "delta-parquet",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "dtype": {"type": "string", "required": true},
    "block_size": {"type": "int", "required": false, "default": 128, "encode_only": true},
    "miniblock_count": {"type": "int", "required": false, "default": 4, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **conceptual decomposition**: `delta(dtype) → fixedscaleoffset(scale=1, offset=per_block_min) → bitpack(bit_width=per_miniblock)`. Not directly expressible as a pipeline because parameters vary per block within the stream.
- **spec**: https://parquet.apache.org/docs/file-format/data-pages/encodings/#delta-encoding-delta_binary_packed--5
- **implementations**: [parquet-cpp](https://github.com/apache/arrow/tree/main/cpp/src/parquet), [parquet-rs](https://github.com/apache/arrow-rs)
- **licensing**: Apache 2.0

---

### delta-length-byte-array

- **canonical-id**: `delta-length-byte-array`
- **aliases**: `{parquet, "DELTA_LENGTH_BYTE_ARRAY"}`
- **awareness**: type-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "delta-length-byte-array",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "dtype": {"type": "string", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **conceptual decomposition**: `variable-width-split() → { delta-parquet(lengths), identity(values) }`. Fan-out, then per-stream codec.
- **spec**: https://parquet.apache.org/docs/file-format/data-pages/encodings/#delta-encoding-of-string-lengths-delta_length_byte_array--6
- **implementations**: [parquet-cpp](https://github.com/apache/arrow/tree/main/cpp/src/parquet), [parquet-rs](https://github.com/apache/arrow-rs)
- **licensing**: Apache 2.0
- **notes**: Stores lengths via delta-binary-packed encoding, followed by concatenated raw bytes.

---

### delta-byte-array

- **canonical-id**: `delta-byte-array`
- **aliases**: `{parquet, "DELTA_BYTE_ARRAY"}`
- **awareness**: type-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "delta-byte-array",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "dtype": {"type": "string", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **conceptual decomposition**: `front-coding-split() → { delta-parquet(prefix_lengths), identity(suffixes) }`. Fan-out into two streams with delta on prefix lengths.
- **spec**: https://parquet.apache.org/docs/file-format/data-pages/encodings/#incremental-encoding-for-byte-arrays-aka-front-coding-delta_byte_array--7
- **implementations**: [parquet-cpp](https://github.com/apache/arrow/tree/main/cpp/src/parquet), [parquet-rs](https://github.com/apache/arrow-rs)
- **licensing**: Apache 2.0
- **notes**: Front coding: encodes shared prefixes between successive byte arrays.

---

### delta

- **canonical-id**: `delta`
- **aliases**: `{zarr-v3, "numcodecs.delta"}`, `{numcodecs, "numcodecs.Delta"}`
- **awareness**: type-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "delta",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "dtype": {"type": "string", "required": true},
    "element_size": {"type": "uint", "required": true},
    "shape": {"type": "uint[]", "required": false},
    "order": {"type": "string", "required": false, "default": "C"}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://numcodecs.readthedocs.io/en/stable/delta.html
- **implementations**: [numcodecs](https://github.com/zarr-developers/numcodecs)
- **licensing**: MIT
- **notes**: Differences between successive elements. Unlike tiff-predictor-2, operates on the full array (not row-by-row) unless shape/order constrain traversal. `dtype` affects overflow behavior.

---

### fixedscaleoffset

- **canonical-id**: `fixedscaleoffset`
- **aliases**: `{numcodecs, "numcodecs.FixedScaleOffset"}`, `{zarr-v3, "numcodecs.fixedscaleoffset"}`
- **awareness**: type-aware
- **lossiness**: conditional
- **signature**:
```json
{
  "codec_id": "fixedscaleoffset",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "offset": {"type": "float", "required": true},
    "scale": {"type": "float", "required": true},
    "dtype": {"type": "string", "required": true},
    "astype": {"type": "string", "required": false}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://numcodecs.readthedocs.io/en/stable/fixedscaleoffset.html
- **implementations**: [numcodecs](https://github.com/zarr-developers/numcodecs)
- **licensing**: MIT
- **notes**: Lossless if `astype` can represent all scaled values. Lossy otherwise. With `scale=1` this is frame-of-reference encoding (subtract offset only).

---

### quantize

- **canonical-id**: `quantize`
- **aliases**: `{numcodecs, "numcodecs.Quantize"}`
- **awareness**: type-aware
- **lossiness**: lossy
- **signature**:
```json
{
  "codec_id": "quantize",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "digits": {"type": "int", "required": true},
    "dtype": {"type": "string", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://numcodecs.readthedocs.io/en/stable/quantize.html
- **implementations**: [numcodecs](https://github.com/zarr-developers/numcodecs)
- **licensing**: MIT

---

### bitround

- **canonical-id**: `bitround`
- **aliases**: `{numcodecs, "numcodecs.BitRound"}`
- **awareness**: type-aware
- **lossiness**: lossy
- **signature**:
```json
{
  "codec_id": "bitround",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "keepbits": {"type": "int", "required": true},
    "dtype": {"type": "string", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://numcodecs.readthedocs.io/en/stable/bitround.html
- **implementations**: [numcodecs](https://github.com/zarr-developers/numcodecs)
- **licensing**: MIT
- **notes**: Zeroes low-order mantissa bits. Often followed by zstd or lz4.

---

### astype

- **canonical-id**: `astype`
- **aliases**: `{numcodecs, "numcodecs.AsType"}`
- **awareness**: type-aware
- **lossiness**: conditional
- **signature**:
```json
{
  "codec_id": "astype",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "encode_dtype": {"type": "string", "required": true},
    "decode_dtype": {"type": "string", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://numcodecs.readthedocs.io/en/stable/astype.html
- **implementations**: [numcodecs](https://github.com/zarr-developers/numcodecs)
- **licensing**: MIT
- **notes**: Lossless if encode_dtype can represent all values exactly. Lossy otherwise.

---

### bytes

- **canonical-id**: `bytes`
- **aliases**: `{zarr-v3, "bytes"}`
- **awareness**: type-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "bytes",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "dtype": {"type": "string", "required": true},
    "shape": {"type": "uint[]", "required": true},
    "endian": {"type": "string", "required": false, "default": "little"},
    "order": {"type": "string", "required": false, "default": "C"}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://zarr-specs.readthedocs.io/en/latest/v3/codecs/bytes/v1.0.html
- **implementations**: [zarr-python](https://github.com/zarr-developers/zarr-python), [zarr-rs](https://github.com/LDeakin/zarr_tools)
- **licensing**: MIT
- **notes**: Boundary codec in Zarr v3. Explicit endianness and layout order.

---

### plain-parquet

- **canonical-id**: `plain-parquet`
- **aliases**: `{parquet, "PLAIN"}`
- **awareness**: type-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "plain-parquet",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "physical_type": {"type": "string", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://parquet.apache.org/docs/file-format/data-pages/encodings/#plain-plain--0
- **implementations**: [parquet-cpp](https://github.com/apache/arrow/tree/main/cpp/src/parquet), [parquet-rs](https://github.com/apache/arrow-rs)
- **licensing**: Apache 2.0
- **notes**: Encoding varies by physical type. Fixed-width: packed little-endian. BYTE_ARRAY: 4-byte length + data. BOOLEAN: 8 per byte LSB-first.

---

### plain-dictionary

- **canonical-id**: `plain-dictionary`
- **aliases**: `{parquet, "PLAIN_DICTIONARY"}`, `{parquet, "RLE_DICTIONARY"}`
- **awareness**: type-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "plain-dictionary",
  "inputs": {
    "indices": {"type": "bytes", "required": true},
    "dictionary": {"type": "bytes", "required": true},
    "physical_type": {"type": "string", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://parquet.apache.org/docs/file-format/data-pages/encodings/#dictionary-encoding-plain_dictionary--2-and-rle_dictionary--8
- **implementations**: [parquet-cpp](https://github.com/apache/arrow/tree/main/cpp/src/parquet), [parquet-rs](https://github.com/apache/arrow-rs)
- **licensing**: Apache 2.0
- **notes**: `dictionary` is a second `bytes` input supplied by the format driver from a separate dictionary page. `PLAIN_DICTIONARY` (deprecated) and `RLE_DICTIONARY` are functionally equivalent.

---

### orc-rle-v1

- **canonical-id**: `orc-rle-v1`
- **aliases**: `{orc-v0, "RLE_V1"}`
- **awareness**: type-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "orc-rle-v1",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "dtype": {"type": "string", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://orc.apache.org/specification/ORCv0/
- **implementations**: [orc (Java)](https://github.com/apache/orc), [arrow (C++)](https://github.com/apache/arrow)
- **licensing**: Apache 2.0
- **notes**: Per-block adaptive selection between Run (fixed-delta) and Literals (varint). Signed integers use zigzag encoding internally. Not decomposable into a flat pipeline due to per-block strategy selection.

---

### orc-rle-v2

- **canonical-id**: `orc-rle-v2`
- **aliases**: `{orc-v1, "RLE_V2"}`, `{orc-v2, "RLE_V2"}`
- **awareness**: type-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "orc-rle-v2",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "dtype": {"type": "string", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://orc.apache.org/specification/ORCv1/
- **implementations**: [orc (Java)](https://github.com/apache/orc), [arrow (C++)](https://github.com/apache/arrow)
- **licensing**: Apache 2.0
- **notes**: Per-block adaptive selection from four sub-encodings via 2-bit header: Short Repeat, Direct (bitpacked), Patched Base, Delta. Not decomposable. Signed integers use zigzag internally.

---

### orc-varint

- **canonical-id**: `orc-varint`
- **aliases**: `{orc, "VARINT"}`
- **awareness**: type-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "orc-varint",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "dtype": {"type": "string", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **pipeline** (decode, signed types):
```json
{
  "codec_id": "orc-varint",
  "direction": "decode",

  "inputs": {
    "bytes": {"type": "bytes"},
    "dtype": {"type": "string"}
  },

  "outputs": {
    "bytes": "unzigzag.signed_ints"
  },

  "steps": {
    "decode_varint": {
      "codec": "varint",
      "inputs": {
        "bytes": "input.bytes"
      },
      "outputs": {
        "bytes": "raw_uints"
      }
    },
    "unzigzag": {
      "codec": "zigzag",
      "inputs": {
        "bytes": "decode_varint.raw_uints",
        "dtype": "input.dtype"
      },
      "outputs": {
        "bytes": "signed_ints"
      }
    }
  }
}
```
Note: For unsigned types, the zigzag step is skipped. Modeling of conditional steps is TBD.
- **spec**: https://orc.apache.org/specification/ORCv1/
- **implementations**: [orc (Java)](https://github.com/apache/orc)
- **licensing**: Apache 2.0
- **notes**: Base-128 variable-length integer encoding with zigzag for signed types. See generic `varint` and `zigzag` codecs.

---

### orc-string-direct

- **canonical-id**: `orc-string-direct`
- **aliases**: `{orc, "DIRECT"}` (string columns)
- **awareness**: type-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "orc-string-direct",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "dtype": {"type": "string", "required": true}
  },
  "outputs": {
    "lengths": {"type": "bytes"},
    "data": {"type": "bytes"}
  }
}
```
- **spec**: https://orc.apache.org/specification/ORCv1/
- **implementations**: [orc (Java)](https://github.com/apache/orc), [arrow (C++)](https://github.com/apache/arrow)
- **licensing**: Apache 2.0
- **notes**: Fan-out: produces two output streams. Lengths stream uses orc-rle-v1 or orc-rle-v2.

---

### orc-string-dictionary

- **canonical-id**: `orc-string-dictionary`
- **aliases**: `{orc, "DICTIONARY"}`, `{orc, "DICTIONARY_V2"}` (string columns)
- **awareness**: type-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "orc-string-dictionary",
  "inputs": {
    "indices": {"type": "bytes", "required": true},
    "dictionary": {"type": "bytes", "required": true},
    "dtype": {"type": "string", "required": true}
  },
  "outputs": {
    "data": {"type": "bytes"},
    "lengths": {"type": "bytes"}
  }
}
```
- **spec**: https://orc.apache.org/specification/ORCv1/
- **implementations**: [orc (Java)](https://github.com/apache/orc), [arrow (C++)](https://github.com/apache/arrow)
- **licensing**: Apache 2.0
- **notes**: Dictionary is per-stripe, supplied by format driver as a second `bytes` input. Multiple byte-stream inputs + multiple byte-stream outputs.

---

### vlen-utf8

- **canonical-id**: `vlen-utf8`
- **aliases**: `{numcodecs, "numcodecs.VLenUTF8"}`, `{zarr-v2, "numcodecs.VLenUTF8"}`
- **awareness**: type-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "vlen-utf8",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "dtype": {"type": "string", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://numcodecs.readthedocs.io/en/stable/vlen.html ⚠️
- **implementations**: [numcodecs](https://github.com/zarr-developers/numcodecs)
- **licensing**: MIT
- **notes**: Serializes variable-length UTF-8 strings into a flat bytestream. Output length not predictable from dtype/shape. Breaks fixed-stride assumption. Currently Zarr/Python-specific framing.

---

### vlen-bytes

- **canonical-id**: `vlen-bytes`
- **aliases**: `{numcodecs, "numcodecs.VLenBytes"}`, `{zarr-v2, "numcodecs.VLenBytes"}`
- **awareness**: type-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "vlen-bytes",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "dtype": {"type": "string", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://numcodecs.readthedocs.io/en/stable/vlen.html ⚠️
- **implementations**: [numcodecs](https://github.com/zarr-developers/numcodecs)
- **licensing**: MIT
- **notes**: Same as `vlen-utf8` but for arbitrary byte sequences. Same constraints apply.

---

### categorize

- **canonical-id**: `categorize`
- **aliases**: `{numcodecs, "numcodecs.Categorize"}`
- **awareness**: type-aware
- **lossiness**: conditional
- **signature**:
```json
{
  "codec_id": "categorize",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "labels": {"type": "string", "required": true},
    "dtype": {"type": "string", "required": true},
    "astype": {"type": "string", "required": false, "default": "u1"}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://numcodecs.readthedocs.io/en/stable/categorize.html
- **implementations**: [numcodecs](https://github.com/zarr-developers/numcodecs)
- **licensing**: MIT
- **notes**: Maps categorical values to integer codes. Analogous to dictionary encoding. Currently Python-specific in framing. Lossless if all values in labels; error on unknown values.

---

### zigzag

- **canonical-id**: `zigzag`
- **aliases**: embedded inside ORC and Protocol Buffers encodings
- **awareness**: type-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "zigzag",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "dtype": {"type": "string", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://protobuf.dev/programming-guides/encoding/#signed-ints
- **implementations**: Trivially implementable; included in protobuf, ORC, and Arrow libraries.
- **licensing**: Unencumbered
- **notes**: Maps signed integers to unsigned: 0→0, -1→1, 1→2, -2→3, etc. Improves subsequent varint or bitpack encoding. Used implicitly inside `orc-rle-v1`, `orc-rle-v2`, `orc-varint`.

---

### varint

- **canonical-id**: `varint`
- **aliases**: `{protobuf, "varint"}`, `{orc, "base-128-varint"}`
- **awareness**: type-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "varint",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "dtype": {"type": "string", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://protobuf.dev/programming-guides/encoding/#varints
- **implementations**: Included in all protobuf implementations; trivially implementable.
- **licensing**: Unencumbered
- **notes**: Base-128 variable-length integer encoding. 7 data bits + 1 continuation bit per byte. Handles any unsigned integer type — small values use fewer bytes regardless of declared type width. Signed types should be zigzag-encoded first. See `orc-varint` pipeline decomposition.

---

### zfp

- **canonical-id**: `zfp`
- **aliases**: `{hdf5, 32013}`, `{hdf5plugin, "hdf5plugin.Zfp"}` ⚠️ verify numcodecs identifier
- **awareness**: type-aware
- **lossiness**: conditional
- **signature**:
```json
{
  "codec_id": "zfp",
  "inputs": {
    "bytes":      {"type": "bytes", "required": true},
    "dtype":      {"type": "string", "required": true},
    "shape":      {"type": "uint[]", "required": true},
    "mode":       {"type": "string", "required": true},
    "rate":       {"type": "float", "required": false, "encode_only": true},
    "precision":  {"type": "int", "required": false, "encode_only": true},
    "accuracy":   {"type": "float", "required": false, "encode_only": true},
    "minbits":    {"type": "int", "required": false, "encode_only": true},
    "maxbits":    {"type": "int", "required": false, "encode_only": true},
    "maxprec":    {"type": "int", "required": false, "encode_only": true},
    "minexp":     {"type": "int", "required": false, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: Lindstrom, P. (2014). "Fixed-Rate Compressed Floating-Point Arrays." IEEE TVCG 20(12). https://zfp.readthedocs.io/
- **implementations**: [zfp (C/C++, LLNL)](https://github.com/LLNL/zfp), [H5Z-ZFP (HDF5 filter)](https://github.com/LLNL/H5Z-ZFP), [hdf5plugin (Python)](https://github.com/silx-kit/hdf5plugin)
- **licensing**: BSD 3-Clause
- **notes**: Transform-based lossy compressor for spatially correlated floating-point and integer arrays. Partitions data into 4^d blocks, applies block-floating-point conversion, a custom near-orthogonal decorrelation transform (similar to DCT), coefficient reordering, and embedded bitplane coding with truncation. Supports 1D–4D arrays of float32, float64, int32, int64; works best on 2D+ data with spatial correlation. Five compression modes selected by `mode`: `rate` (fixed bits per value), `precision` (fixed bit planes), `accuracy` (absolute error tolerance), `expert` (direct control of minbits/maxbits/maxprec/minexp), `reversible` (lossless). Only the parameters relevant to the selected mode are used. All mode-specific parameters are encode-only — the compressed stream is self-describing for decompression. Internal stages are tightly integrated and not decomposable — truncation-based coding is fundamental to the algorithm and cannot be separated from the transform.

---

### sz2 ⚠️

- **canonical-id**: `sz2`
- **aliases**: `{hdf5, 32017}`, `{hdf5plugin, "hdf5plugin.SZ"}`
- **awareness**: type-aware
- **lossiness**: conditional
- **signature**:
```json
{
  "codec_id": "sz2",
  "inputs": {
    "bytes":               {"type": "bytes", "required": true},
    "dtype":               {"type": "string", "required": true},
    "shape":               {"type": "uint[]", "required": true},
    "absolute":            {"type": "float", "required": false, "encode_only": true},
    "relative":            {"type": "float", "required": false, "encode_only": true},
    "pointwise_relative":  {"type": "float", "required": false, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: Di, S. & Cappello, F. (2016). "Fast Error-bounded Lossy HPC Data Compression with SZ." IEEE IPDPS. https://github.com/szcompressor/SZ2
- **implementations**: [SZ2 (C)](https://github.com/szcompressor/SZ2), [H5Z-SZ (HDF5 filter)](https://github.com/disheng222/H5Z-SZ), [hdf5plugin (Python)](https://github.com/silx-kit/hdf5plugin)
- **licensing**: BSD
- **notes**: ⚠️ Prediction-based lossy compressor from Argonne National Laboratory. Predicts each value from its neighbors using Lorenzo predictor or linear regression, quantizes prediction errors within the error bound, and entropy-codes the residuals (Huffman + Zstd). Designed for large-scale scientific simulation output. Supports float32, float64, and integers at arbitrary dimensionality. While the backend (Huffman + Zstd) is conceptually generic compression, the prediction-quantization-entropy pipeline is co-designed with internal intermediate formats — not decomposable into catalog codecs. Error bound parameters are encode-only — the compressed stream stores the mode and tolerance. Superseded by SZ3; cannot decode SZ3 data, and SZ3 cannot decode SZ2 data. ⚠️ Verify exact parameter set for HDF5 filter interface.

---

### sz3 ⚠️

- **canonical-id**: `sz3`
- **aliases**: `{hdf5, 32024}`, `{hdf5plugin, "hdf5plugin.SZ3"}`
- **awareness**: type-aware
- **lossiness**: conditional
- **signature**:
```json
{
  "codec_id": "sz3",
  "inputs": {
    "bytes":                      {"type": "bytes", "required": true},
    "dtype":                      {"type": "string", "required": true},
    "shape":                      {"type": "uint[]", "required": true},
    "absolute":                   {"type": "float", "required": false, "encode_only": true},
    "relative":                   {"type": "float", "required": false, "encode_only": true},
    "norm2":                      {"type": "float", "required": false, "encode_only": true},
    "peak_signal_to_noise_ratio": {"type": "float", "required": false, "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: Zhao et al. (2021). "Optimizing Error-Bounded Lossy Compression for Scientific Data by Dynamic Spline Interpolation." IEEE ICDE. https://github.com/szcompressor/SZ3
- **implementations**: [SZ3 (C++)](https://github.com/szcompressor/SZ3), [hdf5plugin (Python)](https://github.com/silx-kit/hdf5plugin)
- **licensing**: BSD
- **notes**: ⚠️ Successor to SZ2 with a modular framework and improved spline interpolation predictor. Higher compression ratios than SZ2 in most cases. Incompatible compressed format with SZ2. Same algorithmic approach (prediction → quantization → entropy coding) but with better predictors and auto-tuning. Not decomposable into catalog codecs for the same reasons as SZ2. ⚠️ Verify exact parameter interface for the HDF5 filter.

---


## Structure-Aware Codecs

---

### transpose

- **canonical-id**: `transpose`
- **aliases**: `{zarr-v3, "transpose"}`
- **awareness**: structure-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "transpose",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "shape": {"type": "uint[]", "required": true},
    "order": {"type": "uint[]", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://zarr-specs.readthedocs.io/en/latest/v3/codecs/transpose/v1.0.html
- **implementations**: [zarr-python](https://github.com/zarr-developers/zarr-python)
- **licensing**: MIT
- **notes**: Reorders axes before serialization. Equivalent to numpy transpose.

---

### tiff-predictor-2

- **canonical-id**: `tiff-predictor-2`
- **aliases**: `{tiff, "predictor=2"}`
- **awareness**: structure-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "tiff-predictor-2",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "element_size": {"type": "uint", "required": true},
    "row_width": {"type": "uint", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: TIFF 6.0 specification, Section 14
- **implementations**: [libtiff](https://libtiff.gitlab.io/libtiff/), [imagecodecs](https://github.com/cgohlke/imagecodecs)
- **licensing**: Unencumbered
- **notes**: Horizontal differencing row-by-row. First sample per row stored as-is; subsequent samples store difference from previous. Requires `row_width` to know row boundaries.

---

### tiff-predictor-3

- **canonical-id**: `tiff-predictor-3`
- **aliases**: `{tiff, "predictor=3"}`
- **awareness**: structure-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "tiff-predictor-3",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "dtype": {"type": "string", "required": true},
    "element_size": {"type": "uint", "required": true},
    "row_width": {"type": "uint", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: TIFF Technical Note 3 ⚠️ verify
- **implementations**: [libtiff](https://libtiff.gitlab.io/libtiff/), [imagecodecs](https://github.com/cgohlke/imagecodecs)
- **licensing**: Unencumbered
- **notes**: Byte-level shuffle specific to IEEE 754 float layout, followed by horizontal differencing, row-by-row. ⚠️ Verify exact byte shuffle and delta order.

---

### png-filter

- **canonical-id**: `png-filter`
- **aliases**: `{numcodecs, "imagecodecs.pngfilter"}` ⚠️ verify
- **awareness**: structure-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "png-filter",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "element_size": {"type": "uint", "required": true},
    "row_width": {"type": "uint", "required": true},
    "filter_type": {"type": "string", "required": false, "default": "adaptive", "encode_only": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://www.w3.org/TR/PNG/#9Filters
- **implementations**: [libpng](http://www.libpng.org/pub/png/libpng.html), [imagecodecs](https://github.com/cgohlke/imagecodecs)
- **licensing**: Unencumbered
- **notes**: Five filter types: None, Sub, Up, Average, Paeth. On decode, filter type is read from per-scanline byte; `filter_type` only affects encoding. A genuinely separable precompression step — can be paired with any entropy coder.

---

### parquet-page-v1-split

- **canonical-id**: `parquet-page-v1-split`
- **aliases**: `{parquet, "data-page-v1"}`
- **awareness**: structure-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "parquet-page-v1-split",
  "inputs": {
    "bytes": {"type": "bytes", "required": true}
  },
  "outputs": {
    "rep_bytes": {"type": "bytes"},
    "def_bytes": {"type": "bytes"},
    "value_bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://parquet.apache.org/docs/file-format/data-pages/
- **implementations**: [parquet-cpp](https://github.com/apache/arrow/tree/main/cpp/src/parquet), [parquet-rs](https://github.com/apache/arrow-rs)
- **licensing**: Apache 2.0
- **notes**: Self-delimiting — decoder parses through RLE-framed level sections to find boundaries. No external length metadata. Fragile; prefer v2.

---

### parquet-page-v2-split

- **canonical-id**: `parquet-page-v2-split`
- **aliases**: `{parquet, "data-page-v2"}`
- **awareness**: structure-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "parquet-page-v2-split",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "rep_length": {"type": "uint", "required": true},
    "def_length": {"type": "uint", "required": true}
  },
  "outputs": {
    "rep_bytes": {"type": "bytes"},
    "def_bytes": {"type": "bytes"},
    "value_bytes": {"type": "bytes"}
  }
}
```
- **pipeline** (decode, dictionary-encoded column with zstd):
```json
{
  "codec_id": "parquet-v2-dict-decode",
  "direction": "decode",

  "inputs": {
    "data_page":       {"type": "bytes"},
    "dict_page":       {"type": "bytes"},
    "rep_length":      {"type": "uint"},
    "def_length":      {"type": "uint"},
    "index_bit_width": {"type": "uint"},
    "physical_type":   {"type": "string"}
  },

  "constants": {
    "def_bit_width": {"type": "uint", "value": 1},
    "rep_bit_width": {"type": "uint", "value": 1}
  },

  "outputs": {
    "values": "dict_lookup.decoded_values",
    "def":    "decode_def.decoded_def",
    "rep":    "decode_rep.decoded_rep"
  },

  "steps": {
    "decompress_page": {
      "codec": "zstd",
      "inputs": {
        "bytes": "input.data_page"
      },
      "outputs": {
        "bytes": "decompressed_page"
      }
    },
    "split_page": {
      "codec": "parquet-page-v2-split",
      "inputs": {
        "bytes":      "decompress_page.decompressed_page",
        "rep_length": "input.rep_length",
        "def_length": "input.def_length"
      },
      "outputs": {
        "rep_bytes":   "raw_rep",
        "def_bytes":   "raw_def",
        "value_bytes": "raw_indices"
      }
    },
    "decode_indices": {
      "codec": "rle-parquet",
      "inputs": {
        "bytes":     "split_page.raw_indices",
        "bit_width": "input.index_bit_width"
      },
      "outputs": {
        "bytes": "decoded_indices"
      }
    },
    "dict_lookup": {
      "codec": "plain-dictionary",
      "inputs": {
        "indices":       "decode_indices.decoded_indices",
        "dictionary":    "input.dict_page",
        "physical_type": "input.physical_type"
      },
      "outputs": {
        "bytes": "decoded_values"
      }
    },
    "decode_def": {
      "codec": "rle-parquet",
      "inputs": {
        "bytes":     "split_page.raw_def",
        "bit_width": "constant.def_bit_width"
      },
      "outputs": {
        "bytes": "decoded_def"
      }
    },
    "decode_rep": {
      "codec": "rle-parquet",
      "inputs": {
        "bytes":     "split_page.raw_rep",
        "bit_width": "constant.rep_bit_width"
      },
      "outputs": {
        "bytes": "decoded_rep"
      }
    }
  }
}
```
Note: `def_bit_width` and `rep_bit_width` are constants for non-nested schemas; for nested schemas they become pipeline inputs derived from schema depth.
- **spec**: https://parquet.apache.org/docs/file-format/data-pages/
- **implementations**: [parquet-cpp](https://github.com/apache/arrow/tree/main/cpp/src/parquet), [parquet-rs](https://github.com/apache/arrow-rs)
- **licensing**: Apache 2.0
- **notes**: `rep_length` and `def_length` from page header enable clean splitting. Preferred over v1.

---

### arrow-ipc-buffer

- **canonical-id**: `arrow-ipc-buffer`
- **aliases**: `{arrow-ipc, "buffer-layout"}`, `{lance, "arrow-buffer-layout"}`
- **awareness**: structure-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "arrow-ipc-buffer",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "arrow_type": {"type": "string", "required": true},
    "nullable": {"type": "bool", "required": true}
  },
  "outputs": {
    "validity": {"type": "bytes"},
    "offsets": {"type": "bytes"},
    "values": {"type": "bytes"}
  }
}
```
- **spec**: https://arrow.apache.org/docs/format/Columnar.html
- **implementations**: [arrow-cpp](https://github.com/apache/arrow), [arrow-rs](https://github.com/apache/arrow-rs)
- **licensing**: Apache 2.0
- **notes**: Output streams vary by type. Non-nullable primitive: only `values`. Nullable: `validity` + `values`. Variable-width: `validity` + `offsets` + `values`. Lance inherits this layout.

---

### tiff-old-jpeg

- **canonical-id**: `tiff-old-jpeg`
- **aliases**: `{tiff, 6}`
- **awareness**: structure-aware
- **lossiness**: lossy
- **signature**:
```json
{
  "codec_id": "tiff-old-jpeg",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "jpeg_tables": {"type": "bytes", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: TIFF 6.0 specification (compression=6)
- **implementations**: [libtiff](https://libtiff.gitlab.io/libtiff/), [imagecodecs](https://github.com/cgohlke/imagecodecs)
- **licensing**: Unencumbered
- **notes**: Old-style JPEG in TIFF. JPEG tables stored in IFD tags, supplied as second `bytes` input. Strongly prefer new-style JPEG (compression=7).

---

### ccitt-group3

- **canonical-id**: `ccitt-group3`
- **aliases**: `{tiff, 3}`
- **awareness**: structure-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "ccitt-group3",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "image_width": {"type": "uint", "required": true},
    "t4_options": {"type": "uint", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: ITU-T T.4
- **implementations**: [libtiff](https://libtiff.gitlab.io/libtiff/), [imagecodecs](https://github.com/cgohlke/imagecodecs)
- **licensing**: Unencumbered
- **notes**: Bilevel (1-bit) image data only. Row-oriented.

---

### ccitt-group4

- **canonical-id**: `ccitt-group4`
- **aliases**: `{tiff, 4}`
- **awareness**: structure-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "ccitt-group4",
  "inputs": {
    "bytes": {"type": "bytes", "required": true},
    "image_width": {"type": "uint", "required": true},
    "t6_options": {"type": "uint", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: ITU-T T.6
- **implementations**: [libtiff](https://libtiff.gitlab.io/libtiff/), [imagecodecs](https://github.com/cgohlke/imagecodecs)
- **licensing**: Unencumbered
- **notes**: Bilevel (1-bit) image data only. Pure 2D encoding — each row relative to previous.

---

### grib-grid-packing ⚠️

- **canonical-id**: `grib-grid-packing`
- **aliases**: `{grib-edition-1, "simple-packing"}`
- **awareness**: structure-aware
- **lossiness**: lossy
- **signature**:
```json
{
  "codec_id": "grib-grid-packing",
  "inputs": {
    "bytes": {"type": "bytes", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: https://library.wmo.int/records/item/35625-manual-on-codes-volume-i-2 ⚠️
- **implementations**: [eccodes](https://github.com/ecmwf/eccodes), [cfgrib](https://github.com/ecmwf/cfgrib)
- **licensing**: Apache 2.0 (eccodes)
- **notes**: ⚠️ Low confidence. Scale factor and reference value in GRIB headers. Verify details.

---

### grib-ccsds ⚠️

- **canonical-id**: `grib-ccsds`
- **aliases**: `{grib-edition-2, "ccsds"}`, `{grib2, "template-42"}`
- **awareness**: structure-aware
- **lossiness**: lossless
- **signature**:
```json
{
  "codec_id": "grib-ccsds",
  "inputs": {
    "bytes": {"type": "bytes", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: CCSDS 121.0-B; WMO GRIB2 template 42 ⚠️
- **implementations**: [eccodes](https://github.com/ecmwf/eccodes), [libaec](https://gitlab.dkrz.de/k202009/libaec)
- **licensing**: Apache 2.0 (eccodes); BSD (libaec)
- **notes**: ⚠️ Low confidence. Adaptive entropy coding from space mission data.

---

### grib-jpeg2000 ⚠️

- **canonical-id**: `grib-jpeg2000`
- **aliases**: `{grib-edition-2, "jpeg2000"}`, `{grib2, "template-40"}`
- **awareness**: structure-aware
- **lossiness**: conditional
- **signature**:
```json
{
  "codec_id": "grib-jpeg2000",
  "inputs": {
    "bytes": {"type": "bytes", "required": true}
  },
  "outputs": {
    "bytes": {"type": "bytes"}
  }
}
```
- **spec**: WMO GRIB2 template 40 ⚠️
- **implementations**: [eccodes](https://github.com/ecmwf/eccodes)
- **licensing**: Apache 2.0 (eccodes)
- **notes**: ⚠️ Low confidence. JPEG2000 lossless or lossy.

---


## Explicitly Excluded

The following appear in codec registries but are excluded from this catalog for
the reasons given.

### Serialization bridges

These are object serialization formats, not typed array codecs.

#### json (excluded)

- **identifier**: `numcodecs.JSON`
- **reason**: Encodes arbitrary Python objects to JSON bytes. Decode output is runtime-dependent, not a typed numeric array.

#### msgpack (excluded)

- **identifier**: `numcodecs.MsgPack`
- **reason**: Binary serialization for arbitrary object graphs. Not a typed array codec.

#### pickle (excluded)

- **identifier**: `numcodecs.Pickle`
- **reason**: Python-specific. Security concerns (arbitrary code execution on unpickle). Excluded on portability and security grounds.

### Storage/access layer operations

These are registered as codecs in their respective formats but do not perform
data transformation in the sense used by this catalog. They reorganize how
chunks are stored and accessed — a concern of the storage/access layer above
the codec layer.

#### sharding-indexed (excluded)

- **identifier**: `{zarr-v3, "sharding_indexed"}`
- **spec**: https://zarr-specs.readthedocs.io/en/latest/v3/codecs/sharding-indexed/index.html
- **reason**: Sharding splits a chunk ("shard") into inner sub-chunks, applies a
  nested codec pipeline to each sub-chunk independently, concatenates the
  compressed sub-chunks, and appends an index (offset + length per sub-chunk)
  with its own codec pipeline. This is a storage organization operation — it
  determines how data is laid out within a storage object for efficient partial
  access — not a data transformation. No byte of actual array data is
  transformed by sharding itself; the transformation is delegated to the inner
  codec pipelines, which are composed of codecs from this catalog.

  Zarr v3 registers sharding as a codec (specifically an `array → bytes` codec)
  because Zarr's codec pipeline is the only extension point available for
  operations between the array and storage layers. This is an overloading of the
  codec concept. A more precise model would place sharding in a separate
  storage/access layer that sits above the codec layer — responsible for
  chunking strategy, sub-chunk indexing, and partial I/O — while the codec layer
  handles the actual data transformations applied to each chunk or sub-chunk.

  The storage/access layer could potentially be modeled with a similar signature
  and pipeline structure to what this catalog uses for codecs, but that design
  is out of scope here.

## Open Questions and Issues

1. ~~**deflate framing variants (resolved)**~~: Handled by underlying zlib library.

2. **lzo licensing**: GPL v2 may be problematic. Worth flagging.

3. **szip losslessness for floats**: Verify whether SZIP guarantees losslessness for float data.

4. **TIFF predictor-3 traversal order**: Verify row-by-row vs full flattened array.

5. **ORC v2 codec evolution**: ORC v2 proposes removing RLEv1/v2. Monitor.

6. **GRIB entries**: All three are low confidence.

7. ~~**netcdf-classic-record (resolved)**~~: No separate entry needed.

8. **variable-width-split pattern**: `orc-string-direct`, `arrow-ipc-buffer`, and `delta-length-byte-array` share a lengths/values split pattern. A generic `variable-width-split` codec may unify these, but input framing varies across formats. TBD.

9. **Data-derived inputs**: Should data-derived inputs (built during encode, required for decode, stored in metadata — e.g. FSST `symbol_table`) have an explicit flag in the signature?

10. ~~**Encode-only flag (resolved)**~~: Implemented as `encode_only: true` in signatures.

11. **Format-specific algorithm variants**: `lzw-tiff` pattern. Check when expanding to new formats.

12. **Checksum codec scope**: `fletcher32`, `crc32c`, and `crc32` are now included. Determine whether additional checksum variants exist in target formats. Adler-32 (used in zlib framing) is embedded in the deflate codec's framing, not a separate codec.

13. ~~**Pipeline direction and inversion (resolved)**~~: Formalized in the Pipeline Model section. Reverse the DAG edges, swap pipeline inputs/outputs, constants unchanged, `encode_only` inputs active only when encoding.

14. **Conditional pipeline steps**: `orc-varint` zigzag step is conditional on signedness. How to model conditional steps is TBD.

15. **Per-block parameter variation**: `delta-parquet` uses per-block/per-miniblock parameters. These vary within a single stream, preventing direct pipeline decomposition. Consider block-wise parameterization.

16. **TIFF bitpack alias**: Verify whether TIFF sub-byte sample packing is correctly modeled as `bitpack(bit_width=bits_per_sample)`.

17. **Lance codec compatibility**: Verify whether Lance's RLE, dictionary, and delta are byte-compatible with Parquet/ORC variants.

18. **Codec URI scheme**: Codec IDs will become versioned URIs. Scheme TBD.

19. ~~**ZFP / SZ lossy compressors (resolved)**~~: Added as `zfp`, `sz2`, `sz3`. All are atomic (not decomposable). Transform-based (ZFP) vs prediction-based (SZ) approaches to error-bounded lossy compression.

20. ~~**Zarr v3 sharding codec (resolved)**~~: Excluded. Sharding is a storage/access layer concern, not a data transformation. See the `sharding-indexed` entry in Explicitly Excluded.

21. **Self-describing codec boundary**: Blosc is atomic because its header is embedded in the chunk. Formalize the criterion.

22. **Awareness classification edge cases**: `tiff-predictor-2` and `png-filter` moved from stride-aware to structure-aware because they require `row_width` (dimensional layout knowledge). `delta` moved from stride-aware to type-aware because it requires `dtype` for overflow semantics. These reclassifications should be verified.