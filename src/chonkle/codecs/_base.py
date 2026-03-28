"""Base types and ABC for codec wrappers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chonkle.pipeline import Direction
    from chonkle.wasm_signature import Signature

SIGNATURES_DIR = Path(__file__).parent.parent / "signatures" / "numcodecs"

CODEC_TRANSFORM_IFACE = "chonkle:codec/transform@0.1.0"


class Backend(StrEnum):
    COMPONENT = "component"
    CORE = "core"
    NATIVE = "native"


type PortMap = list[tuple[str, bytes]]


class Codec(ABC):
    """Abstract base class for codec wrappers.

    The executor interacts only with this interface.  Concrete subclasses
    handle Component Model, Core Wasm, and native backends.
    """

    @property
    @abstractmethod
    def codec_type(self) -> Backend:
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
