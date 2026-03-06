"""Load and run Wasm codec modules via the generic codec ABI."""

import json
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

import wasmtime
import wasmtime.component

from chonkle.wasm_download import download_https, download_oci

# Wasm binary format: 4-byte magic followed by 4-byte version field.
_WASM_MAGIC = b"\x00asm"
_CORE_VERSION = b"\x01\x00\x00\x00"
_COMPONENT_VERSION = b"\x0d\x00\x01\x00"


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


def _is_wasm_component(path: Path) -> bool:
    """Return True if the file is a Wasm Component, False if a Core module."""
    with path.open("rb") as f:
        header = f.read(8)
    if len(header) < 8 or header[:4] != _WASM_MAGIC:
        msg = f"Not a valid Wasm binary: {path}"
        raise ValueError(msg)
    version = header[4:]
    if version == _CORE_VERSION:
        return False
    if version == _COMPONENT_VERSION:
        return True
    msg = f"Unknown Wasm format (version bytes {version.hex()!r}): {path}"
    raise ValueError(msg)


def _wasm_core_call(
    wasm_path: Path,
    export_name: str,
    data: bytes,
    configuration: dict,
) -> bytes:
    """Call a Core Wasm codec export and return the result bytes.

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


def _wasm_component_call(
    wasm_path: Path,
    export_name: str,
    data: bytes,
    configuration: dict,
) -> bytes:
    """Call a Wasm Component Model codec export and return the result bytes.

    The component must export a function named 'encode' or 'decode' (either
    directly at the world level or within an interface) with signature:

        func(data: list<u8>, config: string) -> result<list<u8>, string>

    componentize-py components built against WASIp2 are supported.
    """
    resolved = wasm_path.resolve()
    config = wasmtime.Config()
    config.cache = True
    engine = wasmtime.Engine(config)

    component = wasmtime.component.Component.from_file(engine, str(resolved))

    store = wasmtime.Store(engine)
    # WASIp2 components require both set_wasi and add_wasip2.
    store.set_wasi(wasmtime.WasiConfig())
    linker = wasmtime.component.Linker(engine)
    linker.add_wasip2()

    instance = linker.instantiate(store, component)

    # Discover the export: check world-level functions first, then interfaces.
    comp_exports = component.type.exports(engine)
    fn_idx = None

    for iface_name, item in comp_exports.items():
        if isinstance(item, wasmtime.component.FuncType) and iface_name == export_name:
            fn_idx = instance.get_export_index(store, iface_name)
            break

    if fn_idx is None:
        for iface_name, item in comp_exports.items():
            if isinstance(item, wasmtime.component.ComponentInstanceType):
                iface_exports = item.exports(engine)
                if export_name in iface_exports and isinstance(
                    iface_exports[export_name], wasmtime.component.FuncType
                ):
                    iface_idx = instance.get_export_index(store, iface_name)
                    fn_idx = instance.get_export_index(store, export_name, iface_idx)
                    break

    if fn_idx is None:
        msg = (
            f"Wasm component is missing required export '{export_name}'. "
            f"Component at {resolved}"
        )
        raise RuntimeError(msg)

    fn = instance.get_func(store, fn_idx)
    if fn is None:
        msg = (
            f"Wasm component export '{export_name}' is not a function. "
            f"Component at {resolved}"
        )
        raise RuntimeError(msg)
    config_str = json.dumps(configuration)
    result = fn(store, data, config_str)
    fn.post_return(store)

    # result<list<u8>, string> returns bytes on Ok or str on Err.
    if isinstance(result, str):
        msg = f"Wasm component {export_name} returned error: {result}"
        raise RuntimeError(msg)

    return result


def _wasm_call(
    wasm_path: Path,
    export_name: str,
    data: bytes,
    configuration: dict,
) -> bytes:
    """Dispatch to the Core or Component call path based on the binary header."""
    if _is_wasm_component(wasm_path):
        return _wasm_component_call(wasm_path, export_name, data, configuration)
    return _wasm_core_call(wasm_path, export_name, data, configuration)


def wasm_decode(wasm_path: Path, data: bytes, configuration: dict) -> bytes:
    """Decode 'data' using the Wasm codec module at 'wasm_path'."""
    return _wasm_call(wasm_path, "decode", data, configuration)


def wasm_encode(wasm_path: Path, data: bytes, configuration: dict) -> bytes:
    """Encode 'data' using the Wasm codec module at 'wasm_path'."""
    return _wasm_call(wasm_path, "encode", data, configuration)
