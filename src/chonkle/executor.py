"""Execute a DAG pipeline via Wasmtime Component Model."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import wasmtime
import wasmtime.component

from chonkle.pipeline import Direction, Pipeline, StepSpec, WiringRef
from chonkle.wasm_download import resolve_uri

log = logging.getLogger(__name__)

type PortMap = list[tuple[str, bytes]]


def run(
    pipeline: Pipeline,
    inputs: dict[str, bytes],
    direction: Direction,
    *,
    force_download: bool = False,
) -> dict[str, bytes]:
    """Execute a pipeline in the specified direction and return output port values.

    Args:
        pipeline: Parsed and validated pipeline DAG.
        inputs: Pipeline-level input names mapped to byte values.  For forward
            execution (``direction == pipeline.direction``) keys correspond to
            ``pipeline.inputs`` names.  For inverted execution keys correspond
            to ``pipeline.outputs`` names — the caller provides data for the
            "other side" of the pipeline boundary.
        direction: Direction to execute: ``"decode"`` or ``"encode"``.
            May differ from ``pipeline.direction`` to invert the DAG.
        force_download: Re-download codec .wasm files even if cached.

    Returns:
        Pipeline output names mapped to byte values.  For forward execution
        the keys are ``pipeline.outputs`` names; for inverted execution the
        keys are ``pipeline.inputs`` data-port names.

    Raises:
        ValueError: A required input is missing or signature validation fails.
        RuntimeError: A codec component call returns an error.
    """
    inverted = direction != pipeline.direction

    if not inverted:
        # Forward: require pipeline.inputs keys; skip encode_only for decode.
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
        # Inverted: require pipeline.outputs keys (the "other side").
        for name in pipeline.outputs:
            if name not in inputs:
                msg = f"Missing pipeline input: {name!r}"
                raise ValueError(msg)

    step_by_name = {s.name: s for s in pipeline.steps}

    # Phase 1: resolve all codec URIs to local paths.
    wasm_paths = {
        name: resolve_uri(step_by_name[name].src, force_download=force_download)
        for name in pipeline.execution_order
    }

    # Phase 2: validate all signatures before any component is called.
    _validate_signatures(pipeline, step_by_name, wasm_paths, direction)

    # Phase 3: build the shared Wasmtime engine and execute.
    config = wasmtime.Config()
    config.cache = True
    engine = wasmtime.Engine(config)

    if direction != pipeline.direction:
        return _execute_inverted(
            pipeline, step_by_name, wasm_paths, inputs, direction, engine
        )
    return _execute_forward(
        pipeline, step_by_name, wasm_paths, inputs, direction, engine
    )


def _execute_forward(
    pipeline: Pipeline,
    step_by_name: dict[str, StepSpec],
    wasm_paths: dict[str, Path],
    inputs: dict[str, bytes],
    direction: Direction,
    engine: wasmtime.Engine,
) -> dict[str, bytes]:
    """Execute steps in topological order, calling the direction WIT function.

    Seeds value_store with constants and pipeline inputs (omitting encode_only
    inputs when direction is ``"decode"``).

    Args:
        pipeline: Validated pipeline with execution_order, constants, and outputs.
        step_by_name: Step name to StepSpec lookup.
        wasm_paths: Step name to resolved local .wasm path.
        inputs: Caller-provided byte values keyed by pipeline input names.
        direction: Runtime execution direction.
        engine: Shared Wasmtime engine (carries the compilation cache).

    Returns:
        Pipeline output names mapped to byte values.

    Raises:
        RuntimeError: A codec component call fails or returns an Err result.
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
        output_map = _call_component(engine, wasm_paths[step_name], direction, port_map)
        for port_name, value in output_map:
            value_store[f"{step_name}.{port_name}"] = value

    return {
        out_name: value_store[ref_str] for out_name, ref_str in pipeline.outputs.items()
    }


