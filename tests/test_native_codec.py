"""Tests for NativeCodec and native codec integration."""

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from chonkle.codecs._base import Backend, Codec, PortMap
from chonkle.codecs.native import NativeCodec
from chonkle.executor import run
from chonkle.pipeline import Direction, Pipeline, PreparedPipeline
from chonkle.resolver import Resolver
from chonkle.wasm_signature import Signature

numcodecs = pytest.importorskip("numcodecs")
np = pytest.importorskip("numpy")


class TestNativeCodecInstantiation:
    def test_loads_signature_from_bundled_file(self) -> None:
        codec = NativeCodec("zlib")
        sig = codec.signature()
        assert sig.codec_id == "zlib"
        assert sig.data_format == "bytes"
        assert "bytes" in sig.inputs

    def test_codec_type_is_native(self) -> None:
        codec = NativeCodec("zlib")
        assert codec.codec_type == "native"

    def test_codec_id_and_implementation(self) -> None:
        codec = NativeCodec("zlib")
        assert codec.codec_id == "zlib"
        assert codec.implementation == "numcodecs.zlib"

    def test_missing_signature_raises(self) -> None:
        with pytest.raises(ValueError, match="No native codec signature"):
            NativeCodec("nonexistent-codec-xyz")

    def test_numcodecs_not_installed_raises(self) -> None:
        with (
            patch.dict("sys.modules", {"numcodecs": None}),
            pytest.raises(ImportError, match="numcodecs is required"),
        ):
            NativeCodec("zlib")


class TestNativeCodecBytesFormat:
    """Test bytes-to-bytes native codecs (zlib, gzip, bz2, etc.)."""

    def test_zlib_encode_decode_roundtrip(self) -> None:
        codec = NativeCodec("zlib")
        data = bytes(range(256)) * 64
        encoded = codec.call("encode", [("bytes", data)])
        assert len(encoded) == 1
        assert encoded[0][0] == "bytes"
        assert encoded[0][1] != data  # compressed

        decoded = codec.call("decode", [("bytes", encoded[0][1])])
        assert decoded[0][1] == data

    def test_gzip_encode_decode_roundtrip(self) -> None:
        codec = NativeCodec("gzip")
        data = bytes(range(256)) * 64
        encoded = codec.call("encode", [("bytes", data)])
        decoded = codec.call("decode", [("bytes", encoded[0][1])])
        assert decoded[0][1] == data

    def test_bz2_encode_decode_roundtrip(self) -> None:
        codec = NativeCodec("bz2")
        data = bytes(range(256)) * 64
        encoded = codec.call("encode", [("bytes", data)])
        decoded = codec.call("decode", [("bytes", encoded[0][1])])
        assert decoded[0][1] == data

    def test_lzma_encode_decode_roundtrip(self) -> None:
        codec = NativeCodec("lzma")
        data = bytes(range(256)) * 64
        encoded = codec.call("encode", [("bytes", data)])
        decoded = codec.call("decode", [("bytes", encoded[0][1])])
        assert decoded[0][1] == data

    def test_encode_with_level_parameter(self) -> None:
        codec = NativeCodec("zlib")
        data = bytes(range(256)) * 64
        # level is JSON-encoded in the port-map
        encoded = codec.call("encode", [("bytes", data), ("level", b"9")])
        decoded = codec.call("decode", [("bytes", encoded[0][1])])
        assert decoded[0][1] == data


class TestNativeCodecNdarrayFormat:
    """Test ndarray-format native codecs (delta, shuffle)."""

    def test_delta_encode_decode_roundtrip(self) -> None:
        codec = NativeCodec("delta")
        arr = np.arange(100, dtype="<i4")
        data = arr.tobytes()
        encoded = codec.call("encode", [("bytes", data), ("dtype", b'"<i4"')])
        decoded = codec.call("decode", [("bytes", encoded[0][1]), ("dtype", b'"<i4"')])
        result = np.frombuffer(decoded[0][1], dtype="<i4")
        np.testing.assert_array_equal(result, arr)

    def test_shuffle_encode_decode_roundtrip(self) -> None:
        codec = NativeCodec("shuffle")
        arr = np.arange(100, dtype="<f4")
        data = arr.tobytes()
        encoded = codec.call("encode", [("bytes", data), ("dtype", b'"<f4"')])
        decoded = codec.call("decode", [("bytes", encoded[0][1]), ("dtype", b'"<f4"')])
        result = np.frombuffer(decoded[0][1], dtype="<f4")
        np.testing.assert_array_equal(result, arr)

    def test_ndarray_missing_dtype_raises(self) -> None:
        codec = NativeCodec("delta")
        data = bytes(range(16))
        with pytest.raises((ValueError, TypeError)):
            codec.call("encode", [("bytes", data)])


