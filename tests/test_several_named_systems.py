"""Several named systems that answer alike answer together (#784 D62–D71).

Where the asked pairs name several systems an installation holds, every
question that answers for a resolved system asks each candidate as if it had
been named alone and answers the shared answer where they all give the same
one — resolution ``unanimous``, ``system-unanimous``, entries naming no
system — and its empty form, ``ambiguous``, where two differ. The vectors in
``vectors/machines/several-named-systems.json`` hold the rule per question
and per installation; these tests hold the comparison's parts one at a time.
"""

from __future__ import annotations

import ast
import collections.abc
import dataclasses
import functools
import json
import sys
import typing
import traceback
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest

import atlas
import atlas.contract
from atlas import standalone_firmware
from atlas.contract import catalogue_contract, firmware_contract, savefile_answer_contract, savestate_answer_contract
from atlas import _shared_answer as shared_answer
from atlas import standalone_saves
from atlas._shared_answer import SharedEntry, _merged, alike  # pyright: ignore[reportPrivateUsage]
from atlas.machine import FixtureMachine
from atlas.standalone_firmware import standalone_firmware_cards
from atlas.standalone_saves import load_standalone_saves

HOME = "/home/deck"
RETRODECK_JSON = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
RETRODECK_CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch.cfg"
RD_JSON = '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}'
CATALOGUE = (
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/"
    "components/es-de/share/es-de/resources/systems/linux/es_systems.xml"
)
GAMELISTS = "/mnt/sd/retrodeck/ES-DE/gamelists"
# Where the deployed RetroDECK keeps its cores, which /app/cores names inside the Flatpak.
CORES = "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/cores"
# libretro's Mega Drive name names ES-DE's genesis, megadrive and megadrivejp alike.
MEGA_DRIVE = ("libretro", "Sega - Mega Drive - Genesis")
CANDIDATES = (("esde", "genesis"), ("esde", "megadrive"))
PICODRIVE = ("PicoDrive", "picodrive")
GPGX = ("Genesis Plus GX", "genesis_plus_gx")


def _system(name: str, entries: tuple[tuple[str, str], ...], folder: str = "megadrive") -> str:
    commands = "".join(
        f'\n    <command label="{label}">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/{core}_libretro.so %ROM%</command>'
        for label, core in entries
    )
    return (
        f"  <system><name>{name}</name><path>%ROMPATH%/{folder}</path>\n"
        f"    <extension>.md .zip</extension>{commands}\n    <platform>genesis</platform>\n  </system>\n"
    )


def _catalogue(genesis: tuple[tuple[str, str], ...], megadrive: tuple[tuple[str, str], ...]) -> str:
    """A catalogue declaring genesis and megadrive with the given entries, and nothing else."""
    return '<?xml version="1.0"?>\n<systemList>\n' + _system("genesis", genesis) + _system("megadrive", megadrive) + "</systemList>\n"


def _retrodeck(
    genesis: tuple[tuple[str, str], ...],
    megadrive: tuple[tuple[str, str], ...],
    extra: dict[str, str] | None = None,
    cores: dict[str, Any] | None = None,
) -> atlas.RetroDeck:
    """A RetroDECK whose catalogue declares genesis and megadrive with the given entries, and nothing else."""
    files = {RETRODECK_JSON: RD_JSON, RETRODECK_CFG: "", CATALOGUE: _catalogue(genesis, megadrive), **(extra or {})}
    dirs = ["/mnt/sd/retrodeck/saves", "/mnt/sd/retrodeck/bios", f"{HOME}/.var/app/net.retrodeck.retrodeck/config/ROMs"]
    return atlas.RetroDeck(HOME, FixtureMachine(files, dirs=dirs, cores=cores))


def _alternative(label: str) -> str:
    return f'<?xml version="1.0"?>\n<alternativeEmulator>\n  <label>{label}</label>\n</alternativeEmulator>\n<gameList />\n'


def _per_game(label: str) -> str:
    return (
        '<?xml version="1.0"?>\n<gameList>\n  <game>\n    <path>./Game.md</path>\n'
        f"    <altemulator>{label}</altemulator>\n  </game>\n</gameList>\n"
    )


def _amiga() -> atlas.RetroDeck:
    """amiga and amiga1200 declared alike with PUAE, whose .info declares one Kickstart image."""
    puae = (
        'corename = "PUAE"\nsystemname = "Amiga"\ndatabase = "Commodore - Amiga|Commodore - CD32|Commodore - CDTV"\n'
        'firmware_count = "1"\nfirmware0_desc = "kick34005.A500 (Kickstart v1.3)"\n'
        'firmware0_path = "kick34005.A500"\nfirmware0_opt = "true"\n'
    )
    system = (
        "  <system><name>{name}</name><path>%ROMPATH%/amiga</path>\n    <extension>.adf</extension>\n"
        '    <command label="PUAE">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/puae_libretro.so %ROM%</command>\n'
        "  </system>\n"
    )
    catalogue = '<?xml version="1.0"?>\n<systemList>\n' + "".join(system.format(name=n) for n in ("amiga", "amiga1200")) + "</systemList>\n"
    return _retrodeck(
        (),
        (),
        {
            CATALOGUE: catalogue,
            RETRODECK_CFG: 'libretro_directory = "/app/cores"\nlibretro_info_path = "/app/cores"\n',
            f"{CORES}/puae_libretro.info": puae,
            f"{CORES}/puae_libretro.so": "",
        },
    )


AMIGA = (("esde", "amiga"), ("esde", "amiga1200"))


