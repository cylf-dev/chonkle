# WASI

**WASI** (WebAssembly System Interface) is a standard set of APIs that give Wasm modules access to OS-like functionality — things like memory allocation, file I/O, and clocks. Think of it as a minimal, portable "operating system" interface for Wasm.

A common reason to use WASI is that C code calls `malloc` and `free`. These libc functions need an underlying memory allocator, which WASI provides. Without WASI, you'd have to write your own allocator from scratch.

## WASI Preview 1 vs Preview 2

WASI has two major versions with different foundations:

- **Preview 1** (`wasm32-wasi`) — APIs defined as raw Core Wasm imports: plain function signatures using `i32`/`i64` primitives. The component model did not exist yet. This is what the `wasm32-wasi` build target produces.
- **Preview 2** (WASIp2) — WASI redefined using WIT and the Component Model. The system interfaces (`wasi:filesystem/types`, `wasi:clocks/wall-clock`, etc.) are now WIT interfaces, not raw imports. A WASIp2 component declares which WASI interfaces it imports in its world; Wasmtime satisfies them at load time.

The Component Model did not replace WASI — WASI Preview 2 *adopted* the Component Model as its foundation. This is why tools like `componentize-py` (which compiles Python source into a Wasm component) are described as producing "WASIp2 components": the output is a Component Model component that may import WASIp2 WASI interfaces.

### The `wasm32-wasi` target

When compiling to Wasm, the build target `wasm32-wasi` means:

- `wasm32` — 32-bit WebAssembly (pointers are 32 bits)
- `wasi` — use the WASI Preview 1 system interface (as opposed to `wasm32-unknown-unknown`, which has no system interface and is used for browser WebAssembly where JavaScript is the host)

This produces a Core Wasm module with Preview 1 imports.

### The `wasm32-wasip2` target

`wasm32-wasip2` is the Preview 2 counterpart. It produces a Component Model component with WASIp2 imports. Not all toolchains require you to name this target explicitly — `componentize-py`, for example, compiles Python source directly to a WASIp2 component with no build target flag involved. Rust toolchains (`cargo-component`) do specify `wasm32-wasip2` explicitly in their build configuration.

## Command vs reactor

Wasm modules come in two execution models:

- **Command** — has a `_start` function (like `main()` in C). The runtime calls `_start`, the module runs to completion, and then it's done. This is the default and is analogous to running a CLI program.
- **Reactor** — has no `_start`. Instead it exports individual functions that the host calls on demand, potentially many times over the module's lifetime. The module stays alive between calls.

A module is built as a reactor (via `-mexec-model=reactor` in the build flags) when the host needs to call exported functions on demand, potentially many times. The module stays alive between calls, preserving any state it initializes at startup.

Core Wasm WASI reactor modules export a function called `_initialize` (instead of `_start`). The host must call `_initialize` once after instantiation to set up the WASI environment before calling any other exported functions.

This project's codecs are [Component Model](COMPONENT_MODEL.md) components, not bare Core modules. They are conceptually reactors — exporting `encode` and `decode` for the host to call repeatedly — but `_initialize` is never called explicitly. Wasmtime's Component Model linker (`linker.instantiate()`) handles all initialization automatically during instantiation.