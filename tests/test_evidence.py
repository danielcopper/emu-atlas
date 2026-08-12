"""Tests for atlas.evidence — the packaged record of what atlas has seen alive.

Three things are checked here: the loader refuses a record that would claim more
than it establishes, every answer-bearing question of every unverified
arrangement states its evidence, and every question of a verified arrangement
whose machine has moved on states *that*. The completeness checks are on
purpose — a question added to the protocol, or a handle added to detection, must
not be able to answer without saying what atlas has established about it.

Nothing here hard-codes the version the packaged record pins: re-verifying
RetroDECK is meant to be a one-file change, and a test that spelled the pin out
would make it two.
"""

from __future__ import annotations

import json

import pytest

import atlas
from atlas.machine import FixtureMachine
from atlas import installations
from atlas.evidence import (
    ARRANGEMENT_EVIDENCE_SCHEMA,
    CAVEAT_ARRANGEMENT_UNVERIFIED,
    CAVEAT_ARRANGEMENT_VERSION_DRIFTED,
    arrangement_caveats,
    load_arrangement_evidence,
    lookup_arrangement,
)
from tests.answers import placed, state_placed

HOME = "/home/deck"
RETRODECK_JSON = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
EMUDECK_SETTINGS = f"{HOME}/.config/EmuDeck/settings.sh"
STANDALONE_CFG = f"{HOME}/.var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg"
NATIVE_CFG = f"{HOME}/.config/retroarch/retroarch.cfg"
CORE_SO = "mgba_libretro.so"
SYSTEM = "gb"

VERIFIED_RECORD = {"version": "0.10.9b", "date": "2026-08-05", "reference": "one live installation"}

# A version no record will ever pin, so "this machine has moved on" needs no
# maintenance the day the pinned one does.
DRIFTED_VERSION = "0.0.0-never-shipped"


def _pin(kind: str) -> str:
    """The version the packaged record says *kind* was verified against."""
    record = lookup_arrangement(kind)
    assert record is not None, f"{kind} has no evidence record"
    assert record.verified is not None, f"{kind} is not a verified arrangement"
    return record.verified.version


def _handle_kinds() -> set[str]:
    """Every installation kind there is — read off the handle classes themselves.

    A completeness check whose two sides are both hand-written lists proves
    nothing: a fifth handle would change neither, and the check would pass
    while the new arrangement had no record. So the kinds come from the code
    that defines them — every public handle class in
    :mod:`atlas.installations` declaring a ``kind`` of its own. Shared bases
    (``_RetroArchInstall``) declare none and are private besides; the protocol's
    ``kind`` is a property, not a string.
    """
    return {
        member.kind
        for name, member in vars(installations).items()
        if not name.startswith("_")
        and isinstance(member, type)
        and isinstance(vars(member).get("kind"), str)
    }


def _unverified_kinds() -> set[str]:
    """The kinds the packaged data says nobody has observed live."""
    return {
        kind
        for kind in _handle_kinds()
        if (record := lookup_arrangement(kind)) is None or record.verified is None
    }


def _record(**entry) -> str:
    return json.dumps({"schema": ARRANGEMENT_EVIDENCE_SCHEMA, "arrangements": {"x": entry}})