class TestTheComparison:
    def test_candidates_declared_alike_answer_the_shared_answer(self):
        answer = _retrodeck((PICODRIVE, GPGX), (PICODRIVE, GPGX)).emulators_for(MEGA_DRIVE)
        assert answer.resolution is not None
        assert answer.resolution.status == atlas.RESOLUTION_UNANIMOUS
        assert tuple(candidate.system for candidate in answer.resolution.candidates) == CANDIDATES
        assert [entry.label for entry in answer.entries] == ["PicoDrive", "Genesis Plus GX"]
        unanimous = [caveat for caveat in answer.caveats if caveat.code == atlas.CAVEAT_SYSTEM_UNANIMOUS]
        assert [dict(caveat.data) for caveat in unanimous] == [
            {"sources": ("esde", "esde"), "keys": ("genesis", "megadrive")}
        ]
        assert atlas.CAVEAT_SYSTEM_AMBIGUOUS not in [caveat.code for caveat in answer.caveats]

    def test_entries_a_gamelist_selection_reorders_stay_ambiguous(self):
        # megadrive's gamelist selects Genesis Plus GX, which moves it first
        # and states the selection on it: the two lists differ in order and
        # in that entry's `selection` (#784 D63). Order alone is held by
        # test_entries_in_another_order_are_another_answer.
        installation = _retrodeck(
            (PICODRIVE, GPGX),
            (PICODRIVE, GPGX),
            {f"{GAMELISTS}/megadrive/gamelist.xml": _alternative("Genesis Plus GX")},
        )
        answer = installation.emulators_for(MEGA_DRIVE)
        assert answer.resolution is not None
        assert answer.resolution.status == atlas.RESOLUTION_AMBIGUOUS
        assert answer.entries == ()
        assert atlas.CAVEAT_SYSTEM_AMBIGUOUS in [caveat.code for caveat in answer.caveats]

    def test_entries_in_another_order_are_another_answer(self):
        # An answer's entry order follows from the entries' own fields — the
        # declared position and the selection that promoted one — so a
        # catalogue cannot give two candidates lists that differ in order
        # alone: declared in another order, the positions differ too. The
        # comparison is held directly: the same entries, one list reversed,
        # are not the same answer (#784 D63).
        installation = _retrodeck((PICODRIVE, GPGX), (PICODRIVE, GPGX))
        genesis, megadrive = (installation.emulators_for(system) for system in CANDIDATES)
        reversed_megadrive = dataclasses.replace(megadrive, entries=megadrive.entries[::-1])
        assert alike([genesis, megadrive], CANDIDATES)
        assert not alike([genesis, reversed_megadrive], CANDIDATES)

    def test_candidates_apart_only_in_a_caveat_stay_ambiguous(self):
        # A per-game choice megadrive's gamelist records is stated on its
        # entries and nowhere on genesis's: the serialized answers differ in a
        # caveat alone, and caveats are compared (#784 D65 d).
        installation = _retrodeck(
            (PICODRIVE, GPGX),
            (PICODRIVE, GPGX),
            {f"{GAMELISTS}/megadrive/gamelist.xml": _per_game("Genesis Plus GX")},
        )
        alone = [catalogue_contract(installation.emulators_for(system)) for system in CANDIDATES]
        for serialized in alone:
            serialized.pop("resolution")
            for entry in serialized["entries"]:
                entry.pop("system")
                entry.pop("caveats")
        assert alone[0] == alone[1], "the candidates differ in more than their caveats"
        answer = installation.emulators_for(MEGA_DRIVE)
        assert answer.resolution is not None
        assert answer.resolution.status == atlas.RESOLUTION_AMBIGUOUS

    def test_an_entry_that_is_not_its_own_candidates_makes_the_answers_apart(self):
        # Every entry a candidate's answer holds stands for that candidate's
        # system; one that stands for another is no answer the others give,
        # and the question answers ambiguous rather than fail (#784 D66).
        installation = _retrodeck((PICODRIVE, GPGX), (PICODRIVE, GPGX))
        genesis, megadrive = (installation.emulators_for(system) for system in CANDIDATES)
        foreign = dataclasses.replace(megadrive, entries=genesis.entries)
        assert not alike([genesis, foreign], CANDIDATES)
        assert shared_answer.shared([genesis, foreign], CANDIDATES, installation.emulators_for(MEGA_DRIVE).resolution) is None

    def test_cores_alike_on_file_emulator_and_label_keep_one_order_whatever_the_candidates(self):
        # The last tie-break of the firmware order: two cores the same on the
        # three keys, apart elsewhere, are listed alike from either order.
        answer = _amiga().firmware_for_system(AMIGA[0])
        core = answer.cores[0]
        twin = dataclasses.replace(core, declared_index=5)
        one, other = (
            shared_answer._in_file_order(dataclasses.replace(answer, cores=cores))  # pyright: ignore[reportPrivateUsage]
            for cores in ((core, twin), (twin, core))
        )
        assert [c.declared_index for c in one.cores] == [c.declared_index for c in other.cores]

    def test_the_firmware_answer_compares_its_cores_as_a_set_and_lists_them_by_file(self):
        # The firmware answer promises no order of its cores (#784 D63).
        installation = _retrodeck(
            (PICODRIVE, GPGX),
            (PICODRIVE, GPGX),
            {f"{GAMELISTS}/megadrive/gamelist.xml": _alternative("Genesis Plus GX")},
        )
        orders = [[core.core_so for core in installation.firmware_for_system(system).cores] for system in CANDIDATES]
        assert orders[0] != orders[1], "the candidates list their cores in one order"
        answer = installation.firmware_for_system(MEGA_DRIVE)
        assert answer.resolution is not None
        assert answer.resolution.status == atlas.RESOLUTION_UNANIMOUS
        assert [core.core_so for core in answer.cores] == ["genesis_plus_gx_libretro.so", "picodrive_libretro.so"]


class TestTheSharedAnswer:
    def test_an_entry_names_no_system_exactly_in_a_shared_answer(self):
        installation = _retrodeck((PICODRIVE, GPGX), (PICODRIVE, GPGX))
        shared = catalogue_contract(installation.emulators_for(MEGA_DRIVE))
        assert [entry["system"] for entry in shared["entries"]] == [None, None]
        alone = catalogue_contract(installation.emulators_for(CANDIDATES[0]))
        assert [entry["system"] for entry in alone["entries"]] == [{"source": "esde", "key": "genesis"}] * 2

    def test_a_caveat_naming_a_candidate_is_stated_once_per_candidate_in_candidate_order(self):
        # No info path: the firmware answer states the declaration it could not
        # read for the system asked, which each candidate names as its own.
        installation = _retrodeck((PICODRIVE, GPGX), (PICODRIVE, GPGX))
        caveats = firmware_contract(installation.firmware_for_system(MEGA_DRIVE))["caveats"]
        unknown = [caveat["data"] for caveat in caveats if caveat["code"] == "firmware-declaration-unknown"]
        assert unknown == [{"source": "esde", "key": "genesis"}, {"source": "esde", "key": "megadrive"}]

    def test_the_resolutions_notes_ride_along(self):
        # A pair the table cannot place rides along with its own caveat and
        # decides nothing (#784 D44); the shared answer still states it.
        installation = _retrodeck((PICODRIVE, GPGX), (PICODRIVE, GPGX))
        answer = installation.emulators_for([MEGA_DRIVE, ("romm", "no-such-platform")])
        assert answer.resolution is not None
        assert answer.resolution.status == atlas.RESOLUTION_UNANIMOUS
        codes = [caveat.code for caveat in answer.caveats]
        assert codes.index(atlas.CAVEAT_PLATFORM_UNMAPPED) < codes.index(atlas.CAVEAT_SYSTEM_UNANIMOUS)


