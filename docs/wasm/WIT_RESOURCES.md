# Component Model Resources

Resources are a distinct category of WIT type. Unlike a `string` or a `list<u8>`,
a resource is not data you copy and pass around — it is an object that lives in one
place and you hold a *handle* to it. A handle is a 32-bit integer index into a private
table maintained by the runtime. Passing handles between components is cheap; the
underlying data does not move.

## Defining a resource in WIT

A resource is defined inside an interface, which is exported from a world:

```wit
package a:comp@0.1.0;

interface blobs {
    resource blob {
        constructor(data: list<u8>);
        as-bytes: func() -> list<u8>;
        size: func() -> u64;
    }
}

world a {
    export blobs;
}
```

When a function returns a `blob`, it returns a handle, not the
bytes themselves. The bytes stay inside the component that owns the resource.

## Own vs borrow

Every handle is either *owned* or *borrowed*. This determines who is responsible for
the resource's eventual cleanup.

**Owning a handle** means you are responsible for eventually returning it. When an owned
handle is dropped (goes out of scope or is explicitly released), the runtime calls the
resource's destructor — the cleanup function that frees whatever the resource was holding.

**Borrowing a handle** is a temporary loan for the duration of a function call. You can
call methods on it, but you cannot keep it, transfer it, or drop the underlying resource.
When the function call ends, the borrow automatically expires. The original owner retains
the handle.

In WIT, the distinction appears in function signatures:

```wit
// Returns an owned handle — the caller now owns this blob
make-blob: func(data: list<u8>) -> blob;

// Takes a borrowed handle — only needs it for this call
measure: func(b: borrow<blob>) -> u64;

// Takes ownership — the callee is now responsible for cleanup
consume: func(b: blob);
```

Method signatures always desugar to `borrow<self>` — calling a method on a resource
does not transfer ownership. Constructors always return an owned handle.

## Destructors

A resource can declare a destructor — a function the runtime calls automatically when
an owned handle is dropped. This is where cleanup happens: closing a file, freeing
memory, ending a network connection.

If no destructor is declared, dropping the handle simply removes the entry from the
handle table. Any memory the resource was holding in the component's linear memory is
not reclaimed — the Component Model has no implicit garbage collection. For short-lived
component instances this may be acceptable, but within a long-running instance a
resource without a destructor that frees its backing memory is a leak.

The runtime guarantees the destructor runs when the owned handle is dropped, even if
the drop is implicit. A borrowed handle being dropped does *not* trigger the destructor.
Only the owner's final drop does.

## The handle table

Each component instance maintains its own private **handle table** — a list of all the
resources it currently holds handles to. Handles are indices into this table. Index `3`
in component A's table is unrelated to index `3` in component B's table.

**A handle from one component is meaningless to another.** The runtime is the only thing
that knows the mapping; a component cannot forge a handle or access a resource it was
never given.

When an owned handle crosses a component boundary, the runtime removes the entry from
the sender's table and adds a new entry to the receiver's table — the integer index may
change, but both point at the same underlying resource. For a `borrow<blob>`, the
original entry stays in the owner's table and the runtime creates a temporary entry in
the borrower's table that expires when the call returns.

## Sharing resources between components

For a handle to be usable across a component boundary, both components must use the
resource type from the *same* interface definition. There are two patterns for
achieving this.

### Pattern 1: upstream component defines the resource

Component A defines and exports the `blob` resource. Component B imports A's interface
and uses the type. B's WIT requires two things: a `use` statement (type identity) and
an `import` in the world (runtime instance dependency):

```wit
package b:comp@0.1.0;

interface transform {
    use a:comp/blobs@0.1.0.{blob};    // blob here is the same type as blob from A

    process: func(input: borrow<blob>) -> list<u8>;
}

world b {
    import a:comp/blobs@0.1.0;        // B must be linked to the same A instance
    export transform;
}
```

`use` tells the WIT compiler that `blob` in `process`'s signature is A's type.
`import` in the world tells the linker that B requires A's interface to be wired in
at compose time, and that handle tables for `blob` are shared with that A instance.

**The constraint this creates**: B's WIT hardcodes a dependency on A's specific package.
B must know at compile time which upstream component it will receive blobs from.

### Pattern 2: shared third component defines the resource

A third "buffer-store" component defines and exports the `blob` resource. Both A and B
import from the same instance of that component, so a handle produced by A is valid
when passed to B — they share the same handle table.

