"""Component Model Wasm codec wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import wasmtime
import wasmtime.component

from chonkle.codecs._base import CODEC_TRANSFORM_IFACE, Codec, PortMap, Signature
from chonkle.pipeline import Direction
from chonkle.wasm_signature import read_signature


class ComponentCodec(Codec):
    """Wraps a Component Model Wasm codec.

    Instantiates a Wasmtime Component Model component and calls
    ``encode``/``decode`` via the WIT ``chonkle:codec/transform`` interface.
    Signature is read from the ``chonkle:signature`` custom section at init.
    """

    def __init__(self, engine: wasmtime.Engine, wasm_path: Path) -> None:
        self._engine = engine
        self._wasm_path = wasm_path
        self._sig = Signature.from_dict(read_signature(wasm_path))
        self._component = wasmtime.component.Component.from_file(engine, str(wasm_path))

    @property
    def codec_type(self) -> str:
        return "component"

    @property
    def codec_id(self) -> str:
        return self._sig.codec_id

    @property
    def implementation(self) -> str:
        return self._sig.implementation

    def signature(self) -> Signature:
        return self._sig

    def call(self, direction: Direction, port_map: PortMap) -> PortMap:
        """Call encode or decode on the Component Model codec."""
        store = wasmtime.Store(self._engine)
        store.set_wasi(wasmtime.WasiConfig())
        linker = wasmtime.component.Linker(self._engine)
        linker.add_wasip2()

        instance = linker.instantiate(store, self._component)
        fn = self._get_function(instance, store, direction)

        result = fn(store, port_map)
        fn.post_return(store)

        if isinstance(result, str):
            msg = f"Codec component {direction} returned error: {result}"
            raise RuntimeError(msg)

        return [(str(name), bytes(data)) for name, data in result]

    def _get_function(
        self,
        instance: wasmtime.component.Instance,
        store: wasmtime.Store,
        fn_name: str,
    ) -> Any:
        """Return the named Func from the CODEC_TRANSFORM_IFACE interface export."""
        comp_exports = self._component.type.exports(self._engine)
        item = comp_exports.get(CODEC_TRANSFORM_IFACE)
        if isinstance(item, wasmtime.component.ComponentInstanceType):
            iface_exports = item.exports(self._engine)
            if fn_name in iface_exports and isinstance(
                iface_exports[fn_name], wasmtime.component.FuncType
            ):
                iface_idx = instance.get_export_index(store, CODEC_TRANSFORM_IFACE)
                if iface_idx is not None:
                    fn_idx = instance.get_export_index(store, fn_name, iface_idx)
                    if fn_idx is not None:
                        return instance.get_func(store, fn_idx)

        msg = (
            f"Component at {self._wasm_path} does not export {fn_name!r} "
            f"in the {CODEC_TRANSFORM_IFACE!r} interface. "
            "Components must be compiled against codec.wit."
        )
        raise RuntimeError(msg)