def _execute_inverted(
    pipeline: Pipeline,
    step_by_name: dict[str, StepSpec],
    wasm_paths: dict[str, Path],
    inputs: dict[str, bytes],
    direction: Direction,
    engine: wasmtime.Engine,
) -> dict[str, bytes]:
    """Execute steps in reversed topological order, routing results backward.

    Seeds value_store from pipeline output wiring refs (the caller provides
    data for the "other side" of the pipeline boundary), then runs each step
    in reverse calling the direction WIT function.

    Args:
        pipeline: Validated pipeline with execution_order, constants, and outputs.
        step_by_name: Step name to StepSpec lookup.
        wasm_paths: Step name to resolved local .wasm path.
        inputs: Caller-provided byte values keyed by pipeline output names.
        direction: Runtime execution direction.
        engine: Shared Wasmtime engine (carries the compilation cache).

    Returns:
        Pipeline input names mapped to byte values (excluding encode_only inputs
        when direction is ``"decode"``).

    Raises:
        RuntimeError: A codec component call fails or returns an Err result.
    """
    value_store: dict[str, bytes] = {}

    for name, descriptor in pipeline.constants.items():
        value_store[f"constant.{name}"] = json.dumps(descriptor["value"]).encode()

    # Seed from pipeline outputs — wiring refs become the starting values for
    # the reversed traversal.
    for out_name, ref_str in pipeline.outputs.items():
        value_store[ref_str] = inputs[out_name]

    for step_name in reversed(pipeline.execution_order):
        step = step_by_name[step_name]
        port_map = _inverted_port_map(step_name, step, value_store, direction)
        log.debug(
            "step %r: calling %s with %d ports", step_name, direction, len(port_map)
        )
        output_map = _call_component(engine, wasm_paths[step_name], direction, port_map)
        for port_name, value in output_map:
            if port_name in step.inputs and port_name not in step.encode_only_inputs:
                value_store[step.inputs[port_name]] = value

    # Collect from pipeline inputs; encode_only ports excluded during decode.
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

    Args:
        step: Step whose inputs define the port names and wiring refs.
        value_store: Accumulated resolved byte values keyed by wiring ref string.
        direction: Runtime execution direction; encode_only_inputs are excluded
            when this is ``"decode"``.

    Returns:
        List of (port_name, bytes) tuples to pass to the codec component.
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
    encode_only_inputs are appended when calling encode (they are codec
    parameters, e.g. compression level).

    Args:
        step_name: Name of the step; used to look up output values in
            value_store via the ``"<step_name>.<port>"`` key convention.
        step: Step whose outputs define which values to look up and whose
            encode_only_inputs are conditionally appended.
        value_store: Accumulated resolved byte values keyed by wiring ref string.
        direction: Runtime execution direction; encode_only_inputs are appended
            only when this is ``"encode"``.

    Returns:
        List of (port_name, bytes) tuples to pass to the codec component.
    """
    port_map: PortMap = []
    for port_name in step.outputs:
        val = value_store.get(f"{step_name}.{port_name}")
        if val is not None:
            port_map.append((port_name, val))
    if direction == "encode":
        for port_name in step.encode_only_inputs:
            port_map.append((port_name, value_store[step.inputs[port_name]]))
    return port_map


def _validate_signatures(
    pipeline: Pipeline,
    step_by_name: dict[str, StepSpec],
    wasm_paths: dict[str, Path],
    direction: Direction,
) -> None:
    """Validate all step signatures before any component is called.

    Collects errors from every step and raises a single ValueError listing
    all problems, so the caller sees every issue at once rather than failing
    on the first.

    Args:
        pipeline: Pipeline providing execution_order.
        step_by_name: Step name to StepSpec lookup.
        wasm_paths: Step name to resolved local .wasm path.
        direction: Runtime execution direction (may differ from pipeline.direction).

    Raises:
        ValueError: One or more steps have signature validation errors.
    """
    errors: list[str] = []
    step_output_types: dict[str, dict[str, str]] = {}
    for step_name in pipeline.execution_order:
        step = step_by_name[step_name]
        wasm_path = wasm_paths[step_name]
        signature_path = wasm_path.parent / (wasm_path.stem + ".signature.json")
        try:
            output_types = _validate_signature(
                step, signature_path, direction, pipeline, step_output_types
            )
            step_output_types[step_name] = output_types or {}
        except (ValueError, FileNotFoundError) as exc:
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
    """Return type-mismatch error strings for each active wired input.

    For each active step input, resolves the type declared by the wiring
    source (pipeline input, constant, or upstream step output) and compares
    it against the codec's declared type.  Returns an empty list when all
    types match or when type information is absent on either side.

    Args:
        step: Step whose active inputs are being type-checked.
        signature_inputs: Codec signature input descriptors keyed by port name,
            each containing at least a ``"type"`` field.
        direction: Runtime execution direction; encode_only_inputs are skipped
            when this is ``"decode"``.
        pipeline: The pipeline, used to resolve type info for ``input.*`` and
            ``constant.*`` wiring refs.
        step_output_types: Accumulated output types from previously validated
            upstream steps, keyed by step name then port name; used to resolve
            types for ``<step>.<port>`` wiring refs.

    Returns:
        List of human-readable error strings describing type mismatches.
        Empty when all active inputs pass type checks.
    """
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
    signature_path: Path,
    direction: str,
    pipeline: Pipeline,
    step_output_types: dict[str, dict[str, str]],
) -> dict[str, str]:
    """Verify a step's port declarations against its codec signature sidecar.

    Each check is a subset check — the step need not use every port the codec
    declares. Input validation is direction-aware: encode_only ports are
    excluded from the valid input set when direction is "decode". After name
    checks, each active input's wiring source type is compared against the
    codec's declared type for that port; a mismatch is an error.

    Args:
        step: Step whose declared inputs and outputs are being checked.
        signature_path: Path to the .signature.json sidecar file.
        direction: Runtime execution direction ("encode" or "decode").
        pipeline: The pipeline, used to resolve input and constant types.
        step_output_types: Accumulated output types from previously validated
            upstream steps, keyed by step name then port name.

    Returns:
        Mapping of output port name to type string, from the signature.

    Raises:
        FileNotFoundError: The signature sidecar does not exist.
        ValueError: Declared ports are not valid per the signature.
    """
    if not signature_path.exists():
        msg = f"Step {step.name!r}: signature not found at {signature_path}"
        raise FileNotFoundError(msg)

    with signature_path.open() as f:
        signature = json.load(f)

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

        # Active inputs: wired ports minus encode_only_inputs (skipped during decode).
        active_inputs = set(step.inputs.keys()) - set(step.encode_only_inputs)
        unknown = active_inputs - valid_inputs
        if unknown:
            errors.append(
                f"inputs {sorted(unknown)} are not valid signature {direction} inputs "
                f"{sorted(valid_inputs)}"
            )

        # Type compatibility: check each active wired input.
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
        msg = f"Step {step.name!r}: {joined} (from {signature_path})"
        raise ValueError(msg)

    return {
        port: desc.get("type", "")
        for port, desc in signature.get("outputs", {}).items()
    }


def _call_component(
    engine: wasmtime.Engine,
    wasm_path: Path,
    direction: str,
    port_map: PortMap,
) -> PortMap:
    """Call encode or decode on a single Wasm Component Model codec.

    Args:
        engine: Shared Wasmtime engine (carries the compilation cache).
        wasm_path: Path to the compiled .wasm file.
        direction: "encode" or "decode" — selects the exported function.
        port_map: Named input byte buffers to pass to the component.

    Returns:
        Named output byte buffers returned by the component.

    Raises:
        RuntimeError: The component does not export the expected function,
            or the component returns an Err result.
    """
    component = wasmtime.component.Component.from_file(engine, str(wasm_path))

    store = wasmtime.Store(engine)
    store.set_wasi(wasmtime.WasiConfig())
    linker = wasmtime.component.Linker(engine)
    linker.add_wasip2()

    instance = linker.instantiate(store, component)
    fn = _get_function(instance, store, engine, component.type, direction, wasm_path)

    result = fn(store, port_map)
    fn.post_return(store)

    # wasmtime returns the Err string directly for a result<T, E> error variant.
    if isinstance(result, str):
        msg = f"Codec component {direction} returned error: {result}"
        raise RuntimeError(msg)

    # Normalize to list[tuple[str, bytes]] in case wasmtime returns lists.
    return [(str(name), bytes(data)) for name, data in result]


def _get_function(
    instance: wasmtime.component.Instance,
    store: wasmtime.Store,
    engine: wasmtime.Engine,
    component_type: Any,
    fn_name: str,
    wasm_path: Path,
) -> Any:
    """Return the named Func from a component's exports.

    Searches world-level exports first, then interface-level exports.
    Codec components export 'transform' as an interface, so encode/decode
    live one level down inside that interface.

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
        RuntimeError: The function is not found in any export scope.
    """
    comp_exports = component_type.exports(engine)

    for name, item in comp_exports.items():
        if (
            isinstance(item, wasmtime.component.FuncType)
            and name == fn_name
            and (idx := instance.get_export_index(store, name)) is not None
        ):
            return instance.get_func(store, idx)

    for iface_name, item in comp_exports.items():
        if isinstance(item, wasmtime.component.ComponentInstanceType):
            iface_exports = item.exports(engine)
            if fn_name in iface_exports and isinstance(
                iface_exports[fn_name], wasmtime.component.FuncType
            ):
                if (iface_idx := instance.get_export_index(store, iface_name)) is None:
                    continue
                fn_idx = instance.get_export_index(store, fn_name, iface_idx)
                if fn_idx is not None:
                    return instance.get_func(store, fn_idx)

    msg = (
        f"Component at {wasm_path} does not export {fn_name!r} "
        "(expected in chonkle:codec/transform interface)"
    )
    raise RuntimeError(msg)
