"""Load and run Wasm codec modules via the generic codec ABI."""

import json
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

import wasmtime

from chonkle.wasm_download import download_https, download_oci


def resolve_wasm_uri(uri: str, *, force_download: bool = False) -> Path:
    """Resolve a Wasm URI to an absolute file path.

    Supported URI forms:

    - file:///path/to/codec.wasm (local file)
    - https://… (HTTPS download, cached)
    - oci://ghcr.io/org/repo:tag (OCI registry pull, cached)
    """
    parsed = urlparse(uri)

    if parsed.scheme == "file":
        return Path(parsed.path)

    if parsed.scheme == "http":
        msg = "HTTP is not supported for Wasm downloads; use HTTPS instead"
        raise ValueError(msg)

    if parsed.scheme == "https":
        return download_https(uri, force=force_download)

    if parsed.scheme == "oci":
        return download_oci(uri, force=force_download)

    msg = (
        f"Unsupported URI scheme: {parsed.scheme!r}"
        if parsed.scheme
        else f"URI must include a scheme (e.g. file://, https://, oci://): {uri!r}"
    )
    raise NotImplementedError(msg)


def _wasm_call(
    wasm_path: Path,
    export_name: str,
    data: bytes,
    configuration: dict,
) -> bytes:
    """Call a Wasm codec export and return the result bytes.

    The module must export the generic Wasm codec ABI:

    - alloc(size: i32) -> i32
    - dealloc(ptr: i32, size: i32)
    - <export_name>(input_ptr, input_len, config_ptr, config_len) -> i64
    - memory

    Both freestanding (no imports) and WASI modules are supported.
    """
    resolved = wasm_path.resolve()
    config = wasmtime.Config()
    config.cache = True
    engine = wasmtime.Engine(config)
    module = wasmtime.Module.from_file(engine, str(resolved))
    store = wasmtime.Store(engine)

    if module.imports:
        wasi_config = wasmtime.WasiConfig()
        store.set_wasi(wasi_config)
        linker = wasmtime.Linker(engine)
        linker.define_wasi()
        instance = linker.instantiate(store, module)
    else:
        instance = wasmtime.Instance(store, module, [])

    # Get exported functions and memory.
    exports = instance.exports(store)

    # Validate required ABI exports before accessing them.
    required = ["alloc", "dealloc", "memory", export_name]
    missing = [name for name in required if exports.get(name) is None]
    if missing:
        msg = (
            f"Wasm module is missing required ABI export(s): {', '.join(missing)}. "
            f"Module at {resolved}"
        )
        raise RuntimeError(msg)

    # WASI reactors export _initialize for runtime setup.
    initialize_fn = exports.get("_initialize")
    if initialize_fn is not None:
        cast(wasmtime.Func, initialize_fn)(store)
    alloc_fn = cast(wasmtime.Func, exports["alloc"])
    dealloc_fn = cast(wasmtime.Func, exports["dealloc"])
    codec_fn = cast(wasmtime.Func, exports[export_name])
    memory = cast(wasmtime.Memory, exports["memory"])

    # Write input data into Wasm linear memory.
    input_len = len(data)
    input_ptr = alloc_fn(store, input_len)
    memory.write(store, data, input_ptr)

    # Write configuration JSON into Wasm linear memory.
    config_bytes = json.dumps(configuration).encode()
    config_len = len(config_bytes)
    config_ptr = alloc_fn(store, config_len)
    memory.write(store, config_bytes, config_ptr)

    # Call the codec function.
    result_i64 = codec_fn(store, input_ptr, input_len, config_ptr, config_len)

    # Unpack (output_ptr << 32) | output_len.
    out_ptr = (result_i64 >> 32) & 0xFFFFFFFF
    out_len = result_i64 & 0xFFFFFFFF

    if out_ptr == 0 and out_len == 0:
        msg = f"Wasm {export_name} returned null (bad configuration?)"
        raise RuntimeError(msg)

    # Read output from Wasm memory.
    output = memory.read(store, out_ptr, out_ptr + out_len)

    # Free all Wasm-side allocations.
    dealloc_fn(store, input_ptr, input_len)
    dealloc_fn(store, config_ptr, config_len)
    dealloc_fn(store, out_ptr, out_len)

    return bytes(output)


def wasm_decode(wasm_path: Path, data: bytes, configuration: dict) -> bytes:
    """Decode 'data' using the Wasm codec module at 'wasm_path'."""
    return _wasm_call(wasm_path, "decode", data, configuration)


def wasm_encode(wasm_path: Path, data: bytes, configuration: dict) -> bytes:
    """Encode 'data' using the Wasm codec module at 'wasm_path'."""
    return _wasm_call(wasm_path, "encode", data, configuration)
