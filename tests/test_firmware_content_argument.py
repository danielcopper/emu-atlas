"""Tests for the game a firmware question names (issue #661): its path must be absolute.

``content_path`` is the path a launch is handed, and a relative one names
nothing without a working folder of its own. Every entry point that takes it —
each handle's ``firmware_for_core`` and ``firmware_for_system``, the aggregate
and the CLI — refuses one at the call, the way ``launch_command`` refuses it,
before anything is read. What a value that is taken does to an answer is the
vectors' to pin (``vectors/machines/disc-region.json``).
"""

from __future__ import annotations

import functools
import re
from typing import Any, Callable

import pytest

import atlas
from atlas.cli import run
from atlas.machine import FixtureMachine
from atlas.placement import checked_content_path

HOME = "/home/deck"

# A relative spelling, one that only looks anchored, and the empty string.
NOT_ABSOLUTE = ("roms/psx/Game.m3u", "./Game.cue", "")

HANDLES: dict[str, Callable[[FixtureMachine], Any]] = {
    "retrodeck": lambda machine: atlas.RetroDeck(HOME, machine),
    "emudeck": lambda machine: atlas.EmuDeck(HOME, machine),
    "bare_retroarch_native": lambda machine: atlas.BareRetroArchNative(HOME, machine),
    "bare_retroarch_flatpak": lambda machine: atlas.BareRetroArchFlatpak(HOME, machine),
}


def _questions(target: Any) -> dict[str, Callable[..., Any]]:
    return {
        "firmware_for_core": functools.partial(target.firmware_for_core, "swanstation_libretro.so"),
        "firmware_for_system": functools.partial(target.firmware_for_system, "psx"),
    }


def _refusal(value: str) -> str:
    return re.escape(f"content_path must be an absolute path, got {value!r}")


@pytest.mark.parametrize("value", NOT_ABSOLUTE)
@pytest.mark.parametrize("question", ["firmware_for_core", "firmware_for_system"])
@pytest.mark.parametrize("kind", sorted(HANDLES))
def test_every_handle_refuses_a_content_path_that_is_not_absolute(kind, question, value):
    ask = _questions(HANDLES[kind](FixtureMachine({})))[question]
    with pytest.raises(ValueError, match=_refusal(value)):
        ask(content_path=value)


@pytest.mark.parametrize("value", NOT_ABSOLUTE)
@pytest.mark.parametrize("question", ["firmware_for_core", "firmware_for_system"])
def test_the_aggregate_refuses_one_with_no_installation_to_ask(question, value):
    # No handle at all: the refusal is the aggregate's own.
    ask = _questions(atlas.EveryInstallation([]))[question]
    with pytest.raises(ValueError, match=_refusal(value)):
        ask(content_path=value)


def test_an_absolute_path_and_none_pass():
    assert checked_content_path("/roms/psx/Game.m3u") == "/roms/psx/Game.m3u"
    assert checked_content_path(None) is None


@pytest.mark.parametrize("command", [["firmware-for-core", "--core", "swanstation_libretro.so"],
                                     ["firmware-for-system", "--system", "psx"]])
def test_the_cli_refuses_a_relative_content_path_as_a_usage_error(command, capsys):
    argv = [*command, "--content", "roms/psx/Game.m3u"]
    machine = FixtureMachine({})
    with pytest.raises(SystemExit) as stopped:
        run(argv, home=HOME, machine=machine)
    assert stopped.value.code == 2
    assert "must be an absolute path" in capsys.readouterr().err
