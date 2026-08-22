"""Tests for the mods family — the mod cards, and the soft-patching question.

Three things are under test and they are different in kind: the packaged cards
and build record (world knowledge, so the loaders must refuse anything they
cannot stand behind), the mod answer, which joins a live root to a recorded
fragment and may state several trees at once, and the soft-patching answer,
which is arithmetic over the content path plus two live readings.
"""

from __future__ import annotations

import json
from typing import Mapping, Sequence

import pytest

import atlas
from atlas.machine import FixtureFileSpec, FixtureMachine
from atlas.mods import (
    MODS_SCHEMA,
    load_mod_cards,
    load_soft_patch_builds,
    load_standalone_mod_cards,
    lookup_mod_card,
    lookup_soft_patch_build,
    lookup_standalone_mod_card,
)
from atlas.placement import KEYINGS, PATCH_FORMATS, ModPlacement, SoftPatchAnswer, Unresolved
from tests.answers import mod_placed

HOME = "/home/deck"
RD_APP = f"{HOME}/.var/app/net.retrodeck.retrodeck"
RETRODECK_JSON = f"{RD_APP}/config/retrodeck/retrodeck.json"
RETRODECK_CFG = f"{RD_APP}/config/retroarch/retroarch.cfg"
ROOT = "/mnt/sd/retrodeck"
CORES = f"{ROOT}/cores"
INFO = f"{ROOT}/cores"
SHIPPED_VERSION = "0.10.9b"
RD_JSON = json.dumps(
    {
        "version": SHIPPED_VERSION,
        "paths": {"rd_home_path": ROOT, "saves_path": f"{ROOT}/saves", "bios_path": f"{ROOT}/bios"},
    }
)
CFG = (
    f'libretro_directory = "{CORES}"\n'
    f'libretro_info_path = "{INFO}"\n'
    f'system_directory = "{ROOT}/bios"\n'
    f'savefile_directory = "{ROOT}/saves"\n'
)
FBNEO = "fbneo_libretro.so"
FBNEO_INFO = f"{INFO}/fbneo_libretro.info"
ROM = "/mnt/sd/retrodeck/roms/arcade/sf2ce.zip"

_Cores = Mapping[str, Mapping[str, object] | None]
DEPLOYED: _Cores = {f"{CORES}/{FBNEO}": {"library_name": "FinalBurn Neo"}}


def _machine(
    files: Mapping[str, FixtureFileSpec] | None = None,
    *,
    cores: _Cores | None = None,
    dirs: Sequence[str] = (),
    symlinks: Mapping[str, str] | None = None,
    marker: str = RD_JSON,
) -> FixtureMachine:
    return FixtureMachine(
        {RETRODECK_JSON: marker, RETRODECK_CFG: CFG, **(files or {})},
        cores=DEPLOYED if cores is None else cores,
        dirs=[f"{ROOT}/saves", CORES, *dirs],
        symlinks=symlinks,
    )


def _retrodeck(**kwargs) -> atlas.Installation:
    return atlas.detect(HOME, _machine(**kwargs))[0]


def _answered(outcome: SoftPatchAnswer | Unresolved) -> SoftPatchAnswer:
    """The answer this fixture guarantees — never the refusal."""
    assert isinstance(outcome, SoftPatchAnswer), f"expected a soft-patch answer, got {outcome}"
    return outcome


def _codes(answer) -> list[str]:
    return [c.code for c in answer.caveats]


def _paths(answer: SoftPatchAnswer) -> list[str]:
    return [candidate.path for candidate in answer.candidates]


def _record(**fields) -> str:
    return json.dumps(
        {
            "schema": MODS_SCHEMA,
            "soft_patching": {
                "demo": {
                    "formats": ["ips"],
                    "verified_arrangement": "1.0",
                    "citation": "[V-binary] a citation",
                    **fields,
                }
            },
        }
    )


class TestTheShippedRecordSaysWhatItCanStandBehind:
    @pytest.mark.parametrize("build", load_soft_patch_builds().values(), ids=lambda b: b.kind)
    def test_a_record_pins_the_arrangement_it_was_read_at(self, build):
        # A claim about a compiled binary that outlived the build it was read
        # from would re-validate itself on every update, silently.
        assert build.verified_arrangement
        assert build.citation

    @pytest.mark.parametrize("build", load_soft_patch_builds().values(), ids=lambda b: b.kind)
    def test_a_record_speaks_only_the_known_formats(self, build):
        assert build.formats <= set(PATCH_FORMATS)
        assert set(build.attempts()) == set(PATCH_FORMATS)

    def test_the_shipped_retrodeck_build_attempts_all_four(self):
        build = lookup_soft_patch_build("retrodeck")
        assert build is not None
        assert build.attempts() == {fmt: True for fmt in PATCH_FORMATS}

    @pytest.mark.parametrize("kind", ["emudeck", "bare_retroarch_flatpak", "bare_retroarch_native"])
    def test_the_arrangements_nobody_read_carry_no_record(self, kind):
        # Absence is the honest state, and the answer says so rather than
        # borrowing the upstream build defaults, which are a fact about the
        # source tree and not about anyone's binary.
        assert lookup_soft_patch_build(kind) is None


