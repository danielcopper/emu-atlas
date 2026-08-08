"""Tests for atlas.every_installation — fan-out, labels, order, and nothing else.

The aggregate's whole contract is that it adds nothing: every labelled answer
must be the answer the handle route gives for the same question, the labels must
be the handles themselves, and the order must be detection order. So most of
these tests ask the same question twice — once of the aggregate, once of the
handle — and demand the two agree.
"""

from __future__ import annotations

import inspect
from typing import Any

import atlas
from atlas.machine import FixtureMachine
from atlas.contract import (
    catalogue_contract,
    firmware_contract,
    health_contract,
    identification_contract,
    installation_answers_contract,
    installation_contract,
    placement_contract,
    rom_placement_contract,
    savestate_placement_contract,
    systems_contract,
)

HOME = "/home/deck"
RETRODECK_JSON = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
RETRODECK_CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch.cfg"
NATIVE_CFG = f"{HOME}/.config/retroarch/retroarch.cfg"

ROM = f"{HOME}/roms/gb/Tetris (World).zip"
CORE_SO = "mgba_libretro.so"
MD5 = "32fbbd84168d3482956eb3c5051637f5"
FLAT_LAYOUT = 'sort_savefiles_enable = "false"\nsort_savefiles_by_content_enable = "false"\n'

# One machine carrying two arrangements: RetroDECK's saves live on the card,
# the native install's beside the home directory. Neither answer is the other's.
COEXISTENCE_FILES = {
    RETRODECK_JSON: '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}',
    "/mnt/sd/retrodeck/roms/systeminfo.txt": "",
    RETRODECK_CFG: f'savefile_directory = "/mnt/sd/retrodeck/saves"\n{FLAT_LAYOUT}',
    NATIVE_CFG: f'savefile_directory = "~/ra-saves"\n{FLAT_LAYOUT}',
    ROM: "",
}
COEXISTENCE_DIRS = ["/mnt/sd/retrodeck/saves", f"{HOME}/ra-saves"]


def _coexistence() -> FixtureMachine:
    return FixtureMachine(COEXISTENCE_FILES, dirs=COEXISTENCE_DIRS)


def _handles(machine: FixtureMachine) -> list[atlas.Installation]:
    return atlas.detect(HOME, machine)


def _every(machine: FixtureMachine) -> atlas.EveryInstallation:
    return atlas.EveryInstallation(_handles(machine))


class TestTheEmptyMachine:
    """Nothing installed is an answer, not a failure — detect's own empty."""

    def test_no_installations_are_asked(self):
        assert _every(FixtureMachine({})).installations == ()

    def test_a_question_answers_with_nothing(self):
        assert _every(FixtureMachine({})).save_location(core_so=CORE_SO) == ()

    def test_the_empty_answer_serializes_to_an_empty_list(self):
        answers = _every(FixtureMachine({})).save_location(core_so=CORE_SO)
        assert installation_answers_contract(answers, placement_contract) == []


class TestTheFanOut:
    """Every detected installation answers, in detection order, labelled."""

    def test_every_installation_answers(self):
        machine = _coexistence()
        answers = atlas.EveryInstallation(_handles(machine)).save_location(content_path=ROM)
        assert len(answers) == len(_handles(machine))

    def test_the_answers_keep_detection_order(self):
        answers = _every(_coexistence()).save_location(content_path=ROM)
        assert [a.installation.kind for a in answers] == ["retrodeck", "bare_retroarch_native"]

    def test_each_answer_is_labelled_with_the_handle_that_gave_it(self):
        # Identity, not a copy: the label is the handle a caller drills down
        # with, and reading kind/root/health off it stays a live read.
        handles = _handles(_coexistence())
        answers = atlas.EveryInstallation(handles).save_location(content_path=ROM)
        assert [a.installation for a in answers] == handles

    def test_a_single_installation_machine_answers_once(self):
        machine = FixtureMachine(
            {NATIVE_CFG: COEXISTENCE_FILES[NATIVE_CFG], ROM: ""}, dirs=[f"{HOME}/ra-saves"]
        )
        answers = _every(machine).save_location(content_path=ROM)
        assert [a.installation.kind for a in answers] == ["bare_retroarch_native"]

    def test_the_two_answers_are_not_the_same_answer(self):
        # The point of the aggregate: two arrangements, two save roots, both
        # true — nothing merged, nothing preferred.
        answers = _every(_coexistence()).save_location(content_path=ROM)
        assert [a.answer.dir for a in answers] == ["/mnt/sd/retrodeck/saves", f"{HOME}/ra-saves"]