```wit
// buffer-store: defines the shared blob type
package buffer:store@0.1.0;

interface blobs {
    resource blob {
        constructor(data: list<u8>);
        as-bytes: func() -> list<u8>;
        size: func() -> u64;
    }
}

world buffer-store {
    export blobs;
}
```

```wit
// Component A: creates blobs
package a:comp@0.1.0;

interface produce {
    use buffer:store/blobs@0.1.0.{blob};
    make: func(data: list<u8>) -> blob;
}

world a {
    import buffer:store/blobs@0.1.0;    // same interface as B
    export produce;
}
```

```wit
// Component B: consumes blobs
package b:comp@0.1.0;

interface transform {
    use buffer:store/blobs@0.1.0.{blob};
    process: func(input: borrow<blob>) -> list<u8>;
}

world b {
    import buffer:store/blobs@0.1.0;    // same interface as A
    export transform;
}
```

Both world declarations import `buffer:store/blobs@0.1.0`. When composition wires A and
B to the **same instance** of the buffer-store, handles A produces are valid in B because
they reference the same handle table. Sharing the interface name is necessary but not
sufficient: A and B must be wired to the same actual instance, not two separate
buffer-stores that happen to export identical interfaces.

If two components each define their own `blob` type independently — even with identical
WIT text — those types are different, and passing a handle from one to the other fails
at link time.

## Resources in practice: WASI file descriptors

The most common real-world use of resources is WASI's file descriptor. In
`wasi:filesystem/types`, a `descriptor` is a resource:

```wit
resource descriptor {
    read-via-stream: func(offset: filesize) -> result<input-stream, error-code>;
    write-via-stream: func(offset: filesize) -> result<output-stream, error-code>;
    stat: func() -> result<descriptor-stat, error-code>;
    // ...
}
```

The host provides this resource. When a component opens a file, it receives an owned
`descriptor` handle — a number. The actual file handle lives in the host's table. The
component can read, write, and stat by calling methods through that number. When the
component drops the descriptor, the host's destructor runs and the file is closed.

The guest never sees the underlying OS file descriptor. It only sees its own handle
table index. WASI streams, clocks, sockets, and most other WASI abstractions work
the same way.

## Lift, lower, and copy counts

"Lifting" is reading a value out of a component's linear memory into a representation the host can work with. "Lowering" is the inverse: writing a value from the host into a component's linear memory. Every cross-component value transfer involves a lift followed by a lower.

For a `list<u8>` payload, the lift materializes the data into a host-side buffer (copy 1: source component → host); the lower writes from that buffer into the destination component's memory (copy 2: host → destination component). In an orchestrated pipeline — where a host program routes data between independently compiled components — this occurs at every step boundary:

```text
component A runs
  → lift: A's memory → host          (copy 1)
orchestrator routes
  → lower: host → B's memory         (copy 2)
component B runs
```

This gives **2 copies per edge** for value types like `list<u8>` in any orchestrated component pipeline.

## Resources do not reduce copies in orchestrated pipelines

**Pattern 1 requires static coupling.** For B to accept a `blob` handle from A, B's WIT must contain `use a:comp/blobs@0.1.0.{blob}` — it must name A's package at compile time. In any pipeline where components are selected or assembled at runtime, no component knows its neighbors when it is compiled. Pattern 1 requires each component to hardcode its upstream neighbor in its WIT, which is incompatible with a generic plugin model.

**Pattern 2 resources give the same copy count as value passing, through a different mechanism.** With `list<u8>`, data surfaces in the host at every step boundary — the orchestrator is in the data path. With Pattern 2 resources, the orchestrator only exchanges 32-bit handles and data never surfaces in the host, but data still crosses two component boundaries per edge: A calls `blob.constructor(data)`, copying its output into the buffer-store (copy 1: A's memory → buffer-store's memory), and B calls `blob.as-bytes()`, copying the data back out (copy 2: buffer-store's memory → B's memory). The buffer-store replaces the host as the intermediate stop, but the boundary count is the same:

| Approach | Copies per edge |
| --- | --- |
| `list<u8>` with orchestrator | 2 |
| Resources, Pattern 2 (shared buffer-store) | 2 |
| Resources, Pattern 1 (direct composition) | 1 — requires static compile-time coupling |

The only path to 1 copy per edge is direct composition (Pattern 1), which eliminates the intermediate component from the data path but requires static compile-time coupling between components.
