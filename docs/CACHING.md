# Caching

This document describes how caching currently works in chonkle and what we've considered but haven't implemented yet.

## Wasm file download cache

Downloaded `.wasm` files are cached on disk to avoid redundant network fetches. See `wasm_download.py`.

- **HTTPS**: keyed by `sha256(url)`, stored at `<cache_dir>/https/<hash>/<filename>`
- **OCI**: keyed by registry reference, stored at `<cache_dir>/oci/<ref_path>/`
- **Default location**: `$TMPDIR/chonkle/wasm/` (overridable via `CHONKLE_CACHE_DIR`)
- **Force re-download**: `CHONKLE_FORCE_DOWNLOAD=1` or `force=True` parameter
- **Atomic writes**: HTTPS downloads use tempfile + rename to avoid partial files

There is no eviction, TTL, or size management. The default `$TMPDIR` location is cleaned by the OS periodically, Wasm modules are small (typically KBs), and the force-download escape hatch exists for stale cache scenarios. This may need revisiting if the library moves to longer-lived deployment targets.

## wasmtime Engine/Module reuse

Currently, every call to `_wasm_call` in `wasm_runner.py` creates a fresh `wasmtime.Engine`, compiles the `Module` from the `.wasm` file, creates a `Store`, and instantiates the module. Nothing is reused across calls.

`Module.from_file()` compiles Wasm bytecode to native code, which is the most expensive operation in the pipeline. For the current CLI usage (one chunk per invocation), this doesn't matter. For a service processing many chunks, caching the `Engine` and compiled `Module` would likely be the single highest-impact optimization.

Some notes if we get there:

- `Engine` and `Module` are immutable and thread-safe — good candidates for caching
- `Store` and `Instance` hold mutable per-call state (linear memory, globals) — these must stay per-call
- wasmtime supports `Module.serialize()` / `Module.deserialize()` for persisting compiled native code to disk
- A module-level `dict` or `functools.lru_cache` keyed by resolved file path would likely cover most of the benefit

## Codec object recreation

Pipeline codec objects (both numcodecs and Wasm) are created fresh on every `encode()`/`decode()` call. numcodecs objects are cheap to create, and statelessness keeps correctness simple. The real cost in the Wasm path is module compilation (above), not object creation. We don't see a reason to cache codec objects.
