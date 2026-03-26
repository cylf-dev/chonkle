"""Embed a ``chonkle:signature`` custom section into a Wasm binary.

Usage::

    python -m chonkle.tools.embed_signature <wasm_file> <signature_json>

Reads *signature_json*, embeds it as a ``chonkle:signature`` custom section
in *wasm_file* (in-place).  If the binary already contains a
``chonkle:signature`` section, it is replaced.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from chonkle.wasm_signature import embed_signature

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2:
        sys.stderr.write(
            "Usage: python -m chonkle.tools.embed_signature"
            " <wasm_file> <signature_json>\n"
        )
        sys.exit(1)

    wasm_path = Path(args[0])
    sig_path = Path(args[1])

    wasm_bytes = wasm_path.read_bytes()
    with sig_path.open() as f:
        signature = json.load(f)

    result = embed_signature(wasm_bytes, signature)
    wasm_path.write_bytes(result)

    added = len(result) - len(wasm_bytes)
    log.info("Embedded %d byte custom section in %s", added, wasm_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
