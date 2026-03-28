"""Core Wasm codec wrapper, CoreWasmRef, and port-map serialization."""

from __future__ import annotations

import ctypes
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import wasmtime

from chonkle.codecs._base import Backend, Codec, PortMap
from chonkle.pipeline import Direction
from chonkle.wasm_signature import Signature

type OutputPortMap = list[tuple[str, bytes | CoreWasmRef]]


@dataclass
class CoreWasmRef:
    """Deferred reference to data in a core wasm module's linear memory.

    The data remains in linear memory and is not copied to Python until
    ``materialize()`` is called. This enables single-copy transfer between
    sequential core wasm codec steps via ``ctypes.memmove``.
    """

    codec: CoreWasmCodec
    ptr: int
    length: int

    def materialize(self) -> bytes:
        """Copy the data from linear memory to a Python bytes object."""
        return bytes(
            self.codec.memory.read(self.codec.store, self.ptr, self.ptr + self.length)
        )


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
    for the codec's lifetime so downstream core codecs can single-copy
    transfer data directly from this module's linear memory.

    Required module exports: ``memory``, ``alloc``, ``dealloc``,
    ``encode``, ``decode``.
    """

    def __init__(self, engine: wasmtime.Engine, wasm_path: Path) -> None:
        self._engine = engine
        self._wasm_path = wasm_path
        self._sig = Signature.from_wasm(wasm_path)

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
    def codec_type(self) -> Backend:
        return Backend.CORE

    @property
    def codec_id(self) -> str:
        return self._sig.codec_id

    @property
    def implementation(self) -> str:
        return self._sig.implementation

    def signature(self) -> Signature:
        return self._sig

    @property
    def memory(self) -> wasmtime.Memory:
        """The module's linear memory (exposed for single-copy transfer)."""
        return self._memory

    @property
    def store(self) -> wasmtime.Store:
        """The Wasmtime store bound to this instance."""
        return self._store

    def close(self) -> None:
        """Release the Wasmtime store and its linear memory."""
        self._store = None  # type: ignore[assignment]
        self._memory = None  # type: ignore[assignment]

    def _alloc(self, size: int) -> int:
        """Allocate bytes in the module's linear memory."""
        ptr = self._alloc_fn(self._store, size)
        if ptr == 0:
            msg = f"Core wasm alloc returned null for {size} bytes"
            raise RuntimeError(msg)
        return ptr

    def call(self, direction: Direction, port_map: PortMap) -> OutputPortMap:
        """Call encode or decode using the core port-map ABI.

        Input port-map entries may be ``bytes`` or ``CoreWasmRef``. When a
        ``CoreWasmRef`` from another module is present, data is transferred
        via single-copy (``ctypes.memmove``) directly into this module's
        linear memory.

        Output port-map entries are returned as ``CoreWasmRef`` (lazy
        parsing — bulk data stays in linear memory until materialized or
        single-copied to a downstream module).
        """
        fn = self._encode_fn if direction == "encode" else self._decode_fn

        input_ptr, input_len = self._write_input(port_map)
        result = fn(self._store, input_ptr, input_len)

        output_ptr = (result >> 32) & 0xFFFFFFFF
        output_len = result & 0xFFFFFFFF

        if output_ptr == 0 and output_len == 0:
            msg = f"Core wasm codec {direction} returned error"
            raise RuntimeError(msg)

        return _deserialize_port_map_lazy(self, output_ptr, output_len)

    def _write_input(
        self, port_map: Sequence[tuple[str, bytes | CoreWasmRef]]
    ) -> tuple[int, int]:
        """Write a port-map into this module's linear memory.

        Fast path when all values are bytes; single-copy path when any
        value is a ``CoreWasmRef``.
        """
        if any(isinstance(v, CoreWasmRef) for _, v in port_map):
            return self._write_input_with_refs(port_map)
        data = _serialize_port_map(cast(PortMap, port_map))
        ptr = self._alloc(len(data))
        self._memory.write(self._store, data, ptr)
        return ptr, len(data)

    def _write_input_with_refs(
        self, port_map: Sequence[tuple[str, bytes | CoreWasmRef]]
    ) -> tuple[int, int]:
        """Build a serialized port-map in linear memory with single-copy."""
        entries: list[tuple[bytes, bytes | CoreWasmRef, int]] = []
        total = 4  # entry_count
        for name, value in port_map:
            name_bytes = name.encode("utf-8")
            data_len = value.length if isinstance(value, CoreWasmRef) else len(value)
            total += 4 + len(name_bytes) + 4 + data_len
            entries.append((name_bytes, value, data_len))

        base = self._alloc(total)
        offset = base

        self._memory.write(self._store, struct.pack("<I", len(entries)), offset)
        offset += 4

        for name_bytes, value, data_len in entries:
            self._memory.write(self._store, struct.pack("<I", len(name_bytes)), offset)
            offset += 4
            self._memory.write(self._store, name_bytes, offset)
            offset += len(name_bytes)
            self._memory.write(self._store, struct.pack("<I", data_len), offset)
            offset += 4
            if isinstance(value, CoreWasmRef):
                _copy_between_memories(value, self, offset)
            else:
                self._memory.write(self._store, value, offset)
            offset += data_len

        return base, total


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


