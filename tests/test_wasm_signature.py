"""Tests for chonkle:signature custom section reader/writer."""

import json

import pytest

from chonkle.wasm_signature import (
    SECTION_NAME,
    embed_signature,
    read_signature,
    read_signature_bytes,
)

# Minimal valid Wasm headers (magic + version only, no sections).
COMPONENT_HEADER = b"\x00asm\x0d\x00\x01\x00"
CORE_HEADER = b"\x00asm\x01\x00\x00\x00"

SAMPLE_SIGNATURE = {
    "codec_id": "zlib",
    "implementation": "zlib-rs",
    "inputs": {
        "bytes": {"type": "bytes", "required": True},
        "level": {"type": "int", "required": False, "default": 6, "encode_only": True},
    },
    "outputs": {"bytes": {"type": "bytes"}},
}


class TestEmbedAndRead:
    """Round-trip: embed then read back."""

    def test_roundtrip_component_model(self) -> None:
        result = embed_signature(COMPONENT_HEADER, SAMPLE_SIGNATURE)
        assert read_signature_bytes(result) == SAMPLE_SIGNATURE

    def test_roundtrip_core_wasm(self) -> None:
        result = embed_signature(CORE_HEADER, SAMPLE_SIGNATURE)
        assert read_signature_bytes(result) == SAMPLE_SIGNATURE

    def test_roundtrip_via_file(self, tmp_path) -> None:
        wasm_path = tmp_path / "test.wasm"
        wasm_path.write_bytes(embed_signature(COMPONENT_HEADER, SAMPLE_SIGNATURE))
        assert read_signature(wasm_path) == SAMPLE_SIGNATURE

    def test_embed_replaces_existing(self) -> None:
        first = embed_signature(COMPONENT_HEADER, {"codec_id": "old"})
        second = embed_signature(first, SAMPLE_SIGNATURE)
        assert read_signature_bytes(second) == SAMPLE_SIGNATURE
        # Only one custom section should exist.
        assert second.count(SECTION_NAME.encode()) == 1

    def test_minimal_signature(self) -> None:
        sig = {"codec_id": "identity"}
        result = embed_signature(COMPONENT_HEADER, sig)
        assert read_signature_bytes(result) == sig


class TestReadErrors:
    """Error conditions for the reader."""

    def test_not_wasm_raises(self) -> None:
        with pytest.raises(ValueError, match="Not a valid Wasm binary"):
            read_signature_bytes(b"not wasm")

    def test_too_short_raises(self) -> None:
        with pytest.raises(ValueError, match="Not a valid Wasm binary"):
            read_signature_bytes(b"\x00asm")

    def test_no_signature_section_raises(self) -> None:
        with pytest.raises(ValueError, match=SECTION_NAME):
            read_signature_bytes(COMPONENT_HEADER)

    def test_file_not_found(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            read_signature(tmp_path / "nonexistent.wasm")

    def test_no_section_in_wasm_with_other_sections(self) -> None:
        """A wasm binary with a non-signature custom section still raises."""
        # Build a custom section with a different name.
        name = b"other"
        payload = b"data"
        name_field = bytes([len(name)]) + name
        section_body = name_field + payload
        section = b"\x00" + bytes([len(section_body)]) + section_body
        wasm = COMPONENT_HEADER + section
        with pytest.raises(ValueError, match=SECTION_NAME):
            read_signature_bytes(wasm)


class TestEmbedErrors:
    """Error conditions for the writer."""

    def test_not_wasm_raises(self) -> None:
        with pytest.raises(ValueError, match="Not a valid Wasm binary"):
            embed_signature(b"not wasm", {})


_CODEC_PARAMS = [
    ("codec/identity-c/identity.wasm", "identity", "identity-c"),
    (
        "codec/tiff-predictor-2-c/tiff-predictor-2.wasm",
        "tiff-predictor-2",
        "tiff-predictor-2-c",
    ),
    ("codec/zlib-rs/zlib.wasm", "zlib", "zlib-rs"),
]


class TestRealCodecFiles:
    """Verify reading signatures from the actual built codec .wasm files."""

    @pytest.fixture(params=_CODEC_PARAMS)
    def codec_info(self, request):
        from pathlib import Path

        rel_path, codec_id, implementation = request.param
        wasm_path = Path(__file__).parent.parent / rel_path
        if not wasm_path.exists():
            pytest.skip(f"{rel_path} not built")
        return wasm_path, codec_id, implementation

    def test_embedded_signature_has_expected_fields(self, codec_info) -> None:
        wasm_path, codec_id, implementation = codec_info
        sig = read_signature(wasm_path)
        assert sig["codec_id"] == codec_id
        assert sig["implementation"] == implementation
        assert "inputs" in sig
        assert "outputs" in sig

    def test_embedded_signature_matches_source(self, codec_info) -> None:
        wasm_path, _codec_id, _implementation = codec_info
        source_sig_path = wasm_path.parent / "signature.json"
        if not source_sig_path.exists():
            pytest.skip("source signature.json not found")
        with source_sig_path.open() as f:
            expected = json.load(f)
        actual = read_signature(wasm_path)
        assert actual == expected
