"""Codec signatures: types, detection, and Wasm custom-section I/O.

Pure-Python implementation — no wasmtime or instantiation needed.  Works with
both core Wasm (version ``01 00 00 00``) and Component Model (version
``0d 00 01 00``) binaries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chonkle.codecs._base import Backend

SECTION_NAME = "chonkle:signature"

# Wasm binary header constants.
_WASM_MAGIC = b"\x00asm"
_HEADER_SIZE = 8  # magic (4) + version (4)
_CORE_WASM_VERSION = b"\x01\x00\x00\x00"
_COMPONENT_VERSION = b"\x0d\x00\x01\x00"


@dataclass(frozen=True)
class PortDescriptor:
    """Descriptor for a single input or output port in a codec signature."""

    type: str
    required: bool = True
    default: Any = None
    encode_only: bool = False
    decode_only: bool = False


@dataclass(frozen=True)
class Signature:
    """Structured codec signature.

    Replaces the raw ``dict[str, Any]`` signature format with typed fields.
    """

    codec_id: str
    implementation: str
    inputs: dict[str, PortDescriptor] = field(default_factory=dict)
    outputs: dict[str, PortDescriptor] = field(default_factory=dict)

    @classmethod
    def from_wasm(cls, wasm_path: str | Path) -> Signature:
        """Construct a Signature from a ``.wasm`` file's embedded custom section."""
        return cls.from_dict(read_signature(wasm_path))

    @classmethod
    def from_json(cls, json_path: str | Path) -> Signature:
        """Construct a Signature from a JSON signature file."""
        return cls.from_dict(json.loads(Path(json_path).read_text()))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Signature:
        """Construct a Signature from a raw JSON-derived dict."""
        inputs: dict[str, PortDescriptor] = {}
        for name, desc in d.get("inputs", {}).items():
            inputs[name] = PortDescriptor(
                type=desc.get("type", ""),
                required=desc.get("required", True),
                default=desc.get("default"),
                encode_only=desc.get("encode_only", False),
                decode_only=desc.get("decode_only", False),
            )
        outputs: dict[str, PortDescriptor] = {}
        for name, desc in d.get("outputs", {}).items():
            outputs[name] = PortDescriptor(
                type=desc.get("type", ""),
                required=desc.get("required", True),
                default=desc.get("default"),
            )
        codec_id = d.get("codec_id")
        if not codec_id:
            msg = "Signature missing required field 'codec_id'"
            raise ValueError(msg)
        return cls(
            codec_id=codec_id,
            implementation=d.get("implementation", ""),
            inputs=inputs,
            outputs=outputs,
        )

    def encode_only_inputs(self) -> set[str]:
        """Port names where encode_only is True."""
        return {n for n, p in self.inputs.items() if p.encode_only}

    def decode_only_inputs(self) -> set[str]:
        """Port names where decode_only is True."""
        return {n for n, p in self.inputs.items() if p.decode_only}

    def output_types(self) -> dict[str, str]:
        """Map of output port name to type string."""
        return {n: p.type for n, p in self.outputs.items()}


def _read_leb128(data: bytes, offset: int) -> tuple[int, int]:
    """Decode an unsigned LEB128 value starting at *offset*.

    Returns (value, new_offset).
    """
    result = 0
    shift = 0
    while True:
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << shift
        if (byte & 0x80) == 0:
            break
        shift += 7
    return result, offset


def _encode_leb128(value: int) -> bytes:
    """Encode *value* as unsigned LEB128."""
    buf = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value != 0:
            byte |= 0x80
        buf.append(byte)
        if value == 0:
            break
    return bytes(buf)


def read_signature(wasm_path: str | Path) -> dict[str, Any]:
    """Read the ``chonkle:signature`` custom section from a ``.wasm`` file.

    Args:
        wasm_path: Path to the ``.wasm`` binary.

    Returns:
        The parsed signature dict.

    Raises:
        FileNotFoundError: The ``.wasm`` file does not exist.
        ValueError: The file is not a valid Wasm binary or contains no
            ``chonkle:signature`` section.
    """
    data = Path(wasm_path).read_bytes()
    return read_signature_bytes(data, context=str(wasm_path))


