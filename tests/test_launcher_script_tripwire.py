"""The launcher-script tripwire: the deployed EmuDeck still holds the files its scripts' readings were made from (#573).

What an EmuDeck launcher script runs beside the emulator is read per script
and pinned file by file (``atlas/data/emudeck_launchers.json``): the script by
the git blob hash of its deployed copy, every backend file it sources and every
file it executes by theirs. An answer checks those hashes itself and refuses
with ``launcher-script-unpinned`` where one differs, so a stale pin never
answers wrongly — it stops answering. Where EmuDeck is deployed this turns that
silence red:

1. Every file the scripts source hashes to its pin in the backend checkout.
2. Every pinned script that is deployed under ``<toolsPath>/launchers`` hashes
   to a pin, and so does every file it executes.
3. The pinned source set is the closure of what ``functions/all.sh`` sources,
   read off the deployed files the way bash walks them (the darwin branch and
   the settings file aside) — so a file the backend starts sourcing is not
   silently missing from the pins.

The backend updates itself at every launch, so a pull that moves any of these
files is what this catches; the message names what to re-read before a pin
moves. Skipped where EmuDeck's backend is not deployed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

import pytest

from atlas.installations import EmuDeck
from atlas.launcher_scripts import BACKEND_ROOT, launcher_rules
from atlas.machine import DIGEST_GIT_BLOB, RealMachine

HOME = Path.home()
BACKEND = HOME / ".config" / "EmuDeck" / "backend"

_REREAD = (
    "re-read the script and every file it sources or executes at the deployed backend revision — its steps, the "
    "settings each turns on, the build it starts — then move the pins in atlas/data/emudeck_launchers.json"
)


def pin_drift(pins: Mapping[str, str], hashes: Mapping[str, str | None]) -> list[str]:
    """One line per pinned file whose deployed hash is not its pin — a missing or unreadable file included."""
    drift: list[str] = []
    for path, pin in pins.items():
        found = hashes.get(path)
        if found != pin:
            state = "is not there or cannot be read" if found is None else f"hashes to {found}"
            drift.append(f"{path} {state}, pinned at {pin}: {_REREAD}")
    return drift


# A source line: `source` or `.`, one path argument, quoted or not, and
# nothing after it but a `;` or a comment. Any other shape of a source line is
# one this walk does not understand, and is reported rather than skipped.
_SOURCE_LINE = re.compile(r"^\s*(?:source|\.)\s")
# A source command later in a line, after a list operator or a keyword that
# starts a command, with an argument shaped like a file — quoted, a variable,
# or a path — so that jq's `else . end` inside a quoted filter is no source.
_EMBEDDED_SOURCE = re.compile(r"(?:;|&&|\|\||\bthen|\bdo|\belse)\s+(?:source|\.)\s+(?:[\"'$~/]|[^\s;#]*/)")
_SOURCE = re.compile(r'^\s*(?:source|\.)\s+(?P<path>"[^"]*"[^\s;#]*|[^\s;#"]+)\s*;?\s*(?:#.*)?$')
# The spellings the backend uses for its own checkout.
_BACKEND_SPELLINGS = ("${emudeckBackend}", "$emudeckBackend", "$HOME/.config/EmuDeck/backend")
# What a pinned file may source besides the backend: the settings files, the
# python environment a helper activates, and darwin's branch, which SteamOS
# never takes (functions/all.sh:139-142). Nothing else is left out.
_NOT_PINNED = (
    "$SETTINGSFILE",
    "$HOME/emudeck/settings.sh",
    "$emuDecksettingsFile",
    "$emudeckFolder/python_virtual_env/bin/activate",
)
_NOT_ON_LINUX = ("darwin/",)


def _backend_path(argument: str) -> str | None:
    """The backend-relative path a source argument names, ``None`` where it names none."""
    for spelling in _BACKEND_SPELLINGS:
        if argument.startswith(spelling):
            return argument[len(spelling) :].lstrip("/")
    return None


def source_closure(read: Mapping[str, str]) -> tuple[set[str], list[str]]:
    """The backend files ``functions/all.sh`` sources, transitively, as ``$emudeckBackend/…`` paths — and every
    source line the walk does not understand.

    *read* maps a backend-relative path to its text; a path it lacks ends the
    walk there and is part of the result, so a missing file is reported by the
    hash comparison rather than hidden.
    """
    seen: set[str] = set()
    unknown: list[str] = []
    pending = ["functions/all.sh"]
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        for number, line in enumerate((read.get(path) or "").splitlines(), start=1):
            target = _sourced(line)
            if target is _NOT_UNDERSTOOD:
                unknown.append(f"{path}:{number}: {line.strip()}")
            elif target is not None:
                pending.append(target)
    return {BACKEND_ROOT + path for path in seen}, unknown


# What _sourced answers for a source line the walk does not understand.
_NOT_UNDERSTOOD = "\0"


def _sourced(line: str) -> str | None:
    """The backend file *line* sources, ``None`` where it sources none the walk follows — no source line, darwin's,
    or one of :data:`_NOT_PINNED` — and :data:`_NOT_UNDERSTOOD` where it is a source line the walk does not read.
    """
    if not _SOURCE_LINE.match(line):
        return _NOT_UNDERSTOOD if _EMBEDDED_SOURCE.search(line) else None
    match = _SOURCE.match(line)
    if match is None:
        return _NOT_UNDERSTOOD
    argument = match.group("path").replace('"', "")
    target = _backend_path(argument)
    if target is None:
        return None if argument in _NOT_PINNED else _NOT_UNDERSTOOD
    return None if target.startswith(_NOT_ON_LINUX) else target


def _backend_texts() -> dict[str, str]:
    return {
        str(path.relative_to(BACKEND)): path.read_text(encoding="utf-8", errors="replace")
        for path in BACKEND.rglob("*.sh")
        if ".git" not in path.parts
    }


def _deployed() -> bool:
    return (BACKEND / ".git").is_dir()


@pytest.mark.skipif(not _deployed(), reason="EmuDeck's backend is not deployed on this machine")
class TestTheDeployedEmuDeckHoldsThePinnedFiles:
    def test_every_sourced_file_hashes_to_its_pin(self):
        machine = RealMachine()
        sources = launcher_rules().sources
        hashes = {
            path: machine.file_digest(str(BACKEND / path[len(BACKEND_ROOT) :]), DIGEST_GIT_BLOB) for path in sources
        }
        assert pin_drift(sources, hashes) == []

    def test_every_deployed_script_and_what_it_executes_hashes_to_a_pin(self):
        machine = RealMachine()
        handle = EmuDeck(str(HOME), machine)
        settings = handle._read_marker()[0]  # pyright: ignore[reportPrivateUsage] - the settings the answer reads
        tools = handle._setting_path(settings, "toolsPath", "tools")[0]  # pyright: ignore[reportPrivateUsage]
        checked = 0
        for rule in launcher_rules().by_blob.values():
            script = Path(tools) / "launchers" / rule.script
            if not script.is_file():
                continue
            checked += 1
            assert pin_drift({str(script): rule.blob}, {str(script): machine.file_digest(str(script), DIGEST_GIT_BLOB)}) == []
            paths = {
                pinned: handle._pinned_path(pinned, settings)  # pyright: ignore[reportPrivateUsage] - the answer's own walk
                for pinned in rule.executes
            }
            hashes = {pinned: machine.file_digest(path, DIGEST_GIT_BLOB) for pinned, path in paths.items()}
            assert pin_drift(rule.executes, hashes) == []
        if checked == 0:
            pytest.skip("no pinned launcher script is deployed under toolsPath/launchers")

    def test_the_pinned_sources_are_what_all_sh_sources(self):
        closure, unknown = source_closure(_backend_texts())
        assert unknown == []
        assert closure == set(launcher_rules().sources)


class TestTheRulesFireWithoutADeploy:
    """The tripwire's two readings, watched failing on data rather than on a deploy."""

    def test_a_moved_or_missing_file_is_drift(self):
        pins = {"$emudeckBackend/functions/all.sh": "a" * 40, "$toolsPath/proton-launch.sh": "b" * 40}
        assert pin_drift(pins, {"$emudeckBackend/functions/all.sh": "a" * 40, "$toolsPath/proton-launch.sh": "b" * 40}) == []
        assert len(pin_drift(pins, {"$emudeckBackend/functions/all.sh": "c" * 40, "$toolsPath/proton-launch.sh": "b" * 40})) == 1
        assert len(pin_drift(pins, {"$emudeckBackend/functions/all.sh": "a" * 40})) == 1

    def test_the_closure_follows_every_source_line_and_leaves_darwin_out(self):
        read = {
            "functions/all.sh": (
                'source "$emudeckBackend"/vars.sh\n'
                '. "$HOME/.config/EmuDeck/backend/functions/helpers.sh"\n'
                'source "$emudeckBackend/android/functions/all.sh"\n'
                '    source "$emudeckBackend/darwin/functions/all.sh"\n'
                'source "$SETTINGSFILE"\n'
            ),
            "functions/helpers.sh": "source ${emudeckBackend}/functions/cloud.sh; # the cloud helpers\n",
            "android/functions/all.sh": 'source "$emudeckBackend"/android/vars.sh  # android\n',
        }
        assert source_closure(read) == (
            {
                "$emudeckBackend/functions/all.sh",
                "$emudeckBackend/vars.sh",
                "$emudeckBackend/functions/helpers.sh",
                "$emudeckBackend/functions/cloud.sh",
                "$emudeckBackend/android/functions/all.sh",
                "$emudeckBackend/android/vars.sh",
            },
            [],
        )

    def test_a_dot_inside_a_quoted_jq_filter_is_no_source(self):
        line = """      | if $tmpl == null then error("no gamepad entry in input_config") else . end"""
        assert source_closure({"functions/all.sh": line + "\n"})[1] == []

    @pytest.mark.parametrize(
        "line",
        [
            'source "$emudeckFolder/other.sh"',
            'source all.sh && "@a"',
            '. "$(dirname "$0")/x.sh"',
            '[ -f x ] && source "$emudeckBackend/functions/x.sh"',
            'if [ -f x ]; then . "$emudeckBackend/functions/y.sh"; fi',
        ],
    )
    def test_a_source_line_the_walk_does_not_understand_is_reported(self, line: str):
        assert source_closure({"functions/all.sh": line + "\n"})[1] == [f"functions/all.sh:1: {line}"]
