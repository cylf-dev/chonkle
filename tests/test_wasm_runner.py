from pathlib import Path

import numpy as np
import pytest

from chonkle.codecs import TiffPredictor2
from chonkle.wasm_runner import resolve_wasm_uri, wasm_decode, wasm_encode

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
            resolve_wasm_uri("ftp://registry/codec:v1")

    def test_https_uri_delegates_to_download(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        wasm = tmp_path / "downloaded.wasm"
        wasm.write_bytes(b"\x00")
        monkeypatch.setattr(
            "chonkle.wasm_runner.download_https",
            lambda url, **kw: wasm,
        )
        result = resolve_wasm_uri("https://example.com/codec.wasm")
        assert result == wasm

    def test_oci_uri_delegates_to_download(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        wasm = tmp_path / "downloaded.wasm"
        wasm.write_bytes(b"\x00")
        monkeypatch.setattr(
            "chonkle.wasm_runner.download_oci",
            lambda uri, **kw: wasm,
        )
        result = resolve_wasm_uri("oci://ghcr.io/org/repo:v1")
        assert result == wasm


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


class TestWasmEncode:
    def test_small_uint16_array(self) -> None:
        """Encode with WASM, decode with Python, verify roundtrip."""
        codec = TiffPredictor2()
        arr = np.array(
            [[10, 12, 15, 11], [100, 98, 95, 99]],
            dtype=np.uint16,
        )

        encoded_bytes = wasm_encode(
            WASM_PATH,
            arr.tobytes(),
            {"bytes_per_sample": 2, "width": 4},
        )

        encoded = np.frombuffer(encoded_bytes, dtype=np.uint16).reshape(arr.shape)
        result = codec.decode(encoded)
        np.testing.assert_array_equal(result, arr)

    def test_small_uint8_array(self) -> None:
        """Encode with WASM, decode with Python, verify roundtrip."""
        codec = TiffPredictor2()
        arr = np.array([[1, 3, 6, 10], [200, 198, 195, 199]], dtype=np.uint8)

        encoded_bytes = wasm_encode(
            WASM_PATH,
            arr.tobytes(),
            {"bytes_per_sample": 1, "width": 4},
        )

        encoded = np.frombuffer(encoded_bytes, dtype=np.uint8).reshape(arr.shape)
        result = codec.decode(encoded)
        np.testing.assert_array_equal(result, arr)

    def test_wasm_encode_matches_python_encode(self) -> None:
        """WASM encode produces identical output to Python encode."""
        codec = TiffPredictor2()
        arr = np.array(
            [[10, 12, 15, 11], [100, 98, 95, 99]],
            dtype=np.uint16,
        )
        python_encoded = codec.encode(arr)

        wasm_encoded_bytes = wasm_encode(
            WASM_PATH,
            arr.tobytes(),
            {"bytes_per_sample": 2, "width": 4},
        )
        wasm_encoded = np.frombuffer(wasm_encoded_bytes, dtype=np.uint16).reshape(
            arr.shape
        )

        np.testing.assert_array_equal(wasm_encoded, python_encoded)


class TestWasmRoundtrip:
    def test_encode_then_decode(self) -> None:
        """WASM encode followed by WASM decode returns original data."""
        arr = np.array(
            [[10, 12, 15, 11], [100, 98, 95, 99]],
            dtype=np.uint16,
        )
        config = {"bytes_per_sample": 2, "width": 4}

        encoded_bytes = wasm_encode(WASM_PATH, arr.tobytes(), config)
        decoded_bytes = wasm_decode(WASM_PATH, encoded_bytes, config)

        result = np.frombuffer(decoded_bytes, dtype=np.uint16).reshape(arr.shape)
        np.testing.assert_array_equal(result, arr)

    def test_decode_then_encode(self) -> None:
        """WASM decode followed by WASM encode returns original data."""
        codec = TiffPredictor2()
        arr = np.array(
            [[10, 12, 15, 11], [100, 98, 95, 99]],
            dtype=np.uint16,
        )
        differenced = codec.encode(arr)
        config = {"bytes_per_sample": 2, "width": 4}

        decoded_bytes = wasm_decode(WASM_PATH, differenced.tobytes(), config)
        reencoded_bytes = wasm_encode(WASM_PATH, decoded_bytes, config)

        result = np.frombuffer(reencoded_bytes, dtype=np.uint16).reshape(arr.shape)
        np.testing.assert_array_equal(result, differenced)