class TestItDelegates:
    """Each labelled answer is byte-identical to the handle route's answer."""

    def _pairs(self, question):
        """(aggregate answers, the same question asked of each handle directly)."""
        machine = _coexistence()
        handles = _handles(machine)
        aggregate = question(atlas.EveryInstallation(handles))
        return [a.answer for a in aggregate], [question(handle) for handle in handles]

    def test_save_location(self):
        asked, direct = self._pairs(lambda h: h.save_location(content_path=ROM, core_so=CORE_SO))
        assert [placement_contract(p) for p in asked] == [placement_contract(p) for p in direct]

    def test_state_location(self):
        asked, direct = self._pairs(lambda h: h.state_location(content_path=ROM, core_so=CORE_SO))
        assert [savestate_placement_contract(p) for p in asked] == [
            savestate_placement_contract(p) for p in direct
        ]

    def test_emulators_for(self):
        asked, direct = self._pairs(lambda h: h.emulators_for("n64", content_path=ROM))
        assert [catalogue_contract(c) for c in asked] == [catalogue_contract(c) for c in direct]

    def test_the_handles_state_their_own_catalogue_refusals(self):
        # The two arrangements refuse for different reasons, and the aggregate
        # keeps both — collapsing them would be exactly the merge it must not do.
        answers = _every(_coexistence()).emulators_for("n64")
        assert [a.answer.caveats[0].code for a in answers] == [
            "emulator-catalogue-unreadable",
            "emulator-catalogue-unavailable",
        ]

    def test_systems(self):
        asked, direct = self._pairs(lambda h: h.systems())
        assert [systems_contract(s) for s in asked] == [systems_contract(s) for s in direct]

    def test_rom_location(self):
        asked, direct = self._pairs(lambda h: h.rom_location("n64"))
        assert [rom_placement_contract(p) for p in asked] == [
            rom_placement_contract(p) for p in direct
        ]

    def test_health(self):
        asked, direct = self._pairs(lambda h: h.health())
        assert asked == direct

    def test_health_serializes_through_the_aggregate(self):
        # The aggregate delegates, so the health findings' shape should come
        # for free — checked rather than assumed, since health is the one
        # answer whose serializer the label carries too.
        asked, direct = self._pairs(lambda h: h.health())
        assert [health_contract(h) for h in asked] == [health_contract(h) for h in direct]

    def test_the_label_carries_the_findings_in_full(self):
        machine = FixtureMachine({RETRODECK_JSON: '{"paths": {"rd_home_path": "/gone"}}'})
        answered = atlas.EveryInstallation(_handles(machine)).health()[0]
        serialized = installation_answers_contract([answered], health_contract)[0]
        assert serialized["installation"]["health"] == serialized["answer"]["issues"]

    def test_firmware_for_core(self):
        asked, direct = self._pairs(lambda h: h.firmware_for_core(core_so=CORE_SO))
        assert [firmware_contract(f) for f in asked] == [firmware_contract(f) for f in direct]

    def test_firmware_for_system(self):
        asked, direct = self._pairs(lambda h: h.firmware_for_system(system="gb"))
        assert [firmware_contract(f) for f in asked] == [firmware_contract(f) for f in direct]

    def test_firmware_inventory(self):
        asked, direct = self._pairs(lambda h: h.firmware_inventory())
        assert [firmware_contract(f) for f in asked] == [firmware_contract(f) for f in direct]

    def test_identify_firmware(self):
        asked, direct = self._pairs(lambda h: h.identify_firmware(md5=MD5))
        assert [identification_contract(i) for i in asked] == [
            identification_contract(i) for i in direct
        ]


def _parameters(method) -> list[tuple[str, object, object]]:
    """One question's arguments: name, kind and default — the callable part.

    Return annotations differ by design (a handle answers one, the aggregate a
    tuple of labelled ones), so they stay out of the comparison.
    """
    return [
        (p.name, p.kind, p.default)
        for p in inspect.signature(method).parameters.values()
        if p.name != "self"
    ]


class TestTheSurfaceMirrorsTheProtocol:
    """The aggregate asks what a handle can be asked — checked, not asserted by hand.

    A question added to the :class:`~atlas.installations.Installation` protocol
    and forgotten here would leave callers of the aggregate route with no way to
    ask it, silently. ``root`` is deliberately not fanned out: it names the
    installation rather than reading its state, and the label answers it.
    """

    IDENTITY = {"root"}

    def _questions(self) -> set[str]:
        return {
            name
            for name, member in vars(atlas.Installation).items()
            if not name.startswith("_") and callable(member)
        } - self.IDENTITY

    def test_the_protocol_has_questions_to_mirror(self):
        assert "save_location" in self._questions()

    def test_every_protocol_question_is_on_the_aggregate(self):
        missing = sorted(q for q in self._questions() if not hasattr(atlas.EveryInstallation, q))
        assert missing == []

    def test_every_question_takes_what_the_protocol_takes(self):
        # Names alone are not the mirror: a question whose arguments drifted —
        # a subject that became keyword-only on one side, a modifier that went
        # positional on the other — would still pass the check above while the
        # aggregate silently refused calls the handle route accepts.
        differing = {
            question: (
                str(inspect.signature(getattr(atlas.Installation, question))),
                str(inspect.signature(getattr(atlas.EveryInstallation, question))),
            )
            for question in sorted(self._questions())
            if _parameters(getattr(atlas.Installation, question))
            != _parameters(getattr(atlas.EveryInstallation, question))
        }
        assert differing == {}


class TestTheContract:
    """The serialized aggregate answer: the label, and the question's own form."""

    def _first(self) -> dict[str, Any]:
        answers = _every(_coexistence()).save_location(content_path=ROM)
        return installation_answers_contract(answers, placement_contract)[0]

    def test_each_entry_is_the_label_and_the_answer(self):
        assert set(self._first()) == {"installation", "answer"}

    def test_the_label_is_the_installation_contract(self):
        handle = _handles(_coexistence())[0]
        assert self._first()["installation"] == installation_contract(handle)

    def test_the_answer_is_the_questions_own_contract(self):
        handle = _handles(_coexistence())[0]
        direct = handle.save_location(content_path=ROM)
        assert self._first()["answer"] == placement_contract(direct)


class TestTheOneCallForm:
    """``every_installation(home)`` is ``detect(home)``, ready to be asked."""

    def test_it_asks_what_detect_found(self):
        machine = _coexistence()
        aggregate = atlas.every_installation(HOME, machine)
        assert [i.kind for i in aggregate.installations] == [i.kind for i in _handles(machine)]

    def test_an_empty_machine_stays_empty(self):
        assert atlas.every_installation(HOME, FixtureMachine({})).installations == ()
