"""Tests for DAG pipeline parsing and wiring validation."""

import pytest

from chonkle.pipeline import Pipeline, WiringRef


class TestParseRef:
    def test_input_ref(self) -> None:
        ref = WiringRef.parse("input.bytes")
        assert ref.kind == "input"
        assert ref.source == "input"
        assert ref.port == "bytes"

    def test_constant_ref(self) -> None:
        ref = WiringRef.parse("constant.level")
        assert ref.kind == "constant"
        assert ref.source == "constant"
        assert ref.port == "level"

    def test_step_ref(self) -> None:
        ref = WiringRef.parse("my_step.out_bytes")
        assert ref.kind == "step"
        assert ref.source == "my_step"
        assert ref.port == "out_bytes"

    def test_step_ref_returns_wiringref(self) -> None:
        ref = WiringRef.parse("zstd.bytes")
        assert isinstance(ref, WiringRef)

    def test_missing_dot_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid wiring reference"):
            WiringRef.parse("noport")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid wiring reference"):
            WiringRef.parse("")


class TestParsePipeline:
    def test_linear_pipeline_from_fixture(self, cog_decode_pipeline_json: dict) -> None:
        pipeline = Pipeline.parse(cog_decode_pipeline_json)
        assert isinstance(pipeline, Pipeline)
        assert pipeline.direction == "decode"
        assert list(pipeline.inputs.keys()) == ["bytes"]
        assert len(pipeline.steps) == 2
        assert pipeline.steps[0].name == "zlib"
        assert pipeline.steps[1].name == "predictor2"
        assert pipeline.execution_order == ["zlib", "predictor2"]

    def test_linear_pipeline_codec_id(self, cog_decode_pipeline_json: dict) -> None:
        pipeline = Pipeline.parse(cog_decode_pipeline_json)
        assert pipeline.codec_id == "cog-zlib-predictor2"

    def test_linear_pipeline_step_fields(self, cog_decode_pipeline_json: dict) -> None:
        pipeline = Pipeline.parse(cog_decode_pipeline_json)
        step = pipeline.steps[0]  # zlib step
        assert step.src.startswith("file://")
        assert step.inputs == {"bytes": "input.bytes", "level": "constant.level"}
        assert step.outputs == ["bytes"]
        assert step.encode_only_inputs == ["level"]

    def test_dag_pipeline_from_fixture(self, page_split_pipeline_json: dict) -> None:
        pipeline = Pipeline.parse(page_split_pipeline_json)
        assert pipeline.direction == "encode"
        assert len(pipeline.steps) == 4  # page_split + 3 identity steps

    def test_dag_pipeline_shared_codec_id(self, page_split_pipeline_json: dict) -> None:
        pipeline = Pipeline.parse(page_split_pipeline_json)
        identity_steps = [s for s in pipeline.steps if s.codec_id == "identity"]
        assert len(identity_steps) == 3
        assert {s.name for s in identity_steps} == {
            "identity_rep",
            "identity_def",
            "identity_data",
        }

    def test_dag_execution_order(self, page_split_pipeline_json: dict) -> None:
        pipeline = Pipeline.parse(page_split_pipeline_json)
        order = pipeline.execution_order
        page_split_idx = order.index("page_split")
        for name in ("identity_rep", "identity_def", "identity_data"):
            assert page_split_idx < order.index(name), f"page_split must precede {name}"

    def test_dag_pipeline_constants(self, page_split_pipeline_json: dict) -> None:
        pipeline = Pipeline.parse(page_split_pipeline_json)
        assert pipeline.constants == {
            "rep_length": {"type": "uint", "value": 128},
            "def_length": {"type": "uint", "value": 256},
        }

    def test_dag_pipeline_outputs(self, page_split_pipeline_json: dict) -> None:
        pipeline = Pipeline.parse(page_split_pipeline_json)
        assert set(pipeline.outputs.keys()) == {"rep_levels", "def_levels", "data"}

    def test_missing_direction_raises(self) -> None:
        with pytest.raises(ValueError, match="direction"):
            Pipeline.parse({"codec_id": "t", "inputs": {}, "steps": []})

    def test_invalid_direction_raises(self) -> None:
        with pytest.raises(ValueError, match="direction"):
            Pipeline.parse(
                {"codec_id": "t", "direction": "transform", "inputs": {}, "steps": []}
            )

    def test_missing_codec_id_raises(self) -> None:
        with pytest.raises(ValueError, match="codec_id"):
            Pipeline.parse(
                {
                    "direction": "encode",
                    "inputs": {"bytes": {"type": "bytes"}},
                    "constants": {},
                    "outputs": {"bytes": "s.bytes"},
                    "steps": [
                        {
                            "name": "s",
                            "codec_id": "some-codec",
                            "src": "file:///fake.wasm",
                            "inputs": {"bytes": "input.bytes"},
                            "outputs": ["bytes"],
                        }
                    ],
                }
            )

    def test_parse_accepts_dict(self) -> None:
        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {"bytes": "s.bytes"},
            "steps": [
                {
                    "name": "s",
                    "codec_id": "some-codec",
                    "src": "file:///fake.wasm",
                    "inputs": {"bytes": "input.bytes"},
                    "outputs": ["bytes"],
                }
            ],
        }
        pipeline = Pipeline.parse(data)
        assert pipeline.direction == "encode"


