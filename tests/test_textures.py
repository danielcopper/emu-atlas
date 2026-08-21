"""Tests for atlas.textures — the texture cards, and how the resolver applies them."""

from __future__ import annotations

import json
from typing import Mapping, Sequence

import pytest

import atlas
from atlas.machine import FixtureMachine
from atlas.oddities import AuditEntry, lookup_audit
from atlas.placement import KEYINGS, ROOT_KINDS, TexturePlacement, Unresolved
from atlas.textures import (
    TEXTURE_PACKS_SCHEMA,
    XDG_BASES,
    load_standalone_texture_packs,
    load_texture_packs,
    lookup_standalone_texture_card,
    lookup_texture_card,
)
from tests.answers import texture_placed

HOME = "/home/deck"
RETRODECK_JSON = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
RETRODECK_CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch.cfg"
OPTIONS_CFG = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch-core-options.cfg"
ROOT = "/mnt/sd/retrodeck"
CORES = f"{ROOT}/cores"
RD_JSON = json.dumps(
    {"paths": {"rd_home_path": ROOT, "saves_path": f"{ROOT}/saves", "bios_path": f"{ROOT}/bios"}}
)
CFG = (
    f'libretro_directory = "{CORES}"\n'
    f'system_directory = "{ROOT}/bios"\n'
    f'savefile_directory = "{ROOT}/saves"\n'
)
FLYCAST_OPTIONS = {"reicast_custom_textures": {"default": "disabled", "values": ["disabled", "enabled"]}}


_Cores = Mapping[str, Mapping[str, object] | None]
DEPLOYED = {f"{CORES}/flycast_libretro.so": {"library_name": "Flycast", "options": FLYCAST_OPTIONS}}


def _machine(
    files: Mapping[str, str] | None = None,
    *,
    cores: _Cores | None = None,
    dirs: Sequence[str] = (),
    symlinks: Mapping[str, str] | None = None,
) -> FixtureMachine:
    """A RetroDECK machine with the Flycast core deployed unless told otherwise.

    ``cores={}`` is a machine that deploys none, which is a different machine
    from one that was not asked — so the default is ``None`` rather than a
    falsy empty mapping the two would collapse into.
    """
    return FixtureMachine(
        {RETRODECK_JSON: RD_JSON, RETRODECK_CFG: CFG, **(files or {})},
        cores=DEPLOYED if cores is None else cores,
        dirs=[f"{ROOT}/saves", *dirs],
        symlinks=symlinks,
    )


def _retrodeck(**kwargs) -> atlas.Installation:
    return atlas.detect(HOME, _machine(**kwargs))[0]


def _card(**textures) -> str:
    """A one-card table, with the fields every card needs already filled in."""
    return json.dumps(
        {
            "schema": TEXTURE_PACKS_SCHEMA,
            "cores": {
                "demo": {
                    "identifiers": {"library_name": ["Demo"]},
                    "textures": {"root": "system_directory", "subdir": "demo/textures", **textures},
                    "provenance": {"source": "[V] a citation"},
                }
            },
        }
    )


class TestTheShippedTableSaysWhatItCanStandBehind:
    """The packaged cards, held to the rules the loader cannot check alone."""

    def test_every_card_loads(self):
        assert {card.key for card in load_texture_packs()} >= {"flycast", "mesen"}

    @pytest.mark.parametrize("card", load_texture_packs(), ids=lambda c: c.key)
    def test_a_card_names_a_root_the_placement_grammar_knows(self, card):
        assert card.root in ROOT_KINDS

    @pytest.mark.parametrize("card", load_texture_packs(), ids=lambda c: c.key)
    def test_a_card_that_states_a_keying_cites_it(self, card):
        # The one field of this table no machine can contradict, so an uncited
        # one would be indistinguishable from a cited one where it is acted on.
        assert (card.keying is None) == (card.keying_citation is None)
        assert card.keying is None or card.keying in KEYINGS

    @pytest.mark.parametrize("card", load_texture_packs(), ids=lambda c: c.key)
    def test_a_card_carries_the_library_name_lookup_needs(self, card):
        # Lookup works from either side — the .so basename or what the binary
        # calls itself — and the entry route only ever has the first.
        assert card.library_names
        by_so = lookup_texture_card(so_basename=card.so_name, library_name=None)
        by_name = lookup_texture_card(so_basename=None, library_name=card.library_names[0])
        assert by_so is not None
        assert by_so.key == card.key
        assert by_name is not None
        assert by_name.key == card.key

    def test_a_card_states_either_a_switch_or_the_absence_of_one(self):
        # The two are contradictory claims about one build, and an answer would
        # have to pick. The loader refuses both together; this holds the shipped
        # cards to it.
        for card in load_texture_packs():
            assert card.option is None or card.absent_switch is None

    def test_an_absent_switch_is_pinned_to_the_core_it_was_proven_against(self):
        # "Nothing in this build writes it" is the strongest negative in the
        # file, and a build is exactly what could add a writer — so the claim
        # names the one it was established on, or it would travel to every
        # future generation unexamined.
        for card in load_texture_packs():
            if card.absent_switch is None:
                continue
            assert card.absent_switch.verified_core

    def test_the_pin_is_the_cards_own_and_not_the_audits_save_side_record(self):
        # The audit's core_library_version moves whenever a live round
        # re-verifies a core's SAVE behaviour. Keying this claim on it would let
        # a bump for an unrelated reason silently re-validate it against a build
        # nobody examined for texture replacement.
        for card in load_texture_packs():
            switch = card.absent_switch
            if switch is None:
                continue
            audit = lookup_audit(card.key)
            recorded = audit.verified["retrodeck"] if audit is not None else None
            assert recorded is None or switch.verified_core == recorded.core_library_version, (
                "they agree today — the point is that the resolver reads the card's field, which "
                "the drift test below proves by moving only the card"
            )


