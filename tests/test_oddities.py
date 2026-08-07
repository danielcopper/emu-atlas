"""Tests for atlas.oddities — rule cards and their application in the resolver."""

from __future__ import annotations

import importlib.resources
import json

import pytest

import atlas
from atlas.machine import FixtureMachine
from atlas.oddities import load_audit, load_oddities, lookup_card
from atlas.placement import (
    GRANULARITIES,
    GRANULARITY_PER_GAME_FILE,
    GRANULARITY_PER_GAME_FILES,
    GRANULARITY_SHARED_CARD,
    ROOT_KINDS,
)

HOME = "/home/deck"
RETRODECK_JSON = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
RETRODECK_CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch.cfg"
OPTIONS_CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch-core-options.cfg"
FLYCAST_GAME_OPT = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/config/Flycast/Dreamcast Game (Europe).opt"
FLYCAST_CORE_OVERRIDE = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/config/Flycast/Flycast.cfg"
FLYCAST_CORE_OPT = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/config/Flycast/Flycast.opt"

RD_JSON = '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}'
SAVES_KEEP = "/mnt/sd/retrodeck/saves/.keep"
CFG = (
    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
    'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
    'system_directory = "/mnt/sd/retrodeck/bios"\n'
    'global_core_options = "true"\n'
    'libretro_directory = "/app/cores"\n'
)
CFG_WITHOUT_GLOBAL_OPTS = CFG.replace('global_core_options = "true"\n', "")
DEPLOY = "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/cores"
ROM_STEM = "Dreamcast Game (Europe)"
ROM = f"/mnt/sd/retrodeck/roms/dreamcast/{ROM_STEM}.gdi"


class TestCardLookup:
    def test_by_so_basename(self):
        card = lookup_card(so_basename="flycast_libretro.so", library_name=None)
        assert card is not None
        assert card.key == "flycast"

    def test_by_library_name(self):
        card = lookup_card(so_basename=None, library_name="Flycast")
        assert card is not None
        assert card.key == "flycast"

    def test_no_card_for_ordinary_core(self):
        assert lookup_card(so_basename="mgba_libretro.so", library_name="mGBA") is None

    def test_card_modes_and_default(self):
        card = lookup_card(so_basename="flycast_libretro.so", library_name=None)
        assert card is not None
        assert card.option_key == "reicast_per_content_vmus"
        assert card.option_default == "disabled"
        assert set(card.modes) == {"disabled", "VMU A1", "All VMUs"}
        assert card.modes["disabled"].files is not None
        # 'All VMUs' names its files through the content's own id — a template,
        # kept as one: atlas states the shape, never the id. 'VMU A1' moves one
        # port and leaves the rest on the shared card, which one root cannot say.
        assert card.modes["All VMUs"].files == (
            "<save_id>.A1.bin",
            "<save_id>.B1.bin",
            "<save_id>.C1.bin",
            "<save_id>.D1.bin",
        )
        assert card.modes["All VMUs"].files_without_save_id == (
            "<rom_stem>.A1.bin",
            "<rom_stem>.B1.bin",
            "<rom_stem>.C1.bin",
            "<rom_stem>.D1.bin",
        )
        assert card.modes["VMU A1"].files is None
        assert card.modes["VMU A1"].also_under == "system_directory"


def _retrodeck(files, **kwargs):
    machine = FixtureMachine(files, **kwargs)
    return atlas.RetroDeck(HOME, machine)


def _flycast_query(files):
    rd = _retrodeck(files, cores={f"{DEPLOY}/flycast_libretro.so": {"library_name": "Flycast"}})
    return rd.save_location(content_path=ROM, core_so="flycast_libretro.so")