class TestTheRecordLoaderRefusesWhatItCannotStand:
    def test_an_unsupported_schema_is_refused(self):
        text = json.dumps({"schema": MODS_SCHEMA + 1})
        with pytest.raises(ValueError, match="unsupported schema"):
            load_soft_patch_builds(text)

    def test_a_format_outside_the_vocabulary_is_refused(self):
        # It would reach a client as a candidate's attempted state — a claim
        # about a file atlas never composes a path for.
        text = _record(formats=["ips", "vcdiff"])
        with pytest.raises(ValueError, match="formats must be drawn from"):
            load_soft_patch_builds(text)

    def test_formats_that_are_not_a_list_are_refused(self):
        text = _record(formats="ips")
        with pytest.raises(ValueError, match="formats must be a list"):
            load_soft_patch_builds(text)

    def test_a_record_without_its_pin_is_refused(self):
        text = _record(verified_arrangement="")
        with pytest.raises(ValueError, match="verified_arrangement"):
            load_soft_patch_builds(text)

    def test_a_record_without_a_citation_is_refused(self):
        text = _record(citation="")
        with pytest.raises(ValueError, match="citation"):
            load_soft_patch_builds(text)

    def test_a_build_that_attempts_nothing_is_a_legal_record(self):
        # Patching compiled out is a real build and a real reading; it must not
        # be spelled the same way as a build nobody examined.
        builds = load_soft_patch_builds(_record(formats=[]))
        assert builds["demo"].attempts() == {fmt: False for fmt in PATCH_FORMATS}


class TestTheCandidatesAreTheContentsOwnNames:
    def test_the_four_formats_come_back_in_retroarchs_attempt_order(self):
        answer = _answered(_retrodeck().soft_patch_candidates(ROM))
        assert [c.format for c in answer.candidates] == list(PATCH_FORMATS)
        assert _paths(answer) == [
            "/mnt/sd/retrodeck/roms/arcade/sf2ce.ips",
            "/mnt/sd/retrodeck/roms/arcade/sf2ce.bps",
            "/mnt/sd/retrodeck/roms/arcade/sf2ce.ups",
            "/mnt/sd/retrodeck/roms/arcade/sf2ce.xdelta",
        ]

    def test_each_candidate_carries_its_nine_indexed_continuations(self):
        answer = _answered(_retrodeck().soft_patch_candidates(ROM))
        first = answer.candidates[0]
        assert first.continuations == tuple(
            f"/mnt/sd/retrodeck/roms/arcade/sf2ce.ips{index}" for index in range(1, 10)
        )
        # One digit is upstream's own bound: it writes a single character into
        # the byte behind the name (task_patch.c:1121-1147).
        assert len(first.continuations) == 9

    def test_content_inside_an_archive_is_named_after_the_entry(self):
        # path_basedir_wrapper cuts at the archive delimiter and keeps the
        # archive's directory, path_basename returns what follows it — so the
        # patch sits beside the archive under the inner file's name.
        answer = _answered(
            _retrodeck().soft_patch_candidates("/mnt/sd/retrodeck/roms/nes/pack.zip#Game.nes")
        )
        assert _paths(answer)[0] == "/mnt/sd/retrodeck/roms/nes/Game.ips"

    def test_only_the_last_extension_is_truncated(self):
        answer = _answered(_retrodeck().soft_patch_candidates("/roms/psx/Game.v1.1.cue"))
        assert _paths(answer)[0] == "/roms/psx/Game.v1.1.ips"

    def test_a_content_path_that_names_no_file_yields_no_candidates(self):
        # RetroArch's path math derives an empty name from it, so it composes no
        # patch name either; every candidate would be a dotfile in a directory
        # nobody asked about.
        answer = _answered(_retrodeck().soft_patch_candidates("/roms/psx/Game/"))
        assert answer.candidates == ()
        assert atlas.CAVEAT_CONTENT_PATH_UNNAMED in _codes(answer)


