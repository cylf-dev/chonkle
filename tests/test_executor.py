"""Tests for DAG executor via Wasmtime Component Model."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from chonkle.executor import run
from chonkle.pipeline import Pipeline

_REPO_ROOT = Path(__file__).parent.parent
_CODEC_DIR = _REPO_ROOT / "codec"

# Tests below this marker require compiled codec .wasm files.
# They will not pass until the codecs are built in a separate session.
CODEC_REQUIRED = pytest.mark.skipif(
    True,
    reason="requires compiled codec .wasm files (built in a separate session)",
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


def _make_simple_pipeline(src: str = "file:///fake.wasm") -> dict:
    """Return a single-step encode pipeline dict."""
    return {
        "codec_id": "test",
        "direction": "encode",
        "inputs": {"bytes": {"type": "bytes"}},
        "constants": {"level": {"type": "int", "value": 3}},
        "outputs": {"bytes": "codec.bytes"},
        "steps": [
            {
                "name": "codec",
                "codec_id": "codec",
                "src": src,
                "inputs": {"bytes": "input.bytes", "level": "constant.level"},
                "outputs": ["bytes"],
                "encode_only_inputs": ["level"],
            }
        ],
    }


def _make_decode_pipeline(src: str = "file:///fake.wasm") -> dict:
    """Return a single-step decode pipeline dict (decode-declared)."""
    return {
        "codec_id": "test",
        "direction": "decode",
        "inputs": {"bytes": {"type": "bytes"}},
        "constants": {"level": {"type": "int", "value": 3}},
        "outputs": {"bytes": "codec.bytes"},
        "steps": [
            {
                "name": "codec",
                "codec_id": "codec",
                "src": src,
                "inputs": {"bytes": "input.bytes", "level": "constant.level"},
                "outputs": ["bytes"],
                "encode_only_inputs": ["level"],
            }
        ],
    }


class TestRunWiring:
    """Verify executor wiring logic using mocked component calls."""

    @pytest.fixture(autouse=True)
    def _skip_signature_validation(self):
        """Patch out signature validation so wiring tests need no sidecar files."""
        with patch("chonkle.executor._validate_signature"):
            yield

    def test_passes_input_bytes_to_step(self, raw_chunk: bytes) -> None:
        pipeline = Pipeline.parse(_make_simple_pipeline())
        received: list = []

        def fake_call(_engine, wasm_path, direction, port_map):
            received.append((direction, list(port_map)))
            return [("bytes", b"compressed")]

        with (
            patch("chonkle.executor.resolve_uri", return_value=Path("/fake.wasm")),
            patch("chonkle.executor._call_component", side_effect=fake_call),
        ):
            result = run(pipeline, {"bytes": raw_chunk}, pipeline.direction)

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
            "steps": [
                {
                    "name": "step_a",
                    "codec_id": "some-codec",
                    "src": "file:///a.wasm",
                    "inputs": {"bytes": "input.bytes"},
                    "outputs": ["bytes"],
                },
                {
                    "name": "step_b",
                    "codec_id": "some-codec",
                    "src": "file:///b.wasm",
                    "inputs": {"bytes": "step_a.bytes"},
                    "outputs": ["bytes"],
                },
            ],
        }
        pipeline = Pipeline.parse(data)
        call_log: list = []

        def fake_call(_engine, wasm_path, direction, port_map):
            call_log.append((wasm_path.name, list(port_map)))
            if wasm_path.name == "a.wasm":
                return [("bytes", b"after_a")]
            return [("bytes", b"after_b")]

        with (
            patch(
                "chonkle.executor.resolve_uri",
                side_effect=lambda uri, **kw: Path("/" + uri.removeprefix("file:///")),
            ),
            patch("chonkle.executor._call_component", side_effect=fake_call),
        ):
            result = run(pipeline, {"bytes": b"original"}, pipeline.direction)

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
            "steps": [
                {
                    "name": "s",
                    "codec_id": "some-codec",
                    "src": "file:///s.wasm",
                    "inputs": {
                        "bytes": "input.bytes",
                        "level": "constant.level",
                    },
                    "outputs": ["bytes"],
                }
            ],
        }
        pipeline = Pipeline.parse(data)
        received_pm: list = []

        def fake_call(_engine, wasm_path, direction, port_map):
            received_pm.extend(port_map)
            return [("bytes", b"output")]

        with (
            patch("chonkle.executor.resolve_uri", return_value=Path("/s.wasm")),
            patch("chonkle.executor._call_component", side_effect=fake_call),
        ):
            run(pipeline, {"bytes": b"data"}, pipeline.direction)

        pm_dict = dict(received_pm)
        assert pm_dict["level"] == b"5"

    def test_encode_only_inputs_skipped_during_decode(self) -> None:
        data = {
            "codec_id": "test",
            "direction": "decode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {"sym_table": {"type": "string", "value": "abc"}},
            "outputs": {"bytes": "s.bytes"},
            "steps": [
                {
                    "name": "s",
                    "codec_id": "some-codec",
                    "src": "file:///s.wasm",
                    "inputs": {
                        "bytes": "input.bytes",
                        "sym_table": "constant.sym_table",
                    },
                    "outputs": ["bytes"],
                    "encode_only_inputs": ["sym_table"],
                }
            ],
        }
        pipeline = Pipeline.parse(data)
        received_pm: list = []

        def fake_call(_engine, wasm_path, direction, port_map):
            received_pm.extend(port_map)
            return [("bytes", b"decoded")]

        with (
            patch("chonkle.executor.resolve_uri", return_value=Path("/s.wasm")),
            patch("chonkle.executor._call_component", side_effect=fake_call),
        ):
            run(pipeline, {"bytes": b"encoded"}, pipeline.direction)

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
            "steps": [
                {
                    "name": "s",
                    "codec_id": "some-codec",
                    "src": "file:///s.wasm",
                    "inputs": {
                        "bytes": "input.bytes",
                        "sym_table": "constant.sym_table",
                    },
                    "outputs": ["bytes"],
                    "encode_only_inputs": ["sym_table"],
                }
            ],
        }
        pipeline = Pipeline.parse(data)
        received_pm: list = []

        def fake_call(_engine, wasm_path, direction, port_map):
            received_pm.extend(port_map)
            return [("bytes", b"encoded")]

        with (
            patch("chonkle.executor.resolve_uri", return_value=Path("/s.wasm")),
            patch("chonkle.executor._call_component", side_effect=fake_call),
        ):
            run(pipeline, {"bytes": b"data"}, pipeline.direction)

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
            "steps": [
                {
                    "name": "s",
                    "codec_id": "some-codec",
                    "src": "file:///s.wasm",
                    "inputs": {"bytes": "input.bytes"},
                    "outputs": ["bytes"],
                }
            ],
        }
        pipeline = Pipeline.parse(data)

        with (
            patch("chonkle.executor.resolve_uri", return_value=Path("/s.wasm")),
            patch(
                "chonkle.executor._call_component",
                return_value=[("bytes", b"decoded")],
            ),
        ):
            # Must not raise despite "dtype" being absent from inputs
            result = run(pipeline, {"bytes": b"encoded"}, pipeline.direction)

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
            "steps": [
                {
                    "name": "s",
                    "codec_id": "some-codec",
                    "src": "file:///s.wasm",
                    "inputs": {"bytes": "input.bytes"},
                    "outputs": ["bytes"],
                }
            ],
        }
        pipeline = Pipeline.parse(data)

        with pytest.raises(ValueError, match="Missing pipeline input"):
            run(pipeline, {"bytes": b"data"}, pipeline.direction)

    def test_missing_input_raises(self) -> None:
        pipeline = Pipeline.parse(_make_simple_pipeline())
        with pytest.raises(ValueError, match="Missing pipeline input"):
            run(pipeline, {}, pipeline.direction)

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
            "steps": [
                {
                    "name": "split",
                    "codec_id": "page-split",
                    "src": "file:///split.wasm",
                    "inputs": {"bytes": "input.bytes"},
                    "outputs": ["a", "b"],
                },
                {
                    "name": "proc_a",
                    "codec_id": "identity",
                    "src": "file:///id.wasm",
                    "inputs": {"bytes": "split.a"},
                    "outputs": ["bytes"],
                },
                {
                    "name": "proc_b",
                    "codec_id": "identity",
                    "src": "file:///id.wasm",
                    "inputs": {"bytes": "split.b"},
                    "outputs": ["bytes"],
                },
            ],
        }
        pipeline = Pipeline.parse(data)

        def fake_call(_engine, wasm_path, direction, port_map):
            if wasm_path.name == "split.wasm":
                data_bytes = port_map[0][1]
                mid = len(data_bytes) // 2
                return [("a", data_bytes[:mid]), ("b", data_bytes[mid:])]
            # identity: return input unchanged
            return port_map

        with (
            patch(
                "chonkle.executor.resolve_uri",
                side_effect=lambda uri, **kw: Path("/" + uri.removeprefix("file:///")),
            ),
            patch("chonkle.executor._call_component", side_effect=fake_call),
        ):
            result = run(pipeline, {"bytes": b"abcdefgh"}, pipeline.direction)

        assert result["a_out"] == b"abcd"
        assert result["b_out"] == b"efgh"


class TestResolveUriIntegration:
    @pytest.fixture(autouse=True)
    def _skip_signature_validation(self):
        """Patch out signature validation so URI tests need no sidecar files."""
        with patch("chonkle.executor._validate_signature"):
            yield

    def test_https_uri_calls_resolve_uri(self) -> None:
        """https:// URIs are passed to resolve_uri for fetch-caching."""
        pipeline_data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "s.bytes"},
            "steps": [
                {
                    "name": "s",
                    "codec_id": "some-codec",
                    "src": "https://example.com/codec.wasm",
                    "inputs": {"bytes": "input.bytes"},
                    "outputs": ["bytes"],
                }
            ],
        }
        pipeline = Pipeline.parse(pipeline_data)

        with (
            patch(
                "chonkle.executor.resolve_uri", return_value=Path("/fake.wasm")
            ) as mock_resolve,
            patch(
                "chonkle.executor._call_component",
                return_value=[("bytes", b"out")],
            ),
        ):
            run(pipeline, {"bytes": b"data"}, pipeline.direction)

        mock_resolve.assert_called_once_with(
            "https://example.com/codec.wasm", force_download=False
        )

    def test_force_download_propagated(self) -> None:
        """force_download=True is forwarded to resolve_uri."""
        pipeline = Pipeline.parse(_make_simple_pipeline())

        with (
            patch(
                "chonkle.executor.resolve_uri", return_value=Path("/fake.wasm")
            ) as mock_resolve,
            patch(
                "chonkle.executor._call_component",
                return_value=[("bytes", b"out")],
            ),
        ):
            run(pipeline, {"bytes": b"data"}, pipeline.direction, force_download=True)

        mock_resolve.assert_called_once_with("file:///fake.wasm", force_download=True)


