import shutil
from pathlib import Path

import pytest

from chonkle.wasm_download import download_https

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "chunks"

_COG_WASM_DIR = FIXTURES_DIR / "cog_wasm"

# Registry of .wasm fixtures not committed to git.
# To add a new fixture: add an entry here, add a named fixture below,
# and add a CI cache step in .github/workflows/ci.yml keyed on the version tag.
_WASM_DOWNLOADS: dict[str, str] = {
    "tiff-predictor-2-c.wasm": (
        "https://github.com/cylf-dev/tiff-predictor-2-c"
        "/releases/download/v0.2.0/tiff-predictor-2-c.wasm"
    ),
    "tiff-predictor-2-python.wasm": (
        "https://github.com/cylf-dev/tiff-predictor-2-python"
        "/releases/download/v0.1.0/tiff-predictor-2-python.wasm"
    ),
}


def _ensure_wasm_fixture(filename: str) -> Path:
    dest = _COG_WASM_DIR / filename
    if not dest.exists():
        shutil.copy2(download_https(_WASM_DOWNLOADS[filename]), dest)
    return dest


@pytest.fixture(scope="session")
def ensure_core_wasm() -> Path:
    return _ensure_wasm_fixture("tiff-predictor-2-c.wasm")


@pytest.fixture(scope="session")
def ensure_component_wasm() -> Path:
    return _ensure_wasm_fixture("tiff-predictor-2-python.wasm")


CHUNK_PATHS = [
    FIXTURES_DIR / "cog" / "0",
    FIXTURES_DIR / "zarr_zlib" / "0",
    FIXTURES_DIR / "zarr_zstd" / "0",
]


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-network",
        action="store_true",
        default=False,
        help="run tests that require network access",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("--run-network"):
        return
    skip_network = pytest.mark.skip(reason="needs --run-network option to run")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip_network)
