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

Every codec call in `executor.py` creates a `wasmtime.Engine` and compiles the `.wasm` file to native machine code — via `Module.from_file()` on the Core path or `Component.from_file()` on the Component path. This compilation is the most expensive per-call overhead in the Wasm path.

To avoid this repeated compilation, the Engine is configured with wasmtime's built-in compiled-code cache (`config.cache = True`). wasmtime transparently stores compiled native code on disk. When `Module.from_file()` or `Component.from_file()` is called for a `.wasm` file that has already been compiled with the same wasmtime version, the cached native code is loaded instead of recompiling.

This is separate from the download cache above: the download cache stores `.wasm` files to avoid network fetches, while the compilation cache stores native machine code to avoid recompilation.

wasmtime manages the cache location and eviction automatically (`~/.cache` on Linux, `~/Library/Caches` on macOS). The cache invalidates automatically when the wasmtime version changes.

## In-process caching

`executor.py` currently creates a new `wasmtime.Engine`, `wasmtime.component.Linker`, and `wasmtime.component.Component` on each `_call_component()` invocation. Timing instrumentation in `_call_component()` (see [CANONICAL_ABI_PERF.md](decisions/CANONICAL_ABI_PERF.md)) showed that with a warm disk cache, `Component.from_file()` costs ~0.003s and `Store`/`Linker` construction is essentially free.

Although negligible, if these initialization costs were a concern, the approach would be to cache at module level:

- `_engine` — `wasmtime.Engine` singleton, created once with `cache = True`
- `_linker` — `wasmtime.component.Linker` singleton, created once with `add_wasip2()`
- `_component_cache` — `dict[Path, wasmtime.component.Component]`, keyed by resolved `.wasm` path

Cache invalidation on `force_download=True` would require evicting `_component_cache` entries for affected paths after URI resolution.

Note that `Store` and `Instance` hold mutable Wasm linear memory and should still be created fresh per call.