def read_signature_bytes(data: bytes, *, context: str = "<bytes>") -> dict[str, Any]:
    """Read the ``chonkle:signature`` custom section from in-memory Wasm bytes.

    Args:
        data: Raw Wasm binary content.
        context: Label used in error messages (typically a file path).

    Returns:
        The parsed signature dict.

    Raises:
        ValueError: Not a valid Wasm binary or no ``chonkle:signature`` section.
    """
    if len(data) < _HEADER_SIZE or data[:4] != _WASM_MAGIC:
        msg = f"Not a valid Wasm binary: {context}"
        raise ValueError(msg)

    offset = _HEADER_SIZE

    while offset < len(data):
        section_id = data[offset]
        offset += 1
        section_size, offset = _read_leb128(data, offset)
        section_end = offset + section_size

        if section_id == 0:  # custom section
            name_len, name_start = _read_leb128(data, offset)
            name = data[name_start : name_start + name_len].decode("utf-8")
            payload_start = name_start + name_len

            if name == SECTION_NAME:
                payload = data[payload_start:section_end]
                return json.loads(payload)

        offset = section_end

    msg = f"No {SECTION_NAME!r} custom section in {context}"
    raise ValueError(msg)


def embed_signature(wasm_bytes: bytes, signature: dict[str, Any]) -> bytes:
    """Append a ``chonkle:signature`` custom section to Wasm bytes.

    If a ``chonkle:signature`` section already exists, it is replaced.

    Args:
        wasm_bytes: Raw Wasm binary content.
        signature: The signature dict to embed as JSON.

    Returns:
        New Wasm bytes with the custom section appended.

    Raises:
        ValueError: *wasm_bytes* is not a valid Wasm binary.
    """
    if len(wasm_bytes) < _HEADER_SIZE or wasm_bytes[:4] != _WASM_MAGIC:
        msg = "Not a valid Wasm binary"
        raise ValueError(msg)

    stripped = _strip_section(wasm_bytes, SECTION_NAME)
    payload = json.dumps(signature, separators=(",", ":")).encode("utf-8")
    section = _build_custom_section(SECTION_NAME, payload)
    return stripped + section


def _strip_section(data: bytes, section_name: str) -> bytes:
    """Return *data* with any custom section named *section_name* removed."""
    result = bytearray(data[:_HEADER_SIZE])
    offset = _HEADER_SIZE

    while offset < len(data):
        section_start = offset
        section_id = data[offset]
        offset += 1
        section_size, offset = _read_leb128(data, offset)
        section_end = offset + section_size

        keep = True
        if section_id == 0:
            name_len, name_start = _read_leb128(data, offset)
            name = data[name_start : name_start + name_len].decode("utf-8")
            if name == section_name:
                keep = False

        if keep:
            result.extend(data[section_start:section_end])

        offset = section_end

    return bytes(result)


def _build_custom_section(name: str, payload: bytes) -> bytes:
    """Build a Wasm custom section (id=0) with *name* and *payload*."""
    name_bytes = name.encode("utf-8")
    name_field = _encode_leb128(len(name_bytes)) + name_bytes
    section_body = name_field + payload
    return b"\x00" + _encode_leb128(len(section_body)) + section_body


def detect_codec_type(wasm_path: Path) -> Backend:
    """Detect whether a ``.wasm`` file is core or component model.

    Reads the 8-byte header (magic + version) to distinguish:

    - ``0d 00 01 00`` → ``"component"`` (Component Model)
    - ``01 00 00 00`` → ``"core"`` (Core Wasm)

    Args:
        wasm_path: Path to the ``.wasm`` binary.

    Returns:
        ``"component"`` or ``"core"``.

    Raises:
        ValueError: Not a valid Wasm binary or unrecognized version.
    """
    with Path(wasm_path).open("rb") as f:
        header = f.read(8)

    if len(header) < 8 or header[:4] != _WASM_MAGIC:
        msg = f"Not a valid Wasm binary: {wasm_path}"
        raise ValueError(msg)

    version = header[4:8]
    if version == _COMPONENT_VERSION:
        return Backend.COMPONENT
    if version == _CORE_WASM_VERSION:
        return Backend.CORE

    msg = f"Unknown Wasm version {version.hex()}: {wasm_path}"
    raise ValueError(msg)
