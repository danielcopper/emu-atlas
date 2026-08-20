"""Re-establish (or refute) the ES-DE half of the EmuDeck evidence anchor — issue #102.

The EmuDeck evidence record (``atlas/data/arrangement_evidence.json``) pins two
things. The backend checkout's git HEAD is read live by the library itself —
mechanized since the day it was pinned. The deployed ES-DE version was
established **by hand**: the AppImage's md5 was compared against the published
release manifest, because ES-DE stores no version on disk (no
``ApplicationVersion`` in Settings.cpp @ v3.4.1; the startup log states one
only after a first launch, and the verification machine had none). This script
is that ritual, written down: run it on the reference machine and it answers
whether the pinned identification still holds.

A maintainer tool, not part of the library: it talks to the network (ES-DE's
``latest_release.json`` — the exact manifest EmuDeck's own installer reads,
``emuDeckESDE.sh:15,23-29`` @ 863ab69) and prints a verdict for a human. The
library never does either. stdlib only, like everything else in the tree.

Verdicts, one per line on stdout, exit 0 only on CONFIRMED:

- ``CONFIRMED``    — the AppImage's md5 matches the manifest's stable
  ``LinuxSteamDeckAppImage`` package: the deployed ES-DE is that version.
- ``PRERELEASE``   — it matches the prerelease package instead: the machine
  runs ahead of stable, and the evidence prose ("the deployed ES-DE was X")
  needs re-checking.
- ``REFUTED``      — it matches neither: upstream has moved on or the AppImage
  was replaced; which version is deployed is unestablished until the log
  anchor (first line of ``~/ES-DE/logs/es_log.txt``, printed below when one
  exists) or a fresh manifest identifies it.
- ``UNESTABLISHED`` — no AppImage on this machine, or the manifest could not
  be fetched or parsed: nothing was compared, which is never a refutation.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

MANIFEST_URL = "https://gitlab.com/es-de/emulationstation-de/-/raw/master/latest_release.json"
APPIMAGE = os.path.expanduser("~/Applications/ES-DE.AppImage")
ES_LOG = os.path.expanduser("~/ES-DE/logs/es_log.txt")
PACKAGE = "LinuxSteamDeckAppImage"


def _appimage_md5() -> str | None:
    try:
        # md5 because that is the digest the manifest publishes (and the one
        # EmuDeck's own installer compares); an identity check, not a security
        # function — which is what usedforsecurity=False states.
        digest = hashlib.md5(usedforsecurity=False)
        with open(APPIMAGE, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as error:
        print(f"no AppImage to identify: {APPIMAGE} ({error.strerror})")
        return None


def _manifest_package(channel: dict[str, Any]) -> tuple[str | None, str | None]:
    for package in channel.get("packages", []):
        if package.get("name") == PACKAGE:
            return package.get("md5"), channel.get("version")
    return None, None


def _log_anchor() -> None:
    """The secondary anchor, shown when it exists: ES-DE writes its version there on launch."""
    try:
        with open(ES_LOG, encoding="utf-8", errors="replace") as handle:
            first = handle.readline().strip()
    except OSError:
        return
    if first:
        print(f"log anchor ({ES_LOG}): {first!r}")


def main() -> int:
    md5 = _appimage_md5()
    if md5 is None:
        print("UNESTABLISHED")
        return 1
    print(f"AppImage md5: {md5}")
    try:
        with urllib.request.urlopen(MANIFEST_URL, timeout=30) as response:
            manifest = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        print(f"manifest not readable ({MANIFEST_URL}): {error}")
        _log_anchor()
        print("UNESTABLISHED")
        return 1
    stable_md5, stable_version = _manifest_package(manifest.get("stable", {}))
    pre_md5, pre_version = _manifest_package(manifest.get("prerelease", {}))
    if stable_md5 is None and pre_md5 is None:
        print(f"manifest names no {PACKAGE} package — the format moved; re-read emuDeckESDE.sh")
        _log_anchor()
        print("UNESTABLISHED")
        return 1
    if md5 == stable_md5:
        print(f"matches the stable package: the deployed ES-DE is {stable_version}")
        print("CONFIRMED")
        return 0
    if md5 == pre_md5:
        print(f"matches the prerelease package ({pre_version}) — the machine runs ahead of stable")
        _log_anchor()
        print("PRERELEASE")
        return 1
    print(
        f"matches neither published package (stable {stable_version}, prerelease {pre_version}) — "
        "upstream moved on or the AppImage was replaced; the deployed version is unestablished"
    )
    _log_anchor()
    print("REFUTED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
