"""Execute a prepared DAG pipeline via codec wrappers."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping

from chonkle.codecs._base import Codec
from chonkle.codecs.core import CoreWasmCodec, CoreWasmRef, OutputPortMap
from chonkle.pipeline import (
    Direction,
    Pipeline,
    PreparedPipeline,
    StepSpec,
    _get_encode_only_inputs,
)

log = logging.getLogger(__name__)


def run(
    prepared: PreparedPipeline,
    inputs: dict[str, bytes],
) -> dict[str, bytes]:
    """Execute a prepared pipeline and return output port values.

    Args:
        prepared: A :class:`PreparedPipeline` from :func:`prepare`.
        inputs: Pipeline-level input names mapped to byte values.  For
            forward execution (``direction == pipeline.direction``) keys
            correspond to ``pipeline.inputs`` names.  For inverted
            execution keys correspond to ``pipeline.outputs`` names.

    Returns:
        Pipeline output names mapped to byte values.

    Raises:
        ValueError: A required input is missing.
        RuntimeError: A codec call returns an error.
    """
    pipeline = prepared.pipeline
    direction = prepared.direction
    inverted = direction != pipeline.direction

    if not inverted:
        required = [
            name
            for name, desc in pipeline.inputs.items()
            if direction == "encode" or not desc.get("encode_only", False)
        ]
        for name in required:
            if name not in inputs:
                msg = f"Missing pipeline input: {name!r}"
                raise ValueError(msg)
    else:
        for name in pipeline.outputs:
            if name not in inputs:
                msg = f"Missing pipeline input: {name!r}"
                raise ValueError(msg)

    step_by_name = prepared.step_by_name

    if inverted:
        return _execute_inverted(
            pipeline, step_by_name, prepared.codecs, inputs, direction
        )
    return _execute_forward(pipeline, step_by_name, prepared.codecs, inputs, direction)


def _execute_forward(
    pipeline: Pipeline,
    step_by_name: Mapping[str, StepSpec],
    codecs: Mapping[str, Codec],
    inputs: dict[str, bytes],
    direction: Direction,
) -> dict[str, bytes]:
    """Execute steps in topological order, calling the direction function.

    Seeds value_store with constants and pipeline inputs (omitting encode_only
    inputs when direction is ``"decode"``).
    """
    value_store: dict[str, bytes | CoreWasmRef] = {}

    for name, descriptor in pipeline.constants.items():
        value_store[f"constant.{name}"] = json.dumps(descriptor["value"]).encode()

    active_inputs = [
        name
        for name, desc in pipeline.inputs.items()
        if direction == "encode" or not desc.get("encode_only", False)
    ]
    for name in active_inputs:
        value_store[f"input.{name}"] = inputs[name]

    for step_name in pipeline.execution_order:
        step = step_by_name[step_name]
        codec = codecs[step_name]
        encode_only = _get_encode_only_inputs(codec.signature())
        port_map = _forward_port_map(step, value_store, direction, encode_only, codec)
        log.debug(
            "step %r: calling %s with %d ports", step_name, direction, len(port_map)
        )
        output_map = codec.call(direction, port_map)
        for port_name, value in output_map:
            value_store[f"{step_name}.{port_name}"] = value

    return {
        out_name: _materialize(value_store[ref_str])
        for out_name, ref_str in pipeline.outputs.items()
    }


def _execute_inverted(
    pipeline: Pipeline,
    step_by_name: Mapping[str, StepSpec],
    codecs: Mapping[str, Codec],
    inputs: dict[str, bytes],
    direction: Direction,
) -> dict[str, bytes]:
    """Execute steps in reversed topological order, routing results backward."""
    value_store: dict[str, bytes | CoreWasmRef] = {}

    for name, descriptor in pipeline.constants.items():
        value_store[f"constant.{name}"] = json.dumps(descriptor["value"]).encode()

    for out_name, ref_str in pipeline.outputs.items():
        value_store[ref_str] = inputs[out_name]

    for step_name in reversed(pipeline.execution_order):
        step = step_by_name[step_name]
        codec = codecs[step_name]
        sig = codec.signature()
        encode_only = _get_encode_only_inputs(sig)
        output_ports = list(sig.get("outputs", {}).keys())
        port_map = _inverted_port_map(
            step_name, step, value_store, direction, output_ports, encode_only, codec
        )
        log.debug(
            "step %r: calling %s with %d ports", step_name, direction, len(port_map)
        )
        output_map = codec.call(direction, port_map)
        for port_name, value in output_map:
            if port_name in step.inputs and port_name not in encode_only:
                value_store[step.inputs[port_name]] = value

    return {
        name: _materialize(value_store[f"input.{name}"])
        for name, desc in pipeline.inputs.items()
        if direction == "encode" or not desc.get("encode_only", False)
    }


def _forward_port_map(
    step: StepSpec,
    value_store: dict[str, bytes | CoreWasmRef],
    direction: Direction,
    encode_only_inputs: set[str],
    codec: Codec,
) -> OutputPortMap:
    """Build the port-map for a step in forward execution.

    Iterates step.inputs, omitting encode_only ports when direction is decode.
    ``CoreWasmRef`` values are passed through for core wasm codecs (enabling
    single-copy transfer) and materialized to bytes for other backends.
    """
    is_core = isinstance(codec, CoreWasmCodec)
    port_map: OutputPortMap = []
    for port_name, ref_str in step.inputs.items():
        if direction == "decode" and port_name in encode_only_inputs:
            continue
        value = value_store[ref_str]
        if isinstance(value, CoreWasmRef) and not is_core:
            value = value.materialize()
        port_map.append((port_name, value))
    return port_map


def _inverted_port_map(
    step_name: str,
    step: StepSpec,
    value_store: dict[str, bytes | CoreWasmRef],
    direction: Direction,
    output_ports: list[str],
    encode_only_inputs: set[str],
    codec: Codec,
) -> OutputPortMap:
    """Build the port-map for a step in inverted execution.

    The step's forward-direction outputs (from signature) become its
    inverted-direction inputs. Inputs wired from constants (non-encode_only)
    are always included. encode_only_inputs are appended only when calling
    encode. ``CoreWasmRef`` values are passed through for core wasm codecs
    and materialized for other backends.
    """
    is_core = isinstance(codec, CoreWasmCodec)
    port_map: OutputPortMap = []
    for port_name in output_ports:
        val = value_store.get(f"{step_name}.{port_name}")
        if val is not None:
            if isinstance(val, CoreWasmRef) and not is_core:
                val = val.materialize()
            port_map.append((port_name, val))
    for port_name, ref_str in step.inputs.items():
        if ref_str.startswith("constant.") and port_name not in encode_only_inputs:
            port_map.append((port_name, value_store[ref_str]))
    if direction == "encode":
        for port_name in encode_only_inputs:
            if port_name in step.inputs:
                port_map.append((port_name, value_store[step.inputs[port_name]]))
    return port_map


def _materialize(value: bytes | CoreWasmRef) -> bytes:
    """Resolve a value to bytes, materializing ``CoreWasmRef`` if needed."""
    if isinstance(value, CoreWasmRef):
        return value.materialize()
    return value
