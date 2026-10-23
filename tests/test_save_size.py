"""Whether a save file keeps its size (#681): the field, the packaged claims, the guards.

The resolver's half — the standard rule's groups, ``save_file_compression``
read live, the trailing-fill caveat, a card's frontend-written file observed
and declared, the folder saves — is held by the vectors in
``vectors/machines/save-file-sets.json`` and ``standalone-saves.json``. What is
here is everything a vector cannot reach: the types refusing a claim that is
not one, the loaders refusing a table that half-states one, and the packaged
data stating the values the issue decided.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from atlas.oddities import load_oddities
from atlas.placement import (
    GRANULARITY_PER_GAME_FILE,
    ROLE_BATTERY,
    SIZE_FILL_FF,
    Caveat,
    CAVEAT_SIZE_FIXED_EXCEPT_TRAILING_FILL,
    FileGroup,
    SizeFact,
)
from atlas.save_memory import (
    MEMORY_RTC,
    MEMORY_SAVE_RAM,
    SAVE_MEMORY_SCHEMA,
    load_save_memory,
)
from scripts.validate_vectors import VectorError, validate_machines_vector
from tests.test_validate_vectors import (
    _base_placement,  # pyright: ignore[reportPrivateUsage] - the validator suite's own shapes
    _file_set,  # pyright: ignore[reportPrivateUsage]
    _group,  # pyright: ignore[reportPrivateUsage]
)

CITATION = "[V] libretro.c:1-2 at example abc1234"


def _doc(systems: dict[str, Any]) -> str:
    entry = {
        "identifiers": {"library_name": ["Example"]},
        "systems": systems,
        "provenance": {"source": "read at the revision the binary names"},
    }
    return json.dumps({"schema": SAVE_MEMORY_SCHEMA, "cores": {"example": entry}})


def _entry(memory_types: list[str], size: dict[str, Any] | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "memory_types": memory_types,
        "verified_core": "1.0 abc1234",
        "citation": CITATION,
    }
    if size is not None:
        entry["size"] = size
    return entry


def _fact(fixed: bool | None, **extra: Any) -> dict[str, Any]:
    return {"fixed": fixed, "citation": CITATION, **extra}


class TestTheField:
    def test_a_group_states_true_false_or_nothing(self):
        for value in (True, False, None):
            group = FileGroup(
                dir="/saves",
                files=("a.srm",),
                granularity=GRANULARITY_PER_GAME_FILE,
                role=ROLE_BATTERY,
                size_fixed=value,
            )
            assert group.size_fixed is value

    def test_a_group_left_unsaid_is_not_established(self):
        group = FileGroup(
            dir="/saves", files=("a.srm",), granularity=GRANULARITY_PER_GAME_FILE, role=ROLE_BATTERY
        )
        assert group.size_fixed is None

    @pytest.mark.parametrize("value", ["false", 0, 1])
    def test_a_group_refuses_a_claim_that_is_not_a_boolean(self, value):
        # bool("false") is True; a truthy string would be read as "fixed".
        with pytest.raises(ValueError, match="size_fixed must be true, false or None"):
            FileGroup(
                dir="/saves",
                files=("a.srm",),
                granularity=GRANULARITY_PER_GAME_FILE,
                role=ROLE_BATTERY,
                size_fixed=value,  # pyright: ignore[reportArgumentType] - the refusal under test
            )


class TestTheFact:
    def test_a_fill_rides_only_on_a_fixed_size(self):
        assert SizeFact(fixed=True, citation=CITATION, fill=SIZE_FILL_FF).fill == SIZE_FILL_FF
        for fixed in (False, None):
            with pytest.raises(ValueError, match="rides only on fixed true"):
                SizeFact(fixed=fixed, citation=CITATION, fill=SIZE_FILL_FF)

    def test_a_fill_outside_the_vocabulary_is_refused(self):
        with pytest.raises(ValueError, match="fill must be one of"):
            SizeFact(fixed=True, citation=CITATION, fill="0xaa")

    def test_a_claim_without_its_citation_is_refused(self):
        with pytest.raises(ValueError, match="carries the citation"):
            SizeFact(fixed=True, citation="")

    def test_the_fill_caveat_refuses_a_byte_outside_the_vocabulary(self):
        with pytest.raises(ValueError, match="fill must be one of"):
            Caveat(
                CAVEAT_SIZE_FIXED_EXCEPT_TRAILING_FILL,
                "message",
                {"file": "a.srm", "fill": "0xaa", "citation": CITATION},
            )


class TestTheRecord:
    def test_a_claim_per_memory_type_is_read(self):
        size = {"save_ram": _fact(True), "rtc": _fact(None)}
        (record,) = load_save_memory(_doc({"gb": _entry(["save_ram", "rtc"], size)}))
        assert record.size("gb", MEMORY_SAVE_RAM) == SizeFact(fixed=True, citation=CITATION)
        assert record.size("gb", MEMORY_RTC) == SizeFact(fixed=None, citation=CITATION)

    def test_an_entry_nobody_read_the_size_of_answers_nothing(self):
        (record,) = load_save_memory(_doc({"gb": _entry(["save_ram"])}))
        assert record.size("gb", MEMORY_SAVE_RAM) is None

    def test_half_a_size_block_is_refused(self):
        text = _doc({"gb": _entry(["save_ram", "rtc"], {"save_ram": _fact(True)})})
        with pytest.raises(ValueError, match="one claim per memory type filled"):
            load_save_memory(text)

    def test_a_claim_for_a_file_never_written_is_refused(self):
        text = _doc({"gb": _entry([], {"save_ram": _fact(True)})})
        with pytest.raises(ValueError, match="one claim per memory type filled"):
            load_save_memory(text)

    def test_a_string_is_not_read_as_a_boolean(self):
        text = _doc({"gb": _entry(["save_ram"], {"save_ram": _fact("false")})})  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="expected true, false or null"):
            load_save_memory(text)

    def test_a_fill_beside_a_size_that_is_not_fixed_is_refused(self):
        text = _doc({"gb": _entry(["save_ram"], {"save_ram": _fact(None, fill="0xff")})})
        with pytest.raises(ValueError, match="exception to a fixed size"):
            load_save_memory(text)

    def test_unnamed_the_systems_have_to_agree_on_the_claim(self):
        agreeing = _doc(
            {
                "gb": _entry(["save_ram"], {"save_ram": _fact(True)}),
                "gbc": _entry(["save_ram"], {"save_ram": _fact(True)}),
            }
        )
        (record,) = load_save_memory(agreeing)
        fact = record.size(None, MEMORY_SAVE_RAM)
        assert fact is not None
        assert fact.fixed is True

        differing = _doc(
            {
                "megadrive": _entry(["save_ram"], {"save_ram": _fact(True)}),
                "segacd": _entry(["save_ram"], {"save_ram": _fact(None)}),
            }
        )
        (record,) = load_save_memory(differing)
        assert record.size(None, MEMORY_SAVE_RAM) is None
        # The files still agree, so the answer keeps naming them.
        assert record.for_system(None) is not None


class TestThePackagedClaims:
    """D3 coverage and the values D6 decided, read off the shipped tables."""

    def test_every_recorded_file_states_whether_its_size_is_fixed(self):
        unstated = [
            (record.key, system)
            for record in load_save_memory()
            for system, entry in record.systems.items()
            if entry.memory_types and set(entry.sizes) != set(entry.memory_types)
        ]
        assert unstated == []

    @pytest.mark.parametrize(
        ("core", "system", "memory", "fixed", "fill"),
        [
            ("mgba", "gba", MEMORY_SAVE_RAM, True, SIZE_FILL_FF),
            ("mgba", "gb", MEMORY_SAVE_RAM, True, None),
            ("mgba", "gb", MEMORY_RTC, True, None),
            ("vbam", "gba", MEMORY_SAVE_RAM, None, None),
            ("melondsds", "nds", MEMORY_SAVE_RAM, None, None),
            ("picodrive", "megacd", MEMORY_SAVE_RAM, None, None),
            ("picodrive", "megadrive", MEMORY_SAVE_RAM, True, None),
            ("jollycv", "crvision", MEMORY_SAVE_RAM, None, None),
            ("gambatte", "gb", MEMORY_RTC, True, None),
        ],
    )
    def test_the_decided_values(self, core, system, memory, fixed, fill):
        (record,) = [record for record in load_save_memory() if record.key == core]
        fact = record.size(system, memory)
        assert fact is not None
        assert (fact.fixed, fact.fill) == (fixed, fill)

    def test_the_cards_state_the_file_they_hand_the_frontend(self):
        cards = {card.key: card for card in load_oddities()}
        stated: dict[str, set[tuple[bool | None, str | None]]] = {}
        for key, card in cards.items():
            for mode in card.modes.values():
                for group in mode.groups:
                    if group.frontend is not None:
                        assert group.files == ("<rom_stem>.srm",)
                        assert group.frontend.memory == MEMORY_SAVE_RAM
                        stated.setdefault(key, set()).add(
                            (group.frontend.size.fixed, group.frontend.size.fill)
                        )
        assert stated == {
            "pcsx_rearmed": {(True, None)},
            "mednafen_psx": {(True, None)},
            "mednafen_psx_hw": {(True, None)},
            "swanstation": {(True, None)},
            "genesis_plus_gx": {(True, SIZE_FILL_FF)},
        }


def _card_table(group: dict[str, Any]) -> str:
    return json.dumps(
        {
            "schema": 1,
            "cores": {
                "example": {
                    "identifiers": {"library_name": ["Example"]},
                    "saves": {"modes": {"always": {"root": "savefile_directory", "groups": [group]}}},
                    "provenance": {"source": "[V] a citation"},
                }
            },
        }
    )


def _frontend_group(files: list[str], memory: str = "save_ram") -> dict[str, Any]:
    return {
        "files": files,
        "granularity": "per-game-file",
        "role": "battery",
        "written_by_frontend": {"memory": memory, "size": _fact(True)},
    }


class TestTheCardBlock:
    def test_the_frontends_own_name_is_accepted(self):
        (card,) = load_oddities(_card_table(_frontend_group(["<rom_stem>.srm"])))
        (group,) = card.modes["always"].groups
        assert group.frontend is not None
        assert group.frontend.size.fixed is True

    @pytest.mark.parametrize(
        "files",
        [["<rom_stem>.rtc"], ["<rom_stem>.1.mcr"], ["<rom_stem>.srm", "<rom_stem>.rtc"]],
    )
    def test_a_file_the_frontend_does_not_name_is_refused(self, files):
        table = _card_table(_frontend_group(files))
        with pytest.raises(ValueError, match="holds exactly <rom_stem>.srm"):
            load_oddities(table)

    def test_a_memory_id_that_reaches_no_file_is_refused(self):
        table = _card_table(_frontend_group(["<rom_stem>.srm"], memory="system_ram"))
        with pytest.raises(ValueError, match="written_by_frontend.memory must be one of"):
            load_oddities(table)


class TestTheValidator:
    def test_a_group_whose_size_is_a_string_is_refused(self):
        group = {**_group(), "size_fixed": "true"}
        vector = _base_placement(
            file_set=_file_set(state="declared", files=["a.srm"], groups=[group])
        )
        with pytest.raises(VectorError, match="size_fixed must be true, false or null"):
            validate_machines_vector(vector)

    def test_a_fill_caveat_outside_the_vocabulary_is_refused(self):
        group = {**_group(), "size_fixed": True}
        vector = _base_placement(
            file_set=_file_set(state="declared", files=["a.srm"], groups=[group]),
            caveats=[
                {
                    "code": "size-fixed-except-trailing-fill",
                    "data": {"file": "a.srm", "fill": "0xaa", "citation": CITATION},
                }
            ],
        )
        with pytest.raises(VectorError, match="size-fixed-except-trailing-fill.fill must be one of"):
            validate_machines_vector(vector)
