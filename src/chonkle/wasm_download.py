"""Download and cache Wasm codec components; resolve codec URIs to local paths."""

import hashlib
import logging
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

import oras.client


def resolve_uri(uri: str, *, force_download: bool = False) -> Path:
    """Resolve a codec URI to an absolute local file path.

    Dispatches to the appropriate handler based on the URI scheme:

    - ``file://`` — returns the path directly, no download
    - ``https://`` — downloads via ``download_https()``
    - ``oci://`` — pulls via ``download_oci()``
    - ``http://`` — rejected; use HTTPS

    Args:
        uri: The codec URI to resolve.
        force_download: If True, bypass the local cache and
            re-download even when a cached copy exists. The
            ``CHONKLE_FORCE_DOWNLOAD=1`` environment variable
            has the same effect.

    Returns:
        The absolute path to the local ``.wasm`` file.

    Raises:
        ValueError: If the scheme is ``http://``, unsupported,
            or absent.
    """
    parsed = urllib.parse.urlparse(uri)

    if parsed.scheme == "file":
        return Path(parsed.path)

    if parsed.scheme == "http":
        msg = "HTTP is not supported for Wasm downloads; use HTTPS instead"
        raise ValueError(msg)

    if parsed.scheme == "https":
        return download_https(uri, force=force_download)

    if parsed.scheme == "oci":
        return download_oci(uri, force=force_download)

    msg = (
        f"Unsupported URI scheme: {parsed.scheme!r}"
        if parsed.scheme
        else f"URI must include a scheme (file://, https://, oci://): {uri!r}"
    )
    raise ValueError(msg)


def get_cache_dir() -> Path:
    """Return the directory used to cache downloaded ``.wasm`` files.

    Uses ``CHONKLE_CACHE_DIR`` if set, otherwise falls back to a
    ``chonkle/wasm`` subdirectory inside the OS temporary directory.
    The directory is created if it does not already exist.

    Returns:
        An existing directory Path suitable for writing cached files.
    """
    override = os.environ.get("CHONKLE_CACHE_DIR", "")
    if override:
        base = Path(override)
    else:
        base = Path(tempfile.gettempdir()) / "chonkle" / "wasm"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _should_force() -> bool:
    """Return True if the ``CHONKLE_FORCE_DOWNLOAD`` env var is set to ``"1"``."""
    return os.environ.get("CHONKLE_FORCE_DOWNLOAD", "") == "1"


def _derive_manifest_url(wasm_url: str) -> str:
    """Derive the ``.manifest.json`` sidecar URL from a ``.wasm`` HTTPS URL.

    Replaces the filename in the URL path with ``{stem}.manifest.json``,
    preserving the scheme, host, and all other path components. For
    example::

        https://example.com/codecs/zstd.wasm
        → https://example.com/codecs/zstd.manifest.json

    Args:
        wasm_url: The HTTPS URL of the ``.wasm`` file.

    Returns:
        The HTTPS URL of the corresponding ``.manifest.json`` file.
    """
    parsed = urllib.parse.urlparse(wasm_url)
    path = PurePosixPath(parsed.path)
    manifest_name = path.stem + ".manifest.json"
    manifest_path = str(path.parent / manifest_name)
    return urllib.parse.urlunparse(parsed._replace(path=manifest_path))


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


def download_https(
    url: str, *, cache_dir: Path | None = None, force: bool = False
) -> Path:
    """Download a ``.wasm`` file and its ``.manifest.json`` sidecar from HTTPS.

    Both files are stored under a cache subdirectory keyed by the
    SHA-256 hash of the ``.wasm`` URL. If both are already present and
    neither ``force`` nor ``CHONKLE_FORCE_DOWNLOAD=1`` is active, the
    cached path is returned without re-downloading.

    Args:
        url: The HTTPS URL of the ``.wasm`` file. The manifest URL is
            derived by replacing ``.wasm`` with ``.manifest.json`` in
            the path component.
        cache_dir: Root directory for the cache. Defaults to the
            result of ``get_cache_dir()``.
        force: If True, re-download even when a cached copy exists.

    Returns:
        The local path to the cached ``.wasm`` file.
    """
    if cache_dir is None:
        cache_dir = get_cache_dir()

    url_hash = hashlib.sha256(url.encode()).hexdigest()
    filename = PurePosixPath(urllib.parse.urlparse(url).path).name or "module.wasm"
    dest_dir = cache_dir / "https" / url_hash
    dest = dest_dir / filename

    manifest_url = _derive_manifest_url(url)
    manifest_filename = PurePosixPath(urllib.parse.urlparse(manifest_url).path).name
    manifest_dest = dest_dir / manifest_filename

    already_cached = dest.exists() and manifest_dest.exists()
    if already_cached and not force and not _should_force():
        return dest

    dest_dir.mkdir(parents=True, exist_ok=True)
    _download_url_to(url, dest, dest_dir)
    _download_url_to(manifest_url, manifest_dest, dest_dir)

    return dest


def download_oci(
    uri: str, *, cache_dir: Path | None = None, force: bool = False
) -> Path:
    """Pull a ``.wasm`` codec and its ``.manifest.json`` sidecar from an OCI registry.

    The artifact must contain both a ``.wasm`` layer and a
    ``.manifest.json`` layer. Pull output is cached under a directory
    derived from the OCI reference. If the cache directory already
    contains both file types and neither ``force`` nor
    ``CHONKLE_FORCE_DOWNLOAD=1`` is active, the cached path is returned
    without re-pulling.

    Args:
        uri: OCI reference with the ``oci://`` scheme prefix, e.g.
            ``oci://ghcr.io/cylf-dev/tiff-predictor-2-c:v0.1.0``.
        cache_dir: Root directory for the cache. Defaults to the
            result of ``get_cache_dir()``.
        force: If True, re-pull even when a cached copy exists.

    Returns:
        The local path to the pulled ``.wasm`` file.

    Raises:
        ValueError: If the OCI artifact contains no ``.wasm`` file
            or no ``.manifest.json`` sidecar.
    """
    if cache_dir is None:
        cache_dir = get_cache_dir()

    ref = uri.removeprefix("oci://")
    ref_dir = cache_dir / "oci" / ref.replace(":", "/")

    if not force and not _should_force() and ref_dir.exists():
        wasm_files = list(ref_dir.glob("*.wasm"))
        manifest_files = list(ref_dir.glob("*.manifest.json"))
        if wasm_files and manifest_files:
            return wasm_files[0]

    ref_dir.mkdir(parents=True, exist_ok=True)
    client = oras.client.OrasClient()

    # The oras library reads ~/.docker/config.json and tries credential
    # helpers (e.g. docker-credential-desktop) before falling back to
    # anonymous access. For public registries this always fails and logs
    # noisy warnings. Suppress them during the pull.
    oras_logger = logging.getLogger("oras")
    prev_level = oras_logger.level
    oras_logger.setLevel(logging.ERROR)
    try:
        files = client.pull(target=ref, outdir=str(ref_dir))
    finally:
        oras_logger.setLevel(prev_level)

    wasm_files = [Path(f) for f in files if f.endswith(".wasm")]
    if not wasm_files:
        msg = "No .wasm file found in OCI artifact"
        raise ValueError(msg)

    manifest_files = [Path(f) for f in files if f.endswith(".manifest.json")]
    if not manifest_files:
        msg = "No .manifest.json sidecar found in OCI artifact"
        raise ValueError(msg)

    return wasm_files[0]
