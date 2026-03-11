"""DAG pipeline: parse JSON, validate wiring, topological sort."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

Direction = Literal["encode", "decode"]


@dataclass
class WiringRef:
    """A parsed wiring reference from the pipeline JSON."""

    kind: Literal["input", "constant", "step"]
    source: str  # "input", "constant", or step codec_id
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
    src: str
    inputs: dict[str, str]  # port_name -> wiring_ref string
    outputs: list[str]  # declared output port names
    encode_only_inputs: list[str]  # port names that are skipped during decode


@dataclass
class Pipeline:
    """A parsed and validated DAG pipeline."""

    codec_id: str
    direction: Direction
    inputs: dict[str, Any]
    constants: dict[str, Any]
    outputs: dict[str, str]  # pipeline_output_name -> wiring_ref string
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
                references a non-existent step or port, or if the step
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

        steps: list[StepSpec] = []
        for step_data in data.get("steps", []):
            step = StepSpec(
                name=step_data["name"],
                codec_id=step_data["codec_id"],
                src=step_data["src"],
                inputs=dict(step_data.get("inputs", {})),
                outputs=list(step_data.get("outputs", [])),
                encode_only_inputs=list(step_data.get("encode_only_inputs", [])),
            )
            steps.append(step)

        pipeline = cls(
            codec_id=codec_id,
            direction=direction,
            inputs=inputs,
            constants=constants,
            outputs=outputs,
            steps=steps,
            execution_order=[],
        )

        _validate_pipeline(pipeline)
        pipeline.execution_order = _topological_sort(steps)
        return pipeline


def _validate_pipeline(pipeline: Pipeline) -> None:
    """Validate step declarations and all wiring references.

    Performs the following checks in order:

    1. Step names are unique within the pipeline.
    2. Every port listed in a step's ``encode_only_inputs`` is also
       declared in that step's ``inputs``.
    3. Every step input wiring reference resolves to a declared
       pipeline input, constant, or an output port of another step.
    4. Every pipeline output wiring reference resolves in the
       same way.

    Raises:
        ValueError: If any of the above checks fail.
    """
    step_names = {s.name for s in pipeline.steps}
    step_outputs = {s.name: set(s.outputs) for s in pipeline.steps}

    seen: set[str] = set()
    for step in pipeline.steps:
        if step.name in seen:
            msg = f"Duplicate step name: {step.name!r}"
            raise ValueError(msg)
        seen.add(step.name)

    for step in pipeline.steps:
        for eoi in step.encode_only_inputs:
            if eoi not in step.inputs:
                msg = (
                    f"Step {step.name!r}: encode_only_input {eoi!r} "
                    "is not declared in inputs"
                )
                raise ValueError(msg)
        for port_name, ref_str in step.inputs.items():
            _check_ref(
                pipeline,
                ref_str,
                f"step {step.name!r} input {port_name!r}",
                step_names,
                step_outputs,
            )

    for out_name, ref_str in pipeline.outputs.items():
        _check_ref(
            pipeline,
            ref_str,
            f"pipeline output {out_name!r}",
            step_names,
            step_outputs,
        )


def _check_ref(
    pipeline: Pipeline,
    ref_str: str,
    context: str,
    step_names: set[str],
    step_outputs: dict[str, set[str]],
) -> None:
    """Validate a single wiring reference against pipeline declarations.

    For ``input`` references, confirms the port is declared in
    ``pipeline.inputs``.  For ``constant`` references, confirms the
    name is declared in ``pipeline.constants``.  For step references,
    confirms the step exists and that the referenced port is in
    the step's declared outputs.

    Args:
        pipeline: Pipeline providing inputs and constants for validation.
        ref_str: The raw wiring reference string (e.g.
            ``"input.bytes"`` or ``"zstd_step.bytes"``).
        context: A human-readable label for the reference site,
            used in error messages (e.g.
            ``"step 'foo' input 'bytes'"``).
        step_names: The set of all step names defined in the pipeline.
        step_outputs: Mapping from step name to the set of output
            port names that step declares.

    Raises:
        ValueError: If the reference is unresolvable — the input
            or constant port is not declared, the target step does
            not exist, or the target step does not declare the
            referenced output port.
    """
    ref = WiringRef.parse(ref_str)
    if ref.kind == "input":
        if ref.port not in pipeline.inputs:
            msg = (
                f"{context}: input {ref.port!r} is not declared in pipeline inputs"
                f" (declared: {pipeline.inputs})"
            )
            raise ValueError(msg)
    elif ref.kind == "constant":
        if ref.port not in pipeline.constants:
            msg = (
                f"{context}: constant {ref.port!r} is not declared"
                f" in pipeline constants (declared: {sorted(pipeline.constants)})"
            )
            raise ValueError(msg)
    else:
        if ref.source not in step_names:
            msg = f"{context}: step {ref.source!r} does not exist"
            raise ValueError(msg)
        if ref.port not in step_outputs[ref.source]:
            msg = (
                f"{context}: step {ref.source!r} does not declare output port"
                f" {ref.port!r} (declared: {sorted(step_outputs[ref.source])})"
            )
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