class TestTheLoaderRefusesWhatItCannotStand:
    def test_an_unknown_schema_is_refused(self):
        text = json.dumps({"schema": 99, "cores": {}})
        with pytest.raises(ValueError, match="unsupported schema"):
            load_texture_packs(text)

    def test_a_card_without_provenance_is_refused(self):
        # Every recorded fact here is world knowledge, so an uncited card is a
        # guess wearing the same field names as an established one.
        table = json.loads(_card())
        del table["cores"]["demo"]["provenance"]
        text = json.dumps(table)
        with pytest.raises(ValueError, match="provenance.source"):
            load_texture_packs(text)

    def test_a_root_the_placement_grammar_does_not_know_is_refused(self):
        text = _card(root="texture_directory")
        with pytest.raises(ValueError, match="textures.root"):
            load_texture_packs(text)

    @pytest.mark.parametrize("subdir", ["/absolute/textures", "../climbing/out", "demo/../../out"])
    def test_a_subdir_that_escapes_its_root_is_refused(self, subdir):
        # It is joined onto a directory resolved from a config, so an absolute
        # fragment discards that root and a '..' climbs out of it — both reach
        # a caller as an ordinary-looking path somewhere the emulator never
        # reads.
        text = _card(subdir=subdir)
        with pytest.raises(ValueError, match="textures.subdir|absolute|climbs"):
            load_texture_packs(text)

    def test_a_keying_outside_the_vocabulary_is_refused(self):
        text = _card(keying={"value": "per-game", "citation": "[V] somewhere"})
        with pytest.raises(ValueError, match="textures.keying.value"):
            load_texture_packs(text)

    def test_a_keying_without_a_citation_is_refused(self):
        text = _card(keying={"value": "serial"})
        with pytest.raises(ValueError, match="textures.keying"):
            load_texture_packs(text)

    def test_an_option_whose_values_all_mean_the_same_is_refused(self):
        # It would report the feature as permanently on while the machine could
        # say otherwise — an option that governs nothing is not an option.
        text = _card(replacement_option={"setting": "demo_textures", "values": {"on": True, "yes": True}})
        with pytest.raises(ValueError, match="means enabled and one that means"):
            load_texture_packs(text)

    def test_an_option_value_meaning_a_string_is_refused(self):
        # bool("false") is True in Python — this claim is never coerced.
        text = _card(replacement_option={"setting": "demo_textures", "values": {"on": "true", "off": False}})
        with pytest.raises(ValueError, match="must be a JSON boolean"):
            load_texture_packs(text)

    def test_a_restated_so_name_is_refused(self):
        table = json.loads(_card())
        table["cores"]["demo"]["identifiers"]["so"] = "demo_libretro.so"
        text = json.dumps(table)
        with pytest.raises(ValueError, match="identifiers.so"):
            load_texture_packs(text)


class TestTheAnswerJoinsALiveRootToARecordedFragment:
    def test_the_root_is_read_and_the_fragment_is_recorded(self):
        placement = texture_placed(_retrodeck().texture_pack_location(core_so="flycast_libretro.so"))
        assert placement.dir == f"{ROOT}/bios/dc/textures"

    def test_a_moved_system_directory_moves_the_answer(self):
        # The proof that the root is read rather than written down: nothing in
        # the packaged table changed, and the answer did.
        machine = _machine({RETRODECK_CFG: CFG.replace(f"{ROOT}/bios", "/elsewhere/bios")})
        placement = texture_placed(
            atlas.detect(HOME, machine)[0].texture_pack_location(core_so="flycast_libretro.so")
        )
        assert placement.dir == "/elsewhere/bios/dc/textures"

    def test_a_wired_tree_reports_both_truthful_paths(self):
        placement = texture_placed(
            _retrodeck(
                files={f"{ROOT}/texture_packs/Flycast/keep": ""},
                symlinks={f"{ROOT}/bios/dc/textures": f"{ROOT}/texture_packs/Flycast"},
            ).texture_pack_location(core_so="flycast_libretro.so")
        )
        assert placement.dir == f"{ROOT}/bios/dc/textures"
        assert placement.physical_dir == f"{ROOT}/texture_packs/Flycast"

    def test_a_cited_keying_is_stated(self):
        placement = texture_placed(_retrodeck().texture_pack_location(core_so="flycast_libretro.so"))
        assert placement.keying == "game-id"

    def test_an_uncited_keying_is_absent_rather_than_derived(self):
        placement = texture_placed(
            _retrodeck(
                cores={f"{CORES}/mesen_libretro.so": {"library_name": "Mesen"}}
            ).texture_pack_location(core_so="mesen_libretro.so")
        )
        assert placement.keying is None