# -- Fake codec for mixed pipeline tests --


_DEFAULT_SIG = Signature.from_dict(
    {
        "codec_id": "fake",
        "inputs": {"bytes": {"type": "bytes"}},
        "outputs": {"bytes": {"type": "bytes"}},
    }
)


class _FakeCodec(Codec):
    """Test double for wiring tests."""

    def __init__(self, call_fn: Any = None, sig: Signature | None = None) -> None:
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


def _make_prepared(
    pipeline: Pipeline,
    direction: Direction,
    codecs: dict[str, Codec],
) -> PreparedPipeline:
    """Build a PreparedPipeline computing encode_only_inputs and output_ports."""
    return PreparedPipeline(
        pipeline=pipeline,
        direction=direction,
        codecs=codecs,
        encode_only_inputs={
            name: frozenset(codecs[name].signature().encode_only_inputs())
            for name in pipeline.steps
        },
        output_ports={
            name: tuple(codecs[name].signature().outputs.keys())
            for name in pipeline.steps
        },
    )


class TestNativePipeline:
    """Test native codecs running in a DAG pipeline via the executor."""

    def test_single_native_step_encode_decode(self) -> None:
        """Single native zlib step round-trips correctly."""
        pipeline_dict = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "compress.bytes"},
            "steps": {
                "compress": {
                    "codec_id": "zlib",
                    "inputs": {"bytes": "input.bytes"},
                }
            },
        }
        pipeline = Pipeline.parse(pipeline_dict)
        codec = NativeCodec("zlib")
        prepared = _make_prepared(pipeline, "encode", {"compress": codec})
        data = bytes(range(256)) * 64
        result = run(prepared, {"bytes": data})
        assert result["bytes"] != data

        # Decode direction
        pipeline_dec = Pipeline.parse({**pipeline_dict, "direction": "decode"})
        prepared_dec = _make_prepared(
            pipeline_dec, "decode", {"compress": NativeCodec("zlib")}
        )
        decoded = run(prepared_dec, {"bytes": result["bytes"]})
        assert decoded["bytes"] == data

    def test_two_native_steps_chained(self) -> None:
        """Two native steps (gzip then bz2) chain correctly."""
        pipeline_dict = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "step_b.bytes"},
            "steps": {
                "step_a": {
                    "codec_id": "gzip",
                    "inputs": {"bytes": "input.bytes"},
                },
                "step_b": {
                    "codec_id": "bz2",
                    "inputs": {"bytes": "step_a.bytes"},
                },
            },
        }
        pipeline = Pipeline.parse(pipeline_dict)
        prepared = _make_prepared(
            pipeline,
            "encode",
            {"step_a": NativeCodec("gzip"), "step_b": NativeCodec("bz2")},
        )
        data = bytes(range(256)) * 64
        result = run(prepared, {"bytes": data})
        assert result["bytes"] != data

    def test_native_with_constant_parameter(self) -> None:
        """Native codec receives constant parameter from pipeline."""
        pipeline_dict = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {"level": {"type": "int", "value": 9}},
            "outputs": {"bytes": "compress.bytes"},
            "steps": {
                "compress": {
                    "codec_id": "zlib",
                    "inputs": {
                        "bytes": "input.bytes",
                        "level": "constant.level",
                    },
                }
            },
        }
        pipeline = Pipeline.parse(pipeline_dict)
        codec = NativeCodec("zlib")
        prepared = _make_prepared(pipeline, "encode", {"compress": codec})
        data = bytes(range(256)) * 64
        result = run(prepared, {"bytes": data})
        assert result["bytes"] != data

    def test_mixed_native_and_fake_wasm_pipeline(self) -> None:
        """Native codec followed by a fake wasm codec in a pipeline."""
        pipeline_dict = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "step_b.bytes"},
            "steps": {
                "step_a": {
                    "codec_id": "zlib",
                    "inputs": {"bytes": "input.bytes"},
                },
                "step_b": {
                    "codec_id": "some-codec",
                    "inputs": {"bytes": "step_a.bytes"},
                },
            },
        }
        pipeline = Pipeline.parse(pipeline_dict)
        codecs: dict[str, Codec] = {
            "step_a": NativeCodec("zlib"),
            "step_b": _FakeCodec(call_fn=lambda d, pm: pm),
        }
        prepared = _make_prepared(pipeline, "encode", codecs)
        data = bytes(range(256)) * 64
        result = run(prepared, {"bytes": data})
        # step_b is identity (returns input), so output is zlib-compressed
        assert result["bytes"] != data
        assert len(result["bytes"]) < len(data)

    def test_encode_only_inputs_skipped_during_decode(self) -> None:
        """Encode-only parameters (level) are not passed during decode."""
        pipeline_dict = {
            "codec_id": "test",
            "direction": "decode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {"level": {"type": "int", "value": 9}},
            "outputs": {"bytes": "compress.bytes"},
            "steps": {
                "compress": {
                    "codec_id": "zlib",
                    "inputs": {
                        "bytes": "input.bytes",
                        "level": "constant.level",
                    },
                }
            },
        }
        # First encode some data
        encode_codec = NativeCodec("zlib")
        data = bytes(range(256)) * 64
        encoded = encode_codec.call("encode", [("bytes", data)])

        pipeline = Pipeline.parse(pipeline_dict)
        prepared = _make_prepared(pipeline, "decode", {"compress": NativeCodec("zlib")})
        result = run(prepared, {"bytes": encoded[0][1]})
        assert result["bytes"] == data


