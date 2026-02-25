"""Convert COG files to Zarr format."""

from pathlib import Path

import rioxarray
import xarray as xr


def cog_to_zarr(
    input_path: Path,
    output_path: Path,
    codec: str | None = None,
    level: int | None = None,
    chunks: list[int] | None = None,
) -> None:
    """Convert a local COG file to Zarr v3 format."""
    ds = rioxarray.open_rasterio(input_path, band_as_variable=True)
    if not isinstance(ds, xr.Dataset):
        msg = f"Expected a Dataset, got {type(ds)}"
        raise TypeError(msg)

    encoding: dict | None = None
    var_enc: dict = {}
    if codec is not None:
        var_enc["compressors"] = _make_compressor(codec, level)
    if chunks is not None:
        var_enc["chunks"] = chunks
    if var_enc:
        encoding = dict.fromkeys(ds.data_vars, var_enc)

    ds.to_zarr(output_path, zarr_format=3, encoding=encoding)
    ds.close()


def _make_compressor(codec: str, level: int | None) -> list | None:
    """Build a compressor list for zarr encoding."""
    if codec == "none":
        return None

    if codec == "zlib":
        from zarr.codecs.numcodecs._codecs import Zlib

        return [Zlib(level=level if level is not None else 5)]

    if codec == "zstd":
        from zarr.codecs import ZstdCodec

        return [ZstdCodec(level=level if level is not None else 0)]

    msg = f"Unknown codec: {codec}"
    raise ValueError(msg)
