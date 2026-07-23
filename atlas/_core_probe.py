"""Probe a libretro core for ``retro_get_system_info`` — run as a subprocess.

Invoked as ``python -m atlas._core_probe <core.so>`` by
:meth:`atlas.machine.RealMachine.query_core`. The subprocess *is* the crash
isolation: a core that segfaults on load takes this process down, not the host.
On success a JSON object with ``library_name`` / ``library_version`` /
``valid_extensions`` is written to stdout; any failure exits non-zero.

This is the same read RetroArch performs when it loads a core — a live read of
the binary on disk, not a lookup.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys


class _RetroSystemInfo(ctypes.Structure):
    _fields_ = [
        ("library_name", ctypes.c_char_p),
        ("library_version", ctypes.c_char_p),
        ("valid_extensions", ctypes.c_char_p),
        ("need_fullpath", ctypes.c_bool),
        ("block_extract", ctypes.c_bool),
    ]


def _decode(value: bytes | None) -> str | None:
    return value.decode("utf-8", "replace") if value else None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m atlas._core_probe <core.so>", file=sys.stderr)
        return 2
    so_path = argv[1]
    try:
        lib = ctypes.CDLL(so_path, mode=os.RTLD_LAZY)
        info = _RetroSystemInfo()
        lib.retro_get_system_info(ctypes.byref(info))
    except OSError as exc:
        print(f"cannot load core: {exc}", file=sys.stderr)
        return 1
    name = _decode(info.library_name)
    if not name:
        print("core reported no library_name", file=sys.stderr)
        return 1
    json.dump(
        {
            "library_name": name,
            "library_version": _decode(info.library_version),
            "valid_extensions": _decode(info.valid_extensions),
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
