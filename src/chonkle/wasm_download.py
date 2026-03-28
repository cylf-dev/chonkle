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

    - ``file://`` — yields the path directly; no temp dir created
    - ``https://`` — downloads to a temp dir, yields the path, cleans up on exit
    - ``oci://`` — pulls to a temp dir, yields the path, cleans up on exit
    - ``http://`` — rejected; use HTTPS

    The caller is responsible for installing the yielded file into the codec
    store before the context exits if durable storage is needed.

    Args:
        uri: The codec URI to resolve.

    Yields:
        The absolute path to a local ``.wasm`` file.

    Raises:
        ValueError: If the scheme is ``http://``, unsupported, or absent.
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
    """Download ``url`` to ``dest`` atomically via a temp file in ``dest_dir``.

    Writes to a temporary file first, then replaces ``dest`` on success
    so that a partial download never leaves a corrupt file at the target
    path. The temporary file is removed on any exception.

    Args:
        url: The HTTPS URL to fetch.
        dest: The final destination path for the downloaded file.
        dest_dir: Directory in which to create the temporary file
            during the download.
    """
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
    """Download a ``.wasm`` file from HTTPS into *dest_dir*.

    Signatures are embedded in the ``.wasm`` binary as a
    ``chonkle:signature`` custom section — no sidecar download is needed.

    Args:
        url: The HTTPS URL of the ``.wasm`` file.
        dest_dir: Directory to download into.

    Returns:
        The path to the downloaded ``.wasm`` file inside *dest_dir*.
    """
    filename = PurePosixPath(urllib.parse.urlparse(url).path).name or "module.wasm"
    dest = dest_dir / filename
    _download_url_to(url, dest, dest_dir)
    return dest


def _download_oci(uri: str, dest_dir: Path) -> Path:
    """Pull a ``.wasm`` codec from an OCI registry into *dest_dir*.

    The artifact must contain a ``.wasm`` layer. Signatures are embedded
    in the binary as a ``chonkle:signature`` custom section — no sidecar
    layer is required.

    Args:
        uri: OCI reference with the ``oci://`` scheme prefix, e.g.
            ``oci://ghcr.io/cylf-dev/tiff-predictor-2-c:v0.1.0``.
        dest_dir: Directory to pull into.

    Returns:
        The path to the pulled ``.wasm`` file inside *dest_dir*.

    Raises:
        ValueError: If the OCI artifact contains no ``.wasm`` file.
    """
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