def _deserialize_port_map_lazy(
    codec: CoreWasmCodec, base_ptr: int, total_len: int
) -> list[tuple[str, bytes | CoreWasmRef]]:
    """Parse port-map metadata from linear memory, returning CoreWasmRef entries.

    Only reads metadata (entry count, port names, data lengths) from memory.
    Bulk data remains in linear memory as ``CoreWasmRef`` entries.
    """
    store = codec.store
    mem = codec.memory
    pos = base_ptr

    hdr = bytes(mem.read(store, pos, pos + 4))
    (entry_count,) = struct.unpack("<I", hdr)
    pos += 4

    result: list[tuple[str, bytes | CoreWasmRef]] = []
    for _ in range(entry_count):
        nl = bytes(mem.read(store, pos, pos + 4))
        (name_len,) = struct.unpack("<I", nl)
        pos += 4

        name = bytes(mem.read(store, pos, pos + name_len)).decode("utf-8")
        pos += name_len

        dl = bytes(mem.read(store, pos, pos + 4))
        (data_len,) = struct.unpack("<I", dl)
        pos += 4

        result.append((name, CoreWasmRef(codec, pos, data_len)))
        pos += data_len

    return result


def _copy_between_memories(
    src_ref: CoreWasmRef, dst_codec: CoreWasmCodec, dst_offset: int
) -> None:
    """Copy data between two core wasm linear memories.

    Uses ``ctypes.memmove`` via ``Memory.data_ptr()`` for a single-copy
    transfer at native speed. Falls back to ``Memory.read`` +
    ``Memory.write`` (two copies) if ``data_ptr()`` fails.
    """
    try:
        src_base = src_ref.codec.memory.data_ptr(src_ref.codec.store)
        dst_base = dst_codec.memory.data_ptr(dst_codec.store)
        src_int = ctypes.cast(src_base, ctypes.c_void_p).value
        dst_int = ctypes.cast(dst_base, ctypes.c_void_p).value
        if src_int is None or dst_int is None:
            msg = "Memory.data_ptr() returned a null pointer"
            raise TypeError(msg)
        ctypes.memmove(dst_int + dst_offset, src_int + src_ref.ptr, src_ref.length)
    except (AttributeError, TypeError, OSError):
        data = src_ref.materialize()
        dst_codec.memory.write(dst_codec.store, data, dst_offset)


def _single_copy_transfer(
    src_ref: CoreWasmRef, dst_codec: CoreWasmCodec
) -> CoreWasmRef:
    """Transfer data between core wasm linear memories, returning a ref in the
    destination module.

    Allocates space in the destination module's memory and copies the data
    from the source module via ``_copy_between_memories``.
    """
    dst_ptr = dst_codec._alloc(src_ref.length)
    _copy_between_memories(src_ref, dst_codec, dst_ptr)
    return CoreWasmRef(dst_codec, dst_ptr, src_ref.length)
