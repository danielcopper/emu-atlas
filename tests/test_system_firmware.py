"""Tests for atlas.system_firmware — the table of systems that need firmware.

Two halves. The shipped table loads and says what it is meant to say, and every
way of writing a claim this table must not carry is refused rather than
accepted and half-understood. The refusals matter more than usual here: nothing
in atlas reads a verdict yet, so a malformed entry would sit in the package
until the cut that makes an answer carry it, and be found by a client.
"""

from __future__ import annotations

import json

import pytest

from atlas.system_firmware import (
    EVIDENCE_DERIVED,
    EVIDENCE_LEVELS,
    EVIDENCE_OPEN,
    EVIDENCE_VERIFIED,
    SYSTEM_FIRMWARE_VERDICTS,
    VERDICT_CANNOT_RUN_WITHOUT,
    VERDICT_OPEN,
    VERDICT_RUNS_WITHOUT,
    load_system_firmware,
)


def _table(entry: object, system: str = "Demo System") -> str:
    return json.dumps({"schema": 1, "spec": "a spec", "systems": {system: entry}})


_SOUND = {"verdict": VERDICT_OPEN, "evidence": EVIDENCE_OPEN, "source": "[O] nobody looked"}


class TestTheShippedTable:
    def test_it_loads(self):
        assert load_system_firmware()

    def test_every_entry_carries_a_known_verdict_and_level(self):
        for system, entry in load_system_firmware().items():
            assert entry.system == system
            assert entry.verdict in SYSTEM_FIRMWARE_VERDICTS
            assert entry.evidence in EVIDENCE_LEVELS
            assert entry.source

    def test_playstation_cannot_run_without_firmware_and_exempts_rearmed(self):
        # The one system with observations behind it, and the one exemption: a
        # core whose all-optional declaration is correct because it supplies a
        # substitute. The exemption is recorded for the cut that makes an
        # answer read this table; today the only check consuming it is the
        # staleness one in the tripwire.
        entry = load_system_firmware()["PlayStation"]
        assert entry.verdict == VERDICT_CANNOT_RUN_WITHOUT
        assert entry.evidence == EVIDENCE_VERIFIED
        assert [alternative.core for alternative in entry.alternatives] == ["pcsx_rearmed"]
        assert all(alternative.reason for alternative in entry.alternatives)

    def test_the_open_verdict_reads_as_neither_answer(self):
        # What makes 'open' a value rather than an absence: it loads, it is
        # nothing a reader can mistake for a verdict, and it carries a source
        # saying what is and is not established.
        entry = load_system_firmware()["Saturn"]
        assert entry.verdict == VERDICT_OPEN
        assert entry.verdict not in (VERDICT_CANNOT_RUN_WITHOUT, VERDICT_RUNS_WITHOUT)
        assert entry.evidence == EVIDENCE_OPEN
        assert entry.alternatives == ()
        assert entry.source

    def test_only_a_needs_firmware_entry_exempts_a_core(self):
        for entry in load_system_firmware().values():
            if entry.verdict != VERDICT_CANNOT_RUN_WITHOUT:
                assert entry.alternatives == ()


