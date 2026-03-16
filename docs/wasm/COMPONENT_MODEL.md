# The WebAssembly Component Model

The **Component Model** is a higher-level standard built on top of Core WebAssembly. It defines a binary format for *components* — self-describing Wasm binaries that declare their typed imports and exports in a machine-readable way — along with a composition model and an ABI for marshalling rich types across component boundaries.

The Component Model is a Bytecode Alliance specification, separate from (but layered on top of) the Core Wasm spec. See [component-model.bytecodealliance.org](https://component-model.bytecodealliance.org/) for the full specification.

## Core modules vs. components

A **Core Wasm module** (`.wasm` with magic bytes `01 00 00 00`) exposes only Wasm primitives at its boundary: integers (`i32`, `i64`) and floats (`f32`, `f64`). To exchange bytes or strings with a host, the module and host must agree on a manual convention — pointers and lengths passed as integers, manual memory management on both sides. There is no machine-readable description of what the module expects or produces.

A **component** (`.wasm` with magic bytes `0d 00 01 00`) wraps one or more Core modules and adds a typed interface layer. Its imports and exports are described using [WIT](WIT.md) (WebAssembly Interface Types), so tooling can verify the interface at load time and generate marshalling glue automatically. The host calls the component's exported functions passing real typed values, not raw pointers — the Canonical ABI handles the serialization.

The two formats are mutually exclusive but complementary: components are built *from* Core modules (or from source via language toolchains), and the host runtime detects the format automatically by reading the magic bytes.

## Why the Component Model exists

Core Wasm's primitive-only boundary works, but it puts the burden on every codec author and every host to agree on the same manual convention. If the convention changes, or if two independently developed modules use slightly different conventions, things break silently. There is no tooling support, no interface verification, and no code generation.

The Component Model solves this by making the interface explicit and verifiable:

- **Interface verification at load time** — Wasmtime rejects a component whose exports don't match the expected WIT signature immediately, before any code runs.
- **Code generation** — Language toolchains (Rust via `wit-bindgen`, Python via `componentize-py`, C via `wit-bindgen-c`) generate the marshalling glue from the WIT file. Component authors write functions against the WIT interface in their native language; the toolchain handles the rest.
- **Ecosystem interoperability** — Any language with a Component Model toolchain can implement the same WIT interface, so components authored in different languages compose without manual coordination.

## Component composition

The Component Model's design intent is to enable *composition*: combining components by wiring one component's exports to another's imports, producing a new component. This is the "lego bricks" promise — build a pipeline by composing individually authored codec components.

Current composition tooling:

- **`wasm-tools compose`** — static composition tool: takes a root component and a set of dependencies, wires imports to exports, and produces a single composed component.
- **`wac`** (WebAssembly Compositions) — a higher-level composition language that compiles to the same output as `wasm-tools compose`.

**Current limitation**: these tools support static, single-topology linking. They wire one component's exports to another's imports in a fixed data-flow graph. They cannot generate two distinct wiring topologies within one component (e.g., a codec pipeline where encoding chains components in one order and decoding chains them in reverse).

## Toolchain for authoring components

| Language | Tool | Notes |
| --- | --- | --- |
| Python | `componentize-py` | Generates a WASIp2 component from a Python class; requires a `.wit` file |
| Rust | `cargo-component` | Cargo subcommand; generates bindings from WIT |
| Rust | `wit-bindgen` | Lower-level binding generator; used directly or via `cargo-component` |
| C / C++ | `wit-bindgen-c` | Generates C headers from WIT |
| Go | `wit-bindgen-go` | Generates Go bindings from WIT |

## Further reading

- [Component Model specification](https://component-model.bytecodealliance.org/)
- [WIT format reference](https://component-model.bytecodealliance.org/design/wit.html) — see also [WIT.md](WIT.md) in this knowledge base
- [Canonical ABI spec](https://github.com/WebAssembly/component-model/blob/main/design/mvp/CanonicalABI.md)
- [componentize-py](https://github.com/bytecodealliance/componentize-py)
- [wasm-tools](https://github.com/bytecodealliance/wasm-tools)
