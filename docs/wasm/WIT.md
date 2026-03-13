# WIT — WebAssembly Interface Types

**WIT** (WebAssembly Interface Types) is the interface definition language for the Wasm Component Model. It lets you describe the types and functions a Wasm component exposes in a language-neutral `.wit` file, so that tooling can generate marshalling glue for both the component (Wasm side) and the host (the runtime loading it), regardless of what language each is written in.

A `.wit` file is distinct from the component itself — it's a declaration of what the component looks like, not an implementation of it.

## Building blocks

A complete `.wit` file brings all the building blocks together:

```wit
package chonkle:codec@0.1.0;

interface transform {
    type port-name = string;
    type port-map = list<tuple<port-name, list<u8>>>;

    encode: func(inputs: port-map) -> result<port-map, string>;
    decode: func(inputs: port-map) -> result<port-map, string>;
}

world codec {
    export transform;
}
```

Each element (`package`, `interface`, `world`) has a distinct role, explained below.

### `package`

```wit
package chonkle:codec@0.1.0;
```

Declares a namespace, name, and version for this group of definitions. The format is `<namespace>:<name>@<version>`. It's the Component Model's equivalent of a package identifier — it scopes interface names so they don't collide with those from other packages, and tooling uses it to refer to this component as a dependency.

### `interface`

```wit
interface transform {
    ...
}
```

Declares a named set of functions and types. This is the shared contract: every codec component in this project is compiled against the same `wit/codec.wit`, which means every one of them implements the *same* `transform` interface — not just functions that happen to look the same, but functions explicitly identified by name and package. The host uses the same file to know what to call. That shared identity is what lets the toolchain verify compatibility and lets the host treat all codecs uniformly.

### `world`

```wit
world codec {
    export transform;
}
```

Declares what a component exposes (`export`) and what it needs from the host (`import`). A world is the complete contract for a specific component. One package can define multiple worlds.

- `export transform` — the component provides the functions in the `transform` interface; any host that loads it can call them.
- `import ...` — the component expects the host to provide the named interface or function. Common uses: WASI interfaces (`wasi:filesystem/types`, `wasi:clocks/wall-clock`) for components that need I/O or system services; host-provided callbacks; or interfaces exported by another component in a composed pipeline, which the tooling wires together by name.

`codec.wit` has no imports intentionally — codec components are pure data transformation with no side effects and no host services needed. The one exception is `componentize-py`: it may silently inject WASI imports into the compiled binary because the Python runtime requires them (clocks, environment, etc.), even though `codec.wit` doesn't declare them. This is a toolchain implementation detail, not something you control in the WIT file.

### Types

WIT has its own type system, separate from any host language. Types appear in function signatures, type aliases, and composite structures. The two things to understand are the built-in types and how to name them with aliases.

#### Built-in types

| WIT type                   | Meaning                                 |
|----------------------------|-----------------------------------------|
| `u8`, `u16`, `u32`, `u64`  | Unsigned integers                       |
| `s8`, `s16`, `s32`, `s64`  | Signed integers                         |
| `f32`, `f64`               | Floating-point numbers                  |
| `bool`                     | Boolean                                 |
| `string`                   | UTF-8 string                            |
| `list<T>`                  | Variable-length sequence of `T`         |
| `option<T>`                | Optional value (`Some(T)` or `None`)    |
| `result<T, E>`             | Success (`T`) or failure (`E`)          |
| `record`                   | Named struct                            |
| `variant`                  | Tagged union (like Rust `enum`)         |
| `tuple<T, U>`              | Fixed-length heterogeneous sequence     |

Notes:

- `list<u8>` is the idiomatic byte array in WIT.
- `result<port-map, string>` means "returns the output port map on success or an error string on failure" — analogous to Rust's `Result<T, String>`.

#### Type aliases

```wit
type port-name = string;
type port-map = list<tuple<port-name, list<u8>>>;
```

`type` declares a named alias within an interface. `port-name = string` is a readable label; `port-map = list<tuple<port-name, list<u8>>>` names a more complex structure — a list of `(name, bytes)` pairs — so WIT function signatures can write `port-map` rather than spelling out `list<tuple<port-name, list<u8>>>` every time.

## The Canonical ABI

WIT only defines the interface. The **Canonical ABI** is the separate specification that defines how these high-level types are serialized into Wasm's low-level number primitives (`i32`, `i64`, `f32`, `f64`) and linear memory. When you compile a component with a WIT file, the toolchain generates glue code that handles all the pointer/length/memory management according to the Canonical ABI — you call exported functions passing real typed values directly, rather than manually allocating memory and passing pointers.

This is the key difference from Core Wasm: with Core modules you manage the low-level representation yourself; with Components the toolchain does it for you.

## Further reading

- [Component Model](COMPONENT_MODEL.md) — what components are, composition, toolchain (this knowledge base)
- [WIT format reference](https://component-model.bytecodealliance.org/design/wit.html)
- [Canonical ABI spec](https://github.com/WebAssembly/component-model/blob/main/design/mvp/CanonicalABI.md)