class TestSignatureValidation:
    """Signature format: protospec codec signature — inputs/outputs are dicts."""

    def _make_pipeline(
        self,
        wasm_file: Path,
        *,
        direction: str = "encode",
        step_inputs: dict | None = None,
        step_outputs: list | None = None,
        encode_only_inputs: list | None = None,
    ) -> Pipeline:
        """Build a single-step pipeline for signature validation tests."""
        if step_inputs is None:
            step_inputs = {"bytes": "input.bytes"}
        if step_outputs is None:
            step_outputs = ["bytes"]
        step: dict = {
            "name": "s",
            "codec_id": "some-codec",
            "src": f"file:///{wasm_file}",
            "inputs": step_inputs,
            "outputs": list(step_outputs),
        }
        if encode_only_inputs is not None:
            step["encode_only_inputs"] = encode_only_inputs
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
        return Pipeline.parse(
            {
                "codec_id": "test",
                "direction": direction,
                "inputs": pipeline_inputs,
                "constants": {},
                "outputs": {"bytes": f"s.{step_outputs[0]}"},
                "steps": [step],
            }
        )

    def test_signature_mismatch_raises(self, tmp_path: Path) -> None:
        """A signature with an unknown output port name raises ValueError."""
        wasm_file = tmp_path / "codec.wasm"
        wasm_file.write_bytes(b"\x00asm\x0d\x00\x01\x00")
        signature = {
            "inputs": {"bytes": {"type": "bytes", "required": True}},
            "outputs": {"wrong_port": {"type": "bytes"}},
        }
        (tmp_path / "codec.signature.json").write_text(json.dumps(signature))
        pipeline = self._make_pipeline(wasm_file, step_outputs=["bytes"])

        with (
            patch("chonkle.executor.resolve_uri", return_value=wasm_file),
            pytest.raises(ValueError, match="not valid signature outputs"),
        ):
            run(pipeline, {"bytes": b"data"}, pipeline.direction)

    def test_matching_signature_does_not_raise(self, tmp_path: Path) -> None:
        """A signature matching declared ports does not raise."""
        wasm_file = tmp_path / "codec.wasm"
        wasm_file.write_bytes(b"\x00asm\x0d\x00\x01\x00")
        signature = {
            "inputs": {
                "bytes": {"type": "bytes", "required": True},
                "level": {"type": "int", "required": False, "encode_only": True},
            },
            "outputs": {"bytes": {"type": "bytes"}},
        }
        (tmp_path / "codec.signature.json").write_text(json.dumps(signature))
        pipeline = self._make_pipeline(
            wasm_file,
            step_inputs={"bytes": "input.bytes"},
            step_outputs=["bytes"],
        )

        with (
            patch("chonkle.executor.resolve_uri", return_value=wasm_file),
            patch("chonkle.executor._call_component", return_value=[("bytes", b"out")]),
        ):
            result = run(pipeline, {"bytes": b"data"}, pipeline.direction)

        assert result == {"bytes": b"out"}

    def test_signature_unknown_input_raises(self, tmp_path: Path) -> None:
        """A step wiring a port not in the signature inputs raises ValueError."""
        wasm_file = tmp_path / "codec.wasm"
        wasm_file.write_bytes(b"\x00asm\x0d\x00\x01\x00")
        signature = {
            "inputs": {"bytes": {"type": "bytes", "required": True}},
            "outputs": {"bytes": {"type": "bytes"}},
        }
        (tmp_path / "codec.signature.json").write_text(json.dumps(signature))
        # Step wires "wrong" which is not in signature inputs
        pipeline = self._make_pipeline(
            wasm_file,
            step_inputs={"wrong": "input.bytes"},
            step_outputs=["bytes"],
        )

        with (
            patch("chonkle.executor.resolve_uri", return_value=wasm_file),
            pytest.raises(ValueError, match="not valid signature encode inputs"),
        ):
            run(pipeline, {"bytes": b"data"}, pipeline.direction)

    def test_signature_encode_only_excluded_during_decode(self, tmp_path: Path) -> None:
        """encode_only inputs are excluded from valid decode inputs; no raise."""
        wasm_file = tmp_path / "codec.wasm"
        wasm_file.write_bytes(b"\x00asm\x0d\x00\x01\x00")
        signature = {
            "inputs": {
                "bytes": {"type": "bytes", "required": True},
                "level": {"type": "int", "required": False, "encode_only": True},
            },
            "outputs": {"bytes": {"type": "bytes"}},
        }
        (tmp_path / "codec.signature.json").write_text(json.dumps(signature))
        # Step wires "level" but marks it as encode_only; decode direction skips it
        pipeline = self._make_pipeline(
            wasm_file,
            direction="decode",
            step_inputs={"bytes": "input.bytes", "level": "input.bytes"},
            step_outputs=["bytes"],
            encode_only_inputs=["level"],
        )

        with (
            patch("chonkle.executor.resolve_uri", return_value=wasm_file),
            patch("chonkle.executor._call_component", return_value=[("bytes", b"out")]),
        ):
            result = run(pipeline, {"bytes": b"data"}, pipeline.direction)

        assert result == {"bytes": b"out"}

    def test_signature_subset_output_passes(self, tmp_path: Path) -> None:
        """Declaring fewer outputs than the signature advertises is valid."""
        wasm_file = tmp_path / "codec.wasm"
        wasm_file.write_bytes(b"\x00asm\x0d\x00\x01\x00")
        signature = {
            "inputs": {"bytes": {"type": "bytes", "required": True}},
            "outputs": {"bytes": {"type": "bytes"}, "checksum": {"type": "bytes"}},
        }
        (tmp_path / "codec.signature.json").write_text(json.dumps(signature))
        # Step only declares "bytes", not "checksum"
        pipeline = self._make_pipeline(wasm_file, step_outputs=["bytes"])

        with (
            patch("chonkle.executor.resolve_uri", return_value=wasm_file),
            patch("chonkle.executor._call_component", return_value=[("bytes", b"out")]),
        ):
            result = run(pipeline, {"bytes": b"data"}, pipeline.direction)

        assert result == {"bytes": b"out"}

    def test_signature_outputs_key_absent_skips_check(self, tmp_path: Path) -> None:
        """A signature with no 'outputs' key skips output validation."""
        wasm_file = tmp_path / "codec.wasm"
        wasm_file.write_bytes(b"\x00asm\x0d\x00\x01\x00")
        signature = {"inputs": {"bytes": {"type": "bytes", "required": True}}}
        (tmp_path / "codec.signature.json").write_text(json.dumps(signature))
        pipeline = self._make_pipeline(wasm_file)

        with (
            patch("chonkle.executor.resolve_uri", return_value=wasm_file),
            patch("chonkle.executor._call_component", return_value=[("bytes", b"out")]),
        ):
            result = run(pipeline, {"bytes": b"data"}, pipeline.direction)

        assert result == {"bytes": b"out"}

    def test_signature_inputs_key_absent_skips_check(self, tmp_path: Path) -> None:
        """A signature with no 'inputs' key skips input validation."""
        wasm_file = tmp_path / "codec.wasm"
        wasm_file.write_bytes(b"\x00asm\x0d\x00\x01\x00")
        signature = {"outputs": {"bytes": {"type": "bytes"}}}
        (tmp_path / "codec.signature.json").write_text(json.dumps(signature))
        pipeline = self._make_pipeline(wasm_file)

        with (
            patch("chonkle.executor.resolve_uri", return_value=wasm_file),
            patch("chonkle.executor._call_component", return_value=[("bytes", b"out")]),
        ):
            result = run(pipeline, {"bytes": b"data"}, pipeline.direction)

        assert result == {"bytes": b"out"}

    def test_signature_both_wrong_reports_both(self, tmp_path: Path) -> None:
        """When both inputs and outputs are wrong, the error reports both."""
        wasm_file = tmp_path / "codec.wasm"
        wasm_file.write_bytes(b"\x00asm\x0d\x00\x01\x00")
        signature = {
            "inputs": {"bytes": {"type": "bytes", "required": True}},
            "outputs": {"bytes": {"type": "bytes"}},
        }
        (tmp_path / "codec.signature.json").write_text(json.dumps(signature))
        pipeline = self._make_pipeline(
            wasm_file,
            step_inputs={"bad_in": "input.bytes"},
            step_outputs=["bad_out"],
        )

        with (
            patch("chonkle.executor.resolve_uri", return_value=wasm_file),
            pytest.raises(
                ValueError, match="not valid signature encode inputs"
            ) as exc_info,
        ):
            run(pipeline, {"bytes": b"data"}, pipeline.direction)

        assert "not valid signature outputs" in str(exc_info.value)

    def test_missing_signature_raises(self, tmp_path: Path) -> None:
        """A codec with no .signature.json sidecar raises ValueError."""
        wasm_file = tmp_path / "codec.wasm"
        wasm_file.write_bytes(b"\x00asm\x0d\x00\x01\x00")
        # Deliberately omit codec.signature.json
        pipeline = self._make_pipeline(wasm_file)

        with (
            patch("chonkle.executor.resolve_uri", return_value=wasm_file),
            pytest.raises(ValueError, match="signature not found"),
        ):
            run(pipeline, {"bytes": b"data"}, pipeline.direction)

    def test_two_steps_both_wrong_reports_both(self, tmp_path: Path) -> None:
        """Signature errors from multiple steps are all reported in one exception."""
        wasm_a = tmp_path / "codec_a.wasm"
        wasm_b = tmp_path / "codec_b.wasm"
        wasm_a.write_bytes(b"\x00asm\x0d\x00\x01\x00")
        wasm_b.write_bytes(b"\x00asm\x0d\x00\x01\x00")
        signature = {
            "inputs": {"bytes": {"type": "bytes", "required": True}},
            "outputs": {"bytes": {"type": "bytes"}},
        }
        (tmp_path / "codec_a.signature.json").write_text(json.dumps(signature))
        (tmp_path / "codec_b.signature.json").write_text(json.dumps(signature))

        pipeline = Pipeline.parse(
            {
                "codec_id": "test",
                "direction": "encode",
                "inputs": {"bytes": {"type": "bytes"}},
                "constants": {},
                "outputs": {"bytes": "step_b.bytes"},
                "steps": [
                    {
                        "name": "step_a",
                        "codec_id": "some-codec",
                        "src": f"file:///{wasm_a}",
                        "inputs": {"bad_a": "input.bytes"},
                        "outputs": ["bytes"],
                    },
                    {
                        "name": "step_b",
                        "codec_id": "some-codec",
                        "src": f"file:///{wasm_b}",
                        "inputs": {"bad_b": "step_a.bytes"},
                        "outputs": ["bytes"],
                    },
                ],
            }
        )

        with (
            patch(
                "chonkle.executor.resolve_uri",
                side_effect=lambda uri, **kw: Path(uri.removeprefix("file:///")),
            ),
            pytest.raises(ValueError, match="step_a") as exc_info,
        ):
            run(pipeline, {"bytes": b"data"}, pipeline.direction)

        assert "step_b" in str(exc_info.value)

    def test_signature_input_port_missing_type_raises(self, tmp_path: Path) -> None:
        """A signature input port without a 'type' field raises ValueError."""
        wasm_file = tmp_path / "codec.wasm"
        wasm_file.write_bytes(b"\x00asm\x0d\x00\x01\x00")
        signature = {
            "inputs": {"bytes": {"required": True}},  # no "type"
            "outputs": {"bytes": {"type": "bytes"}},
        }
        (tmp_path / "codec.signature.json").write_text(json.dumps(signature))
        pipeline = self._make_pipeline(wasm_file)

        with (
            patch("chonkle.executor.resolve_uri", return_value=wasm_file),
            pytest.raises(ValueError, match="missing required 'type' field"),
        ):
            run(pipeline, {"bytes": b"data"}, pipeline.direction)

    def test_signature_output_port_missing_type_raises(self, tmp_path: Path) -> None:
        """A signature output port without a 'type' field raises ValueError."""
        wasm_file = tmp_path / "codec.wasm"
        wasm_file.write_bytes(b"\x00asm\x0d\x00\x01\x00")
        signature = {
            "inputs": {"bytes": {"type": "bytes", "required": True}},
            "outputs": {"bytes": {}},  # no "type"
        }
        (tmp_path / "codec.signature.json").write_text(json.dumps(signature))
        pipeline = self._make_pipeline(wasm_file)

        with (
            patch("chonkle.executor.resolve_uri", return_value=wasm_file),
            pytest.raises(ValueError, match="missing required 'type' field"),
        ):
            run(pipeline, {"bytes": b"data"}, pipeline.direction)

    def test_type_mismatch_pipeline_input_raises(self, tmp_path: Path) -> None:
        """Pipeline input type mismatch against codec signature raises ValueError."""
        wasm_file = tmp_path / "codec.wasm"
        wasm_file.write_bytes(b"\x00asm\x0d\x00\x01\x00")
        signature = {
            "inputs": {"data": {"type": "bytes", "required": True}},
            "outputs": {"data": {"type": "bytes"}},
        }
        (tmp_path / "codec.signature.json").write_text(json.dumps(signature))
        # Pipeline declares input "data" as type "int"; codec expects "bytes".
        pipeline = Pipeline.parse(
            {
                "codec_id": "test",
                "direction": "encode",
                "inputs": {"data": {"type": "int"}},
                "constants": {},
                "outputs": {"data": "s.data"},
                "steps": [
                    {
                        "name": "s",
                        "codec_id": "some-codec",
                        "src": f"file:///{wasm_file}",
                        "inputs": {"data": "input.data"},
                        "outputs": ["data"],
                    }
                ],
            }
        )

        with (
            patch("chonkle.executor.resolve_uri", return_value=wasm_file),
            pytest.raises(ValueError, match="provides type"),
        ):
            run(pipeline, {"data": b"payload"}, pipeline.direction)

    def test_type_mismatch_constant_raises(self, tmp_path: Path) -> None:
        """Constant type mismatch against codec signature raises ValueError."""
        wasm_file = tmp_path / "codec.wasm"
        wasm_file.write_bytes(b"\x00asm\x0d\x00\x01\x00")
        signature = {
            "inputs": {
                "bytes": {"type": "bytes", "required": True},
                "level": {"type": "bytes", "required": False},
            },
            "outputs": {"bytes": {"type": "bytes"}},
        }
        (tmp_path / "codec.signature.json").write_text(json.dumps(signature))
        # Constant "level" has type "int"; codec expects "bytes".
        pipeline = Pipeline.parse(
            {
                "codec_id": "test",
                "direction": "encode",
                "inputs": {"bytes": {"type": "bytes"}},
                "constants": {"level": {"type": "int", "value": 3}},
                "outputs": {"bytes": "s.bytes"},
                "steps": [
                    {
                        "name": "s",
                        "codec_id": "some-codec",
                        "src": f"file:///{wasm_file}",
                        "inputs": {"bytes": "input.bytes", "level": "constant.level"},
                        "outputs": ["bytes"],
                    }
                ],
            }
        )

        with (
            patch("chonkle.executor.resolve_uri", return_value=wasm_file),
            pytest.raises(ValueError, match="provides type"),
        ):
            run(pipeline, {"bytes": b"data"}, pipeline.direction)

    def test_type_mismatch_step_output_raises(self, tmp_path: Path) -> None:
        """Step output type mismatch against downstream codec input raises."""
        wasm_a = tmp_path / "codec_a.wasm"
        wasm_b = tmp_path / "codec_b.wasm"
        wasm_a.write_bytes(b"\x00asm\x0d\x00\x01\x00")
        wasm_b.write_bytes(b"\x00asm\x0d\x00\x01\x00")
        sig_a = {
            "inputs": {"bytes": {"type": "bytes", "required": True}},
            "outputs": {"bytes": {"type": "bytes"}},
        }
        sig_b = {
            # Expects "int" but step_a outputs "bytes".
            "inputs": {"bytes": {"type": "int", "required": True}},
            "outputs": {"bytes": {"type": "int"}},
        }
        (tmp_path / "codec_a.signature.json").write_text(json.dumps(sig_a))
        (tmp_path / "codec_b.signature.json").write_text(json.dumps(sig_b))

        pipeline = Pipeline.parse(
            {
                "codec_id": "test",
                "direction": "encode",
                "inputs": {"bytes": {"type": "bytes"}},
                "constants": {},
                "outputs": {"bytes": "step_b.bytes"},
                "steps": [
                    {
                        "name": "step_a",
                        "codec_id": "codec-a",
                        "src": f"file:///{wasm_a}",
                        "inputs": {"bytes": "input.bytes"},
                        "outputs": ["bytes"],
                    },
                    {
                        "name": "step_b",
                        "codec_id": "codec-b",
                        "src": f"file:///{wasm_b}",
                        "inputs": {"bytes": "step_a.bytes"},
                        "outputs": ["bytes"],
                    },
                ],
            }
        )

        with (
            patch(
                "chonkle.executor.resolve_uri",
                side_effect=lambda uri, **kw: Path(uri.removeprefix("file:///")),
            ),
            pytest.raises(ValueError, match="provides type"),
        ):
            run(pipeline, {"bytes": b"data"}, pipeline.direction)

    def test_type_match_passes(self, tmp_path: Path) -> None:
        """Matching types across pipeline input and codec signature do not raise."""
        wasm_file = tmp_path / "codec.wasm"
        wasm_file.write_bytes(b"\x00asm\x0d\x00\x01\x00")
        signature = {
            "inputs": {"bytes": {"type": "bytes", "required": True}},
            "outputs": {"bytes": {"type": "bytes"}},
        }
        (tmp_path / "codec.signature.json").write_text(json.dumps(signature))
        pipeline = self._make_pipeline(wasm_file)

        with (
            patch("chonkle.executor.resolve_uri", return_value=wasm_file),
            patch("chonkle.executor._call_component", return_value=[("bytes", b"out")]),
        ):
            result = run(pipeline, {"bytes": b"data"}, pipeline.direction)

        assert result == {"bytes": b"out"}

    def test_missing_type_in_source_skips_check(self, tmp_path: Path) -> None:
        """Pipeline input with no 'type' key skips type check without raising."""
        wasm_file = tmp_path / "codec.wasm"
        wasm_file.write_bytes(b"\x00asm\x0d\x00\x01\x00")
        signature = {
            "inputs": {"bytes": {"type": "bytes", "required": True}},
            "outputs": {"bytes": {"type": "bytes"}},
        }
        (tmp_path / "codec.signature.json").write_text(json.dumps(signature))
        # Pipeline input descriptor has no "type" key.
        pipeline = Pipeline.parse(
            {
                "codec_id": "test",
                "direction": "encode",
                "inputs": {"bytes": {}},
                "constants": {},
                "outputs": {"bytes": "s.bytes"},
                "steps": [
                    {
                        "name": "s",
                        "codec_id": "some-codec",
                        "src": f"file:///{wasm_file}",
                        "inputs": {"bytes": "input.bytes"},
                        "outputs": ["bytes"],
                    }
                ],
            }
        )

        with (
            patch("chonkle.executor.resolve_uri", return_value=wasm_file),
            patch("chonkle.executor._call_component", return_value=[("bytes", b"out")]),
        ):
            result = run(pipeline, {"bytes": b"data"}, pipeline.direction)

        assert result == {"bytes": b"out"}