class TestFlycastResolution:
    def test_default_shared_vmus_in_system_directory(self):
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "disabled"\n',
                "/mnt/sd/retrodeck/bios/dc/vmu_save_A1.bin": "v",
            }
        )
        assert p.dir == "/mnt/sd/retrodeck/bios/dc"
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY
        assert p.file_set.state == "observed"
        assert p.file_set.files == ("vmu_save_A1.bin",)
        assert p.granularity is not None
        assert p.granularity.value == GRANULARITY_SHARED_CARD
        assert ("VMU A1", GRANULARITY_PER_GAME_FILE) in p.granularity.alternatives
        assert p.granularity.options_file == OPTIONS_CFG

    def test_option_absent_uses_core_default(self):
        p = _flycast_query({RETRODECK_JSON: RD_JSON, RETRODECK_CFG: CFG})
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY
        assert p.granularity is not None
        assert p.granularity.option_value == "disabled"
        assert "core default" in p.granularity.option_provenance

    def test_no_vmus_present_is_declared_from_card(self):
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "disabled"\n',
            }
        )
        assert p.file_set.state == "declared"
        assert "vmu_save_A1.bin" in p.file_set.files

    def test_slot2_vmus_are_observed_when_present(self):
        # The card's observe list is wider than the declared defaults: slot-2
        # VMUs exist only when a port's slot 2 is configured as VMU (M2).
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "disabled"\n',
                "/mnt/sd/retrodeck/bios/dc/vmu_save_A1.bin": "v",
                "/mnt/sd/retrodeck/bios/dc/vmu_save_A2.bin": "v",
            }
        )
        assert p.file_set.files == ("vmu_save_A1.bin", "vmu_save_A2.bin")
        assert p.file_set.complete is False

    def test_per_game_mode_switches_root_and_granularity(self):
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "VMU A1"\n',
                SAVES_KEEP: "",
            }
        )
        assert p.dir == "/mnt/sd/retrodeck/saves/dreamcast"
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY
        assert p.granularity is not None
        assert p.granularity.value == GRANULARITY_PER_GAME_FILE
        # Only port A1 goes per-content here (oslib.cpp:40-41) — B1..D1 and the
        # console flash keep using the shared card, so the save lies under two
        # roots and the card, which states one, states no file set at all.
        assert p.file_set.state == "unknown"
        assert p.needs == ()
        assert not any(c.code == atlas.CAVEAT_FILENAMES_UNVERIFIED for c in p.caveats)
        spans = [c for c in p.caveats if c.code == atlas.CAVEAT_FILE_SET_SPANS_ROOTS]
        assert [dict(c.data) for c in spans] == [
            {"card": "flycast", "mode": "VMU A1", "also_under": "system_directory"}
        ]

    def test_all_vmus_declares_one_file_per_connected_port(self):
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "All VMUs"\n',
                SAVES_KEEP: "",
                "/mnt/sd/retrodeck/saves/dreamcast/.keep": "",
            }
        )
        assert p.dir == "/mnt/sd/retrodeck/saves/dreamcast"
        assert p.granularity is not None
        assert p.granularity.value == GRANULARITY_PER_GAME_FILES
        assert p.file_set.state == "declared"
        assert p.file_set.files == (
            "<save_id>.A1.bin",
            "<save_id>.B1.bin",
            "<save_id>.C1.bin",
            "<save_id>.D1.bin",
        )
        # A template names the shape, never the whole save: the console flash
        # stays in system_directory, and slot-2 VMUs appear when configured.
        assert p.file_set.complete is False
        assert p.needs == ("save_id",)

    def test_all_vmus_hands_over_both_spellings_of_the_names(self):
        # The id branch applies only to console content with a readable header
        # (oslib.cpp:44); everything else is named after the ROM (:62). atlas
        # cannot decide that — it states the id-keyed set and carries the
        # alternative, filled as far as it can fill it, in the caveat's data.
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "All VMUs"\n',
                SAVES_KEEP: "",
                "/mnt/sd/retrodeck/saves/dreamcast/.keep": "",
            }
        )
        conditional = [c for c in p.caveats if c.code == atlas.CAVEAT_FILENAMES_CONTENT_CONDITIONAL]
        assert len(conditional) == 1
        data = dict(conditional[0].data)
        assert data["card"] == "flycast"
        assert data["mode"] == "All VMUs"
        assert data["files"].split(", ") == list(p.file_set.files)
        assert data["files_without_save_id"].split(", ") == [
            f"{ROM_STEM}.A1.bin",
            f"{ROM_STEM}.B1.bin",
            f"{ROM_STEM}.C1.bin",
            f"{ROM_STEM}.D1.bin",
        ]

    def test_all_vmus_marks_the_port_set_as_the_console_one(self):
        # Not every content difference is a spelling: a Naomi board connects
        # VMUs on B1/C1 only (maple_cfg.cpp:246-253), so two of the four names
        # can never exist there. The scope is data, not prose.
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "All VMUs"\n',
                SAVES_KEEP: "",
                "/mnt/sd/retrodeck/saves/dreamcast/.keep": "",
            }
        )
        data = dict(
            next(c for c in p.caveats if c.code == atlas.CAVEAT_FILENAMES_CONTENT_CONDITIONAL).data
        )
        assert data["files_established_for"] == "console"
        assert "maple_cfg.cpp:246-253" in data["citation"]

    def test_the_alternative_stays_a_template_without_a_content_path(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "All VMUs"\n',
                SAVES_KEEP: "",
            },
            cores={f"{DEPLOY}/flycast_libretro.so": {"library_name": "Flycast"}},
        )
        p = rd.save_location(core_so="flycast_libretro.so")
        conditional = [c for c in p.caveats if c.code == atlas.CAVEAT_FILENAMES_CONTENT_CONDITIONAL]
        assert conditional
        assert conditional[0].data["files_without_save_id"].startswith("<rom_stem>.A1.bin")

    def test_the_card_provenance_rides_along_on_the_standard_route(self):
        # A mode switch MOVES the save — the shared cards stay behind stale —
        # and no field of the placement can say that. The card's provenance is
        # where it is written, so it reaches this route too, not only the
        # system_directory one.
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "All VMUs"\n',
                SAVES_KEEP: "",
            }
        )
        assert any(s.startswith("rule card 'flycast' governs this placement") for s in p.sources)

    def test_the_save_id_hole_is_never_filled_from_the_content_name(self):
        # The id lives in the disc header, which atlas does not read — a file
        # lying there under the ROM's name is not this save (REVIEW 13b).
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "All VMUs"\n',
                SAVES_KEEP: "",
                "/mnt/sd/retrodeck/saves/dreamcast/MK-5105950.A1.bin": "v",
            }
        )
        assert p.file_set.state == "declared"
        assert "MK-5105950.A1.bin" not in p.file_set.files
        assert p.needs == ("save_id",)

    def test_game_opt_file_wins_over_global(self):
        # runloop.c validate_per_core_options: the game .opt is THE source.
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                FLYCAST_GAME_OPT: 'reicast_per_content_vmus = "VMU A1"\n',
                OPTIONS_CFG: 'reicast_per_content_vmus = "disabled"\n',
            }
        )
        assert p.granularity is not None
        assert p.granularity.option_value == "VMU A1"
        assert "Dreamcast Game (Europe).opt" in p.granularity.option_provenance

    def test_unknown_option_value_applies_core_default_mode(self):
        # RetroArch's option manager keeps the core default on an invalid
        # persisted value (REVIEW M1) — for Flycast that is shared VMUs, not
        # the standard rule.
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'reicast_per_content_vmus = "something new"\n',
            }
        )
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY
        assert p.granularity is not None
        assert p.granularity.value == GRANULARITY_SHARED_CARD
        assert any(c.code == atlas.CAVEAT_UNKNOWN_OPTION_VALUE and c.data["value"] == "something new" for c in p.caveats)

    def test_override_can_switch_the_game_opt_layer_off(self):
        # game_specific_options is read from the MERGED config: the core's own
        # retro_set_environment (runloop.c:5037) drives runloop_init_core_options
        # and its settings->bools.game_specific_options read (runloop.c:1529),
        # one step after config_load_override merged the overrides (:5003). So
        # the override below really does keep RetroArch out of the game .opt.
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                FLYCAST_CORE_OVERRIDE: 'game_specific_options = "false"\n',
                FLYCAST_GAME_OPT: 'reicast_per_content_vmus = "VMU A1"\n',
                OPTIONS_CFG: 'reicast_per_content_vmus = "disabled"\n',
            }
        )
        assert p.granularity is not None
        assert p.granularity.option_value == "disabled"
        assert p.granularity.options_file == OPTIONS_CFG

    def test_a_dropped_game_specific_options_line_leaves_the_game_opt_on(self):
        # The line sets nothing, so the default (true) stands and the game .opt
        # governs after all — stated, because nothing else in the answer shows it.
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                FLYCAST_CORE_OVERRIDE: 'game_specific_options="false"\n',
                FLYCAST_GAME_OPT: 'reicast_per_content_vmus = "VMU A1"\n',
                OPTIONS_CFG: 'reicast_per_content_vmus = "disabled"\n',
                SAVES_KEEP: "",
            }
        )
        assert p.granularity is not None
        assert p.granularity.option_value == "VMU A1"
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_CFG_LINE_DROPPED] == [
            {"key": "game_specific_options", "line": 'game_specific_options="false"'}
        ]

    def test_a_dropped_core_options_path_line_leaves_the_default_file_governing(self):
        # The line sets nothing, so the options file stays the default one
        # beside retroarch.cfg and the file the line names is never opened.
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG + 'core_options_path="/mnt/sd/retrodeck/elsewhere.cfg"\n',
                "/mnt/sd/retrodeck/elsewhere.cfg": 'reicast_per_content_vmus = "VMU A1"\n',
                OPTIONS_CFG: 'reicast_per_content_vmus = "disabled"\n',
                SAVES_KEEP: "",
            }
        )
        assert p.granularity is not None
        assert p.granularity.option_value == "disabled"
        assert p.granularity.options_file == OPTIONS_CFG
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_CFG_LINE_DROPPED] == [
            {"key": "core_options_path", "line": 'core_options_path="/mnt/sd/retrodeck/elsewhere.cfg"'}
        ]

    def test_a_dropped_global_core_options_line_leaves_the_per_core_opt_governing(self):
        # Dropped, so the compile-time default (false) stands and the per-core
        # .opt IS consulted — the opposite of what the line asks for.
        p = _flycast_query(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG_WITHOUT_GLOBAL_OPTS + 'global_core_options="true"\n',
                FLYCAST_CORE_OPT: 'reicast_per_content_vmus = "VMU A1"\n',
                OPTIONS_CFG: 'reicast_per_content_vmus = "disabled"\n',
                SAVES_KEEP: "",
            }
        )
        assert p.granularity is not None
        assert p.granularity.option_value == "VMU A1"
        assert p.granularity.options_file == FLYCAST_CORE_OPT
        assert [c.data for c in p.caveats if c.code == atlas.CAVEAT_CFG_LINE_DROPPED] == [
            {"key": "global_core_options", "line": 'global_core_options="true"'}
        ]

    def test_ordinary_core_has_no_granularity(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                "/mnt/sd/retrodeck/roms/gba/Game.zip": "",
            },
            cores={f"{DEPLOY}/mgba_libretro.so": {"library_name": "mGBA"}},
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/gba/Game.zip", core_so="mgba_libretro.so")
        assert p.granularity is None