class TestWhetherThisBuildTriesAFormatIsAReadingOrNothing:
    def test_the_examined_build_answers_for_every_format(self):
        answer = _answered(_retrodeck().soft_patch_candidates(ROM))
        assert [c.attempted for c in answer.candidates] == [True, True, True, True]
        assert atlas.CAVEAT_PATCH_FORMATS_UNESTABLISHED not in _codes(answer)

    def test_an_unexamined_build_leaves_every_format_unstated(self):
        machine = FixtureMachine(
            {f"{HOME}/.config/retroarch/retroarch.cfg": CFG}, dirs=[f"{ROOT}/saves"]
        )
        answer = _answered(
            atlas.detect(HOME, machine)[0].soft_patch_candidates("/roms/nes/Game.nes")
        )
        assert [c.attempted for c in answer.candidates] == [None, None, None, None]
        stated = next(
            c for c in answer.caveats if c.code == atlas.CAVEAT_PATCH_FORMATS_UNESTABLISHED
        )
        assert stated.data == {"formats": "ips,bps,ups,xdelta"}

    def test_a_machine_running_another_arrangement_version_is_not_handed_the_claim_unexamined(self):
        drifted = json.loads(RD_JSON)
        drifted["version"] = "0.11.0b"
        answer = _answered(_retrodeck(marker=json.dumps(drifted)).soft_patch_candidates(ROM))
        stated = next(c for c in answer.caveats if c.code == atlas.CAVEAT_UNVERIFIED_VERSION)
        assert stated.data == {
            "verification": "drifted",
            "arrangement_verified": SHIPPED_VERSION,
            "arrangement_live": "0.11.0b",
        }
        # The formats are still stated — the caveat qualifies the claim, it does
        # not withdraw it.
        assert [c.attempted for c in answer.candidates] == [True, True, True, True]

    def test_a_machine_that_states_no_version_is_not_compared(self):
        silent = json.dumps({"paths": {"rd_home_path": ROOT, "saves_path": f"{ROOT}/saves"}})
        answer = _answered(_retrodeck(marker=silent).soft_patch_candidates(ROM))
        assert atlas.CAVEAT_UNVERIFIED_VERSION not in _codes(answer)


class TestWhetherPatchingRunsAtAllIsReadFromTheCore:
    def test_a_core_that_loads_content_into_memory_is_patched(self):
        answer = _answered(
            _retrodeck(files={FBNEO_INFO: 'needs_fullpath = "false"\n'}).soft_patch_candidates(
                ROM, core_so=FBNEO
            )
        )
        assert answer.applies is True

    def test_a_core_handed_a_path_never_is(self):
        answer = _answered(
            _retrodeck(files={FBNEO_INFO: 'needs_fullpath = "true"\n'}).soft_patch_candidates(
                ROM, core_so=FBNEO
            )
        )
        assert answer.applies is False

    def test_an_info_that_states_nothing_leaves_it_unanswered(self):
        answer = _answered(
            _retrodeck(files={FBNEO_INFO: 'corename = "FinalBurn Neo"\n'}).soft_patch_candidates(
                ROM, core_so=FBNEO
            )
        )
        assert answer.applies is None
        # Read, and silent: not a degradation of the read, so neither of the two
        # codes that mean "atlas could not look" may appear — the same shape a
        # texture card with no governing option answers with.
        assert atlas.CAVEAT_CORE_INFO_UNREADABLE not in _codes(answer)
        assert atlas.CAVEAT_INFO_PATH_UNRESOLVED not in _codes(answer)

    def test_a_value_outside_retroarchs_boolean_vocabulary_states_nothing(self):
        # config_get_bool accepts 1/true/0/false and nothing else; "yes" sets no
        # value at all, and it certainly does not mean false.
        answer = _answered(
            _retrodeck(files={FBNEO_INFO: 'needs_fullpath = "yes"\n'}).soft_patch_candidates(
                ROM, core_so=FBNEO
            )
        )
        assert answer.applies is None

    def test_an_unreadable_info_is_stated(self):
        answer = _answered(
            _retrodeck(files={FBNEO_INFO: {"status": "unreadable"}}).soft_patch_candidates(
                ROM, core_so=FBNEO
            )
        )
        assert answer.applies is None
        assert atlas.CAVEAT_CORE_INFO_UNREADABLE in _codes(answer)

    def test_an_unresolvable_info_directory_is_stated(self):
        cfg = CFG.replace(f'libretro_info_path = "{INFO}"\n', "")
        answer = _answered(
            _retrodeck(files={RETRODECK_CFG: cfg}).soft_patch_candidates(ROM, core_so=FBNEO)
        )
        assert answer.applies is None
        assert atlas.CAVEAT_INFO_PATH_UNRESOLVED in _codes(answer)

    def test_naming_no_core_costs_exactly_that_one_field(self):
        answer = _answered(_retrodeck().soft_patch_candidates(ROM))
        assert answer.applies is None
        assert atlas.CAVEAT_NO_CORE in _codes(answer)
        # The candidates are the content's own and stand either way.
        assert len(answer.candidates) == 4


