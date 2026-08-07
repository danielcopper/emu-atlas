"""Tests for atlas.evidence — the packaged record of what atlas has seen alive.

Two things are checked here: the loader refuses a record that would claim more
than it establishes, and every answer-bearing question of every unverified
arrangement states its evidence. The second is a completeness check on purpose —
a question added to the protocol, or a handle added to detection, must not be
able to answer without saying what atlas has established about it.
"""

from __future__ import annotations

import json

import pytest

import atlas
from atlas import installations
from atlas.evidence import (
    ARRANGEMENT_EVIDENCE_SCHEMA,
    CAVEAT_ARRANGEMENT_UNVERIFIED,
    arrangement_caveats,
    load_arrangement_evidence,
    lookup_arrangement,
)

HOME = "/home/deck"
RETRODECK_JSON = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
EMUDECK_SETTINGS = f"{HOME}/.config/EmuDeck/settings.sh"
STANDALONE_CFG = f"{HOME}/.var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg"
NATIVE_CFG = f"{HOME}/.config/retroarch/retroarch.cfg"
CORE_SO = "mgba_libretro.so"
SYSTEM = "gb"

VERIFIED_RECORD = {"version": "0.10.9b", "date": "2026-08-05", "reference": "one live installation"}


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


class TestWhatTheCaveatSays:
    def test_a_verified_arrangement_states_nothing(self):
        assert arrangement_caveats("retrodeck") == ()

    def test_an_unverified_arrangement_states_one_caveat(self):
        assert [c.code for c in arrangement_caveats("emudeck")] == [CAVEAT_ARRANGEMENT_UNVERIFIED]

    def test_the_caveat_names_the_installation_kind(self):
        assert arrangement_caveats("native_retroarch")[0].data == {"kind": "native_retroarch"}

    def test_an_arrangement_nobody_recorded_is_unverified(self):
        # A missing record is not evidence: the safe direction is to say so.
        assert [c.code for c in arrangement_caveats("no_such_arrangement")] == [
            CAVEAT_ARRANGEMENT_UNVERIFIED
        ]

    def test_the_message_does_not_claim_the_reading_was_guessed(self):
        # The precision the caveat exists for: what is missing is the live
        # observation, not the source-verified config chain.
        assert "source-verified" in arrangement_caveats("emudeck")[0].message


class TestEveryHandleKindHasARecord:
    """The packaged data covers detection — an omission is a build mistake.

    Behaviour stays safe either way (an unrecorded kind answers *unverified*),
    but a handle that silently gained no record would state a fact nobody wrote
    down, so the omission is refused here rather than shipped.
    """

    @pytest.mark.parametrize("kind", sorted(_handle_kinds()))
    def test_the_kind_is_recorded(self, kind):
        assert lookup_arrangement(kind) is not None


def _machine(files, **kwargs) -> atlas.FixtureMachine:
    return atlas.FixtureMachine(files, **kwargs)


# One fixture machine per arrangement, each minimal: the marker its detection
# triggers on, plus what its health needs to be quiet.
UNVERIFIED_MACHINES = {
    "emudeck": (
        {
            EMUDECK_SETTINGS: 'romsPath="$HOME/Emulation/roms"\nsavesPath="$HOME/Emulation/saves"\n',
            STANDALONE_CFG: 'savefile_directory = "~/Emulation/saves"\n',
        },
        {"dirs": [f"{HOME}/Emulation/saves"]},
    ),
    "standalone_retroarch_flatpak": ({STANDALONE_CFG: ""}, {}),
    "native_retroarch": ({NATIVE_CFG: ""}, {}),
}

RETRODECK_MACHINE = (
    {
        RETRODECK_JSON: '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}',
        "/mnt/sd/retrodeck/roms/systeminfo.txt": "",
    },
    {"dirs": ["/mnt/sd/retrodeck/saves"]},
)


def _answers(handle) -> dict[str, tuple[str, ...]]:
    """Every question of the protocol that answers with caveats, asked once.

    ``health`` is deliberately absent: its issues are machine defects, and
    ``Health.ok`` is defined as their absence, so an evidence note there would
    report a working installation as broken. :class:`TestHealthStaysAMachineFact`
    holds that decision down.
    """
    return {
        "save_location": tuple(c.code for c in handle.save_location(core_so=CORE_SO).caveats),
        "systems": tuple(c.code for c in handle.systems().caveats),
        "emulators_for": tuple(c.code for c in handle.emulators_for(SYSTEM).caveats),
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

    def test_the_verified_kinds_are_the_one_this_file_asks_directly(self):
        # RETRODECK_MACHINE stands for every verified kind — true while there
        # is one. A second verified arrangement needs its own fixture here.
        assert _handle_kinds() - _unverified_kinds() == {atlas.RetroDeck.kind}

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

    def test_the_verified_arrangement_says_nothing_anywhere(self):
        files, kwargs = RETRODECK_MACHINE
        handle = atlas.detect(HOME, _machine(files, **kwargs))[0]
        stated = sorted(
            question
            for question, codes in _answers(handle).items()
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
        files, kwargs = RETRODECK_MACHINE
        assert questions == set(_answers(atlas.detect(HOME, _machine(files, **kwargs))[0]))

    def test_the_aggregate_carries_it_too(self):
        # The aggregate delegates, so it should come for free — proven, not assumed.
        files, kwargs = UNVERIFIED_MACHINES["native_retroarch"]
        every = atlas.every_installation(HOME, _machine(files, **kwargs))
        answered = every.save_location(core_so=CORE_SO)[0]
        assert CAVEAT_ARRANGEMENT_UNVERIFIED in [c.code for c in answered.answer.caveats]


class TestHealthStaysAMachineFact:
    """Health answers "is this installation broken", and evidence is not a break.

    ``Health.ok`` is the absence of issues, and clients act on it — the usage
    guide tells them not to sync against an installation that is not ok. An
    evidence note there would report every EmuDeck and every bare RetroArch as
    defective, which is a claim about the machine that nobody made.
    """

    @pytest.mark.parametrize("kind", sorted(UNVERIFIED_MACHINES))
    def test_an_unverified_arrangement_can_still_be_healthy(self, kind):
        files, kwargs = UNVERIFIED_MACHINES[kind]
        handle = atlas.detect(HOME, _machine(files, **kwargs))[0]
        assert handle.health().ok

    def test_health_carries_no_evidence_issue(self):
        files, kwargs = UNVERIFIED_MACHINES["emudeck"]
        handle = atlas.detect(HOME, _machine(files, **kwargs))[0]
        assert CAVEAT_ARRANGEMENT_UNVERIFIED not in handle.health().codes
