"""Tests for atlas.oddities — rule cards and their application in the resolver."""

from __future__ import annotations

import json

import pytest

import atlas
from atlas.oddities import load_audit, load_oddities, lookup_card

HOME = "/home/deck"
RETRODECK_JSON = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
RETRODECK_CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch.cfg"
OPTIONS_CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch-core-options.cfg"
FLYCAST_GAME_OPT = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/config/Flycast/Shenmue (Europe).opt"

RD_JSON = '{"paths": {"rd_home_path": "/mnt/sd/retrodeck", "saves_path": "/mnt/sd/retrodeck/saves"}}'
SAVES_KEEP = "/mnt/sd/retrodeck/saves/.keep"
CFG = (
    'savefile_directory = "/mnt/sd/retrodeck/saves"\n'
    'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
    'system_directory = "/mnt/sd/retrodeck/bios"\n'
    'global_core_options = "true"\n'
    'libretro_directory = "/app/cores"\n'
)
DEPLOY = "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/cores"
ROM = "/mnt/sd/retrodeck/roms/dreamcast/Shenmue (Europe).gdi"


class TestCardLookup:
    def test_by_so_basename(self):
        card = lookup_card(so_basename="flycast_libretro.so", library_name=None)
        assert card is not None and card.key == "flycast"

    def test_by_library_name(self):
        card = lookup_card(so_basename=None, library_name="Flycast")
        assert card is not None and card.key == "flycast"

    def test_no_card_for_ordinary_core(self):
        assert lookup_card(so_basename="mgba_libretro.so", library_name="mGBA") is None

    def test_card_modes_and_default(self):
        card = lookup_card(so_basename="flycast_libretro.so", library_name=None)
        assert card is not None
        assert card.option_key == "reicast_per_content_vmus"
        assert card.option_default == "disabled"
        assert set(card.modes) == {"disabled", "VMU A1", "All VMUs"}
        assert card.modes["disabled"].files is not None
        assert card.modes["VMU A1"].files is None  # unverified, never guessed


def _retrodeck(files, **kwargs):
    machine = atlas.FixtureMachine(files, **kwargs)
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
        assert p.granularity.value == "shared-card"
        assert ("VMU A1", "per-game-file") in p.granularity.alternatives
        assert p.granularity.options_file == OPTIONS_CFG

    def test_option_absent_uses_core_default(self):
        p = _flycast_query({RETRODECK_JSON: RD_JSON, RETRODECK_CFG: CFG})
        assert p.root_kind == atlas.ROOT_SYSTEM_DIRECTORY
        assert p.granularity is not None
        assert p.granularity.option_value == "disabled"
        assert "core default" in p.granularity.option_source

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
        assert p.granularity.value == "per-game-file"
        assert p.file_set.state == "unknown"
        assert any(c.code == atlas.CAVEAT_FILENAMES_UNVERIFIED for c in p.caveats)

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
        assert "Shenmue (Europe).opt" in p.granularity.option_source

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
        assert p.granularity is not None and p.granularity.value == "shared-card"
        assert any(c.code == atlas.CAVEAT_UNKNOWN_OPTION_VALUE and c.data["value"] == "something new" for c in p.caveats)

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
        assert by_so is not None and by_so.key == "pcsx2"
        assert by_name is not None and by_name.key == "pcsx2"

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
        assert g is not None and g.value == "shared-card"
        assert g.option_key == "pcsx2_shared_memory_cards"
        assert g.option_value == "enabled"
        assert ("disabled", "per-game-file") in g.alternatives

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
        assert p.granularity is not None and p.granularity.value == "per-game-file"

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
        assert mismatch and mismatch[0].data["card"] == "flycast"

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
        assert p.granularity is not None and p.granularity.value == "per-game-file"
        assert ("shared", "shared-card") in p.granularity.alternatives

    def test_shared_mode_switches_subdir(self):
        p = self._query({OPTIONS_CFG: 'opera_nvram_storage = "shared"\n'})
        assert p.dir == "/mnt/sd/retrodeck/saves/opera/shared"
        assert p.granularity is not None and p.granularity.value == "shared-card"

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
                "schema": 2,
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
                "schema": 2,
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
                "schema": 2,
                "cores": {"x": {"verdict": "standard", "note": "source-verified", "verified": {}}},
            }
        )
        with pytest.raises(ValueError, match="per_game_capable"):
            load_audit(text)

    def test_non_boolean_per_game_capability_is_rejected(self):
        text = json.dumps(
            {
                "schema": 2,
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
                "schema": 2,
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
        assert stale and stale[0].data["arrangement_live"] == "0.11.0"
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
        assert stale and stale[0].data["core_live"] == "fffffff"

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
        assert stale and stale[0].data["verification"] == "runtime-version-unknown"
        assert "arrangement_version" in stale[0].data["missing"]
        assert "core_library_version" in stale[0].data["missing"]

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
        machine = atlas.FixtureMachine(
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
        assert stale and stale[0].data["arrangement"] == "emudeck"