class TestTheLoaderRefusesWhatItCannotPlace:
    def test_an_unknown_schema_is_rejected(self):
        with pytest.raises(ValueError, match="schema"):
            load_arrangement_evidence('{"schema": 99, "arrangements": {}}')

    def test_a_missing_schema_is_rejected(self):
        with pytest.raises(ValueError, match="schema"):
            load_arrangement_evidence('{"arrangements": {}}')

    def test_a_verification_without_a_version_is_rejected(self):
        # A record pinning nothing would read as verified everywhere and
        # forever — the one shape worse than never verified.
        text = _record(label="X", note="n", verified={"reference": "somewhere"})
        with pytest.raises(ValueError, match="version"):
            load_arrangement_evidence(text)

    def test_a_verification_without_a_reference_is_rejected(self):
        text = _record(label="X", note="n", verified={"version": "1.0"})
        with pytest.raises(ValueError, match="reference"):
            load_arrangement_evidence(text)

    def test_a_record_without_a_note_is_rejected(self):
        with pytest.raises(ValueError, match="note"):
            load_arrangement_evidence(_record(label="X", verified=None))

    def test_a_verified_record_loads_with_its_pin(self):
        record = load_arrangement_evidence(_record(label="X", note="n", verified=VERIFIED_RECORD))["x"]
        assert record.verified is not None and record.verified.version == "0.10.9b"

    def test_an_undated_verification_is_allowed(self):
        text = _record(label="X", note="n", verified={"version": "1.0", "reference": "r"})
        assert load_arrangement_evidence(text)["x"].verified is not None


# The two arrangements the packaged record says were observed live — every
# tripwire test runs over both, because each pins its version in its own
# spelling (a release string, a git HEAD) and the comparison must not care.
VERIFIED_KINDS = (atlas.EmuDeck.kind, atlas.RetroDeck.kind)


class TestWhatTheCaveatSays:
    @pytest.mark.parametrize("kind", VERIFIED_KINDS)
    def test_a_verified_arrangement_states_nothing(self, kind):
        assert arrangement_caveats(kind) == ()

    def test_an_unverified_arrangement_states_one_caveat(self):
        assert [c.code for c in arrangement_caveats("bare_retroarch_flatpak")] == [
            CAVEAT_ARRANGEMENT_UNVERIFIED
        ]

    def test_the_caveat_names_the_installation_kind(self):
        assert arrangement_caveats("bare_retroarch_native")[0].data == {"kind": "bare_retroarch_native"}

    def test_an_arrangement_nobody_recorded_is_unverified(self):
        # A missing record is not evidence: the safe direction is to say so.
        assert [c.code for c in arrangement_caveats("no_such_arrangement")] == [
            CAVEAT_ARRANGEMENT_UNVERIFIED
        ]

    def test_the_message_does_not_claim_the_reading_was_guessed(self):
        # The precision the caveat exists for: what is missing is the live
        # observation, not the source-verified config chain.
        assert "source-verified" in arrangement_caveats("bare_retroarch_flatpak")[0].message


class TestTheVersionTripwire:
    """A verified arrangement was verified against one version of itself.

    The comparison needs both sides, and only both sides together license a
    statement: the record's pin and the version the machine states about itself.
    """

    def _codes(self, kind, **kwargs) -> list[str]:
        return [c.code for c in arrangement_caveats(kind, **kwargs)]

    @pytest.mark.parametrize("kind", VERIFIED_KINDS)
    def test_the_pinned_version_states_nothing(self, kind):
        assert arrangement_caveats(kind, observed_version=_pin(kind)) == ()

    @pytest.mark.parametrize("kind", VERIFIED_KINDS)
    def test_another_version_states_the_drift(self, kind):
        assert self._codes(kind, observed_version=DRIFTED_VERSION) == [
            CAVEAT_ARRANGEMENT_VERSION_DRIFTED
        ]

    @pytest.mark.parametrize("kind", VERIFIED_KINDS)
    def test_the_caveat_names_both_sides(self, kind):
        caveat = arrangement_caveats(kind, observed_version=DRIFTED_VERSION)[0]
        assert caveat.data == {
            "kind": kind,
            "verified": _pin(kind),
            "observed": DRIFTED_VERSION,
        }

    @pytest.mark.parametrize("kind", VERIFIED_KINDS)
    def test_a_machine_that_states_no_version_stays_silent(self, kind):
        # Not "no drift" — no drift ESTABLISHED. Claiming a comparison nobody
        # could make is the one thing atlas never does.
        assert arrangement_caveats(kind) == ()

    @pytest.mark.parametrize("kind", VERIFIED_KINDS)
    def test_an_empty_version_names_no_version_either(self, kind):
        assert arrangement_caveats(kind, observed_version="") == ()

    def test_an_unverified_arrangement_has_no_pin_to_drift_from(self):
        # Whatever version an unobserved arrangement states, the missing
        # observation is the more general fact and the only one stated.
        drifting = arrangement_caveats("bare_retroarch_flatpak", observed_version=DRIFTED_VERSION)
        assert [c.code for c in drifting] == [CAVEAT_ARRANGEMENT_UNVERIFIED]

    @pytest.mark.parametrize("kind", VERIFIED_KINDS)
    def test_the_message_states_what_is_pending_not_that_the_answer_is_wrong(self, kind):
        message = arrangement_caveats(kind, observed_version=DRIFTED_VERSION)[0].message
        assert "re-verification is pending" in message


