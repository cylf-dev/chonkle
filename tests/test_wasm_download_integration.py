import numpy as np
import pytest

from chonkle.codecs import TiffPredictor2
from chonkle.wasm_download import download_https, download_oci
from chonkle.wasm_runner import resolve_wasm_uri, wasm_decode

HTTPS_URL = (
    "https://github.com/cylf-dev/tiff-predictor-2-c/"
    "releases/download/v0.1.0/tiff-predictor-2.wasm"
)
OCI_URI = "oci://ghcr.io/cylf-dev/tiff-predictor-2-c:v0.1.0"

WASM_MAGIC = b"\x00asm"


@pytest.mark.network
class TestHttpsDownloadIntegration:
    def test_download_from_github_releases(self, tmp_path):
        path = download_https(HTTPS_URL, cache_dir=tmp_path)
        assert path.exists()
        assert path.stat().st_size > 0
        assert path.read_bytes()[:4] == WASM_MAGIC

    def test_cache_hit_on_second_download(self, tmp_path):
        path1 = download_https(HTTPS_URL, cache_dir=tmp_path)
        path2 = download_https(HTTPS_URL, cache_dir=tmp_path)
        assert path1 == path2


@pytest.mark.network
class TestOciDownloadIntegration:
    def test_download_from_ghcr(self, tmp_path):
        path = download_oci(OCI_URI, cache_dir=tmp_path)
        assert path.exists()
        assert path.stat().st_size > 0
        assert path.read_bytes()[:4] == WASM_MAGIC


@pytest.mark.network
class TestResolveWasmUriIntegration:
    def test_https_resolve_and_run(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CHONKLE_CACHE_DIR", str(tmp_path))
        path = resolve_wasm_uri(HTTPS_URL)

        codec = TiffPredictor2()
        arr = np.array([[10, 12, 15, 11]], dtype=np.uint16)
        encoded = codec.encode(arr).tobytes()
        result_bytes = wasm_decode(path, encoded, {"bytes_per_sample": 2, "width": 4})
        result = np.frombuffer(result_bytes, dtype=np.uint16).reshape(arr.shape)
        np.testing.assert_array_equal(result, arr)

    def test_oci_resolve_and_run(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CHONKLE_CACHE_DIR", str(tmp_path))
        path = resolve_wasm_uri(OCI_URI)

        codec = TiffPredictor2()
        arr = np.array([[10, 12, 15, 11]], dtype=np.uint16)
        encoded = codec.encode(arr).tobytes()
        result_bytes = wasm_decode(path, encoded, {"bytes_per_sample": 2, "width": 4})
        result = np.frombuffer(result_bytes, dtype=np.uint16).reshape(arr.shape)
        np.testing.assert_array_equal(result, arr)
