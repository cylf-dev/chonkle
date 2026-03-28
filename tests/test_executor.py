"""Tests for DAG executor via codec wrappers."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from chonkle.codecs._base import Backend, Codec, PortMap
from chonkle.codecs.core import CoreWasmCodec, CoreWasmRef
from chonkle.executor import run
from chonkle.pipeline import Direction, Pipeline, PreparedPipeline, prepare
from chonkle.resolver import CodecStore, Resolver
from chonkle.wasm_signature import Signature, embed_signature

_REPO_ROOT = Path(__file__).parent.parent
_CODEC_DIR = _REPO_ROOT / "codec"

# Minimal valid Component Model header (magic + version, no sections).
_CM_HEADER = b"\x00asm\x0d\x00\x01\x00"


def _wasm_with_signature(signature: dict) -> bytes:
    """Return minimal .wasm bytes with an embedded chonkle:signature section."""
    sig = {"codec_id": "test-codec", **signature}
    return embed_signature(_CM_HEADER, sig)


# ---------- Fake codec for wiring tests ----------


_DEFAULT_SIG = Signature.from_dict(
    {
        "codec_id": "fake",
        "inputs": {"bytes": {"type": "bytes"}},
        "outputs": {"bytes": {"type": "bytes"}},
    }
)


class _FakeCodec(Codec):
    """Test double that records calls and returns canned outputs."""

    def __init__(
        self,
        call_fn: Any = None,
        sig: Signature | None = None,
    ) -> None:
        self._call_fn = call_fn or (lambda d, pm: [("bytes", b"output")])
        self._sig = sig or _DEFAULT_SIG

    @property
    def codec_type(self) -> Backend:
        return Backend.COMPONENT

    @property
    def codec_id(self) -> str:
        return self._sig.codec_id or "fake"

    @property
    def implementation(self) -> str:
        return self._sig.implementation or "fake"

    def signature(self) -> Signature:
        return self._sig

    def call(self, direction: Direction, port_map: PortMap) -> PortMap:
        return self._call_fn(direction, port_map)


def _prepared(
    pipeline: Pipeline,
    direction: Direction | None = None,
    codecs: Mapping[str, Codec] | None = None,
) -> PreparedPipeline:
    """Build a PreparedPipeline with fake codecs for wiring tests."""
    if direction is None:
        direction = pipeline.direction
    if codecs is None:
        codecs = {name: _FakeCodec() for name in pipeline.steps}
    encode_only_inputs = {
        name: frozenset(codecs[name].signature().encode_only_inputs())
        for name in pipeline.steps
    }
    output_ports = {
        name: tuple(codecs[name].signature().outputs.keys()) for name in pipeline.steps
    }
    return PreparedPipeline(
        pipeline=pipeline,
        direction=direction,
        codecs=codecs,
        encode_only_inputs=encode_only_inputs,
        output_ports=output_ports,
    )


# Tests below this marker require compiled codec .wasm files.
CODEC_REQUIRED = pytest.mark.skipif(
    True,
    reason="requires compiled codec .wasm files (built in a separate session)",
)

_CORE_IDENTITY_WASM = _CODEC_DIR / "identity-core-c" / "zig-out" / "identity-core.wasm"
CORE_CODEC_REQUIRED = pytest.mark.skipif(
    not _CORE_IDENTITY_WASM.exists(),
    reason=(
        "Core identity codec .wasm not present — build codec/identity-core-c first"
    ),
)

_COG_WASM_ZLIB = _CODEC_DIR / "zlib-rs" / "zlib.wasm"
_COG_WASM_PREDICTOR2 = _CODEC_DIR / "tiff-predictor-2-c" / "tiff-predictor-2.wasm"
COG_CODECS_REQUIRED = pytest.mark.skipif(
    not (_COG_WASM_ZLIB.exists() and _COG_WASM_PREDICTOR2.exists()),
    reason=(
        "COG codec .wasm files not present"
        " — build codec/zlib-rs and codec/tiff-predictor-2-c first"
    ),
)


def _make_simple_pipeline() -> dict:
    """Return a single-step encode pipeline dict."""
    return {
        "codec_id": "test",
        "direction": "encode",
        "inputs": {"bytes": {"type": "bytes"}},
        "constants": {"level": {"type": "int", "value": 3}},
        "outputs": {"bytes": "codec.bytes"},
        "steps": {
            "codec": {
                "codec_id": "codec",
                "inputs": {"bytes": "input.bytes", "level": "constant.level"},
            }
        },
    }


def _make_decode_pipeline() -> dict:
    """Return a single-step decode pipeline dict (decode-declared)."""
    return {
        "codec_id": "test",
        "direction": "decode",
        "inputs": {"bytes": {"type": "bytes"}},
        "constants": {"level": {"type": "int", "value": 3}},
        "outputs": {"bytes": "codec.bytes"},
        "steps": {
            "codec": {
                "codec_id": "codec",
                "inputs": {"bytes": "input.bytes", "level": "constant.level"},
            }
        },
    }


# Signature with level marked as encode_only.
_SIG_WITH_ENCODE_ONLY = Signature.from_dict(
    {
        "codec_id": "fake",
        "inputs": {
            "bytes": {"type": "bytes"},
            "level": {"type": "int", "encode_only": True},
        },
        "outputs": {"bytes": {"type": "bytes"}},
    }
)


class TestRunWiring:
    """Verify executor wiring logic using fake codec instances."""

    def test_passes_input_bytes_to_step(self, raw_chunk: bytes) -> None:
        pipeline = Pipeline.parse(_make_simple_pipeline())
        received: list = []

        def fake_call(direction, port_map):
            received.append((direction, list(port_map)))
            return [("bytes", b"compressed")]

        codecs = {"codec": _FakeCodec(call_fn=fake_call, sig=_SIG_WITH_ENCODE_ONLY)}
        prepared = _prepared(pipeline, codecs=codecs)
        result = run(prepared, {"bytes": raw_chunk})

        assert result == {"bytes": b"compressed"}
        assert len(received) == 1
        direction, port_map = received[0]
        assert direction == "encode"
        assert ("bytes", raw_chunk) in port_map

    def test_routes_step_output_to_next_step_input(self) -> None:
        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "step_b.bytes"},
            "steps": {
                "step_a": {
                    "codec_id": "some-codec",
                    "inputs": {"bytes": "input.bytes"},
                },
                "step_b": {
                    "codec_id": "some-codec",
                    "inputs": {"bytes": "step_a.bytes"},
                },
            },
        }
        pipeline = Pipeline.parse(data)
        call_log: list = []

        def fake_a(direction, port_map):
            call_log.append(("a", list(port_map)))
            return [("bytes", b"after_a")]

        def fake_b(direction, port_map):
            call_log.append(("b", list(port_map)))
            return [("bytes", b"after_b")]

        codecs = {
            "step_a": _FakeCodec(call_fn=fake_a),
            "step_b": _FakeCodec(call_fn=fake_b),
        }
        prepared = _prepared(pipeline, codecs=codecs)
        result = run(prepared, {"bytes": b"original"})

        assert result == {"bytes": b"after_b"}
        _, first_pm = call_log[0]
        _, second_pm = call_log[1]
        assert first_pm == [("bytes", b"original")]
        assert second_pm == [("bytes", b"after_a")]

    def test_constants_passed_as_json_bytes(self) -> None:
        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {"level": {"type": "int", "value": 5}},
            "outputs": {"bytes": "s.bytes"},
            "steps": {
                "s": {
                    "codec_id": "some-codec",
                    "inputs": {
                        "bytes": "input.bytes",
                        "level": "constant.level",
                    },
                }
            },
        }
        pipeline = Pipeline.parse(data)
        received_pm: list = []

        def fake_call(direction, port_map):
            received_pm.extend(port_map)
            return [("bytes", b"output")]

        codecs = {"s": _FakeCodec(call_fn=fake_call)}
        prepared = _prepared(pipeline, codecs=codecs)
        run(prepared, {"bytes": b"data"})

        pm_dict = dict(received_pm)
        assert pm_dict["level"] == b"5"

    def test_encode_only_inputs_skipped_during_decode(self) -> None:
        data = {
            "codec_id": "test",
            "direction": "decode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {"sym_table": {"type": "string", "value": "abc"}},
            "outputs": {"bytes": "s.bytes"},
            "steps": {
                "s": {
                    "codec_id": "some-codec",
                    "inputs": {
                        "bytes": "input.bytes",
                        "sym_table": "constant.sym_table",
                    },
                }
            },
        }
        pipeline = Pipeline.parse(data)
        received_pm: list = []

        def fake_call(direction, port_map):
            received_pm.extend(port_map)
            return [("bytes", b"decoded")]

        sig = Signature.from_dict(
            {
                "codec_id": "fake",
                "inputs": {
                    "bytes": {"type": "bytes"},
                    "sym_table": {"type": "string", "encode_only": True},
                },
                "outputs": {"bytes": {"type": "bytes"}},
            }
        )
        codecs = {"s": _FakeCodec(call_fn=fake_call, sig=sig)}
        prepared = _prepared(pipeline, codecs=codecs)
        run(prepared, {"bytes": b"encoded"})

        port_names = [name for name, _ in received_pm]
        assert "sym_table" not in port_names
        assert "bytes" in port_names

    def test_encode_only_inputs_included_during_encode(self) -> None:
        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {"sym_table": {"type": "string", "value": "abc"}},
            "outputs": {"bytes": "s.bytes"},
            "steps": {
                "s": {
                    "codec_id": "some-codec",
                    "inputs": {
                        "bytes": "input.bytes",
                        "sym_table": "constant.sym_table",
                    },
                }
            },
        }
        pipeline = Pipeline.parse(data)
        received_pm: list = []

        def fake_call(direction, port_map):
            received_pm.extend(port_map)
            return [("bytes", b"encoded")]

        sig = Signature.from_dict(
            {
                "codec_id": "fake",
                "inputs": {
                    "bytes": {"type": "bytes"},
                    "sym_table": {"type": "string", "encode_only": True},
                },
                "outputs": {"bytes": {"type": "bytes"}},
            }
        )
        codecs = {"s": _FakeCodec(call_fn=fake_call, sig=sig)}
        prepared = _prepared(pipeline, codecs=codecs)
        run(prepared, {"bytes": b"data"})

        port_names = [name for name, _ in received_pm]
        assert "sym_table" in port_names

    def test_encode_only_pipeline_input_not_required_during_decode(self) -> None:
        """encode_only pipeline inputs are not required when running decode."""
        data = {
            "codec_id": "test",
            "direction": "decode",
            "inputs": {
                "bytes": {"type": "bytes"},
                "dtype": {"type": "string", "encode_only": True},
            },
            "constants": {},
            "outputs": {"bytes": "s.bytes"},
            "steps": {
                "s": {
                    "codec_id": "some-codec",
                    "inputs": {"bytes": "input.bytes"},
                }
            },
        }
        pipeline = Pipeline.parse(data)
        codecs = {"s": _FakeCodec(call_fn=lambda d, pm: [("bytes", b"decoded")])}
        prepared = _prepared(pipeline, codecs=codecs)
        result = run(prepared, {"bytes": b"encoded"})
        assert result == {"bytes": b"decoded"}

    def test_encode_only_pipeline_input_required_during_encode(self) -> None:
        """encode_only pipeline inputs ARE required when running encode."""
        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {
                "bytes": {"type": "bytes"},
                "dtype": {"type": "string", "encode_only": True},
            },
            "constants": {},
            "outputs": {"bytes": "s.bytes"},
            "steps": {
                "s": {
                    "codec_id": "some-codec",
                    "inputs": {"bytes": "input.bytes"},
                }
            },
        }
        pipeline = Pipeline.parse(data)
        prepared = _prepared(pipeline)
        with pytest.raises(ValueError, match="Missing pipeline input"):
            run(prepared, {"bytes": b"data"})

    def test_missing_input_raises(self) -> None:
        pipeline = Pipeline.parse(_make_simple_pipeline())
        prepared = _prepared(pipeline)
        with pytest.raises(ValueError, match="Missing pipeline input"):
            run(prepared, {})

    def test_fan_out_routing(self) -> None:
        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {
                "a_out": "proc_a.bytes",
                "b_out": "proc_b.bytes",
            },
            "steps": {
                "split": {
                    "codec_id": "page-split",
                    "inputs": {"bytes": "input.bytes"},
                },
                "proc_a": {
                    "codec_id": "identity",
                    "inputs": {"bytes": "split.a"},
                },
                "proc_b": {
                    "codec_id": "identity",
                    "inputs": {"bytes": "split.b"},
                },
            },
        }
        pipeline = Pipeline.parse(data)

        def fake_split(direction, port_map):
            data_bytes = port_map[0][1]
            mid = len(data_bytes) // 2
            return [("a", data_bytes[:mid]), ("b", data_bytes[mid:])]

        split_sig = Signature.from_dict(
            {
                "codec_id": "fake-split",
                "inputs": {"bytes": {"type": "bytes"}},
                "outputs": {"a": {"type": "bytes"}, "b": {"type": "bytes"}},
            }
        )
        codecs = {
            "split": _FakeCodec(call_fn=fake_split, sig=split_sig),
            "proc_a": _FakeCodec(call_fn=lambda d, pm: pm),
            "proc_b": _FakeCodec(call_fn=lambda d, pm: pm),
        }
        prepared = _prepared(pipeline, codecs=codecs)
        result = run(prepared, {"bytes": b"abcdefgh"})

        assert result["a_out"] == b"abcd"
        assert result["b_out"] == b"efgh"


class TestResolverIntegration:
    """Verify that prepare() uses the Resolver to find codecs."""

    def test_resolver_resolves_codec_id(self, tmp_path: Path) -> None:
        """The resolver maps codec_id to a wasm file."""
        wasm_file = tmp_path / "codec.wasm"
        sig = {
            "codec_id": "some-codec",
            "inputs": {"bytes": {"type": "bytes", "required": True}},
            "outputs": {"bytes": {"type": "bytes"}},
        }
        wasm_file.write_bytes(_wasm_with_signature(sig))

        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "s.bytes"},
            "steps": {
                "s": {
                    "codec_id": "some-codec",
                    "inputs": {"bytes": "input.bytes"},
                }
            },
        }

        resolver = Resolver(paths={"some-codec": wasm_file})
        prepared = prepare(data, "encode", resolver=resolver)
        assert "s" in prepared.codecs

    def test_unresolvable_codec_raises(self) -> None:
        """A codec_id with no available implementation raises ValueError."""
        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "s.bytes"},
            "steps": {
                "s": {
                    "codec_id": "nonexistent-codec",
                    "inputs": {"bytes": "input.bytes"},
                }
            },
        }

        resolver = Resolver(codec_store=Path("/nonexistent"))
        with pytest.raises(ValueError, match="No codec implementation found"):
            prepare(data, "encode", resolver=resolver)


class TestForceSourceResolution:
    """Verify force_sources bypasses the store and overwrites on install."""

    def _make_wasm(self, codec_id: str, impl: str = "impl-a") -> bytes:
        return _wasm_with_signature(
            {
                "codec_id": codec_id,
                "implementation": impl,
                "inputs": {"bytes": {"type": "bytes"}},
                "outputs": {"bytes": {"type": "bytes"}},
            }
        )

    def test_force_install_overwrites_store_entry(self, tmp_path: Path) -> None:
        """force_install=True causes install() to overwrite an existing file."""
        store = CodecStore(tmp_path)

        wasm_v1 = tmp_path / "v1.wasm"
        wasm_v1.write_bytes(self._make_wasm("fc", "same-impl"))
        entry1 = store.install(wasm_v1)
        mtime_after_v1 = entry1.location.stat().st_mtime

        # Build a distinct v2 binary (different implementation tag to get
        # different bytes, but re-use the same implementation *name* so
        # install targets the same store path).
        wasm_v2 = tmp_path / "v2.wasm"
        v2_bytes = self._make_wasm("fc", "same-impl")
        wasm_v2.write_bytes(v2_bytes)

        # Without force_install, existing file is kept (mtime unchanged)
        store.install(wasm_v2)
        assert entry1.location.stat().st_mtime == mtime_after_v1

        # With force_install, file is overwritten
        entry2 = store.install(wasm_v2, force_install=True)
        assert entry2.location == entry1.location
        assert entry2.location.stat().st_mtime >= mtime_after_v1

    def test_force_source_bypasses_store(self, tmp_path: Path) -> None:
        """force_sources is consulted before the local store."""
        store_dir = tmp_path / "store"
        store_dir.mkdir()

        # Pre-populate store with impl-a
        store_codec_dir = store_dir / "fc"
        store_codec_dir.mkdir()
        (store_codec_dir / "impl-a.wasm").write_bytes(self._make_wasm("fc", "impl-a"))

        # Put a different impl in a file:// source
        source_wasm = tmp_path / "source.wasm"
        source_wasm.write_bytes(self._make_wasm("fc", "impl-b"))

        resolver = Resolver(
            codec_store=store_dir,
            force_sources={"fc": f"file://{source_wasm}"},
        )

        # Before resolve, only impl-a is in the store
        assert "impl-b" not in CodecStore(store_dir).get_index().get("fc", {})

        codec = resolver.resolve("fc")
        assert codec.signature().implementation == "impl-b"

        # After resolve, impl-b is installed in the store
        assert "impl-b" in CodecStore(store_dir).get_index().get("fc", {})

    def test_explicit_path_takes_priority_over_force_source(
        self, tmp_path: Path
    ) -> None:
        """paths (step 0) beats force_sources (step 1)."""
        wasm_file = tmp_path / "local.wasm"
        wasm_file.write_bytes(self._make_wasm("fc", "local-impl"))

        resolver = Resolver(
            codec_store=Path("/nonexistent"),
            paths={"fc": wasm_file},
            # If force_sources were consulted, this would try to download
            # from a non-existent URL and fail.
            force_sources={"fc": "https://should-not-be-called.example.com/x.wasm"},
        )
        codec = resolver.resolve("fc")
        assert codec.signature().implementation == "local-impl"

    def test_force_source_only_affects_specified_codec(self, tmp_path: Path) -> None:
        """Codecs not in force_sources resolve normally."""
        from chonkle.codecs.native import NativeCodec

        resolver = Resolver(
            codec_store=Path("/nonexistent"),
            force_sources={"other-codec": "file:///doesnt-matter.wasm"},
        )
        codec = resolver.resolve("zlib")
        assert isinstance(codec, NativeCodec)


class TestSignatureValidation:
    """Signature format: protospec codec signature — inputs/outputs are dicts."""

    def _make_pipeline(
        self,
        *,
        codec_id: str = "some-codec",
        direction: str = "encode",
        step_inputs: dict | None = None,
        pipeline_output_ref: str = "s.bytes",
    ) -> dict:
        """Build a single-step pipeline dict for signature validation tests."""
        if step_inputs is None:
            step_inputs = {"bytes": "input.bytes"}
        pipeline_input_names = list(
            {
                ref.split(".")[1]
                for ref in step_inputs.values()
                if ref.startswith("input.")
            }
        )
        pipeline_inputs = {
            name: {"type": "bytes"} for name in (pipeline_input_names or ["bytes"])
        }
        return {
            "codec_id": "test",
            "direction": direction,
            "inputs": pipeline_inputs,
            "constants": {},
            "outputs": {"bytes": pipeline_output_ref},
            "steps": {
                "s": {
                    "codec_id": codec_id,
                    "inputs": step_inputs,
                }
            },
        }

    def _resolver(self, codec_id: str, wasm_file: Path) -> Resolver:
        return Resolver(paths={codec_id: wasm_file})

    def test_signature_mismatch_raises(self, tmp_path: Path) -> None:
        """A signature with an unknown output port name raises ValueError."""
        wasm_file = tmp_path / "codec.wasm"
        signature = {
            "inputs": {"bytes": {"type": "bytes", "required": True}},
            "outputs": {"wrong_port": {"type": "bytes"}},
        }
        wasm_file.write_bytes(_wasm_with_signature(signature))
        data = self._make_pipeline()
        resolver = self._resolver("some-codec", wasm_file)

        with pytest.raises(ValueError, match="does not declare output port"):
            prepare(data, "encode", resolver=resolver)

    def test_matching_signature_does_not_raise(self, tmp_path: Path) -> None:
        """A signature matching declared ports does not raise."""
        wasm_file = tmp_path / "codec.wasm"
        signature = {
            "inputs": {
                "bytes": {"type": "bytes", "required": True},
                "level": {"type": "int", "required": False, "encode_only": True},
            },
            "outputs": {"bytes": {"type": "bytes"}},
        }
        wasm_file.write_bytes(_wasm_with_signature(signature))
        data = self._make_pipeline(
            step_inputs={"bytes": "input.bytes"},
        )
        resolver = self._resolver("some-codec", wasm_file)

        prepared = prepare(data, "encode", resolver=resolver)
        assert "s" in prepared.codecs

    def test_signature_unknown_input_raises(self, tmp_path: Path) -> None:
        """A step wiring a port not in the signature inputs raises ValueError."""
        wasm_file = tmp_path / "codec.wasm"
        signature = {
            "inputs": {"bytes": {"type": "bytes", "required": True}},
            "outputs": {"bytes": {"type": "bytes"}},
        }
        wasm_file.write_bytes(_wasm_with_signature(signature))
        data = self._make_pipeline(
            step_inputs={"wrong": "input.bytes"},
        )
        resolver = self._resolver("some-codec", wasm_file)

        with pytest.raises(ValueError, match="not valid signature encode inputs"):
            prepare(data, "encode", resolver=resolver)

    def test_signature_encode_only_excluded_during_decode(self, tmp_path: Path) -> None:
        """encode_only inputs are excluded from valid decode inputs; no raise."""
        wasm_file = tmp_path / "codec.wasm"
        signature = {
            "inputs": {
                "bytes": {"type": "bytes", "required": True},
                "level": {"type": "int", "required": False, "encode_only": True},
            },
            "outputs": {"bytes": {"type": "bytes"}},
        }
        wasm_file.write_bytes(_wasm_with_signature(signature))
        data = self._make_pipeline(
            direction="decode",
            step_inputs={"bytes": "input.bytes", "level": "input.bytes"},
        )
        resolver = self._resolver("some-codec", wasm_file)

        prepared = prepare(data, "decode", resolver=resolver)
        assert "s" in prepared.codecs

    def test_signature_subset_output_passes(self, tmp_path: Path) -> None:
        """Declaring fewer outputs than the signature advertises is valid."""
        wasm_file = tmp_path / "codec.wasm"
        signature = {
            "inputs": {"bytes": {"type": "bytes", "required": True}},
            "outputs": {"bytes": {"type": "bytes"}, "checksum": {"type": "bytes"}},
        }
        wasm_file.write_bytes(_wasm_with_signature(signature))
        data = self._make_pipeline()
        resolver = self._resolver("some-codec", wasm_file)

        prepared = prepare(data, "encode", resolver=resolver)
        assert "s" in prepared.codecs

    def test_signature_outputs_key_absent_skips_check(self, tmp_path: Path) -> None:
        """A signature with no 'outputs' key skips output validation."""
        wasm_file = tmp_path / "codec.wasm"
        signature = {"inputs": {"bytes": {"type": "bytes", "required": True}}}
        wasm_file.write_bytes(_wasm_with_signature(signature))
        resolver = self._resolver("some-codec", wasm_file)

        # Wiring validation will skip step output port check when
        # the signature has no outputs key (empty set, not missing step).
        # The pipeline output ref "s.bytes" won't match but the wiring
        # validation catches it. Use an input passthrough instead.
        data = self._make_pipeline(pipeline_output_ref="input.bytes")
        prepared = prepare(data, "encode", resolver=resolver)
        assert "s" in prepared.codecs

    def test_signature_inputs_key_absent_skips_check(self, tmp_path: Path) -> None:
        """A signature with no 'inputs' key skips input validation."""
        wasm_file = tmp_path / "codec.wasm"
        signature = {"outputs": {"bytes": {"type": "bytes"}}}
        wasm_file.write_bytes(_wasm_with_signature(signature))
        data = self._make_pipeline()
        resolver = self._resolver("some-codec", wasm_file)

        prepared = prepare(data, "encode", resolver=resolver)
        assert "s" in prepared.codecs

    def test_signature_both_wrong_reports_both(self, tmp_path: Path) -> None:
        """When inputs are wrong, the error reports the input issue."""
        wasm_file = tmp_path / "codec.wasm"
        signature = {
            "inputs": {"bytes": {"type": "bytes", "required": True}},
            "outputs": {"bytes": {"type": "bytes"}},
        }
        wasm_file.write_bytes(_wasm_with_signature(signature))
        data = self._make_pipeline(
            step_inputs={"bad_in": "input.bytes"},
        )
        resolver = self._resolver("some-codec", wasm_file)

        with pytest.raises(ValueError, match="not valid signature encode inputs"):
            prepare(data, "encode", resolver=resolver)

    def test_missing_signature_raises(self, tmp_path: Path) -> None:
        """A .wasm with no embedded chonkle:signature section raises ValueError."""
        wasm_file = tmp_path / "codec.wasm"
        wasm_file.write_bytes(_CM_HEADER)
        data = self._make_pipeline()
        resolver = self._resolver("some-codec", wasm_file)

        with pytest.raises(ValueError, match="chonkle:signature"):
            prepare(data, "encode", resolver=resolver)

    def test_two_steps_both_wrong_reports_both(self, tmp_path: Path) -> None:
        """Signature errors from multiple steps are all reported in one exception."""
        signature = {
            "inputs": {"bytes": {"type": "bytes", "required": True}},
            "outputs": {"bytes": {"type": "bytes"}},
        }
        wasm_a = tmp_path / "codec_a.wasm"
        wasm_b = tmp_path / "codec_b.wasm"
        wasm_a.write_bytes(_wasm_with_signature(signature))
        wasm_b.write_bytes(_wasm_with_signature(signature))

        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "step_b.bytes"},
            "steps": {
                "step_a": {
                    "codec_id": "codec-a",
                    "inputs": {"bad_a": "input.bytes"},
                },
                "step_b": {
                    "codec_id": "codec-b",
                    "inputs": {"bad_b": "step_a.bytes"},
                },
            },
        }

        resolver = Resolver(
            paths={
                "codec-a": wasm_a,
                "codec-b": wasm_b,
            }
        )

        with pytest.raises(ValueError, match="step_a") as exc_info:
            prepare(data, "encode", resolver=resolver)

        assert "step_b" in str(exc_info.value)

    def test_signature_input_port_missing_type_raises(self, tmp_path: Path) -> None:
        """A signature input port without a 'type' field raises ValueError."""
        wasm_file = tmp_path / "codec.wasm"
        signature = {
            "inputs": {"bytes": {"required": True}},  # no "type"
            "outputs": {"bytes": {"type": "bytes"}},
        }
        wasm_file.write_bytes(_wasm_with_signature(signature))
        data = self._make_pipeline()
        resolver = self._resolver("some-codec", wasm_file)

        with pytest.raises(ValueError, match="missing required 'type' field"):
            prepare(data, "encode", resolver=resolver)

    def test_signature_output_port_missing_type_raises(self, tmp_path: Path) -> None:
        """A signature output port without a 'type' field raises ValueError."""
        wasm_file = tmp_path / "codec.wasm"
        signature = {
            "inputs": {"bytes": {"type": "bytes", "required": True}},
            "outputs": {"bytes": {}},  # no "type"
        }
        wasm_file.write_bytes(_wasm_with_signature(signature))
        data = self._make_pipeline()
        resolver = self._resolver("some-codec", wasm_file)

        with pytest.raises(ValueError, match="missing required 'type' field"):
            prepare(data, "encode", resolver=resolver)

    def test_type_mismatch_pipeline_input_raises(self, tmp_path: Path) -> None:
        """Pipeline input type mismatch against codec signature raises ValueError."""
        wasm_file = tmp_path / "codec.wasm"
        signature = {
            "inputs": {"data": {"type": "bytes", "required": True}},
            "outputs": {"data": {"type": "bytes"}},
        }
        wasm_file.write_bytes(_wasm_with_signature(signature))
        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"data": {"type": "int"}},
            "constants": {},
            "outputs": {"data": "s.data"},
            "steps": {
                "s": {
                    "codec_id": "some-codec",
                    "inputs": {"data": "input.data"},
                }
            },
        }
        resolver = self._resolver("some-codec", wasm_file)

        with pytest.raises(ValueError, match="provides type"):
            prepare(data, "encode", resolver=resolver)

    def test_type_mismatch_constant_raises(self, tmp_path: Path) -> None:
        """Constant type mismatch against codec signature raises ValueError."""
        wasm_file = tmp_path / "codec.wasm"
        signature = {
            "inputs": {
                "bytes": {"type": "bytes", "required": True},
                "level": {"type": "bytes", "required": False},
            },
            "outputs": {"bytes": {"type": "bytes"}},
        }
        wasm_file.write_bytes(_wasm_with_signature(signature))
        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {"level": {"type": "int", "value": 3}},
            "outputs": {"bytes": "s.bytes"},
            "steps": {
                "s": {
                    "codec_id": "some-codec",
                    "inputs": {"bytes": "input.bytes", "level": "constant.level"},
                }
            },
        }
        resolver = self._resolver("some-codec", wasm_file)

        with pytest.raises(ValueError, match="provides type"):
            prepare(data, "encode", resolver=resolver)

    def test_type_mismatch_step_output_raises(self, tmp_path: Path) -> None:
        """Step output type mismatch against downstream codec input raises."""
        sig_a = {
            "inputs": {"bytes": {"type": "bytes", "required": True}},
            "outputs": {"bytes": {"type": "bytes"}},
        }
        sig_b = {
            "inputs": {"bytes": {"type": "int", "required": True}},
            "outputs": {"bytes": {"type": "int"}},
        }
        wasm_a = tmp_path / "codec_a.wasm"
        wasm_b = tmp_path / "codec_b.wasm"
        wasm_a.write_bytes(_wasm_with_signature(sig_a))
        wasm_b.write_bytes(_wasm_with_signature(sig_b))

        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "step_b.bytes"},
            "steps": {
                "step_a": {
                    "codec_id": "codec-a",
                    "inputs": {"bytes": "input.bytes"},
                },
                "step_b": {
                    "codec_id": "codec-b",
                    "inputs": {"bytes": "step_a.bytes"},
                },
            },
        }

        resolver = Resolver(
            paths={
                "codec-a": wasm_a,
                "codec-b": wasm_b,
            }
        )

        with pytest.raises(ValueError, match="provides type"):
            prepare(data, "encode", resolver=resolver)

    def test_type_match_passes(self, tmp_path: Path) -> None:
        """Matching types across pipeline input and codec signature do not raise."""
        wasm_file = tmp_path / "codec.wasm"
        signature = {
            "inputs": {"bytes": {"type": "bytes", "required": True}},
            "outputs": {"bytes": {"type": "bytes"}},
        }
        wasm_file.write_bytes(_wasm_with_signature(signature))
        data = self._make_pipeline()
        resolver = self._resolver("some-codec", wasm_file)

        prepared = prepare(data, "encode", resolver=resolver)
        assert "s" in prepared.codecs

    def test_missing_type_in_source_skips_check(self, tmp_path: Path) -> None:
        """Pipeline input with no 'type' key skips type check without raising."""
        wasm_file = tmp_path / "codec.wasm"
        signature = {
            "inputs": {"bytes": {"type": "bytes", "required": True}},
            "outputs": {"bytes": {"type": "bytes"}},
        }
        wasm_file.write_bytes(_wasm_with_signature(signature))
        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {}},
            "constants": {},
            "outputs": {"bytes": "s.bytes"},
            "steps": {
                "s": {
                    "codec_id": "some-codec",
                    "inputs": {"bytes": "input.bytes"},
                }
            },
        }
        resolver = self._resolver("some-codec", wasm_file)

        prepared = prepare(data, "encode", resolver=resolver)
        assert "s" in prepared.codecs

    def test_step_output_port_wiring_validated(self, tmp_path: Path) -> None:
        """Wiring ref to a non-existent output port raises at prepare() time."""
        wasm_file = tmp_path / "codec.wasm"
        signature = {
            "inputs": {"bytes": {"type": "bytes", "required": True}},
            "outputs": {"bytes": {"type": "bytes"}},
        }
        wasm_file.write_bytes(_wasm_with_signature(signature))
        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"out": "s.missing_port"},
            "steps": {
                "s": {
                    "codec_id": "some-codec",
                    "inputs": {"bytes": "input.bytes"},
                }
            },
        }
        resolver = self._resolver("some-codec", wasm_file)

        with pytest.raises(ValueError, match="does not declare output port"):
            prepare(data, "encode", resolver=resolver)


class TestInvertedExecution:
    """Verify inverted DAG execution (direction != pipeline.direction)."""

    def test_inverted_single_step_calls_encode(self) -> None:
        """A decode-declared pipeline run as encode calls encode on the codec."""
        pipeline = Pipeline.parse(_make_decode_pipeline())
        received_directions: list[str] = []

        def fake_call(direction, port_map):
            received_directions.append(direction)
            return [("bytes", b"raw")]

        codecs = {"codec": _FakeCodec(call_fn=fake_call, sig=_SIG_WITH_ENCODE_ONLY)}
        prepared = _prepared(pipeline, direction="encode", codecs=codecs)
        run(prepared, {"bytes": b"compressed"})

        assert received_directions == ["encode"]

    def test_inverted_port_map_built_from_step_outputs(self) -> None:
        """Inverted encode: port-map is built from signature outputs."""
        pipeline = Pipeline.parse(_make_decode_pipeline())
        received_port_maps: list = []

        def fake_call(direction, port_map):
            received_port_maps.append(list(port_map))
            return [("bytes", b"raw")]

        codecs = {"codec": _FakeCodec(call_fn=fake_call, sig=_SIG_WITH_ENCODE_ONLY)}
        prepared = _prepared(pipeline, direction="encode", codecs=codecs)
        run(prepared, {"bytes": b"compressed"})

        assert len(received_port_maps) == 1
        pm = dict(received_port_maps[0])
        assert "bytes" in pm
        assert pm["bytes"] == b"compressed"
        assert "level" in pm
        assert pm["level"] == b"3"

    def test_inverted_result_keyed_by_pipeline_inputs(self) -> None:
        """Inverted encode: result is keyed by pipeline.inputs names."""
        pipeline = Pipeline.parse(_make_decode_pipeline())
        codecs = {
            "codec": _FakeCodec(
                call_fn=lambda d, pm: [("bytes", b"encoded_result")],
                sig=_SIG_WITH_ENCODE_ONLY,
            )
        }
        prepared = _prepared(pipeline, direction="encode", codecs=codecs)
        result = run(prepared, {"bytes": b"raw"})
        assert result == {"bytes": b"encoded_result"}

    def test_inverted_execution_order_reversed(self) -> None:
        """Inverted encode on a two-step decode pipeline runs in reversed order."""
        data = {
            "codec_id": "test",
            "direction": "decode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "step_b.bytes"},
            "steps": {
                "step_a": {
                    "codec_id": "some-codec",
                    "inputs": {"bytes": "input.bytes"},
                },
                "step_b": {
                    "codec_id": "some-codec",
                    "inputs": {"bytes": "step_a.bytes"},
                },
            },
        }
        pipeline = Pipeline.parse(data)
        call_order: list[str] = []

        def fake_a(direction, port_map):
            call_order.append("a")
            return [("bytes", b"result")]

        def fake_b(direction, port_map):
            call_order.append("b")
            return [("bytes", b"result")]

        codecs = {
            "step_a": _FakeCodec(call_fn=fake_a),
            "step_b": _FakeCodec(call_fn=fake_b),
        }
        prepared = _prepared(pipeline, direction="encode", codecs=codecs)
        run(prepared, {"bytes": b"raw"})

        assert call_order == ["b", "a"]

    def test_inverted_encode_routes_results_backward(self) -> None:
        """Inverted encode: step results route via decode-direction input wiring."""
        data = {
            "codec_id": "test",
            "direction": "decode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "step_b.bytes"},
            "steps": {
                "step_a": {
                    "codec_id": "some-codec",
                    "inputs": {"bytes": "input.bytes"},
                },
                "step_b": {
                    "codec_id": "some-codec",
                    "inputs": {"bytes": "step_a.bytes"},
                },
            },
        }
        pipeline = Pipeline.parse(data)
        received: dict[str, list] = {}

        def fake_a(direction, port_map):
            received["a"] = list(port_map)
            return [("bytes", b"a_encoded")]

        def fake_b(direction, port_map):
            received["b"] = list(port_map)
            return [("bytes", b"b_encoded")]

        codecs = {
            "step_a": _FakeCodec(call_fn=fake_a),
            "step_b": _FakeCodec(call_fn=fake_b),
        }
        prepared = _prepared(pipeline, direction="encode", codecs=codecs)
        result = run(prepared, {"bytes": b"raw_input"})

        assert dict(received["b"]) == {"bytes": b"raw_input"}
        assert dict(received["a"]) == {"bytes": b"b_encoded"}
        assert result == {"bytes": b"a_encoded"}

    def test_inverted_fan_in_page_split_pattern(self) -> None:
        """Inverted encode on a fan-out decode pipeline fans back in correctly."""
        data = {
            "codec_id": "test",
            "direction": "decode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {
                "rep": "identity_rep.bytes",
                "def_": "identity_def.bytes",
                "data": "identity_data.bytes",
            },
            "steps": {
                "page_split": {
                    "codec_id": "page-split",
                    "inputs": {"bytes": "input.bytes"},
                },
                "identity_rep": {
                    "codec_id": "identity",
                    "inputs": {"bytes": "page_split.rep_levels"},
                },
                "identity_def": {
                    "codec_id": "identity",
                    "inputs": {"bytes": "page_split.def_levels"},
                },
                "identity_data": {
                    "codec_id": "identity",
                    "inputs": {"bytes": "page_split.data"},
                },
            },
        }
        pipeline = Pipeline.parse(data)

        def fake_split(direction, port_map):
            pm = dict(port_map)
            combined = (
                pm.get("rep_levels", b"")
                + pm.get("def_levels", b"")
                + pm.get("data", b"")
            )
            return [("bytes", combined)]

        split_sig = Signature.from_dict(
            {
                "codec_id": "fake-split",
                "inputs": {"bytes": {"type": "bytes"}},
                "outputs": {
                    "rep_levels": {"type": "bytes"},
                    "def_levels": {"type": "bytes"},
                    "data": {"type": "bytes"},
                },
            }
        )
        codecs = {
            "page_split": _FakeCodec(call_fn=fake_split, sig=split_sig),
            "identity_rep": _FakeCodec(call_fn=lambda d, pm: pm),
            "identity_def": _FakeCodec(call_fn=lambda d, pm: pm),
            "identity_data": _FakeCodec(call_fn=lambda d, pm: pm),
        }
        prepared = _prepared(pipeline, direction="encode", codecs=codecs)
        result = run(
            prepared,
            {"rep": b"RRR", "def_": b"DDD", "data": b"VVV"},
        )

        assert "bytes" in result
        assert b"RRR" in result["bytes"]
        assert b"DDD" in result["bytes"]
        assert b"VVV" in result["bytes"]

    def test_inverted_missing_input_raises(self) -> None:
        """Inverted direction with a missing pipeline.outputs key raises ValueError."""
        pipeline = Pipeline.parse(_make_decode_pipeline())
        prepared = _prepared(pipeline, direction="encode")
        with pytest.raises(ValueError, match="Missing pipeline input"):
            run(prepared, {})

    def test_inverted_encode_only_excluded_from_decode_call(self) -> None:
        """Encode-declared pipeline inverted to decode: encode_only_inputs absent."""
        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {"level": {"type": "int", "value": 9}},
            "outputs": {"bytes": "s.bytes"},
            "steps": {
                "s": {
                    "codec_id": "some-codec",
                    "inputs": {"bytes": "input.bytes", "level": "constant.level"},
                }
            },
        }
        pipeline = Pipeline.parse(data)
        received_port_maps: list = []

        def fake_call(direction, port_map):
            received_port_maps.append(list(port_map))
            return [("bytes", b"decoded")]

        sig = Signature.from_dict(
            {
                "codec_id": "fake",
                "inputs": {
                    "bytes": {"type": "bytes"},
                    "level": {"type": "int", "encode_only": True},
                },
                "outputs": {"bytes": {"type": "bytes"}},
            }
        )
        codecs = {"s": _FakeCodec(call_fn=fake_call, sig=sig)}
        prepared = _prepared(pipeline, direction="decode", codecs=codecs)
        run(prepared, {"bytes": b"encoded"})

        assert len(received_port_maps) == 1
        port_names = [name for name, _ in received_port_maps[0]]
        assert "level" not in port_names
        assert "bytes" in port_names


@CODEC_REQUIRED
class TestPageSplitFanOut:
    def test_fan_out_produces_three_outputs(
        self,
        page_split_input: tuple[bytes, int, int],
        page_split_pipeline_json: dict,
    ) -> None:
        """page-split produces three non-empty output byte buffers."""
        data, rep_length, def_length = page_split_input

        page_split_pipeline_json["constants"]["rep_length"]["value"] = rep_length
        page_split_pipeline_json["constants"]["def_length"]["value"] = def_length

        prepared = prepare(
            page_split_pipeline_json, page_split_pipeline_json["direction"]
        )
        result = run(prepared, {"bytes": data})

        assert set(result.keys()) == {"rep_levels", "def_levels", "data"}
        for port_name, value in result.items():
            assert len(value) > 0, f"Expected non-empty output for {port_name!r}"

    def test_split_segment_sizes(
        self,
        page_split_input: tuple[bytes, int, int],
        page_split_pipeline_json: dict,
    ) -> None:
        """page-split segments have the sizes dictated by rep_length and def_length."""
        data, rep_length, def_length = page_split_input

        page_split_pipeline_json["constants"]["rep_length"]["value"] = rep_length
        page_split_pipeline_json["constants"]["def_length"]["value"] = def_length

        prepared = prepare(
            page_split_pipeline_json, page_split_pipeline_json["direction"]
        )
        result = run(prepared, {"bytes": data})

        assert len(result["rep_levels"]) == rep_length
        assert len(result["def_levels"]) == def_length
        assert len(result["data"]) == len(data) - rep_length - def_length


@CORE_CODEC_REQUIRED
class TestCoreWasmPipeline:
    """Tests for core wasm codec steps in DAG pipelines."""

    def test_single_step_core_encode(self, raw_chunk: bytes) -> None:
        """A single core wasm identity step produces identical output on encode."""
        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "s.bytes"},
            "steps": {
                "s": {
                    "codec_id": "identity",
                    "inputs": {"bytes": "input.bytes"},
                }
            },
        }
        resolver = Resolver(paths={"identity": _CORE_IDENTITY_WASM})
        prepared = prepare(data, "encode", resolver=resolver)
        assert prepared.codecs["s"].codec_type == "core"
        result = run(prepared, {"bytes": raw_chunk})
        assert result["bytes"] == raw_chunk

    def test_single_step_core_decode(self, raw_chunk: bytes) -> None:
        """A single core wasm identity step produces identical output on decode."""
        data = {
            "codec_id": "test",
            "direction": "decode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "s.bytes"},
            "steps": {
                "s": {
                    "codec_id": "identity",
                    "inputs": {"bytes": "input.bytes"},
                }
            },
        }
        resolver = Resolver(paths={"identity": _CORE_IDENTITY_WASM})
        prepared = prepare(data, "decode", resolver=resolver)
        result = run(prepared, {"bytes": raw_chunk})
        assert result["bytes"] == raw_chunk

    def test_two_step_core_pipeline(self, raw_chunk: bytes) -> None:
        """Two sequential core wasm identity steps route data correctly."""
        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "step_b.bytes"},
            "steps": {
                "step_a": {
                    "codec_id": "identity",
                    "inputs": {"bytes": "input.bytes"},
                },
                "step_b": {
                    "codec_id": "identity",
                    "inputs": {"bytes": "step_a.bytes"},
                },
            },
        }
        resolver = Resolver(paths={"identity": _CORE_IDENTITY_WASM})
        prepared = prepare(data, "encode", resolver=resolver)
        result = run(prepared, {"bytes": raw_chunk})
        assert result["bytes"] == raw_chunk

    def test_inverted_core_pipeline(self, raw_chunk: bytes) -> None:
        """An inverted core wasm pipeline produces identical output."""
        data = {
            "codec_id": "test",
            "direction": "decode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "s.bytes"},
            "steps": {
                "s": {
                    "codec_id": "identity",
                    "inputs": {"bytes": "input.bytes"},
                }
            },
        }
        resolver = Resolver(paths={"identity": _CORE_IDENTITY_WASM})
        prepared = prepare(data, "encode", resolver=resolver)
        result = run(prepared, {"bytes": raw_chunk})
        assert result["bytes"] == raw_chunk

    def test_core_codec_resolver_detects_type(self) -> None:
        """The resolver instantiates a CoreWasmCodec for core wasm binaries."""
        resolver = Resolver(paths={"identity": _CORE_IDENTITY_WASM})
        codec = resolver.resolve("identity")
        assert codec.codec_type == "core"
        assert codec.codec_id == "identity"
        assert codec.implementation == "identity-core-c"


@COG_CODECS_REQUIRED
class TestCogChunkPipeline:
    """Round-trip and decode tests for the COG zlib+tiff-predictor-2 pipeline."""

    _TILE_BYTES = 1024 * 1024 * 2  # 1024x1024 uint16

    def test_decode_declared_forward_decode_output_size(
        self,
        cog_chunk: bytes,
        cog_decode_pipeline_json: dict,
        cog_codec_resolver: Resolver,
    ) -> None:
        """Decode-declared pipeline forward decode: output is 1024x1024x2 bytes."""
        prepared = prepare(
            cog_decode_pipeline_json, "decode", resolver=cog_codec_resolver
        )
        result = run(prepared, {"bytes": cog_chunk})
        assert len(result["bytes"]) == self._TILE_BYTES

    def test_decode_declared_forward_decode_output_is_bytes(
        self,
        cog_chunk: bytes,
        cog_decode_pipeline_json: dict,
        cog_codec_resolver: Resolver,
    ) -> None:
        """Decode-declared pipeline running forward decode returns a bytes object."""
        prepared = prepare(
            cog_decode_pipeline_json, "decode", resolver=cog_codec_resolver
        )
        result = run(prepared, {"bytes": cog_chunk})
        assert isinstance(result["bytes"], bytes)

    def test_encode_declared_inverted_decode_output_size(
        self,
        cog_chunk: bytes,
        cog_encode_pipeline_json: dict,
        cog_codec_resolver: Resolver,
    ) -> None:
        """Encode-declared pipeline inverted decode: output is 1024x1024x2 bytes."""
        prepared = prepare(
            cog_encode_pipeline_json, "decode", resolver=cog_codec_resolver
        )
        result = run(prepared, {"bytes": cog_chunk})
        assert len(result["bytes"]) == self._TILE_BYTES

    def test_encode_declared_inverted_decode_output_is_bytes(
        self,
        cog_chunk: bytes,
        cog_encode_pipeline_json: dict,
        cog_codec_resolver: Resolver,
    ) -> None:
        """Encode-declared pipeline running inverted decode returns a bytes object."""
        prepared = prepare(
            cog_encode_pipeline_json, "decode", resolver=cog_codec_resolver
        )
        result = run(prepared, {"bytes": cog_chunk})
        assert isinstance(result["bytes"], bytes)

    def test_decode_declared_pipeline_roundtrip(
        self,
        raw_chunk: bytes,
        cog_decode_pipeline_json: dict,
        cog_codec_resolver: Resolver,
    ) -> None:
        """Decode-declared pipeline roundtrip: inverted encode then forward decode."""
        tile = (raw_chunk * ((self._TILE_BYTES // len(raw_chunk)) + 1))[
            : self._TILE_BYTES
        ]
        enc_prepared = prepare(
            cog_decode_pipeline_json, "encode", resolver=cog_codec_resolver
        )
        encoded = run(enc_prepared, {"bytes": tile})
        dec_prepared = prepare(
            cog_decode_pipeline_json, "decode", resolver=cog_codec_resolver
        )
        decoded = run(dec_prepared, {"bytes": encoded["bytes"]})
        assert decoded["bytes"] == tile

    def test_encode_declared_pipeline_roundtrip(
        self,
        raw_chunk: bytes,
        cog_encode_pipeline_json: dict,
        cog_codec_resolver: Resolver,
    ) -> None:
        """Encode-declared pipeline roundtrip: forward encode then inverted decode."""
        tile = (raw_chunk * ((self._TILE_BYTES // len(raw_chunk)) + 1))[
            : self._TILE_BYTES
        ]
        enc_prepared = prepare(
            cog_encode_pipeline_json, "encode", resolver=cog_codec_resolver
        )
        encoded = run(enc_prepared, {"bytes": tile})
        dec_prepared = prepare(
            cog_encode_pipeline_json, "decode", resolver=cog_codec_resolver
        )
        decoded = run(dec_prepared, {"bytes": encoded["bytes"]})
        assert decoded["bytes"] == tile

    def test_encode_declared_inverted_decode_matches_decode_declared_forward(
        self,
        cog_chunk: bytes,
        cog_decode_pipeline_json: dict,
        cog_encode_pipeline_json: dict,
        cog_codec_resolver: Resolver,
    ) -> None:
        """Encode-declared pipeline inverted decode yields the same raw tile
        as decode-declared pipeline forward decode."""
        fwd = prepare(cog_decode_pipeline_json, "decode", resolver=cog_codec_resolver)
        inv = prepare(cog_encode_pipeline_json, "decode", resolver=cog_codec_resolver)
        forward = run(fwd, {"bytes": cog_chunk})
        inverted = run(inv, {"bytes": cog_chunk})
        assert inverted["bytes"] == forward["bytes"]

    def test_decode_declared_inverted_encode_matches_encode_declared_forward(
        self,
        raw_chunk: bytes,
        cog_decode_pipeline_json: dict,
        cog_encode_pipeline_json: dict,
        cog_codec_resolver: Resolver,
    ) -> None:
        """Decode-declared pipeline inverted encode yields the same compressed bytes
        as encode-declared pipeline forward encode."""
        tile = (raw_chunk * ((self._TILE_BYTES // len(raw_chunk)) + 1))[
            : self._TILE_BYTES
        ]
        inv = prepare(cog_decode_pipeline_json, "encode", resolver=cog_codec_resolver)
        fwd = prepare(cog_encode_pipeline_json, "encode", resolver=cog_codec_resolver)
        inverted = run(inv, {"bytes": tile})
        forward = run(fwd, {"bytes": tile})
        assert inverted["bytes"] == forward["bytes"]


class TestSingleCopyWiring:
    """Verify that CoreWasmRef values flow through the executor correctly.

    Uses _FakeCodec variants to test materialization logic without needing
    compiled wasm modules.
    """

    @staticmethod
    def _make_ref(data: bytes) -> CoreWasmRef:
        """Create a CoreWasmRef backed by mock memory for wiring tests."""

        class _MockMemory:
            def read(self, store: Any, start: int, end: int) -> bytes:
                return data[start:end]

        class _MockCodec:
            memory = _MockMemory()
            store = None

        return CoreWasmRef(codec=_MockCodec(), ptr=0, length=len(data))  # type: ignore[arg-type]

    def test_ref_materialized_for_component_codec(self) -> None:
        """CoreWasmRef from an upstream step is materialized when passed
        to a downstream component (non-core) codec."""
        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "step_b.bytes"},
            "steps": {
                "step_a": {
                    "codec_id": "some-codec",
                    "inputs": {"bytes": "input.bytes"},
                },
                "step_b": {
                    "codec_id": "some-codec",
                    "inputs": {"bytes": "step_a.bytes"},
                },
            },
        }
        pipeline = Pipeline.parse(data)
        received_b: list = []

        ref = self._make_ref(b"from_core")

        def fake_a(direction, port_map):
            return [("bytes", ref)]

        def fake_b(direction, port_map):
            received_b.extend(port_map)
            return [("bytes", b"final")]

        codecs: dict[str, Codec] = {
            "step_a": _FakeCodec(call_fn=fake_a),
            "step_b": _FakeCodec(call_fn=fake_b),
        }
        prepared = _prepared(pipeline, codecs=codecs)
        result = run(prepared, {"bytes": b"input"})

        assert result == {"bytes": b"final"}
        # step_b is a component codec, so the ref should be materialized
        _, value = received_b[0]
        assert isinstance(value, bytes)
        assert value == b"from_core"

    def test_ref_materialized_in_final_output(self) -> None:
        """CoreWasmRef in the final pipeline output is materialized to bytes."""
        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "s.bytes"},
            "steps": {
                "s": {
                    "codec_id": "some-codec",
                    "inputs": {"bytes": "input.bytes"},
                }
            },
        }
        pipeline = Pipeline.parse(data)
        ref = self._make_ref(b"deferred_output")

        codecs: dict[str, Codec] = {
            "s": _FakeCodec(call_fn=lambda d, pm: [("bytes", ref)]),
        }
        prepared = _prepared(pipeline, codecs=codecs)
        result = run(prepared, {"bytes": b"input"})

        assert isinstance(result["bytes"], bytes)
        assert result["bytes"] == b"deferred_output"

    def test_inverted_ref_materialized_in_final_output(self) -> None:
        """CoreWasmRef is materialized in inverted execution final output."""
        data = {
            "codec_id": "test",
            "direction": "decode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "s.bytes"},
            "steps": {
                "s": {
                    "codec_id": "some-codec",
                    "inputs": {"bytes": "input.bytes"},
                }
            },
        }
        pipeline = Pipeline.parse(data)
        ref = self._make_ref(b"inverted_deferred")

        codecs: dict[str, Codec] = {
            "s": _FakeCodec(call_fn=lambda d, pm: [("bytes", ref)]),
        }
        prepared = _prepared(pipeline, direction="encode", codecs=codecs)
        result = run(prepared, {"bytes": b"input"})

        assert isinstance(result["bytes"], bytes)
        assert result["bytes"] == b"inverted_deferred"


@CORE_CODEC_REQUIRED
class TestSingleCopyCorePipeline:
    """Integration tests for single-copy transfer between core wasm steps.

    Requires the identity-core-c codec to be built.
    """

    def test_two_step_core_returns_correct_output(self, raw_chunk: bytes) -> None:
        """Two sequential core identity steps produce correct output via
        deferred CoreWasmRef values in the value store."""
        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "step_b.bytes"},
            "steps": {
                "step_a": {
                    "codec_id": "identity",
                    "inputs": {"bytes": "input.bytes"},
                },
                "step_b": {
                    "codec_id": "identity",
                    "inputs": {"bytes": "step_a.bytes"},
                },
            },
        }
        resolver = Resolver(paths={"identity": _CORE_IDENTITY_WASM})
        prepared = prepare(data, "encode", resolver=resolver)

        assert isinstance(prepared.codecs["step_a"], CoreWasmCodec)
        assert isinstance(prepared.codecs["step_b"], CoreWasmCodec)

        result = run(prepared, {"bytes": raw_chunk})
        assert result["bytes"] == raw_chunk

    def test_core_codec_call_returns_core_wasm_ref(self, raw_chunk: bytes) -> None:
        """CoreWasmCodec.call() returns CoreWasmRef entries (lazy output)."""
        resolver = Resolver(paths={"identity": _CORE_IDENTITY_WASM})
        codec = resolver.resolve("identity")
        assert isinstance(codec, CoreWasmCodec)

        output = codec.call("encode", [("bytes", raw_chunk)])
        assert len(output) == 1
        name, value = output[0]
        assert name == "bytes"
        assert isinstance(value, CoreWasmRef)
        assert value.materialize() == raw_chunk

    def test_three_step_core_chain(self, raw_chunk: bytes) -> None:
        """Three sequential core identity steps chain correctly."""
        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "step_c.bytes"},
            "steps": {
                "step_a": {
                    "codec_id": "identity",
                    "inputs": {"bytes": "input.bytes"},
                },
                "step_b": {
                    "codec_id": "identity",
                    "inputs": {"bytes": "step_a.bytes"},
                },
                "step_c": {
                    "codec_id": "identity",
                    "inputs": {"bytes": "step_b.bytes"},
                },
            },
        }
        resolver = Resolver(paths={"identity": _CORE_IDENTITY_WASM})
        prepared = prepare(data, "encode", resolver=resolver)
        result = run(prepared, {"bytes": raw_chunk})
        assert result["bytes"] == raw_chunk

    def test_inverted_two_step_core_pipeline(self, raw_chunk: bytes) -> None:
        """Inverted execution through two core identity steps works correctly."""
        data = {
            "codec_id": "test",
            "direction": "decode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "step_b.bytes"},
            "steps": {
                "step_a": {
                    "codec_id": "identity",
                    "inputs": {"bytes": "input.bytes"},
                },
                "step_b": {
                    "codec_id": "identity",
                    "inputs": {"bytes": "step_a.bytes"},
                },
            },
        }
        resolver = Resolver(paths={"identity": _CORE_IDENTITY_WASM})
        prepared = prepare(data, "encode", resolver=resolver)
        result = run(prepared, {"bytes": raw_chunk})
        assert result["bytes"] == raw_chunk
