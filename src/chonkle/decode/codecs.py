"""Custom numcodecs codecs for chunk decoding."""

from enum import StrEnum
from typing import Any

import numcodecs
import numcodecs.abc
import numpy as np


class Endian(StrEnum):
    """Byte order for multi-byte data types."""

    LITTLE = "little"
    BIG = "big"


_ZARR_TO_NUMPY: dict[str, str] = {
    "bool": "b1",
    "int8": "i1",
    "int16": "i2",
    "int32": "i4",
    "int64": "i8",
    "uint8": "u1",
    "uint16": "u2",
    "uint32": "u4",
    "uint64": "u8",
    "float16": "f2",
    "float32": "f4",
    "float64": "f8",
}


class TiffPredictor2(numcodecs.abc.Codec):
    """TIFF Predictor 2 (horizontal differencing).

    Encoding stores per-row differences; decoding undoes this
    via cumulative sum along the last axis.
    """

    codec_id = "tiff_predictor_2"

    def encode(self, buf: Any) -> np.ndarray:
        arr = np.asarray(buf)
        out = np.empty_like(arr)
        out[..., 0] = arr[..., 0]
        out[..., 1:] = np.diff(arr, axis=-1)
        return out

    def decode(self, buf: Any, out: Any = None) -> np.ndarray:
        arr = np.asarray(buf)
        result = np.cumsum(arr, axis=-1, dtype=arr.dtype)
        if out is not None:
            np.copyto(out, result)
            return out
        return result


def _numpy_dtype(data_type: str, endian: Endian) -> np.dtype:
    """Build a numpy dtype from a data_type name and endianness."""
    try:
        type_char = _ZARR_TO_NUMPY[data_type]
    except KeyError:
        msg = f"Unsupported zarr data_type: {data_type}"
        raise ValueError(msg) from None
    byte_order = "<" if endian is Endian.LITTLE else ">"
    return np.dtype(f"{byte_order}{type_char}")


class BytesCodec(numcodecs.abc.Codec):
    """Bytes codec: converts between raw bytes and typed numpy arrays."""

    codec_id = "bytes"

    def __init__(
        self,
        data_type: str,
        endian: Endian | str,
        shape: tuple[int, ...] | None = None,
    ) -> None:
        self.data_type = data_type
        self.shape = shape
        self.endian = Endian(endian)

    def encode(self, buf: Any) -> bytes:
        arr = np.asarray(buf)
        dtype = _numpy_dtype(self.data_type, self.endian)
        return arr.astype(dtype).tobytes()

    def decode(self, buf: Any, out: Any = None) -> np.ndarray:
        dtype = _numpy_dtype(self.data_type, self.endian)
        arr = np.frombuffer(buf, dtype=dtype)
        if self.shape is not None:
            arr = arr.reshape(self.shape)
        if out is not None:
            np.copyto(out, arr)
            return out
        return arr.copy()


numcodecs.register_codec(TiffPredictor2)
numcodecs.register_codec(BytesCodec)
