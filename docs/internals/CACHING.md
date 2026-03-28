# Caching

Chonkle uses three layers of caching to avoid redundant downloads, recompilation, and re-instantiation. Each layer answers a different question:

| Layer             | What it caches                      | Where                | Managed by            |
| ----------------- | ----------------------------------- | -------------------- | --------------------- |
| Codec store       | Downloaded `.wasm` binaries         | `~/.chonkle/codecs/` | chonkle               |
| Compilation cache | Compiled native machine code        | OS cache dir         | wasmtime              |
| In-process        | Compiled modules and live instances | Memory               | codec wrapper objects |

A cold start (first run with a new codec) hits all three layers. A warm start (same codec, same wasmtime version) skips the download and recompilation entirely.

## Codec store

The codec store is the only durable cache for downloaded Wasm binaries. It lives at `~/.chonkle/codecs/` (overridable via `CHONKLE_CODEC_STORE`) and is organized by codec_id:

```text
~/.chonkle/codecs/
  zlib/
    zlib-rs.wasm
  tiff-predictor-2/
    tiff-predictor-2-c.wasm
```

The filename comes from the `implementation` field in the binary's embedded signature.

**Population:** When a pipeline declares a `sources` URI for a codec that is not already in the store, the binary is downloaded to a temporary directory, then copied into the store. Subsequent resolutions for the same codec_id skip the download.

**Invalidation:** Manual. Delete the directory or set `CHONKLE_FORCE_INSTALL=1` to overwrite an existing entry on next download.

**Integrity:** HTTPS downloads write to a tempfile and rename on completion, avoiding partial files.

## Compilation cache

Wasmtime maintains a separate disk cache of compiled native machine code (`config.cache = True`). When a `.wasm` file is loaded, wasmtime checks this cache before compiling. On a hit, precompiled native code is loaded directly.

This is orthogonal to the codec store: the codec store holds portable `.wasm` binaries; the compilation cache holds platform-specific compiled output derived from them.

**Location:** Managed by wasmtime (`~/.cache` on Linux, `~/Library/Caches` on macOS).

**Invalidation:** Automatic when the wasmtime version changes.

**Cost:** With a warm compilation cache, loading a component costs ~0.003s (see [CANONICAL_ABI_PERF.md](CANONICAL_ABI_PERF.md)).

## In-process lifetime

Codec wrapper objects compile and instantiate Wasm modules once during pipeline preparation, not on every codec call:

- **Component codecs** load and compile the component at init time. Each call creates only a new store and instance (mutable Wasm state that cannot be reused across calls).
- **Core codecs** load, compile, and instantiate the module at init time, keeping the instance and its linear memory alive for the codec's lifetime. The persistent instance enables single-copy data transfer between core codecs via direct memory access.

All codec instantiations share a single wasmtime engine, which in turn shares the compilation cache.
