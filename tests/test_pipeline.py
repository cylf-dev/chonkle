import json
from pathlib import Path

import numpy as np
import pytest

from chonkle.decode.pipeline import decode_chunk

from .conftest import CHUNK_PATHS, FIXTURES_DIR


class TestDecodeChunk:
    def test_all_chunks_decode_identically(self) -> None:
        arrays = [decode_chunk(p) for p in CHUNK_PATHS]
        for arr in arrays[1:]:
            np.testing.assert_array_equal(arrays[0], arr)

    def test_missing_codecs_key_raises(self, tmp_path: Path) -> None:
        chunk = tmp_path / "bad"
        chunk.write_bytes(b"\x00")
        chunk.with_suffix(".json").write_text(json.dumps({"not_codecs": []}))
        with pytest.raises(KeyError, match="codecs"):
            decode_chunk(chunk)


class TestDecodeChunkWasm:
    def test_wasm_decode_matches_native(self, tmp_path: Path) -> None:
        """WASM TiffPredictor2 produces identical output to native."""
        native = decode_chunk(FIXTURES_DIR / "cog" / "0")

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

        wasm_result = decode_chunk(chunk)
        np.testing.assert_array_equal(native, wasm_result)
