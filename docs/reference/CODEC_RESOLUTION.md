# Codec Resolution

The `Resolver` maps codec_ids to `Codec` instances via a resolution chain. Each step is tried in order; the first match wins.

## Resolution chain

1. **Explicit paths** -- a `--codec-path` CLI flag or `paths` dict mapping a codec_id directly to a `.wasm` file. Useful for testing.
2. **Force sources** -- a `--source ID=URI` CLI flag or `force_sources` dict. Downloads the binary from the URI unconditionally, installs it into the store (overwriting any existing entry with the same implementation name), and returns the newly installed codec. Use this to fetch a specific remote build regardless of what is already in the store.
3. **Per-codec overrides** -- an `overrides` dict mapping a codec_id to a specific implementation name, bypassing the preference system entirely.
4. **Local codec store and native** -- all implementations for the codec_id are collected from the store and from bundled native signatures, then selected by backend preference.
5. **Pipeline sources** -- if no local match is found, a `sources` URI from the pipeline JSON is downloaded, installed into the store, and instantiated.

## Backend preference

When step 4 finds candidates, the resolver walks an ordered preference list (default: `["native", "core", "component"]`) and returns the first candidate whose backend matches. This selects between backend *types*, not between implementations of the same type.

Preference is strict: if no available backend matches the preference list, the resolver raises `ValueError`.

## Multiple implementations of the same backend

If the store contains more than one implementation of the same backend for a given codec_id (e.g. two different core wasm builds), the resolver returns whichever appears first in the store index. The store index is built by scanning `.wasm` files in sorted filename order, so the implementation whose filename sorts first alphabetically wins. This is a deterministic but arbitrary tiebreak -- use an explicit override to select a specific implementation when it matters.

## Binary detection

`detect_codec_type()` reads the 8-byte wasm header to route `.wasm` files to the correct backend:

- Core module: layer field `01 00 00 00`
- Component: layer field `0d 00 01 00`

See [codec-contract/COMPONENT_MODEL.md](codec-contract/COMPONENT_MODEL.md) and [codec-contract/CORE.md](codec-contract/CORE.md) for per-backend details.
