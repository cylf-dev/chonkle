# Guides

Step-by-step guides for building chonkle codecs. For contract specifications, see [reference/codec-contract/](../reference/codec-contract/).

## Building a codec

- [Component Model codec in Rust](COMPONENT_MODEL_RUST.md) — recommended starting point
- [Component Model codec in C](COMPONENT_MODEL_C.md) — C with Zig build system
- [Core Wasm codec](CORE.md) — C with Zig build system, no Component Model
- [Native codec](NATIVE.md) — numcodecs wrapper (no build step)

## Which type should I build?

**Component Model (Rust)** if you're writing a new codec from scratch. Rust's `cargo-component` handles all the WIT bindings and Component Model lifting automatically, so you write idiomatic Rust and get a `.wasm` binary out. This is the simplest path.

**Component Model (C)** if you have existing C code you want to wrap, or need to avoid Rust. The Zig build system cross-compiles C to `wasm32-wasi` and `wasm-tools` lifts the result to a Component Model binary. You get the same Component Model interface as the Rust path but manage memory and ABI glue manually.

**Core Wasm** if you need maximum host-to-module transfer throughput in the Python host. Core Wasm bypasses the Component Model canonical ABI, which is the primary bottleneck in the Python host's data path. The trade-off is a lower-level interface: you implement `alloc`/`dealloc` and work with a binary port-map wire format directly. See [design/DATA_COPIES.md](../design/DATA_COPIES.md) and [design/CANONICAL_ABI_PERF.md](../design/CANONICAL_ABI_PERF.md) for the performance context.

**Native** if [numcodecs](https://numcodecs.readthedocs.io/) already implements the codec you need. You write a JSON signature file and chonkle wraps the numcodecs codec automatically. No build step, no Wasm. Requires `pip install chonkle[native]`.
