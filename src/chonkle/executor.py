"""Execute a DAG pipeline via Wasmtime Component Model."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import wasmtime

from chonkle.codecs import Codec, ComponentCodec, PortMap, detect_codec_type
from chonkle.pipeline import Direction, Pipeline, StepSpec, WiringRef
from chonkle.wasm_download import resolve_uri

log = logging.getLogger(__name__)


@dataclass
class PreparedPipeline:
    """A pipeline that has been validated and is ready for execution.

    Created by :func:`prepare`.  Pass to :func:`run` to execute.
    """

    pipeline: Pipeline
    direction: Direction
    codecs: Mapping[str, Codec]


def prepare(
    pipeline: Pipeline,
    direction: Direction,
    *,
    force_download: bool = False,
) -> PreparedPipeline:
    """Validate a pipeline and prepare it for execution.

    Resolves codec URIs, instantiates codec wrappers (which load embedded
    signatures), and validates all step signatures.  If this returns
    successfully, the pipeline is guaranteed executable for the given
    direction.

    Args:
        pipeline: Parsed and validated pipeline DAG.
        direction: Direction to execute (``"encode"`` or ``"decode"``).
        force_download: Re-download codec ``.wasm`` files even if cached.

    Returns:
        A :class:`PreparedPipeline` ready for :func:`run`.

    Raises:
        ValueError: Signature validation fails or a codec type is
            unsupported.
    """
    step_by_name = {s.name: s for s in pipeline.steps}

    # Phase 1: resolve all codec URIs to local paths.
    wasm_paths = {
        name: resolve_uri(step_by_name[name].src, force_download=force_download)
        for name in pipeline.execution_order
    }

    # Phase 2: create codec wrapper instances (loads signatures from
    # embedded custom sections).
    config = wasmtime.Config()
    config.cache = True
    engine = wasmtime.Engine(config)

    codecs: dict[str, Codec] = {}
    for step_name in pipeline.execution_order:
        wasm_path = wasm_paths[step_name]
        codec_type = detect_codec_type(wasm_path)
        if codec_type == "component":
            codecs[step_name] = ComponentCodec(engine, wasm_path)
        else:
            msg = f"Core Wasm codecs are not yet supported: {wasm_path}"
            raise ValueError(msg)

    # Phase 3: validate all signatures before any component is called.
    _validate_signatures(pipeline, step_by_name, codecs, direction)

    return PreparedPipeline(pipeline=pipeline, direction=direction, codecs=codecs)


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

    step_by_name = {s.name: s for s in pipeline.steps}

    if inverted:
        return _execute_inverted(
            pipeline, step_by_name, prepared.codecs, inputs, direction
        )
    return _execute_forward(pipeline, step_by_name, prepared.codecs, inputs, direction)


def _execute_forward(
    pipeline: Pipeline,
    step_by_name: dict[str, StepSpec],
    codecs: Mapping[str, Codec],
    inputs: dict[str, bytes],
    direction: Direction,
) -> dict[str, bytes]:
    """Execute steps in topological order, calling the direction function.

    Seeds value_store with constants and pipeline inputs (omitting encode_only
    inputs when direction is ``"decode"``).
    """
    value_store: dict[str, bytes] = {}

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
        port_map = _forward_port_map(step, value_store, direction)
        log.debug(
            "step %r: calling %s with %d ports", step_name, direction, len(port_map)
        )
        output_map = codecs[step_name].call(direction, port_map)
        for port_name, value in output_map:
            value_store[f"{step_name}.{port_name}"] = value

    return {
        out_name: value_store[ref_str] for out_name, ref_str in pipeline.outputs.items()
    }


def _execute_inverted(
    pipeline: Pipeline,
    step_by_name: dict[str, StepSpec],
    codecs: Mapping[str, Codec],
    inputs: dict[str, bytes],
    direction: Direction,
) -> dict[str, bytes]:
    """Execute steps in reversed topological order, routing results backward."""
    value_store: dict[str, bytes] = {}

    for name, descriptor in pipeline.constants.items():
        value_store[f"constant.{name}"] = json.dumps(descriptor["value"]).encode()

    for out_name, ref_str in pipeline.outputs.items():
        value_store[ref_str] = inputs[out_name]

    for step_name in reversed(pipeline.execution_order):
        step = step_by_name[step_name]
        port_map = _inverted_port_map(step_name, step, value_store, direction)
        log.debug(
            "step %r: calling %s with %d ports", step_name, direction, len(port_map)
        )
        output_map = codecs[step_name].call(direction, port_map)
        for port_name, value in output_map:
            if port_name in step.inputs and port_name not in step.encode_only_inputs:
                value_store[step.inputs[port_name]] = value

    return {
        name: value_store[f"input.{name}"]
        for name, desc in pipeline.inputs.items()
        if direction == "encode" or not desc.get("encode_only", False)
    }


def _forward_port_map(
    step: StepSpec,
    value_store: dict[str, bytes],
    direction: Direction,
) -> PortMap:
    """Build the port-map for a step in forward execution.

    Iterates step.inputs, omitting encode_only_inputs when direction is decode.
    """
    port_map: PortMap = []
    for port_name, ref_str in step.inputs.items():
        if direction == "decode" and port_name in step.encode_only_inputs:
            continue
        port_map.append((port_name, value_store[ref_str]))
    return port_map


def _inverted_port_map(
    step_name: str,
    step: StepSpec,
    value_store: dict[str, bytes],
    direction: Direction,
) -> PortMap:
    """Build the port-map for a step in inverted execution.

    The step's forward-direction outputs become its inverted-direction inputs.
    Inputs wired from constants (non-encode_only) are always included.
    encode_only_inputs are appended only when calling encode.
    """
    port_map: PortMap = []
    for port_name in step.outputs:
        val = value_store.get(f"{step_name}.{port_name}")
        if val is not None:
            port_map.append((port_name, val))
    for port_name, ref_str in step.inputs.items():
        if ref_str.startswith("constant.") and port_name not in step.encode_only_inputs:
            port_map.append((port_name, value_store[ref_str]))
    if direction == "encode":
        for port_name in step.encode_only_inputs:
            port_map.append((port_name, value_store[step.inputs[port_name]]))
    return port_map


def _validate_signatures(
    pipeline: Pipeline,
    step_by_name: dict[str, StepSpec],
    codecs: Mapping[str, Codec],
    direction: Direction,
) -> None:
    """Validate all step signatures before any component is called.

    Collects errors from every step and raises a single ValueError listing
    all problems.
    """
    errors: list[str] = []
    step_output_types: dict[str, dict[str, str]] = {}
    for step_name in pipeline.execution_order:
        step = step_by_name[step_name]
        codec = codecs[step_name]
        try:
            output_types = _validate_signature(
                step, codec.signature(), direction, pipeline, step_output_types
            )
            step_output_types[step_name] = output_types or {}
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise ValueError("Pipeline signature validation failed:\n" + "\n".join(errors))


def _check_input_types(
    step: StepSpec,
    signature_inputs: dict[str, Any],
    direction: str,
    pipeline: Pipeline,
    step_output_types: dict[str, dict[str, str]],
) -> list[str]:
    """Return type-mismatch error strings for each active wired input."""
    errors: list[str] = []
    for port_name, ref_str in step.inputs.items():
        if direction == "decode" and port_name in step.encode_only_inputs:
            continue
        if port_name not in signature_inputs:
            continue
        expected_type = signature_inputs[port_name].get("type")
        if expected_type is None:
            continue
        ref = WiringRef.parse(ref_str)
        if ref.kind == "input":
            actual_type = (pipeline.inputs.get(ref.port) or {}).get("type")
        elif ref.kind == "constant":
            actual_type = (pipeline.constants.get(ref.port) or {}).get("type")
        else:
            actual_type = step_output_types.get(ref.source, {}).get(ref.port)
        if actual_type is not None and actual_type != expected_type:
            errors.append(
                f"input {port_name!r}: wiring {ref_str!r} provides type"
                f" {actual_type!r} but codec expects {expected_type!r}"
            )
    return errors


def _validate_signature(
    step: StepSpec,
    signature: dict[str, Any],
    direction: str,
    pipeline: Pipeline,
    step_output_types: dict[str, dict[str, str]],
) -> dict[str, str]:
    """Verify a step's port declarations against a codec signature.

    Each check is a subset check — the step need not use every port the codec
    declares. Input validation is direction-aware: encode_only ports are
    excluded from the valid input set when direction is "decode".

    Args:
        step: Step whose declared inputs and outputs are being checked.
        signature: The codec signature dict (from ``codec.signature()``).
        direction: Runtime execution direction ("encode" or "decode").
        pipeline: The pipeline, used to resolve input and constant types.
        step_output_types: Accumulated output types from previously validated
            upstream steps.

    Returns:
        Mapping of output port name to type string, from the signature.

    Raises:
        ValueError: Declared ports are not valid per the signature.
    """
    errors: list[str] = []

    if "inputs" in signature:
        signature_inputs: dict[str, Any] = signature["inputs"]
        errors.extend(
            f"input port {p!r} is missing required 'type' field"
            for p, d in signature_inputs.items()
            if "type" not in d
        )

        if direction == "decode":
            valid_inputs = {
                name
                for name, desc in signature_inputs.items()
                if not desc.get("encode_only", False)
            }
        else:
            valid_inputs = set(signature_inputs.keys())

        active_inputs = set(step.inputs.keys()) - set(step.encode_only_inputs)
        unknown = active_inputs - valid_inputs
        if unknown:
            errors.append(
                f"inputs {sorted(unknown)} are not valid signature {direction} inputs "
                f"{sorted(valid_inputs)}"
            )

        errors.extend(
            _check_input_types(
                step, signature_inputs, direction, pipeline, step_output_types
            )
        )

    if "outputs" in signature:
        errors.extend(
            f"output port {p!r} is missing required 'type' field"
            for p, d in signature["outputs"].items()
            if "type" not in d
        )

        signature_outputs = set(signature["outputs"].keys())
        declared_outputs = set(step.outputs)
        unknown_outputs = declared_outputs - signature_outputs
        if unknown_outputs:
            errors.append(
                f"outputs {sorted(unknown_outputs)} are not valid signature outputs "
                f"{sorted(signature_outputs)}"
            )

    if errors:
        joined = "; ".join(errors)
        msg = f"Step {step.name!r}: {joined}"
        raise ValueError(msg)

    return {
        port: desc.get("type", "")
        for port, desc in signature.get("outputs", {}).items()
    }
