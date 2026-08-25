"""Tests for atlas.contract — the serializers the vectors assert answers with.

The vectors exercise every serializer through whole machines; what belongs here
is the shape rules a single vector cannot state, starting with health: a finding
serializes as the caveat it is, and the installation form composes that rather
than restating it.
"""

from __future__ import annotations

import json

import pytest

import atlas
from atlas.machine import FixtureMachine
from atlas.contract import health_contract, installation_contract
from tests.answers import placed, state_placed

HOME = "/home/deck"
RETRODECK_JSON = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
GONE = "/run/media/gone/retrodeck"


def _broken() -> atlas.RetroDeck:
    machine = FixtureMachine({RETRODECK_JSON: '{"paths": {"rd_home_path": "%s"}}' % GONE})
    return atlas.RetroDeck(HOME, machine)


def _healthy() -> atlas.RetroDeck:
    machine = FixtureMachine(
        {
            RETRODECK_JSON: '{"paths": {"rd_home_path": "/mnt/sd/retrodeck"}}',
            "/mnt/sd/retrodeck/roms/systeminfo.txt": "",
        },
        dirs=["/mnt/sd/retrodeck/saves"],
    )
    return atlas.RetroDeck(HOME, machine)


class TestHealthContract:
    """The health answer is an object like every other answer in the grammar."""

    def test_a_healthy_installation_answers_ok_with_no_issues(self):
        assert health_contract(_healthy().health()) == {"ok": True, "issues": []}

    def test_a_broken_one_is_not_ok(self):
        assert health_contract(_broken().health())["ok"] is False

    def test_a_finding_serializes_as_a_caveat(self):
        assert health_contract(_broken().health())["issues"][0] == {
            "code": atlas.HEALTH_ISSUE_ROOT_MISSING,
            "data": {"path": GONE},
        }

    def test_every_finding_is_carried(self):
        health = _broken().health()
        assert [f["code"] for f in health_contract(health)["issues"]] == list(health.codes)

    def test_ok_says_what_the_issues_say(self):
        # The summary is derived, never a second fact: it can only ever agree.
        answer = health_contract(_broken().health())
        assert answer["ok"] == (not answer["issues"])

    def test_the_form_is_json(self):
        # A read-only mapping inside would serialize fine here and fail in a
        # caller's json.dumps, so the check is the round trip, not the type.
        serialized = health_contract(_broken().health())
        assert json.loads(json.dumps(serialized)) == serialized


class TestInstallationContractCarriesTheFindings:
    """Inside an installation, health is a field — the findings, unwrapped."""

    def test_the_health_field_is_the_findings(self):
        handle = _broken()
        assert installation_contract(handle)["health"] == health_contract(handle.health())["issues"]

    def test_a_healthy_installation_states_an_empty_list(self):
        assert installation_contract(_healthy())["health"] == []

    def test_identity_is_unchanged(self):
        assert set(installation_contract(_healthy())) == {"kind", "kinds", "root", "health"}


class TestTheSavestateFormOmitsGranularity:
    """The one field the two placement serializers do not share.

    A ``"granularity": null`` here would be a key no answer can ever fill, and
    a client would read it as "not established yet" rather than "cannot exist".
    Its absence is therefore contractual, and so is the rest of the form being
    identical — the two answers come off one upstream rule.
    """

    def _placements(self):
        handle = _healthy()
        return (
            atlas.savefile_placement_contract(placed(handle.savefile_location())),
            atlas.savestate_placement_contract(state_placed(handle.savestate_location())),
        )

    def test_granularity_is_absent_rather_than_null(self):
        _, state = self._placements()
        assert "granularity" not in state

    def test_every_other_key_is_the_same(self):
        save, state = self._placements()
        assert set(save) - set(state) == {"granularity"}
        assert set(state) - set(save) == set()

    def test_the_root_kind_names_the_savestate_anchor(self):
        _, state = self._placements()
        assert state["root_kind"] == atlas.ROOT_SAVESTATE_DIRECTORY

    def test_the_form_is_json(self):
        _, state = self._placements()
        assert json.loads(json.dumps(state)) == state


class TestTheStatedNoIsItsOwnShape:
    """#284: ``no_savestates`` is an answer — not a placement, not a refusal."""

    def _absence(self, **overrides):
        stated = {
            "emulator": "DEMO",
            "citation": "the whole tree at v1: no state serializer",
            "sources": ("standalone savestate card 'DEMO': prose",),
        }
        stated.update(overrides)
        return atlas.SavestateAbsence(**stated)

    def test_the_answer_serializer_branches_to_it(self):
        block = atlas.savestate_answer_contract(self._absence())
        assert set(block) == {"no_savestates"}
        assert block["no_savestates"]["emulator"] == "DEMO"
        assert block["no_savestates"]["citation"].startswith("the whole tree")
        assert block["no_savestates"]["caveats"] == []

    def test_the_cards_own_caveat_rides_contractually(self):
        caveat = atlas.Caveat(
            atlas.CAVEAT_UNVERIFIED_VERSION,
            "nothing ships this emulator",
            {"emulator": "DEMO", "verification": "build-unestablished"},
        )
        block = atlas.savestate_absence_contract(self._absence(caveats=(caveat,)))
        assert block["no_savestates"]["caveats"] == [
            {
                "code": "unverified-version",
                "data": {"emulator": "DEMO", "verification": "build-unestablished"},
            }
        ]

    def test_sources_stay_out_like_every_provenance(self):
        block = atlas.savestate_absence_contract(self._absence())
        assert "sources" not in block["no_savestates"]

    def test_a_citation_free_no_cannot_exist(self):
        with pytest.raises(ValueError, match="citation"):
            self._absence(citation="")

    def test_the_form_is_json(self):
        block = atlas.savestate_absence_contract(self._absence())
        assert json.loads(json.dumps(block)) == block
