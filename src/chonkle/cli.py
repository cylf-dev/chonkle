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
        description="Utilities for encoding, decoding, and inspecting chunks.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── cog (parent for COG subcommands) ──
    cog_parser = subparsers.add_parser(
        "cog",
        help="COG data operations.",
    )
    cog_subparsers = cog_parser.add_subparsers(
        dest="cog_command",
        required=True,
    )

    # -- cog download --
    dl_parser = cog_subparsers.add_parser(
        "download",
        help="Download a COG file from a public HTTPS URL.",
    )
    dl_parser.add_argument(
        "url",
        help="Public HTTPS URL of the COG file.",
    )
    dl_parser.add_argument(
        "output",
        nargs="?",
        default=None,
        type=Path,
        help="Local file path to save the downloaded COG. "
        "Defaults to ./<filename from URL>.",
    )

    # -- cog to-zarr --
    cz_parser = cog_subparsers.add_parser(
        "to-zarr",
        help="Convert a local COG file to Zarr v3 format.",
    )
    cz_parser.add_argument(
        "input",
        type=Path,
        help="Path to the input COG file.",
    )
    cz_parser.add_argument(
        "output",
        type=Path,
        help="Path for the output Zarr store directory.",
    )
    cz_parser.add_argument(
        "--codec",
        choices=["zstd", "zlib", "none"],
        default=None,
        help="Compression codec for the data variable.",
    )
    cz_parser.add_argument(
        "--level",
        type=int,
        default=None,
        help="Compression level (codec-specific).",
    )
    cz_parser.add_argument(
        "--chunks",
        type=int,
        nargs="+",
        default=None,
        help="Chunk dimensions, one per spatial axis "
        "(e.g. --chunks 1024 1024 for y/x).",
    )

    # -- cog metadata --
    cm_parser = cog_subparsers.add_parser(
        "metadata",
        help="Print TIFF metadata for the first page of a COG file.",
    )
    cm_parser.add_argument(
        "input",
        type=Path,
        help="Path to the local COG file.",
    )

    # -- cog extract-tile --
    et_parser = cog_subparsers.add_parser(
        "extract-tile",
        help="Extract raw bytes of a single tile from a COG.",
    )
    et_parser.add_argument(
        "input",
        type=Path,
        help="Path to the local COG file.",
    )
    et_parser.add_argument(
        "tile_index",
        type=int,
        help="Zero-based index of the tile to extract.",
    )
    et_parser.add_argument(
        "output",
        nargs="?",
        default=None,
        type=Path,
        help="Output file path. Defaults to <tile_index> next to the COG file.",
    )

    # ── decode (direct subcommand) ──
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

    # ── encode (direct subcommand) ──
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

    if args.command == "cog":
        _run_cog(args)
    elif args.command == "decode":
        _run_decode(args)
    elif args.command == "encode":
        _run_encode(args)


def _run_cog(args: argparse.Namespace) -> None:
    """Dispatch COG subcommands."""
    try:
        if args.cog_command == "download":
            from chonkle.cog.download import download_cog

            download_cog(args.url, args.output)
        elif args.cog_command == "to-zarr":
            if args.level is not None and args.codec is None:
                sys.stderr.write("Warning: --level has no effect without --codec\n")
            from chonkle.cog.convert import cog_to_zarr

            cog_to_zarr(
                args.input,
                args.output,
                args.codec,
                args.level,
                args.chunks,
            )
        elif args.cog_command == "metadata":
            from chonkle.cog.inspect import cog_metadata

            for name, value in cog_metadata(args.input):
                sys.stdout.write(f"{name}: {value}\n")
        elif args.cog_command == "extract-tile":
            from chonkle.cog.inspect import extract_tile

            extract_tile(args.input, args.tile_index, args.output)
    except ImportError:
        sys.stderr.write(
            "The 'cog' commands require extra dependencies.\n"
            "Install them with: uv sync --extra cog\n"
        )
        sys.exit(1)


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
