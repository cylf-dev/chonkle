"""Inspect and extract data from COG files."""

from pathlib import Path

import tifffile


def cog_metadata(input_path: Path) -> list[tuple[str, object]]:
    """Return TIFF tags for the first page of a COG file."""
    with tifffile.TiffFile(input_path) as tif:
        page = tif.pages.first
        return [(tag.name, tag.value) for tag in page.tags.values()]


def extract_tile(
    input_path: Path,
    tile_index: int,
    output_path: Path | None = None,
) -> None:
    """Extract raw bytes of a single tile from the first page of a COG."""
    if output_path is None:
        output_path = input_path.parent / str(tile_index)

    with tifffile.TiffFile(input_path) as tif:
        page = tif.pages.first

        offsets = page.tags["TileOffsets"].value
        byte_counts = page.tags["TileByteCounts"].value

        if tile_index < 0 or tile_index >= len(offsets):
            msg = f"Tile index {tile_index} out of range [0, {len(offsets) - 1}]"
            raise IndexError(msg)

        offset = offsets[tile_index]
        count = byte_counts[tile_index]

    with input_path.open("rb") as f:
        f.seek(offset)
        raw_bytes = f.read(count)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as out:
        out.write(raw_bytes)
