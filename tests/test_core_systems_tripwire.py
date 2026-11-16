"""The tripwire for the pairs atlas packages for cores that declare no libretro database name (#784 D46).

A bare RetroArch names its systems by the libretro database names its cores'
``.info`` files declare. A core that declares none is named by the pair
`atlas/data/core_systems.json` cites for it — a hand judgement that holds only
while the core still declares no database name and still calls its system what
the entry records. This holds every entry against the deployed `.info` files,
and the other direction too: every deployed core without a database name is
either entered or named here as a core no system names.

**What this cannot see.** It reads the deployed `.info` files, not the cores:
a core that names a system its `.info` does not is beyond it, as it is beyond
RetroArch's own listing.

The deployed half is skipped where RetroDECK is not deployed, like the
tripwires beside it.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from atlas.core_declarations import packaged_core_declarations
from atlas.core_info import parse_core_info
from atlas.firmware import PackagedCoreSystem, core_short_name, packaged_core_systems
from atlas.platforms import PAIR_KNOWN, translate

DEPLOYED_CORES = Path(
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
    "/retroarch/rd_extras/cores"
)

# The deployed cores that declare no database name and have no entry: each is
# itself a game, a player or a tool rather than an emulator of a system, or
# emulates one no table source spells — reviewed one by one, so a new such core
# is a decision rather than a silent gap.
UNNAMED = {
    "anarch",  # a game
    "gme",  # a music player
    "gong",  # a Pong clone, a game
    "mcsoftserve",  # systemname "C713", which no table source spells
    "oberon",  # the Oberon RISC machine, which no table source spells
    "pocketcdg",  # a karaoke player
    "qemu",  # a general machine emulator naming no system
    "remotejoy",  # a remote-play client
    "romcleaner",  # a tool
    "superbroswar",  # a game
    "tamalibretro",  # the Tamagotchi P1, which no table source spells
    "thepowdertoy",  # a physics toy
}


def disagreements(entries: Mapping[str, PackagedCoreSystem], infos: Mapping[str, str]) -> list[str]:
    """The entries the deployed `.info` text contradicts: a database name it now declares, or another systemname.

    *infos* maps a core's short name to its deployed `.info` text; an entered
    core that ships none (PCEE2) is held to atlas's packaged declaration
    instead (:func:`packaged_systemnames`). Factored out of the assertions so
    the comparison runs without a deployment.
    """
    found: list[str] = []
    for name, entry in sorted(entries.items()):
        if name not in infos:
            continue
        fields = parse_core_info(infos[name])
        if fields.get("database", "").strip():
            found.append(f"{name}: the deployed .info now declares the database {fields['database']!r}")
        if fields.get("systemname", "") != entry.systemname:
            found.append(
                f"{name}: the deployed .info's systemname is {fields.get('systemname', '')!r}, "
                f"the entry records {entry.systemname!r}"
            )
    return found


def packaged_systemnames() -> dict[str, str]:
    """The systemname atlas's packaged declarations state for a core that ships no `.info`, by short name."""
    return {core_short_name(entry.stem): entry.systemname for entry in packaged_core_declarations()}


def unnamed(infos: Mapping[str, str], entries: Mapping[str, PackagedCoreSystem]) -> set[str]:
    """The cores of *infos* that declare no database name and have no entry."""
    return {
        name
        for name, text in infos.items()
        if not parse_core_info(text).get("database", "").strip() and name not in entries
    }


def _deployed_infos() -> dict[str, str]:
    if not DEPLOYED_CORES.is_dir():
        pytest.skip(f"no cores are deployed at {DEPLOYED_CORES}")
    return {
        core_short_name(info.stem): info.read_text(encoding="utf-8", errors="replace")
        for info in sorted(DEPLOYED_CORES.glob("*_libretro.info"))
        if (DEPLOYED_CORES / f"{info.stem}.so").is_file()
    }


class TestEveryEntryIsACitedKeyOfItsSource:
    def test_every_pair_is_a_key_its_source_holds(self):
        strays = {
            name: entry.system
            for name, entry in packaged_core_systems().items()
            if translate([entry.system]).pairs[0].status != PAIR_KNOWN
        }
        assert strays == {}

    def test_every_entry_cites_its_judgement(self):
        assert [name for name, entry in packaged_core_systems().items() if not entry.citation.strip()] == []

    def test_no_entry_is_also_reviewed_as_unnamed(self):
        assert set(packaged_core_systems()) & UNNAMED == set()


class TestTheComparisonFires:
    """The guards, watched failing without a deployment."""

    ENTRY = PackagedCoreSystem(("romm", "ti-83"), "TI83", "a citation")

    def test_a_core_that_gained_a_database_name_is_reported(self):
        info = 'systemname = "TI83"\ndatabase = "Texas Instruments - TI-83"\n'
        assert disagreements({"numero": self.ENTRY}, {"numero": info}) == [
            "numero: the deployed .info now declares the database 'Texas Instruments - TI-83'"
        ]

    def test_a_renamed_systemname_is_reported(self):
        assert disagreements({"numero": self.ENTRY}, {"numero": 'systemname = "TI-83"\n'}) == [
            "numero: the deployed .info's systemname is 'TI-83', the entry records 'TI83'"
        ]

    def test_an_agreeing_core_is_not(self):
        assert disagreements({"numero": self.ENTRY}, {"numero": 'systemname = "TI83"\n'}) == []

    def test_a_new_core_without_a_database_name_is_unnamed_until_reviewed(self):
        infos = {"numero": 'systemname = "TI83"\n', "newcore": 'systemname = "Thing"\n', "db": 'database = "X"\n'}
        assert unnamed(infos, {"numero": self.ENTRY}) == {"newcore"}


class TestTheDeployedCoresAgree:
    def test_every_entry_agrees_with_its_deployed_info(self):
        assert disagreements(packaged_core_systems(), _deployed_infos()) == []

    def test_an_entry_whose_core_ships_no_info_agrees_with_the_packaged_declaration(self):
        infos = _deployed_infos()
        packaged = packaged_systemnames()
        without_info = {name: entry for name, entry in packaged_core_systems().items() if name not in infos}
        assert without_info, "every entered core ships an .info here, so this half holds nothing"
        assert {name: packaged.get(name) for name in without_info} == {
            name: entry.systemname for name, entry in without_info.items()
        }

    def test_every_core_without_a_database_name_is_entered_or_reviewed_as_unnamed(self):
        assert unnamed(_deployed_infos(), packaged_core_systems()) == UNNAMED