class TestTheQuestionRefusesForACoreThisMachineDoesNotHave:
    def test_a_core_that_is_not_installed_ends_the_question(self):
        outcome = _retrodeck(cores={}).soft_patch_candidates(ROM, core_so=FBNEO)
        assert isinstance(outcome, Unresolved)
        assert outcome.code == atlas.UNRESOLVED_CORE_NOT_INSTALLED

    def test_a_cores_directory_nobody_could_read_is_not_an_absence(self):
        # "cannot look" is never "is not there": with the cores directory gone,
        # the answer stands and the core is simply unqueryable.
        machine = FixtureMachine(
            {RETRODECK_JSON: RD_JSON, RETRODECK_CFG: CFG}, cores={}, dirs=[f"{ROOT}/saves"]
        )
        outcome = atlas.detect(HOME, machine)[0].soft_patch_candidates(ROM, core_so=FBNEO)
        assert isinstance(outcome, SoftPatchAnswer)
        assert atlas.CAVEAT_CORE_UNQUERYABLE in _codes(outcome)


class TestTheAnswerTravelsLikeEveryOtherAnswer:
    def test_the_aggregate_asks_every_installation(self):
        answers = atlas.EveryInstallation(
            atlas.detect(HOME, _machine())
        ).soft_patch_candidates(ROM)
        assert [a.installation.kind for a in answers] == ["retrodeck"]
        assert _answered(answers[0].answer).candidates

    def test_the_serialized_form_is_the_contract(self):
        answer = _answered(_retrodeck().soft_patch_candidates(ROM))
        serialized = atlas.soft_patch_answer_contract(answer)
        assert set(serialized) == {"candidates", "applies", "caveats"}
        assert serialized["candidates"][0] == {
            "format": "ips",
            "path": "/mnt/sd/retrodeck/roms/arcade/sf2ce.ips",
            "continuations": [
                f"/mnt/sd/retrodeck/roms/arcade/sf2ce.ips{index}" for index in range(1, 10)
            ],
            "attempted": True,
        }

    def test_a_refusal_serializes_through_the_same_pair(self):
        outcome = _retrodeck(cores={}).soft_patch_candidates(ROM, core_so=FBNEO)
        assert atlas.soft_patch_answer_contract(outcome) == {
            "unresolved": {"code": "core-not-installed", "data": {"core_so": FBNEO}}
        }


# --- the mod cards ------------------------------------------------------------

DOLPHIN_CORE = "dolphin_libretro.so"
AZAHAR_CORE = "azahar_libretro.so"
FBNEO_TREES = ("patched", "ips", "romdata")


def _card(**mods) -> str:
    return json.dumps(
        {
            "schema": MODS_SCHEMA,
            "cores": {
                "demo": {
                    "mods": {
                        "root": "system_directory",
                        "trees": [{"subdir": "demo/mods", "keying": None}],
                        **mods,
                    },
                    "provenance": {"source": "[V] a citation"},
                }
            },
        }
    )


def _standalone_card(**mods) -> str:
    return json.dumps(
        {
            "schema": MODS_SCHEMA,
            "emulators": {
                "DEMO": {
                    "mods": {
                        "base": "data",
                        "trees": [{"subdir": "demo/mods", "keying": None}],
                        **mods,
                    },
                    "provenance": {"source": "[V] a citation"},
                }
            },
        }
    )


