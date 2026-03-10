"""CLI entry point for chonkle."""

import argparse
import sys
from pathlib import Path

from chonkle.executor import run
from chonkle.pipeline import Pipeline


def main() -> None:
    """Entry point for the chonkle CLI."""
    parser = argparse.ArgumentParser(
        prog="chonkle",
        description="Execute a Wasm Component Model codec pipeline DAG.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

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
        "--force-download",
        action="store_true",
        default=False,
        help="Re-download cached codec .wasm files even if they exist locally.",
    )

    args = parser.parse_args()
    if args.command == "run":
        _run_command(args)


def _run_command(args: argparse.Namespace) -> None:
    """Execute a pipeline and write or report outputs."""
    pipeline = Pipeline.parse(args.pipeline)

    inputs: dict[str, bytes] = {}
    for spec in args.inputs:
        if "=" not in spec:
            sys.stderr.write(f"--input must be NAME=FILE, got {spec!r}\n")
            sys.exit(1)
        name, path = spec.split("=", 1)
        inputs[name] = Path(path).read_bytes()

    result = run(pipeline, inputs, force_download=args.force_download)

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
