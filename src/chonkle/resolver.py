"""Codec resolution: maps codec_ids to Codec instances.

The Resolver handles finding, downloading, and instantiating codec
implementations for a pipeline. It maintains a local codec store
indexed by codec_id and implementation name. Set
``CHONKLE_FORCE_INSTALL=1`` to overwrite an existing store entry when
re-installing a codec from a source URI.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import wasmtime

from chonkle.codecs._base import SIGNATURES_DIR, Backend, Codec
from chonkle.codecs.component import ComponentCodec
from chonkle.codecs.core import CoreWasmCodec
from chonkle.codecs.native import NativeCodec
from chonkle.wasm_signature import Signature, detect_codec_type


def _default_store_path() -> Path:
    """Return the default local codec store path."""
    override = os.environ.get("CHONKLE_CODEC_STORE", "")
    if override:
        return Path(override)
    return Path.home() / ".chonkle" / "codecs"


@dataclass(frozen=True)
class CodecInfo:
    """Metadata about a codec implementation."""

    codec_id: str
    implementation: str
    backend: Backend
    location: Path  # .wasm for wasm entries, .json for native


class CodecStore:
    """Local codec store: scan, install, and list wasm codec entries."""

    def __init__(self, store_path: Path) -> None:
        self._store_path = store_path
        self._index: dict[str, dict[str, CodecInfo]] | None = None

    @property
    def path(self) -> Path:
        return self._store_path

    def get_index(self) -> dict[str, dict[str, CodecInfo]]:
        """Return the codec index, scanning the store on first access."""
        if self._index is None:
            self._index = _scan_store(self._store_path)
        return self._index

    def install(self, wasm_path: Path, *, force_install: bool = False) -> CodecInfo:
        """Install a wasm file into the store and return its entry."""
        sig = Signature.from_wasm(wasm_path)
        backend = detect_codec_type(wasm_path)

        impl_name = _sanitize_filename(sig.implementation or wasm_path.stem)
        dest_dir = self._store_path / sig.codec_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{impl_name}.wasm"

        if not dest.exists() or force_install or _should_force_install():
            shutil.copy2(wasm_path, dest)

        entry = CodecInfo(
            codec_id=sig.codec_id,
            implementation=sig.implementation,
            backend=backend,
            location=dest,
        )
        self._index = None  # invalidate so next access re-scans
        return entry

    def list_entries(self) -> list[CodecInfo]:
        """Return all wasm entries in the store."""
        result: list[CodecInfo] = []
        for entries in self.get_index().values():
            result.extend(entries.values())
        return result


class Resolver:
    """Maps codec_ids to Codec instances using a resolution chain.

    Resolution order:

    1. Explicit paths (if provided via ``paths``)
    2. Force sources (download from URI regardless of store)
    3. Per-codec overrides (specific implementation name)
    4. Local store (selected by backend preference)
    5. Pipeline sources (download and install)

    Args:
        codec_store: Path to the local codec store directory.
            Defaults to ``CHONKLE_CODEC_STORE`` env var or
            ``~/.chonkle/codecs``.
        preference: Ordered list of backend types to prefer when
            multiple implementations exist for a codec_id.
            Supported values: ``"core"``, ``"component"``, ``"native"``.
        overrides: Maps codec_id to a specific implementation name,
            bypassing the preference system.
        force_sources: Maps codec_id to a download URI. Unlike
            ``pipeline_sources``, these are fetched unconditionally,
            bypassing the local store. The fetched binary is
            installed into the store (overwriting any existing
            entry with the same implementation name).
        pipeline_sources: Maps codec_id to a download URI (from the
            pipeline's ``sources`` field).
        paths: Maps codec_id to a specific ``.wasm`` file path.
            Useful for testing and ``--codec-path`` CLI usage.
        engine: Shared Wasmtime engine for codec instantiation.
            If not provided, a new engine with compilation caching
            is created.
    """

    def __init__(
        self,
        *,
        codec_store: Path | None = None,
        preference: Sequence[str] | None = None,
        overrides: dict[str, str] | None = None,
        force_sources: dict[str, str] | None = None,
        pipeline_sources: dict[str, str] | None = None,
        paths: dict[str, Path] | None = None,
        engine: wasmtime.Engine | None = None,
    ) -> None:
        self._store = CodecStore(codec_store or _default_store_path())
        self._preference = list(
            preference or (Backend.NATIVE, Backend.CORE, Backend.COMPONENT)
        )
        self._overrides = dict(overrides or {})
        self._force_sources = dict(force_sources or {})
        self._pipeline_sources = dict(pipeline_sources or {})
        self._paths = dict(paths or {})
        if engine is not None:
            self._engine = engine
        else:
            config = wasmtime.Config()
            config.cache = True
            self._engine = wasmtime.Engine(config)

    @property
    def store_path(self) -> Path:
        """Path to the local codec store directory."""
        return self._store.path

    def resolve(self, codec_id: str) -> Codec:
        """Resolve a codec_id to a Codec instance.

        Args:
            codec_id: The logical codec identifier to resolve.

        Returns:
            A ready-to-use Codec instance.

        Raises:
            ValueError: If no implementation is found or the resolved
                binary uses an unsupported backend.
        """
        # 0. Explicit paths (testing, CLI --codec-path)
        if codec_id in self._paths:
            return self._instantiate(self._paths[codec_id])

        # 1. Force sources (download from URI regardless of store)
        if codec_id in self._force_sources:
            return self._resolve_from_source(
                self._force_sources[codec_id], force_install=True
            )

        # 2. Per-codec override
        if codec_id in self._overrides:
            return self._resolve_override(codec_id, self._overrides[codec_id])

        # 3. Local store + native: collect candidates and pick by preference
        entries = self._store.get_index().get(codec_id, {})
        has_native = _has_native_signature(codec_id)
        if entries or has_native:
            return self._resolve_by_preference(codec_id, entries, has_native)

        # 4. Pipeline sources
        if codec_id in self._pipeline_sources:
            return self._resolve_from_source(self._pipeline_sources[codec_id])

        msg = f"No codec implementation found for {codec_id!r}"
        raise ValueError(msg)

    def list_codecs(self) -> list[CodecInfo]:
        """List all codec implementations (local store and native)."""
        result = list(self._store.list_entries())
        result.extend(_list_native_codecs())
        return result

    def _resolve_by_preference(
        self,
        codec_id: str,
        entries: dict[str, CodecInfo],
        has_native: bool,
    ) -> Codec:
        """Select the best implementation for *codec_id* using the preference list.

        Considers both wasm entries from the local store and the native
        (numcodecs) backend if a signature file exists.
        """
        for pref in self._preference:
            if pref == Backend.NATIVE and has_native:
                return NativeCodec(codec_id)
            for entry in entries.values():
                if entry.backend == pref:
                    return self._instantiate(entry.location)
        available = [e.backend for e in entries.values()]
        if has_native:
            available.append(Backend.NATIVE)
        msg = (
            f"No implementation for {codec_id!r} matches preference "
            f"{self._preference}. Available backends: {available}"
        )
        raise ValueError(msg)

    def _resolve_override(self, codec_id: str, impl_name: str) -> Codec:
        entries = self._store.get_index().get(codec_id, {})
        if impl_name in entries:
            return self._instantiate(entries[impl_name].location)
        # Check native
        available = list(entries)
        sig_path = SIGNATURES_DIR / f"{codec_id}.json"
        if sig_path.exists():
            sig = Signature.from_json(sig_path)
            if sig.implementation == impl_name:
                return NativeCodec(codec_id)
            available.append(sig.implementation)
        msg = (
            f"Override implementation {impl_name!r} for codec {codec_id!r} "
            f"not found. Available: {available}"
        )
        raise ValueError(msg)

    def _instantiate(self, wasm_path: Path) -> Codec:
        """Create a Codec from a wasm path."""
        codec_type = detect_codec_type(wasm_path)
        if codec_type == Backend.COMPONENT:
            return ComponentCodec(self._engine, wasm_path)
        return CoreWasmCodec(self._engine, wasm_path)

    def _resolve_from_source(self, uri: str, *, force_install: bool = False) -> Codec:
        from chonkle.wasm_download import resolve_uri

        with resolve_uri(uri) as wasm_path:
            entry = self._store.install(wasm_path, force_install=force_install)
            return self._instantiate(entry.location)


def _should_force_install() -> bool:
    return os.environ.get("CHONKLE_FORCE_INSTALL", "") == "1"


_UNSAFE_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


def _sanitize_filename(name: str) -> str:
    """Replace filesystem-unsafe characters in *name* with underscores."""
    return _UNSAFE_CHARS.sub("_", name)


def _has_native_signature(codec_id: str) -> bool:
    """Check whether a native codec signature file exists for *codec_id*."""
    return (SIGNATURES_DIR / f"{codec_id}.json").exists()


def _list_native_codecs() -> list[CodecInfo]:
    """Return CodecInfo for all native codecs with bundled signatures."""
    if not SIGNATURES_DIR.exists():
        return []
    result: list[CodecInfo] = []
    for sig_file in sorted(SIGNATURES_DIR.glob("*.json")):
        sig = Signature.from_json(sig_file)
        result.append(
            CodecInfo(
                codec_id=sig.codec_id,
                implementation=sig.implementation,
                backend=Backend.NATIVE,
                location=sig_file,
            )
        )
    return result


def _scan_store(store_path: Path) -> dict[str, dict[str, CodecInfo]]:
    """Scan the local codec store and return entries indexed by codec_id."""
    index: dict[str, dict[str, CodecInfo]] = {}
    if not store_path.exists():
        return index
    for codec_dir in sorted(store_path.iterdir()):
        if not codec_dir.is_dir():
            continue
        for wasm_file in sorted(codec_dir.glob("*.wasm")):
            try:
                sig = Signature.from_wasm(wasm_file)
                backend = detect_codec_type(wasm_file)
            except (ValueError, OSError):
                continue
            entry = CodecInfo(
                codec_id=sig.codec_id,
                implementation=sig.implementation,
                backend=backend,
                location=wasm_file,
            )
            index.setdefault(entry.codec_id, {})[entry.implementation] = entry
    return index
