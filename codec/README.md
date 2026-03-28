# codec/

Wasm codec implementations for the chonkle pipeline. Codecs fall into two categories:

- **Component Model codecs** implement the `chonkle:codec/transform` WIT interface and are lifted to Component Model `.wasm` binaries. See [docs/reference/CODEC_CONTRACT.md](../docs/reference/CODEC_CONTRACT.md).
- **Core Wasm codecs** are wasm32-wasi reactor modules that export `memory`, `alloc`, `dealloc`, `encode`, and `decode` using the binary port-map wire format. See [docs/reference/CORE_ABI.md](../docs/reference/CORE_ABI.md).

All codec build processes include a post-build step to embed the codec signature into the `.wasm` binary:

```
chonkle embed-signature <output.wasm> signature.json
```

Each codec directory contains a `signature.json` build input that is embedded as a `chonkle:signature` custom section.

## Component Model codecs

### identity-c/

Passthrough codec used for benchmarking pipeline overhead. `encode` and
`decode` are both no-ops: output `bytes` = input `bytes`.

Ports: `bytes` in → `bytes` out.

Toolchain: C + `zig cc` → wasm32-wasi reactor → `wasm-tools` component lift.

```
cd codec/identity-c
zig build
cp zig-out/identity.wasm identity.wasm
chonkle embed-signature identity.wasm signature.json
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
chonkle embed-signature tiff-predictor-2.wasm signature.json
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
chonkle embed-signature zlib.wasm signature.json
```

## Core Wasm codecs

### identity-core-c/

Passthrough codec for benchmarking using the core ABI. `encode` and `decode` are both no-ops: output `bytes` = input `bytes`. Uses the binary port-map wire format instead of the Component Model canonical ABI.

Ports: `bytes` in → `bytes` out.

Exports: `memory`, `alloc`, `dealloc`, `encode`, `decode`.

Toolchain: C + `zig cc` → wasm32-wasi reactor (no Component Model lift step). Same build toolchain as identity-c and tiff-predictor-2-c but without the `wasm-tools component new` step.

```
cd codec/identity-core-c
zig build
cp zig-out/identity-core.wasm identity-core.wasm
chonkle embed-signature identity-core.wasm signature.json
```

---

## Shared C infrastructure — shared/

Used by C+Zig codecs:

- **codec.h** / **codec.c** — WIT-generated canonical ABI bindings for Component Model codecs
- **codec_component_type.o** — component type object produced by wit-bindgen; passed to `zig cc` as a source file alongside the C sources
- **core_abi.h** / **core_abi.c** — C reference implementation of the core ABI port-map parse/serialize/find operations, used by core wasm codecs like identity-core-c

Regenerate WIT bindings after any WIT interface change:

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

### Component Model codec

1. Implement `encode` and `decode` for the `chonkle:codec/transform` WIT interface.
2. Build to a Component Model `.wasm` and place it in `codec/<name>/`.
3. Write a `signature.json` in the codec directory.
4. Embed the signature: `chonkle embed-signature <name>.wasm signature.json`.
5. Add a `.gitignore` for your toolchain's build artifacts.

### Core Wasm codec

1. Implement and export `memory`, `alloc`, `dealloc`, `encode`, and `decode` per the [core ABI spec](../docs/reference/CORE_ABI.md).
2. Build to a core wasm32-wasi `.wasm` and place it in `codec/<name>/`.
3. Write a `signature.json` in the codec directory.
4. Embed the signature: `chonkle embed-signature <name>.wasm signature.json`.
5. Add a `.gitignore` for your toolchain's build artifacts.

See [docs/reference/CODEC_CONTRACT.md](../docs/reference/CODEC_CONTRACT.md) for the Component Model signature format and port conventions, or [docs/reference/CORE_ABI.md](../docs/reference/CORE_ABI.md) for the core ABI contract.