class TestInvertedExecution:
    """Verify inverted DAG execution (direction != pipeline.direction)."""

    @pytest.fixture(autouse=True)
    def _skip_signature_validation(self):
        with patch("chonkle.executor._validate_signature"):
            yield

    def test_inverted_single_step_calls_encode(self) -> None:
        """A decode-declared pipeline run as encode calls encode on the codec."""
        pipeline = Pipeline.parse(_make_decode_pipeline())
        received_directions: list[str] = []

        def fake_call(_engine, wasm_path, direction, port_map):
            received_directions.append(direction)
            return [("bytes", b"raw")]

        with (
            patch("chonkle.executor.resolve_uri", return_value=Path("/fake.wasm")),
            patch("chonkle.executor._call_component", side_effect=fake_call),
        ):
            run(pipeline, {"bytes": b"compressed"}, "encode")

        assert received_directions == ["encode"]

    def test_inverted_port_map_built_from_step_outputs(self) -> None:
        """Inverted encode: port-map is built from step.outputs, not step.inputs."""
        pipeline = Pipeline.parse(_make_decode_pipeline())
        received_port_maps: list = []

        def fake_call(_engine, wasm_path, direction, port_map):
            received_port_maps.append(list(port_map))
            return [("bytes", b"raw")]

        with (
            patch("chonkle.executor.resolve_uri", return_value=Path("/fake.wasm")),
            patch("chonkle.executor._call_component", side_effect=fake_call),
        ):
            run(pipeline, {"bytes": b"compressed"}, "encode")

        assert len(received_port_maps) == 1
        pm = dict(received_port_maps[0])
        # "bytes" is the step's output port name — it becomes the encode input
        assert "bytes" in pm
        assert pm["bytes"] == b"compressed"
        # "level" is encode_only, included in encode direction
        assert "level" in pm
        assert pm["level"] == b"3"

    def test_inverted_result_keyed_by_pipeline_inputs(self) -> None:
        """Inverted encode: result is keyed by pipeline.inputs names."""
        pipeline = Pipeline.parse(_make_decode_pipeline())

        with (
            patch("chonkle.executor.resolve_uri", return_value=Path("/fake.wasm")),
            patch(
                "chonkle.executor._call_component",
                return_value=[("bytes", b"encoded_result")],
            ),
        ):
            result = run(pipeline, {"bytes": b"raw"}, "encode")

        # pipeline.inputs has key "bytes"; pipeline.outputs also has key "bytes"
        # but the inverted result comes from value_store["input.bytes"]
        assert result == {"bytes": b"encoded_result"}

    def test_inverted_execution_order_reversed(self) -> None:
        """Inverted encode on a two-step decode pipeline runs in reversed order."""
        data = {
            "codec_id": "test",
            "direction": "decode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "step_b.bytes"},
            "steps": [
                {
                    "name": "step_a",
                    "codec_id": "some-codec",
                    "src": "file:///a.wasm",
                    "inputs": {"bytes": "input.bytes"},
                    "outputs": ["bytes"],
                },
                {
                    "name": "step_b",
                    "codec_id": "some-codec",
                    "src": "file:///b.wasm",
                    "inputs": {"bytes": "step_a.bytes"},
                    "outputs": ["bytes"],
                },
            ],
        }
        pipeline = Pipeline.parse(data)
        call_order: list[str] = []

        def fake_call(_engine, wasm_path, direction, port_map):
            call_order.append(wasm_path.name)
            return [("bytes", b"result")]

        with (
            patch(
                "chonkle.executor.resolve_uri",
                side_effect=lambda uri, **kw: Path("/" + uri.removeprefix("file:///")),
            ),
            patch("chonkle.executor._call_component", side_effect=fake_call),
        ):
            run(pipeline, {"bytes": b"raw"}, "encode")

        # Decode order is [step_a, step_b]; encode (inverted) order is [step_b, step_a]
        assert call_order == ["b.wasm", "a.wasm"]

    def test_inverted_encode_routes_results_backward(self) -> None:
        """Inverted encode: step results route via decode-direction input wiring."""
        data = {
            "codec_id": "test",
            "direction": "decode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "step_b.bytes"},
            "steps": [
                {
                    "name": "step_a",
                    "codec_id": "some-codec",
                    "src": "file:///a.wasm",
                    "inputs": {"bytes": "input.bytes"},
                    "outputs": ["bytes"],
                },
                {
                    "name": "step_b",
                    "codec_id": "some-codec",
                    "src": "file:///b.wasm",
                    "inputs": {"bytes": "step_a.bytes"},
                    "outputs": ["bytes"],
                },
            ],
        }
        pipeline = Pipeline.parse(data)
        received: dict[str, list] = {}

        def fake_call(_engine, wasm_path, direction, port_map):
            received[wasm_path.name] = list(port_map)
            if wasm_path.name == "b.wasm":
                return [("bytes", b"b_encoded")]
            return [("bytes", b"a_encoded")]

        with (
            patch(
                "chonkle.executor.resolve_uri",
                side_effect=lambda uri, **kw: Path("/" + uri.removeprefix("file:///")),
            ),
            patch("chonkle.executor._call_component", side_effect=fake_call),
        ):
            result = run(pipeline, {"bytes": b"raw_input"}, "encode")

        # step_b encode receives the seeded value (pipeline.outputs["bytes"])
        assert dict(received["b.wasm"]) == {"bytes": b"raw_input"}
        # step_a encode receives what step_b returned (step_b.inputs["bytes"])
        assert dict(received["a.wasm"]) == {"bytes": b"b_encoded"}
        # final result is from value_store["input.bytes"] — what step_a returned
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
            "steps": [
                {
                    "name": "page_split",
                    "codec_id": "page-split",
                    "src": "file:///split.wasm",
                    "inputs": {"bytes": "input.bytes"},
                    "outputs": ["rep_levels", "def_levels", "data"],
                },
                {
                    "name": "identity_rep",
                    "codec_id": "identity",
                    "src": "file:///id.wasm",
                    "inputs": {"bytes": "page_split.rep_levels"},
                    "outputs": ["bytes"],
                },
                {
                    "name": "identity_def",
                    "codec_id": "identity",
                    "src": "file:///id.wasm",
                    "inputs": {"bytes": "page_split.def_levels"},
                    "outputs": ["bytes"],
                },
                {
                    "name": "identity_data",
                    "codec_id": "identity",
                    "src": "file:///id.wasm",
                    "inputs": {"bytes": "page_split.data"},
                    "outputs": ["bytes"],
                },
            ],
        }
        pipeline = Pipeline.parse(data)
        received: dict[str, list] = {}
        call_count: dict[str, int] = {}

        def fake_call(_engine, wasm_path, direction, port_map):
            name = wasm_path.name
            call_count[name] = call_count.get(name, 0) + 1
            key = f"{name}_{call_count[name]}"
            received[key] = list(port_map)
            if name == "split.wasm":
                # page_split encode combines three inputs into one
                pm = dict(port_map)
                combined = (
                    pm.get("rep_levels", b"")
                    + pm.get("def_levels", b"")
                    + pm.get("data", b"")
                )
                return [("bytes", combined)]
            # identity encode: pass through
            return port_map

        with (
            patch(
                "chonkle.executor.resolve_uri",
                side_effect=lambda uri, **kw: Path("/" + uri.removeprefix("file:///")),
            ),
            patch("chonkle.executor._call_component", side_effect=fake_call),
        ):
            result = run(
                pipeline,
                {"rep": b"RRR", "def_": b"DDD", "data": b"VVV"},
                "encode",
            )

        # Result is keyed by pipeline.inputs name ("bytes")
        assert "bytes" in result
        # Combined output should contain all three segments
        assert b"RRR" in result["bytes"]
        assert b"DDD" in result["bytes"]
        assert b"VVV" in result["bytes"]

    def test_inverted_missing_input_raises(self) -> None:
        """Inverted direction with a missing pipeline.outputs key raises ValueError."""
        pipeline = Pipeline.parse(_make_decode_pipeline())
        # pipeline.outputs has key "bytes"; caller must provide it
        with pytest.raises(ValueError, match="Missing pipeline input"):
            run(pipeline, {}, "encode")

    def test_inverted_encode_only_excluded_from_decode_call(self) -> None:
        """Encode-declared pipeline inverted to decode: encode_only_inputs absent."""
        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {"level": {"type": "int", "value": 9}},
            "outputs": {"bytes": "s.bytes"},
            "steps": [
                {
                    "name": "s",
                    "codec_id": "some-codec",
                    "src": "file:///s.wasm",
                    "inputs": {"bytes": "input.bytes", "level": "constant.level"},
                    "outputs": ["bytes"],
                    "encode_only_inputs": ["level"],
                }
            ],
        }
        pipeline = Pipeline.parse(data)
        received_port_maps: list = []

        def fake_call(_engine, wasm_path, direction, port_map):
            received_port_maps.append(list(port_map))
            return [("bytes", b"decoded")]

        with (
            patch("chonkle.executor.resolve_uri", return_value=Path("/s.wasm")),
            patch("chonkle.executor._call_component", side_effect=fake_call),
        ):
            # Invert encode-declared pipeline to decode direction
            run(pipeline, {"bytes": b"encoded"}, "decode")

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

        # Override constants in the fixture with test-specific values.
        page_split_pipeline_json["constants"]["rep_length"]["value"] = rep_length
        page_split_pipeline_json["constants"]["def_length"]["value"] = def_length

        pipeline = Pipeline.parse(page_split_pipeline_json)
        result = run(pipeline, {"bytes": data}, pipeline.direction)

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

        pipeline = Pipeline.parse(page_split_pipeline_json)
        result = run(pipeline, {"bytes": data}, pipeline.direction)

        assert len(result["rep_levels"]) == rep_length
        assert len(result["def_levels"]) == def_length
        assert len(result["data"]) == len(data) - rep_length - def_length


