"""The parse of ES-DE's find rules a RetroDECK handle keeps between questions (#84).

The handle keeps one thing: each find-rules file's parse, under the stat stamp
(``st_mtime_ns``, ``st_size``) the file was read at. These run on a real machine
over a temporary tree, because a stamp is the real filesystem's to give — a
fixture machine's files cannot change underneath a handle and it stamps nothing.
Parses are counted at the one function that makes them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import atlas
import atlas.installations
from atlas.find_rules import FindRules
from atlas.machine import RealMachine

APP = "net.retrodeck.retrodeck"
COMPONENT = "/app/retrodeck/components/dolphin/component_launcher.sh"
OTHER = "/app/retrodeck/components/dolphin/component_launchex.sh"
SYSTEMS = (
    '<?xml version="1.0"?>\n<systemList>\n  <system><name>gc</name><path>%ROMPATH%/gc</path>'
    '<extension>.iso</extension>\n    <command label="Dolphin (Standalone)">%EMULATOR_DOLPHIN% -b -e %ROM%</command>\n'
    "  </system>\n</systemList>\n"
)


def rules(path: str) -> str:
    return (
        '<?xml version="1.0"?>\n<ruleList>\n    <emulator name="DOLPHIN">\n        <rule type="staticpath">\n'
        f"            <entry>{path}</entry>\n        </rule>\n    </emulator>\n</ruleList>\n"
    )


class Tree:
    """A RetroDECK user deploy and its config home under a temporary home."""

    def __init__(self, root: Path) -> None:
        self.home = root / "home"
        files = self.home / ".local/share/flatpak/app" / APP / "current/active/files"
        self.systems = files / "retrodeck/components/es-de/share/es-de/resources/systems/linux"
        self.shipped = self.systems / "es_find_rules.xml"
        config = self.home / ".var/app" / APP / "config"
        self.custom = config / "ES-DE/custom_systems/es_find_rules.xml"
        self.shadow = config / "ES-DE/resources/systems/linux/es_find_rules.xml"
        rd_home = root / "retrodeck"
        self.write(config / "retrodeck/retrodeck.json", json.dumps({"paths": {"rd_home_path": str(rd_home)}}))
        (rd_home / "saves").mkdir(parents=True)
        self.write(self.systems / "es_systems.xml", SYSTEMS)
        self.write(files.parent / "metadata", f"[Application]\nname={APP}\n\n[Context]\nfilesystems=host;\n")
        for component in (COMPONENT, OTHER):
            self.write(files / component.removeprefix("/app/"), "#!/bin/bash\n")
        self.write(self.shipped, rules(COMPONENT))

    @staticmethod
    def write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def handle(self) -> atlas.RetroDeck:
        return atlas.RetroDeck(str(self.home), RealMachine())


def launcher(handle: atlas.RetroDeck) -> str | None:
    (entry,) = handle.emulators_for("gc").entries
    return None if entry.launcher is None else entry.launcher.path


@pytest.fixture
def parses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every find-rules parse the handle makes, by the text's first entry."""
    made: list[str] = []
    real = atlas.installations.parse_find_rules

    def counted(text: str) -> FindRules | None:
        made.append(text)
        return real(text)

    monkeypatch.setattr(atlas.installations, "parse_find_rules", counted)
    return made


class TestAnUnchangedFileIsNotParsedAgain:
    def test_a_second_answer_parses_nothing(self, tmp_path: Path, parses: list[str]):
        handle = Tree(tmp_path).handle()
        assert launcher(handle) == COMPONENT
        assert len(parses) == 1
        assert launcher(handle) == COMPONENT
        assert launcher(handle) == COMPONENT
        assert len(parses) == 1

    def test_a_second_handle_parses_for_itself(self, tmp_path: Path, parses: list[str]):
        tree = Tree(tmp_path)
        launcher(tree.handle())
        launcher(tree.handle())
        assert len(parses) == 2


class TestAChangedFileIsParsedAgain:
    def test_the_same_size_under_a_new_mtime(self, tmp_path: Path, parses: list[str]):
        tree = Tree(tmp_path)
        handle = tree.handle()
        assert launcher(handle) == COMPONENT
        before = tree.shipped.stat()
        tree.shipped.write_text(rules(OTHER))
        os.utime(tree.shipped, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
        assert tree.shipped.stat().st_size == before.st_size
        assert launcher(handle) == OTHER
        assert len(parses) == 2

    def test_a_new_size_under_the_same_mtime(self, tmp_path: Path, parses: list[str]):
        tree = Tree(tmp_path)
        handle = tree.handle()
        assert launcher(handle) == COMPONENT
        before = tree.shipped.stat()
        tree.shipped.write_text(rules(OTHER) + "\n")
        os.utime(tree.shipped, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert launcher(handle) == OTHER
        assert len(parses) == 2


class TestALayerThatComesOrGoesChangesTheAnswer:
    def test_a_custom_layer(self, tmp_path: Path, parses: list[str]):
        tree = Tree(tmp_path)
        handle = tree.handle()
        assert launcher(handle) == COMPONENT
        tree.write(tree.custom, rules(OTHER))
        assert launcher(handle) == OTHER
        tree.custom.unlink()
        assert launcher(handle) == COMPONENT
        assert len(parses) == 2

    def test_a_bundled_shadow(self, tmp_path: Path, parses: list[str]):
        tree = Tree(tmp_path)
        handle = tree.handle()
        assert launcher(handle) == COMPONENT
        tree.write(tree.shadow, rules(OTHER))
        assert launcher(handle) == OTHER
        tree.shadow.unlink()
        assert launcher(handle) == COMPONENT

    def test_the_shipped_file(self, tmp_path: Path, parses: list[str]):
        tree = Tree(tmp_path)
        handle = tree.handle()
        assert launcher(handle) == COMPONENT
        held = tree.shipped.read_text()
        tree.shipped.unlink()
        (entry,) = handle.emulators_for("gc").entries
        assert entry.availability == atlas.AVAILABILITY_UNESTABLISHED
        assert [c.code for c in entry.caveats] == [atlas.CAVEAT_FIND_RULES_UNREADABLE]
        tree.write(tree.shipped, held)
        assert launcher(handle) == COMPONENT
