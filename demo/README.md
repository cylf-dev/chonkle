# Demo

A notebook demonstrating chonkle's codec pipeline with Python and WebAssembly codecs,
using a real Sentinel-2 COG tile as test data.

## Setup

From the repo root:

```
uv sync --group notebook
```

## Running

```
uv run jupyter lab --notebook-dir=demo
```

Then open `chonkle-pipeline.ipynb` from the file browser.

## Notes

- The notebook downloads a ~200 MB Sentinel-2 COG on first run; subsequent runs skip the download.
- The HTTPS and OCI Wasm loading steps require internet access.
- A pre-compiled `tiff-predictor-2-c.wasm` is included for the local `file://` test.