@COG_CODECS_REQUIRED
class TestCogChunkPipeline:
    """Round-trip and decode tests for the COG zlib+tiff-predictor-2 pipeline."""

    _TILE_BYTES = 1024 * 1024 * 2  # 1024x1024 uint16

    def test_decode_declared_forward_decode_output_size(
        self, cog_chunk: bytes, cog_decode_pipeline_json: dict
    ) -> None:
        """Decode-declared pipeline forward decode: output is 1024x1024x2 bytes."""
        pipeline = Pipeline.parse(cog_decode_pipeline_json)
        result = run(pipeline, {"bytes": cog_chunk}, "decode")
        assert len(result["bytes"]) == self._TILE_BYTES

    def test_decode_declared_forward_decode_output_is_bytes(
        self, cog_chunk: bytes, cog_decode_pipeline_json: dict
    ) -> None:
        """Decode-declared pipeline running forward decode returns a bytes object."""
        pipeline = Pipeline.parse(cog_decode_pipeline_json)
        result = run(pipeline, {"bytes": cog_chunk}, "decode")
        assert isinstance(result["bytes"], bytes)

    def test_encode_declared_inverted_decode_output_size(
        self, cog_chunk: bytes, cog_encode_pipeline_json: dict
    ) -> None:
        """Encode-declared pipeline inverted decode: output is 1024x1024x2 bytes."""
        pipeline = Pipeline.parse(cog_encode_pipeline_json)
        result = run(pipeline, {"bytes": cog_chunk}, "decode")
        assert len(result["bytes"]) == self._TILE_BYTES

    def test_encode_declared_inverted_decode_output_is_bytes(
        self, cog_chunk: bytes, cog_encode_pipeline_json: dict
    ) -> None:
        """Encode-declared pipeline running inverted decode returns a bytes object."""
        pipeline = Pipeline.parse(cog_encode_pipeline_json)
        result = run(pipeline, {"bytes": cog_chunk}, "decode")
        assert isinstance(result["bytes"], bytes)

    def test_decode_declared_pipeline_roundtrip(
        self, raw_chunk: bytes, cog_decode_pipeline_json: dict
    ) -> None:
        """Decode-declared pipeline roundtrip: inverted encode then forward decode.

        raw_chunk is padded to 1024x1024x2 bytes so tiff-predictor-2 can
        interpret it as a full tile (width=1024, bytes_per_sample=2).
        """
        tile = (raw_chunk * ((self._TILE_BYTES // len(raw_chunk)) + 1))[
            : self._TILE_BYTES
        ]
        pipeline = Pipeline.parse(cog_decode_pipeline_json)
        encoded = run(pipeline, {"bytes": tile}, "encode")
        decoded = run(pipeline, {"bytes": encoded["bytes"]}, "decode")
        assert decoded["bytes"] == tile

    def test_encode_declared_pipeline_roundtrip(
        self, raw_chunk: bytes, cog_encode_pipeline_json: dict
    ) -> None:
        """Encode-declared pipeline roundtrip: forward encode then inverted decode.

        raw_chunk is padded to 1024x1024x2 bytes so tiff-predictor-2 can
        interpret it as a full tile (width=1024, bytes_per_sample=2).
        """
        tile = (raw_chunk * ((self._TILE_BYTES // len(raw_chunk)) + 1))[
            : self._TILE_BYTES
        ]
        pipeline = Pipeline.parse(cog_encode_pipeline_json)
        encoded = run(pipeline, {"bytes": tile}, "encode")
        decoded = run(pipeline, {"bytes": encoded["bytes"]}, "decode")
        assert decoded["bytes"] == tile

    def test_encode_declared_inverted_decode_matches_decode_declared_forward(
        self,
        cog_chunk: bytes,
        cog_decode_pipeline_json: dict,
        cog_encode_pipeline_json: dict,
    ) -> None:
        """Encode-declared pipeline inverted decode yields the same raw tile
        as decode-declared pipeline forward decode."""
        decode_pipeline = Pipeline.parse(cog_decode_pipeline_json)
        encode_pipeline = Pipeline.parse(cog_encode_pipeline_json)
        forward = run(decode_pipeline, {"bytes": cog_chunk}, "decode")
        inverted = run(encode_pipeline, {"bytes": cog_chunk}, "decode")
        assert inverted["bytes"] == forward["bytes"]

    def test_decode_declared_inverted_encode_matches_encode_declared_forward(
        self,
        raw_chunk: bytes,
        cog_decode_pipeline_json: dict,
        cog_encode_pipeline_json: dict,
    ) -> None:
        """Decode-declared pipeline inverted encode yields the same compressed bytes
        as encode-declared pipeline forward encode."""
        tile = (raw_chunk * ((self._TILE_BYTES // len(raw_chunk)) + 1))[
            : self._TILE_BYTES
        ]
        decode_pipeline = Pipeline.parse(cog_decode_pipeline_json)
        encode_pipeline = Pipeline.parse(cog_encode_pipeline_json)
        inverted = run(decode_pipeline, {"bytes": tile}, "encode")
        forward = run(encode_pipeline, {"bytes": tile}, "encode")
        assert inverted["bytes"] == forward["bytes"]
