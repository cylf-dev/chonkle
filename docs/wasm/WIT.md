# WIT — WebAssembly Interface Types

**WIT** (WebAssembly Interface Types) is the interface definition language for the Wasm Component Model. It lets you describe the types and functions a Wasm component exposes in a language-neutral `.wit` file, so that tooling can generate glue code for any language on either side of the boundary.

A `.wit` file is distinct from the component itself — it's a declaration of what the component looks like, not an implementation of it.

## The four building blocks

### `package`

```wit
package cylf:tiff-predictor-2-python@0.1.0;
```

Declares a namespace, name, and version for this group of definitions. The format is `<namespace>:<name>@<version>`. It's the Component Model's equivalent of a package identifier — it scopes interface names so they don't collide with those from other packages, and tooling uses it to refer to this component as a dependency.

### `interface`

```wit
interface codec {
    encode: func(data: list<u8>, config: string) -> result<list<u8>, string>;
    decode: func(data: list<u8>, config: string) -> result<list<u8>, string>;
}
```

Declares a named set of functions and types. An interface is a reusable unit — it can be exported by one component and imported by another without being tied to a specific world or implementation.

### `world`

```wit
world tiff-predictor-2-python {
    export codec;
}
```

Declares what a component exposes (`export`) and what it needs from the host (`import`). A world is the complete contract for a specific component. One package can define multiple worlds.

- `export codec` — the component provides the functions in the `codec` interface; any host that loads it can call them.
- `import ...` — the component expects the host to provide the named interface or function.

### WIT types

WIT has its own type system, separate from any host language. Common types:

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

`list<u8>` is the idiomatic byte array in WIT. `result<list<u8>, string>` means "returns bytes on success or an error string on failure" — analogous to Rust's `Result<Vec<u8>, String>`.

## The Canonical ABI

WIT only defines the interface. The **Canonical ABI** is the separate specification that defines how these high-level types are serialized into Wasm's low-level number primitives (`i32`, `i64`, `f32`, `f64`) and linear memory. When you compile a component with a WIT file, the toolchain generates glue code that handles all the pointer/length/memory management according to the Canonical ABI — you call `encode(data, config)` passing a byte list and a string directly, rather than manually allocating memory and passing pointers.

This is the key difference from Core Wasm: with Core modules you manage the low-level representation yourself; with Components the toolchain does it for you.

## How this project relates to WIT

chonkle supports both Core Wasm modules and Component Model components; `wasm_runner.py` detects the format automatically. See [WASM.md](../WASM.md#choosing-between-core-modules-and-components) for a tradeoffs comparison and [CODEC_CONTRACT.md](../CODEC_CONTRACT.md) for the interface contract each format must satisfy.

The tiff-predictor-2-python codec is a Component — it's authored in Python via componentize-py, which generates a Component by necessity. It is used as a test fixture in `tests/fixtures/chunks/cog_wasm/` and exercised by `test_wasm_runner.py` via the Component path in `wasm_runner.py`. Its WIT file:

```wit
package cylf:tiff-predictor-2-python@0.1.0;

interface codec {
    encode: func(data: list<u8>, config: string) -> result<list<u8>, string>;
    decode: func(data: list<u8>, config: string) -> result<list<u8>, string>;
}

world tiff-predictor-2-python {
    export codec;
}
```

## Further reading

- [WIT format reference](https://component-model.bytecodealliance.org/design/wit.html)
- [Component Model overview](https://component-model.bytecodealliance.org/)
- [Canonical ABI spec](https://github.com/WebAssembly/component-model/blob/main/design/mvp/CanonicalABI.md)
