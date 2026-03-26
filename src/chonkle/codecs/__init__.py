"""Codec wrapper classes normalizing different backends.

Each wrapper loads its signature at instantiation and exposes a uniform
``call(direction, port_map)`` interface so the executor does not need to
know which backend is in use.
"""

from chonkle.codecs._base import (
    CODEC_TRANSFORM_IFACE as CODEC_TRANSFORM_IFACE,
)
from chonkle.codecs._base import (
    SIGNATURES_DIR as SIGNATURES_DIR,
)
from chonkle.codecs._base import (
    Codec as Codec,
)
from chonkle.codecs._base import (
    PortMap as PortMap,
)
from chonkle.codecs._base import (
    detect_codec_type as detect_codec_type,
)
from chonkle.codecs.component import ComponentCodec as ComponentCodec
from chonkle.codecs.core import (
    CoreWasmCodec as CoreWasmCodec,
)
from chonkle.codecs.core import (
    CoreWasmRef as CoreWasmRef,
)
from chonkle.codecs.core import (
    OutputPortMap as OutputPortMap,
)
from chonkle.codecs.core import (
    _deserialize_port_map as _deserialize_port_map,
)
from chonkle.codecs.core import (
    _serialize_port_map as _serialize_port_map,
)
from chonkle.codecs.native import NativeCodec as NativeCodec

__all__ = [
    "CODEC_TRANSFORM_IFACE",
    "SIGNATURES_DIR",
    "Codec",
    "ComponentCodec",
    "CoreWasmCodec",
    "CoreWasmRef",
    "NativeCodec",
    "OutputPortMap",
    "PortMap",
    "detect_codec_type",
]
