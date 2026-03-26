"""Codec wrapper classes normalizing different Wasm backends.

Each wrapper loads its signature at instantiation and exposes a uniform
``call(direction, port_map)`` interface so the executor does not need to
know which backend is in use.
"""

from __future__ import annotations

import struct
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


def _require_export[T](
    exports: Any, name: str, expected_type: type[T], wasm_path: Path
) -> T:
    """Get a named export and verify its type, raising on mismatch."""
    val = exports[name]
    if not isinstance(val, expected_type):
        msg = (
            f"Core wasm module at {wasm_path}: export {name!r} "
            f"is {type(val).__name__}, expected {expected_type.__name__}"
        )
        raise RuntimeError(msg)
    return val


class CoreWasmCodec(Codec):
    """Wraps a Core Wasm codec implementing the core port-map ABI.

    Instantiates a core wasm32-wasi reactor module and calls
    ``encode``/``decode`` using the binary port-map wire format via
    ``Memory.read``/``Memory.write``. The module instance is kept alive
    for the duration of the codec's lifetime to support single-copy
    transfer in a future phase.

    Required module exports: ``memory``, ``alloc``, ``dealloc``,
    ``encode``, ``decode``.
    """

    def __init__(self, engine: wasmtime.Engine, wasm_path: Path) -> None:
        self._engine = engine
        self._wasm_path = wasm_path
        self._sig = read_signature(wasm_path)

        module = wasmtime.Module.from_file(engine, str(wasm_path))
        self._store = wasmtime.Store(engine)
        self._store.set_wasi(wasmtime.WasiConfig())
        linker = wasmtime.Linker(engine)
        linker.define_wasi()
        instance = linker.instantiate(self._store, module)

        exports = instance.exports(self._store)
        self._memory = _require_export(exports, "memory", wasmtime.Memory, wasm_path)
        self._alloc_fn = _require_export(exports, "alloc", wasmtime.Func, wasm_path)
        self._dealloc_fn = _require_export(exports, "dealloc", wasmtime.Func, wasm_path)
        self._encode_fn = _require_export(exports, "encode", wasmtime.Func, wasm_path)
        self._decode_fn = _require_export(exports, "decode", wasmtime.Func, wasm_path)

    @property
    def codec_type(self) -> str:
        return "core"

    @property
    def codec_id(self) -> str:
        return self._sig.get("codec_id", "")

    @property
    def implementation(self) -> str:
        return self._sig.get("implementation", "")

    def signature(self) -> dict[str, Any]:
        return self._sig

    @property
    def memory(self) -> wasmtime.Memory:
        """The module's linear memory (exposed for single-copy transfer)."""
        return self._memory

    @property
    def store(self) -> wasmtime.Store:
        """The Wasmtime store bound to this instance."""
        return self._store

    def call(self, direction: Direction, port_map: PortMap) -> PortMap:
        """Call encode or decode using the core port-map ABI."""
        fn = self._encode_fn if direction == "encode" else self._decode_fn

        input_bytes = _serialize_port_map(port_map)
        input_ptr = self._alloc_fn(self._store, len(input_bytes))
        if input_ptr == 0:
            msg = f"Core wasm alloc returned null for {len(input_bytes)} bytes"
            raise RuntimeError(msg)
        self._memory.write(self._store, input_bytes, input_ptr)

        result = fn(self._store, input_ptr, len(input_bytes))

        output_ptr = (result >> 32) & 0xFFFFFFFF
        output_len = result & 0xFFFFFFFF

        if output_ptr == 0 and output_len == 0:
            msg = f"Core wasm codec {direction} returned error"
            raise RuntimeError(msg)

        output_bytes = bytes(
            self._memory.read(self._store, output_ptr, output_ptr + output_len)
        )
        self._dealloc_fn(self._store, output_ptr, output_len)

        return _deserialize_port_map(output_bytes)


def _serialize_port_map(port_map: PortMap) -> bytes:
    """Serialize a port-map to the core ABI wire format."""
    parts = [struct.pack("<I", len(port_map))]
    for name, data in port_map:
        name_bytes = name.encode("utf-8")
        parts.append(struct.pack("<I", len(name_bytes)))
        parts.append(name_bytes)
        parts.append(struct.pack("<I", len(data)))
        parts.append(data)
    return b"".join(parts)


def _deserialize_port_map(data: bytes) -> PortMap:
    """Deserialize a port-map from the core ABI wire format."""
    offset = 0
    (entry_count,) = struct.unpack_from("<I", data, offset)
    offset += 4
    result: PortMap = []
    for _ in range(entry_count):
        (name_len,) = struct.unpack_from("<I", data, offset)
        offset += 4
        name = data[offset : offset + name_len].decode("utf-8")
        offset += name_len
        (data_len,) = struct.unpack_from("<I", data, offset)
        offset += 4
        port_data = data[offset : offset + data_len]
        offset += data_len
        result.append((name, bytes(port_data)))
    return result


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
