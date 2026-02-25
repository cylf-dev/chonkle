"""Load and run WASM codec modules via the generic codec ABI."""

import json
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

import wasmtime


def resolve_wasm_uri(uri: str) -> Path:
    """Resolve a WASM URI to an absolute file path.

    Currently only ``file://`` URIs are supported.  Other schemes (e.g.
    ``oci://``) will be added as registry support is implemented.
    """
    parsed = urlparse(uri)

    if parsed.scheme == "file":
        return Path(parsed.path)

    msg = (
        f"Unsupported URI scheme: {parsed.scheme!r}"
        if parsed.scheme
        else (f"URI must include a scheme (e.g. file://): {uri!r}")
    )
    raise NotImplementedError(msg)


def wasm_decode(wasm_path: Path, data: bytes, configuration: dict) -> bytes:
    """Decode *data* using the WASM codec module at *wasm_path*.

    The module must export the generic WASM codec ABI:

    * ``alloc(size: i32) -> i32``
    * ``dealloc(ptr: i32, size: i32)``
    * ``decode(input_ptr, input_len, config_ptr, config_len) -> i64``
    * ``memory``
    """
    resolved = wasm_path.resolve()
    engine = wasmtime.Engine()
    module = wasmtime.Module.from_file(engine, str(resolved))
    store = wasmtime.Store(engine)
    instance = wasmtime.Instance(store, module, [])

    # Get exported functions and memory.
    exports = instance.exports(store)
    alloc_fn = cast(wasmtime.Func, exports["alloc"])
    dealloc_fn = cast(wasmtime.Func, exports["dealloc"])
    decode_fn = cast(wasmtime.Func, exports["decode"])
    memory = cast(wasmtime.Memory, exports["memory"])

    # Write input data into WASM linear memory.
    input_len = len(data)
    input_ptr = alloc_fn(store, input_len)
    memory.write(store, data, input_ptr)

    # Write configuration JSON into WASM linear memory.
    config_bytes = json.dumps(configuration).encode()
    config_len = len(config_bytes)
    config_ptr = alloc_fn(store, config_len)
    memory.write(store, config_bytes, config_ptr)

    # Call decode.
    result_i64 = decode_fn(store, input_ptr, input_len, config_ptr, config_len)

    # Unpack (output_ptr << 32) | output_len.
    out_ptr = (result_i64 >> 32) & 0xFFFFFFFF
    out_len = result_i64 & 0xFFFFFFFF

    if out_ptr == 0 and out_len == 0:
        msg = "WASM decode returned null (bad configuration?)"
        raise RuntimeError(msg)

    # Read output from WASM memory.
    output = memory.read(store, out_ptr, out_ptr + out_len)

    # Free all WASM-side allocations.
    dealloc_fn(store, input_ptr, input_len)
    dealloc_fn(store, config_ptr, config_len)
    dealloc_fn(store, out_ptr, out_len)

    return bytes(output)