class TestWhetherReplacementIsOnIsALiveRead:
    def _enabled(self, files=None, *, options=FLYCAST_OPTIONS) -> bool | None:
        cores = {f"{CORES}/flycast_libretro.so": {"library_name": "Flycast", "options": options}}
        handle = _retrodeck(files=files, cores=cores)
        return texture_placed(handle.texture_pack_location(core_so="flycast_libretro.so")).enabled

    def test_the_options_file_answers(self):
        assert self._enabled({OPTIONS_CFG: 'reicast_custom_textures = "enabled"\n'}) is True
        assert self._enabled({OPTIONS_CFG: 'reicast_custom_textures = "disabled"\n'}) is False

    def test_the_installed_core_s_default_answers_where_no_file_does(self):
        assert self._enabled() is False
        assert self._enabled(
            options={"reicast_custom_textures": {"default": "enabled", "values": ["disabled", "enabled"]}}
        ) is True

    def test_nothing_established_is_null_and_null_is_not_off(self):
        # No options file states the key and the probe captured no registration
        # to fall back on. A client that renders this as "off" reports a fact
        # nobody read.
        assert self._enabled(options=None) is None

    def test_a_value_the_record_cannot_interpret_is_null_with_a_caveat(self):
        handle = _retrodeck(files={OPTIONS_CFG: 'reicast_custom_textures = "on"\n'})
        placement = texture_placed(handle.texture_pack_location(core_so="flycast_libretro.so"))
        assert placement.enabled is None
        assert atlas.CAVEAT_UNKNOWN_OPTION_VALUE in [c.code for c in placement.caveats]

    def test_a_per_game_options_file_outranks_the_global_one(self):
        # The one way content reaches this answer: it never moves the
        # directory, and it does decide which options file governs.
        rom = f"{ROOT}/roms/dreamcast/Game (Europe).gdi"
        game_opt = (
            f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retroarch/config/Flycast/Game (Europe).opt"
        )
        handle = _retrodeck(
            files={
                OPTIONS_CFG: 'reicast_custom_textures = "disabled"\n',
                game_opt: 'reicast_custom_textures = "enabled"\n',
                rom: "",
            }
        )
        placement = texture_placed(
            handle.texture_pack_location(core_so="flycast_libretro.so", content_path=rom)
        )
        assert placement.enabled is True


class TestTheDoubtIsDrivenByTheAuditRecord:
    """The fourth-root caveat retires by an edit to the audit, not to a resolver.

    That is the whole design of the hook: three cores build their tree under a
    user directory whose root nobody has watched them choose, the audit already
    carries that as a ``suspect`` verdict for their saves, and closing the
    verdict has to be enough to retire the caveat here too.
    """

    CORE = f"{CORES}/ppsspp_libretro.so"
    CORE_ANSWER = {"library_name": "PPSSPP"}

    def _codes(self) -> list[str]:
        handle = _retrodeck(cores={self.CORE: self.CORE_ANSWER})
        return [c.code for c in texture_placed(handle.texture_pack_location(core_so="ppsspp_libretro.so")).caveats]

    def test_a_suspect_verdict_states_the_doubt(self):
        assert atlas.CAVEAT_EMULATOR_READ_UNESTABLISHED in self._codes()

    def test_the_caveat_names_the_core_and_the_verdict_it_came_from(self):
        handle = _retrodeck(cores={self.CORE: self.CORE_ANSWER})
        placement = texture_placed(handle.texture_pack_location(core_so="ppsspp_libretro.so"))
        caveat = next(
            c for c in placement.caveats if c.code == atlas.CAVEAT_EMULATOR_READ_UNESTABLISHED
        )
        assert dict(caveat.data) == {"core": "ppsspp", "verdict": "suspect"}

    def test_closing_the_verdict_in_the_audit_retires_the_caveat(self, monkeypatch):
        # No resolver names a core, so the record is the only thing that has to
        # change — the same way arrangement-unverified retires by an edit to
        # arrangement_evidence.json.
        closed = AuditEntry(
            key="ppsspp",
            verdict="standard",
            per_game_capable=True,
            note="[V-live] observed",
            verified={},
        )
        monkeypatch.setattr(
            "atlas.installations.lookup_audit", lambda key: closed if key == "ppsspp" else None
        )
        assert atlas.CAVEAT_EMULATOR_READ_UNESTABLISHED not in self._codes()

    def test_a_core_the_audit_calls_settled_states_nothing(self):
        handle = _retrodeck()
        placement = texture_placed(handle.texture_pack_location(core_so="flycast_libretro.so"))
        assert atlas.CAVEAT_EMULATOR_READ_UNESTABLISHED not in [c.code for c in placement.caveats]


