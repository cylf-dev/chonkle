"""Download COG files from public HTTPS URLs."""

import shutil
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath


def download_cog(url: str, output: Path | None = None) -> None:
    """Download a COG file from a public HTTPS URL."""
    if output is None:
        filename = PurePosixPath(urllib.parse.urlparse(url).path).name
        if not filename:
            msg = f"Cannot extract filename from URL: {url}"
            raise ValueError(msg)
        output = Path(filename)

    output.parent.mkdir(parents=True, exist_ok=True)

    request = urllib.request.Request(url)  # noqa: S310
    with urllib.request.urlopen(request) as response, output.open("wb") as out_file:  # noqa: S310
        shutil.copyfileobj(response, out_file)
