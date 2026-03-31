"""Native (numcodecs) codec wrapper."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chonkle.codecs._base import SIGNATURES_DIR, Backend, Codec, PortMap
from chonkle.pipeline import Direction
from chonkle.wasm_signature import Signature


def _native_signature_path(codec_id: str) -> Path:
    """Return the path to a native codec's bundled signature JSON file.

    Raises:
        ValueError: No signature file exists for *codec_id*.
    """
    sig_path = SIGNATURES_DIR / f"{codec_id}.json"
    if not sig_path.exists():
        msg = f"No native codec signature for {codec_id!r} (looked in {SIGNATURES_DIR})"
        raise ValueError(msg)
    return sig_path


class NativeCodec(Codec):
    """Wraps a numcodecs codec object.

    Instantiated from a ``codec_id`` that must have a matching signature
    file in ``src/chonkle/signatures/numcodecs/``. ``numcodecs`` is
    imported lazily — users who only use wasm codecs do not need it
    installed.

    The ``native`` block in the signature JSON controls the calling
    convention per direction:

    - ``mode: "bytes"``: the ``bytes`` port is passed directly to
      ``codec.encode()`` / ``codec.decode()``.
    - ``mode: "ndarray"``: the ``bytes`` port is interpreted as a raw
      buffer, converted to a numpy ndarray using the dtype from the
      port named by ``dtype_port``, processed, and converted back to
      bytes.

    ``constructor_ports`` lists input port names whose JSON-decoded
    values are passed as kwargs to the numcodecs codec constructor.
    """

    def __init__(self, codec_id: str) -> None:
        sig_path = _native_signature_path(codec_id)
        raw = json.loads(sig_path.read_text())
        self._sig = Signature.from_dict(raw)
        if "native" not in raw:
            msg = f"Signature for {codec_id!r} missing required 'native' block"
            raise ValueError(msg)
        self._native: dict[str, Any] = raw["native"]
        self._numcodecs = _import_numcodecs()

    @property
    def codec_type(self) -> Backend:
        return Backend.NATIVE

    @property
    def codec_id(self) -> str:
        return self._sig.codec_id

    @property
    def implementation(self) -> str:
        return self._sig.implementation

    def signature(self) -> Signature:
        return self._sig

    def call(self, direction: Direction, port_map: PortMap) -> PortMap:
        """Execute encode or decode via numcodecs."""
        pm = dict(port_map)
        data = pm.pop("bytes")

        all_ports = {k: json.loads(v) for k, v in pm.items()}

        constructor_kwargs = {
            name: all_ports[name]
            for name in self._native["constructor_ports"]
            if name in all_ports
        }
        codec_obj = self._build_codec(constructor_kwargs)

        recipe = self._native[direction]
        if recipe["mode"] == "ndarray":
            dtype_str = all_ports[recipe["dtype_port"]]
            return self._call_ndarray(codec_obj, direction, data, dtype_str)
        return self._call_bytes(codec_obj, direction, data)

    def _build_codec(self, constructor_kwargs: dict[str, Any]) -> Any:
        """Instantiate the numcodecs codec from constructor kwargs."""
        return self._numcodecs.get_codec(
            {"id": self._sig.codec_id, **constructor_kwargs}
        )

    @staticmethod
    def _call_bytes(codec: Any, direction: Direction, data: bytes) -> PortMap:
        result = codec.encode(data) if direction == "encode" else codec.decode(data)
        return [("bytes", bytes(result))]

    @staticmethod
    def _call_ndarray(
        codec: Any, direction: Direction, data: bytes, dtype_str: str
    ) -> PortMap:
        np = _import_numpy()
        dtype = np.dtype(dtype_str)
        arr = np.frombuffer(data, dtype=dtype)
        result = codec.encode(arr) if direction == "encode" else codec.decode(arr)
        if isinstance(result, np.ndarray):
            return [("bytes", result.tobytes())]
        return [("bytes", bytes(result))]


def _import_numcodecs() -> Any:
    """Lazily import numcodecs, raising a clear error if not installed."""
    try:
        import numcodecs
    except ImportError:
        msg = (
            "numcodecs is required for native codec support but is not installed. "
            "Install it with: pip install numcodecs"
        )
        raise ImportError(msg) from None
    return numcodecs


def _import_numpy() -> Any:
    """Lazily import numpy, raising a clear error if not installed."""
    try:
        import numpy
    except ImportError:
        msg = (
            "numpy is required for ndarray-format native codecs but is not installed. "
            "Install it with: pip install numpy"
        )
        raise ImportError(msg) from None
    return numpy
