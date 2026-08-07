"""Tests for atlas.contract — the serializers the vectors assert answers with.

The vectors exercise every serializer through whole machines; what belongs here
is the shape rules a single vector cannot state, starting with health: a finding
serializes as the caveat it is, and the installation form composes that rather
than restating it.
"""

from __future__ import annotations

import json

import atlas
from atlas.contract import health_contract, installation_contract

HOME = "/home/deck"
RETRODECK_JSON = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
GONE = "/run/media/gone/retrodeck"


def _broken() -> atlas.RetroDeck:
    machine = atlas.FixtureMachine({RETRODECK_JSON: '{"paths": {"rd_home_path": "%s"}}' % GONE})
    return atlas.RetroDeck(HOME, machine)


def _healthy() -> atlas.RetroDeck:
    machine = atlas.FixtureMachine(
        {
            RETRODECK_JSON: '{"paths": {"rd_home_path": "/mnt/sd/retrodeck"}}',
            "/mnt/sd/retrodeck/roms/systeminfo.txt": "",
        },
        dirs=["/mnt/sd/retrodeck/saves"],
    )
    return atlas.RetroDeck(HOME, machine)


class TestHealthContract:
    def test_healthy_serializes_to_no_findings(self):
        assert health_contract(_healthy().health()) == []

    def test_a_finding_serializes_as_a_caveat(self):
        assert health_contract(_broken().health())[0] == {
            "code": atlas.HEALTH_ISSUE_ROOT_MISSING,
            "data": {"path": GONE},
        }

    def test_every_finding_is_carried(self):
        health = _broken().health()
        assert [f["code"] for f in health_contract(health)] == list(health.codes)

    def test_the_form_is_json(self):
        # A read-only mapping inside would serialize fine here and fail in a
        # caller's json.dumps, so the check is the round trip, not the type.
        serialized = health_contract(_broken().health())
        assert json.loads(json.dumps(serialized)) == serialized


class TestInstallationContractComposesHealth:
    def test_the_health_field_is_the_health_contract(self):
        handle = _broken()
        assert installation_contract(handle)["health"] == health_contract(handle.health())

    def test_a_healthy_installation_states_an_empty_list(self):
        assert installation_contract(_healthy())["health"] == []

    def test_identity_is_unchanged(self):
        assert set(installation_contract(_healthy())) == {"kind", "kinds", "root", "health"}