class TestTheLoaderRefuses:
    def test_an_unsupported_schema(self):
        with pytest.raises(ValueError, match="unsupported schema"):
            load_system_firmware(json.dumps({"schema": 99, "systems": {"Demo": _SOUND}}))

    def test_a_document_that_is_not_an_object(self):
        with pytest.raises(ValueError, match="unsupported schema"):
            load_system_firmware(json.dumps([{"schema": 1}]))

    def test_an_empty_systems_block(self):
        with pytest.raises(ValueError, match="non-empty object"):
            load_system_firmware(json.dumps({"schema": 1, "spec": "a spec", "systems": {}}))

    def test_a_malformed_entry(self):
        with pytest.raises(ValueError, match="verdict, evidence and source"):
            load_system_firmware(_table({"verdict": VERDICT_OPEN}))

    def test_an_entry_that_is_not_an_object(self):
        with pytest.raises(ValueError, match="verdict, evidence and source"):
            load_system_firmware(_table("cannot run"))

    def test_an_unknown_key(self):
        with pytest.raises(ValueError, match="unknown key"):
            load_system_firmware(_table({**_SOUND, "confidence": "high"}))

    def test_an_unknown_verdict(self):
        with pytest.raises(ValueError, match="verdict must be one of"):
            load_system_firmware(
                _table({**_SOUND, "verdict": "probably", "evidence": EVIDENCE_DERIVED})
            )

    def test_an_unknown_evidence_level(self):
        with pytest.raises(ValueError, match="evidence must be one of"):
            load_system_firmware(_table({**_SOUND, "evidence": "[X]"}))

    def test_an_empty_source(self):
        with pytest.raises(ValueError, match="source: expected a non-blank string"):
            load_system_firmware(_table({**_SOUND, "source": ""}))

    def test_a_whitespace_only_source(self):
        # A citation of one space says exactly what an empty one says, and
        # would slip past a truthiness check.
        with pytest.raises(ValueError, match="source: expected a non-blank string"):
            load_system_firmware(_table({**_SOUND, "source": "   "}))

    def test_a_stated_verdict_resting_on_an_open_level(self):
        # A verdict that says something cannot cite an open question as its
        # evidence — that is the shape of a claim nobody checked.
        with pytest.raises(ValueError, match="disagree"):
            load_system_firmware(
                _table({**_SOUND, "verdict": VERDICT_RUNS_WITHOUT, "evidence": EVIDENCE_OPEN})
            )

    def test_an_open_verdict_claiming_a_verified_level(self):
        with pytest.raises(ValueError, match="disagree"):
            load_system_firmware(_table({**_SOUND, "evidence": EVIDENCE_VERIFIED}))

    def test_an_exemption_on_a_system_that_does_not_need_firmware(self):
        # There is no understatement to excuse where nothing is understated,
        # so an exemption here would be a sentence about nothing.
        with pytest.raises(ValueError, match="only a 'cannot-run-without-firmware' entry"):
            load_system_firmware(
                _table({**_SOUND, "cores_supplying_an_alternative": {"demo": "it has HLE"}})
            )

    def test_an_empty_exemption_block(self):
        with pytest.raises(ValueError, match="non-empty object"):
            load_system_firmware(
                _table(
                    {
                        "verdict": VERDICT_CANNOT_RUN_WITHOUT,
                        "evidence": EVIDENCE_VERIFIED,
                        "source": "[V-live] watched it refuse",
                        "cores_supplying_an_alternative": {},
                    }
                )
            )

    @pytest.mark.parametrize("reason", ["", "   ", "\n\t "])
    def test_an_exemption_without_a_reason(self, reason):
        # A bare exemption is the thing this field exists to prevent: the
        # reason is the evidence, and a name on its own excuses by assertion.
        # Blank counts as bare — a reason of one space is not a reason.
        with pytest.raises(ValueError, match="reason: expected a non-blank string"):
            load_system_firmware(
                _table(
                    {
                        "verdict": VERDICT_CANNOT_RUN_WITHOUT,
                        "evidence": EVIDENCE_VERIFIED,
                        "source": "[V-live] watched it refuse",
                        "cores_supplying_an_alternative": {"demo": reason},
                    }
                )
            )

    def test_an_exemption_field_stated_as_null_on_an_open_entry(self):
        # Key presence, not value truthiness: a `null` here states the field on
        # an entry that must not carry it, and reading it through `.get()` let
        # exactly that through on every verdict.
        with pytest.raises(ValueError, match="only a 'cannot-run-without-firmware' entry"):
            load_system_firmware(_table({**_SOUND, "cores_supplying_an_alternative": None}))

    def test_an_exemption_field_stated_as_null_on_a_needs_firmware_entry(self):
        with pytest.raises(ValueError, match="non-empty object"):
            load_system_firmware(
                _table(
                    {
                        "verdict": VERDICT_CANNOT_RUN_WITHOUT,
                        "evidence": EVIDENCE_VERIFIED,
                        "source": "[V-live] watched it refuse",
                        "cores_supplying_an_alternative": None,
                    }
                )
            )

    @pytest.mark.parametrize("system", ["", "   "])
    def test_a_blank_system_key(self, system):
        with pytest.raises(ValueError, match="system key: expected a non-blank string"):
            load_system_firmware(_table(_SOUND, system=system))
