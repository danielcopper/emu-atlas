"""Tests for atlas.platforms — the crosswalk loader and the pure lookups.

Two things are held down: the loader refuses a table it cannot place (every
refusal is an identity a question could otherwise answer out of), and the
matching rules are exactly the documented ones — numeric id or slug for IGDB,
the database name verbatim for libretro, digits for the two scraper columns.
The machine-qualified half lives on the handles and is proven by the vectors.
"""

from __future__ import annotations

import json

import pytest

from atlas.platforms import (
    KNOWN_PLATFORM_VOCABULARIES,
    PLATFORM_CROSSWALK_SCHEMA,
    load_platform_crosswalk,
    known_platforms,
    platform_identities,
    platforms_for,
)


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
        with pytest.raises(ValueError, match="non-empty"):
            load_platform_crosswalk(json.dumps({"schema": 1, "platforms": {}}))

    def test_a_row_with_stray_keys_is_rejected(self):
        with pytest.raises(ValueError, match="exactly"):
            load_platform_crosswalk(_document(gba=_row(extra=1)))

    def test_an_igdb_identity_without_a_numeric_id_is_rejected(self):
        # The numeric id is the stable key — a string there would let a
        # drifted slug pose as one.
        row = _row(igdb=[{"id": "24", "slug": "gba", "name": "Game Boy Advance"}])
        with pytest.raises(ValueError, match="integer"):
            load_platform_crosswalk(_document(gba=row))

    def test_a_repeated_igdb_id_is_rejected(self):
        row = _row(
            igdb=[
                {"id": 24, "slug": "gba", "name": "Game Boy Advance"},
                {"id": 24, "slug": "gba-again", "name": "Game Boy Advance"},
            ]
        )
        with pytest.raises(ValueError, match="repeats"):
            load_platform_crosswalk(_document(gba=row))

    def test_a_scraper_id_that_is_not_an_integer_is_rejected(self):
        with pytest.raises(ValueError, match="integer or null"):
            load_platform_crosswalk(_document(gba=_row(screenscraper="12")))

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
        assert engines is not None and engines.igdb == ()

    def test_the_vocabularies_are_the_documented_four(self):
        assert KNOWN_PLATFORM_VOCABULARIES == ("igdb", "libretro", "screenscraper", "thegamesdb")