GAME_BOY = ("libretro", "Nintendo - Game Boy")
GB_CANDIDATES = (("esde", "gb"), ("esde", "sgb"))
GB_CONTENT = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/ROMs/gb/Game.gb"


def _game_boy() -> atlas.RetroDeck:
    """gb and sgb declared alike, with Gambatte, whose save record holds gb and not sgb, and mGBA."""
    entries = (("Gambatte", "gambatte"), ("mGBA", "mgba"))
    catalogue = (
        '<?xml version="1.0"?>\n<systemList>\n'
        + _system("gb", entries, folder="gb").replace(".md .zip", ".gb .zip")
        + _system("sgb", entries, folder="gb").replace(".md .zip", ".gb .zip")
        + "</systemList>\n"
    )
    return _retrodeck((), (), {CATALOGUE: catalogue})


class TestASharedEntrysOwnQuestions:
    """An entry of a shared answer asks every candidate's entry at its place (#784 D66, D67)."""

    def test_where_the_candidates_entries_agree_their_answer_is_the_answer(self):
        installation = _retrodeck((PICODRIVE, GPGX), (PICODRIVE, GPGX))
        shared = installation.emulators_for(MEGA_DRIVE).entries[0]
        placement = shared.savestate_location(content_path=f"{HOME}/roms/megadrive/Game.md")
        alone = [
            installation.emulators_for(system).entries[0].savestate_location(content_path=f"{HOME}/roms/megadrive/Game.md")
            for system in CANDIDATES
        ]
        assert not isinstance(placement, atlas.Unresolved)
        assert savestate_answer_contract(placement) == savestate_answer_contract(alone[0])

    def test_where_they_differ_the_entry_refuses_naming_the_candidates(self):
        installation = _game_boy()
        shared = installation.emulators_for(GAME_BOY)
        assert shared.resolution is not None
        assert shared.resolution.status == atlas.RESOLUTION_UNANIMOUS
        alone = [
            savefile_answer_contract(installation.emulators_for(system).entries[0].savefile_location(content_path=GB_CONTENT))
            for system in GB_CANDIDATES
        ]
        assert alone[0] != alone[1], "the candidates' entries place the save alike"
        refusal = shared.entries[0].savefile_location(content_path=GB_CONTENT)
        assert isinstance(refusal, atlas.Unresolved)
        assert refusal.code == atlas.UNRESOLVED_SYSTEM_AMBIGUOUS == atlas.CAVEAT_SYSTEM_AMBIGUOUS
        assert dict(refusal.data) == {"sources": ("esde", "esde"), "keys": ("gb", "sgb")}

    def test_save_portability_asks_each_candidates_entries(self):
        # save_portability never asks a shared entry: each candidate's answer
        # asks that candidate's own entries, and the answers are compared.
        # Gambatte's record holds gb and not sgb, so they differ.
        installation = _game_boy()
        assert installation.emulators_for(GAME_BOY).resolution is not None
        answer = installation.save_portability(GAME_BOY, source="Gambatte", target="mGBA", content_path=GB_CONTENT)
        assert answer.resolution is not None
        assert answer.resolution.status == atlas.RESOLUTION_AMBIGUOUS

    def test_the_provenance_states_every_candidates_in_candidate_order(self):
        # genesis declared in the bundled layer, megadrive in the custom one:
        # prose for debugging, stated for each and never compared (#784 D67 c).
        overlay = "/mnt/sd/retrodeck/ES-DE/custom_systems/es_systems.xml"
        megadrive = '<?xml version="1.0"?>\n<systemList>\n' + _system("megadrive", (PICODRIVE, GPGX)) + "</systemList>\n"
        genesis = '<?xml version="1.0"?>\n<systemList>\n' + _system("genesis", (PICODRIVE, GPGX)) + "</systemList>\n"
        installation = _retrodeck((), (), {CATALOGUE: genesis, overlay: megadrive})
        answer = installation.emulators_for(MEGA_DRIVE)
        assert answer.resolution is not None
        assert answer.resolution.status == atlas.RESOLUTION_UNANIMOUS
        alone = [installation.emulators_for(system).entries[0].provenance for system in CANDIDATES]
        assert alone[0] != alone[1]
        assert answer.entries[0].provenance == "; ".join(alone)


def _carded_catalogue() -> str:
    """genesis and megadrive declared alike with one standalone entry, Cemu, whose firmware card atlas packages."""
    system = (
        "  <system><name>{name}</name><path>%ROMPATH%/megadrive</path>\n    <extension>.md</extension>\n"
        '    <command label="Cemu (Standalone)">%EMULATOR_CEMU% -g %ROM%</command>\n'
        "    <platform>genesis</platform>\n  </system>\n"
    )
    return '<?xml version="1.0"?>\n<systemList>\n' + "".join(system.format(name=name) for name in ("genesis", "megadrive")) + "</systemList>\n"


def _emudeck(catalogue: str) -> atlas.EmuDeck:
    """An EmuDeck whose exclusive custom catalogue is *catalogue*."""
    files = {
        f"{HOME}/.config/EmuDeck/settings.sh": 'romsPath="$HOME/Emulation/roms"\nsavesPath="$HOME/Emulation/saves"\n',
        f"{HOME}/.var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg": (
            'savefile_directory = "/home/deck/Emulation/saves/retroarch/saves"\n'
        ),
        f"{HOME}/ES-DE/custom_systems/es_systems.xml": catalogue.replace("<systemList>", "<loadExclusive/>\n<systemList>", 1),
    }
    dirs = [f"{HOME}/Emulation/saves", f"{HOME}/Emulation/saves/retroarch/saves", f"{HOME}/Applications", f"{HOME}/ROMs"]
    return atlas.EmuDeck(HOME, FixtureMachine(files, dirs=dirs))


def _standalone(label: str, token: str) -> str:
    """genesis and megadrive declared alike with one standalone entry."""
    system = (
        "  <system><name>{name}</name><path>%ROMPATH%/megadrive</path>\n    <extension>.md</extension>\n"
        f'    <command label="{label}">%EMULATOR_{token}% %ROM%</command>\n  </system>\n'
    )
    return '<?xml version="1.0"?>\n<systemList>\n' + "".join(system.format(name=n) for n in ("genesis", "megadrive")) + "</systemList>\n"


