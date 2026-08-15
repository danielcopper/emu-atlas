"""The save-memory records: the loader's refusals, and what the packaged file must hold."""

from __future__ import annotations

import json

import pytest

from atlas import placement
from atlas.oddities import load_oddities
from atlas.save_memory import (
    MEMORY_RTC,
    MEMORY_SAVE_RAM,
    MEMORY_TYPE_EXTENSIONS,
    SAVE_MEMORY_SCHEMA,
    load_save_memory,
    lookup_save_memory,
)
from atlas.systems import known_systems


def _doc(systems=None, **record):
    entry = {
        "identifiers": {"library_name": ["Example"]},
        "systems": systems
        if systems is not None
        else {
            "gba": {
                "memory_types": ["save_ram"],
                "verified_core": "1.0 abc1234",
                "citation": "[V] libretro.c:1-2 at example abc1234",
            }
        },
        "provenance": {"source": "read at the revision the binary names"},
    }
    entry.update(record)
    return json.dumps({"schema": SAVE_MEMORY_SCHEMA, "cores": {"example": entry}})


class TestLoader:
    def test_a_record_carries_its_systems_and_derives_its_so_name(self):
        (record,) = load_save_memory(_doc())
        assert record.key == "example"
        assert record.so_name == "example_libretro.so"
        assert record.library_names == ("Example",)
        assert sorted(record.systems) == ["gba"]

    def test_an_unsupported_schema_is_refused_rather_than_read(self):
        text = json.dumps({"schema": SAVE_MEMORY_SCHEMA + 1, "cores": {}})
        with pytest.raises(ValueError, match="unsupported schema"):
            load_save_memory(text)

    def test_a_memory_id_that_reaches_no_file_is_refused(self):
        systems = {
            "gba": {
                "memory_types": ["system_ram"],
                "verified_core": "1.0 abc1234",
                "citation": "[V] libretro.c:1",
            }
        }
        text = _doc(systems)
        with pytest.raises(ValueError, match="not memory ids that reach a file"):
            load_save_memory(text)

    def test_an_empty_memory_list_is_the_commonest_claim_not_a_gap(self):
        """Most cores fill no id at all; recording that is an answer, not silence."""
        systems = {
            "gba": {
                "memory_types": [],
                "verified_core": "1.0 abc1234",
                "citation": "[V] libretro.c:1",
            }
        }
        (record,) = load_save_memory(_doc(systems))
        entry = record.for_system("gba")
        assert entry is not None
        assert entry.memory_types == ()
        assert entry.file_templates == ()
        assert entry.frontend_writes_nothing is True

    def test_a_filled_id_is_not_read_as_writing_nothing(self):
        (record,) = load_save_memory(_doc())
        entry = record.for_system("gba")
        assert entry is not None
        assert entry.frontend_writes_nothing is False

    def test_a_system_the_record_leaves_out_stays_silence(self):
        """The other half of the distinction: absent is 'not looked at', empty is 'none'."""
        (record,) = load_save_memory(_doc())
        assert record.for_system("snes") is None

    def test_a_repeated_memory_id_is_refused(self):
        systems = {
            "gba": {
                "memory_types": ["save_ram", "save_ram"],
                "verified_core": "1.0 abc1234",
                "citation": "[V] libretro.c:1",
            }
        }
        text = _doc(systems)
        with pytest.raises(ValueError, match="listed twice"):
            load_save_memory(text)

    def test_the_order_is_the_frontends_own_not_the_records(self):
        """Two records stating one fact must not produce two different file sets."""
        systems = {
            "gba": {
                "memory_types": ["rtc", "save_ram"],
                "verified_core": "1.0 abc1234",
                "citation": "[V] libretro.c:1",
            }
        }
        (record,) = load_save_memory(_doc(systems))
        entry = record.for_system("gba")
        assert entry is not None
        assert entry.memory_types == (MEMORY_SAVE_RAM, MEMORY_RTC)
        assert entry.file_templates == ("<rom_stem>.srm", "<rom_stem>.rtc")

    def test_a_system_outside_atlass_vocabulary_is_refused(self):
        systems = {
            "gameboy-advance": {
                "memory_types": ["save_ram"],
                "verified_core": "1.0 abc1234",
                "citation": "[V] libretro.c:1",
            }
        }
        text = _doc(systems)
        with pytest.raises(ValueError, match="not atlas system ids"):
            load_save_memory(text)

    def test_a_record_without_systems_is_refused(self):
        text = _doc(systems={})
        with pytest.raises(ValueError, match="non-empty 'systems' object"):
            load_save_memory(text)

    def test_a_restated_so_name_is_refused_because_it_could_only_disagree(self):
        text = _doc(identifiers={"so": "example_libretro.so"})
        with pytest.raises(ValueError, match="identifiers.so is derived"):
            load_save_memory(text)

    def test_a_claim_without_provenance_is_refused(self):
        text = _doc(provenance={})
        with pytest.raises(ValueError, match="provenance.source"):
            load_save_memory(text)

    @pytest.mark.parametrize("missing", ["verified_core", "citation"])
    def test_a_system_entry_needs_both_its_pins(self, missing: str):
        entry = {
            "memory_types": ["save_ram"],
            "verified_core": "1.0 abc1234",
            "citation": "[V] libretro.c:1",
        }
        del entry[missing]
        text = _doc({"gba": entry})
        with pytest.raises(ValueError, match="expected"):
            load_save_memory(text)


