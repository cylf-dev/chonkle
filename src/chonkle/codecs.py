"""Codec wrapper classes normalizing different Wasm backends.

Each wrapper loads its signature at instantiation and exposes a uniform
``call(direction, port_map)`` interface so the executor does not need to
know which backend is in use.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import wasmtime
import wasmtime.component

from chonkle.pipeline import Direction
from chonkle.wasm_signature import read_signature

# WIT interface export key as it appears in a compiled Component Model binary.
CODEC_TRANSFORM_IFACE = "chonkle:codec/transform@0.1.0"

type PortMap = list[tuple[str, bytes]]

# Wasm binary header constants.
_WASM_MAGIC = b"\x00asm"
_CORE_WASM_VERSION = b"\x01\x00\x00\x00"
_COMPONENT_VERSION = b"\x0d\x00\x01\x00"


class Codec(ABC):
    """Abstract base class for codec wrappers.

    The executor interacts only with this interface.  Concrete subclasses
    handle Component Model, Core Wasm, and (future) native backends.
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
    def signature(self) -> dict[str, Any]:
        """Return the codec's signature (loaded at instantiation)."""
        ...

    @abstractmethod
    def call(self, direction: Direction, port_map: PortMap) -> PortMap:
        """Execute encode or decode and return the output port-map."""
        ...


class ComponentCodec(Codec):
    """Wraps a Component Model Wasm codec.

    Instantiates a Wasmtime Component Model component and calls
    ``encode``/``decode`` via the WIT ``chonkle:codec/transform`` interface.
    Signature is read from the ``chonkle:signature`` custom section at init.
    """

    def __init__(self, engine: wasmtime.Engine, wasm_path: Path) -> None:
        self._engine = engine
        self._wasm_path = wasm_path
        self._sig = read_signature(wasm_path)

    @property
    def codec_type(self) -> str:
        return "component"

    @property
    def codec_id(self) -> str:
        return self._sig.get("codec_id", "")

    @property
    def implementation(self) -> str:
        return self._sig.get("implementation", "")

    def signature(self) -> dict[str, Any]:
        return self._sig

    def call(self, direction: Direction, port_map: PortMap) -> PortMap:
        """Call encode or decode on the Component Model codec."""
        component = wasmtime.component.Component.from_file(
            self._engine, str(self._wasm_path)
        )
        store = wasmtime.Store(self._engine)
        store.set_wasi(wasmtime.WasiConfig())
        linker = wasmtime.component.Linker(self._engine)
        linker.add_wasip2()

        instance = linker.instantiate(store, component)
        fn = _get_function(
            instance, store, self._engine, component.type, direction, self._wasm_path
        )

        result = fn(store, port_map)
        fn.post_return(store)

        if isinstance(result, str):
            msg = f"Codec component {direction} returned error: {result}"
            raise RuntimeError(msg)

        return [(str(name), bytes(data)) for name, data in result]


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


def _get_function(
    instance: wasmtime.component.Instance,
    store: wasmtime.Store,
    engine: wasmtime.Engine,
    component_type: Any,
    fn_name: str,
    wasm_path: Path,
) -> Any:
    """Return the named Func from the CODEC_TRANSFORM_IFACE interface export.

    Components must be compiled against codec.wit and export the transform
    interface under the exact key defined by CODEC_TRANSFORM_IFACE.

    Args:
        instance: Instantiated Wasmtime component.
        store: Wasmtime store bound to the instance.
        engine: Wasmtime engine used to inspect types.
        component_type: Type descriptor of the component.
        fn_name: Name of the function to locate ("encode" or "decode").
        wasm_path: Path used only for error messages.

    Returns:
        The located Wasmtime Func.

    Raises:
        RuntimeError: The function is not found in the expected interface.
    """
    comp_exports = component_type.exports(engine)
    item = comp_exports.get(CODEC_TRANSFORM_IFACE)
    if isinstance(item, wasmtime.component.ComponentInstanceType):
        iface_exports = item.exports(engine)
        if fn_name in iface_exports and isinstance(
            iface_exports[fn_name], wasmtime.component.FuncType
        ):
            iface_idx = instance.get_export_index(store, CODEC_TRANSFORM_IFACE)
            if iface_idx is not None:
                fn_idx = instance.get_export_index(store, fn_name, iface_idx)
                if fn_idx is not None:
                    return instance.get_func(store, fn_idx)

    msg = (
        f"Component at {wasm_path} does not export {fn_name!r} "
        f"in the {CODEC_TRANSFORM_IFACE!r} interface. "
        "Components must be compiled against codec.wit."
    )
    raise RuntimeError(msg)
