"""Tests for atlas.systems — atlas's own system vocabulary.

Two things are held down here: the loader refuses a list it cannot place, and
the packaged ids really are the ids the shipped build declares. The second one
is what makes the set evidence rather than a claim — everything else in atlas
only ever asks whether a name is *in* the set, so an invented id appended to the
file would pass the whole suite without it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest

import atlas
from atlas.systems import (
    SYSTEM_IDS_SCHEMA,
    from_esde_system,
    known_systems,
    load_system_ids,
)

# The build the packaged id set is cited to, where RetroDECK deploys it. Its
# per-emulator knowledge lives in the Flatpak, not in RetroDECK's Git repository.
DEPLOYED_ES_SYSTEMS = Path(
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
    "/es-de/share/es-de/resources/systems/linux/es_systems.xml"
)


def _document(**overrides) -> str:
    document = {
        "schema": SYSTEM_IDS_SCHEMA,
        "spec": "spec",
        "description": "description",
        "sources": {},
        "systems": ["gb", "n64"],
        "platforms": {"gb": ["gb"], "n64": ["n64"]},
        **overrides,
    }
    return json.dumps(document)


def _shipped() -> dict[str, Any]:
    path = Path(atlas.__file__).parent / "data" / "system_ids.json"
    return json.loads(path.read_text(encoding="utf-8"))


class TestTheLoaderRefusesAListItCannotPlace:
    def test_an_unknown_schema_is_rejected(self):
        with pytest.raises(ValueError, match="schema"):
            load_system_ids('{"schema": 99}')

    def test_a_missing_schema_is_rejected(self):
        with pytest.raises(ValueError, match="schema"):
            load_system_ids('{"systems": ["gb"]}')

    def test_an_empty_id_set_is_rejected(self):
        # An empty vocabulary would answer "not an id" for every name on earth,
        # which reads exactly like a machine nothing is installed on.
        document = _document(systems=[])
        with pytest.raises(ValueError, match="non-empty list"):
            load_system_ids(document)

    def test_an_id_that_is_not_a_string_is_rejected(self):
        document = _document(systems=["gb", 64])
        with pytest.raises(ValueError, match="non-empty string"):
            load_system_ids(document)

    def test_a_duplicate_id_is_rejected(self):
        document = _document(systems=["gb", "gb"])
        with pytest.raises(ValueError, match="duplicate id"):
            load_system_ids(document)

    def test_a_wellformed_list_loads(self):
        assert load_system_ids(_document()) == frozenset({"gb", "n64"})

    def test_a_platform_column_not_covering_the_id_set_is_rejected(self):
        # The column exists to answer for exactly the vocabulary — a missing
        # system would silently turn "snapshot says X" into "snapshot is mute".
        document = _document(platforms={"gb": ["gb"]})
        with pytest.raises(ValueError, match="platforms"):
            load_system_ids(document)

    def test_a_platform_tag_that_is_not_a_string_is_rejected(self):
        document = _document(platforms={"gb": ["gb"], "n64": [64]})
        with pytest.raises(ValueError, match="non-empty string"):
            load_system_ids(document)

    def test_a_platform_value_that_is_not_a_list_is_rejected(self):
        document = _document(platforms={"gb": ["gb"], "n64": "n64"})
        with pytest.raises(ValueError, match="list of tags"):
            load_system_ids(document)


class TestTheCanonicalIds:
    def test_an_esde_name_is_its_own_id(self):
        assert from_esde_system("dreamcast") == "dreamcast"

    def test_a_name_the_vocabulary_does_not_declare_is_not_an_id(self):
        assert from_esde_system("nintendo-64") is None

    def test_a_plausible_looking_name_is_still_not_an_id(self):
        # The check a caller holding another vocabulary actually needs: the
        # spelling looks like a system and is not one, so a mapping that
        # targeted it would send every question of that platform nowhere.
        assert from_esde_system("sega-dreamcast") is None

    def test_the_ids_are_the_vocabulary_the_questions_take(self):
        # ES-DE's own names, so a system a live catalogue declares is an id.
        for system in ("gb", "n64", "psx", "dreamcast", "3do"):
            assert from_esde_system(system) == system

    def test_known_systems_is_sorted_and_deduplicated(self):
        ids = known_systems()
        assert list(ids) == sorted(set(ids))

    def test_known_systems_is_the_set_membership_is_tested_against(self):
        # One vocabulary, not two: the set a caller validates against is the
        # same one every question resolves through.
        assert all(from_esde_system(system) == system for system in known_systems())


class TestTheIdSetIsTheBuildsIdSet:
    """The ids are ES-DE's, so the file ES-DE ships is what says which they are.

    Without this the packaged list is a claim nobody checks: an invented id
    appended to it passes every other test here, because everything else only
    ever asks whether a name is *in* the set. Skipped where the deployment is
    absent — the Flatpak is not a build dependency, and the packaged list is
    what ships either way.
    """

    def _declared(self) -> set[str]:
        if not DEPLOYED_ES_SYSTEMS.exists():
            pytest.skip(f"RetroDECK's ES-DE is not deployed at {DEPLOYED_ES_SYSTEMS}")
        # ElementTree drops comments, which is the whole reason to parse rather
        # than grep: this file carries commented-out <system> blocks, and a
        # block that is commented out is not a declaration.
        root = ET.fromstring(DEPLOYED_ES_SYSTEMS.read_text(encoding="utf-8"))
        names = ((system.findtext("name") or "").strip() for system in root.findall("system"))
        return {name for name in names if name}

    def test_the_packaged_ids_are_exactly_what_the_build_declares(self):
        assert sorted(_shipped()["systems"]) == sorted(self._declared())

    def test_the_packaged_platform_tags_are_the_builds_tags(self):
        # Same guard, second column: the snapshot's platform tags must be what
        # the stated build's own <platform> text reads as — an edited tag would
        # otherwise pass every test, because everything else only looks tags up.
        # The read goes through the public parser, so the guard and the
        # resolvers can never disagree about how a tag list is read.
        from atlas.esde import parse_es_systems

        if not DEPLOYED_ES_SYSTEMS.exists():
            pytest.skip(f"RetroDECK's ES-DE is not deployed at {DEPLOYED_ES_SYSTEMS}")
        layer = parse_es_systems(
            DEPLOYED_ES_SYSTEMS.read_text(encoding="utf-8"), provenance="guard"
        )
        declared = {name: list(decl.platforms) for name, decl in layer.systems.items()}
        assert _shipped()["platforms"] == declared
