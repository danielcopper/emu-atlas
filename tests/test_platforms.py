"""Tests for atlas.platforms — the crosswalk and per-system loaders and the pure lookups.

Two things are held down: the loader refuses a table it cannot place (every
refusal is an identity a question could otherwise answer out of), and the
matching rules are exactly the documented ones — numeric id or slug for IGDB,
the database name verbatim for libretro, digits for the two scraper columns.
The machine-qualified half lives on the handles and is proven by the vectors —
except the value boundary, which refuses instead of answering and so has no
vector to live in: a handle asks it here, where the consumer meets it.
"""

from __future__ import annotations

import json

import pytest

import atlas
import atlas.platforms
from atlas.machine import FixtureMachine
from atlas.platforms import (
    KNOWN_PLATFORM_VOCABULARIES,
    PLATFORM_CROSSWALK_SCHEMA,
    SYSTEM_PLATFORM_IDS_SCHEMA,
    load_platform_crosswalk,
    load_system_platform_ids,
    known_platforms,
    platform_identities,
    platforms_for,
    systems_for,
)
from scripts import generate_platform_ids_by_system as generator


def _row(**overrides):
    row = {
        "comment": "Nintendo Game Boy Advance",
        "igdb": [{"id": 24, "slug": "gba", "name": "Game Boy Advance"}],
        "libretro": ["Nintendo - Game Boy Advance"],
        "screenscraper": 12,
        "thegamesdb": 5,
        **overrides,
    }
    return row


def _document(**platforms) -> str:
    return json.dumps(
        {
            "schema": PLATFORM_CROSSWALK_SCHEMA,
            "spec": "spec",
            "description": "description",
            "sources": {},
            "platforms": platforms or {"gba": _row()},
        }
    )


class TestTheLoaderRefusesATableItCannotPlace:
    def test_an_unknown_schema_is_rejected(self):
        with pytest.raises(ValueError, match="schema"):
            load_platform_crosswalk('{"schema": 99}')

    def test_an_empty_table_is_rejected(self):
        document = json.dumps({"schema": 1, "platforms": {}})
        with pytest.raises(ValueError, match="non-empty"):
            load_platform_crosswalk(document)

    def test_a_row_with_stray_keys_is_rejected(self):
        document = _document(gba=_row(extra=1))
        with pytest.raises(ValueError, match="exactly"):
            load_platform_crosswalk(document)

    def test_an_igdb_identity_without_a_numeric_id_is_rejected(self):
        # The numeric id is the stable key — a string there would let a
        # drifted slug pose as one.
        document = _document(gba=_row(igdb=[{"id": "24", "slug": "gba", "name": "Game Boy Advance"}]))
        with pytest.raises(ValueError, match="integer"):
            load_platform_crosswalk(document)

    def test_a_repeated_igdb_id_is_rejected(self):
        document = _document(
            gba=_row(
                igdb=[
                    {"id": 24, "slug": "gba", "name": "Game Boy Advance"},
                    {"id": 24, "slug": "gba-again", "name": "Game Boy Advance"},
                ]
            )
        )
        with pytest.raises(ValueError, match="repeats"):
            load_platform_crosswalk(document)

    def test_a_scraper_id_that_is_not_an_integer_is_rejected(self):
        document = _document(gba=_row(screenscraper="12"))
        with pytest.raises(ValueError, match="integer or null"):
            load_platform_crosswalk(document)

    def test_a_wellformed_table_loads(self):
        table = load_platform_crosswalk(_document())
        assert table["gba"].igdb[0].id == 24


