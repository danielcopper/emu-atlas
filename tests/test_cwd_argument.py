"""Tests for the working folder a save, savestate or firmware question takes (issue #581).

The value fills the ``cwd`` hole, so only an absolute host path can fill it —
anything else is the caller's mistake, and every entry point that takes the
argument refuses it at the call, before anything is read and whether or not
the answer would have depended on it. What a value that is taken does to an
answer is the vectors' to pin (``vectors/machines``), each beside the vector
that asks the same question without one.
"""

from __future__ import annotations

import functools
import re
from typing import Any, Callable

import pytest

import atlas
from atlas.esde import KIND_LIBRETRO, KIND_STANDALONE, EmulatorSpec
from atlas.machine import FixtureMachine
from atlas.placement import checked_cwd

HOME = "/home/deck"
RETRODECK_JSON = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
ES_SYSTEMS = (
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components/"
    "es-de/share/es-de/resources/systems/linux/es_systems.xml"
)
CATALOGUE = (
    '<?xml version="1.0"?>\n<systemList>\n  <system><name>nds</name><path>%ROMPATH%/nds</path>\n'
    '    <command label="melonDS (Standalone)">%EMULATOR_MELONDS% %ROM%</command>\n'
    "  </system>\n</systemList>\n"
)

# A relative spelling, one that only looks anchored, and the empty string: none
# of them names a folder by itself.
NOT_ABSOLUTE = ("launch", "./launch", "")

HANDLES: dict[str, Callable[[FixtureMachine], Any]] = {
    "retrodeck": lambda machine: atlas.RetroDeck(HOME, machine),
    "emudeck": lambda machine: atlas.EmuDeck(HOME, machine),
    "bare_retroarch_native": lambda machine: atlas.BareRetroArchNative(HOME, machine),
    "bare_retroarch_flatpak": lambda machine: atlas.BareRetroArchFlatpak(HOME, machine),
}

LIBRETRO_SPEC = EmulatorSpec(
    system="gba",
    label="mGBA",
    kind=KIND_LIBRETRO,
    core_so="mgba_libretro.so",
    command="%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/mgba_libretro.so %ROM%",
    provenance="test",
)
STANDALONE_SPEC = EmulatorSpec(
    system="nds",
    label="melonDS (Standalone)",
    kind=KIND_STANDALONE,
    core_so=None,
    command="%EMULATOR_MELONDS% %ROM%",
    provenance="test",
)


def _handle_questions(handle: Any) -> dict[str, Callable[..., Any]]:
    """Every question a handle answers that takes ``cwd``, with its other arguments bound."""
    return {
        "savefile_location": handle.savefile_location,
        "savestate_location": handle.savestate_location,
        "entry_savefile_location (libretro)": functools.partial(
            handle.entry_savefile_location, LIBRETRO_SPEC
        ),
        "entry_savefile_location (standalone)": functools.partial(
            handle.entry_savefile_location, STANDALONE_SPEC
        ),
        "entry_savestate_location (libretro)": functools.partial(
            handle.entry_savestate_location, LIBRETRO_SPEC
        ),
        "entry_savestate_location (standalone)": functools.partial(
            handle.entry_savestate_location, STANDALONE_SPEC
        ),
        "firmware_for_core": functools.partial(handle.firmware_for_core, "mgba_libretro.so"),
        "firmware_for_system": functools.partial(handle.firmware_for_system, "xbox"),
        "firmware_inventory": handle.firmware_inventory,
    }


_QUESTION_NAMES = tuple(_handle_questions(atlas.RetroDeck(HOME, FixtureMachine({}))))


def _refusal(value: str) -> str:
    """The message a refused value carries: the argument's name, then the value as written."""
    return re.escape(f"cwd must be an absolute path, got {value!r}")


@pytest.mark.parametrize("value", NOT_ABSOLUTE)
@pytest.mark.parametrize("question", _QUESTION_NAMES)
@pytest.mark.parametrize("kind", sorted(HANDLES))
def test_every_handle_question_refuses_a_working_folder_that_is_not_absolute(kind, question, value):
    ask = _handle_questions(HANDLES[kind](FixtureMachine({})))[question]
    with pytest.raises(ValueError, match=_refusal(value)):
        ask(cwd=value)


def _catalogue_entry() -> atlas.EmulatorEntry:
    machine = FixtureMachine(
        {RETRODECK_JSON: '{"paths": {"rd_home_path": "/mnt/sd/retrodeck"}}', ES_SYSTEMS: CATALOGUE}
    )
    return atlas.RetroDeck(HOME, machine).emulators_for("nds").entries[0]


@pytest.mark.parametrize("value", NOT_ABSOLUTE)
@pytest.mark.parametrize("question", ["savefile_location", "savestate_location"])
def test_a_catalogue_entry_refuses_a_working_folder_that_is_not_absolute(question, value):
    ask = getattr(_catalogue_entry(), question)
    with pytest.raises(ValueError, match=_refusal(value)):
        ask(cwd=value)


_AGGREGATE_QUESTIONS = {
    "savefile_location": lambda every: every.savefile_location,
    "savestate_location": lambda every: every.savestate_location,
    "firmware_for_core": lambda every: functools.partial(every.firmware_for_core, "mgba_libretro.so"),
    "firmware_for_system": lambda every: functools.partial(every.firmware_for_system, "xbox"),
    "firmware_inventory": lambda every: every.firmware_inventory,
}


@pytest.mark.parametrize("value", NOT_ABSOLUTE)
@pytest.mark.parametrize("question", sorted(_AGGREGATE_QUESTIONS))
def test_the_aggregate_refuses_a_working_folder_with_no_installation_to_ask(question, value):
    # No handle at all: the refusal is the aggregate's own, not one a handle
    # would have raised had there been one to ask.
    ask = _AGGREGATE_QUESTIONS[question](atlas.EveryInstallation([]))
    with pytest.raises(ValueError, match=_refusal(value)):
        ask(cwd=value)


class TestCheckedCwd:
    """The one rule every entry point applies."""

    @pytest.mark.parametrize("value", NOT_ABSOLUTE)
    def test_a_value_that_is_not_absolute_raises_naming_the_argument_and_the_value(self, value):
        with pytest.raises(ValueError, match=_refusal(value)):
            checked_cwd(value)

    def test_an_absolute_value_is_taken_as_written(self):
        assert checked_cwd("/home/deck/launch/") == "/home/deck/launch/"

    def test_no_value_is_not_given(self):
        assert checked_cwd(None) is None
