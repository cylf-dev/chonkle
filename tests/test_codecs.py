"""Tests for codec wrapper classes and binary detection."""

from pathlib import Path

import pytest

from chonkle.codecs import detect_codec_type

# Wasm magic + version headers.
_CORE_HEADER = b"\x00asm\x01\x00\x00\x00"
_COMPONENT_HEADER = b"\x00asm\x0d\x00\x01\x00"


class TestDetectCodecType:
    def test_component_model(self, tmp_path: Path) -> None:
        wasm = tmp_path / "codec.wasm"
        wasm.write_bytes(_COMPONENT_HEADER)
        assert detect_codec_type(wasm) == "component"

    def test_core_wasm(self, tmp_path: Path) -> None:
        wasm = tmp_path / "codec.wasm"
        wasm.write_bytes(_CORE_HEADER)
        assert detect_codec_type(wasm) == "core"

    def test_not_wasm_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "not.wasm"
        bad.write_bytes(b"not a wasm file")
        with pytest.raises(ValueError, match="Not a valid Wasm binary"):
            detect_codec_type(bad)

    def test_too_short_raises(self, tmp_path: Path) -> None:
        short = tmp_path / "short.wasm"
        short.write_bytes(b"\x00asm")
        with pytest.raises(ValueError, match="Not a valid Wasm binary"):
            detect_codec_type(short)

    def test_unknown_version_raises(self, tmp_path: Path) -> None:
        wasm = tmp_path / "future.wasm"
        wasm.write_bytes(b"\x00asm\xff\x00\x00\x00")
        with pytest.raises(ValueError, match="Unknown Wasm version"):
            detect_codec_type(wasm)
