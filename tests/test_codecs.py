import numpy as np
import pytest

from chonkle.decode.codecs import (
    BytesCodec,
    Endian,
    TiffPredictor2,
    _numpy_dtype,
)


class TestTiffPredictor2:
    def test_roundtrip(self) -> None:
        codec = TiffPredictor2()
        arr = np.array([[10, 12, 15, 11], [100, 98, 95, 99]], dtype=np.uint16)
        result = codec.decode(codec.encode(arr))
        np.testing.assert_array_equal(result, arr)


class TestBytesCodec:
    def test_roundtrip(self) -> None:
        codec = BytesCodec(data_type="uint16", endian=Endian.LITTLE, shape=(2, 3))
        arr = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.uint16)
        result = codec.decode(codec.encode(arr))
        np.testing.assert_array_equal(result, arr)


class TestNumpyDtype:
    def test_unsupported_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported zarr data_type"):
            _numpy_dtype("complex128", Endian.LITTLE)
