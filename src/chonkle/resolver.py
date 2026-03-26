"""Codec resolution: maps codec_ids to Codec instances.

The Resolver handles finding, downloading, and instantiating codec
implementations for a pipeline. It replaces direct URI resolution with
a local codec store indexed by codec_id and content hash.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import wasmtime

from chonkle.codecs._base import SIGNATURES_DIR, Codec, detect_codec_type
from chonkle.codecs.component import ComponentCodec
from chonkle.codecs.core import CoreWasmCodec
from chonkle.codecs.native import NativeCodec
from chonkle.wasm_signature import read_signature


def _default_store_path() -> Path:
    """Return the default local codec store path.

    Uses ``CHONKLE_CODEC_STORE`` if set, otherwise ``~/.chonkle/codecs``.
    """
    override = os.environ.get("CHONKLE_CODEC_STORE", "")
    if override:
        return Path(override)
    return Path.home() / ".chonkle" / "codecs"


@dataclass
class CodecEntry:
    """Metadata about a codec implementation in the local store."""

    path: Path
    codec_id: str
    implementation: str
    codec_type: str  # "component" or "core"


class Resolver:
    """Maps codec_ids to Codec instances using a resolution chain.

    Resolution order:

    1. Explicit paths (if provided via ``paths``)
    2. Per-codec overrides (specific implementation name)
    3. Local store (selected by backend preference)
    4. Pipeline sources (download and install)

    Args:
        codec_store: Path to the local codec store directory.
            Defaults to ``CHONKLE_CODEC_STORE`` env var or
            ``~/.chonkle/codecs``.
        preference: Ordered list of backend types to prefer when
            multiple implementations exist for a codec_id.
            Supported values: ``"core"``, ``"component"``, ``"native"``.
        overrides: Maps codec_id to a specific implementation name,
            bypassing the preference system.
        pipeline_sources: Maps codec_id to a download URI (from the
            pipeline's ``sources`` field).
        paths: Maps codec_id to a specific ``.wasm`` file path.
            Useful for testing and ``--codec-path`` CLI usage.
    """

    def __init__(
        self,
        *,
        codec_store: Path | None = None,
        preference: Sequence[str] | None = None,
        overrides: dict[str, str] | None = None,
        pipeline_sources: dict[str, str] | None = None,
        paths: dict[str, Path] | None = None,
    ) -> None:
        self._store_path = codec_store or _default_store_path()
        self._preference = list(preference or ("core", "component", "native"))
        self._overrides = dict(overrides or {})
        self._pipeline_sources = dict(pipeline_sources or {})
        self._paths = dict(paths or {})
        config = wasmtime.Config()
        config.cache = True
        self._engine = wasmtime.Engine(config)
        self._index: dict[str, list[CodecEntry]] | None = None

    @property
    def engine(self) -> wasmtime.Engine:
        """The shared Wasmtime engine used for all codec instantiation."""
        return self._engine

    @property
    def store_path(self) -> Path:
        """Path to the local codec store directory."""
        return self._store_path

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
            return self._instantiate_path(self._paths[codec_id])

        # 1. Per-codec override
        if codec_id in self._overrides:
            return self._resolve_override(codec_id, self._overrides[codec_id])

        # 2. Local store + native: collect candidates and pick by preference
        entries = self._get_index().get(codec_id, [])
        has_native = _has_native_signature(codec_id)
        if entries or has_native:
            return self._resolve_by_preference(codec_id, entries, has_native)

        # 3. Pipeline sources
        if codec_id in self._pipeline_sources:
            return self._resolve_from_source(codec_id, self._pipeline_sources[codec_id])

        # Not found
        tried = [f"local store ({self._store_path})", "native (numcodecs)"]
        msg = (
            f"No codec implementation found for {codec_id!r}. "
            f"Checked: {', '.join(tried)}"
        )
        raise ValueError(msg)

    def list_codecs(self) -> list[CodecEntry]:
        """List all codec implementations (local store and native)."""
        result: list[CodecEntry] = []
        for entries in self._get_index().values():
            result.extend(entries)
        if SIGNATURES_DIR.exists():
            for sig_file in sorted(SIGNATURES_DIR.glob("*.json")):
                sig = json.loads(sig_file.read_text())
                cid = sig.get("codec_id", sig_file.stem)
                result.append(
                    CodecEntry(
                        path=sig_file,
                        codec_id=cid,
                        implementation=sig.get("implementation", f"numcodecs.{cid}"),
                        codec_type="native",
                    )
                )
        return result

    def _get_index(self) -> dict[str, list[CodecEntry]]:
        if self._index is None:
            self._index = _scan_store(self._store_path)
        return self._index

    def _resolve_by_preference(
        self,
        codec_id: str,
        entries: list[CodecEntry],
        has_native: bool,
    ) -> Codec:
        """Select the best implementation for *codec_id* using the preference list.

        Considers both wasm entries from the local store and the native
        (numcodecs) backend if a signature file exists.
        """
        for pref in self._preference:
            if pref == "native" and has_native:
                return NativeCodec(codec_id)
            for entry in entries:
                if entry.codec_type == pref:
                    return self._instantiate(entry)
        # Fallback: first wasm entry if available, otherwise native
        if entries:
            return self._instantiate(entries[0])
        return NativeCodec(codec_id)

    def _resolve_override(self, codec_id: str, impl_name: str) -> Codec:
        entries = self._get_index().get(codec_id, [])
        for entry in entries:
            if entry.implementation == impl_name:
                return self._instantiate(entry)
        # Check native
        available = [e.implementation for e in entries]
        sig_path = SIGNATURES_DIR / f"{codec_id}.json"
        if sig_path.exists():
            native_impl = json.loads(sig_path.read_text()).get(
                "implementation", f"numcodecs.{codec_id}"
            )
            if native_impl == impl_name:
                return NativeCodec(codec_id)
            available.append(native_impl)
        msg = (
            f"Override implementation {impl_name!r} for codec {codec_id!r} "
            f"not found. Available: {available}"
        )
        raise ValueError(msg)

    def _instantiate_path(self, wasm_path: Path) -> Codec:
        """Create a Codec from an explicit wasm path."""
        codec_type = detect_codec_type(wasm_path)
        if codec_type == "component":
            return ComponentCodec(self._engine, wasm_path)
        return CoreWasmCodec(self._engine, wasm_path)

    def _instantiate(self, entry: CodecEntry) -> Codec:
        if entry.codec_type == "component":
            return ComponentCodec(self._engine, entry.path)
        return CoreWasmCodec(self._engine, entry.path)

    def _resolve_from_source(self, codec_id: str, uri: str) -> Codec:
        from chonkle.wasm_download import resolve_uri

        wasm_path = resolve_uri(uri)
        return self._install_and_instantiate(wasm_path)

    def _install_and_instantiate(self, wasm_path: Path) -> Codec:
        """Install a downloaded wasm file into the store and instantiate."""
        sig = read_signature(wasm_path)
        codec_id = sig.get("codec_id", wasm_path.stem)
        codec_type = detect_codec_type(wasm_path)

        content_hash = hashlib.sha256(wasm_path.read_bytes()).hexdigest()[:16]
        dest_dir = self._store_path / codec_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{content_hash}.wasm"

        if not dest.exists():
            shutil.copy2(wasm_path, dest)

        entry = CodecEntry(
            path=dest,
            codec_id=codec_id,
            implementation=sig.get("implementation", ""),
            codec_type=codec_type,
        )
        self._get_index().setdefault(codec_id, []).append(entry)
        return self._instantiate(entry)


def _has_native_signature(codec_id: str) -> bool:
    """Check whether a native codec signature file exists for *codec_id*."""
    return (SIGNATURES_DIR / f"{codec_id}.json").exists()


def _scan_store(store_path: Path) -> dict[str, list[CodecEntry]]:
    """Scan the local codec store and return entries indexed by codec_id."""
    index: dict[str, list[CodecEntry]] = {}
    if not store_path.exists():
        return index
    for codec_dir in sorted(store_path.iterdir()):
        if not codec_dir.is_dir():
            continue
        for wasm_file in sorted(codec_dir.glob("*.wasm")):
            try:
                sig: dict[str, Any] = read_signature(wasm_file)
                codec_type = detect_codec_type(wasm_file)
            except (ValueError, OSError):
                continue
            entry = CodecEntry(
                path=wasm_file,
                codec_id=sig.get("codec_id", codec_dir.name),
                implementation=sig.get("implementation", ""),
                codec_type=codec_type,
            )
            index.setdefault(entry.codec_id, []).append(entry)
    return index
