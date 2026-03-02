import json
from pathlib import Path

import numpy as np
import pytest

from chonkle.pipeline import decode, encode, get_codecs

from .conftest import CHUNK_PATHS, FIXTURES_DIR


def _decode_fixture(chunk_path: Path) -> np.ndarray:
    """Load a test fixture: read chunk bytes + sidecar JSON, decode."""
    codecs = get_codecs(Path(str(chunk_path) + ".json"))
    return decode(chunk_path.read_bytes(), codecs)


class TestGetCodecs:
    def test_from_path(self) -> None:
        codecs = get_codecs(FIXTURES_DIR / "cog" / "0.json")
        assert isinstance(codecs, list)
        assert len(codecs) > 0

    def test_from_dict(self) -> None:
        spec = {"codecs": [{"name": "zlib", "type": "numcodecs"}]}
        codecs = get_codecs(spec)
        assert codecs == [{"name": "zlib", "type": "numcodecs"}]

    def test_missing_codecs_key_raises(self) -> None:
        with pytest.raises(KeyError, match="codecs"):
            get_codecs({"not_codecs": []})


class TestDecode:
    def test_all_chunks_decode_identically(self) -> None:
        arrays = [_decode_fixture(p) for p in CHUNK_PATHS]
        for arr in arrays[1:]:
            np.testing.assert_array_equal(arrays[0], arr)


class TestDecodeWasm:
    def test_wasm_decode_matches_native(self, tmp_path: Path) -> None:
        """Wasm TiffPredictor2 produces identical output to native."""
        native = _decode_fixture(FIXTURES_DIR / "cog" / "0")

        # Build a temporary fixture with a fully-qualified file:// URI
        # since the metadata JSON cannot contain a portable absolute path.
        src = FIXTURES_DIR / "cog_wasm"
        chunk = tmp_path / "0"
        chunk.write_bytes((src / "0").read_bytes())

        wasm_uri = (src / "tiff-predictor-2-c.wasm").resolve().as_uri()
        metadata = json.loads((src / "0.json").read_text())
        wasm_codec = next(c for c in metadata["codecs"] if c["type"] == "wasm")
        wasm_codec["uri"] = wasm_uri
        (tmp_path / "0.json").write_text(json.dumps(metadata))

        wasm_result = _decode_fixture(chunk)
        np.testing.assert_array_equal(native, wasm_result)


class TestEncode:
    def test_encode_then_decode_roundtrip(self) -> None:
        """Encoding then decoding through the same pipeline returns the original."""
        chunk_path = FIXTURES_DIR / "cog" / "0"
        codec_specs = get_codecs(Path(str(chunk_path) + ".json"))

        original = _decode_fixture(chunk_path)
        encoded = encode(original, codec_specs)
        decoded = decode(encoded, codec_specs)

        np.testing.assert_array_equal(decoded, original)

    def test_encode_produces_bytes(self) -> None:
        """Encoding should produce a bytes object."""
        chunk_path = FIXTURES_DIR / "zarr_zstd" / "0"
        codec_specs = get_codecs(Path(str(chunk_path) + ".json"))

        original = _decode_fixture(chunk_path)
        encoded = encode(original, codec_specs)

        assert isinstance(encoded, bytes)
        assert len(encoded) > 0

    def test_encode_pipeline_not_producing_bytes_raises(self) -> None:
        """A pipeline that doesn't end with bytes should raise TypeError."""
        arr = np.array([[1, 2], [3, 4]], dtype=np.uint16)
        codec_specs = [
            {"name": "tiff_predictor_2", "type": "numcodecs", "configuration": {}},
        ]
        with pytest.raises(TypeError, match="did not produce bytes"):
            encode(arr, codec_specs)
