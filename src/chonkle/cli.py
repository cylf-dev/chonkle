"""CLI entry point for chonkle."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from chonkle.executor import run
from chonkle.pipeline import Direction, prepare
from chonkle.resolver import Resolver
from chonkle.wasm_signature import embed_signature


def main() -> None:
    """Entry point for the chonkle CLI."""
    parser = argparse.ArgumentParser(
        prog="chonkle",
        description="Execute a Wasm codec pipeline DAG.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- run command ---
    run_parser = subparsers.add_parser(
        "run",
        help="Execute a pipeline JSON against named input data.",
    )
    run_parser.add_argument(
        "pipeline",
        type=Path,
        help="Path to a pipeline JSON file.",
    )
    run_parser.add_argument(
        "--input",
        metavar="NAME=FILE",
        action="append",
        default=[],
        dest="inputs",
        help="Named input port: --input bytes=chunk.bin (repeatable).",
    )
    run_parser.add_argument(
        "--output",
        metavar="NAME=FILE",
        action="append",
        default=[],
        dest="outputs",
        help="Write named output port to file: --output bytes=result.bin (repeatable).",
    )
    run_parser.add_argument(
        "--direction",
        choices=["encode", "decode"],
        default=None,
        help=(
            "Direction to run the pipeline "
            "(default: the pipeline's declared direction). "
            "Specifying the opposite direction inverts the DAG."
        ),
    )
    run_parser.add_argument(
        "--codec-store",
        type=Path,
        default=None,
        help="Path to the local codec store directory.",
    )
    run_parser.add_argument(
        "--preference",
        default=None,
        help="Comma-separated backend preference order (e.g., 'core,component').",
    )
    run_parser.add_argument(
        "--override",
        metavar="ID=IMPL",
        action="append",
        default=[],
        dest="overrides",
        help="Override codec implementation: --override zlib=zlib-rs (repeatable).",
    )

    # --- codecs command ---
    codecs_parser = subparsers.add_parser(
        "codecs",
        help="List installed codec implementations in the local store.",
    )
    codecs_parser.add_argument(
        "codec_id",
        nargs="?",
        default=None,
        help="Filter to a specific codec_id.",
    )
    codecs_parser.add_argument(
        "--codec-store",
        type=Path,
        default=None,
        help="Path to the local codec store directory.",
    )

    # --- embed-signature command ---
    embed_parser = subparsers.add_parser(
        "embed-signature",
        help="Embed a signature JSON file into a .wasm binary as a custom section.",
    )
    embed_parser.add_argument(
        "wasm_file",
        type=Path,
        help="Path to the .wasm binary (modified in-place).",
    )
    embed_parser.add_argument(
        "signature_json",
        type=Path,
        help="Path to the signature JSON file.",
    )

    args = parser.parse_args()
    if args.command == "run":
        _run_command(args)
    elif args.command == "codecs":
        _codecs_command(args)
    elif args.command == "embed-signature":
        _embed_signature_command(args)


def _build_resolver(
    args: argparse.Namespace, pipeline_sources: dict[str, str]
) -> Resolver:
    """Build a Resolver from CLI flags and pipeline sources."""
    preference = args.preference.split(",") if args.preference else None

    overrides: dict[str, str] = {}
    for spec in args.overrides:
        if "=" not in spec:
            sys.stderr.write(f"--override must be ID=IMPL, got {spec!r}\n")
            sys.exit(1)
        codec_id, impl = spec.split("=", 1)
        overrides[codec_id] = impl

    return Resolver(
        codec_store=args.codec_store,
        preference=preference,
        overrides=overrides,
        pipeline_sources=pipeline_sources,
    )


def _run_command(args: argparse.Namespace) -> None:
    """Execute a pipeline and write or report outputs."""
    with args.pipeline.open() as f:
        pipeline_json: dict[str, Any] = json.load(f)

    inputs: dict[str, bytes] = {}
    for spec in args.inputs:
        if "=" not in spec:
            sys.stderr.write(f"--input must be NAME=FILE, got {spec!r}\n")
            sys.exit(1)
        name, path = spec.split("=", 1)
        inputs[name] = Path(path).read_bytes()

    raw_direction = args.direction or pipeline_json.get("direction")
    if raw_direction not in ("encode", "decode"):
        sys.stderr.write(f"Invalid or missing direction: {raw_direction!r}\n")
        sys.exit(1)
    direction: Direction = raw_direction
    resolver = _build_resolver(args, pipeline_json.get("sources", {}))
    prepared = prepare(pipeline_json, direction, resolver=resolver)
    result = run(prepared, inputs)

    requested_outputs: dict[str, str] = {}
    for spec in args.outputs:
        if "=" not in spec:
            sys.stderr.write(f"--output must be NAME=FILE, got {spec!r}\n")
            sys.exit(1)
        name, path = spec.split("=", 1)
        requested_outputs[name] = path

    for port_name, data in result.items():
        if port_name in requested_outputs:
            out_path = Path(requested_outputs[port_name])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(data)
            sys.stdout.write(f"Wrote {len(data)} bytes to {out_path}\n")
        else:
            sys.stdout.write(f"Output {port_name!r}: {len(data)} bytes\n")


def _codecs_command(args: argparse.Namespace) -> None:
    """List installed codec implementations."""
    resolver = Resolver(codec_store=args.codec_store)
    entries = resolver.list_codecs()

    if args.codec_id:
        entries = [e for e in entries if e.codec_id == args.codec_id]

    if not entries:
        if args.codec_id:
            sys.stdout.write(f"No implementations found for {args.codec_id!r}\n")
        else:
            sys.stdout.write(f"No codecs installed in {resolver.store_path}\n")
        return

    if args.codec_id:
        sys.stdout.write(f"codec_id: {args.codec_id}\nimplementations:\n")
        for entry in entries:
            sys.stdout.write(
                f"  {entry.implementation or '(unnamed)':<20s}"
                f" {entry.codec_type:<12s}"
                f" {entry.path}\n"
            )
    else:
        sys.stdout.write(
            f"{'codec_id':<24s} {'implementation':<20s} {'backend':<12s}\n"
        )
        for entry in entries:
            sys.stdout.write(
                f"{entry.codec_id:<24s} "
                f"{entry.implementation or '(unnamed)':<20s} "
                f"{entry.codec_type:<12s}\n"
            )


def _embed_signature_command(args: argparse.Namespace) -> None:
    """Embed a codec signature into a .wasm binary."""
    wasm_bytes = args.wasm_file.read_bytes()
    with args.signature_json.open() as f:
        signature = json.load(f)

    result = embed_signature(wasm_bytes, signature)
    args.wasm_file.write_bytes(result)

    added = len(result) - len(wasm_bytes)
    sys.stdout.write(f"Embedded {added} byte custom section in {args.wasm_file}\n")
