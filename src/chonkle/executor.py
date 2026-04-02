"""Execute a prepared DAG pipeline via codec wrappers."""

from __future__ import annotations

import json
import logging

from chonkle.codecs.core import CoreWasmCodec, CoreWasmRef, OutputPortMap
from chonkle.pipeline import (
    PreparedPipeline,
    StepSpec,
)

log = logging.getLogger(__name__)


def run(
    prepared: PreparedPipeline,
    inputs: dict[str, bytes],
) -> dict[str, bytes]:
    """Execute a prepared pipeline and return output port values.

    Args:
        prepared: A PreparedPipeline from prepare().
        inputs: Pipeline-level input names mapped to byte values. For
            forward execution (direction == pipeline.direction) keys
            correspond to pipeline.inputs names. For inverted
            execution keys correspond to pipeline.outputs names.

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
            if (direction == "encode" or not desc.encode_only)
            and (direction == "decode" or not desc.decode_only)
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

    if inverted:
        return _execute_inverted(prepared, inputs)
    return _execute_forward(prepared, inputs)


def _execute_forward(
    prepared: PreparedPipeline,
    inputs: dict[str, bytes],
) -> dict[str, bytes]:
    """Execute steps in topological order, calling the direction function."""
    pipeline = prepared.pipeline
    direction = prepared.direction
    value_store: dict[str, bytes | CoreWasmRef] = {}

    for name, descriptor in pipeline.constants.items():
        value_store[f"constant.{name}"] = json.dumps(descriptor.value).encode()

    active_inputs = [
        name
        for name, desc in pipeline.inputs.items()
        if (direction == "encode" or not desc.encode_only)
        and (direction == "decode" or not desc.decode_only)
    ]
    for name in active_inputs:
        value_store[f"input.{name}"] = inputs[name]

    for step_name, step in pipeline.steps.items():
        codec = prepared.codecs[step_name]
        encode_only = prepared.encode_only_inputs[step_name]
        decode_only = prepared.decode_only_inputs[step_name]
        port_map = _forward_port_map(
            step, value_store, direction, encode_only, decode_only, codec
        )
        log.debug(
            "step %r: calling %s with %d ports", step_name, direction, len(port_map)
        )
        output_map = codec.call(direction, port_map)
        for port_name, value in output_map:
            value_store[f"{step_name}.{port_name}"] = value

    return {
        out_name: _materialize(value_store[str(ref)])
        for out_name, ref in pipeline.outputs.items()
    }


def _execute_inverted(
    prepared: PreparedPipeline,
    inputs: dict[str, bytes],
) -> dict[str, bytes]:
    """Execute steps in reverse topological order, routing results backward."""
    pipeline = prepared.pipeline
    direction = prepared.direction
    value_store: dict[str, bytes | CoreWasmRef] = {}

    for name, descriptor in pipeline.constants.items():
        value_store[f"constant.{name}"] = json.dumps(descriptor.value).encode()

    for out_name, ref in pipeline.outputs.items():
        value_store[str(ref)] = inputs[out_name]

    for step_name, step in reversed(pipeline.steps.items()):
        codec = prepared.codecs[step_name]
        encode_only = prepared.encode_only_inputs[step_name]
        decode_only = prepared.decode_only_inputs[step_name]
        output_ports = prepared.output_ports[step_name]
        port_map = _inverted_port_map(
            step_name,
            step,
            value_store,
            direction,
            output_ports,
            encode_only,
            decode_only,
            codec,
        )
        log.debug(
            "step %r: calling %s with %d ports", step_name, direction, len(port_map)
        )
        output_map = codec.call(direction, port_map)
        for port_name, value in output_map:
            if port_name in step.inputs and port_name not in encode_only | decode_only:
                value_store[str(step.inputs[port_name])] = value

    return {
        name: _materialize(value_store[f"input.{name}"])
        for name, desc in pipeline.inputs.items()
        if (direction == "encode" or not desc.encode_only)
        and (direction == "decode" or not desc.decode_only)
    }


def _forward_port_map(
    step: StepSpec,
    value_store: dict[str, bytes | CoreWasmRef],
    direction: str,
    encode_only_inputs: frozenset[str],
    decode_only_inputs: frozenset[str],
    codec: object,
) -> OutputPortMap:
    """Build the port-map for a step in forward execution."""
    is_core = isinstance(codec, CoreWasmCodec)
    port_map: OutputPortMap = []
    for port_name, ref in step.inputs.items():
        if direction == "decode" and port_name in encode_only_inputs:
            continue
        if direction == "encode" and port_name in decode_only_inputs:
            continue
        value = value_store[str(ref)]
        if isinstance(value, CoreWasmRef) and not is_core:
            value = value.materialize()
        port_map.append((port_name, value))
    return port_map


def _inverted_port_map(
    step_name: str,
    step: StepSpec,
    value_store: dict[str, bytes | CoreWasmRef],
    direction: str,
    output_ports: tuple[str, ...],
    encode_only_inputs: frozenset[str],
    decode_only_inputs: frozenset[str],
    codec: object,
) -> OutputPortMap:
    """Build the port-map for a step in inverted execution."""
    is_core = isinstance(codec, CoreWasmCodec)
    port_map: OutputPortMap = []
    for port_name in output_ports:
        val = value_store.get(f"{step_name}.{port_name}")
        if val is not None:
            if isinstance(val, CoreWasmRef) and not is_core:
                val = val.materialize()
            port_map.append((port_name, val))
    direction_only = encode_only_inputs | decode_only_inputs
    for port_name, ref in step.inputs.items():
        if ref.kind == "constant" and port_name not in direction_only:
            port_map.append((port_name, value_store[str(ref)]))
    active_dir_ports = (
        encode_only_inputs
        if direction == "encode"
        else decode_only_inputs
        if direction == "decode"
        else frozenset()
    )
    for port_name in active_dir_ports:
        if port_name in step.inputs:
            port_map.append((port_name, value_store[str(step.inputs[port_name])]))
    return port_map


def _materialize(value: bytes | CoreWasmRef) -> bytes:
    """Resolve a value to bytes, materializing CoreWasmRef if needed."""
    if isinstance(value, CoreWasmRef):
        return value.materialize()
    return value