class TestResolverNativeIntegration:
    """Test that the Resolver discovers and instantiates native codecs."""

    def test_resolver_finds_native_codec(self) -> None:
        """Resolver returns NativeCodec when preference includes native."""
        resolver = Resolver(
            codec_store=Path("/nonexistent"),
            preference=["native", "core", "component"],
        )
        codec = resolver.resolve("zlib")
        assert isinstance(codec, NativeCodec)
        assert codec.codec_type == "native"

    def test_resolver_default_preference_selects_native(self) -> None:
        """With default preference, native is preferred over wasm."""
        resolver = Resolver(codec_store=Path("/nonexistent"))
        codec = resolver.resolve("zlib")
        assert isinstance(codec, NativeCodec)

    def test_resolver_override_selects_native(self) -> None:
        """Per-codec override can select native implementation."""
        resolver = Resolver(
            codec_store=Path("/nonexistent"),
            overrides={"zlib": "numcodecs.zlib"},
        )
        codec = resolver.resolve("zlib")
        assert isinstance(codec, NativeCodec)

    def test_resolver_list_includes_native(self) -> None:
        """list_codecs() includes native codec entries."""
        resolver = Resolver(codec_store=Path("/nonexistent"))
        entries = resolver.list_codecs()
        native_entries = [e for e in entries if e.backend == "native"]
        assert len(native_entries) > 0
        codec_ids = {e.codec_id for e in native_entries}
        assert "zlib" in codec_ids
        assert "gzip" in codec_ids

    def test_preference_mismatch_raises(self) -> None:
        """Preference list that excludes available backends raises ValueError."""
        resolver = Resolver(
            codec_store=Path("/nonexistent"),
            preference=["component"],
        )
        with pytest.raises(ValueError, match=r"No implementation.*matches preference"):
            resolver.resolve("zlib")

    def test_resolver_unknown_native_raises(self) -> None:
        """Resolver raises for unknown codec with no wasm and no native."""
        resolver = Resolver(codec_store=Path("/nonexistent"))
        with pytest.raises(ValueError, match="No codec implementation found"):
            resolver.resolve("totally-unknown-codec-xyz")