class TestTheShippedCardsSayWhatTheyCanStandBehind:
    @pytest.mark.parametrize("card", load_mod_cards(), ids=lambda c: c.key)
    def test_a_card_that_states_a_keying_cites_it(self, card):
        for tree in card.trees:
            assert (tree.keying is None) == (tree.keying_citation is None)
            assert tree.keying is None or tree.keying in KEYINGS

    @pytest.mark.parametrize("card", load_mod_cards(), ids=lambda c: c.key)
    def test_only_a_multi_tree_card_names_roles(self, card):
        roles = [tree.role for tree in card.trees]
        assert roles == [None] if len(card.trees) == 1 else None not in roles

    @pytest.mark.parametrize("card", load_standalone_mod_cards(), ids=lambda c: c.token)
    def test_a_standalone_card_states_a_base_for_every_file_it_names(self, card):
        # A base is stated exactly where a tree hangs off one: a card whose
        # trees are all configuration keys names no XDG root at all, because
        # the root is what that configuration decides.
        fixed = [tree for tree in card.trees if tree.subdir is not None]
        assert card.base in ("data", "config") if fixed else card.base is None
        assert card.config is None or card.config.base in ("data", "config")

    def test_the_core_that_reads_three_trees_states_all_three(self):
        card = lookup_mod_card(so_basename="fbneo_libretro.so", library_name=None)
        assert card is not None
        assert tuple(tree.role for tree in card.trees) == FBNEO_TREES

    def test_the_late_registering_core_writes_its_default_down_with_the_build(self):
        # Nothing on a machine states it: the core registers its options after
        # retro_set_environment, so a probe reads none.
        card = lookup_mod_card(so_basename="fbneo_libretro.so", library_name=None)
        assert card is not None
        assert card.option is not None
        default = card.option.default
        assert default is not None
        assert default.value == "enabled"
        assert default.verified_core
        assert default.citation

    def test_the_installer_written_row_is_absent_on_purpose(self):
        # MAME's plugin directories are values RetroDECK writes into mame.ini,
        # not defaults the emulator opens. Quoting them would state an
        # arrangement's directory as an emulator's read location.
        assert lookup_standalone_mod_card("MAME") is None

    def test_the_emulator_with_no_established_switch_names_no_config(self):
        card = lookup_standalone_mod_card("AZAHAR")
        assert card is not None
        assert card.config is None

    def test_azahar_is_the_only_card_allowed_to_name_no_config(self):
        # Omitting the config silently drops emulator-config-unread, which is
        # what tells a caller where to look for the switch. Azahar earns the
        # omission because nobody has established that any switch exists; a
        # future card omitting it for a lesser reason would state a weaker
        # claim than it can, and nothing else would notice.
        silent = sorted(
            card.token for card in load_standalone_mod_cards() if card.config is None
        )
        assert silent == ["AZAHAR"]

    def test_the_core_card_that_names_a_config_states_it_below_the_root(self):
        # A core's ini sits inside the user tree it builds, so the path is
        # relative to the root the trees hang off — never an absolute path that
        # would silently discard that root.
        for card in load_mod_cards():
            if card.config is not None:
                assert not card.config.path.startswith("/")
                assert card.config.base is None

    def test_cemu_answers_the_same_directory_in_both_families(self):
        # One mechanism, one directory: a graphic pack carries texture rules and
        # may carry an assembler patch beside them.
        from atlas.textures import lookup_standalone_texture_card

        mods = lookup_standalone_mod_card("CEMU")
        textures = lookup_standalone_texture_card("CEMU")
        assert mods is not None
        assert textures is not None
        assert mods.trees[0].subdir == textures.subdir


class TestTheCardLoaderRefusesWhatItCannotStand:
    def test_a_card_without_provenance_is_refused(self):
        text = json.dumps(
            {"schema": MODS_SCHEMA, "cores": {"demo": {"mods": {"root": "system_directory", "trees": [{"subdir": "d", "keying": None}]}}}}
        )
        with pytest.raises(ValueError, match="provenance.source"):
            load_mod_cards(text)

    def test_a_root_outside_the_vocabulary_is_refused(self):
        text = _card(root="savestate_directory")
        with pytest.raises(ValueError, match="mods.root"):
            load_mod_cards(text)

    def test_a_subdir_that_escapes_its_root_is_refused(self):
        text = _card(trees=[{"subdir": "../etc", "keying": None}])
        with pytest.raises(ValueError, match="climbs out"):
            load_mod_cards(text)

    def test_a_card_with_no_tree_is_refused(self):
        text = _card(trees=[])
        with pytest.raises(ValueError, match="non-empty list of trees"):
            load_mod_cards(text)

    def test_a_lone_tree_naming_a_role_is_refused(self):
        # The field tells several trees apart; on a single tree it would be
        # vocabulary a client has to learn to ignore.
        text = _card(trees=[{"subdir": "d", "role": "only", "keying": None}])
        with pytest.raises(ValueError, match="names no role"):
            load_mod_cards(text)

    def test_several_trees_without_roles_are_refused(self):
        text = _card(trees=[{"subdir": "a", "keying": None}, {"subdir": "b", "keying": None}])
        with pytest.raises(ValueError, match="names its role"):
            load_mod_cards(text)

    def test_a_core_tree_may_not_name_a_configuration_key(self):
        # RetroArch hands a core its root, so a setting of an emulator's own
        # has nothing to name on that side.
        text = _card(trees=[{"directory": _SETTING, "keying": None}])
        with pytest.raises(ValueError, match="handed its root by RetroArch"):
            load_mod_cards(text)


# A configured tree's setting, in the shape the loader demands.
_SETTING = {
    "section": "Folders",
    "key": "Cheats",
    "default": "cheats",
    "citation": "[V-source] a citation",
}


