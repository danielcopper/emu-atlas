"""The launch tripwire: the deployed RetroDECK still runs the lookup atlas mirrors (#84).

A catalogue entry's launch answer is ES-DE's ``FileData::findEmulator`` read off
RetroDECK's fork at one tag (:data:`atlas.installations.RetroDeck.ESDE_FORK_BUILD`),
evaluated against the ``--home`` RetroDECK starts it with, and compared with what
RetroDECK's own ``run_game.sh`` would run. All three are facts of one deployed
build, so where RetroDECK is deployed this holds each against it:

1. ``components/es-de/component_version`` names the pinned fork tag. A new
   build may carry a different ``findEmulator``; the message names the function
   to diff before the pin moves.
2. Every line :data:`atlas.installations.RetroDeck.LAUNCH_CITATIONS` cites still
   holds the text it cites: ``component_launcher.sh:10`` passes
   ``--home "${XDG_CONFIG_HOME}"``, ``component_functions.sh:7`` names the find
   rules ``run_game.sh`` reads, and ``run_game.sh`` keeps its token pattern, its
   ``OS-SHELL`` substitution and ``find_emulator`` with its two tests at the cited
   lines.

The existing component-script tripwire (``tests/test_deployed_citations.py``)
reads the citations the packaged *data* makes; these are the code's, which no
data file carries, so they are held here rather than added there.

Skipped where RetroDECK is not deployed, like every machine-bound tripwire; the
weekly canary deploys the latest build, which is where a moved line turns red.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from atlas.installations import RetroDeck

FILES = Path("/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files")
COMPONENT_VERSION = "retrodeck/components/es-de/component_version"


def build_drift(version: str | None) -> list[str]:
    """What to re-verify when the deployed ES-DE build is not the pinned one."""
    if version == RetroDeck.ESDE_FORK_BUILD:
        return []
    return [
        f"{COMPONENT_VERSION} names {version!r}, not the pinned {RetroDeck.ESDE_FORK_BUILD!r}: diff "
        "FileData::findEmulator (and launchGame's %CORE_ handling) of the new tag against ES-DE "
        "v3.4.1, then move RetroDeck.ESDE_FORK_BUILD"
    ]


def citation_drift(read_lines: Mapping[str, "list[str] | None"]) -> list[str]:
    """Every cited line that no longer holds its text — *read_lines* maps a cited file to its lines.

    ``None`` is a file the deploy no longer carries. Factored out of the
    assertion so the rule can be watched failing without a deploy.
    """
    drift: list[str] = []
    for file, line, text in RetroDeck.LAUNCH_CITATIONS:
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