class TestTheQuestionRefusesRatherThanInventingADirectory:
    def test_a_core_no_card_covers_refuses_with_its_own_code(self):
        outcome = _retrodeck(
            cores={f"{CORES}/mgba_libretro.so": {"library_name": "mGBA"}}
        ).texture_pack_location(core_so="mgba_libretro.so")
        assert isinstance(outcome, Unresolved)
        assert outcome.code == atlas.UNRESOLVED_TEXTURE_WIRING_UNESTABLISHED

    def test_the_refusal_does_not_claim_the_emulator_has_no_texture_packs(self):
        outcome = _retrodeck(
            cores={f"{CORES}/mgba_libretro.so": {"library_name": "mGBA"}}
        ).texture_pack_location(core_so="mgba_libretro.so")
        assert isinstance(outcome, Unresolved)
        assert "not established" in outcome.message

    def test_a_core_the_machine_says_is_absent_refuses_the_way_the_save_route_does(self):
        # One fact, one code across the routes: a caller who learned the word
        # from savefile_location reads it here.
        machine = _machine(files={f"{CORES}/keep": ""}, cores={})
        outcome = atlas.detect(HOME, machine)[0].texture_pack_location(core_so="flycast_libretro.so")
        assert isinstance(outcome, Unresolved)
        assert outcome.code == atlas.UNRESOLVED_CORE_NOT_INSTALLED

    def test_naming_no_core_at_all_refuses(self):
        # There is no standard texture rule to fall back on the way the save
        # route falls back on RetroArch's path math: without a core there is no
        # card, and without a card there is nothing to join to a root.
        outcome = _retrodeck().texture_pack_location()
        assert isinstance(outcome, Unresolved)
        assert outcome.code == atlas.UNRESOLVED_TEXTURE_WIRING_UNESTABLISHED


class TestTheAnswerIsAPlacementLikeTheOthers:
    def test_a_directory_that_is_still_a_template_names_its_hole(self):
        # systemfiles_in_content_dir sends the core to the ROM's own directory,
        # so the texture root goes there too — and with no content named the
        # hole is the caller's to fill, exactly as on a save placement.
        machine = _machine({RETRODECK_CFG: CFG + 'systemfiles_in_content_dir = "true"\n'})
        placement = texture_placed(
            atlas.detect(HOME, machine)[0].texture_pack_location(core_so="flycast_libretro.so")
        )
        assert placement.needs == (atlas.HOLE_CONTENT_DIR,)
        assert placement.dir.startswith("<content_dir>/")
        # Nothing can be link-resolved through a hole.
        assert placement.physical_dir is None

    def test_a_dead_link_is_stated_and_no_physical_directory_is_claimed(self):
        placement = texture_placed(
            _retrodeck(
                symlinks={f"{ROOT}/bios/dc/textures": f"{ROOT}/texture_packs/gone"}
            ).texture_pack_location(core_so="flycast_libretro.so")
        )
        assert placement.physical_dir is None
        assert atlas.CAVEAT_DEAD_SYMLINK in [c.code for c in placement.caveats]


# --- the standalone rows -------------------------------------------------------

ESDE_BUNDLED = (
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
    "/es-de/share/es-de/resources/systems/linux/es_systems.xml"
)
RD_APP = f"{HOME}/.var/app/net.retrodeck.retrodeck"
ESDE_SYSTEMS = """<?xml version="1.0"?>
<systemList>
  <system>
    <name>gc</name>
    <fullname>Nintendo GameCube</fullname>
    <path>%ROMPATH%/gc</path>
    <extension>.rvz .RVZ</extension>
    <command label="Dolphin (Standalone)">%EMULATOR_DOLPHIN% -b -e %ROM%</command>
    <platform>gc</platform>
    <theme>gc</theme>
  </system>
  <system>
    <name>ps2</name>
    <fullname>Sony PlayStation 2</fullname>
    <path>%ROMPATH%/ps2</path>
    <extension>.chd .CHD</extension>
    <command label="PCSX2 (Standalone)">%EMULATOR_PCSX2% -batch %ROM%</command>
    <platform>ps2</platform>
    <theme>ps2</theme>
  </system>
  <system>
    <name>n3ds</name>
    <fullname>Nintendo 3DS</fullname>
    <path>%ROMPATH%/n3ds</path>
    <extension>.3ds .3DS</extension>
    <command label="Azahar (Standalone)">%EMULATOR_AZAHAR% %ROM%</command>
    <platform>n3ds</platform>
    <theme>n3ds</theme>
  </system>
  <system>
    <name>wii</name>
    <fullname>Nintendo Wii</fullname>
    <path>%ROMPATH%/wii</path>
    <extension>.rvz .RVZ</extension>
    <command label="PrimeHack (Standalone)">%EMULATOR_PRIMEHACK% -b -e %ROM%</command>
    <platform>wii</platform>
    <theme>wii</theme>
  </system>
  <system>
    <name>psvita</name>
    <fullname>Sony PlayStation Vita</fullname>
    <path>%ROMPATH%/psvita</path>
    <extension>.psvita .PSVITA</extension>
    <command label="Vita3K (Standalone)">%EMULATOR_VITA3K% %ROM%</command>
    <platform>psvita</platform>
    <theme>psvita</theme>
  </system>
</systemList>
"""