class TestTheLookupsSpeakTheDocumentedRules:
    def test_the_packaged_table_loads_and_is_sorted(self):
        platforms = known_platforms()
        assert list(platforms) == sorted(platforms)
        assert "gba" in platforms

    def test_an_igdb_numeric_id_matches(self):
        assert "gba" in platforms_for("igdb", "24")

    def test_an_igdb_slug_matches_case_insensitively(self):
        # Slugs drift and are conveniences; the numeric id is the key. Case
        # folding costs nothing because no two slugs differ by case alone.
        assert platforms_for("igdb", "GBA") == platforms_for("igdb", "gba")

    def test_a_libretro_database_name_matches_verbatim(self):
        assert "gba" in platforms_for("libretro", "Nintendo - Game Boy Advance")

    def test_a_scraper_id_matches_as_digits(self):
        assert "gba" in platforms_for("screenscraper", "12")
        assert "gba" in platforms_for("thegamesdb", "5")

    def test_a_value_nothing_carries_answers_empty(self):
        assert platforms_for("igdb", "not-a-platform") == ()

    def test_an_unknown_vocabulary_raises(self):
        # The set is atlas's own and closed — a typo here is a caller bug, not
        # a machine state, and answering () would read as "no platform".
        with pytest.raises(ValueError, match="vocabulary"):
            platforms_for("romm", "gba")

    def test_one_igdb_id_may_land_on_several_platforms(self):
        # IGDB files the whole 8-bit family under one platform; ES-DE keeps
        # atari800 and atarixe apart. Both answer, and the consumer sees both.
        assert set(platforms_for("igdb", "65")) == {"atari800", "atarixe"}

    def test_an_unknown_tag_is_none_and_an_empty_row_is_not(self):
        assert platform_identities("selfmade") is None
        engines = platform_identities("mugen")
        assert engines is not None
        assert engines.igdb == ()

    def test_the_vocabularies_are_the_documented_four(self):
        assert KNOWN_PLATFORM_VOCABULARIES == ("igdb", "libretro", "screenscraper", "thegamesdb")


class TestTheValueBoundaryRefusesRatherThanCoerces:
    """Issue #338: the natural call of a consumer holding a numeric id.

    A server product publishes its platforms as IGDB's numeric ids, so
    ``systems_for_platform("igdb", 7)`` is the call its client reaches for.
    Only the type is refused, and it is refused by name: ``None``, ``True`` and
    ``7.0`` carry no key at all, so a stated refusal beats the silently empty
    answer a ``str()`` would produce. The empty string is the other side — a
    value no platform carries, which is a question with an answer.
    """

    def test_a_numeric_id_passed_as_an_int_is_refused(self):
        with pytest.raises(ValueError, match="expected a string"):
            platforms_for("igdb", 7)  # type: ignore[arg-type]

    def test_the_refusal_names_the_vocabulary_and_the_value(self):
        # The message is the fix instruction. Without the vocabulary and the
        # value in it, the consumer is back to probing for what was wanted.
        with pytest.raises(ValueError) as refusal:
            platforms_for("igdb", 7)  # type: ignore[arg-type]
        assert "'igdb'" in str(refusal.value)
        assert "got 7" in str(refusal.value)

    def test_a_bool_is_no_more_a_string_than_an_int(self):
        with pytest.raises(ValueError, match="expected a string"):
            platforms_for("igdb", True)  # type: ignore[arg-type]

    def test_none_is_refused_rather_than_read_as_no_value(self):
        with pytest.raises(ValueError, match="expected a string"):
            platforms_for("igdb", None)  # type: ignore[arg-type]

    def test_a_float_is_refused_though_its_str_looks_numeric(self):
        # The case a coercion would get quietly wrong: str(7.0) is "7.0", a
        # shape no crosswalk row carries, so it would answer nothing found.
        with pytest.raises(ValueError, match="expected a string"):
            platforms_for("igdb", 7.0)  # type: ignore[arg-type]

    def test_the_decimal_string_of_a_numeric_id_is_the_key(self):
        # The rule the first consumer had to find by probing: hold IGDB's
        # numeric id, ask with str(id).
        igdb_id = 24
        assert "gba" in platforms_for("igdb", str(igdb_id))

    def test_an_empty_value_stays_a_question_with_an_answer(self):
        # Only the type is refused. "" is not a caller bug, it is a value no
        # platform carries — and that has an answer already.
        assert platforms_for("igdb", "") == ()

    def test_a_whitespace_value_stays_a_question_with_an_answer(self):
        assert platforms_for("libretro", "   ") == ()

    def test_the_refusal_carries_through_an_installation(self):
        # Where the consumer actually stands: the handle's question, not the
        # module's. Nothing about the machine changes the boundary's answer.
        handle = atlas.RetroDeck("/home/deck", FixtureMachine({}))
        with pytest.raises(ValueError, match="expected a string"):
            handle.systems_for_platform("igdb", 7)  # type: ignore[arg-type]


