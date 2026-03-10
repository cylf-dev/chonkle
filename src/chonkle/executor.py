"""Execute a DAG pipeline via Wasmtime Component Model."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import wasmtime
import wasmtime.component

from chonkle.pipeline import Pipeline, StepSpec
from chonkle.wasm_download import resolve_uri

log = logging.getLogger(__name__)

type PortMap = list[tuple[str, bytes]]


def run(
    pipeline: Pipeline,
    inputs: dict[str, bytes],
    *,
    force_download: bool = False,
) -> dict[str, bytes]:
    """Execute a pipeline and return the named output port values.

    Args:
        pipeline: Parsed and validated pipeline DAG.
        inputs: Pipeline-level input names mapped to byte values.
        force_download: Re-download codec .wasm files even if cached.

    Returns:
        Pipeline output names mapped to byte values.

    Raises:
        ValueError: A required input is missing or manifest validation fails.
        RuntimeError: A codec component call returns an error.
    """
    direction = pipeline.direction
    if direction == "decode":
        active_inputs = [
            name
            for name, desc in pipeline.inputs.items()
            if not desc.get("encode_only", False)
        ]
    else:
        active_inputs = list(pipeline.inputs)

    for name in active_inputs:
        if name not in inputs:
            msg = f"Missing pipeline input: {name!r}"
            raise ValueError(msg)

    step_by_name = {s.name: s for s in pipeline.steps}

    # Phase 1: resolve all codec URIs to local paths.
    wasm_paths = _resolve_uris(pipeline, step_by_name, force_download=force_download)

    # Phase 2: validate all manifests before any component is called.
    _validate_manifests(pipeline, step_by_name, wasm_paths)

    # Phase 3: execute steps in topological order.
    return _execute_steps(pipeline, step_by_name, wasm_paths, inputs, active_inputs)


def _execute_steps(
    pipeline: Pipeline,
    step_by_name: dict[str, StepSpec],
    wasm_paths: dict[str, Path],
    inputs: dict[str, bytes],
    active_inputs: list[str],
) -> dict[str, bytes]:
    """Execute pipeline steps in topological order and return the output port values.

    Seeds a value_store (wiring-ref → bytes) with active inputs and serialized
    constants, then calls each step's codec component in execution order, routing
    outputs into the store for downstream steps to consume.

    Args:
        pipeline: Validated pipeline with execution_order, constants, and outputs.
        step_by_name: Step name to StepSpec lookup.
        wasm_paths: Step name to resolved local .wasm path.
        inputs: Pipeline-level input names mapped to byte values.
        active_inputs: Input names active for the pipeline's direction.

    Returns:
        Pipeline output names mapped to byte values.

    Raises:
        RuntimeError: A codec component call fails or returns an Err result.
    """
    direction = pipeline.direction
    value_store: dict[str, bytes] = {}

    for name in active_inputs:
        value_store[f"input.{name}"] = inputs[name]

    # Constants are serialized as UTF-8 JSON bytes so codecs receive them
    # as a self-describing byte payload.
    for name, descriptor in pipeline.constants.items():
        value_store[f"constant.{name}"] = json.dumps(descriptor["value"]).encode()

    config = wasmtime.Config()
    config.cache = True
    engine = wasmtime.Engine(config)

    for step_name in pipeline.execution_order:
        step = step_by_name[step_name]
        wasm_path = wasm_paths[step_name]

        # Build the input port-map.  encode_only_inputs are omitted during
        # decode so the codec does not receive stale or unavailable data.
        port_map: PortMap = []
        for port_name, ref_str in step.inputs.items():
            if direction == "decode" and port_name in step.encode_only_inputs:
                continue
            port_map.append((port_name, value_store[ref_str]))

        log.debug(
            "step %r: calling %s with %d ports", step_name, direction, len(port_map)
        )

        output_map = _call_component(engine, wasm_path, direction, port_map)

        for port_name, value in output_map:
            value_store[f"{step_name}.{port_name}"] = value

    return {
        out_name: value_store[ref_str] for out_name, ref_str in pipeline.outputs.items()
    }


def _resolve_uris(
    pipeline: Pipeline,
    step_by_name: dict[str, StepSpec],
    *,
    force_download: bool = False,
) -> dict[str, Path]:
    """Resolve all step codec URIs to local .wasm paths.

    Args:
        pipeline: Pipeline whose execution_order drives iteration.
        step_by_name: Step name to StepSpec lookup.
        force_download: Re-download even if a cached file exists.

    Returns:
        Step name to resolved local .wasm path.
    """
    return {
        name: resolve_uri(step_by_name[name].src, force_download=force_download)
        for name in pipeline.execution_order
    }


def _validate_manifests(
    pipeline: Pipeline,
    step_by_name: dict[str, StepSpec],
    wasm_paths: dict[str, Path],
) -> None:
    """Validate all step manifests before any component is called.

    Collects errors from every step and raises a single ValueError listing
    all problems, so the caller sees every issue at once rather than failing
    on the first.

    Args:
        pipeline: Pipeline providing execution_order and direction.
        step_by_name: Step name to StepSpec lookup.
        wasm_paths: Step name to resolved local .wasm path.

    Raises:
        ValueError: One or more steps have manifest validation errors.
    """
    errors: list[str] = []
    for step_name in pipeline.execution_order:
        step = step_by_name[step_name]
        wasm_path = wasm_paths[step_name]
        manifest_path = wasm_path.parent / (wasm_path.stem + ".manifest.json")
        try:
            _validate_manifest(step, manifest_path, pipeline.direction)
        except (ValueError, FileNotFoundError) as exc:
            errors.append(str(exc))
    if errors:
        raise ValueError("Pipeline manifest validation failed:\n" + "\n".join(errors))


def _validate_manifest(step: StepSpec, manifest_path: Path, direction: str) -> None:
    """Verify a step's port declarations against its codec manifest sidecar.

    Each check is a subset check — the step need not use every port the codec
    declares. Input validation is direction-aware: encode_only ports are
    excluded from the valid input set when direction is "decode".

    Args:
        step: Step whose declared inputs and outputs are being checked.
        manifest_path: Path to the .manifest.json sidecar file.
        direction: Pipeline direction ("encode" or "decode").

    Raises:
        FileNotFoundError: The manifest sidecar does not exist.
        ValueError: Declared ports are not valid per the manifest.
    """
    if not manifest_path.exists():
        msg = f"Step {step.name!r}: manifest not found at {manifest_path}"
        raise FileNotFoundError(msg)

    with manifest_path.open() as f:
        manifest = json.load(f)

    errors: list[str] = []

    if "inputs" in manifest:
        manifest_inputs: dict[str, Any] = manifest["inputs"]
        if direction == "decode":
            valid_inputs = {
                name
                for name, desc in manifest_inputs.items()
                if not desc.get("encode_only", False)
            }
        else:
            valid_inputs = set(manifest_inputs.keys())

        # Active inputs: wired ports minus encode_only_inputs (skipped during decode).
        active_inputs = set(step.inputs.keys()) - set(step.encode_only_inputs)
        unknown = active_inputs - valid_inputs
        if unknown:
            errors.append(
                f"inputs {sorted(unknown)} are not valid manifest {direction} inputs "
                f"{sorted(valid_inputs)}"
            )

    if "outputs" in manifest:
        manifest_outputs = set(manifest["outputs"].keys())
        declared_outputs = set(step.outputs)
        unknown_outputs = declared_outputs - manifest_outputs
        if unknown_outputs:
            errors.append(
                f"outputs {sorted(unknown_outputs)} are not valid manifest outputs "
                f"{sorted(manifest_outputs)}"
            )

    if errors:
        joined = "; ".join(errors)
        msg = f"Step {step.name!r}: {joined} (from {manifest_path})"
        raise ValueError(msg)


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