class TestATreeIsAFixedPlaceOrAConfiguredOne:
    def test_a_tree_states_one_shape_and_not_both(self):
        text = _standalone_card(trees=[{"subdir": "d", "directory": _SETTING, "keying": None}])
        with pytest.raises(ValueError, match="exactly one of 'subdir' and 'directory'"):
            load_standalone_mod_cards(text)

    def test_a_tree_that_states_neither_is_refused(self):
        text = _standalone_card(trees=[{"keying": None}])
        with pytest.raises(ValueError, match="exactly one of 'subdir' and 'directory'"):
            load_standalone_mod_cards(text)

    def test_a_configured_card_names_no_xdg_base(self):
        # The root is what the configuration decides, so a base beside it
        # would be a second answer to the same question.
        text = _standalone_card(base="data", trees=[{"directory": _SETTING, "keying": None}])
        with pytest.raises(ValueError, match="names a root no tree uses"):
            load_standalone_mod_cards(text)

    def test_a_fixed_card_without_a_base_is_refused(self):
        text = json.dumps(
            {
                "schema": MODS_SCHEMA,
                "emulators": {
                    "DEMO": {
                        "mods": {"trees": [{"subdir": "demo/mods", "keying": None}]},
                        "provenance": {"source": "[V] a citation"},
                    }
                },
            }
        )
        with pytest.raises(ValueError, match="mods.base is what a tree stating a subdir"):
            load_standalone_mod_cards(text)

    def test_a_setting_missing_a_field_is_refused(self):
        setting = {"section": "Folders", "key": "Cheats", "default": "cheats"}
        text = _standalone_card(base=None, trees=[{"directory": setting, "keying": None}])
        with pytest.raises(ValueError, match="section/key/default/citation"):
            load_standalone_mod_cards(text)

    def test_a_default_that_climbs_out_of_the_root_is_refused(self):
        setting = {**_SETTING, "default": "../cheats"}
        text = _standalone_card(base=None, trees=[{"directory": setting, "keying": None}])
        with pytest.raises(ValueError, match="climbs out"):
            load_standalone_mod_cards(text)

    def test_repeated_roles_are_refused(self):
        text = _card(
            trees=[
                {"subdir": "a", "role": "same", "keying": None},
                {"subdir": "b", "role": "same", "keying": None},
            ]
        )
        with pytest.raises(ValueError, match="tell the trees apart"):
            load_mod_cards(text)

    def test_an_uncited_keying_is_refused(self):
        text = _card(trees=[{"subdir": "d", "keying": {"value": "pack"}}])
        with pytest.raises(ValueError, match="keying"):
            load_mod_cards(text)

    def test_a_default_outside_the_options_own_values_is_refused(self):
        # A record contradicting itself: the answer would refuse to interpret
        # the very value the record says is in force.
        option = {
            "setting": "demo_mods",
            "values": {"enabled": True, "disabled": False},
            "default": {"value": "on", "verified_core": "1", "citation": "c"},
        }
        text = _card(option=option)
        with pytest.raises(ValueError, match="is not one of this option's values"):
            load_mod_cards(text)

    def test_a_default_without_its_build_pin_is_refused(self):
        option = {
            "setting": "demo_mods",
            "values": {"enabled": True, "disabled": False},
            "default": {"value": "enabled", "citation": "c"},
        }
        text = _card(option=option)
        with pytest.raises(ValueError, match="default"):
            load_mod_cards(text)

    def test_a_standalone_config_base_outside_the_vocabulary_is_refused(self):
        text = _standalone_card(config={"base": "cache", "path": "x.ini"})
        with pytest.raises(ValueError, match="config.base"):
            load_standalone_mod_cards(text)