class TestWiringValidation:
    def _base(self) -> dict:
        return {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {},
            "steps": [],
        }

    def test_valid_input_ref(self) -> None:
        data = self._base()
        data["steps"] = [
            {
                "name": "s",
                "codec_id": "some-codec",
                "src": "file:///fake.wasm",
                "inputs": {"bytes": "input.bytes"},
                "outputs": ["bytes"],
            }
        ]
        data["outputs"] = {"bytes": "s.bytes"}
        pipeline = Pipeline.parse(data)
        assert pipeline.execution_order == ["s"]

    def test_valid_constant_ref(self) -> None:
        data = self._base()
        data["constants"] = {"level": {"type": "int", "value": 3}}
        data["steps"] = [
            {
                "name": "s",
                "codec_id": "some-codec",
                "src": "file:///fake.wasm",
                "inputs": {"bytes": "input.bytes", "level": "constant.level"},
                "outputs": ["bytes"],
            }
        ]
        pipeline = Pipeline.parse(data)
        assert len(pipeline.steps) == 1

    def test_valid_step_to_step_ref(self) -> None:
        data = self._base()
        data["steps"] = [
            {
                "name": "a",
                "codec_id": "some-codec",
                "src": "file:///a.wasm",
                "inputs": {"bytes": "input.bytes"},
                "outputs": ["bytes"],
            },
            {
                "name": "b",
                "codec_id": "some-codec",
                "src": "file:///b.wasm",
                "inputs": {"bytes": "a.bytes"},
                "outputs": ["bytes"],
            },
        ]
        pipeline = Pipeline.parse(data)
        assert pipeline.execution_order.index("a") < pipeline.execution_order.index("b")

    def test_same_codec_id_different_names_valid(self) -> None:
        """The same codec may appear multiple times with distinct step names."""
        data = self._base()
        data["steps"] = [
            {
                "name": "proc_a",
                "codec_id": "identity",
                "src": "file:///identity.wasm",
                "inputs": {"bytes": "input.bytes"},
                "outputs": ["bytes"],
            },
            {
                "name": "proc_b",
                "codec_id": "identity",
                "src": "file:///identity.wasm",
                "inputs": {"bytes": "input.bytes"},
                "outputs": ["bytes"],
            },
        ]
        pipeline = Pipeline.parse(data)
        assert {s.name for s in pipeline.steps} == {"proc_a", "proc_b"}
        assert all(s.codec_id == "identity" for s in pipeline.steps)

    def test_undefined_input_raises(self) -> None:
        data = self._base()
        data["steps"] = [
            {
                "name": "s",
                "codec_id": "some-codec",
                "src": "file:///fake.wasm",
                "inputs": {"bytes": "input.missing"},
                "outputs": ["bytes"],
            }
        ]
        with pytest.raises(ValueError, match="input 'missing' is not declared"):
            Pipeline.parse(data)

    def test_undefined_constant_raises(self) -> None:
        data = self._base()
        data["steps"] = [
            {
                "name": "s",
                "codec_id": "some-codec",
                "src": "file:///fake.wasm",
                "inputs": {"n": "constant.missing"},
                "outputs": ["bytes"],
            }
        ]
        with pytest.raises(ValueError, match="constant 'missing' is not declared"):
            Pipeline.parse(data)

    def test_undefined_step_raises(self) -> None:
        data = self._base()
        data["steps"] = [
            {
                "name": "s",
                "codec_id": "some-codec",
                "src": "file:///fake.wasm",
                "inputs": {"bytes": "ghost.bytes"},
                "outputs": ["bytes"],
            }
        ]
        with pytest.raises(ValueError, match="step 'ghost' does not exist"):
            Pipeline.parse(data)

    def test_undefined_step_output_port_raises(self) -> None:
        data = self._base()
        data["steps"] = [
            {
                "name": "a",
                "codec_id": "some-codec",
                "src": "file:///a.wasm",
                "inputs": {"bytes": "input.bytes"},
                "outputs": ["bytes"],
            },
            {
                "name": "b",
                "codec_id": "some-codec",
                "src": "file:///b.wasm",
                "inputs": {"bytes": "a.nonexistent"},
                "outputs": ["bytes"],
            },
        ]
        with pytest.raises(
            ValueError, match="does not declare output port 'nonexistent'"
        ):
            Pipeline.parse(data)

    def test_pipeline_output_input_passthrough(self) -> None:
        """A pipeline output may reference a pipeline input directly (passthrough)."""
        data = self._base()
        data["outputs"] = {"bytes": "input.bytes"}
        pipeline = Pipeline.parse(data)
        assert pipeline.outputs == {"bytes": "input.bytes"}

    def test_pipeline_output_constant_passthrough(self) -> None:
        """A pipeline output may reference a constant directly."""
        data = self._base()
        data["constants"] = {"level": {"type": "int", "value": 3}}
        data["outputs"] = {"level": "constant.level"}
        pipeline = Pipeline.parse(data)
        assert pipeline.outputs == {"level": "constant.level"}

    def test_undefined_pipeline_output_ref_raises(self) -> None:
        data = self._base()
        data["steps"] = [
            {
                "name": "s",
                "codec_id": "some-codec",
                "src": "file:///fake.wasm",
                "inputs": {"bytes": "input.bytes"},
                "outputs": ["bytes"],
            }
        ]
        data["outputs"] = {"out": "s.missing_port"}
        with pytest.raises(
            ValueError, match="does not declare output port 'missing_port'"
        ):
            Pipeline.parse(data)

    def test_encode_only_input_not_in_inputs_raises(self) -> None:
        """encode_only_input absent from step inputs raises at parse time."""
        data = self._base()
        data["steps"] = [
            {
                "name": "s",
                "codec_id": "some-codec",
                "src": "file:///fake.wasm",
                "inputs": {"bytes": "input.bytes"},
                "outputs": ["bytes"],
                "encode_only_inputs": ["level"],  # "level" not in inputs
            }
        ]
        with pytest.raises(
            ValueError, match="encode_only_input 'level' is not declared in inputs"
        ):
            Pipeline.parse(data)

    def test_duplicate_step_name_raises(self) -> None:
        data = self._base()
        data["steps"] = [
            {
                "name": "s",
                "codec_id": "some-codec",
                "src": "file:///fake.wasm",
                "inputs": {"bytes": "input.bytes"},
                "outputs": ["bytes"],
            },
            {
                "name": "s",
                "codec_id": "other-codec",
                "src": "file:///fake.wasm",
                "inputs": {"bytes": "input.bytes"},
                "outputs": ["bytes"],
            },
        ]
        with pytest.raises(ValueError, match="Duplicate step name"):
            Pipeline.parse(data)

    def test_cycle_detection(self) -> None:
        data = self._base()
        data["steps"] = [
            {
                "name": "a",
                "codec_id": "some-codec",
                "src": "file:///a.wasm",
                "inputs": {"bytes": "b.bytes"},
                "outputs": ["bytes"],
            },
            {
                "name": "b",
                "codec_id": "some-codec",
                "src": "file:///b.wasm",
                "inputs": {"bytes": "a.bytes"},
                "outputs": ["bytes"],
            },
        ]
        with pytest.raises(ValueError, match="cycle"):
            Pipeline.parse(data)


