"""Tests for atlas._question — one question reads and parses each configuration layer once."""

from __future__ import annotations

import inspect
from collections import Counter
from typing import Callable, Iterable, Mapping, cast

import pytest

import atlas
import atlas.retroarch_cfg
from atlas._question import once, one_question
from atlas.machine import FixtureFileSpec, FixtureMachine, ReadResult
from atlas.retroarch_cfg import ParsedCfg, parse_cfg_once

HOME = "/home/deck"
INFO_DIR = "/usr/share/libretro/info"
CORE_DIR = "/usr/lib/libretro"
ROM = "/roms/psx/Game.cue"

# The PlayStation cores of the firmware vectors: SwanStation reads core options
# to compose the names it opens, once per option, which is what made the
# options file a layer read again for every option a question needed.
SWANSTATION_INFO = (
    'display_name = "Sony - PlayStation (SwanStation)"\nsystemname = "PlayStation"\n'
    'firmware_count = "2"\nfirmware0_desc = "scph5501.bin (PS1 US BIOS)"\n'
    'firmware0_path = "scph5501.bin"\nfirmware0_opt = "true"\n'
    'firmware1_desc = "psxonpsp660.bin (PSP PS1 BIOS)"\nfirmware1_path = "psxonpsp660.bin"\n'
    'firmware1_opt = "true"\n'
)
REARMED_INFO = (
    'display_name = "Sony - PlayStation (PCSX ReARMed)"\nsystemname = "PlayStation"\n'
    'firmware_count = "1"\nfirmware0_desc = "scph5501.bin (PS1 US BIOS)"\n'
    'firmware0_path = "scph5501.bin"\nfirmware0_opt = "true"\n'
)


def _global_cfg(info_dir: str, core_dir: str) -> str:
    """The global cfg: every key a question here reads from it, set.

    Set so that each reader really reads this file — and with a line RetroArch
    drops, so that the dropped lines travel through the shared parse too.
    """
    return (
        'system_directory = "/bios"\n'
        f'libretro_directory = "{core_dir}"\n'
        f'libretro_info_path = "{info_dir}"\n'
        'savefile_directory = "/saves"\n'
        'savestate_directory = "/states"\n'
        'screenshot_directory = "/shots"\n'
        'auto_overrides_enable = "true"\n'
        'global_core_options = "false"\n'
        'savefiles_in_content_dir="true"\n'
    )


GLOBAL_CFG = _global_cfg(INFO_DIR, CORE_DIR)
OPTIONS = 'swanstation_BIOS_PathNTSCU = "scph5501.bin"\n'


class _CountingMachine(FixtureMachine):
    """A fixture machine that counts every text read, by path."""

    def __init__(self, files: Mapping[str, FixtureFileSpec], *, dirs: Iterable[str]) -> None:
        super().__init__(files, dirs=dirs)
        self.reads: Counter[str] = Counter()

    def read_text(self, path: str) -> ReadResult:
        self.reads[path] += 1
        return super().read_text(path)


def _files(cfg_dir: str, *, info_dir: str = INFO_DIR, core_dir: str = CORE_DIR) -> dict[str, FixtureFileSpec]:
    """One installation's files: the global cfg, the options file beside it, two cores."""
    return {
        f"{cfg_dir}/retroarch.cfg": _global_cfg(info_dir, core_dir),
        f"{cfg_dir}/retroarch-core-options.cfg": OPTIONS,
        f"{info_dir}/swanstation_libretro.info": SWANSTATION_INFO,
        f"{core_dir}/swanstation_libretro.so": {"status": "invalid-text"},
        f"{info_dir}/pcsx_rearmed_libretro.info": REARMED_INFO,
        f"{core_dir}/pcsx_rearmed_libretro.so": {"status": "invalid-text"},
    }


RA_DIR = f"{HOME}/.config/retroarch"
RD_DIR = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch"
RD_FILES = "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files"
ED_DIR = f"{HOME}/.var/app/org.libretro.RetroArch/config/retroarch"

# One machine per handle that overrides a question, each with the global cfg
# and the options file where that handle reads them.
HANDLES: dict[str, tuple[Callable[[FixtureMachine], atlas.Installation], str, dict[str, FixtureFileSpec]]] = {
    "bare": (lambda m: atlas.BareRetroArchNative(HOME, m), RA_DIR, _files(RA_DIR)),
    "retrodeck": (
        lambda m: atlas.RetroDeck(HOME, m),
        RD_DIR,
        {
            # RetroDECK's cfg spells its cores sandbox-side, under /app.
            **_files(RD_DIR, info_dir=f"{RD_FILES}/cores", core_dir=f"{RD_FILES}/cores"),
            f"{RD_DIR}/retroarch.cfg": _global_cfg("/app/cores", "/app/cores"),
            # Its firmware_for_system enumerates the catalogue's entries.
            f"{RD_FILES}/retrodeck/components/es-de/share/es-de/resources/systems/linux/es_systems.xml": (
                '<?xml version="1.0"?>\n<systemList>\n  <system>\n    <name>psx</name>\n'
                "    <path>%ROMPATH%/psx</path>\n    <extension>.cue</extension>\n"
                '    <command label="SwanStation">retroarch -L /app/cores/swanstation_libretro.so %ROM%</command>\n'
                '    <command label="PCSX ReARMed">retroarch -L /app/cores/pcsx_rearmed_libretro.so %ROM%</command>\n'
                "  </system>\n</systemList>\n"
            ),
            f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json": (
                '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/saves"}}'
            ),
        },
    ),
    "emudeck": (
        lambda m: atlas.EmuDeck(HOME, m),
        ED_DIR,
        {**_files(ED_DIR), f"{HOME}/.config/EmuDeck/settings.sh": 'romsPath="/roms"\nsavesPath="/saves"\n'},
    ),
}

