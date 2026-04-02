"""Resolve codec URIs to local paths; remote downloads use a temporary directory."""

import logging
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

import oras.client


@contextmanager
def resolve_uri(uri: str) -> Iterator[Path]:
    """Resolve a codec URI, yielding an absolute local file path.

    Dispatches based on URI scheme:

    - file:// — yields the path directly; no temp dir created
    - https:// — downloads to a temp dir, yields the path, cleans up on exit
    - oci:// — pulls to a temp dir, yields the path, cleans up on exit
    - http:// — rejected; use HTTPS

    The caller is responsible for installing the yielded file into the codec
    store before the context exits if durable storage is needed.

    Args:
        uri: The codec URI to resolve.

    Yields:
        The absolute path to a local .wasm file.

    Raises:
        ValueError: If the scheme is http://, unsupported, or absent.
    """
    parsed = urllib.parse.urlparse(uri)

    if parsed.scheme == "file":
        yield Path(parsed.path)
        return

    if parsed.scheme == "http":
        msg = "HTTP is not supported for Wasm downloads; use HTTPS instead"
        raise ValueError(msg)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        if parsed.scheme == "https":
            yield _download_https(uri, tmp_dir)
        elif parsed.scheme == "oci":
            yield _download_oci(uri, tmp_dir)
        else:
            msg = (
                f"Unsupported URI scheme: {parsed.scheme!r}"
                if parsed.scheme
                else f"URI must include a scheme (file://, https://, oci://): {uri!r}"
            )
            raise ValueError(msg)


def _download_url_to(url: str, dest: Path, dest_dir: Path) -> None:
    """Download a URL to dest atomically via a temp file."""
    request = urllib.request.Request(url)  # noqa: S310
    tmp_fd, tmp_path_str = tempfile.mkstemp(dir=dest_dir)
    tmp = Path(tmp_path_str)
    try:
        with urllib.request.urlopen(request) as response, os.fdopen(tmp_fd, "wb") as f:  # noqa: S310
            shutil.copyfileobj(response, f)
        tmp.replace(dest)
    except BaseException:
        tmp.unlink()
        raise


def _download_https(url: str, dest_dir: Path) -> Path:
    """Download a .wasm file from HTTPS into dest_dir."""
    filename = PurePosixPath(urllib.parse.urlparse(url).path).name or "module.wasm"
    dest = dest_dir / filename
    _download_url_to(url, dest, dest_dir)
    return dest


def _download_oci(uri: str, dest_dir: Path) -> Path:
    """Pull a .wasm codec from an OCI registry into dest_dir."""
    ref = uri.removeprefix("oci://")
    client = oras.client.OrasClient()

    # The oras library reads ~/.docker/config.json and tries credential
    # helpers (e.g. docker-credential-desktop) before falling back to
    # anonymous access. For public registries this always fails and logs
    # noisy warnings. Suppress them during the pull.
    oras_logger = logging.getLogger("oras")
    prev_level = oras_logger.level
    oras_logger.setLevel(logging.ERROR)
    try:
        files = client.pull(target=ref, outdir=str(dest_dir))
    finally:
        oras_logger.setLevel(prev_level)

    wasm_files = [Path(f) for f in files if f.endswith(".wasm")]
    if not wasm_files:
        msg = "No .wasm file found in OCI artifact"
        raise ValueError(msg)

    return wasm_files[0]
