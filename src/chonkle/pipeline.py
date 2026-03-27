"""DAG pipeline: parse, validate, and prepare pipelines for execution."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from chonkle.codecs._base import Codec
    from chonkle.resolver import Resolver

Direction = Literal["encode", "decode"]


@dataclass
class WiringRef:
    """A parsed wiring reference from the pipeline JSON."""

    kind: Literal["input", "constant", "step"]
    source: str  # "input", "constant", or step name
    port: str  # port name within the source

    @classmethod
    def parse(cls, ref_str: str) -> WiringRef:
        """Parse a dot-notation wiring reference string into a WiringRef.

        Accepts the three wiring reference forms used in pipeline JSON:
        ``"input.<port>"``, ``"constant.<port>"``, and
        ``"<step_name>.<port>"``.

        Args:
            ref_str: A dot-separated wiring reference of the form
                ``<source>.<port>``.  The source is either the literal
                string ``"input"``, the literal string ``"constant"``,
                or the name of a pipeline step.

        Returns:
            A WiringRef with ``kind`` set to ``"input"``,
            ``"constant"``, or ``"step"`` depending on the source token.

        Raises:
            ValueError: If ``ref_str`` does not contain exactly one
                dot separator.
        """
        parts = ref_str.split(".", 1)
        if len(parts) != 2:
            msg = f"Invalid wiring reference {ref_str!r}: expected <source>.<port>"
            raise ValueError(msg)

        source, port = parts
        if source == "input":
            kind: Literal["input", "constant", "step"] = "input"
        elif source == "constant":
            kind = "constant"
        else:
            kind = "step"

        return cls(kind=kind, source=source, port=port)


@dataclass
class StepSpec:
    """Specification for a single pipeline step."""

    name: str  # unique DAG node identifier; used in wiring references
    codec_id: str  # logical codec identifier; may repeat across steps
    inputs: dict[str, str]  # port_name -> wiring_ref string


@dataclass
class Pipeline:
    """A parsed and validated DAG pipeline."""

    codec_id: str
    direction: Direction
    inputs: dict[str, dict[str, Any]]
    constants: dict[str, dict[str, Any]]
    outputs: dict[str, str]  # pipeline_output_name -> wiring_ref string
    sources: dict[str, str]  # codec_id -> URI (advisory fetch hints)
    steps: list[StepSpec]
    execution_order: list[str]  # step names in topological order

    @classmethod
    def parse(cls, source: Path | dict[str, Any]) -> Pipeline:
        """Parse a pipeline JSON document into a validated Pipeline.

        Reads and deserializes the JSON if ``source`` is a Path, then
        constructs a Pipeline, runs wiring validation, and computes a
        topological execution order for the steps.

        Args:
            source: Either a Path to a JSON file on disk or a
                pre-parsed dict representing the pipeline document.

        Returns:
            A fully validated Pipeline with ``execution_order``
            populated in topological order.

        Raises:
            ValueError: If ``direction`` is missing or not
                ``"encode"`` or ``"decode"``, if ``codec_id`` is not
                a string, if any wiring reference is unresolvable or
                references a non-existent step, or if the step
                dependency graph contains a cycle.
        """
        if isinstance(source, Path):
            with source.open() as f:
                data: dict[str, Any] = json.load(f)
        else:
            data = source

        direction = data.get("direction")
        if direction not in ("encode", "decode"):
            msg = f"'direction' must be 'encode' or 'decode', got {direction!r}"
            raise ValueError(msg)

        codec_id = data.get("codec_id")
        if not isinstance(codec_id, str):
            msg = f"'codec_id' must be a string, got {codec_id!r}"
            raise ValueError(msg)
        inputs = dict(data.get("inputs", {}))
        constants = dict(data.get("constants", {}))
        outputs = dict(data.get("outputs", {}))
        sources = dict(data.get("sources", {}))

        steps: list[StepSpec] = []
        for step_name, step_data in data.get("steps", {}).items():
            step = StepSpec(
                name=step_name,
                codec_id=step_data["codec_id"],
                inputs=dict(step_data.get("inputs", {})),
            )
            steps.append(step)

        _validate_pipeline(steps, inputs, constants, outputs)
        execution_order = _topological_sort(steps)

        return cls(
            codec_id=codec_id,
            direction=direction,
            inputs=inputs,
            constants=constants,
            outputs=outputs,
            sources=sources,
            steps=steps,
            execution_order=execution_order,
        )


@dataclass
class PreparedPipeline:
    """A pipeline that has been validated and is ready for execution.

    Created by :func:`prepare`.  Pass to :func:`run` to execute.
    """

    pipeline: Pipeline
    direction: Direction
    codecs: Mapping[str, Codec]
    step_by_name: Mapping[str, StepSpec]


def prepare(
    source: Path | dict[str, Any],
    direction: Direction,
    *,
    resolver: Resolver | None = None,
) -> PreparedPipeline:
    """Parse, validate, and prepare a pipeline for execution.

    Parses the pipeline JSON, resolves codec_ids via the resolver,
    validates wiring against codec signatures, and validates all step
    signatures. If this returns successfully, the pipeline is guaranteed
    executable for the given direction.

    Args:
        source: Either a Path to a pipeline JSON file or a pre-parsed
            dict representing the pipeline document.
        direction: Direction to execute (``"encode"`` or ``"decode"``).
        resolver: Codec resolver. If ``None``, a default resolver is
            created using the pipeline's ``sources`` field.

    Returns:
        A :class:`PreparedPipeline` ready for :func:`~chonkle.executor.run`.

    Raises:
        ValueError: Parsing fails, signature validation fails, a codec
            cannot be resolved, or wiring references invalid output ports.
    """
    from chonkle.resolver import Resolver

    pipeline = Pipeline.parse(source)

    if resolver is None:
        resolver = Resolver(pipeline_sources=pipeline.sources)

    step_by_name = {s.name: s for s in pipeline.steps}

    # Phase 1: resolve all codec_ids to Codec instances.
    codecs: dict[str, Codec] = {}
    for step_name in pipeline.execution_order:
        step = step_by_name[step_name]
        codecs[step_name] = resolver.resolve(step.codec_id)

    # Phase 2: validate wiring references against codec signatures
    # (step output port checks that were deferred from parse time).
    _validate_wiring_against_signatures(pipeline, codecs)

    # Phase 3: validate all signatures before any component is called.
    _validate_signatures(pipeline, step_by_name, codecs, direction)

    return PreparedPipeline(
        pipeline=pipeline,
        direction=direction,
        codecs=codecs,
        step_by_name=step_by_name,
    )


def _validate_pipeline(
    steps: list[StepSpec],
    inputs: dict[str, dict[str, Any]],
    constants: dict[str, dict[str, Any]],
    outputs: dict[str, str],
) -> None:
    """Validate step declarations and all wiring references.

    Performs the following checks in order:

    1. Step names are unique within the pipeline.
    2. Every step input wiring reference resolves to a declared
       pipeline input, constant, or an existing step.
    3. Every pipeline output wiring reference resolves in the
       same way.

    Step output port validation is deferred to ``prepare()`` time,
    where codec signatures are available.

    Raises:
        ValueError: If any of the above checks fail.
    """
    step_names = {s.name for s in steps}

    seen: set[str] = set()
    for step in steps:
        if step.name in seen:
            msg = f"Duplicate step name: {step.name!r}"
            raise ValueError(msg)
        seen.add(step.name)

    for step in steps:
        for port_name, ref_str in step.inputs.items():
            _check_ref(
                inputs,
                constants,
                ref_str,
                f"step {step.name!r} input {port_name!r}",
                step_names,
            )

    for out_name, ref_str in outputs.items():
        _check_ref(
            inputs,
            constants,
            ref_str,
            f"pipeline output {out_name!r}",
            step_names,
        )


def _check_ref(
    inputs: dict[str, dict[str, Any]],
    constants: dict[str, dict[str, Any]],
    ref_str: str,
    context: str,
    step_names: set[str],
) -> None:
    """Validate a single wiring reference against pipeline declarations.

    For ``input`` references, confirms the port is declared in
    *inputs*.  For ``constant`` references, confirms the name is
    declared in *constants*.  For step references, confirms the step
    exists.  Output port validation is deferred to ``prepare()`` time
    where codec signatures are available.

    Args:
        inputs: Pipeline-level input declarations.
        constants: Pipeline-level constant declarations.
        ref_str: The raw wiring reference string (e.g.
            ``"input.bytes"`` or ``"zstd_step.bytes"``).
        context: A human-readable label for the reference site,
            used in error messages.
        step_names: The set of all step names defined in the pipeline.

    Raises:
        ValueError: If the reference is unresolvable.
    """
    ref = WiringRef.parse(ref_str)
    if ref.kind == "input":
        if ref.port not in inputs:
            msg = (
                f"{context}: input {ref.port!r} is not declared in pipeline inputs"
                f" (declared: {inputs})"
            )
            raise ValueError(msg)
    elif ref.kind == "constant":
        if ref.port not in constants:
            msg = (
                f"{context}: constant {ref.port!r} is not declared"
                f" in pipeline constants (declared: {sorted(constants)})"
            )
            raise ValueError(msg)
    else:
        if ref.source not in step_names:
            msg = f"{context}: step {ref.source!r} does not exist"
            raise ValueError(msg)


def _topological_sort(steps: list[StepSpec]) -> list[str]:
    """Return step names in a valid execution order using Kahn's algorithm.

    Builds an in-degree map and adjacency list from the step wiring
    references, then processes nodes with zero in-degree iteratively
    until all steps are ordered or a cycle is detected.

    Args:
        steps: The list of StepSpec objects to sort.  Only
            ``"step"``-kind wiring references (i.e. inter-step
            dependencies) are considered; ``"input"`` and
            ``"constant"`` references do not contribute edges.

    Returns:
        A list of step names ordered so that every step appears
        after all steps it depends on.

    Raises:
        ValueError: If the dependency graph contains a cycle,
            listing the step names involved.
    """
    in_degree: dict[str, int] = {s.name: 0 for s in steps}
    dependents: dict[str, list[str]] = defaultdict(list)

    for step in steps:
        deps: set[str] = set()
        for ref_str in step.inputs.values():
            ref = WiringRef.parse(ref_str)
            if ref.kind == "step":
                deps.add(ref.source)
        for dep in deps:
            dependents[dep].append(step.name)
            in_degree[step.name] += 1

    queue: deque[str] = deque(name for name, deg in in_degree.items() if deg == 0)
    order: list[str] = []

    while queue:
        name = queue.popleft()
        order.append(name)
        for dependent in dependents[name]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if len(order) != len(steps):
        remaining = [n for n in in_degree if n not in set(order)]
        msg = f"Pipeline contains a cycle involving steps: {remaining}"
        raise ValueError(msg)

    return order


# ---------------------------------------------------------------------------
# Signature validation (moved from executor.py)
# ---------------------------------------------------------------------------


def _get_encode_only_inputs(signature: dict[str, Any]) -> set[str]:
    """Derive the set of encode-only input port names from a codec signature."""
    return {
        name
        for name, desc in signature.get("inputs", {}).items()
        if desc.get("encode_only", False)
    }


def _validate_wiring_against_signatures(
    pipeline: Pipeline,
    codecs: Mapping[str, Codec],
) -> None:
    """Validate step output port references against codec signatures.

    At parse time, only step existence is checked. This function validates
    that wiring references to step output ports match actual output ports
    declared in the codec's signature.
    """
    sig_outputs: dict[str, set[str]] = {}
    for step in pipeline.steps:
        sig = codecs[step.name].signature()
        sig_outputs[step.name] = set(sig.get("outputs", {}).keys())

    errors: list[str] = []

    for step in pipeline.steps:
        for port_name, ref_str in step.inputs.items():
            ref = WiringRef.parse(ref_str)
            if ref.kind == "step":
                available = sig_outputs.get(ref.source, set())
                if ref.port not in available:
                    errors.append(
                        f"Step {step.name!r} input {port_name!r}: "
                        f"step {ref.source!r} does not declare output port "
                        f"{ref.port!r} (signature outputs: {sorted(available)})"
                    )

    for out_name, ref_str in pipeline.outputs.items():
        ref = WiringRef.parse(ref_str)
        if ref.kind == "step":
            available = sig_outputs.get(ref.source, set())
            if ref.port not in available:
                errors.append(
                    f"Pipeline output {out_name!r}: "
                    f"step {ref.source!r} does not declare output port "
                    f"{ref.port!r} (signature outputs: {sorted(available)})"
                )

    if errors:
        raise ValueError(
            "Wiring validation against signatures failed:\n" + "\n".join(errors)
        )


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


def _validate_signature(
    step: StepSpec,
    signature: dict[str, Any],
    direction: str,
    pipeline: Pipeline,
    step_output_types: dict[str, dict[str, str]],
) -> dict[str, str]:
    """Verify a step's port declarations against a codec signature.

    Each check is a subset check — the step need not use every port the codec
    declares. Input validation is direction-aware: encode_only ports (from
    the signature) are excluded from the valid input set when direction is
    "decode".

    Args:
        step: Step whose declared inputs are being checked.
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

        encode_only_ports = _get_encode_only_inputs(signature)

        if direction == "decode":
            valid_inputs = {
                name
                for name, desc in signature_inputs.items()
                if not desc.get("encode_only", False)
            }
        else:
            valid_inputs = set(signature_inputs.keys())

        active_inputs = set(step.inputs.keys()) - encode_only_ports
        unknown = active_inputs - valid_inputs
        if unknown:
            errors.append(
                f"inputs {sorted(unknown)} are not valid signature {direction} inputs "
                f"{sorted(valid_inputs)}"
            )

        errors.extend(
            _check_input_types(
                step,
                signature_inputs,
                direction,
                pipeline,
                step_output_types,
                encode_only_ports,
            )
        )

    if "outputs" in signature:
        errors.extend(
            f"output port {p!r} is missing required 'type' field"
            for p, d in signature["outputs"].items()
            if "type" not in d
        )

    if errors:
        joined = "; ".join(errors)
        msg = f"Step {step.name!r}: {joined}"
        raise ValueError(msg)

    return {
        port: desc.get("type", "")
        for port, desc in signature.get("outputs", {}).items()
    }


def _check_input_types(
    step: StepSpec,
    signature_inputs: dict[str, Any],
    direction: str,
    pipeline: Pipeline,
    step_output_types: dict[str, dict[str, str]],
    encode_only_inputs: set[str],
) -> list[str]:
    """Return type-mismatch error strings for each active wired input."""
    errors: list[str] = []
    for port_name, ref_str in step.inputs.items():
        if direction == "decode" and port_name in encode_only_inputs:
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