QUESTIONS: dict[str, Callable[[atlas.Installation], object]] = {
    "firmware_for_core": lambda h: h.firmware_for_core("swanstation_libretro.so", verify=True),
    "firmware_for_system": lambda h: h.firmware_for_system("psx", verify=True),
    "firmware_inventory": lambda h: h.firmware_inventory(verify=True),
    "identify_firmware": lambda h: h.identify_firmware(md5="00" * 16),
    "savefile_location": lambda h: h.savefile_location(
        content_path=ROM, core_so="swanstation_libretro.so"
    ),
    "savestate_location": lambda h: h.savestate_location(
        content_path=ROM, core_so="swanstation_libretro.so"
    ),
    "screenshot_location": lambda h: h.screenshot_location(
        content_path=ROM, core_so="swanstation_libretro.so"
    ),
    "texture_pack_location": lambda h: h.texture_pack_location(
        content_path=ROM, core_so="swanstation_libretro.so"
    ),
    "mod_location": lambda h: h.mod_location(
        content_path=ROM, core_so="swanstation_libretro.so"
    ),
    "soft_patch_candidates": lambda h: h.soft_patch_candidates(
        ROM, core_so="swanstation_libretro.so"
    ),
}

# The catalogue questions of a bare RetroArch, which derives its catalogue from
# the very context the firmware route reads. The same questions read no
# RetroArch configuration on RetroDECK and EmuDeck here, and every other
# question about the installation itself parses a file once at most anyway.
BARE_CATALOGUE_QUESTIONS: dict[str, Callable[[atlas.Installation], object]] = {
    "systems": lambda h: h.systems(),
    "systems_for_platform": lambda h: h.systems_for_platform("igdb", "ps"),
    "platform_ids": lambda h: h.platform_ids("psx"),
    "emulators_for": lambda h: h.emulators_for("psx", content_path=ROM),
}

ENTRY_QUESTIONS: dict[str, Callable[[atlas.EmulatorEntry], object]] = {
    "savefile_location": lambda e: e.savefile_location(content_path=ROM),
    "savestate_location": lambda e: e.savestate_location(content_path=ROM),
    "texture_pack_location": lambda e: e.texture_pack_location(content_path=ROM),
    "mod_location": lambda e: e.mod_location(content_path=ROM),
}