class TestLRPS2Card:
    def test_lookup_by_so_and_library_name(self):
        by_so = lookup_card(so_basename="pcsx2_libretro.so", library_name=None)
        by_name = lookup_card(so_basename=None, library_name="LRPS2")
        assert by_so is not None
        assert by_so.key == "pcsx2"
        assert by_name is not None
        assert by_name.key == "pcsx2"

    def test_default_shared_memcards_in_system_directory(self):
        # pcsx2_shared_memory_cards defaults to "enabled" (libretro_core_options.h:169)
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                "/mnt/sd/retrodeck/roms/ps2/Game.iso": "",
            },
            cores={f"{DEPLOY}/pcsx2_libretro.so": {"library_name": "LRPS2"}},
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/ps2/Game.iso", core_so="pcsx2_libretro.so")
        assert p.dir == "/mnt/sd/retrodeck/bios/pcsx2/memcards"
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY
        assert p.file_set.state == "declared"
        assert p.file_set.files == ("Mcd001.ps2", "Mcd002.ps2")
        g = p.granularity
        assert g is not None
        assert g.value == GRANULARITY_SHARED_CARD
        assert g.option_key == "pcsx2_shared_memory_cards"
        assert g.option_value == "enabled"
        assert ("disabled", GRANULARITY_PER_GAME_FILE) in g.alternatives

    def test_per_content_mode_names_card_after_rom_stem(self):
        # main.cpp:2154-2166 + Pcsx2Config.cpp:995-997 — slot 1 = <rom_stem>.ps2
        # in the RetroArch save directory (standard resolution applies).
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                OPTIONS_CFG: 'pcsx2_shared_memory_cards = "disabled"\n',
                SAVES_KEEP: "",
                "/mnt/sd/retrodeck/roms/ps2/Gran Turismo 4 (USA).iso": "",
            },
            cores={f"{DEPLOY}/pcsx2_libretro.so": {"library_name": "LRPS2"}},
        )
        p = rd.save_location(
            content_path="/mnt/sd/retrodeck/roms/ps2/Gran Turismo 4 (USA).iso", core_so="pcsx2_libretro.so"
        )
        assert p.dir == "/mnt/sd/retrodeck/saves/ps2"
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY
        assert p.file_set.state == "declared"
        assert p.file_set.files == ("Gran Turismo 4 (USA).ps2",)
        assert p.granularity is not None
        assert p.granularity.value == GRANULARITY_PER_GAME_FILE

    def test_existing_memcard_is_observed(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                "/mnt/sd/retrodeck/roms/ps2/Game.iso": "",
                "/mnt/sd/retrodeck/bios/pcsx2/memcards/Mcd001.ps2": "m",
            },
            cores={f"{DEPLOY}/pcsx2_libretro.so": {"library_name": "LRPS2"}},
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/ps2/Game.iso", core_so="pcsx2_libretro.so")
        assert p.file_set.state == "observed"
        assert p.file_set.files == ("Mcd001.ps2",)


