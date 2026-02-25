"""Decode raw chunks using metadata-driven codec pipelines."""

import json
from pathlib import Path
from typing import Any

import numcodecs
import numpy as np

import chonkle.decode.codecs  # noqa: F401 — register custom codecs
from chonkle.decode.wasm_runner import resolve_wasm_uri, wasm_decode


def decode_chunk(chunk_path: Path) -> np.ndarray:
    """Decode an encoded chunk and return the resulting array."""
    codecs = _read_metadata(chunk_path)

    data: bytes | np.ndarray = chunk_path.read_bytes()

    for codec_spec in reversed(codecs):
        data = _decode_step(codec_spec, data)

    if not isinstance(data, np.ndarray):
        msg = "Codec pipeline did not produce a numpy array"
        raise TypeError(msg)

    return data


def _read_metadata(chunk_path: Path) -> list[dict[str, Any]]:
    """Read the codec pipeline from the sidecar metadata JSON."""
    metadata_path = Path(str(chunk_path) + ".json")

    with metadata_path.open() as f:
        metadata = json.load(f)

    if "codecs" not in metadata:
        msg = f"Missing required key 'codecs' in {metadata_path}"
        raise KeyError(msg)

    return metadata["codecs"]


def _decode_step(
    codec_spec: dict[str, Any],
    data: bytes | np.ndarray,
) -> bytes | np.ndarray:
    """Decode one step of the codec pipeline."""
    codec_type = codec_spec["type"]

    if codec_type == "numcodecs":
        return _decode_step_numcodecs(codec_spec, data)
    if codec_type == "wasm":
        return _decode_step_wasm(codec_spec, data)

    msg = f"Unknown codec type: {codec_type!r}"
    raise ValueError(msg)


def _decode_step_numcodecs(
    codec_spec: dict[str, Any],
    data: bytes | np.ndarray,
) -> bytes | np.ndarray:
    """Decode one step using a numcodecs codec."""
    name = codec_spec["name"]
    config = codec_spec.get("configuration", {})
    codec_id = name.removeprefix("numcodecs.")

    codec = numcodecs.get_codec({"id": codec_id, **config})
    result = codec.decode(data)
    if result is None:
        msg = f"Codec '{codec_id}' returned None"
        raise ValueError(msg)
    return result


def _decode_step_wasm(
    codec_spec: dict[str, Any],
    data: bytes | np.ndarray,
) -> bytes | np.ndarray:
    """Decode one step using a WASM codec module."""
    wasm_path = resolve_wasm_uri(codec_spec["uri"])
    config = codec_spec.get("configuration", {})

    # WASM operates on raw bytes.  If the pipeline handed us an ndarray,
    # convert to bytes and reconstruct the array from the output.
    dtype: np.dtype | None = None
    shape: tuple[int, ...] | None = None

    if isinstance(data, np.ndarray):
        dtype = data.dtype
        shape = data.shape
        data = data.tobytes(order="C")

    result_bytes = wasm_decode(wasm_path, data, config)

    if dtype is not None and shape is not None:
        return np.frombuffer(result_bytes, dtype=dtype).reshape(shape, order="C")

    return result_bytes
