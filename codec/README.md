# codec/

Wasm Component Model codec implementations for the chonkle pipeline.

Each codec exports `chonkle:codec/transform` (`encode` + `decode`). See
[docs/reference/CODEC_CONTRACT.md](../docs/reference/CODEC_CONTRACT.md) for
the full contract, port conventions, and signature sidecar format.

## Codecs

### identity-c/

Passthrough codec used for benchmarking pipeline overhead. `encode` and
`decode` are both no-ops: output `bytes` = input `bytes`.

Ports: `bytes` in → `bytes` out.

Toolchain: C + `zig cc` → wasm32-wasi reactor → `wasm-tools` component lift.

```
cd codec/identity-c
zig build
cp zig-out/identity.wasm identity.wasm
```

### tiff-predictor-2-c/

TIFF horizontal differencing predictor (TIFF Predictor 2). `encode` applies
differencing; `decode` reconstructs original values. Reversible; encode→decode
roundtrip is identity.

Ports: `bytes` (required) + `bytes_per_sample` (int, required) + `width`
(int, required) in → `bytes` out.

Same toolchain as identity-c.

```
cd codec/tiff-predictor-2-c
zig build -Doptimize=ReleaseFast
cp zig-out/tiff-predictor-2.wasm tiff-predictor-2.wasm
```

### zlib-rs/

zlib compress/decompress via the `flate2` crate with the `rust_backend`
feature (pure Rust, no native dependencies).

Ports: `bytes` (required) + `level` (int, encode-only, default 6) in →
`bytes` out.

Toolchain: `cargo-component` targeting `wasm32-wasip2`.

```
cd codec/zlib-rs
cargo component build --target wasm32-wasip2 --release
cp target/wasm32-wasip1/release/zlib.wasm zlib.wasm
```

---

## Shared C infrastructure — shared/

Used by all C+Zig codecs:

- **codec.h** / **codec.c** — WIT-generated canonical ABI bindings
- **codec_component_type.o** — component type object produced by wit-bindgen;
  passed to `zig cc` as a source file alongside the C sources

Regenerate after any WIT interface change:

```
wit-bindgen c wit/ --world codec --out-dir codec/shared/
```

## WASI adapter — wasi_snapshot_preview1.reactor.wasm

Lifts a `wasm32-wasi` (WASI preview1) reactor module to a Component Model
(WASI preview2) component at build time. Used by `wasm-tools component new
--adapt` in the C+Zig codec builds. Downloaded from the wasmtime v41.0.0
GitHub release to match the `wasmtime` Python package version.

---

## Adding a codec

1. Implement `encode` and `decode` for the `chonkle:codec/transform` WIT interface.
2. Build to a Component Model `.wasm` and place it in `codec/<name>/`.
3. Write a `<name>.signature.json` sidecar in the same directory.
4. Add a `.gitignore` for your toolchain's build artifacts.

See [docs/reference/CODEC_CONTRACT.md](../docs/reference/CODEC_CONTRACT.md)
for the signature format and port conventions.
