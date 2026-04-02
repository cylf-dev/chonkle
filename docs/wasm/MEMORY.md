# Linear Memory

Wasm modules have their own linear memory — a flat, contiguous byte array. The host can read and write this memory to pass data across the Wasm boundary, but the module cannot access host memory. All pointers within a Wasm module are offsets into this byte array, not host memory addresses.

## Memory growth

Linear memory starts at a fixed initial size (typically a few 64 KiB pages). When the module needs more memory — for example, when a memory allocator can't satisfy a request from the existing heap — the memory must grow.

### How it works

Each module includes an allocator (provided by its language runtime) that manages a heap within linear memory. When the allocator cannot satisfy a request from existing space, it calls `memory.grow(n)` — a WebAssembly instruction that appends `n` 64 KiB pages to the end of linear memory. Those pages are always blank: the Wasm spec guarantees a module can never read data left behind by prior allocations or by the host process. If the module declares a maximum memory size and the request would exceed it, `memory.grow` returns -1 and the allocation fails.

This mechanism is the same regardless of source language. Rust, C, Go, and others each bring their own allocator, but all ultimately rely on `memory.grow` when the heap needs to expand.

### Initial memory size

The initial number of pages is not set by the host at runtime — it is declared in the Wasm binary's `memory` section, written there by the codec's build toolchain at compile time. When wasmtime instantiates the component, it allocates exactly that many pages upfront.

Most toolchains default to a small initial size (typically 1–16 pages, or 64 KiB–1 MiB). This means the first call to a codec with a large input will almost always trigger `memory.grow` to accommodate the data being passed in. A codec author can raise the initial size via build flags or linker configuration if the typical input size is known, avoiding that first growth at the cost of higher baseline memory use.

### Growth is contiguous and append-only

`memory.grow` always appends pages to the end of linear memory. The entire address space remains one flat, contiguous byte array. Existing addresses and pointers are never invalidated by growth — new pages simply appear at higher addresses. This is simpler than native virtual memory, where `mmap` might return non-contiguous regions.

However, linear memory **never shrinks** during the lifetime of an instance. Freed memory returns to the allocator's internal freelist for reuse, but no pages are released back to the host. This means a module's memory high-water mark stays elevated after processing a large input. Long-lived instances can accumulate memory over time, which is one reason some designs spin up fresh instances per task.

## Performance considerations

- **`memory.grow` has a fixed cost per call** — many small growths add up.
- **Reallocating may copy** — if an allocation cannot be extended in place, the allocator allocates a new block, copies the data, and frees the old one. For large buffers this is expensive and temporarily uses 2x the memory.
- **Preallocation helps** — when the output size is known, a codec can allocate the output buffer at the right size before processing begins, avoiding both copy-on-realloc and repeated `memory.grow` calls. The codec computes the size from inputs available in the port-map (dimensions, sample size, etc.) before any processing begins.

## Strategies for dynamically growing output buffers

Some codecs — particularly compression or variable-length encoding — don't know the output size in advance. In those cases, the buffer must grow dynamically during processing. Common strategies:

- **Geometric growth** — start with a reasonable initial size and double the buffer each time it fills up. Because copies happen rarely, the total copying work stays proportional to the final output size, at the expense of up to 2x memory overallocation.
- **Worst-case preallocation** — if you can bound the maximum output size (e.g., LZ4 defines a maximum expansion ratio), allocate that size upfront and trim at the end. This avoids all intermediate copies but may waste memory if the bound is loose.

Since a Wasm host reads output as a contiguous region at a single pointer+length, the output must ultimately be a single contiguous buffer in linear memory. Geometric growth is the typical choice when the final size isn't known upfront.