def _refusals_naming_a_system(path: Path) -> Iterator[ast.Call]:
    """Every ``Unresolved(...)`` in *path* whose literal data holds both ``source`` and ``key``."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Unresolved"):
            continue
        data = node.args[2] if len(node.args) > 2 else None
        keys = {key.value for key in data.keys if isinstance(key, ast.Constant)} if isinstance(data, ast.Dict) else set()
        if {"source", "key"} <= keys:
            yield node


class TestARefusalTheCandidatesShare:
    """Where every candidate's entry refuses for the same reason naming its own system (#784 D69)."""

    @staticmethod
    def _shared_entry(installation: atlas.RetroDeck | atlas.EmuDeck) -> atlas.EmulatorEntry:
        answer = installation.emulators_for(MEGA_DRIVE)
        assert answer.resolution is not None
        assert answer.resolution.status == atlas.RESOLUTION_UNANIMOUS
        return answer.entries[0]

    def test_a_standalone_entry_no_card_covers_refuses_with_its_reason_naming_every_candidate(self):
        entry = self._shared_entry(_retrodeck((), (), {CATALOGUE: _standalone("Exodus (Standalone)", "EXODUS")}))
        for refusal in (entry.savefile_location(), entry.savestate_location(), entry.texture_pack_location(), entry.mod_location()):
            assert isinstance(refusal, atlas.Unresolved)
            assert refusal.code == atlas.UNRESOLVED_STANDALONE
            assert dict(refusal.data) == {
                "label": "Exodus (Standalone)",
                "sources": ("esde", "esde"),
                "keys": ("genesis", "megadrive"),
            }

    def test_a_variant_nothing_establishes_refuses_with_its_reason_naming_every_candidate(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # Vita3K's save card answers for psvita alone; given the siblings, the
        # EmuDeck launch without its binary falls to the Proton variant, which
        # no tree is established for, under both.
        cards = tuple(
            dataclasses.replace(card, systems=("genesis", "megadrive")) if card.token == "VITA3K" else card
            for card in load_standalone_saves()
        )
        monkeypatch.setattr(standalone_saves, "_PACKAGED", cards)
        entry = self._shared_entry(_emudeck(_standalone("Vita3K (Standalone)", "VITA3K")))
        refusal = entry.savefile_location()
        assert isinstance(refusal, atlas.Unresolved)
        assert refusal.code == atlas.UNRESOLVED_STANDALONE_VARIANT_UNESTABLISHED
        assert dict(refusal.data) == {
            "label": "Vita3K (Standalone)",
            "variant": "proton",
            "sources": ("esde", "esde"),
            "keys": ("genesis", "megadrive"),
        }

    def test_an_entry_naming_a_foreign_core_refuses_with_its_reason_naming_every_candidate(self):
        foreign = _catalogue((("Genesis Plus GX (Windows)", "genesis_plus_gx"),), (("Genesis Plus GX (Windows)", "genesis_plus_gx"),))
        entry = self._shared_entry(_emudeck(foreign.replace("_libretro.so", "_libretro.dll")))
        refusal = entry.savefile_location()
        assert isinstance(refusal, atlas.Unresolved)
        assert refusal.code == atlas.UNRESOLVED_CORE_FILE_FOREIGN
        assert dict(refusal.data) == {
            "core_file": "genesis_plus_gx_libretro.dll",
            "label": "Genesis Plus GX (Windows)",
            "sources": ("esde", "esde"),
            "keys": ("genesis", "megadrive"),
        }

    def test_the_refusals_naming_the_entrys_own_system_are_the_ones_listed(self):
        # The codes of the Unresolved(...) calls whose data is a dictionary
        # literal naming `source` and `key` are OWN_SYSTEM_REFUSALS (#784 D69).
        exported = {name: getattr(atlas, name) for name in atlas.__all__ if name.startswith("UNRESOLVED_")}
        sites = [node for path in Path(atlas.__file__).parent.glob("*.py") for node in _refusals_naming_a_system(path)]
        assert all(isinstance(node.args[0], ast.Name) for node in sites), "a refusal names its code through no constant"
        assert {exported[cast(ast.Name, node.args[0]).id] for node in sites} == set(shared_answer.OWN_SYSTEM_REFUSALS)

    def test_a_refusal_not_listed_is_never_rewritten(self):
        refusals = [atlas.Unresolved(atlas.UNRESOLVED_NOT_SET_UP, "", {"source": source, "key": key}) for source, key in CANDIDATES]
        with pytest.raises(AssertionError, match="no refusal that names the entry's own system"):
            _merged(refusals, CANDIDATES, None)

    def test_the_shared_refusal_reads_as_no_one_candidates(self):
        entry = self._shared_entry(_retrodeck((), (), {CATALOGUE: _standalone("Exodus (Standalone)", "EXODUS")}))
        refusal = entry.savefile_location()
        assert isinstance(refusal, atlas.Unresolved)
        assert "(genesis)" in refusal.message
        assert "(megadrive)" in refusal.message


class TestTheOwnPairIsDecidedPerPlace:
    """A statement sets its pair aside only where every candidate's names that candidate's own system."""

    def test_a_statement_naming_one_sibling_for_every_candidate_is_compared_as_it_stands(self):
        # PUAE files Amiga firmware under amiga, so every candidate's answer
        # states amiga's recorded firmware verdict: own for amiga, not for
        # amiga1200 — the same statement, compared literally and stated once.
        installation = _amiga()
        answer = installation.firmware_for_system(("libretro", "Commodore - Amiga"))
        assert answer.resolution is not None
        assert answer.resolution.status == atlas.RESOLUTION_UNANIMOUS
        knowledge = [
            dict(caveat.data)
            for core in answer.cores
            for caveat in core.caveats
            if caveat.code == atlas.CAVEAT_SYSTEM_FIRMWARE_WORLD_KNOWLEDGE
        ]
        assert knowledge == [{"source": "esde", "key": "amiga", "evidence": "verified"}]
        # In either candidate order: the statement names the first candidate
        # here and the second there, and is compared as it stands both ways.
        systems = (("esde", "amiga1200"), ("esde", "amiga"))
        assert alike([installation.firmware_for_system(system) for system in systems], systems)


class TestTheMerge:
    def test_a_caveat_stated_once_states_each_candidates_message(self):
        # Alike but for their prose: one caveat, each distinct message once,
        # in candidate order (#784 D67 c).
        caveats = [atlas.Caveat("info-path-unresolved", message, {}) for message in ("genesis said", "megadrive said")]
        merged = _merged(caveats, CANDIDATES, None)
        assert merged.message == "genesis said; megadrive said"

    def test_three_candidates_two_alike_state_each_distinct_value_once(self):
        systems = (*CANDIDATES, ("esde", "megadrivejp"))
        caveats = [atlas.Caveat("info-path-unresolved", message, {}) for message in ("a", "b", "a")]
        assert _merged(caveats, systems, None).message == "a; b"

    def test_an_answer_with_a_resolution_merged_without_one_fails_loudly(self):
        installation = _retrodeck((PICODRIVE, GPGX), (PICODRIVE, GPGX))
        answers = [installation.rom_location(system) for system in CANDIDATES]
        with pytest.raises(AssertionError, match="merged without the unanimous one"):
            _merged(answers, CANDIDATES, None)

    def test_a_caveat_naming_each_candidates_own_system_outside_a_tuple_fails_loudly(self):
        # Stated once per candidate, such a caveat needs a tuple to stand in;
        # anywhere else it would be stated as the first candidate's.
        caveats = [atlas.Caveat("system-unknown", "", {"source": source, "key": key}) for source, key in CANDIDATES]
        with pytest.raises(AssertionError, match="cannot be stated once per candidate"):
            _merged(caveats, CANDIDATES, None)

    def test_tuples_of_different_lengths_the_comparison_passed_fail_loudly(self):
        # Never one candidate's tuple: a length the comparison did not see is an error.
        installation = _retrodeck((PICODRIVE, GPGX), (PICODRIVE, GPGX))
        genesis, megadrive = (installation.firmware_for_system(system) for system in CANDIDATES)
        longer = dataclasses.replace(megadrive, caveats=(*megadrive.caveats, *megadrive.caveats))
        with pytest.raises(AssertionError, match="items at one place"):
            _merged([genesis, longer], CANDIDATES, None)


class TestACardFiledRequirement:
    """A firmware requirement a standalone card files under the asked system (#784 D68)."""

    def test_every_packaged_card_names_exactly_one_system(self):
        several = {card.token: card.systems for card in standalone_firmware_cards() if len(card.systems) != 1}
        assert several == {}, (
            f"standalone firmware cards naming other than one system: {several} — a card naming several "
            "systems is the case #784 D68 left open: two candidates of a shared answer would each carry a "
            "requirement filed under their own system, and the shared form of that requirement's `system` "
            "is to be decided now, on this card"
        )

    def test_it_is_compared_as_it_stands(self, monkeypatch: pytest.MonkeyPatch):
        # No packaged card names two systems, so the case is built by giving
        # Cemu's card the two siblings in place of its own system: each
        # candidate's requirement is filed under its own system, and the
        # candidates stay ambiguous.
        installation = _retrodeck((), (), {CATALOGUE: _carded_catalogue()})
        unanimous = installation.firmware_for_system(MEGA_DRIVE)
        assert unanimous.resolution is not None
        assert unanimous.resolution.status == atlas.RESOLUTION_UNANIMOUS, "the packaged card names neither sibling"
        widened = tuple(
            dataclasses.replace(card, systems=("genesis", "megadrive")) if card.token == "CEMU" else card
            for card in standalone_firmware_cards()
        )
        monkeypatch.setattr(standalone_firmware, "_PACKAGED", widened)
        filed = [
            [
                requirement.system
                for core in installation.firmware_for_system(system).cores
                for requirement in core.requirements
                if isinstance(requirement, atlas.FirmwareRequirement)
            ]
            for system in CANDIDATES
        ]
        assert filed == [[CANDIDATES[0]], [CANDIDATES[1]]]
        answer = installation.firmware_for_system(MEGA_DRIVE)
        assert answer.resolution is not None
        assert answer.resolution.status == atlas.RESOLUTION_AMBIGUOUS


class TestWhatTheRuleLeavesAlone:
    def test_platform_ids_keeps_several_systems_ambiguous(self):
        # It answers about a name's standing (#784 D65 c).
        answer = _retrodeck((PICODRIVE, GPGX), (PICODRIVE, GPGX)).platform_ids(MEGA_DRIVE)
        assert answer.resolution is not None
        assert answer.resolution.status == atlas.RESOLUTION_AMBIGUOUS

    def test_gamelist_selections_still_refuses_several_systems(self):
        installation = _retrodeck((PICODRIVE, GPGX), (PICODRIVE, GPGX))
        with pytest.raises(ValueError, match="names no single system"):
            installation.gamelist_selections(MEGA_DRIVE)


# The attributes of an entry a shared entry may take from its first
# candidate's: the comparison holds them alike — serialized, or the command
# (#784 D67 b) — so they are every candidate's.
_HELD_ALIKE = frozenset(
    {"label", "kind", "core_so", "emulator", "declared_index", "selection", "availability", "launcher", "core_path", "command"}
)


class TestASharedEntryIsNoOneCandidates:
    def test_every_question_and_attribute_is_its_own_or_held_alike(self):
        # A question added to EmulatorEntry later would otherwise answer, on a
        # shared entry, as the first candidate (#784 D66): it has to be
        # overridden there, or be one of the attributes the comparison holds.
        public = {name for name in dir(atlas.EmulatorEntry) if not name.startswith("_")} | {"_keyed_savefile_location"}
        own = set(vars(SharedEntry))
        assert public - own == _HELD_ALIKE, (
            f"EmulatorEntry members a SharedEntry answers as its first candidate: {sorted(public - own - _HELD_ALIKE)}; "
            "override them in atlas._shared_answer.SharedEntry, or hold them alike in the comparison"
        )

    def test_a_shared_entry_stands_for_two_entries_or_more(self):
        entry = _retrodeck((PICODRIVE, GPGX), (PICODRIVE, GPGX)).emulators_for(CANDIDATES[0]).entries[0]
        with pytest.raises(ValueError, match="one entry per candidate, two or more"):
            SharedEntry((entry,), (), CANDIDATES[:1])
        with pytest.raises(ValueError, match="one entry per candidate, two or more"):
            SharedEntry((entry, entry), (), CANDIDATES + (("esde", "megadrivejp"),))

    def test_a_shared_entry_holds_each_candidates_entry_in_candidate_order(self):
        installation = _retrodeck((PICODRIVE, GPGX), (PICODRIVE, GPGX))
        genesis, megadrive = (installation.emulators_for(system).entries[0] for system in CANDIDATES)
        assert SharedEntry((genesis, megadrive), (), CANDIDATES).system is None
        with pytest.raises(ValueError, match="each entry is its own candidate's, in candidate order"):
            SharedEntry((megadrive, genesis), (), CANDIDATES)


# The answer types a candidate gives, which the comparison serializes.
_COMPARED_TYPES = tuple(kind for kind, _ in shared_answer._SERIALIZERS)  # pyright: ignore[reportPrivateUsage]
# What the walks treat whole, and a resolution, which is about the asking.
_NOT_WALKED = (atlas.SystemResolution,)
# A reachable class no vector builds an instance of, so no probe reaches it,
# with the reason its fields are none the lists need.
_NOT_BUILT = {
    atlas.Conversion: (
        "save_portability_contract writes both its fields, rule and length; no vector's convertible part pins a "
        "conversion: `conversion` is null in every one"
    ),
}
# Fields whose probe does not reach the serialization in every instance and
# that neither list holds, each with why it cannot differ once the comparison
# has passed.
_REACHED_WHERE_IT_COUNTS = {
    (atlas.FirmwareAlternatives, "region"): (
        "written exactly where for_content is true (contract.py, _requirement_entry_contract); where it is false the "
        "region is None (FirmwareAlternatives.__post_init__), and for_content is compared"
    ),
    (atlas.FirmwareAlternatives, "region_source"): (
        "stated together with region (FirmwareAlternatives.__post_init__), so it follows region's case"
    ),
    (atlas.FirmwareAlternatives, "options"): (
        "serialized whole as the group's alternatives; the properties satisfied and weighed_options read it first "
        "and fail on the probe, so no instance shows the write"
    ),
    (atlas.SuppliedBy, "distribution"): (
        "serialized as the requirement's supplied_by.distribution; distribution_label fails on the probe before "
        "the output is complete, so no instance shows the write"
    ),
}


def _classes(hint: Any) -> Iterator[type]:
    """The classes a field's type hint names, through unions and generics."""
    args = typing.get_args(hint)
    if not args:
        if isinstance(hint, type):
            yield hint
        return
    for arg in args:
        yield from _classes(arg)


def _walk_hints(cls: type, found: dict[type, dict[str, Any]]) -> None:
    """*cls* and every dataclass its fields reach, each with its field types, into *found*.

    A field the comparison compares whole is not descended into: its value is
    compared as one.
    """
    if cls in found or not dataclasses.is_dataclass(cls) or cls in _NOT_WALKED:
        return
    hints = typing.get_type_hints(cls, vars(sys.modules[cls.__module__]))
    found[cls] = hints
    for name, hint in hints.items():
        if (cls, name) in shared_answer.COMPARED:
            continue
        for reached in _classes(hint):
            _walk_hints(reached, found)


def _reachable() -> dict[type, dict[str, Any]]:
    """Every dataclass reachable from the compared answer types through their fields, with its field types."""
    found: dict[type, dict[str, Any]] = {}
    for root in _COMPARED_TYPES:
        _walk_hints(root, found)
    return found


@functools.cache
def _corpus_answers() -> tuple[Any, ...]:
    """Every answer of a compared type the machines vectors build, captured where the runner serializes it."""
    import tests.test_machine_vectors as runner

    captured: list[Any] = []
    serializers = {name: value for name, value in vars(runner).items() if name.endswith("_contract") and callable(value)}

    def capturing(serialize: Callable[..., Any]) -> Callable[..., Any]:
        def capture(*args: Any, **kwargs: Any) -> Any:
            captured.extend(arg for arg in args if isinstance(arg, _COMPARED_TYPES))
            return serialize(*args, **kwargs)

        return capture

    try:
        for name, serialize in serializers.items():
            setattr(runner, name, capturing(serialize))
        for param in runner.load_vectors():
            vector = cast(dict[str, Any], param.values[0])
            inp = vector["input"]
            installs = atlas.detect(inp["home"], runner.fixture_machine(inp))
            for key, (query_key, ask) in runner.QUESTIONS.items():
                if query_key in inp and key in vector["expected"]:
                    ask(installs, inp[query_key], vector["name"])
    finally:
        for name, serialize in serializers.items():
            setattr(runner, name, serialize)
    return tuple(captured)


def _instances(value: Any, found: dict[type, list[Any]]) -> None:
    """Every dataclass instance *value* holds, by class, not inside a field the comparison compares whole."""
    if isinstance(value, tuple):
        for item in cast(tuple[Any, ...], value):
            _instances(item, found)
        return
    if not dataclasses.is_dataclass(value) or isinstance(value, (type, *_NOT_WALKED)):
        return
    found.setdefault(type(value), []).append(value)
    for part in dataclasses.fields(value):
        if (type(value), part.name) not in shared_answer.COMPARED:
            _instances(getattr(value, part.name), found)


_PROBE = object()
_PACKAGE = Path(atlas.__file__).resolve().parent
_CONTRACT = Path(atlas.contract.__file__).resolve()


def _holds_probe(value: Any) -> bool:
    if value is _PROBE:
        return True
    if isinstance(value, dict):
        return any(_holds_probe(item) for item in cast(dict[str, Any], value).values())
    if isinstance(value, (list, tuple)):
        return any(_holds_probe(item) for item in cast(list[Any], value))
    return False


def _probed(root: Any, instance: Any, name: str, before: str) -> str:
    """What putting a probe in *instance*'s field *name* does to *root*'s serialization.

    ``reached``: the probe is in the output, the output changed, or a
    serializer failed on it. ``derived``: a property or method outside the
    serializers failed on it first, so this instance shows nothing about the
    write. ``silent``: the output is the same — the value reaches it nowhere,
    or only for its truth, or only in other contexts.
    """
    old = getattr(instance, name)
    object.__setattr__(instance, name, _PROBE)
    try:
        after = shared_answer.serialized(root)
    except Exception as failure:  # what failed on the probe says whether a serializer read it
        frames = [
            frame
            for frame in traceback.extract_tb(failure.__traceback__)
            if Path(frame.filename).resolve().is_relative_to(_PACKAGE)
        ]
        return "reached" if frames and Path(frames[-1].filename).resolve() == _CONTRACT else "derived"
    else:
        changed = _holds_probe(after) or json.dumps(after, sort_keys=True, default=str) != before
        return "reached" if changed else "silent"
    finally:
        object.__setattr__(instance, name, old)


def _outcomes(roots: Iterable[Any]) -> tuple[dict[tuple[type, str], set[str]], set[type]]:
    """Per field, every outcome its probe has over every instance *roots* hold; and the classes they build.

    Every instance is probed, so the outcomes are a set the order of the
    roots cannot change.
    """
    outcomes: dict[tuple[type, str], set[str]] = {}
    built: set[type] = set()
    for root in roots:
        found: dict[type, list[Any]] = {}
        _instances(root, found)
        built |= set(found)
        before = json.dumps(shared_answer.serialized(root), sort_keys=True, default=str)
        for cls, instances in found.items():
            for instance, part in ((instance, part) for instance in instances for part in dataclasses.fields(instance)):
                outcomes.setdefault((cls, part.name), set()).add(_probed(root, instance, part.name, before))
    return outcomes, built


class TestWhatNoSerializerWrites:
    """The fields whose value does not reach the serialization in every answer are compared, joined or reasoned.

    Each field of every instance of the classes the machines vectors build is
    replaced by a probe, one at a time (:func:`_probed`). A field is written
    where its probe reaches the output in some instance and leaves it
    unchanged in none; any other — never reaching it, reaching it only in some
    contexts or only for its truth, or shown only through a property that
    fails first — is in atlas._shared_answer's COMPARED or JOINED, or in
    :data:`_REACHED_WHERE_IT_COUNTS` with its reason.
    """

    def test_every_field_not_written_in_every_answer_is_compared_joined_or_reasoned(self):
        outcomes, built = _outcomes(_corpus_answers())
        # Written: reached in some instance and silent in none; an instance a
        # property failed in first shows nothing either way.
        not_always = {field for field, seen in outcomes.items() if "silent" in seen or "reached" not in seen}
        compared_or_joined = shared_answer.COMPARED | shared_answer.JOINED
        assert not_always - compared_or_joined - set(_REACHED_WHERE_IT_COUNTS) == set()
        assert compared_or_joined | set(_REACHED_WHERE_IT_COUNTS) <= not_always, "a listed field always written"
        never = {field for field, seen in outcomes.items() if "reached" not in seen and "silent" in seen}
        assert never <= compared_or_joined, "a field never written may only be compared or joined"
        assert {cls for cls in _reachable() if cls not in built} == set(_NOT_BUILT)

    def test_the_verdict_does_not_turn_on_the_order_of_the_corpus(self):
        answers = _corpus_answers()
        assert _outcomes(answers)[0] == _outcomes(tuple(reversed(answers)))[0]

    def test_the_lists_are_disjoint_fields_and_joined_ones_are_prose(self):
        reachable = _reachable()
        compared, joined, reasoned = shared_answer.COMPARED, shared_answer.JOINED, set(_REACHED_WHERE_IT_COUNTS)
        assert compared & joined == set()
        assert compared & reasoned == set()
        assert joined & reasoned == set()
        assert {(cls, name) for cls, name in compared | joined | reasoned if name not in reachable.get(cls, {})} == set()
        not_prose = {(cls.__name__, name) for cls, name in joined if reachable[cls][name] not in (str, tuple[str, ...])}
        assert not_prose == set()

    def test_no_container_the_walks_pass_whole_holds_a_statement_or_an_entry(self):
        # The walks descend into tuples and dataclasses; a list, a set or a
        # mapping is handed on as the first candidate's, so none may hold
        # something a candidate states about its own system — and a caveat,
        # stated once per candidate where it names each one's own system,
        # stands only in a tuple of any length.
        def holds(hint: Any) -> bool:
            args = typing.get_args(hint)
            if not args:
                return isinstance(hint, type) and (
                    issubclass(hint, (atlas.Caveat, atlas.Unresolved, atlas.EmulatorEntry)) or dataclasses.is_dataclass(hint)
                )
            return any(holds(arg) for arg in args)

        def variadic(hint: Any) -> bool:
            args = typing.get_args(hint)
            return typing.get_origin(hint) is tuple and len(args) == 2 and args[1] is Ellipsis

        whole = [
            f"{cls.__name__}.{name}"
            for cls, hints in _reachable().items()
            for name, hint in hints.items()
            if typing.get_origin(hint) in (list, set, frozenset, dict, collections.abc.Mapping) and holds(hint)
        ]
        lone = [
            f"{cls.__name__}.{name}"
            for cls, hints in _reachable().items()
            for name, hint in hints.items()
            if not variadic(hint) and atlas.Caveat in set(_classes(hint))
        ]
        assert whole == []
        assert lone == []


class TestTheAnswerTypesHoldTheSharedForm:
    """An entry names no system exactly where the answer's resolution is unanimous (#784 D65 e)."""

    def test_an_entry_naming_a_system_beside_a_unanimous_resolution_is_refused(self):
        installation = _retrodeck((PICODRIVE, GPGX), (PICODRIVE, GPGX))
        shared = installation.emulators_for(MEGA_DRIVE)
        alone = installation.emulators_for(CANDIDATES[0])
        assert shared.resolution is not None
        assert alone.resolution is not None
        with pytest.raises(ValueError, match="names no system exactly where the resolution is unanimous"):
            dataclasses.replace(shared, entries=alone.entries)
        with pytest.raises(ValueError, match="names no system exactly where the resolution is unanimous"):
            dataclasses.replace(alone, entries=shared.entries)
        command = installation.launch_command(MEGA_DRIVE, f"{HOME}/roms/megadrive/Game.md")
        with pytest.raises(ValueError, match="names no system exactly where the resolution is unanimous"):
            dataclasses.replace(command, entry=alone.entries[0])

    @staticmethod
    def _launchable(system: Any) -> atlas.LaunchabilityAnswer:
        installation = _retrodeck((PICODRIVE, GPGX), (PICODRIVE, GPGX))
        return installation.launchable(system, f"{HOME}/roms/megadrive/Game.md")

    def test_launchability_answer_entry(self):
        shared, alone = self._launchable(MEGA_DRIVE), self._launchable(CANDIDATES[0])
        assert shared.entry is not None
        assert alone.entry is not None
        with pytest.raises(ValueError, match="names no system exactly where the resolution is unanimous"):
            dataclasses.replace(shared, entry=alone.entry)
        with pytest.raises(ValueError, match="names no system exactly where the resolution is unanimous"):
            dataclasses.replace(alone, entry=shared.entry)

    def test_launchability_answer_installers(self):
        shared, alone = self._launchable(MEGA_DRIVE), self._launchable(CANDIDATES[0])
        assert shared.entry is not None
        assert alone.entry is not None
        needs = {"verdict": atlas.VERDICT_NEEDS_INSTALLATION, "entry": None, "boots": None, "opens": None}
        naming = (atlas.Installer("RPCS3", alone.entry),)
        unnamed = (atlas.Installer("RPCS3", shared.entry),)
        with pytest.raises(ValueError, match="names no system exactly where the resolution is unanimous"):
            dataclasses.replace(shared, **needs, installers=naming)
        with pytest.raises(ValueError, match="names no system exactly where the resolution is unanimous"):
            dataclasses.replace(alone, **needs, installers=unnamed)

    def test_save_portability_answer_source_and_target(self):
        installation = _retrodeck((PICODRIVE, GPGX), (PICODRIVE, GPGX))
        shared = installation.save_portability(MEGA_DRIVE, source="PicoDrive", target="Genesis Plus GX")
        alone = installation.save_portability(CANDIDATES[0], source="PicoDrive", target="Genesis Plus GX")
        assert shared.source is not None
        assert alone.source is not None
        for side in ("source", "target"):
            naming, unnamed = {side: getattr(alone, side)}, {side: getattr(shared, side)}
            with pytest.raises(ValueError, match="names no system exactly where the resolution is unanimous"):
                dataclasses.replace(shared, **naming)
            with pytest.raises(ValueError, match="names no system exactly where the resolution is unanimous"):
                dataclasses.replace(alone, **unnamed)


class TestWhatTheComparisonHoldsBesideTheSerialization:
    """What no serializer writes and the comparison holds all the same: commands (D67 b), COMPARED, and prose (D67 c)."""

    def test_candidates_whose_entry_launches_another_command_stay_ambiguous(self):
        # The catalogue answer does not serialize an entry's command, so the
        # two candidates serialize alike; the command is what the entry
        # launches, and a shared entry stating one would be one candidate's.
        installation = _retrodeck(
            (PICODRIVE, GPGX),
            (PICODRIVE, GPGX),
            {CATALOGUE: _catalogue((PICODRIVE, GPGX), (PICODRIVE, GPGX)).replace(
                "picodrive_libretro.so %ROM%", "picodrive_libretro.so --verbose %ROM%", 1
            )},
        )
        alone = [catalogue_contract(installation.emulators_for(system)) for system in CANDIDATES]
        for serialized in alone:
            serialized.pop("resolution")
            for entry in serialized["entries"]:
                entry.pop("system")
        assert alone[0] == alone[1], "the candidates differ in more than their commands"
        answer = installation.emulators_for(MEGA_DRIVE)
        assert answer.resolution is not None
        assert answer.resolution.status == atlas.RESOLUTION_AMBIGUOUS

    def test_the_systems_a_core_firmware_answer_needs_are_compared(self):
        # CoreFirmware.system_firmware_needs reaches the serialization only
        # through a property; differing in the second candidate only, with
        # every serialized field alike, the candidates are apart.
        installation = _amiga()
        first, second = (installation.firmware_for_system(system) for system in AMIGA)

        def needs(answer: atlas.FirmwareAnswer, key: str) -> atlas.FirmwareAnswer:
            core = dataclasses.replace(answer.cores[0], system_firmware_needs=(("esde", key),))
            return dataclasses.replace(answer, cores=(core,))

        assert alike([needs(first, "amiga"), needs(second, "amiga")], AMIGA)
        assert not alike([needs(first, "amiga"), needs(second, "amiga1200")], AMIGA)

    def test_the_systems_a_search_found_no_image_for_are_compared(self):
        # CoreFirmware.absent_by_search, read only through a property, and
        # only on a packaged card's core with no requirement.
        installation = _retrodeck((PICODRIVE, GPGX), (PICODRIVE, GPGX))
        first, second = (installation.firmware_for_system(system) for system in CANDIDATES)

        def absent(answer: atlas.FirmwareAnswer, key: str) -> atlas.FirmwareAnswer:
            core = dataclasses.replace(answer.cores[0], declaration="packaged", absent_by_search=(("esde", key),))
            return dataclasses.replace(answer, cores=(core, *answer.cores[1:]))

        assert alike([absent(first, "psx"), absent(second, "psx")], CANDIDATES)
        assert not alike([absent(first, "psx"), absent(second, "ps2")], CANDIDATES)

    def test_a_compared_core_fact_that_differs_keeps_the_candidates_apart(self):
        # CoreFirmware.unread, which no serializer writes (atlas._shared_answer.COMPARED).
        # Each candidate's core states one unread key, a different one: the
        # same shape, so only the comparison of the field itself parts them.
        installation = _amiga()
        first, second = (installation.firmware_for_system(system) for system in AMIGA)
        assert alike([first, second], AMIGA)

        def unread(answer: atlas.FirmwareAnswer, key: str) -> atlas.FirmwareAnswer:
            return dataclasses.replace(answer, cores=(dataclasses.replace(answer.cores[0], unread=(key,)),))

        assert alike([unread(first, "firmware8_path"), unread(second, "firmware8_path")], AMIGA)
        assert not alike([unread(first, "firmware8_path"), unread(second, "firmware9_path")], AMIGA)

    def test_a_compared_fact_of_a_requirement_that_differs_keeps_the_candidates_apart(self):
        # FirmwareRequirement.description, inside a core's requirements.
        installation = _amiga()
        first, second = (installation.firmware_for_system(system) for system in AMIGA)
        core = second.cores[0]
        requirement = core.requirements[0]
        assert isinstance(requirement, atlas.FirmwareRequirement)
        other = dataclasses.replace(requirement, description="another description")
        apart = dataclasses.replace(second, cores=(dataclasses.replace(core, requirements=(other,)),))
        assert not alike([first, apart], AMIGA)

    def test_prose_never_decides_whether_the_candidates_agree(self):
        # An answer's sources are prose (JOINED): one more of them in one
        # candidate's answer neither parts the candidates nor drops a source.
        installation = _retrodeck((PICODRIVE, GPGX), (PICODRIVE, GPGX))
        genesis, megadrive = (installation.emulators_for(system) for system in CANDIDATES)
        more = dataclasses.replace(megadrive, sources=(*megadrive.sources, "megadrive's own gamelist"))
        assert alike([genesis, more], CANDIDATES)
        resolution = installation.emulators_for(MEGA_DRIVE).resolution
        merged = shared_answer.shared([genesis, more], CANDIDATES, resolution)
        assert merged is not None
        assert merged.sources == (*dict.fromkeys((*genesis.sources, *more.sources)),)

    def test_a_shared_placement_states_each_candidates_provenance_in_candidate_order(self):
        # FileSet.provenance names the system the record was keyed by.
        installation = _retrodeck(
            (PICODRIVE, GPGX),
            (PICODRIVE, GPGX),
            {RETRODECK_CFG: 'libretro_directory = "/app/cores"\n'},
            cores={f"{CORES}/picodrive_libretro.so": {"library_name": "PicoDrive"}},
        )
        content = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/ROMs/megadrive/Game.md"
        shared = installation.savefile_location(core_so="picodrive_libretro.so", content_path=content, system=MEGA_DRIVE)
        alone = [
            installation.savefile_location(core_so="picodrive_libretro.so", content_path=content, system=system)
            for system in CANDIDATES
        ]
        assert not isinstance(shared, atlas.Unresolved)
        provenances = [placement.file_set.provenance for placement in alone if not isinstance(placement, atlas.Unresolved)]
        assert len(set(provenances)) == 2, "the candidates' placements state one provenance"
        assert shared.file_set.provenance == "; ".join(provenances)
