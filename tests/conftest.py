"""pytest configuration and shared fixtures for chonkle tests."""

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PIPELINES_DIR = FIXTURES_DIR / "pipelines"


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


@pytest.fixture
def raw_chunk() -> bytes:
    """Raw bytes of known content suitable for zstd round-trip testing.

    Patterned bytes compress and decompress deterministically.
    """
    return bytes(range(256)) * 64


@pytest.fixture
def page_split_input() -> tuple[bytes, int, int]:
    """Input bytes and split offsets for page-split testing.

    Returns (data, rep_length, def_length) such that rep_length + def_length
    is less than len(data), producing three non-empty segments.
    """
    data = bytes(range(256)) * 16  # 4096 bytes
    rep_length = 128
    def_length = 256
    return data, rep_length, def_length


@pytest.fixture
def zstd_pipeline_json() -> dict:
    """Parsed pipeline JSON for the zstd-linear fixture."""
    with (PIPELINES_DIR / "zstd-linear.json").open() as f:
        return json.load(f)


@pytest.fixture
def page_split_pipeline_json() -> dict:
    """Parsed pipeline JSON for the page-split-dag fixture."""
    with (PIPELINES_DIR / "page-split-dag.json").open() as f:
        return json.load(f)
