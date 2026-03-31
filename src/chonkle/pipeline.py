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
    from chonkle.wasm_signature import PortDescriptor, Signature

Direction = Literal["encode", "decode"]


@dataclass(frozen=True)
class InputDescriptor:
    """Descriptor for a pipeline-level input port."""

    type: str = ""
    encode_only: bool = False
    decode_only: bool = False


@dataclass(frozen=True)
class ConstantDescriptor:
    """Descriptor for a pipeline-level constant."""

    type: str = ""
    value: Any = None


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
        "input.<port>", "constant.<port>", and "<step_name>.<port>".

        Args:
            ref_str: A dot-separated wiring reference of the form
                <source>.<port>. The source is either the literal
                string "input", the literal string "constant",
                or the name of a pipeline step.

        Returns:
            A WiringRef with kind set to "input", "constant", or "step"
            depending on the source token.

        Raises:
            ValueError: If ref_str does not contain a dot separator.
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

    def __str__(self) -> str:
        """Reconstruct the original dot-notation wiring reference string."""
        return f"{self.source}.{self.port}"


@dataclass
class StepSpec:
    """Specification for a single pipeline step."""

    name: str  # unique DAG node identifier; used in wiring references
    codec_id: str  # logical codec identifier; may repeat across steps
    inputs: dict[str, WiringRef]  # port_name -> parsed wiring reference


@dataclass
class Pipeline:
    """A parsed and validated DAG pipeline."""

    codec_id: str
    direction: Direction
    inputs: dict[str, InputDescriptor]
    constants: dict[str, ConstantDescriptor]
    outputs: dict[str, WiringRef]  # pipeline_output_name -> parsed wiring ref
    sources: dict[str, str]  # codec_id -> URI (advisory fetch hints)
    steps: dict[str, StepSpec]  # keys in topological execution order

    @classmethod
    def parse(cls, pipeline: Path | dict[str, Any]) -> Pipeline:
        """Parse a pipeline JSON document into a validated Pipeline.

        Reads and deserializes the JSON if pipeline is a Path, then
        constructs a Pipeline, runs wiring validation, and computes a
        topological execution order for the steps.

        Args:
            pipeline: Either a Path to a JSON file on disk or a
                pre-parsed dict representing the pipeline document.

        Returns:
            A fully validated Pipeline with steps ordered topologically.

        Raises:
            ValueError: If direction is missing or not "encode"/"decode",
                if codec_id is not a string, if any wiring reference is
                unresolvable or references a non-existent step, or if the
                step dependency graph contains a cycle.
        """
        if isinstance(pipeline, Path):
            with pipeline.open() as f:
                data: dict[str, Any] = json.load(f)
        else:
            data = pipeline

        direction = data.get("direction")
        if direction not in ("encode", "decode"):
            msg = f"'direction' must be 'encode' or 'decode', got {direction!r}"
            raise ValueError(msg)

        codec_id = data.get("codec_id")
        if not isinstance(codec_id, str):
            msg = f"'codec_id' must be a string, got {codec_id!r}"
            raise ValueError(msg)
        inputs = {
            k: InputDescriptor(
                type=v.get("type", ""),
                encode_only=v.get("encode_only", False),
                decode_only=v.get("decode_only", False),
            )
            for k, v in data.get("inputs", {}).items()
        }
        constants = {
            k: ConstantDescriptor(type=v.get("type", ""), value=v.get("value"))
            for k, v in data.get("constants", {}).items()
        }
        raw_outputs: dict[str, str] = dict(data.get("outputs", {}))
        sources = dict(data.get("sources", {}))

        steps: dict[str, StepSpec] = {}
        for step_name, step_data in data.get("steps", {}).items():
            steps[step_name] = StepSpec(
                name=step_name,
                codec_id=step_data["codec_id"],
                inputs={
                    k: WiringRef.parse(v)
                    for k, v in step_data.get("inputs", {}).items()
                },
            )

        outputs = {k: WiringRef.parse(v) for k, v in raw_outputs.items()}

        _validate_wiring(steps, inputs, constants, outputs)
        topo_order = _topological_sort(steps)
        steps = {name: steps[name] for name in topo_order}

        return cls(
            codec_id=codec_id,
            direction=direction,
            inputs=inputs,
            constants=constants,
            outputs=outputs,
            sources=sources,
            steps=steps,
        )


