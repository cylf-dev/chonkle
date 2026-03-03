# Linear Memory

Wasm modules have their own linear memory — a flat, contiguous byte array. The host can read and write this memory to pass data across the Wasm boundary, but the module cannot access host memory. All pointers within a Wasm module are offsets into this byte array, not host memory addresses.

## Memory growth

Linear memory starts at a fixed initial size (typically a few 64 KiB pages). When the module needs more memory — for example, when `malloc` can't satisfy a request from the existing heap — the memory must grow.

### How it works

The WASI libc bundled into the `.wasm` binary includes a `dlmalloc` allocator. It manages a freelist within linear memory and handles growth automatically:

1. C code calls `malloc(size)`.
2. `dlmalloc` checks its freelist. If it can satisfy the request from already-available space, it does.
3. If not, `dlmalloc` calls `memory.grow(n_pages)` — a WebAssembly instruction that appends `n` 64 KiB pages to the end of linear memory.
4. The Wasm runtime (wasmtime, wasmer, etc.) fulfills the growth. If the module declares a maximum memory size and the request exceeds it, `memory.grow` returns -1 and `malloc` returns `NULL`.

Because a WASI module is built with libc, all of this is handled transparently — the C code just calls `malloc`/`free` and never deals with `memory.grow` directly. Without WASI libc (a bare `wasm32-unknown` target), you would need to call `__builtin_wasm_memory_grow(0, n_pages)` yourself or have the host grow the memory from outside.

### Growth is contiguous and append-only

`memory.grow` always appends pages to the end of linear memory. The entire address space remains one flat, contiguous byte array. Existing addresses and pointers are never invalidated by growth — new pages simply appear at higher addresses. This is simpler than native virtual memory, where `mmap` might return non-contiguous regions.

However, linear memory **never shrinks** during the lifetime of an instance. `free` returns memory to `dlmalloc`'s freelist for reuse, but no pages are released back to the host. This means a module's memory high-water mark stays elevated after processing a large input. Long-lived instances can accumulate memory over time, which is one reason some designs spin up fresh instances per task.

## Performance considerations

- **`memory.grow` is not free** — the runtime may need to zero-initialize new pages or update internal bookkeeping. Growing one page at a time in a loop is slower than growing many pages at once. `dlmalloc` handles this reasonably by requesting pages in batches.
- **`realloc` may copy** — if `dlmalloc` can't extend an allocation in place, `realloc` allocates a new block, copies the data, and frees the old one. For large buffers this is expensive and temporarily uses 2x the memory.
- **Preallocation helps** — when the output size is known, allocating once at the correct size avoids both realloc copies and incremental `memory.grow` calls.

## Strategies for dynamically growing output buffers

Some codecs — particularly compression or variable-length encoding — don't know the output size in advance. In those cases, the buffer must grow dynamically during processing. Common strategies:

- **Geometric growth** — start with a reasonable initial size and double the buffer (via `realloc`) each time it fills up. This amortizes the cost of copies to O(1) per element over time, at the expense of up to 2x memory overallocation. This is the standard approach used by dynamic arrays (e.g., C++ `std::vector`). Each `realloc` that can't extend in place will copy the entire buffer to a new location, so the tradeoff is fewer, larger copies versus many small allocations.
- **Worst-case preallocation** — if you can bound the maximum output size (e.g., LZ4 defines a maximum expansion ratio), allocate that size upfront and truncate or `realloc` down at the end. This avoids all intermediate copies but may waste memory if the bound is loose.

Since a Wasm host reads output as a contiguous region at a single pointer+length, the output must ultimately be a single contiguous buffer in linear memory. Geometric growth is the typical choice when the final size isn't known upfront.
