"""The launch tripwire: the deployed RetroDECK still runs the lookup atlas mirrors (#84).

A catalogue entry's launch answer is ES-DE's ``FileData::findEmulator`` read off
RetroDECK's fork at one tag (:data:`atlas.installations.RetroDeck.ESDE_FORK_BUILD`),
evaluated against the ``--home`` RetroDECK starts it with, and compared with what
RetroDECK's own ``run_game.sh`` would run. All three are facts of one deployed
build, so where RetroDECK is deployed this holds each against it:

1. ``components/es-de/component_version`` names the pinned fork tag. A new
   build may carry a different ``findEmulator`` or ``launchGame``; the message
   names the functions to diff before the pin moves. The launch command
   question (#573) reads the same file at answer time and refuses a build other
   than the pin (``frontend-build-unpinned``), so the pin this test holds is
   the one the answer checks.
2. Every line :data:`atlas.installations.RetroDeck.LAUNCH_CITATIONS` cites still
   holds the text it cites: ``component_launcher.sh:10`` passes
   ``--home "${XDG_CONFIG_HOME}"``, ``component_functions.sh:7`` names the find
   rules ``run_game.sh`` reads, and ``run_game.sh`` keeps its token pattern, its
   ``OS-SHELL`` substitution and ``find_emulator`` with its two tests at the cited
   lines.

EmuDeck's entries get the same lookup read off upstream ES-DE at one release
(:data:`atlas.installations.EmuDeck.ESDE_APPIMAGE_BUILD` — ES-DE v3.4.1's
``FileData::findEmulator`` is the one :mod:`atlas.launch` cites), run on the
host with the ``PATH`` EmuDeck's launcher assembles, after the rewrite of the
custom find rules that launcher runs first. Where EmuDeck's ES-DE is deployed
this holds the pair the same way:

3. The build the AppImage's ``usr/bin/es-de`` states, read the way the
   handle reads it (``EmuDeck._deployed_esde_build``, with
   :data:`atlas.installations.EmuDeck.ESDE_VERSION_STAMP`), is the pinned
   release. Opening the image needs a zstd provider; without one the read
   cannot be made and the test says so rather than passing.
4. Every line :data:`atlas.installations.EmuDeck.LAUNCH_CITATIONS` cites still
   holds its text in the backend checkout: the launcher and the ``PATH`` it
   hands ES-DE, and the rewrite of the custom find rules.

The existing component-script tripwire (``tests/test_deployed_citations.py``)
reads the citations the packaged *data* makes; these are the code's, which no
data file carries, so they are held here rather than added there.

Skipped where RetroDECK or EmuDeck is not deployed, like every machine-bound
tripwire; the weekly canary deploys the latest RetroDECK build, which is where
a moved line turns red.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from atlas.installations import EmuDeck, RetroDeck
from atlas.machine import RealMachine
from atlas.squashfs import zstd_provider

FILES = Path("/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files")
COMPONENT_VERSION = RetroDeck.ESDE_COMPONENT_VERSION


EMUDECK_IMAGE = Path.home() / "Applications" / "ES-DE.AppImage"
EMUDECK_BACKEND = Path.home() / ".config" / "EmuDeck" / "backend"

_RETRODECK_REDIFF = (
    "FileData::findEmulator and FileData::launchGame, getEscapedPath and the other FileSystemUtil helpers "
    "launchGame calls, and Platform::launchGameUnix of the new tag against ES-DE v3.4.1, then move "
    "RetroDeck.ESDE_FORK_BUILD"
)
_EMUDECK_REDIFF = (
    "FileData::findEmulator and getPathToBinary of the new release against ES-DE v3.4.1, measure "
    "EmuDeck.ESDE_VERSION_STAMP on the new binary again, then move EmuDeck.ESDE_APPIMAGE_BUILD"
)


def build_drift(
    version: str | None,
    pinned: str = RetroDeck.ESDE_FORK_BUILD,
    where: str = COMPONENT_VERSION,
    rediff: str = _RETRODECK_REDIFF,
) -> list[str]:
    """What to re-verify when the deployed ES-DE build is not the pinned one."""
    if version == pinned:
        return []
    return [f"{where} names {version!r}, not the pinned {pinned!r}: diff {rediff}"]


def citation_drift(
    read_lines: Mapping[str, "list[str] | None"],
    citations: tuple[tuple[str, int, str], ...] = RetroDeck.LAUNCH_CITATIONS,
) -> list[str]:
    """Every cited line that no longer holds its text — *read_lines* maps a cited file to its lines.

    ``None`` is a file the deploy no longer carries. Factored out of the
    assertion so the rule can be watched failing without a deploy.
    """
    drift: list[str] = []
    for file, line, text in citations:
        lines = read_lines.get(file)
        if lines is None:
            drift.append(f"{file} is gone — the launch lookup cites it at :{line}")
        elif line > len(lines) or text not in lines[line - 1]:
            drift.append(
                f"{file}:{line} no longer holds {text!r} — re-read the script and re-verify the "
                "launch lookup's reading of it before moving the citation"
            )
    return drift


def _deployed() -> bool:
    return (FILES / COMPONENT_VERSION).is_file()


def _read(file: str) -> list[str] | None:
    path = FILES / file
    return path.read_text(encoding="utf-8").splitlines() if path.is_file() else None


@pytest.mark.skipif(not _deployed(), reason="RetroDECK is not deployed on this machine")
class TestTheDeployedBuildIsTheOneTheLookupMirrors:
    def test_the_es_de_build_is_the_pinned_fork_tag(self):
        version = (FILES / COMPONENT_VERSION).read_text(encoding="utf-8").strip()
        assert build_drift(version) == []

    def test_every_cited_line_still_holds_its_text(self):
        files = {file for file, _, _ in RetroDeck.LAUNCH_CITATIONS}
        assert citation_drift({file: _read(file) for file in files}) == []


class TestTheRulesFireWithoutADeploy:
    """The two drift readings, watched failing on data rather than on a deploy."""

    def test_another_build_is_drift(self):
        assert build_drift(RetroDeck.ESDE_FORK_BUILD) == []
        assert len(build_drift("retrodeck-main-20991231-000000")) == 1
        assert len(build_drift(None)) == 1

    def test_a_moved_line_and_a_missing_file_are_drift(self):
        held = {file: [""] * 400 for file, _, _ in RetroDeck.LAUNCH_CITATIONS}
        for file, line, text in RetroDeck.LAUNCH_CITATIONS:
            held[file][line - 1] = text
        assert citation_drift(held) == []
        first_file, first_line, _ = RetroDeck.LAUNCH_CITATIONS[0]
        moved = {**held, first_file: ["", *held[first_file]]}
        assert [entry.split(" ")[0] for entry in citation_drift(moved)] == [f"{first_file}:{first_line}"]
        assert citation_drift({**held, first_file: None}) == [
            f"{first_file} is gone — the launch lookup cites it at :{first_line}"
        ]


def _emudeck_deployed() -> bool:
    return EMUDECK_IMAGE.is_file() and (EMUDECK_BACKEND / ".git").is_dir()


def _read_backend(file: str) -> list[str] | None:
    path = EMUDECK_BACKEND / file
    return path.read_text(encoding="utf-8").splitlines() if path.is_file() else None


def _emudeck_build_drift(version: str | None) -> list[str]:
    where = f"{EmuDeck.ESDE_BINARY_ENTRY} in ES-DE.AppImage"
    return build_drift(version, EmuDeck.ESDE_APPIMAGE_BUILD, where, _EMUDECK_REDIFF)


@pytest.mark.skipif(not _emudeck_deployed(), reason="EmuDeck's ES-DE and backend are not deployed on this machine")
class TestEmuDecksFrontendIsTheOneTheLookupMirrors:
    def test_the_appimage_states_the_pinned_es_de_release(self):
        if zstd_provider() is None:
            pytest.skip(
                "no zstd provider in this runtime (compression.zstd, backports.zstd or a registered one) — "
                "the AppImage's binary cannot be read"
            )
        handle = EmuDeck(str(Path.home()), RealMachine())
        build = handle._deployed_esde_build()  # pyright: ignore[reportPrivateUsage] - the read the build gate makes
        assert _emudeck_build_drift(build) == []

    def test_every_cited_backend_line_still_holds_its_text(self):
        files = {file for file, _, _ in EmuDeck.LAUNCH_CITATIONS}
        read = {file: _read_backend(file) for file in files}
        assert citation_drift(read, EmuDeck.LAUNCH_CITATIONS) == []


class TestEmuDecksRulesFireWithoutADeploy:
    """The EmuDeck pair, watched failing on data rather than on a deploy."""

    def test_another_release_and_no_stamp_are_drift(self):
        assert _emudeck_build_drift(EmuDeck.ESDE_APPIMAGE_BUILD) == []
        assert len(_emudeck_build_drift("3.5.0")) == 1
        assert len(_emudeck_build_drift(None)) == 1

    def test_a_moved_backend_line_is_drift(self):
        held = {file: [""] * 1200 for file, _, _ in EmuDeck.LAUNCH_CITATIONS}
        for file, line, text in EmuDeck.LAUNCH_CITATIONS:
            held[file][line - 1] = text
        assert citation_drift(held, EmuDeck.LAUNCH_CITATIONS) == []
        file, line, _ = EmuDeck.LAUNCH_CITATIONS[-1]
        moved = {**held, file: [*held[file][: line - 1], "", *held[file][line:]]}
        assert [entry.split(" ")[0] for entry in citation_drift(moved, EmuDeck.LAUNCH_CITATIONS)] == [f"{file}:{line}"]
        assert citation_drift({**held, file: None}, EmuDeck.LAUNCH_CITATIONS)