class TestEveryHandleKindHasARecord:
    """The packaged data covers detection — an omission is a build mistake.

    Behaviour stays safe either way (an unrecorded kind answers *unverified*),
    but a handle that silently gained no record would state a fact nobody wrote
    down, so the omission is refused here rather than shipped.
    """

    @pytest.mark.parametrize("kind", sorted(_handle_kinds()))
    def test_the_kind_is_recorded(self, kind):
        assert lookup_arrangement(kind) is not None


def _machine(files, **kwargs) -> FixtureMachine:
    return FixtureMachine(files, **kwargs)


# One fixture machine per arrangement, each minimal: the marker its detection
# triggers on, plus what its health needs to be quiet.
UNVERIFIED_MACHINES = {
    "bare_retroarch_flatpak": ({STANDALONE_CFG: ""}, {}),
    "bare_retroarch_native": ({NATIVE_CFG: ""}, {}),
}


def _retrodeck(version: str | None = None) -> atlas.Installation:
    """The first verified arrangement, stating *version* about itself — or none.

    Minimal in the same way the dict above is: the marker detection triggers on,
    plus what its health needs to be quiet, so an evidence statement is never
    confused with a finding about the machine.
    """
    paths = {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}
    marker: dict[str, object] = {"paths": paths}
    if version is not None:
        marker["version"] = version
    files = {RETRODECK_JSON: json.dumps(marker), "/mnt/sd/retrodeck/roms/systeminfo.txt": ""}
    return atlas.detect(HOME, _machine(files, dirs=["/mnt/sd/retrodeck/saves"]))[0]


def _emudeck(version: str | None = None) -> atlas.Installation:
    """The second verified arrangement, stating *version* about itself — or none.

    EmuDeck's version statement is the backend checkout's git HEAD, spelled
    the way a live installation spells it: the symref in ``.git/HEAD`` and
    the loose ref file it names. No version means no anchor files — the state
    every fixture without them is in.
    """
    files = {
        EMUDECK_SETTINGS: 'romsPath="$HOME/Emulation/roms"\nsavesPath="$HOME/Emulation/saves"\n',
        STANDALONE_CFG: 'savefile_directory = "~/Emulation/saves"\n',
    }
    if version is not None:
        files[f"{HOME}/.config/EmuDeck/backend/.git/HEAD"] = "ref: refs/heads/main\n"
        files[f"{HOME}/.config/EmuDeck/backend/.git/refs/heads/main"] = f"{version}\n"
    return atlas.detect(HOME, _machine(files, dirs=[f"{HOME}/Emulation/saves"]))[0]


# The verified arrangements' fixture builders, keyed the way the record is:
# the drift-completeness checks below run over every one of them, so a third
# verified arrangement extends this dict and inherits the whole battery.
VERIFIED_FIXTURES = {
    atlas.RetroDeck.kind: _retrodeck,
    atlas.EmuDeck.kind: _emudeck,
}


