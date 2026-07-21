"""Detection — what emulator installations are present on a machine.

``detect(home, reader)`` probes the config markers of each known install flavor,
in a fixed order, and returns a handle for every one it finds. Multiple flavors
coexist (a RetroDECK install alongside a native RetroArch is ordinary), so the
result is a list, highest-priority first: RetroDECK, then the standalone
RetroArch Flatpak, then a native RetroArch. Every probe goes through the reader,
so detection is fully provable from a fixture machine.
"""

from __future__ import annotations

import os

from atlas.installations import (
    NATIVE_CFG_SUFFIX,
    RETRODECK_JSON_SUFFIX,
    STANDALONE_FLATPAK_CFG_SUFFIX,
    Installation,
    NativeRetroArch,
    RetroDeck,
    StandaloneRetroArchFlatpak,
)
from atlas.reader import FilesystemReader, Reader


def detect(home: str, reader: Reader | None = None) -> list[Installation]:
    """Detect the emulator installations under *home*.

    *reader* defaults to the real filesystem; tests and conformance vectors pass
    a fixture reader. Returns the detected installations in probe order (an empty
    list when nothing is installed).
    """
    if reader is None:
        reader = FilesystemReader()

    found: list[Installation] = []

    retrodeck_json = reader.read_text(os.path.join(home, RETRODECK_JSON_SUFFIX))
    if retrodeck_json is not None:
        found.append(RetroDeck(home, reader, retrodeck_json))

    if reader.exists(os.path.join(home, STANDALONE_FLATPAK_CFG_SUFFIX)):
        found.append(StandaloneRetroArchFlatpak(home, reader))

    if reader.exists(os.path.join(home, NATIVE_CFG_SUFFIX)):
        found.append(NativeRetroArch(home, reader))

    return found
