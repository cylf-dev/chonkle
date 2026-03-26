"""Wasm Component Model codec pipeline library."""

from chonkle.codecs import Codec, ComponentCodec, PortMap, detect_codec_type
from chonkle.executor import PreparedPipeline, prepare, run
from chonkle.pipeline import Direction, Pipeline, StepSpec
from chonkle.wasm_download import resolve_uri
from chonkle.wasm_signature import embed_signature, read_signature

__all__ = [
    "Codec",
    "ComponentCodec",
    "Direction",
    "Pipeline",
    "PortMap",
    "PreparedPipeline",
    "StepSpec",
    "detect_codec_type",
    "embed_signature",
    "prepare",
    "read_signature",
    "resolve_uri",
    "run",
]
