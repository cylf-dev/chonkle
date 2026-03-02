"""Encode and decode raw chunks using metadata-driven codec pipelines."""

import json
from pathlib import Path
from typing import Any, Literal

import numcodecs
import numpy as np

import chonkle.codecs  # noqa: F401 — register custom codecs
from chonkle.wasm_runner import resolve_wasm_uri, wasm_decode, wasm_encode

Direction = Literal["encode", "decode"]


def get_codecs(source: Path | dict[str, Any]) -> list[dict[str, Any]]:
    """Extract codec specs from a pipeline definition.

    'source' may be a Path to a JSON file or a dict, both expected to
    contain a "codecs" key.
    """
    if isinstance(source, Path):
        with source.open() as f:
            metadata: dict[str, Any] = json.load(f)
    else:
        metadata = source

    if "codecs" not in metadata:
        msg = f"Missing required key 'codecs' in {metadata!r}"
        raise KeyError(msg)

    return metadata["codecs"]


def decode(data: bytes, codecs: list[dict[str, Any]]) -> np.ndarray:
    """Decode raw bytes through a codec pipeline, returning an ndarray.

    Codecs are applied in reverse order, unwinding the encoding.
    """
    result: bytes | np.ndarray = data

    for codec_spec in reversed(codecs):
        result = _apply_step(codec_spec, result, "decode")

    if not isinstance(result, np.ndarray):
        msg = "Codec pipeline did not produce a numpy array"
        raise TypeError(msg)

    return result


def encode(data: np.ndarray, codecs: list[dict[str, Any]]) -> bytes:
    """Encode an ndarray through a codec pipeline, returning raw bytes.

    Codecs are applied in forward order.
    """
    result: bytes | np.ndarray = data

    for codec_spec in codecs:
        result = _apply_step(codec_spec, result, "encode")

    if not isinstance(result, bytes):
        msg = "Codec pipeline did not produce bytes"
        raise TypeError(msg)

    return result


# ── codec step dispatch ──


def _apply_step(
    codec_spec: dict[str, Any],
    data: bytes | np.ndarray,
    direction: Direction,
) -> bytes | np.ndarray:
    """Apply one encode or decode step of the codec pipeline."""
    codec_type = codec_spec["type"]

    if codec_type == "numcodecs":
        return _apply_numcodecs(codec_spec, data, direction)
    if codec_type == "wasm":
        return _apply_wasm(codec_spec, data, direction)

    msg = f"Unknown codec type: {codec_type!r}"
    raise ValueError(msg)


def _apply_numcodecs(
    codec_spec: dict[str, Any],
    data: bytes | np.ndarray,
    direction: Direction,
) -> bytes | np.ndarray:
    """Apply one step using a numcodecs codec."""
    name = codec_spec["name"]
    config = codec_spec.get("configuration", {})
    codec_id = name.removeprefix("numcodecs.")

    codec = numcodecs.get_codec({"id": codec_id, **config})
    result = getattr(codec, direction)(data)
    if result is None:
        msg = f"Codec '{codec_id}' returned None"
        raise ValueError(msg)
    return result


def _apply_wasm(
    codec_spec: dict[str, Any],
    data: bytes | np.ndarray,
    direction: Direction,
) -> bytes | np.ndarray:
    """Apply one step using a Wasm codec module."""
    wasm_path = resolve_wasm_uri(codec_spec["uri"])
    config = codec_spec.get("configuration", {})

    # Wasm operates on raw bytes.  If the pipeline handed us an ndarray,
    # convert to bytes and reconstruct the array from the output.
    dtype: np.dtype | None = None
    shape: tuple[int, ...] | None = None

    if isinstance(data, np.ndarray):
        dtype = data.dtype
        shape = data.shape
        data = data.tobytes(order="C")

    wasm_fn = wasm_decode if direction == "decode" else wasm_encode
    result_bytes = wasm_fn(wasm_path, data, config)

    if dtype is not None and shape is not None:
        return np.frombuffer(result_bytes, dtype=dtype).reshape(shape, order="C")

    return result_bytes