def _catalogued(files=None, **kwargs):
    """A RetroDECK whose ES-DE declares one standalone entry per system above."""
    return _retrodeck(files={ESDE_BUNDLED: ESDE_SYSTEMS, **(files or {})}, **kwargs)


def _entry(system: str, files=None, **kwargs):
    return _catalogued(files=files, **kwargs).emulators_for(system).entries[0]


def _standalone_card(**textures) -> str:
    return json.dumps(
        {
            "schema": TEXTURE_PACKS_SCHEMA,
            "emulators": {
                "DEMO": {
                    "textures": {
                        "base": "data",
                        "subdir": "demo/textures",
                        "config": {"base": "config", "path": "demo/settings.ini"},
                        **textures,
                    },
                    "provenance": {"source": "[V] a citation"},
                }
            },
        }
    )


class TestTheShippedStandaloneTableSaysWhatItCanStandBehind:
    @pytest.mark.parametrize("card", load_standalone_texture_packs(), ids=lambda c: c.token)
    def test_a_card_names_a_directory_one_way_and_a_config_to_go_with_it(self, card):
        # Exactly one shape: a fixed subpath below an XDG base, or the
        # configuration key whose value is the directory. Both, or neither,
        # would leave the resolver picking.
        fixed = card.base is not None
        assert fixed == (card.subdir is not None)
        assert fixed != (card.directory is not None)
        if fixed:
            assert card.base in XDG_BASES
        assert card.config.base in XDG_BASES
        # The config is what the answer points a caller at — either as the file
        # emulator-config-unread names, or as the file the switch was read from.
        assert card.config.path

    @pytest.mark.parametrize("card", load_standalone_texture_packs(), ids=lambda c: c.token)
    def test_a_card_that_states_a_keying_cites_it(self, card):
        assert (card.keying is None) == (card.keying_citation is None)
        assert card.keying is None or card.keying in KEYINGS

    def test_vita3k_is_absent_on_purpose(self):
        # Vita3K opens no default either: its texture tree hangs off pref-path
        # in config.yml. PCSX2 was absent for the same reason until its
        # configuration was read (#223); Vita3K's is YAML, and atlas ships no
        # YAML reader — so the absence stays a decision, and worth failing on
        # if somebody reverses it by quoting the path the installer intended
        # instead of reading the config.
        assert lookup_standalone_texture_card("VITA3K") is None

    def test_a_config_stated_card_answers_instead_of_naming_a_default(self):
        # The other half of the same decision: PCSX2's directory IS its
        # configuration's value, so the card states the key rather than a
        # subpath, and neither base nor subdir may be invented for it.
        card = lookup_standalone_texture_card("PCSX2")
        assert card is not None
        assert card.base is None and card.subdir is None
        assert card.directory is not None
        assert (card.directory.section, card.directory.key) == ("Folders", "Textures")
        assert card.switch is not None
        assert card.switch.key == "LoadTextureReplacements"

    def test_a_command_that_names_no_emulator_matches_nothing(self):
        assert lookup_standalone_texture_card(None) is None


class TestTheStandaloneLoaderRefusesWhatItCannotStand:
    def test_a_base_outside_the_vocabulary_is_refused(self):
        text = _standalone_card(base="cache")
        with pytest.raises(ValueError, match="textures.base"):
            load_standalone_texture_packs(text)

    def test_a_subdir_that_escapes_its_base_is_refused(self):
        text = _standalone_card(subdir="/etc")
        with pytest.raises(ValueError, match="absolute|climbs"):
            load_standalone_texture_packs(text)

    def test_a_card_without_a_config_is_refused(self):
        table = json.loads(_standalone_card())
        del table["emulators"]["DEMO"]["textures"]["config"]
        text = json.dumps(table)
        with pytest.raises(ValueError, match="textures.config"):
            load_standalone_texture_packs(text)

    def test_a_config_path_that_escapes_its_base_is_refused(self):
        text = _standalone_card(config={"base": "config", "path": "../../etc/passwd"})
        with pytest.raises(ValueError, match="absolute|climbs"):
            load_standalone_texture_packs(text)

    def test_a_keying_without_a_citation_is_refused(self):
        text = _standalone_card(keying={"value": "pack"})
        with pytest.raises(ValueError, match="textures.keying"):
            load_standalone_texture_packs(text)

    def test_an_unknown_schema_is_refused(self):
        text = json.dumps({"schema": 99, "emulators": {}})
        with pytest.raises(ValueError, match="unsupported schema"):
            load_standalone_texture_packs(text)


