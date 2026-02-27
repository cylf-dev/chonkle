from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "chunks"

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