def _id(vocabulary="screenscraper", value="6", **overrides):
    return {"vocabulary": vocabulary, "value": value, "source": "a pinned line", **overrides}


def _system_row(*ids, **overrides):
    return {
        "comment": "Capcom Play System I",
        "system": "es_systems.xml:475",
        "ids": list(ids) or [_id()],
        **overrides,
    }


def _system_document(**systems) -> str:
    return json.dumps(
        {
            "schema": SYSTEM_PLATFORM_IDS_SCHEMA,
            "spec": "spec",
            "description": "description",
            "sources": {},
            "systems": systems or {"cps1": _system_row()},
        }
    )


class TestThePerSystemLoaderRefusesATableItCannotPlace:
    """Issue #584: every refusal is an answer a malformed row would otherwise give."""

    def test_an_unknown_schema_is_rejected(self):
        with pytest.raises(ValueError, match="schema"):
            load_system_platform_ids('{"schema": 99}')

    def test_an_empty_table_is_rejected(self):
        document = json.dumps({"schema": 1, "systems": {}})
        with pytest.raises(ValueError, match="non-empty"):
            load_system_platform_ids(document)

    def test_a_system_the_vocabulary_does_not_know_is_rejected(self):
        # model3 is real upstream but not an atlas id: the vocabulary list
        # (RetroDECK 0.10.9b's catalogue) has no model3, and RetroDECK's
        # shipped es_systems.xml carries it only commented out. Its absent
        # match would have no tags to carry, and the answer would name a non-id.
        document = _system_document(model3=_system_row(_id(value="55")))
        with pytest.raises(ValueError, match="vocabulary"):
            load_system_platform_ids(document)

    def test_a_row_with_stray_keys_is_rejected(self):
        document = _system_document(cps1=_system_row(extra=1))
        with pytest.raises(ValueError, match="exactly comment, system and ids"):
            load_system_platform_ids(document)

    def test_a_row_without_ids_is_rejected(self):
        document = _system_document(cps1={**_system_row(), "ids": []})
        with pytest.raises(ValueError, match="non-empty list"):
            load_system_platform_ids(document)

    def test_an_id_with_stray_keys_is_rejected(self):
        document = _system_document(cps1=_system_row(_id(extra=1)))
        with pytest.raises(ValueError, match="exactly vocabulary, value and source"):
            load_system_platform_ids(document)

    def test_an_id_without_a_source_is_rejected(self):
        document = _system_document(cps1=_system_row(_id(source="")))
        with pytest.raises(ValueError, match="source"):
            load_system_platform_ids(document)

    def test_an_unknown_vocabulary_is_rejected(self):
        document = _system_document(cps1=_system_row(_id(vocabulary="romm")))
        with pytest.raises(ValueError, match="unknown vocabulary"):
            load_system_platform_ids(document)

    @pytest.mark.parametrize("value", ["CPS1", "06", "6.0", "٦", "²"])
    def test_a_numeric_id_that_is_not_its_decimal_digits_is_rejected(self, value):
        # A question asks with str(id): a row spelled any other way is a row
        # no question reaches, which is a silent gap rather than an answer.
        # "²" is a digit to str.isdigit that int() cannot parse: only the
        # ASCII check turns it into this refusal rather than int()'s own.
        document = _system_document(cps1=_system_row(_id(value=value)))
        with pytest.raises(ValueError, match="decimal digits"):
            load_system_platform_ids(document)

    def test_a_value_with_surrounding_whitespace_is_rejected(self):
        # The lookup strips what it is asked, so a padded row never matches.
        document = _system_document(doom=_system_row(_id(vocabulary="libretro", value="DOOM ")))
        with pytest.raises(ValueError, match="whitespace"):
            load_system_platform_ids(document)

    def test_one_id_standing_for_two_systems_is_rejected(self):
        # The table's whole claim is "this id stands for one system alone".
        document = _system_document(cps1=_system_row(), cps2=_system_row())
        with pytest.raises(ValueError, match="stands for both"):
            load_system_platform_ids(document)

    def test_a_wellformed_table_loads(self):
        assert load_system_platform_ids(_system_document()) == {("screenscraper", "6"): "cps1"}


