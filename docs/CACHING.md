# Caching

This document describes how caching currently works in chonkle and what we've considered but haven't implemented yet.

## Wasm file download cache

Downloaded `.wasm` files are cached on disk to avoid redundant network fetches. See `wasm_download.py`.

- **HTTPS**: keyed by `sha256(url)`, stored at `<cache_dir>/https/<hash>/<filename>`
- **OCI**: keyed by registry reference, stored at `<cache_dir>/oci/<ref_path>/`
- **Default location**: `$TMPDIR/chonkle/wasm/` (overridable via `CHONKLE_CACHE_DIR`)
- **Force re-download**: `CHONKLE_FORCE_DOWNLOAD=1` or `force=True` parameter
- **Atomic writes**: HTTPS downloads use tempfile + rename to avoid partial files

There is no eviction, TTL, or size management. However, Wasm modules are small (typically KBs), the default `$TMPDIR` location is cleaned by the OS periodically, and the force-download provides an escape hatch for stale cache scenarios. A more robust cache can be implemented in the future if needed.

## wasmtime compiled-code disk cache

Every call to `_wasm_call` in `wasm_runner.py` creates a `wasmtime.Engine` and calls `Module.from_file()`, which compiles Wasm bytecode to native machine code. This is the most expensive per-call overhead in the Wasm path.

To avoid this repeated compilation, the Engine is configured with wasmtime's built-in compiled-code cache (`config.cache = True`). wasmtime transparently stores compiled native code on disk. When `Module.from_file()` is called for a `.wasm` file that has already been compiled with the same wasmtime version, the cached native code is loaded instead of recompiling.

This is separate from the download cache above: the download cache stores `.wasm` files to avoid network fetches, while the compilation cache stores native machine code to avoid recompilation.

wasmtime manages the cache location and eviction automatically (`~/.cache` on Linux, `~/Library/Caches` on macOS). The cache invalidates automatically when the wasmtime version changes.

## Warm-container deployments

The compiled-code disk cache still requires reading the cached native code from disk on each process invocation. In warm-container environments (e.g. AWS Lambda), the same process handles multiple invocations, so caching the `Engine` and compiled `Module` objects in Python module-level variables would skip that disk read on subsequent invocations within the same container.

- `Engine` and `Module` are immutable — safe to reuse across calls with no risk of state leaking between invocations
- The `Engine` only needs a single instance (singleton) since the compilation settings don't vary
- A module-level `dict` keyed by resolved file path would cache compiled `Module` objects (one per `.wasm` file)
- `Store` and `Instance` hold mutable state (the Wasm linear memory) — these must be fresh per-call