class TestTheAnswerJoinsALiveRootToRecordedTrees:
    def _fbneo(self, **kwargs) -> ModPlacement:
        return mod_placed(_retrodeck(**kwargs).mod_location(core_so=FBNEO))

    def test_every_tree_hangs_off_the_root_the_core_is_handed(self):
        placement = self._fbneo()
        assert [tree.dir for tree in placement.trees] == [
            f"{ROOT}/bios/fbneo/patched",
            f"{ROOT}/bios/fbneo/ips",
            f"{ROOT}/bios/fbneo/romdata",
        ]
        assert [tree.role for tree in placement.trees] == list(FBNEO_TREES)

    def test_the_switch_is_read_from_the_options_file_first(self):
        placement = self._fbneo(
            files={
                f"{RD_APP}/config/retroarch/retroarch-core-options.cfg": (
                    'fbneo-allow-patched-romsets = "disabled"\n'
                )
            }
        )
        assert placement.enabled is False

    def test_the_recorded_default_stands_where_no_machine_states_one(self):
        assert self._fbneo().enabled is True

    def test_a_live_core_default_outranks_the_recorded_one(self):
        # The record is the last resort: a default the probe DID capture is a
        # fact about this machine's binary.
        placement = mod_placed(
            _retrodeck(
                cores={
                    f"{CORES}/{FBNEO}": {
                        "library_name": "FinalBurn Neo",
                        "options": {
                            "fbneo-allow-patched-romsets": {
                                "default": "disabled",
                                "values": ["disabled", "enabled"],
                            }
                        },
                    }
                }
            ).mod_location(core_so=FBNEO)
        )
        assert placement.enabled is False

    def test_a_build_the_default_was_not_read_at_is_stated(self):
        placement = mod_placed(
            _retrodeck(
                cores={f"{CORES}/{FBNEO}": {"library_name": "FinalBurn Neo", "library_version": "v2"}}
            ).mod_location(core_so=FBNEO)
        )
        stated = next(c for c in placement.caveats if c.code == atlas.CAVEAT_UNVERIFIED_VERSION)
        assert stated.data["core_live"] == "v2"
        assert placement.enabled is True

    def test_a_wired_tree_states_the_directory_behind_it(self):
        placement = self._fbneo(
            files={f"{ROOT}/mods/retroarch-core/fbneo/ips/readme.txt": "x"},
            symlinks={f"{ROOT}/bios/fbneo/ips": f"{ROOT}/mods/retroarch-core/fbneo/ips"},
        )
        by_role = {tree.role: tree for tree in placement.trees}
        assert by_role["ips"].physical_dir == f"{ROOT}/mods/retroarch-core/fbneo/ips"
        # The link walk is per tree: the two nobody wired keep no second path.
        assert by_role["patched"].physical_dir is None

    def test_a_root_that_is_still_a_template_leaves_the_hole_on_the_answer(self):
        placement = self._fbneo(
            files={RETRODECK_CFG: CFG + 'systemfiles_in_content_dir = "true"\n'}
        )
        assert placement.needs == (atlas.HOLE_CONTENT_DIR,)
        assert all(tree.dir.startswith("<content_dir>/") for tree in placement.trees)
        assert all(tree.physical_dir is None for tree in placement.trees)


class TestTheTwoMechanismsAreToldApart:
    def test_a_core_that_is_also_soft_patched_says_so(self):
        placement = mod_placed(
            _retrodeck(files={FBNEO_INFO: 'needs_fullpath = "false"\n'}).mod_location(core_so=FBNEO)
        )
        stated = next(c for c in placement.caveats if c.code == atlas.CAVEAT_SOFT_PATCHING_APPLIES)
        assert stated.data == {"core": "fbneo"}

    def test_a_core_handed_a_path_says_nothing_about_soft_patching(self):
        placement = mod_placed(
            _retrodeck(files={FBNEO_INFO: 'needs_fullpath = "true"\n'}).mod_location(core_so=FBNEO)
        )
        assert atlas.CAVEAT_SOFT_PATCHING_APPLIES not in _codes(placement)

    def test_an_unread_declaration_states_nothing_either(self):
        # Silence here is "nobody established it", never "it does not apply".
        assert atlas.CAVEAT_SOFT_PATCHING_APPLIES not in _codes(
            mod_placed(_retrodeck().mod_location(core_so=FBNEO))
        )


class TestTheQuestionRefusesRatherThanInventingADirectory:
    def test_a_core_no_card_reaches_is_refused_in_its_own_words(self):
        outcome = _retrodeck(
            cores={f"{CORES}/mgba_libretro.so": {"library_name": "mGBA"}}
        ).mod_location(core_so="mgba_libretro.so")
        assert isinstance(outcome, Unresolved)
        assert outcome.code == atlas.UNRESOLVED_MOD_WIRING_UNESTABLISHED

    def test_a_core_that_is_not_installed_ends_the_question(self):
        outcome = _retrodeck(cores={}).mod_location(core_so=FBNEO)
        assert isinstance(outcome, Unresolved)
        assert outcome.code == atlas.UNRESOLVED_CORE_NOT_INSTALLED

    def test_the_aggregate_asks_every_installation(self):
        answers = atlas.EveryInstallation(atlas.detect(HOME, _machine())).mod_location(
            core_so=FBNEO
        )
        assert [a.installation.kind for a in answers] == ["retrodeck"]
        assert mod_placed(answers[0].answer).trees

    def test_the_serialized_form_is_the_contract(self):
        placement = mod_placed(_retrodeck().mod_location(core_so=FBNEO))
        serialized = atlas.mod_answer_contract(placement)
        assert set(serialized) == {"trees", "needs", "enabled", "caveats"}
        assert serialized["trees"][0] == {
            "role": "patched",
            "dir": f"{ROOT}/bios/fbneo/patched",
            "physical_dir": None,
            "keying": "rom-name",
        }