@dataclass
class PreparedPipeline:
    """A pipeline that has been validated and is ready for execution.

    Created by prepare(). Pass to run() to execute.
    """

    pipeline: Pipeline
    direction: Direction
    codecs: Mapping[str, Codec]
    encode_only_inputs: Mapping[str, frozenset[str]]  # step_name -> encode_only ports
    decode_only_inputs: Mapping[str, frozenset[str]]  # step_name -> decode_only ports
    output_ports: Mapping[str, tuple[str, ...]]  # step_name -> output port names


def prepare(
    pipeline: Path | dict[str, Any],
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
        pipeline: Either a Path to a pipeline JSON file or a pre-parsed
            dict representing the pipeline document.
        direction: Direction to execute ("encode" or "decode").
        resolver: Codec resolver. If None, a default resolver is
            created using the pipeline's sources field.

    Returns:
        A PreparedPipeline ready for executor.run().

    Raises:
        ValueError: Parsing fails, signature validation fails, a codec
            cannot be resolved, or wiring references invalid output ports.
    """
    from chonkle.resolver import Resolver

    parsed = Pipeline.parse(pipeline)

    if resolver is None:
        resolver = Resolver(pipeline_sources=parsed.sources)

    codecs: dict[str, Codec] = {}
    for step_name, step in parsed.steps.items():
        codecs[step_name] = resolver.resolve(step.codec_id)

    _validate_codec_signatures(parsed, codecs, direction)

    encode_only_inputs: dict[str, frozenset[str]] = {}
    decode_only_inputs: dict[str, frozenset[str]] = {}
    output_ports: dict[str, tuple[str, ...]] = {}
    for step_name, codec in codecs.items():
        sig = codec.signature()
        encode_only_inputs[step_name] = frozenset(sig.encode_only_inputs())
        decode_only_inputs[step_name] = frozenset(sig.decode_only_inputs())
        output_ports[step_name] = tuple(sig.outputs.keys())

    return PreparedPipeline(
        pipeline=parsed,
        direction=direction,
        codecs=codecs,
        encode_only_inputs=encode_only_inputs,
        decode_only_inputs=decode_only_inputs,
        output_ports=output_ports,
    )


def _validate_wiring(
    steps: dict[str, StepSpec],
    inputs: dict[str, InputDescriptor],
    constants: dict[str, ConstantDescriptor],
    outputs: dict[str, WiringRef],
) -> None:
    """Validate step declarations and all wiring references."""
    step_names = set(steps.keys())

    for step in steps.values():
        for port_name, ref in step.inputs.items():
            _validate_wiring_ref(
                inputs,
                constants,
                ref,
                f"step {step.name!r} input {port_name!r}",
                step_names,
            )

    for out_name, ref in outputs.items():
        _validate_wiring_ref(
            inputs,
            constants,
            ref,
            f"pipeline output {out_name!r}",
            step_names,
        )


def _validate_wiring_ref(
    inputs: dict[str, InputDescriptor],
    constants: dict[str, ConstantDescriptor],
    ref: WiringRef,
    context: str,
    step_names: set[str],
) -> None:
    """Validate a single wiring reference against pipeline declarations."""
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


def _topological_sort(steps: dict[str, StepSpec]) -> list[str]:
    """Return step names in a valid execution order using Kahn's algorithm."""
    in_degree: dict[str, int] = dict.fromkeys(steps, 0)
    dependents: dict[str, list[str]] = defaultdict(list)

    for step_name, step in steps.items():
        deps: set[str] = set()
        for ref in step.inputs.values():
            if ref.kind == "step":
                deps.add(ref.source)
        for dep in deps:
            dependents[dep].append(step_name)
            in_degree[step_name] += 1

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


@dataclass
class _ValidationContext:
    """Accumulated cross-step state for signature validation."""

    pipeline: Pipeline
    direction: Direction
    sig_outputs: dict[str, set[str]]
    step_output_types: dict[str, dict[str, str]]


def _validate_codec_signatures(
    pipeline: Pipeline,
    codecs: Mapping[str, Codec],
    direction: Direction,
) -> None:
    """Validate all step signatures and collect errors into a single ValueError."""
    errors: list[str] = []
    ctx = _ValidationContext(
        pipeline=pipeline,
        direction=direction,
        sig_outputs={},
        step_output_types={},
    )
    for step_name, step in pipeline.steps.items():
        sig = codecs[step_name].signature()
        ctx.sig_outputs[step_name] = set(sig.outputs.keys())
        ctx.step_output_types[step_name] = sig.output_types()
        try:
            _validate_step_signature(step, sig, ctx)
        except ValueError as exc:
            errors.append(str(exc))

    for out_name, ref in pipeline.outputs.items():
        if ref.kind == "step":
            available = ctx.sig_outputs.get(ref.source, set())
            if ref.port not in available:
                errors.append(
                    f"Pipeline output {out_name!r}: "
                    f"step {ref.source!r} does not declare output port "
                    f"{ref.port!r} (signature outputs: {sorted(available)})"
                )

    if errors:
        raise ValueError("Pipeline signature validation failed:\n" + "\n".join(errors))


def _validate_step_signature(
    step: StepSpec,
    signature: Signature,
    ctx: _ValidationContext,
) -> None:
    """Verify a step's wiring and port declarations against its codec signature."""
    errors: list[str] = []

    for port_name, ref in step.inputs.items():
        if ref.kind == "step":
            available = ctx.sig_outputs.get(ref.source, set())
            if ref.port not in available:
                errors.append(
                    f"Step {step.name!r} input {port_name!r}: "
                    f"step {ref.source!r} does not declare output port "
                    f"{ref.port!r} (signature outputs: {sorted(available)})"
                )

    sig_inputs = signature.inputs
    if sig_inputs:
        errors.extend(
            f"input port {p!r} is missing required 'type' field"
            for p, d in sig_inputs.items()
            if not d.type
        )

        encode_only_ports = signature.encode_only_inputs()
        decode_only_ports = signature.decode_only_inputs()

        if ctx.direction == "decode":
            valid_inputs = {
                name for name, desc in sig_inputs.items() if not desc.encode_only
            }
        elif ctx.direction == "encode":
            valid_inputs = {
                name for name, desc in sig_inputs.items() if not desc.decode_only
            }
        else:
            valid_inputs = set(sig_inputs.keys())

        active_inputs = set(step.inputs.keys()) - encode_only_ports - decode_only_ports
        unknown = active_inputs - valid_inputs
        if unknown:
            errors.append(
                f"inputs {sorted(unknown)} are not valid signature "
                f"{ctx.direction} inputs {sorted(valid_inputs)}"
            )

        errors.extend(_check_input_types(step, sig_inputs, active_inputs, ctx))

    output_ports = signature.outputs
    if output_ports:
        errors.extend(
            f"output port {p!r} is missing required 'type' field"
            for p, d in output_ports.items()
            if not d.type
        )

    if errors:
        joined = "; ".join(errors)
        msg = f"Step {step.name!r}: {joined}"
        raise ValueError(msg)


def _check_input_types(
    step: StepSpec,
    sig_inputs: dict[str, PortDescriptor],
    active_inputs: set[str],
    ctx: _ValidationContext,
) -> list[str]:
    """Return type-mismatch error strings for each active wired input."""
    errors: list[str] = []
    for port_name in active_inputs:
        if port_name not in sig_inputs:
            continue
        expected_type = sig_inputs[port_name].type
        if not expected_type:
            continue
        ref = step.inputs[port_name]
        if ref.kind == "input":
            desc = ctx.pipeline.inputs.get(ref.port)
            actual_type = desc.type if desc and desc.type else None
        elif ref.kind == "constant":
            cdesc = ctx.pipeline.constants.get(ref.port)
            actual_type = cdesc.type if cdesc and cdesc.type else None
        else:
            actual_type = ctx.step_output_types.get(ref.source, {}).get(ref.port)
        if actual_type is not None and actual_type != expected_type:
            errors.append(
                f"input {port_name!r}: wiring {str(ref)!r} provides type"
                f" {actual_type!r} but codec expects {expected_type!r}"
            )
    return errors
