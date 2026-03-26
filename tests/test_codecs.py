"""Tests for codec wrapper classes and binary detection."""

import struct
from pathlib import Path

import pytest

from chonkle.codecs._base import detect_codec_type
from chonkle.codecs.core import _deserialize_port_map, _serialize_port_map

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


class TestPortMapSerialization:
    """Tests for the core ABI port-map wire format serialization."""

    def test_roundtrip_single_port(self) -> None:
        port_map = [("bytes", b"\xde\xad\xbe\xef")]
        assert _deserialize_port_map(_serialize_port_map(port_map)) == port_map

    def test_roundtrip_multiple_ports(self) -> None:
        port_map = [("bytes", b"hello"), ("level", b"3")]
        assert _deserialize_port_map(_serialize_port_map(port_map)) == port_map

    def test_roundtrip_empty_port_map(self) -> None:
        port_map: list[tuple[str, bytes]] = []
        assert _deserialize_port_map(_serialize_port_map(port_map)) == port_map

    def test_roundtrip_empty_data(self) -> None:
        port_map = [("empty", b"")]
        assert _deserialize_port_map(_serialize_port_map(port_map)) == port_map

    def test_wire_format_structure(self) -> None:
        """Verify the wire format matches the spec (little-endian u32 lengths)."""
        port_map = [("bytes", b"\xde\xad")]
        raw = _serialize_port_map(port_map)
        # u32 entry_count=1, u32 name_len=5, "bytes", u32 data_len=2, 0xDE 0xAD
        assert struct.unpack_from("<I", raw, 0)[0] == 1  # entry_count
        assert struct.unpack_from("<I", raw, 4)[0] == 5  # name_len
        assert raw[8:13] == b"bytes"
        assert struct.unpack_from("<I", raw, 13)[0] == 2  # data_len
        assert raw[17:19] == b"\xde\xad"
        assert len(raw) == 19

    def test_roundtrip_large_data(self) -> None:
        data = bytes(range(256)) * 1024  # 256 KB
        port_map = [("bytes", data)]
        result = _deserialize_port_map(_serialize_port_map(port_map))
        assert result[0][0] == "bytes"
        assert result[0][1] == data