class TestEmuDeckAnswersItsCoresAndRefusesItsStandaloneEmulators:
    """The arrangement with no mods hub at all — and no established XDG bases.

    EmuDeck's installer creates no mods root and links no emulator's mod
    directory anywhere, so its cores answer their own defaults with nothing
    behind them. Its standalone emulators are each their own flatpak or
    AppImage, so the bases their trees hang off differ per emulator and atlas
    has established none — which is a different refusal from RetroDECK's, where
    one flatpak pins one pair of bases for all of them.
    """

    _SETTINGS = (
        'emulationPath="/mnt/sd/Emulation"\n'
        'biosPath="/mnt/sd/Emulation/bios"\n'
        'romsPath="/mnt/sd/Emulation/roms"\n'
        'savesPath="/mnt/sd/Emulation/saves"\n'
        'storagePath="/mnt/sd/Emulation/storage"\n'
        'toolsPath="/mnt/sd/Emulation/tools"\n'
    )
    _CFG = (
        'libretro_directory = "/mnt/sd/Emulation/cores"\n'
        'system_directory = "/mnt/sd/Emulation/bios"\n'
        'savefile_directory = "/mnt/sd/Emulation/saves/retroarch/saves"\n'
    )

    def _emudeck(self) -> atlas.EmuDeck:
        return self._emudeck_with({})

    def _emudeck_with(self, extra: dict[str, FixtureFileSpec]) -> atlas.EmuDeck:
        machine = FixtureMachine(
            {
                f"{HOME}/.config/EmuDeck/settings.sh": self._SETTINGS,
                f"{HOME}/.var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg": self._CFG,
                **extra,
            },
            cores={"/mnt/sd/Emulation/cores/fbneo_libretro.so": {"library_name": "FinalBurn Neo"}},
            dirs=["/mnt/sd/Emulation/saves/retroarch/saves", "/mnt/sd/Emulation/cores"],
        )
        detected = atlas.detect(HOME, machine)[0]
        # The entry route is the concrete handle's, not the protocol's: an
        # entry asks its own installation for a placement, and that pairing is
        # what this class is about.
        assert isinstance(detected, atlas.EmuDeck)
        return detected

    def test_a_core_answers_its_own_default_with_nothing_behind_it(self):
        placement = mod_placed(self._emudeck().mod_location(core_so=FBNEO))
        assert [tree.dir for tree in placement.trees] == [
            "/mnt/sd/Emulation/bios/fbneo/patched",
            "/mnt/sd/Emulation/bios/fbneo/ips",
            "/mnt/sd/Emulation/bios/fbneo/romdata",
        ]
        assert all(tree.physical_dir is None for tree in placement.trees)

    @staticmethod
    def _dolphin_spec():
        from atlas.esde import KIND_STANDALONE, EmulatorSpec

        return EmulatorSpec(
            system="gc",
            label="Dolphin (Standalone)",
            kind=KIND_STANDALONE,
            core_so=None,
            command="%EMULATOR_DOLPHIN% -b -e %ROM%",
            provenance="test",
        )

    def test_a_standalone_entry_whose_binary_is_unestablished_names_the_variant(self):
        # Nothing under ~/Applications and no installed flatpak: the launch
        # falls through to the Windows build under Proton, whose configuration
        # nobody reads — which is a different statement from "this emulator is
        # not covered", and the one a caller can act on.
        outcome = self._emudeck().entry_mod_location(self._dolphin_spec())
        assert isinstance(outcome, Unresolved)
        assert outcome.code == atlas.UNRESOLVED_STANDALONE_VARIANT_UNESTABLISHED
        assert outcome.data["variant"] == "proton"

    def test_a_standalone_entry_answers_where_the_appimage_establishes_its_homes(self):
        emudeck = self._emudeck_with(
            {f"{HOME}/Applications/Dolphin-1234.AppImage": {"status": "invalid-text"}}
        )
        placement = mod_placed(emudeck.entry_mod_location(self._dolphin_spec()))
        assert [tree.dir for tree in placement.trees] == [
            f"{HOME}/.local/share/dolphin-emu/Load/GraphicMods"
        ]

    def test_an_emulator_no_card_covers_is_still_unsupported(self):
        from atlas.esde import KIND_STANDALONE, EmulatorSpec

        spec = EmulatorSpec(
            system="n64",
            label="Ares (Standalone)",
            kind=KIND_STANDALONE,
            core_so=None,
            command="%EMULATOR_ARES% %ROM%",
            provenance="test",
        )
        emudeck = self._emudeck_with(
            {f"{HOME}/Applications/ares-1234.AppImage": {"status": "invalid-text"}}
        )
        outcome = emudeck.entry_mod_location(spec)
        assert isinstance(outcome, Unresolved)
        assert outcome.code == atlas.UNRESOLVED_STANDALONE
