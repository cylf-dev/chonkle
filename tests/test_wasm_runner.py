from pathlib import Path

import numpy as np
import pytest

from chonkle.decode.codecs import TiffPredictor2
from chonkle.decode.wasm_runner import resolve_wasm_uri, wasm_decode

from .conftest import FIXTURES_DIR

WASM_PATH = FIXTURES_DIR / "cog_wasm" / "tiff-predictor-2-c.wasm"


class TestResolveWasmUri:
    def test_file_uri(self, tmp_path: Path) -> None:
        wasm = tmp_path / "codec.wasm"
        wasm.write_bytes(b"\x00")
        result = resolve_wasm_uri(wasm.as_uri())
        assert result == wasm.resolve()

    def test_no_scheme_raises(self) -> None:
        with pytest.raises(NotImplementedError, match="must include a scheme"):
            resolve_wasm_uri("/absolute/path.wasm")

    def test_unsupported_scheme_raises(self) -> None:
        with pytest.raises(NotImplementedError, match="Unsupported URI scheme"):
            resolve_wasm_uri("oci://registry/codec:v1")


class TestWasmDecode:
    def test_small_uint16_array(self) -> None:
        """Encode with Python, decode with WASM, verify roundtrip."""
        codec = TiffPredictor2()
        arr = np.array(
            [[10, 12, 15, 11], [100, 98, 95, 99]],
            dtype=np.uint16,
        )
        encoded = codec.encode(arr)
        encoded_bytes = encoded.tobytes()

        result_bytes = wasm_decode(
            WASM_PATH,
            encoded_bytes,
            {"bytes_per_sample": 2, "width": 4},
        )

        result = np.frombuffer(result_bytes, dtype=np.uint16).reshape(arr.shape)
        np.testing.assert_array_equal(result, arr)

    def test_small_uint8_array(self) -> None:
        codec = TiffPredictor2()
        arr = np.array([[1, 3, 6, 10], [200, 198, 195, 199]], dtype=np.uint8)
        encoded = codec.encode(arr)
        encoded_bytes = encoded.tobytes()

        result_bytes = wasm_decode(
            WASM_PATH,
            encoded_bytes,
            {"bytes_per_sample": 1, "width": 4},
        )

        result = np.frombuffer(result_bytes, dtype=np.uint8).reshape(arr.shape)
        np.testing.assert_array_equal(result, arr)
