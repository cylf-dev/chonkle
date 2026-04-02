# Wasm Execution Model

This document covers what actually happens when a Wasm module is compiled, instantiated, and called from a host program. The existing docs explain what Wasm is and how linear memory works; this one explains how execution works at runtime.

## Compilation: .wasm → native machine code

A `.wasm` file contains portable bytecode — instructions for the virtual CPU described in [OVERVIEW.md](OVERVIEW.md). Before any of it can run, a runtime like Wasmtime translates that bytecode into native machine code for the host CPU (ARM64, x86-64, etc.). This translation is called JIT (just-in-time) compilation, and Wasmtime does it using its Cranelift compiler.

The result is native machine code — the same kind your OS runs from any compiled binary. Wasmtime can save this compiled output to disk as a `.cwasm` file. On subsequent runs, Wasmtime loads the `.cwasm` directly and skips recompilation. This is the compilation cache; it is why the first call to a codec is slow and subsequent calls are fast.

## Instantiation: allocating runtime state

Compiling a module produces code. Instantiating it produces a running copy of that code with its own state.

Before instantiation, three objects must already exist. An `Engine` holds the compiler configuration and is typically created once per process. A `Store` is the container for all runtime state — instances, memories, tables — and is tied to an engine; it is the thing that actually owns memory allocations and gets passed to every Wasmtime call. A `Linker` is a registry of host-provided imports: you register host functions, a shared memory object, and WASI bindings into the linker before instantiation so it knows how to satisfy each import the module declares.

With those in place, `linker.instantiate(store, module)` wires a compiled module to its imports and produces a live instance. Wasmtime does the following:

1. **Allocates linear memory.** A contiguous byte array is allocated (via `mmap` or equivalent). Its initial size is specified in the module's binary. This step is skipped if the host pre-registered a memory object with the linker: the module's memory import is satisfied by that existing allocation instead of a fresh one. That is how multiple module instances can share the same backing buffer.

2. **Writes data segments.** The module carries a data section — static content like string literals, lookup tables, and initialized global variables. Wasmtime copies these into the linear memory at the addresses the compiler chose. This is why two instances of the same binary cannot safely share a memory object: the destination offsets for data segments are fixed at compile time by the linker, not controlled by the host at instantiation. The second instantiation unconditionally reinitializes those same low addresses, stomping the first instance's data. Two instances of different binaries compiled to use non-overlapping regions could coexist in shared memory, but that requires deliberate coordination at compile time — it is not something Wasmtime or the host can arrange at runtime.

3. **Sets up the function table.** Wasm supports indirect function calls (function pointers). The module's table lists which functions can be called indirectly. Wasmtime populates this table with references to the compiled native functions.

4. **Resolves imports.** The module declares what it needs from the host: memory, functions, globals. Wasmtime connects each declared import to whatever the host provided via the linker — the shared memory object, the `request_output_buffer` callback, WASI functions, etc. Unresolved imports are an error at this step.

5. **Returns an instance.** The instance is a handle to all of the above. Its exports (functions, memory, globals) are accessible to the host.

## Calling a Wasm function

There is no interpreter loop. When you call an exported Wasm function, you are calling a native function compiled from the Wasm bytecode. It runs on the host CPU in the same OS process as your Python program. The host thread that made the call is the thread the Wasm code runs on.

Locals and function call frames live on the host's native call stack, the same stack your Python program uses. Values that do not fit in registers or whose address is taken spill into the module's linear memory via a shadow stack managed by the compiled code (usually pointed to by a global called `__stack_pointer`). This is distinct from the heap that `malloc` manages — it is the equivalent of the C call stack, but placed inside linear memory rather than on the OS stack.

There is no context switch, no system call, and no process boundary when calling a Wasm function. The overhead is comparable to calling a native shared library function.

## The sandbox: how isolation is enforced

Wasm code has no pointers. All memory accesses use 32-bit integers as offsets into the module's linear memory array. The compiled native code translates every load and store into a bounds-checked array access: if the offset is out of range, execution traps immediately (an exception is thrown to the host). The module cannot construct an address to anything outside its linear memory — not the Python heap, not Wasmtime's internals, not the OS.

On 64-bit systems, Wasmtime uses a virtual memory trick to make this essentially free: it `mmap`s a 4 GiB guard region around the linear memory. An out-of-bounds access faults at the OS level (SIGSEGV/SIGBUS) and is caught by Wasmtime's signal handler, converting it into a trap. No explicit bounds-check instruction is needed in the hot path.

System calls are equally unavailable. Wasm has no `syscall` instruction. The only way a module can interact with the outside world is through imported functions the host explicitly provided. If the host did not give the module `fd_write`, the module cannot write to stdout. If the host did not provide `request_output_buffer`, the module cannot call it. The host controls the entire interface surface.

## Linear memory from the host's perspective

From the host's side, the linear memory is just a byte array. Wasmtime exposes it through methods like `mem.write(store, data, offset)` and `mem.read(store, start, end)`, which are single C-level `memcpy` calls into or out of the backing buffer.

There is no marshalling, no type conversion, and no protocol. Writing bytes at an offset makes those bytes visible to the Wasm module at that same offset. The module reads them with a load instruction; the host reads them back with `mem.read`. Both sides see the same flat byte array.

## Shared memory and zero-copy

Normally each module instance has its own linear memory, allocated during instantiation. Wasmtime also supports importing an externally created memory object. When multiple module instances import the same memory object, they all operate on the same underlying byte array.

A Wasm codec writing output to offset 0x500000 in a shared memory makes those bytes immediately visible to any other module instance that imports the same memory — at offset 0x500000, with no copy. The host can also read those bytes at any time via `mem.read`. This is the mechanism that makes zero-copy pipelines possible: the output offset from step N is passed directly as the input offset to step N+1.

## What "VM" means and does not mean

Wasm is called a virtual machine because it defines a portable bytecode format that any conforming runtime can execute. The portability is real: the same `.wasm` file runs on any OS and CPU.

What "VM" does not imply here: a separate process, an interpreter running alongside your program, or a hypervisor. After compilation, Wasm code is native machine code executing on real hardware in the same process as the host. The "virtual" part is the instruction set the source language compiled to (the portable bytecode), not the thing that runs it.
