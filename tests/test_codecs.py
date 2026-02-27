import numpy as np
import pytest

from chonkle.codecs import (
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

    def test_encode_values(self) -> None:
        codec = TiffPredictor2()
        arr = np.array([[10, 12, 15, 11]], dtype=np.int32)
        encoded = codec.encode(arr)
        expected = np.array([[10, 2, 3, -4]], dtype=np.int32)
        np.testing.assert_array_equal(encoded, expected)

    def test_roundtrip_1d(self) -> None:
        codec = TiffPredictor2()
        arr = np.array([5, 10, 20, 15], dtype=np.float32)
        result = codec.decode(codec.encode(arr))
        np.testing.assert_array_equal(result, arr)

    def test_roundtrip_3d(self) -> None:
        codec = TiffPredictor2()
        arr = np.arange(24, dtype=np.uint8).reshape(2, 3, 4)
        result = codec.decode(codec.encode(arr))
        np.testing.assert_array_equal(result, arr)

    def test_decode_with_out_param(self) -> None:
        codec = TiffPredictor2()
        arr = np.array([[1, 3, 6, 10]], dtype=np.uint16)
        encoded = codec.encode(arr)
        out = np.empty_like(arr)
        result = codec.decode(encoded, out=out)
        np.testing.assert_array_equal(result, arr)
        assert result is out


class TestBytesCodec:
    def test_roundtrip(self) -> None:
        codec = BytesCodec(data_type="uint16", endian=Endian.LITTLE, shape=(2, 3))
        arr = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.uint16)
        result = codec.decode(codec.encode(arr))
        np.testing.assert_array_equal(result, arr)

    def test_roundtrip_big_endian(self) -> None:
        codec = BytesCodec(data_type="uint16", endian=Endian.BIG, shape=(2, 2))
        arr = np.array([[256, 512], [1024, 2048]], dtype=np.uint16)
        result = codec.decode(codec.encode(arr))
        np.testing.assert_array_equal(result, arr)

    def test_roundtrip_float32(self) -> None:
        codec = BytesCodec(data_type="float32", endian=Endian.LITTLE, shape=(3,))
        arr = np.array([1.5, 2.5, 3.5], dtype=np.float32)
        result = codec.decode(codec.encode(arr))
        np.testing.assert_array_equal(result, arr)

    def test_roundtrip_no_shape(self) -> None:
        codec = BytesCodec(data_type="int32", endian=Endian.LITTLE)
        arr = np.array([10, 20, 30], dtype=np.int32)
        result = codec.decode(codec.encode(arr))
        np.testing.assert_array_equal(result, arr)
        assert result.ndim == 1

    def test_decode_with_out_param(self) -> None:
        codec = BytesCodec(data_type="uint8", endian=Endian.LITTLE, shape=(4,))
        arr = np.array([1, 2, 3, 4], dtype=np.uint8)
        encoded = codec.encode(arr)
        out = np.empty(4, dtype=np.uint8)
        result = codec.decode(encoded, out=out)
        np.testing.assert_array_equal(result, arr)
        assert result is out

    def test_string_endian(self) -> None:
        codec = BytesCodec(data_type="uint16", endian="little", shape=(2,))
        arr = np.array([100, 200], dtype=np.uint16)
        result = codec.decode(codec.encode(arr))
        np.testing.assert_array_equal(result, arr)


class TestNumpyDtype:
    def test_unsupported_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported zarr data_type"):
            _numpy_dtype("complex128", Endian.LITTLE)
