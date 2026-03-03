# WASI

**WASI** (WebAssembly System Interface) is a standard set of APIs that give Wasm modules access to OS-like functionality — things like memory allocation, file I/O, and clocks. Think of it as a minimal, portable "operating system" interface for Wasm.

A common reason to use WASI is that C code calls `malloc` and `free`. These libc functions need an underlying memory allocator, which WASI provides. Without WASI, you'd have to write your own allocator from scratch.

## The `wasm32-wasi` target

When compiling to Wasm, the build target `wasm32-wasi` means:

- `wasm32` — 32-bit WebAssembly (pointers are 32 bits)
- `wasi` — use the WASI system interface (as opposed to a bare `wasm32-unknown` target with no system interface at all)

## Command vs reactor

Wasm modules come in two execution models:

- **Command** — has a `_start` function (like `main()` in C). The runtime calls `_start`, the module runs to completion, and then it's done. This is the default and is analogous to running a CLI program.
- **Reactor** — has no `_start`. Instead it exports individual functions that the host calls on demand, potentially many times over the module's lifetime. The module stays alive between calls.

A module is built as a reactor (via `-mexec-model=reactor` in the build flags) when the host needs to call exported functions as separate steps. For example, a codec module exports `alloc`, `encode`/`decode`, and `dealloc` — the host calls these in sequence, and the module must stay alive between calls so that memory allocated by `alloc` is still valid when the codec function reads it.

WASI reactor modules export a function called `_initialize` (instead of `_start`). The runtime must call `_initialize` once after instantiation to set up the WASI environment before calling any other exported functions.