class TestFeatureDetection:
    """Card applicability decided by what the core observably registers.

    Key registered → card confirmed, version drift demoted to provenance.
    Key gone → the card describes another generation and steps aside.
    Options not captured → unknown; the version comparison keeps working.
    """

    FLYCAST_OPTIONS = {
        "reicast_per_content_vmus": {
            "default": "disabled",
            "values": ["disabled", "VMU A1", "All VMUs"],
        }
    }

    def _flycast(self, core_spec, files=None, rd_json=RD_JSON):
        base = {
            RETRODECK_JSON: rd_json,
            RETRODECK_CFG: CFG,
            ROM: "",
            SAVES_KEEP: "",
        }
        base.update(files or {})
        rd = _retrodeck(base, cores={f"{DEPLOY}/flycast_libretro.so": core_spec})
        return rd.save_location(content_path=ROM, core_so="flycast_libretro.so")

    def test_registered_key_confirms_card_despite_version_drift(self):
        # Version drifted (fffffff ≠ pinned 1dac369), but the governing option
        # is observably registered — no false alarm, decision on evidence.
        p = self._flycast(
            {
                "library_name": "Flycast",
                "library_version": "fffffff",
                "options": self.FLYCAST_OPTIONS,
            },
            rd_json='{"version": "0.10.9b", "paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}',
        )
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY  # card applied
        assert not any(c.code == atlas.CAVEAT_UNVERIFIED_VERSION for c in p.caveats)
        assert any("feature-detected" in s for s in p.sources)
        assert any("version records differ" in s for s in p.sources)

    def test_missing_key_retires_the_card(self):
        # The LRPS2 lesson as a Flycast fixture: the core registers a
        # different option vocabulary — the card must not be applied.
        p = self._flycast(
            {
                "library_name": "Flycast",
                "options": {"flycast_vmu_layout": {"default": "new", "values": ["new", "old"]}},
            }
        )
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY  # standard frame
        assert p.granularity is None
        mismatch = [c for c in p.caveats if c.code == atlas.CAVEAT_CARD_GENERATION_MISMATCH]
        assert mismatch
        assert mismatch[0].data["card"] == "flycast"

    def test_uncaptured_options_fall_back_to_version_comparison(self):
        p = self._flycast({"library_name": "Flycast"})
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY  # card applied as before
        stale = [c for c in p.caveats if c.code == atlas.CAVEAT_UNVERIFIED_VERSION]
        assert stale  # runtime-version-unknown — unchanged behaviour

    def test_live_default_outranks_card_default(self):
        # The registered default says per-game VMUs — a generation that
        # flipped its default. No options file present: the live default
        # governs, not the card's shipped-generation copy.
        options = {
            "reicast_per_content_vmus": {
                "default": "VMU A1",
                "values": ["disabled", "VMU A1", "All VMUs"],
            }
        }
        p = self._flycast({"library_name": "Flycast", "options": options})
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY
        assert p.granularity is not None
        assert p.granularity.option_value == "VMU A1"

    def test_stored_value_validated_against_live_definition(self):
        # Persisted junk value: RetroArch keeps the core default — confirmed
        # against the LIVE value set, applied via the card's default mode.
        p = self._flycast(
            {"library_name": "Flycast", "options": self.FLYCAST_OPTIONS},
            files={OPTIONS_CFG: 'reicast_per_content_vmus = "sideways"\n'},
        )
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY
        assert any(c.code == atlas.CAVEAT_UNKNOWN_OPTION_VALUE for c in p.caveats)

    def test_live_value_unknown_to_card_is_generation_drift(self):
        # The live core registers a value the card has never heard of, and
        # the user selected it — the card lags the generation; never guess.
        options = {
            "reicast_per_content_vmus": {
                "default": "disabled",
                "values": ["disabled", "VMU A1", "All VMUs", "VMU A1+A2"],
            }
        }
        p = self._flycast(
            {"library_name": "Flycast", "options": options},
            files={OPTIONS_CFG: 'reicast_per_content_vmus = "VMU A1+A2"\n'},
        )
        assert p.granularity is None
        assert any(c.code == atlas.CAVEAT_CARD_GENERATION_MISMATCH for c in p.caveats)


class TestOperaCard:
    """3DO NVRAM: the core nests opera/per_game|shared under the save directory."""

    OPERA_CFG = (
        'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
        'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
        'global_core_options = "true"\n'
        'libretro_directory = "/app/cores"\n'
    )
    ROM_3DO = "/mnt/sd/retrodeck/roms/3do/Game.chd"

    def _query(self, files, cfg=None):
        base = {
            RETRODECK_JSON: RD_JSON,
            RETRODECK_CFG: cfg or self.OPERA_CFG,
            self.ROM_3DO: "",
            SAVES_KEEP: "",
        }
        base.update(files)
        rd = _retrodeck(base, cores={f"{DEPLOY}/opera_libretro.so": {"library_name": "Opera"}})
        return rd.save_location(content_path=self.ROM_3DO, core_so="opera_libretro.so")

    def test_default_per_game_nests_subdir_under_save_dir(self):
        p = self._query({})
        assert p.dir == "/mnt/sd/retrodeck/saves/opera/per_game"
        assert p.root_kind == atlas.ROOT_SAVEFILE_DIRECTORY
        assert p.granularity is not None
        assert p.granularity.value == GRANULARITY_PER_GAME_FILE
        assert ("shared", GRANULARITY_SHARED_CARD) in p.granularity.alternatives

    def test_shared_mode_switches_subdir(self):
        p = self._query({OPTIONS_CFG: 'opera_nvram_storage = "shared"\n'})
        assert p.dir == "/mnt/sd/retrodeck/saves/opera/shared"
        assert p.granularity is not None
        assert p.granularity.value == GRANULARITY_SHARED_CARD

    def test_subdir_follows_the_sorted_directory(self):
        # GET_SAVE_DIRECTORY hands the core the redirected (sorted) dir
        # (runloop.c:2001, 8977) — the core's subtree nests under it.
        cfg = self.OPERA_CFG.replace(
            'sort_savefiles_by_content_enable = "false"', 'sort_savefiles_by_content_enable = "true"'
        )
        p = self._query({"/mnt/sd/retrodeck/saves/3do/.keep": ""}, cfg=cfg)
        assert p.dir == "/mnt/sd/retrodeck/saves/3do/opera/per_game"

    def test_version_parameterized_files_are_observed_not_declared(self):
        p = self._query({"/mnt/sd/retrodeck/saves/opera/per_game/Game.0.srm": "nv"})
        assert p.file_set.state == "observed"
        assert p.file_set.files == ("Game.0.srm",)