class TestLookup:
    def test_a_record_is_found_from_either_side(self):
        by_so = lookup_save_memory(so_basename="mgba_libretro.so", library_name=None)
        by_name = lookup_save_memory(so_basename=None, library_name="mGBA")
        assert by_so is not None
        assert by_name is not None
        assert by_so.key == "mgba"
        assert by_name.key == "mgba"

    def test_a_core_with_no_record_is_none_rather_than_an_empty_one(self):
        # bsnes is shipped and catalogued, and is the default for no system — the records
        # cover the default cores, so it stays outside them however far the reading gets.
        assert lookup_save_memory(so_basename="bsnes_libretro.so", library_name="bsnes") is None

    def test_an_unnamed_system_narrows_nothing_where_the_systems_disagree(self):
        # mGBA is the record the (core, system) key was designed around: the
        # Game Boy branch answers the clock id and the Game Boy Advance one does
        # not, so there is no one answer to give without knowing which it is.
        record = lookup_save_memory(so_basename="mgba_libretro.so", library_name=None)
        assert record is not None
        assert record.for_system(None) is None
        assert record.for_system("n64") is None

    def test_an_unnamed_system_answers_where_every_recorded_system_agrees(self):
        # Gambatte covers gb and gbc and writes the same two files for both, so
        # whichever of them the content is, the answer is the same — the case
        # an arrangement without a frontend catalogue is always in.
        record = lookup_save_memory(so_basename="gambatte_libretro.so", library_name=None)
        assert record is not None
        entry = record.for_system(None)
        named = record.for_system("gb")
        assert entry is not None
        assert named is not None
        assert entry.memory_types == named.memory_types


class TestPackagedRecords:
    def test_no_core_carries_both_a_card_and_a_record(self):
        """A card wins, so a record beside one is knowledge nothing can read.

        Worse than dead: a carded core is a *deviating* core, and the record
        would state the file names of a save the card has moved elsewhere —
        right about the names, wrong about the save. `atlas/data/README.md`
        says so; this is what keeps the two files from drifting into saying it
        differently.
        """
        carded = {card.key for card in load_oddities()}
        recorded = {record.key for record in load_save_memory()}
        assert carded & recorded == set()

    def test_how_many_records_answer_without_a_system_is_pinned(self):
        """The unanimity route's reach, as a number rather than as a hope.

        Answering an unnamed system rests on a property of today's data — every
        system a record covers writing the same files — not on a structural
        guarantee. A record added later that disagrees silently shrinks what
        atlas can say on a catalogue-less arrangement, and nothing else would
        notice. So the count is pinned: a change here is a deliberate edit with
        a reason, not a side effect.
        """
        records = list(load_save_memory())
        answers = [record for record in records if record.unanimous() is not None]
        silent = sorted(record.key for record in records if record.unanimous() is None)
        assert len(records) == 79
        assert len(answers) == 77
        # Both switch on the loaded content's platform before answering, which
        # is the whole reason the records are keyed by system: mGBA answers a
        # Game Boy cartridge's clock and a Game Boy Advance cartridge's not at
        # all, and VBA-M's function branches on the image type first.
        assert silent == ["mgba", "vbam"]

    def test_every_recorded_system_is_an_atlas_system_id(self):
        vocabulary = set(known_systems())
        for record in load_save_memory():
            assert set(record.systems) <= vocabulary, record.key

    def test_every_entry_names_the_build_it_was_read_at(self):
        for record in load_save_memory():
            for system, entry in record.systems.items():
                assert entry.verified_core.strip(), f"{record.key}/{system}"
                assert entry.citation.strip(), f"{record.key}/{system}"

    def test_every_stated_file_is_one_of_the_two_the_frontend_writes(self):
        """The record states which ids a core fills; the extension is never a record's to invent."""
        extensions = set(MEMORY_TYPE_EXTENSIONS.values())
        for record in load_save_memory():
            for entry in record.systems.values():
                for name in entry.file_templates:
                    assert name.startswith("<rom_stem>")
                    assert name[len("<rom_stem>") :] in extensions

    def test_every_template_uses_the_hole_the_resolver_substitutes(self):
        """A second spelling would not fail — it would ship ``<rom_stem>`` as a save's name.

        The substitution runs against :mod:`atlas.placement`'s own token, and
        ``file_set_holes`` knows only ``<save_id>``, so a divergent spelling
        would reach a caller inside a ``declared`` answer with nothing in
        ``needs`` to mark it.
        """
        for record in load_save_memory():
            for entry in record.systems.values():
                for name in entry.file_templates:
                    assert placement.TEMPLATE_ROM_STEM in name, name
                    assert "<" not in name.replace(placement.TEMPLATE_ROM_STEM, ""), name

    def test_mgba_states_the_clock_for_the_handhelds_that_have_one(self):
        """The record that motivates the whole per-system key, asserted as such."""
        record = lookup_save_memory(so_basename="mgba_libretro.so", library_name=None)
        assert record is not None
        gba = record.for_system("gba")
        gb = record.for_system("gb")
        assert gba is not None
        assert gb is not None
        assert gba.memory_types == (MEMORY_SAVE_RAM,)
        assert gb.memory_types == (MEMORY_SAVE_RAM, MEMORY_RTC)
