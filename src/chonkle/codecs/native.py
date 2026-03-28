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

    The ``data_format`` field in the signature controls the calling
    convention:

    - ``"bytes"``: the ``bytes`` port is passed directly to
      ``codec.encode()`` / ``codec.decode()``.
    - ``"ndarray"``: the ``bytes`` port is interpreted as a raw buffer,
      converted to a numpy ndarray using the ``dtype`` port, processed,
      and converted back to bytes.

    Non-``bytes`` ports in the input port-map (excluding ``dtype``) are
    JSON-decoded and passed as constructor kwargs to the numcodecs codec.
    """

    def __init__(self, codec_id: str) -> None:
        self._sig = Signature.from_json(_native_signature_path(codec_id))
        self._data_format: str = self._sig.data_format or "bytes"
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

        # All remaining ports are JSON-decoded kwargs for the codec constructor.
        # For ndarray codecs, pop dtype — it controls buffer conversion and
        # may or may not be a constructor arg (Delta accepts it, Shuffle does not).
        kwargs = {k: json.loads(v) for k, v in pm.items()}
        dtype_str: str | None = None
        if self._data_format == "ndarray":
            dtype_str = kwargs.pop("dtype", None)

        codec_obj = self._build_codec(kwargs, dtype_str)

        if self._data_format == "ndarray":
            return self._call_ndarray(codec_obj, direction, data, dtype_str)
        return self._call_bytes(codec_obj, direction, data)

    def _build_codec(self, kwargs: dict[str, Any], dtype_str: str | None) -> Any:
        """Instantiate the numcodecs codec, trying dtype as a constructor arg."""
        config = {"id": self._sig.codec_id, **kwargs}
        if dtype_str is not None:
            try:
                return self._numcodecs.get_codec({**config, "dtype": dtype_str})
            except TypeError:
                pass
        return self._numcodecs.get_codec(config)

    @staticmethod
    def _call_bytes(codec: Any, direction: Direction, data: bytes) -> PortMap:
        result = codec.encode(data) if direction == "encode" else codec.decode(data)
        return [("bytes", bytes(result))]

    @staticmethod
    def _call_ndarray(
        codec: Any, direction: Direction, data: bytes, dtype_str: str | None
    ) -> PortMap:
        np = _import_numpy()
        if dtype_str is None:
            msg = "ndarray-format native codec requires a 'dtype' port"
            raise ValueError(msg)
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
