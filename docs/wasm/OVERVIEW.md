# What is WebAssembly?

WebAssembly (Wasm) is a specification for a CPU that doesn't physically exist. It defines an instruction set — the operations this CPU can perform (things like "add two integers" or "load a byte from memory") — along with how it executes them and how its memory is organized. A `.wasm` file is a program composed of these operations — bytecode targeting this virtual CPU rather than any real hardware. Languages like C, C++, and Rust can compile to Wasm instead of to a physical CPU's instruction set, producing `.wasm` files.

A program that implements the virtual CPU is called a "runtime." The simplest approach would be for the runtime to interpret the `.wasm` bytecode instruction by instruction, but that would be slow. Instead, modern runtimes like [Wasmtime](https://wasmtime.dev/) compile the bytecode to native machine code for the host CPU before running it. The actual execution runs native instructions, which is why Wasm achieves near-native speed. This compilation step takes time (it is the most expensive overhead when loading a module), but it only needs to happen once per `.wasm` file — runtimes can cache the compiled output on disk and reuse it across runs. The compiled output is specific to the host CPU architecture, but the `.wasm` bytecode itself remains portable.

Because `.wasm` operations target a virtual CPU rather than a specific physical one, the same `.wasm` file is portable across any OS and architecture. And because execution goes through the runtime, the runtime can enforce strict safety guarantees like memory isolation.

## Comparison to other virtual machines

Wasm is often called a "virtual machine," but it's a different kind than the hypervisor kind (VMware, VirtualBox, EC2). Those virtualize an entire computer — CPU, memory, disk, network — so a full guest OS can boot inside. Wasm virtualizes just a CPU — the virtual CPU described above — with no OS, no virtual disk, and no boot process. Python works the same way — `.pyc` files are bytecode for CPython's virtual machine. The JVM (Java Virtual Machine) is another example.

## Browser and standalone runtimes

Wasm was originally created for web browsers, where the runtime is built into the browser itself. But the same properties turned out to be valuable outside of browsers too. Standalone runtimes like [Wasmtime](https://wasmtime.dev/) can run `.wasm` modules outside the browser — either as standalone programs (the module has a `main` entry point and the runtime runs it) or as libraries (the host program loads the module and calls its exported functions on demand).