class TestAStandaloneEmulatorAnswersFromItsOwnXdgTree:
    def test_the_directory_is_the_emulators_own_default_below_the_pinned_base(self):
        placement = texture_placed(_entry("gc").texture_pack_location())
        assert placement.dir == f"{RD_APP}/data/dolphin-emu/Load/Textures"

    def test_a_wired_tree_reports_the_directory_behind_the_link(self):
        placement = texture_placed(
            _entry(
                "gc",
                files={f"{ROOT}/texture_packs/Dolphin/Textures/keep": ""},
                symlinks={
                    f"{RD_APP}/data/dolphin-emu/Load/Textures": f"{ROOT}/texture_packs/Dolphin/Textures"
                },
            ).texture_pack_location()
        )
        assert placement.physical_dir == f"{ROOT}/texture_packs/Dolphin/Textures"

    def test_the_switch_is_unanswered_and_names_the_file_that_would_answer_it(self):
        placement = texture_placed(_entry("gc").texture_pack_location())
        assert placement.enabled is None
        caveat = next(
            c for c in placement.caveats if c.code == atlas.CAVEAT_EMULATOR_CONFIG_UNREAD
        )
        assert dict(caveat.data) == {
            "emulator": "DOLPHIN",
            "config": f"{RD_APP}/config/dolphin-emu/GFX.ini",
        }

    def test_nothing_in_the_join_comes_from_the_content_so_no_hole_is_left(self):
        placement = texture_placed(_entry("gc").texture_pack_location())
        assert placement.needs == ()

    def test_a_cited_keying_is_stated(self):
        assert texture_placed(_entry("gc").texture_pack_location()).keying == "game-id"


class TestTheEntryRouteAsymmetryIsDeliberate:
    """One entry, two questions, two different answers — and that is the design.

    A save routes through a config atlas would have to model; a texture pack
    mostly does not. The pair is asserted together so neither can drift into
    the other's shape unnoticed.
    """

    def test_the_same_entry_answers_textures_and_refuses_its_save(self):
        # PrimeHack has a texture card and no standalone save card — the
        # split inside one entry is evidence, not kind. (Azahar and then
        # DuckStation were this example, until their save cards landed.)
        entry = _entry("wii")
        assert isinstance(entry.texture_pack_location(), TexturePlacement)
        refusal = entry.savefile_location()
        assert isinstance(refusal, Unresolved)
        assert refusal.code == atlas.UNRESOLVED_STANDALONE

    def test_an_emulator_with_a_save_card_answers_the_save_question_too(self):
        # Dolphin carries a standalone save card since #181, so the same entry
        # that answers textures answers its save — the asymmetry was never
        # about the standalone kind, only about what atlas has established.
        entry = _entry("gc")
        assert isinstance(entry.texture_pack_location(), TexturePlacement)
        assert isinstance(entry.savefile_location(), atlas.SavefilePlacement)

    def test_the_savestate_question_still_refuses_where_the_save_answers(self):
        # States are their own wiring (Dolphin's StateSaves tree) and stay
        # outside the save card deliberately — refusal, not silence.
        refusal = _entry("gc").savestate_location()
        assert isinstance(refusal, Unresolved)
        assert refusal.code == atlas.UNRESOLVED_STANDALONE

    def test_an_emulator_whose_directory_lives_in_an_unread_config_refuses(self):
        # The split inside the standalone kind: Vita3K's texture tree hangs off
        # pref-path in a config.yml nothing reads. (PCSX2 was this example
        # until its own configuration was read, #223.)
        refusal = _entry("psvita").texture_pack_location()
        assert isinstance(refusal, Unresolved)
        assert refusal.code == atlas.UNRESOLVED_STANDALONE

    def test_the_two_standalone_outcomes_are_told_apart_by_evidence_not_by_kind(self):
        # Same arrangement, same kind of entry, same catalogue read — one
        # answers and one refuses, and the only difference is what atlas has
        # established about the emulator.
        assert isinstance(_entry("gc").texture_pack_location(), TexturePlacement)
        assert isinstance(_entry("psvita").texture_pack_location(), Unresolved)

    def test_a_config_stated_directory_answers_the_switch_as_well(self):
        # PCSX2 reads both halves out of one file: the directory from
        # [Folders] Textures and the switch from [EmuCore/GS]
        # LoadTextureReplacements — so enabled is a reading here, not None.
        placement = _entry(
            "ps2",
            files={
                f"{HOME}/.var/app/net.retrodeck.retrodeck/config/PCSX2/inis/PCSX2.ini": (
                    "[Folders]\nTextures = /mnt/sd/texture_packs/PCSX2/textures\n"
                    "[EmuCore/GS]\nLoadTextureReplacements = true\n"
                )
            },
        ).texture_pack_location()
        placed = texture_placed(placement)
        assert placed.enabled is True
        assert placed.keying == "serial"
        assert placed.dir == "/mnt/sd/texture_packs/PCSX2/textures/<save_id>/replacements"
        assert placed.needs == ("save_id",)