def _answers(handle) -> dict[str, tuple[str, ...]]:
    """Every question of the protocol that answers with caveats, asked once.

    ``health`` is deliberately absent: its issues are machine defects, and
    ``Health.ok`` is defined as their absence, so an evidence note there would
    report a working installation as broken. :class:`TestHealthStaysAMachineFact`
    holds that decision down.
    """
    return {
        "savefile_location": tuple(c.code for c in placed(handle.savefile_location(core_so=CORE_SO)).caveats),
        "savestate_location": tuple(c.code for c in state_placed(handle.savestate_location(core_so=CORE_SO)).caveats),
        "systems": tuple(c.code for c in handle.systems().caveats),
        "emulators_for": tuple(c.code for c in handle.emulators_for(SYSTEM).caveats),
        "rom_location": tuple(c.code for c in handle.rom_location(SYSTEM).caveats),
        "firmware_for_core": tuple(c.code for c in handle.firmware_for_core(core_so=CORE_SO).caveats),
        "firmware_for_system": tuple(c.code for c in handle.firmware_for_system(system=SYSTEM).caveats),
        "firmware_inventory": tuple(c.code for c in handle.firmware_inventory().caveats),
        "identify_firmware": tuple(c.code for c in handle.identify_firmware(md5="deadbeef").caveats),
    }


class TestEveryAnswerStatesItsEvidence:
    def test_every_unverified_kind_has_a_fixture_machine(self):
        # The parametrized check below can only cover what this dict names, so
        # a newly unverified arrangement without a fixture would skip it in
        # silence. The dict is hand-written (each kind needs its own marker
        # files); that it is complete is not.
        assert set(UNVERIFIED_MACHINES) == _unverified_kinds()

    def test_the_verified_kinds_are_the_ones_this_file_asks_directly(self):
        # Each verified kind has its own fixture builder — a third verified
        # arrangement needs one here, and this is what notices its absence.
        assert _handle_kinds() - _unverified_kinds() == set(VERIFIED_FIXTURES) == set(VERIFIED_KINDS)

    @pytest.mark.parametrize("kind", sorted(UNVERIFIED_MACHINES))
    def test_every_question_of_an_unverified_arrangement_says_so(self, kind):
        files, kwargs = UNVERIFIED_MACHINES[kind]
        handle = atlas.detect(HOME, _machine(files, **kwargs))[0]
        silent = sorted(
            question
            for question, codes in _answers(handle).items()
            if CAVEAT_ARRANGEMENT_UNVERIFIED not in codes
        )
        assert silent == []

    @pytest.mark.parametrize("kind", sorted(VERIFIED_FIXTURES))
    def test_the_verified_arrangements_say_nothing_anywhere(self, kind):
        stated = sorted(
            question
            for question, codes in _answers(VERIFIED_FIXTURES[kind]()).items()
            if CAVEAT_ARRANGEMENT_UNVERIFIED in codes
        )
        assert stated == []

    def test_the_questions_asked_are_the_protocols_own(self):
        # The list above is a hand-written mirror of the protocol; this is what
        # notices when the protocol grows a question it does not carry.
        questions = {
            name
            for name, member in vars(atlas.Installation).items()
            if not name.startswith("_") and callable(member)
        } - {"root", "health"}
        assert questions == set(_answers(_retrodeck()))

    def test_the_aggregate_carries_it_too(self):
        # The aggregate delegates, so it should come for free — proven, not assumed.
        files, kwargs = UNVERIFIED_MACHINES["bare_retroarch_native"]
        every = atlas.every_installation(HOME, _machine(files, **kwargs))
        answered = every.savefile_location(core_so=CORE_SO)[0]
        assert CAVEAT_ARRANGEMENT_UNVERIFIED in [c.code for c in placed(answered.answer).caveats]