class TestThePerSystemLookupAnswersOneSystem:
    # The ids issue #584 decided, each with the one system it stands for.
    DECIDED = (
        ("screenscraper", "6", "cps1"),
        ("screenscraper", "7", "cps2"),
        ("screenscraper", "8", "cps3"),
        ("screenscraper", "54", "model2"),
        ("screenscraper", "69", "stv"),
        ("screenscraper", "290", "doom"),
        ("libretro", "ScummVM", "scummvm"),
        ("libretro", "DOOM", "doom"),
    )

    @pytest.mark.parametrize(("vocabulary", "value", "system"), DECIDED)
    def test_each_decided_id_answers_its_system(self, vocabulary, value, system):
        assert systems_for(vocabulary, value) == (system,)

    def test_the_packaged_table_carries_exactly_the_decided_ids(self):
        table = load_system_platform_ids()
        assert table == {(vocabulary, value): system for vocabulary, value, system in self.DECIDED}

    def test_the_packaged_table_is_what_the_generator_joins(self):
        # The data file is generated; a hand edit beside HAND_JOIN, or a join
        # changed without regenerating, shows here rather than in review.
        joined = {
            (vocabulary, system)
            for system, join in generator.HAND_JOIN.items()
            for vocabulary in join
        }
        assert {(v, s) for (v, _), s in load_system_platform_ids().items()} == joined

    def test_the_generic_arcade_id_is_left_to_the_crosswalk(self):
        assert systems_for("screenscraper", "75") == ()
        assert "arcade" in platforms_for("screenscraper", "75")

    def test_an_igdb_keyword_id_is_in_neither_table(self):
        # RomM's ScummVM record carries 50501, an IGDB keyword id rather than a
        # platform id (igdb.py:2017 @ 5.3.1).
        assert systems_for("igdb", "50501") == ()
        assert platforms_for("igdb", "50501") == ()

    def test_the_asked_value_is_stripped_as_the_crosswalk_strips_it(self):
        assert systems_for("screenscraper", " 6 ") == ("cps1",)

    def test_an_unknown_vocabulary_raises(self):
        with pytest.raises(ValueError, match="vocabulary"):
            systems_for("romm", "6")

    def test_a_non_string_value_is_refused(self):
        with pytest.raises(ValueError, match="expected a string"):
            systems_for("screenscraper", 6)  # type: ignore[arg-type]


class TestAListedIdWinsOverTheCrosswalk:
    """Decision 2 of issue #584, held where no packaged id overlaps yet."""

    def test_the_system_table_answers_and_the_crosswalk_is_not_asked(self, monkeypatch):
        monkeypatch.setattr(
            atlas.platforms, "_PACKAGED_SYSTEM_IDS", {("screenscraper", "75"): "cps1"}
        )
        answer = atlas.RetroDeck("/home/deck", FixtureMachine({})).systems_for_platform(
            "screenscraper", "75"
        )
        assert (answer.systems, answer.platforms) == (("cps1",), ())
        assert [m.system for m in answer.matches] == ["cps1"]
