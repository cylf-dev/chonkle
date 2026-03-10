import io
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from chonkle.wasm_download import (
    download_https,
    download_oci,
    get_cache_dir,
)

URLOPEN = "chonkle.wasm_download.urllib.request.urlopen"
ORAS_CLIENT = "oras.client.OrasClient"


def _fake_response(data: bytes) -> MagicMock:
    """Build a MagicMock that acts as a urllib response context manager."""
    resp = MagicMock()
    resp.__enter__ = lambda s: io.BytesIO(data)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestGetCacheDir:
    def test_default_uses_tempdir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("CHONKLE_CACHE_DIR", raising=False)
        monkeypatch.setenv("TMPDIR", str(tmp_path))
        monkeypatch.setattr(tempfile, "tempdir", None)
        result = get_cache_dir()
        assert result == tmp_path / "chonkle" / "wasm"
        assert result.exists()

    def test_env_var_override(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("CHONKLE_CACHE_DIR", str(tmp_path / "custom"))
        result = get_cache_dir()
        assert result == tmp_path / "custom"
        assert result.exists()


class TestDownloadHttps:
    def test_downloads_and_caches(self, tmp_path: Path) -> None:
        wasm_bytes = b"\x00asm\x01\x00\x00\x00"
        resp = _fake_response(wasm_bytes)

        with patch(URLOPEN, return_value=resp):
            path = download_https(
                "https://example.com/releases/v1/codec.wasm",
                cache_dir=tmp_path,
            )

        assert path.exists()
        assert path.name == "codec.wasm"
        assert path.read_bytes() == wasm_bytes

    def test_cache_hit_skips_download(self, tmp_path: Path) -> None:
        url = "https://example.com/releases/v1/codec.wasm"
        resp = _fake_response(b"\x00asm\x01\x00\x00\x00")

        with patch(URLOPEN, return_value=resp) as mock:
            path1 = download_https(url, cache_dir=tmp_path)
            path2 = download_https(url, cache_dir=tmp_path)

        assert path1 == path2
        # Two calls per download (wasm + manifest.json sidecar); second
        # download_https is a cache hit so no additional calls.
        assert mock.call_count == 2

    def test_force_redownloads(self, tmp_path: Path) -> None:
        url = "https://example.com/releases/v1/codec.wasm"
        resp = _fake_response(b"\x00asm\x01\x00\x00\x00")

        with patch(URLOPEN, return_value=resp) as mock:
            download_https(url, cache_dir=tmp_path)
            download_https(url, cache_dir=tmp_path, force=True)

        # Two downloads x two files (wasm + manifest.json sidecar) each.
        assert mock.call_count == 4


class TestDownloadOci:
    def test_full_flow(self, tmp_path: Path) -> None:
        wasm_bytes = b"\x00asm\x01\x00\x00\x00"

        def fake_pull(*, target, outdir):
            Path(outdir).mkdir(parents=True, exist_ok=True)
            (Path(outdir) / "codec.wasm").write_bytes(wasm_bytes)
            (Path(outdir) / "codec.manifest.json").write_bytes(b"{}")
            return [
                str(Path(outdir) / "codec.wasm"),
                str(Path(outdir) / "codec.manifest.json"),
            ]

        mock_client = MagicMock()
        mock_client.pull.side_effect = fake_pull

        with patch(ORAS_CLIENT, return_value=mock_client):
            path = download_oci(
                "oci://ghcr.io/org/repo:v1.0",
                cache_dir=tmp_path,
            )

        assert path.exists()
        assert path.read_bytes() == wasm_bytes
        mock_client.pull.assert_called_once()

    def test_cache_hit_skips_pull(self, tmp_path: Path) -> None:
        # Pre-populate cache directory with both required sidecar files.
        ref_dir = tmp_path / "oci" / "ghcr.io" / "org" / "repo" / "v1.0"
        ref_dir.mkdir(parents=True)
        cached = ref_dir / "codec.wasm"
        cached.write_bytes(b"\x00asm\x01\x00\x00\x00")
        (ref_dir / "codec.manifest.json").write_bytes(b"{}")

        mock_client = MagicMock()

        with patch(ORAS_CLIENT, return_value=mock_client):
            path = download_oci(
                "oci://ghcr.io/org/repo:v1.0",
                cache_dir=tmp_path,
            )

        assert path == cached
        mock_client.pull.assert_not_called()

    def test_force_bypasses_cache(self, tmp_path: Path) -> None:
        ref_dir = tmp_path / "oci" / "ghcr.io" / "org" / "repo" / "v1.0"
        ref_dir.mkdir(parents=True)
        cached = ref_dir / "codec.wasm"
        cached.write_bytes(b"\x00asm\x01\x00\x00\x00")
        manifest = ref_dir / "codec.manifest.json"
        manifest.write_bytes(b"{}")

        mock_client = MagicMock()
        mock_client.pull.return_value = [str(cached), str(manifest)]

        with patch(ORAS_CLIENT, return_value=mock_client):
            download_oci(
                "oci://ghcr.io/org/repo:v1.0",
                cache_dir=tmp_path,
                force=True,
            )

        mock_client.pull.assert_called_once()

    def test_no_wasm_file_raises(self, tmp_path: Path) -> None:
        txt_file = tmp_path / "oci" / "ghcr.io" / "org" / "repo" / "v1.0" / "readme.txt"
        txt_file.parent.mkdir(parents=True)
        txt_file.write_bytes(b"hello")

        mock_client = MagicMock()
        mock_client.pull.return_value = [str(txt_file)]

        with (
            patch(ORAS_CLIENT, return_value=mock_client),
            pytest.raises(ValueError, match=r"No \.wasm file found"),
        ):
            download_oci(
                "oci://ghcr.io/org/repo:v1.0",
                cache_dir=tmp_path,
                force=True,
            )
