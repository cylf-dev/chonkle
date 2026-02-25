from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "chunks"

CHUNK_PATHS = [
    FIXTURES_DIR / "cog" / "0",
    FIXTURES_DIR / "zarr_zlib" / "0",
    FIXTURES_DIR / "zarr_zstd" / "0",
]
