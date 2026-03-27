"""Base types and ABC for codec wrappers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chonkle.pipeline import Direction

SIGNATURES_DIR = Path(__file__).parent.parent / "signatures" / "numcodecs"

CODEC_TRANSFORM_IFACE = "chonkle:codec/transform@0.1.0"

type PortMap = list[tuple[str, bytes]]

# Wasm binary header constants.
_WASM_MAGIC = b"\x00asm"
_CORE_WASM_VERSION = b"\x01\x00\x00\x00"
_COMPONENT_VERSION = b"\x0d\x00\x01\x00"


@dataclass(frozen=True)
class PortDescriptor:
    """Descriptor for a single input or output port in a codec signature."""

    type: str
    required: bool = True
    default: Any = None
    encode_only: bool = False


@dataclass(frozen=True)
class Signature:
    """Structured codec signature.

    Replaces the raw ``dict[str, Any]`` signature format with typed fields.
    """

    codec_id: str
    implementation: str
    inputs: dict[str, PortDescriptor] = field(default_factory=dict)
    outputs: dict[str, PortDescriptor] = field(default_factory=dict)
    data_format: str | None = None  # "bytes" or "ndarray", native codecs only

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
            )
        outputs: dict[str, PortDescriptor] = {}
        for name, desc in d.get("outputs", {}).items():
            outputs[name] = PortDescriptor(
                type=desc.get("type", ""),
                required=desc.get("required", True),
                default=desc.get("default"),
            )
        return cls(
            codec_id=d.get("codec_id", ""),
            implementation=d.get("implementation", ""),
            inputs=inputs,
            outputs=outputs,
            data_format=d.get("data_format"),
        )

    def encode_only_inputs(self) -> set[str]:
        """Port names where encode_only is True."""
        return {n for n, p in self.inputs.items() if p.encode_only}

    def output_types(self) -> dict[str, str]:
        """Map of output port name to type string."""
        return {n: p.type for n, p in self.outputs.items()}


class Codec(ABC):
    """Abstract base class for codec wrappers.

    The executor interacts only with this interface.  Concrete subclasses
    handle Component Model, Core Wasm, and native backends.
    """

    @property
    @abstractmethod
    def codec_type(self) -> str:
        """Backend type: ``"component"``, ``"core"``, or ``"native"``."""
        ...

    @property
    @abstractmethod
    def codec_id(self) -> str:
        """The logical codec identifier from the signature."""
        ...

    @property
    @abstractmethod
    def implementation(self) -> str:
        """The specific implementation identifier from the signature."""
        ...

    @abstractmethod
    def signature(self) -> Signature:
        """Return the codec's signature (loaded at instantiation)."""
        ...

    @abstractmethod
    def call(
        self, direction: Direction, port_map: list[tuple[str, bytes | Any]]
    ) -> list[tuple[str, bytes | Any]]:
        """Execute encode or decode and return the output port-map.

        Input and return entries may contain ``bytes`` values (all backends) or
        ``CoreWasmRef`` deferred references (core wasm backend only).
        See ``OutputPortMap`` type alias.
        """
        ...


def detect_codec_type(wasm_path: Path) -> str:
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
        return "component"
    if version == _CORE_WASM_VERSION:
        return "core"

    msg = f"Unknown Wasm version {version.hex()}: {wasm_path}"
    raise ValueError(msg)