class TestAuditVerdictCaveats:
    """A verdict a caller cannot see is a verdict that did not happen.

    ``granularity`` is ``None`` for every core without a rule card, so the
    verdict has to arrive as a caveat or not at all.
    """

    def _query(self, *, core_so, library_name, system, rom, extra_files=None):
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,
                RETRODECK_CFG: CFG,
                f"/mnt/sd/retrodeck/roms/{system}/{rom}": "",
                f"/mnt/sd/retrodeck/saves/{system}/.keep": "",
                **(extra_files or {}),
            },
            cores={f"{DEPLOY}/{core_so}": {"library_name": library_name}},
        )
        return rd.save_location(content_path=f"/mnt/sd/retrodeck/roms/{system}/{rom}", core_so=core_so)

    def test_multi_option_verdict_names_the_options_that_decide_granularity(self):
        p = self._query(
            core_so="swanstation_libretro.so",
            library_name="SwanStation",
            system="psx",
            rom="Vagrant Story (USA).chd",
        )
        assert p.granularity is None
        stated = [c for c in p.caveats if c.code == atlas.CAVEAT_CORE_MULTI_OPTION]
        assert stated
        assert stated[0].data["core"] == "swanstation"
        assert stated[0].data["options"].split(", ") == [
            "swanstation_MemoryCards_Card1Type",
            "swanstation_MemoryCards_Card2Type",
            "swanstation_MemoryCards_UsePlaylistTitle",
        ]

    def test_standard_verdict_stays_silent(self):
        # The pair from issue #23: same empty granularity, different meaning.
        p = self._query(
            core_so="mgba_libretro.so",
            library_name="mGBA",
            system="gba",
            rom="Golden Sun (USA).zip",
        )
        assert p.granularity is None
        assert p.caveats == ()

    def test_standard_dir_verdict_stays_silent_and_observes_the_core_written_set(self):
        # standard-dir means the file set is core-owned, not that anything is
        # withheld: no core option governs it, the files are content-keyed, and
        # the literal <rom_stem>.* observation reports all of them.
        stem = "Sega Rally Championship (USA)"
        p = self._query(
            core_so="mednafen_saturn_libretro.so",
            library_name="Beetle Saturn",
            system="saturn",
            rom=f"{stem}.chd",
            extra_files={
                f"/mnt/sd/retrodeck/saves/saturn/{stem}.bcr": "backup",
                f"/mnt/sd/retrodeck/saves/saturn/{stem}.bkr": "backup",
                f"/mnt/sd/retrodeck/saves/saturn/{stem}.smpc": "smpc",
            },
        )
        assert p.caveats == ()
        assert p.file_set.state == "observed"
        assert p.file_set.files == (f"{stem}.bcr", f"{stem}.bkr", f"{stem}.smpc")