@pytest.fixture
def parses(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    """How often RetroArch's parser ran over each text."""
    counted: Counter[str] = Counter()
    parse = atlas.retroarch_cfg.parse_cfg

    def counting(text: str) -> ParsedCfg:
        counted[text] += 1
        return parse(text)

    monkeypatch.setattr(atlas.retroarch_cfg, "parse_cfg", counting)
    return counted


def _parsed_more_than_once(files: Mapping[str, FixtureFileSpec], parses: Counter[str]) -> dict[str, int]:
    """Each fixture file RetroArch's parser ran over more than once, with its count."""
    by_path = {path: parses[text] for path, text in files.items() if isinstance(text, str)}
    return {path: count for path, count in by_path.items() if count > 1}


def _machine(files: Mapping[str, FixtureFileSpec]) -> _CountingMachine:
    return _CountingMachine(files, dirs=["/bios", "/saves", "/states", "/shots"])


class TestOneQuestionParsesEachLayerOnce:
    """Issue #589: the cost of a question no longer grows with how often it asks a layer."""

    @pytest.mark.parametrize("question", QUESTIONS)
    @pytest.mark.parametrize("handle", HANDLES)
    def test_each_file_is_parsed_at_most_once(self, handle: str, question: str, parses: Counter[str]):
        build, cfg_dir, files = HANDLES[handle]
        QUESTIONS[question](build(_machine(files)))
        assert parses[cast(str, files[f"{cfg_dir}/retroarch.cfg"])] == 1
        assert _parsed_more_than_once(files, parses) == {}

    @pytest.mark.parametrize("question", BARE_CATALOGUE_QUESTIONS)
    def test_a_bare_catalogue_question_parses_each_file_at_most_once(self, question: str, parses: Counter[str]):
        build, cfg_dir, files = HANDLES["bare"]
        BARE_CATALOGUE_QUESTIONS[question](build(_machine(files)))
        assert parses[cast(str, files[f"{cfg_dir}/retroarch.cfg"])] == 1
        assert _parsed_more_than_once(files, parses) == {}

    @pytest.mark.parametrize("question", ENTRY_QUESTIONS)
    def test_a_catalogue_entry_asks_one_question_too(self, question: str, parses: Counter[str]):
        build, cfg_dir, files = HANDLES["retrodeck"]
        (entry, *_) = build(_machine(files)).emulators_for("psx").entries
        parses.clear()
        ENTRY_QUESTIONS[question](entry)
        assert parses[cast(str, files[f"{cfg_dir}/retroarch.cfg"])] == 1
        assert _parsed_more_than_once(files, parses) == {}

    @pytest.mark.parametrize("handle", HANDLES)
    def test_an_options_file_asked_for_several_options_is_read_and_parsed_once(
        self, handle: str, parses: Counter[str]
    ):
        build, cfg_dir, files = HANDLES[handle]
        machine = _CountingMachine(files, dirs=["/bios"])
        build(machine).firmware_for_system("psx", verify=True)
        options = f"{cfg_dir}/retroarch-core-options.cfg"
        assert (machine.reads[options], parses[OPTIONS]) == (1, 1)

    def test_the_next_question_reads_the_machine_again(self, parses: Counter[str]):
        build, cfg_dir, files = HANDLES["bare"]
        machine = _CountingMachine(files, dirs=["/bios"])
        handle = build(machine)
        handle.firmware_for_system("psx")
        handle.firmware_for_system("psx")
        assert machine.reads[f"{cfg_dir}/retroarch.cfg"] == 2
        assert parses[GLOBAL_CFG] == 2


# The code every wrapper one_question makes runs: a method runs as one
# question exactly when its code is this one. ``__wrapped__`` would not do —
# every functools.wraps sets it.
_ASKED = one_question(lambda: None).__code__


class TestEveryPublicMethodIsOneQuestion:
    @pytest.mark.parametrize(
        "cls",
        [
            atlas.RetroDeck,
            atlas.EmuDeck,
            atlas.BareRetroArchNative,
            atlas.BareRetroArchFlatpak,
            atlas.EmulatorEntry,
        ],
    )
    def test_every_public_method_runs_as_one_question(self, cls: type):
        methods = {
            name: member
            for name, member in inspect.getmembers(cls, callable)
            if not name.startswith("_") and not isinstance(inspect.getattr_static(cls, name), property)
        }
        assert methods
        unscoped = [name for name, member in methods.items() if getattr(member, "__code__", None) is not _ASKED]
        assert unscoped == []


class TestOnce:
    def test_outside_a_question_every_call_computes(self):
        calls: list[int] = []
        once("key", lambda: calls.append(1))
        once("key", lambda: calls.append(1))
        assert calls == [1, 1]

    def test_within_a_question_a_key_computes_once(self):
        calls: list[str] = []

        @one_question
        def ask() -> list[str]:
            return [once(key, lambda: calls.append(key) or key) for key in ("a", "b", "a", "b")]

        assert ask() == ["a", "b", "a", "b"]
        assert calls == ["a", "b"]

    def test_nothing_outlives_the_question(self):
        calls: list[int] = []

        @one_question
        def ask() -> None:
            once("key", lambda: calls.append(1))

        ask()
        ask()
        assert calls == [1, 1]

    def test_a_question_asked_inside_another_shares_its_memo(self):
        calls: list[int] = []

        @one_question
        def inner() -> None:
            once("key", lambda: calls.append(1))

        @one_question
        def outer() -> None:
            once("key", lambda: calls.append(1))
            inner()
            once("key", lambda: calls.append(1))

        outer()
        assert calls == [1]

    def test_a_question_that_raises_drops_its_memo(self):
        calls: list[int] = []

        @one_question
        def failing() -> None:
            once("key", lambda: calls.append(1))
            raise RuntimeError

        with pytest.raises(RuntimeError):
            failing()
        once("key", lambda: calls.append(1))
        assert calls == [1, 1]


class TestParseCfgOnce:
    def test_a_caller_cannot_change_what_the_next_caller_reads(self):
        @one_question
        def ask() -> tuple[ParsedCfg, ParsedCfg]:
            first = parse_cfg_once(GLOBAL_CFG)
            first.values["system_directory"] = "/elsewhere"
            del first.values["libretro_directory"]
            return first, parse_cfg_once(GLOBAL_CFG)

        _, second = ask()
        assert second.values["system_directory"] == "/bios"
        assert second.values["libretro_directory"] == CORE_DIR

    def test_it_answers_what_the_parser_answers(self):
        @one_question
        def ask() -> ParsedCfg:
            parse_cfg_once(GLOBAL_CFG)
            return parse_cfg_once(GLOBAL_CFG)

        assert ask() == atlas.retroarch_cfg.parse_cfg(GLOBAL_CFG)