class TestAFeatureWithNoSwitchIsStatedAsOne:
    """LRPS2: the read path is established, and this build offers no way to use it.

    The strongest claim the family makes about a switch, and the one furthest
    from a guess: ``enabled`` is ``False`` as a fact about the binary rather
    than as a reading of any file, because the setting exists in the emulator's
    vocabulary and nothing in the shipped build writes it.
    """

    CORE = f"{CORES}/pcsx2_libretro.so"
    SETTING = "EmuCore/GS/LoadTextureReplacements"

    def _placed(self, *, library_version="14d19f8"):
        handle = _retrodeck(
            cores={self.CORE: {"library_name": "LRPS2", "library_version": library_version}},
            dirs=[f"{ROOT}/bios/pcsx2/textures"],
        )
        return texture_placed(handle.texture_pack_location(core_so="pcsx2_libretro.so"))

    def test_the_row_answers_with_the_wired_root(self):
        assert self._placed().dir == f"{ROOT}/bios/pcsx2/textures"

    def test_replacement_is_off_and_says_so_as_a_fact(self):
        assert self._placed().enabled is False

    def test_the_caveat_names_the_core_and_the_setting_nobody_can_reach(self):
        caveat = next(
            c for c in self._placed().caveats if c.code == atlas.CAVEAT_FEATURE_SWITCH_ABSENT
        )
        assert dict(caveat.data) == {"core": "pcsx2", "option_key": self.SETTING}

    def test_the_tree_is_keyed_by_the_discs_serial(self):
        assert self._placed().keying == "serial"

    def test_a_different_core_build_reopens_the_question(self):
        # A build is exactly what could add a writer, so the claim does not
        # travel to a generation nobody examined.
        codes = [c.code for c in self._placed(library_version="deadbee").caveats]
        assert atlas.CAVEAT_UNVERIFIED_VERSION in codes

    def test_the_recorded_build_states_no_drift(self):
        codes = [c.code for c in self._placed().caveats]
        assert atlas.CAVEAT_UNVERIFIED_VERSION not in codes

    def test_a_core_that_states_no_version_is_not_compared(self):
        # Both sides must speak; silence means no drift established, not none.
        handle = _retrodeck(
            cores={self.CORE: {"library_name": "LRPS2"}}, dirs=[f"{ROOT}/bios/pcsx2/textures"]
        )
        placement = texture_placed(handle.texture_pack_location(core_so="pcsx2_libretro.so"))
        assert atlas.CAVEAT_UNVERIFIED_VERSION not in [c.code for c in placement.caveats]

    def test_it_never_rides_with_the_doubt_about_the_read_path(self):
        # The two say opposite kinds of thing: one that the read path is in
        # doubt, the other that it is established and simply never taken. No
        # shipped card can produce both, and the loader plus the audit are what
        # keep it that way.
        assert atlas.CAVEAT_EMULATOR_READ_UNESTABLISHED not in [
            c.code for c in self._placed().caveats
        ]

    def test_no_shipped_card_can_produce_both_codes(self):
        # The structural half of the rule above: a card claiming its build has
        # no switch is a card whose read path was established, so its audit
        # verdict is never the one that drives the doubt.
        for card in load_texture_packs():
            if card.absent_switch is None:
                continue
            audit = lookup_audit(card.key)
            assert audit is not None
            assert audit.verdict != "suspect"

    def test_a_core_nothing_covers_still_refuses(self):
        # The absence of a card and a card stating an absent switch are
        # different answers, and adding the second must not blur the first.
        outcome = _retrodeck(
            cores={f"{CORES}/mgba_libretro.so": {"library_name": "mGBA"}}
        ).texture_pack_location(core_so="mgba_libretro.so")
        assert isinstance(outcome, Unresolved)
        assert outcome.code == atlas.UNRESOLVED_TEXTURE_WIRING_UNESTABLISHED


