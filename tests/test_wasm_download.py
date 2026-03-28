import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from chonkle.wasm_download import resolve_uri

URLOPEN = "chonkle.wasm_download.urllib.request.urlopen"
ORAS_CLIENT = "oras.client.OrasClient"


def _fake_response(data: bytes) -> MagicMock:
    """Build a MagicMock that acts as a urllib response context manager."""
    resp = MagicMock()
    resp.__enter__ = lambda s: io.BytesIO(data)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestResolveUriFile:
    def test_file_uri_yields_path(self, tmp_path: Path) -> None:
        wasm = tmp_path / "codec.wasm"
        wasm.write_bytes(b"\x00asm\x01\x00\x00\x00")
        with resolve_uri(f"file://{wasm}") as path:
            assert path == wasm

    def test_http_raises(self) -> None:
        with (
            pytest.raises(ValueError, match="HTTP is not supported"),
            resolve_uri("http://example.com/codec.wasm"),
        ):
            pass

    def test_unsupported_scheme_raises(self) -> None:
        with (
            pytest.raises(ValueError, match="Unsupported URI scheme"),
            resolve_uri("ftp://example.com/codec.wasm"),
        ):
            pass


class TestDownloadHttps:
    def test_downloads_wasm(self) -> None:
        wasm_bytes = b"\x00asm\x01\x00\x00\x00"
        resp = _fake_response(wasm_bytes)

        with (
            patch(URLOPEN, return_value=resp),
            resolve_uri("https://example.com/releases/v1/codec.wasm") as path,
        ):
            assert path.exists()
            assert path.name == "codec.wasm"
            assert path.read_bytes() == wasm_bytes

    def test_temp_dir_cleaned_up_after_exit(self) -> None:
        wasm_bytes = b"\x00asm\x01\x00\x00\x00"
        resp = _fake_response(wasm_bytes)
        captured: list[Path] = []

        with (
            patch(URLOPEN, return_value=resp),
            resolve_uri("https://example.com/releases/v1/codec.wasm") as path,
        ):
            captured.append(path)

        assert not captured[0].exists()


class TestDownloadOci:
    def test_full_flow(self) -> None:
        wasm_bytes = b"\x00asm\x01\x00\x00\x00"

        def fake_pull(*, target, outdir):
            Path(outdir).mkdir(parents=True, exist_ok=True)
            (Path(outdir) / "codec.wasm").write_bytes(wasm_bytes)
            return [str(Path(outdir) / "codec.wasm")]

        mock_client = MagicMock()
        mock_client.pull.side_effect = fake_pull

        with (
            patch(ORAS_CLIENT, return_value=mock_client),
            resolve_uri("oci://ghcr.io/org/repo:v1.0") as path,
        ):
            assert path.exists()
            assert path.read_bytes() == wasm_bytes

        mock_client.pull.assert_called_once()

    def test_no_wasm_file_raises(self) -> None:
        def fake_pull(*, target, outdir):
            txt = Path(outdir) / "readme.txt"
            txt.write_bytes(b"hello")
            return [str(txt)]

        mock_client = MagicMock()
        mock_client.pull.side_effect = fake_pull

        with (
            patch(ORAS_CLIENT, return_value=mock_client),
            pytest.raises(ValueError, match=r"No \.wasm file found"),
            resolve_uri("oci://ghcr.io/org/repo:v1.0"),
        ):
            pass