class TestStrictLoaders:
    """Packaged data is validated, never coerced — a broken build fails loudly."""

    def test_unknown_oddities_schema_is_rejected(self):
        with pytest.raises(ValueError, match="schema"):
            load_oddities('{"schema": 99, "cores": {}}')

    def test_missing_audit_schema_is_rejected(self):
        with pytest.raises(ValueError, match="schema"):
            load_audit('{"cores": {}}')

    def test_unknown_verdict_is_rejected(self):
        text = json.dumps(
            {
                "schema": 3,
                "cores": {
                    "x": {
                        "verdict": "fine-probably",
                        "per_game_capable": None,
                        "note": "unproven",
                        "verified": {},
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="verdict"):
            load_audit(text)

    def test_audit_capability_and_note_are_loaded(self):
        text = json.dumps(
            {
                "schema": 3,
                "cores": {
                    "x": {
                        "verdict": "standard",
                        "per_game_capable": True,
                        "note": "source-verified",
                        "verified": {},
                    }
                },
            }
        )
        entry = load_audit(text)["x"]
        assert entry.per_game_capable is True
        assert entry.note == "source-verified"

    def test_missing_per_game_capability_is_rejected(self):
        text = json.dumps(
            {
                "schema": 3,
                "cores": {"x": {"verdict": "standard", "note": "source-verified", "verified": {}}},
            }
        )
        with pytest.raises(ValueError, match="per_game_capable"):
            load_audit(text)

    def test_non_boolean_per_game_capability_is_rejected(self):
        text = json.dumps(
            {
                "schema": 3,
                "cores": {
                    "x": {
                        "verdict": "standard",
                        "per_game_capable": 1,
                        "note": "source-verified",
                        "verified": {},
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="boolean or null"):
            load_audit(text)

    @pytest.mark.parametrize("note", ["", None, 1])
    def test_invalid_audit_note_is_rejected(self, note):
        text = json.dumps(
            {
                "schema": 3,
                "cores": {
                    "x": {
                        "verdict": "standard",
                        "per_game_capable": None,
                        "note": note,
                        "verified": {},
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="note"):
            load_audit(text)

    def test_multi_option_save_options_are_loaded(self):
        text = json.dumps(
            {
                "schema": 3,
                "cores": {
                    "x": {
                        "verdict": "multi-option",
                        "per_game_capable": True,
                        "note": "source-verified",
                        "save_options": ["x_card1", "x_card2"],
                        "verified": {},
                    }
                },
            }
        )
        assert load_audit(text)["x"].save_options == ("x_card1", "x_card2")

    def test_multi_option_without_save_options_is_rejected(self):
        # The verdict IS the claim that options decide the answer — an entry
        # that cannot name them would make the caveat say "unknown" again.
        text = json.dumps(
            {
                "schema": 3,
                "cores": {
                    "x": {
                        "verdict": "multi-option",
                        "per_game_capable": True,
                        "note": "source-verified",
                        "verified": {},
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="save_options"):
            load_audit(text)

    def test_save_options_on_another_verdict_are_rejected(self):
        text = json.dumps(
            {
                "schema": 3,
                "cores": {
                    "x": {
                        "verdict": "standard",
                        "per_game_capable": True,
                        "note": "source-verified",
                        "save_options": ["x_card1"],
                        "verified": {},
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="save_options"):
            load_audit(text)

    def test_non_string_save_options_are_rejected(self):
        text = json.dumps(
            {
                "schema": 3,
                "cores": {
                    "x": {
                        "verdict": "multi-option",
                        "per_game_capable": True,
                        "note": "source-verified",
                        "save_options": [1],
                        "verified": {},
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="save_options"):
            load_audit(text)

    def test_non_boolean_complete_is_rejected(self):
        text = json.dumps(
            {
                "schema": 1,
                "cores": {
                    "x": {
                        "identifiers": {"so": ["x_libretro.so"], "library_name": ["X"]},
                        "saves": {
                            "modes": {
                                "always": {
                                    "root": "system_directory",
                                    "granularity": "shared-card",
                                    "complete": "false",
                                }
                            }
                        },
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="complete"):
            load_oddities(text)

    @pytest.mark.parametrize(
        "name",
        [
            "<game_id>.bin",  # well-formed, unknown
            "<rom_stem.A1.bin",  # never closes — a token scan would pass this
            "<<rom_stem>>.A1.bin",  # nested
            "rom_stem>.A1.bin",  # never opens
        ],
    )
    def test_a_malformed_or_unknown_template_is_rejected(self, name):
        # The guarantee the card format documents: a typo cannot travel into a
        # filename atlas states as fact. Only removal of the known tokens can
        # decide that — matching well-formed <…> would let three of these pass.
        text = self._mode_card({"granularity": "per-game-file", "files": [name]})
        with pytest.raises(ValueError, match="unknown template"):
            load_oddities(text)

    @pytest.mark.parametrize("field", ["files", "observe", "files_without_save_id"])
    def test_an_empty_file_list_is_rejected(self, field):
        # A declared set with no files is shape-identical to 'unknown' but
        # labelled 'declared' — and an empty alternative is an empty promise.
        mode = {"granularity": "per-game-file", "files": ["<save_id>.A1.bin"], field: []}
        with pytest.raises(ValueError, match="empty list"):
            load_oddities(self._mode_card(mode))

    def test_an_empty_file_name_is_rejected(self):
        text = self._mode_card({"granularity": "per-game-file", "files": [""]})
        with pytest.raises(ValueError, match="not a file name"):
            load_oddities(text)

    def test_a_second_root_equal_to_the_first_is_rejected(self):
        text = self._mode_card({"granularity": "per-game-file", "also_under": "savefile_directory"})
        with pytest.raises(ValueError, match="own root"):
            load_oddities(text)

    def test_a_scope_without_a_declared_set_is_rejected(self):
        text = self._mode_card({"granularity": "per-game-file", "files_established_for": "console"})
        with pytest.raises(ValueError, match="files_established_for"):
            load_oddities(text)

    @pytest.mark.parametrize("field", ["files_established_for", "files_citation"])
    def test_an_empty_scope_or_citation_is_rejected(self, field):
        # Both reach the caller as caveat data; an empty one states nothing.
        mode = {
            "granularity": "per-game-file",
            "files": ["<save_id>.A1.bin"],
            "files_established_for": "console",
            field: "",
        }
        with pytest.raises(ValueError, match=field):
            load_oddities(self._mode_card(mode))

    def test_a_citation_without_a_scope_is_rejected(self):
        text = self._mode_card(
            {
                "granularity": "per-game-file",
                "files": ["<save_id>.A1.bin"],
                "files_citation": "somewhere.cpp:1",
            }
        )
        with pytest.raises(ValueError, match="files_citation"):
            load_oddities(text)

    @pytest.mark.parametrize("field", ["files", "observe"])
    def test_unknown_file_template_is_rejected(self, field):
        # A token nobody fills would be stated as literal text in a filename —
        # the card language is the placement's hole vocabulary, nothing else.
        text = json.dumps(
            {
                "schema": 1,
                "cores": {
                    "x": {
                        "identifiers": {"so": ["x_libretro.so"], "library_name": ["X"]},
                        "saves": {
                            "modes": {
                                "always": {
                                    "root": "savefile_directory",
                                    "granularity": "per-game-file",
                                    "files": ["<save_id>.bin"],
                                    field: ["<game_id>.bin"],
                                }
                            }
                        },
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="unknown template"):
            load_oddities(text)

    def test_known_file_templates_are_kept_verbatim(self):
        text = json.dumps(
            {
                "schema": 1,
                "cores": {
                    "x": {
                        "identifiers": {"so": ["x_libretro.so"], "library_name": ["X"]},
                        "saves": {
                            "modes": {
                                "always": {
                                    "root": "savefile_directory",
                                    "granularity": "per-game-file",
                                    "files": ["<save_id>.A1.bin", "<rom_stem>.srm"],
                                }
                            }
                        },
                    }
                },
            }
        )
        assert load_oddities(text)[0].modes["always"].files == ("<save_id>.A1.bin", "<rom_stem>.srm")

    def _mode_card(self, mode):
        return json.dumps(
            {
                "schema": 1,
                "cores": {
                    "x": {
                        "identifiers": {"so": ["x_libretro.so"], "library_name": ["X"]},
                        "saves": {"modes": {"always": {"root": "savefile_directory", **mode}}},
                    }
                },
            }
        )

    def test_a_mode_that_spans_roots_cannot_declare_files(self):
        # The field exists because one list cannot describe a two-root save.
        text = self._mode_card(
            {
                "granularity": "per-game-file",
                "files": ["<save_id>.A1.bin"],
                "also_under": "system_directory",
            }
        )
        with pytest.raises(ValueError, match="also_under"):
            load_oddities(text)

    def test_an_unknown_second_root_is_rejected(self):
        text = self._mode_card({"granularity": "per-game-file", "also_under": "somewhere_else"})
        with pytest.raises(ValueError, match="also_under"):
            load_oddities(text)

    def test_the_id_less_alternative_needs_an_id_keyed_set_to_be_the_alternative_to(self):
        text = self._mode_card(
            {
                "granularity": "per-game-file",
                "files": ["<rom_stem>.srm"],
                "files_without_save_id": ["<rom_stem>.A1.bin"],
            }
        )
        with pytest.raises(ValueError, match="files_without_save_id"):
            load_oddities(text)

    def test_the_id_less_alternative_cannot_name_an_id(self):
        text = self._mode_card(
            {
                "granularity": "per-game-file",
                "files": ["<save_id>.A1.bin"],
                "files_without_save_id": ["<save_id>.A1.bin"],
            }
        )
        with pytest.raises(ValueError, match="without an id"):
            load_oddities(text)

    def test_an_unknown_granularity_is_rejected(self):
        # It reaches the caller as the contractual Granularity.value, so a
        # misspelling would be stated as this machine's actual grouping.
        text = self._mode_card({"granularity": "per-gaem-file", "files": ["<rom_stem>.srm"]})
        with pytest.raises(ValueError, match="granularity"):
            load_oddities(text)

    def test_the_card_vocabularies_are_the_placement_s_own(self):
        # One definition per vocabulary, imported rather than respelled: a
        # second copy is how data and contract drift apart. Every value the
        # placement can carry loads from a card — the tests above cover the
        # other direction, that nothing else does.
        for granularity in GRANULARITIES:
            card = load_oddities(self._mode_card({"granularity": granularity, "files": ["a.srm"]}))[0]
            assert card.modes["always"].granularity == granularity
        for root in ROOT_KINDS:
            mode = {"root": root, "granularity": "shared-card", "files": ["a.srm"]}
            card = load_oddities(self._mode_card(mode))[0]
            assert card.modes["always"].root == root

    def test_unknown_mode_root_is_rejected(self):
        text = json.dumps(
            {
                "schema": 1,
                "cores": {
                    "x": {
                        "identifiers": {"so": ["x_libretro.so"], "library_name": ["X"]},
                        "saves": {"modes": {"always": {"root": "wherever", "granularity": "shared-card"}}},
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="root"):
            load_oddities(text)

    @pytest.mark.parametrize(
        "modes",
        [
            {},
            {"enabled": {"root": "savefile_directory", "granularity": "shared-card"}},
            {
                "always": {"root": "savefile_directory", "granularity": "shared-card"},
                "legacy": {"root": "savefile_directory", "granularity": "per-game-file"},
            },
        ],
    )
    def test_a_card_that_governs_nothing_must_state_the_one_mode_it_applies(self, modes):
        """Nothing selects between modes when no option governs the card.

        The resolver takes ``always`` and only ``always`` there, so any other
        shape describes behaviour that can never be applied — and the answer
        came back with no rule card behind it and no caveat either, because
        from the resolver's side nothing had gone wrong. A card ships with the
        code, so that is a build mistake and it fails the load.
        """
        text = json.dumps(
            {
                "schema": 1,
                "cores": {
                    "x": {
                        "identifiers": {"so": ["x_libretro.so"], "library_name": ["X"]},
                        "saves": {"modes": modes},
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="governing_option"):
            load_oddities(text)

    def test_the_same_modes_load_once_an_option_governs_them(self):
        # The refusal is about what can be selected, not about the modes.
        text = json.dumps(
            {
                "schema": 1,
                "cores": {
                    "x": {
                        "identifiers": {"so": ["x_libretro.so"], "library_name": ["X"]},
                        "saves": {
                            "governing_option": {"key": "x_storage", "default": "enabled"},
                            "modes": {"enabled": {"root": "savefile_directory", "granularity": "shared-card"}},
                        },
                    }
                },
            }
        )
        assert load_oddities(text)[0].option_key == "x_storage"

    @pytest.mark.parametrize("record", [{}, {"core_library_version": "1dac369", "date": "2026-08-05"}])
    def test_a_verification_record_that_pins_no_arrangement_version_is_rejected(self, record):
        """A record with no ``version`` can never drift, so it verifies forever.

        The drift check hangs on that field: with it null, no machine can
        disagree with the record, and the entry reads as *verified here* on
        every machine while pinning nothing at all — worse than never verified,
        because it claims the opposite.
        """
        text = json.dumps(
            {
                "schema": 3,
                "cores": {
                    "x": {
                        "verdict": "card",
                        "per_game_capable": True,
                        "note": "source-verified",
                        "verified": {"retrodeck": record},
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="version"):
            load_audit(text)

    def test_a_record_may_still_leave_the_core_version_unstated(self):
        # Plenty of cores report none, and the arrangement version already
        # bounds what was checked — this shape ships today.
        text = json.dumps(
            {
                "schema": 3,
                "cores": {
                    "x": {
                        "verdict": "card",
                        "per_game_capable": True,
                        "note": "source-verified",
                        "verified": {"retrodeck": {"version": "0.10.9b", "core_library_version": None}},
                    }
                },
            }
        )
        record = load_audit(text)["x"].verified["retrodeck"]
        assert record is not None
        assert record.core_library_version is None


class TestCardEvidence:
    """What each mode of a card rests on — stated per mode, never flattened."""

    def _raw_cards(self):
        text = (
            importlib.resources.files("atlas")
            .joinpath("data", "core_oddities.json")
            .read_text(encoding="utf-8")
        )
        return json.loads(text)["cores"]

    def test_every_mode_carries_its_own_provenance_status(self):
        for key, entry in self._raw_cards().items():
            modes = set(entry["saves"]["modes"])
            stated = set(entry["provenance"]["status"])
            assert stated == modes, (
                f"card {key!r}: provenance.status covers {sorted(stated)} but the card has "
                f"modes {sorted(modes)} — every mode says what its placement rests on"
            )

    def test_the_derived_flycast_mode_is_not_stated_as_observed(self):
        # 'All VMUs' was run on a real machine; 'VMU A1' follows from the same
        # code path and was never exercised. One status per mode is what keeps
        # a derivation from being laundered into an observation.
        status = self._raw_cards()["flycast"]["provenance"]["status"]
        assert status["All VMUs"].startswith("[V-live]")
        assert status["VMU A1"].startswith("[D]")

    def test_no_shipped_mode_claims_a_complete_file_set(self):
        """The premise three documents state — pinned so it cannot rot silently.

        ``FileSet.complete`` is documented as reserved: no card can yet
        establish which files a core writes *at all* for the active mode, so
        every answer carries ``False``. The day a card's evidence earns the
        claim, this test is what says so — and what to do about it. Deleting
        this test is then part of the work, not a nuisance.
        """
        claiming = sorted(
            f"{key}.{value}"
            for key, entry in self._raw_cards().items()
            for value, mode in entry["saves"]["modes"].items()
            if mode.get("complete")
        )
        assert claiming == [], (
            f"{claiming} now claims a complete file set, which retires the 'reserved' wording in "
            "atlas/placement.py (FileSet), docs/how-to-use.md ('Reading the file set') and "
            "vectors/README.md — update all three, then delete this test"
        )


class TestVerificationMatrix:
    def test_every_card_has_an_audit_entry(self):
        # Maintenance is enforced: a new card without a verification entry fails here.
        audit = load_audit()
        for card in load_oddities():
            assert card.key in audit, (
                f"rule card {card.key!r} has no entry in atlas/data/core_audit.json — "
                "add its verification record (see the file's spec)"
            )

    def test_matching_versions_carry_no_staleness_caveat(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: '{"version": "0.10.9b", "paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}',
                RETRODECK_CFG: CFG,
                "/mnt/sd/retrodeck/roms/dreamcast/Game.gdi": "",
            },
            cores={f"{DEPLOY}/flycast_libretro.so": {"library_name": "Flycast", "library_version": "1dac369"}},
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/dreamcast/Game.gdi", core_so="flycast_libretro.so")
        assert not any(c.code == atlas.CAVEAT_UNVERIFIED_VERSION for c in p.caveats)

    def test_arrangement_version_drift_fires_caveat(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: '{"version": "0.11.0", "paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}',
                RETRODECK_CFG: CFG,
                "/mnt/sd/retrodeck/roms/dreamcast/Game.gdi": "",
            },
            cores={f"{DEPLOY}/flycast_libretro.so": {"library_name": "Flycast"}},
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/dreamcast/Game.gdi", core_so="flycast_libretro.so")
        stale = [c for c in p.caveats if c.code == atlas.CAVEAT_UNVERIFIED_VERSION]
        assert stale
        assert stale[0].data["arrangement_live"] == "0.11.0"
        assert stale[0].data["arrangement_verified"] == "0.10.9b"

    def test_core_version_drift_fires_caveat(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: '{"version": "0.10.9b", "paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}',
                RETRODECK_CFG: CFG,
                "/mnt/sd/retrodeck/roms/dreamcast/Game.gdi": "",
            },
            cores={f"{DEPLOY}/flycast_libretro.so": {"library_name": "Flycast", "library_version": "fffffff"}},
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/dreamcast/Game.gdi", core_so="flycast_libretro.so")
        stale = [c for c in p.caveats if c.code == atlas.CAVEAT_UNVERIFIED_VERSION]
        assert stale
        assert stale[0].data["core_live"] == "fffffff"

    def test_unknown_live_versions_fail_closed(self):
        # The card is pinned to retrodeck 0.10.9b + core 1dac369, but this
        # machine exposes neither — missing evidence is not verification
        # (REVIEW M3).
        rd = _retrodeck(
            {
                RETRODECK_JSON: RD_JSON,  # no "version" key
                RETRODECK_CFG: CFG,
                "/mnt/sd/retrodeck/roms/dreamcast/Game.gdi": "",
            },
            cores={f"{DEPLOY}/flycast_libretro.so": {"library_name": "Flycast"}},  # no library_version
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/dreamcast/Game.gdi", core_so="flycast_libretro.so")
        stale = [c for c in p.caveats if c.code == atlas.CAVEAT_UNVERIFIED_VERSION]
        assert stale
        assert stale[0].data["verification"] == "runtime-version-unknown"
        assert "arrangement_version" in stale[0].data["missing"]
        assert "core_library_version" in stale[0].data["missing"]

    def test_an_empty_arrangement_version_is_unknown_not_drifted(self):
        # RetroDECK's shipped default config carries "version": "" and the
        # first run fills it in, so a machine can present one. It names no
        # version, and reporting a drift from the pin to nothing would be an
        # alarm about a value nobody wrote — the same reading of the key the
        # arrangement-level comparison uses.
        rd = _retrodeck(
            {
                RETRODECK_JSON: '{"version": "", "paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}',
                RETRODECK_CFG: CFG,
                "/mnt/sd/retrodeck/roms/dreamcast/Game.gdi": "",
            },
            cores={f"{DEPLOY}/flycast_libretro.so": {"library_name": "Flycast", "library_version": "1dac369"}},
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/dreamcast/Game.gdi", core_so="flycast_libretro.so")
        stale = [c for c in p.caveats if c.code == atlas.CAVEAT_UNVERIFIED_VERSION]
        assert stale
        assert stale[0].data["verification"] == "runtime-version-unknown"
        assert stale[0].data["missing"] == "arrangement_version"

    def test_confirmed_verification_lands_in_provenance(self):
        rd = _retrodeck(
            {
                RETRODECK_JSON: '{"version": "0.10.9b", "paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}',
                RETRODECK_CFG: CFG,
                "/mnt/sd/retrodeck/roms/dreamcast/Game.gdi": "",
            },
            cores={f"{DEPLOY}/flycast_libretro.so": {"library_name": "Flycast", "library_version": "1dac369"}},
        )
        p = rd.save_location(content_path="/mnt/sd/retrodeck/roms/dreamcast/Game.gdi", core_so="flycast_libretro.so")
        assert any("verified on retrodeck 0.10.9b" in s for s in p.sources)

    def test_unverified_arrangement_fires_caveat(self):
        # The flycast card was never verified on EmuDeck — the answer says so.
        machine = FixtureMachine(
            {
                f"{HOME}/.config/EmuDeck/settings.sh": 'savesPath="$HOME/Emulation/saves"\nromsPath="$HOME/Emulation/roms"\n',
                f"{HOME}/.var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg": (
                    'savefile_directory = "/home/deck/Emulation/saves/retroarch/saves"\n'
                    'system_directory = "/home/deck/Emulation/bios"\n'
                    'libretro_directory = "/cores"\n'
                ),
                f"{HOME}/Emulation/saves/.keep": "",
                f"{HOME}/Emulation/roms/dreamcast/Game.gdi": "",
            },
            cores={"/cores/flycast_libretro.so": {"library_name": "Flycast"}},
        )
        ed = atlas.EmuDeck(HOME, machine)
        p = ed.save_location(content_path=f"{HOME}/Emulation/roms/dreamcast/Game.gdi", core_so="flycast_libretro.so")
        stale = [c for c in p.caveats if c.code == atlas.CAVEAT_UNVERIFIED_VERSION]
        assert stale
        assert stale[0].data["arrangement"] == "emudeck"
