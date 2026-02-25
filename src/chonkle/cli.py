"""CLI entry point for chonkle."""

import argparse
import sys
from pathlib import Path


def main() -> None:
    """Entry point for the chonkle CLI."""
    parser = argparse.ArgumentParser(
        prog="chonkle",
        description="Utilities for accessing and decoding chunks.",
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
        help="Decode a chunk and print a 5x5 excerpt.",
    )
    dc_parser.add_argument(
        "chunk_path",
        type=Path,
        help="Path to an encoded chunk file "
        "(sidecar metadata expected at <path>.json).",
    )

    args = parser.parse_args()

    if args.command == "cog":
        if args.cog_command == "download":
            from chonkle.cog.download import download_cog

            download_cog(args.url, args.output)
        elif args.cog_command == "to-zarr":
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
    elif args.command == "decode":
        from chonkle.decode.pipeline import decode_chunk

        arr = decode_chunk(args.chunk_path)
        sys.stdout.write(f"Shape: {arr.shape}, dtype: {arr.dtype}\n")
        slices = tuple(slice(min(5, s)) for s in arr.shape)
        sys.stdout.write(f"First 5x5:\n{arr[slices]}\n")