class TestTheAbsentSwitchLoaderRefusesWhatItCannotStand:
    def _card(self, **switch):
        table = json.loads(_card())
        table["cores"]["demo"]["textures"]["absent_switch"] = {
            "setting": "Emu/Flag",
            "enabled": False,
            "verified_core": "abc1234",
            "citation": "[V] proven",
            **switch,
        }
        return json.dumps(table)

    def test_a_switch_state_that_is_a_string_is_refused(self):
        text = self._card(enabled="false")
        with pytest.raises(ValueError, match="must be a JSON boolean"):
            load_texture_packs(text)

    def test_a_claim_without_a_citation_is_refused(self):
        text = self._card(citation="")
        with pytest.raises(ValueError, match="absent_switch"):
            load_texture_packs(text)

    def test_a_claim_that_names_no_build_is_refused(self):
        # Without the pin the claim would hold for every future generation,
        # which is the one shape worse than not making it.
        text = self._card(verified_core="")
        with pytest.raises(ValueError, match="verified_core"):
            load_texture_packs(text)

    def test_stating_both_a_switch_and_its_absence_is_refused(self):
        table = json.loads(self._card())
        table["cores"]["demo"]["textures"]["replacement_option"] = {
            "setting": "demo_textures",
            "values": {"on": True, "off": False},
        }
        text = json.dumps(table)
        with pytest.raises(ValueError, match="never both"):
            load_texture_packs(text)


class TestTheTwoRoutesAgreeOnTheRootTheCoreIsHanded:
    """The same machine, the two families, one root — asserted side by side.

    A texture tree is built under a root RetroArch hands the core, and both
    save-rooted and system-rooted cards inherit that root's resolution rather
    than a reading of the cfg key. This is the property the firmware and card
    routes already pin for the system directory; it was missing on the save
    side, where the texture route read `savefile_directory` while the save
    route followed `savefiles_in_content_dir` into the content's own directory.
    """

    SAVE_ROOTED = "ppsspp_libretro.so"
    SYSTEM_ROOTED = "flycast_libretro.so"

    def _handle(self, extra: str = ""):
        return atlas.detect(HOME, _machine({RETRODECK_CFG: CFG + extra}, cores={
            f"{CORES}/{self.SAVE_ROOTED}": {"library_name": "PPSSPP"},
            f"{CORES}/{self.SYSTEM_ROOTED}": {"library_name": "Flycast", "options": FLYCAST_OPTIONS},
        }))[0]

    def _pair(self, core_so: str, extra: str = "", content_path: str | None = None):
        handle = self._handle(extra)
        save = handle.savefile_location(core_so=core_so, content_path=content_path)
        texture = texture_placed(
            handle.texture_pack_location(core_so=core_so, content_path=content_path)
        )
        assert not isinstance(save, Unresolved)
        return save, texture

    def test_the_save_rooted_card_follows_the_save_family_into_the_content_directory(self):
        save, texture = self._pair(self.SAVE_ROOTED, 'savefiles_in_content_dir = "true"\n')
        assert save.dir.startswith("<content_dir>")
        assert texture.dir.startswith("<content_dir>/")
        # The hole is the whole point: a concrete path here would name a
        # directory RetroArch never hands the core, with nothing saying so.
        assert save.needs == texture.needs == (atlas.HOLE_CONTENT_DIR,)

    def test_a_named_content_fills_the_hole_on_both_routes(self):
        rom = f"{ROOT}/roms/psp/Game (Europe).iso"
        save, texture = self._pair(
            self.SAVE_ROOTED, 'savefiles_in_content_dir = "true"\n', content_path=rom
        )
        content_dir = f"{ROOT}/roms/psp"
        assert save.dir.startswith(content_dir)
        assert texture.dir.startswith(content_dir)
        assert save.needs == ()
        assert texture.needs == ()

    def test_without_the_switch_both_stay_under_the_configured_root(self):
        save, texture = self._pair(self.SAVE_ROOTED)
        assert save.dir.startswith(f"{ROOT}/saves/")
        assert texture.dir.startswith(f"{ROOT}/saves/")

    def test_the_texture_root_skips_the_sorting_stages_the_save_dir_takes(self):
        # The one place the two routes deliberately differ, kept explicit so it
        # reads as a decision rather than an oversight. It is a DERIVATION
        # deciding against contrary evidence: the sorting redirect runs before
        # the core sees the directory (retrodeck-save-placement.md, the
        # [V-live] Flycast observation), but both distributions wire the
        # texture links at the UNSORTED root — see _texture_root's docstring
        # for the full reasoning and the condition that would overturn it.
        save, texture = self._pair(self.SAVE_ROOTED)
        assert save.dir == f"{ROOT}/saves/PPSSPP"  # sorted by library_name
        assert texture.dir == f"{ROOT}/saves/PPSSPP/PSP/TEXTURES"  # the card's own fragment
        assert not texture.dir.startswith(save.dir + "/PPSSPP")

    def test_the_system_rooted_card_follows_its_own_family_the_same_way(self):
        # The property that already held, kept beside the one that did not, so
        # neither route can be "fixed" into disagreeing with the other.
        _, texture = self._pair(self.SYSTEM_ROOTED, 'systemfiles_in_content_dir = "true"\n')
        assert texture.dir.startswith("<content_dir>/")
        assert texture.needs == (atlas.HOLE_CONTENT_DIR,)
