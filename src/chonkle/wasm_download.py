"""Download WASM codec modules from HTTPS URLs and OCI registries."""

import hashlib
import logging
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

import oras.client


def get_cache_dir() -> Path:
    """Return the directory for cached .wasm downloads.

    Uses CHONKLE_CACHE_DIR if set, otherwise falls back to a
    chonkle/wasm subdirectory inside the OS temporary directory.
    """
    override = os.environ.get("CHONKLE_CACHE_DIR", "")
    if override:
        base = Path(override)
    else:
        base = Path(tempfile.gettempdir()) / "chonkle" / "wasm"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _should_force() -> bool:
    """Check whether CHONKLE_FORCE_DOWNLOAD is set."""
    return os.environ.get("CHONKLE_FORCE_DOWNLOAD", "") == "1"


def download_https(
    url: str, *, cache_dir: Path | None = None, force: bool = False
) -> Path:
    """Download a .wasm file from an HTTPS URL, returning the cached path.

    The file is cached by a SHA-256 hash of the URL. Pass 'force' (or set
    CHONKLE_FORCE_DOWNLOAD=1) to re-download even when a cached copy
    exists.
    """
    if cache_dir is None:
        cache_dir = get_cache_dir()

    url_hash = hashlib.sha256(url.encode()).hexdigest()
    filename = PurePosixPath(urllib.parse.urlparse(url).path).name or "module.wasm"
    dest_dir = cache_dir / "https" / url_hash
    dest = dest_dir / filename

    if dest.exists() and not force and not _should_force():
        return dest

    dest_dir.mkdir(parents=True, exist_ok=True)
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

    return dest


def download_oci(
    uri: str, *, cache_dir: Path | None = None, force: bool = False
) -> Path:
    """Download a .wasm file from an OCI registry, returning the cached path.

    'uri' should include the oci:// scheme prefix, e.g.
    oci://ghcr.io/cylf-dev/tiff-predictor-2-c:v0.1.0.

    The file is cached by the OCI reference. Pass 'force' (or set
    CHONKLE_FORCE_DOWNLOAD=1) to re-download even when a cached copy
    exists.
    """
    if cache_dir is None:
        cache_dir = get_cache_dir()

    ref = uri.removeprefix("oci://")
    ref_dir = cache_dir / "oci" / ref.replace(":", "/")

    if not force and not _should_force() and ref_dir.exists():
        wasm_files = list(ref_dir.glob("*.wasm"))
        if wasm_files:
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

    return wasm_files[0]