class TestTopologicalOrder:
    def test_linear_order_regardless_of_declaration_order(self) -> None:
        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"x": {"type": "bytes"}},
            "constants": {},
            "outputs": {"x": "c.x"},
            "steps": [
                {
                    "name": "c",
                    "codec_id": "some-codec",
                    "src": "file:///c.wasm",
                    "inputs": {"x": "b.x"},
                    "outputs": ["x"],
                },
                {
                    "name": "b",
                    "codec_id": "some-codec",
                    "src": "file:///b.wasm",
                    "inputs": {"x": "a.x"},
                    "outputs": ["x"],
                },
                {
                    "name": "a",
                    "codec_id": "some-codec",
                    "src": "file:///a.wasm",
                    "inputs": {"x": "input.x"},
                    "outputs": ["x"],
                },
            ],
        }
        pipeline = Pipeline.parse(data)
        order = pipeline.execution_order
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")

    def test_fan_out_order(self) -> None:
        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"bytes": {"type": "bytes"}},
            "constants": {},
            "outputs": {},
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
        order = pipeline.execution_order
        assert order.index("split") < order.index("proc_a")
        assert order.index("split") < order.index("proc_b")

    def test_independent_steps_all_appear(self) -> None:
        data = {
            "codec_id": "test",
            "direction": "encode",
            "inputs": {"a": {"type": "bytes"}, "b": {"type": "bytes"}},
            "constants": {},
            "outputs": {},
            "steps": [
                {
                    "name": "step_a",
                    "codec_id": "some-codec",
                    "src": "file:///a.wasm",
                    "inputs": {"bytes": "input.a"},
                    "outputs": ["bytes"],
                },
                {
                    "name": "step_b",
                    "codec_id": "some-codec",
                    "src": "file:///b.wasm",
                    "inputs": {"bytes": "input.b"},
                    "outputs": ["bytes"],
                },
            ],
        }
        pipeline = Pipeline.parse(data)
        assert set(pipeline.execution_order) == {"step_a", "step_b"}