class TestEveryAnswerStatesTheDrift:
    """The same completeness, one axis over: verified, but not against this version.

    The three injection seams differ (the firmware context, the catalogue
    wrappers, the placement's caveat channel), and a question routed through the
    wrong one would answer without the caveat — which is exactly the silence
    this feature exists to end. So every question is asked, not a sample.
    """

    def _drifted(self, kind) -> dict[str, tuple[str, ...]]:
        return _answers(VERIFIED_FIXTURES[kind](DRIFTED_VERSION))

    @pytest.mark.parametrize("kind", sorted(VERIFIED_FIXTURES))
    def test_every_question_of_a_drifted_arrangement_says_so(self, kind):
        silent = sorted(
            question
            for question, codes in self._drifted(kind).items()
            if CAVEAT_ARRANGEMENT_VERSION_DRIFTED not in codes
        )
        assert silent == []

    @pytest.mark.parametrize("kind", sorted(VERIFIED_FIXTURES))
    def test_no_question_states_it_twice(self, kind):
        # Two seams reaching one answer would double it, and a client counting
        # caveats would read one drift as two.
        repeated = sorted(
            question
            for question, codes in self._drifted(kind).items()
            if codes.count(CAVEAT_ARRANGEMENT_VERSION_DRIFTED) != 1
        )
        assert repeated == []

    @pytest.mark.parametrize("kind", sorted(VERIFIED_FIXTURES))
    def test_a_machine_on_the_pinned_version_says_nothing_anywhere(self, kind):
        handle = VERIFIED_FIXTURES[kind](_pin(kind))
        stated = sorted(
            question
            for question, codes in _answers(handle).items()
            if CAVEAT_ARRANGEMENT_VERSION_DRIFTED in codes
        )
        assert stated == []

    @pytest.mark.parametrize("kind", sorted(VERIFIED_FIXTURES))
    def test_the_drifted_arrangement_is_still_a_verified_one(self, kind):
        # The two codes are different claims: this arrangement HAS been
        # observed live, so the never-observed caveat must stay away.
        stated = sorted(
            question
            for question, codes in self._drifted(kind).items()
            if CAVEAT_ARRANGEMENT_UNVERIFIED in codes
        )
        assert stated == []

    @pytest.mark.parametrize("kind", sorted(VERIFIED_FIXTURES))
    def test_the_aggregate_carries_it_too(self, kind):
        every = atlas.EveryInstallation((VERIFIED_FIXTURES[kind](DRIFTED_VERSION),))
        answered = every.savefile_location(core_so=CORE_SO)[0]
        assert CAVEAT_ARRANGEMENT_VERSION_DRIFTED in [c.code for c in placed(answered.answer).caveats]


class TestHealthStaysAMachineFact:
    """Health answers "is this installation broken", and evidence is not a break.

    ``Health.ok`` is the absence of issues, and clients act on it — the usage
    guide tells them not to sync against an installation that is not ok. An
    evidence note there would report every bare RetroArch as
    defective, which is a claim about the machine that nobody made. A machine
    that updated past what atlas has verified is the same kind of note: the
    installation is fine, atlas's record of it is what aged.
    """

    @pytest.mark.parametrize("kind", sorted(UNVERIFIED_MACHINES))
    def test_an_unverified_arrangement_can_still_be_healthy(self, kind):
        files, kwargs = UNVERIFIED_MACHINES[kind]
        handle = atlas.detect(HOME, _machine(files, **kwargs))[0]
        assert handle.health().ok

    def test_health_carries_no_evidence_issue(self):
        files, kwargs = UNVERIFIED_MACHINES["bare_retroarch_flatpak"]
        handle = atlas.detect(HOME, _machine(files, **kwargs))[0]
        assert CAVEAT_ARRANGEMENT_UNVERIFIED not in handle.health().codes

    @pytest.mark.parametrize("kind", sorted(VERIFIED_FIXTURES))
    def test_a_drifted_arrangement_can_still_be_healthy(self, kind):
        assert VERIFIED_FIXTURES[kind](DRIFTED_VERSION).health().ok

    @pytest.mark.parametrize("kind", sorted(VERIFIED_FIXTURES))
    def test_health_carries_no_drift_issue(self, kind):
        assert (
            CAVEAT_ARRANGEMENT_VERSION_DRIFTED
            not in VERIFIED_FIXTURES[kind](DRIFTED_VERSION).health().codes
        )
