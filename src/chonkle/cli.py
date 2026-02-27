"""CLI entry point for chonkle."""

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np

from chonkle.pipeline import decode, encode, get_codecs


def main() -> None:
    """Entry point for the chonkle CLI."""
    parser = argparse.ArgumentParser(
        prog="chonkle",
        description="Utilities for encoding and decoding chunks.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── decode ──
    dc_parser = subparsers.add_parser(
        "decode",
        help="Decode a chunk and display or save the result.",
    )
    dc_parser.add_argument(
        "chunk_path",
        type=Path,
        help="Path to an encoded chunk file "
        "(sidecar metadata expected at <path>.json).",
    )
    dc_parser.add_argument(
        "--pipeline",
        type=Path,
        default=None,
        help="Path to a pipeline JSON file. Defaults to <chunk_path>.json sidecar.",
    )
    dc_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Save the decoded array to a .npy file instead of printing.",
    )

    # ── encode ──
    en_parser = subparsers.add_parser(
        "encode",
        help="Encode a .npy array through a codec pipeline.",
    )
    en_parser.add_argument(
        "input",
        type=Path,
        help="Path to a .npy file containing the array to encode.",
    )
    en_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Path for the encoded output. "
        "Pipeline is read from <output>.json sidecar by default.",
    )
    en_parser.add_argument(
        "--pipeline",
        type=Path,
        default=None,
        help="Path to a pipeline JSON file. Defaults to <output>.json sidecar.",
    )

    args = parser.parse_args()

    if args.command == "decode":
        _run_decode(args)
    elif args.command == "encode":
        _run_encode(args)


def _run_decode(args: argparse.Namespace) -> None:
    """Decode a chunk and print or save the result."""
    pipeline_path = args.pipeline or args.chunk_path.parent / (
        args.chunk_path.name + ".json"
    )
    codec_specs = get_codecs(pipeline_path)
    data = args.chunk_path.read_bytes()
    arr = decode(data, codec_specs)

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        np.save(args.output, arr)
        sys.stdout.write(f"Saved {arr.shape} {arr.dtype} array to {args.output}\n")
    else:
        sys.stdout.write(f"Shape: {arr.shape}, dtype: {arr.dtype}\n")
        slices = tuple(slice(min(5, s)) for s in arr.shape)
        sys.stdout.write(f"First 5x5:\n{arr[slices]}\n")


def _run_encode(args: argparse.Namespace) -> None:
    """Encode a .npy array through a codec pipeline."""
    arr = np.load(args.input)

    sidecar = args.output.parent / (args.output.name + ".json")
    pipeline_path = args.pipeline if args.pipeline is not None else sidecar

    codec_specs = get_codecs(pipeline_path)
    encoded = encode(arr, codec_specs)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encoded)

    # Copy pipeline to sidecar if it came from elsewhere.
    if args.pipeline is not None and args.pipeline.resolve() != sidecar.resolve():
        shutil.copy2(args.pipeline, sidecar)

    sys.stdout.write(
        f"Encoded {arr.shape} {arr.dtype} → {len(encoded)} bytes → {args.output}\n"
    )
