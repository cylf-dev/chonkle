"""pytest configuration and shared fixtures for chonkle tests."""

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PIPELINES_DIR = FIXTURES_DIR / "pipelines"
CHUNKS_DIR = FIXTURES_DIR / "chunks"

REPO_ROOT = Path(__file__).parent.parent
CODEC_DIR = REPO_ROOT / "codec"


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
    """Raw bytes of known content suitable for codec round-trip testing.

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
def cog_chunk() -> bytes:
    """A real COG tile compressed with zlib (level 9) + tiff-predictor-2.

    1024x1024 uint16 Sentinel-2 band tile. Decodes to 2,097,152 bytes.
    """
    return (CHUNKS_DIR / "cog-chunk-0").read_bytes()


@pytest.fixture
def cog_decode_pipeline_json() -> dict:
    """Pipeline dict for the COG zlib+tiff-predictor-2 decode chain.

    Uses real file:// URIs pointing to the built codec .wasm files.
    """
    with (PIPELINES_DIR / "cog-decode-pipeline.json").open() as f:
        pipeline = json.load(f)
    step_srcs = {
        "zlib": f"file://{CODEC_DIR / 'zlib-rs' / 'zlib.wasm'}",
        "predictor2": (
            f"file://{CODEC_DIR / 'tiff-predictor-2-c' / 'tiff-predictor-2.wasm'}"
        ),
    }
    for step in pipeline["steps"]:
        step["src"] = step_srcs[step["name"]]
    return pipeline


@pytest.fixture
def cog_encode_pipeline_json() -> dict:
    """Pipeline dict for the COG tiff-predictor-2+zlib encode chain.

    Uses real file:// URIs pointing to the built codec .wasm files.
    """
    with (PIPELINES_DIR / "cog-encode-pipeline.json").open() as f:
        pipeline = json.load(f)
    step_srcs = {
        "predictor2": (
            f"file://{CODEC_DIR / 'tiff-predictor-2-c' / 'tiff-predictor-2.wasm'}"
        ),
        "zlib": f"file://{CODEC_DIR / 'zlib-rs' / 'zlib.wasm'}",
    }
    for step in pipeline["steps"]:
        step["src"] = step_srcs[step["name"]]
    return pipeline


@pytest.fixture
def page_split_pipeline_json() -> dict:
    """Parsed pipeline JSON for the page-split-dag fixture."""
    with (PIPELINES_DIR / "page-split-dag.json").open() as f:
        return json.load(f)
