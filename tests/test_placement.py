"""Tests for atlas.placement — the placement type, its invariants, and layout math."""

from __future__ import annotations

import pytest

from atlas.placement import (
    Caveat,
    ROOT_CONTENT_DIRECTORY,
    ROOT_SAVEFILE_DIRECTORY,
    ROOT_SAVESTATE_DIRECTORY,
    STATE_ROOT_CONTENT_DIRECTORY,
    UNKNOWN_FILE_SET,
    FileSet,
    SavePlacement,
    SavestatePlacement,
    build_save_placement,
    build_savestate_placement,
    file_set_holes,
    needs_with_file_set,
)
from atlas.retroarch_cfg import SAVEFILE_KEYS, SAVESTATE_KEYS, resolve_layout
from tests.shipped_layouts import RETRODECK_SHIPPED



class TestInvariants:
    """M10: invalid states are constructor errors, and values are deeply immutable."""

    def test_unknown_file_set_carries_no_files(self):
        with pytest.raises(ValueError):
            FileSet("unknown", ("a.srm",), "contradiction")

    def test_unknown_file_set_carries_no_completeness_claim(self):
        with pytest.raises(ValueError):
            FileSet("unknown", (), "contradiction", complete=True)

    def test_file_set_state_vocabulary_is_closed(self):
        with pytest.raises(ValueError):
            FileSet("guessed", (), "no such state")  # type: ignore[arg-type]

    def test_root_kind_vocabulary_is_closed(self):
        with pytest.raises(ValueError):
            SavePlacement(
                dir="/saves",
                root_kind="wherever",  # type: ignore[arg-type]
                needs=(),
                file_set=UNKNOWN_FILE_SET,
                sources=(),
                caveats=(),
            )

    def test_placement_dir_must_be_non_empty(self):
        with pytest.raises(ValueError):
            SavePlacement(
                dir="",
                root_kind="savefile_directory",
                needs=(),
                file_set=UNKNOWN_FILE_SET,
                sources=(),
                caveats=(),
            )

    def test_caveat_data_is_read_only(self):
        caveat = Caveat("health", "msg", {"issue": "root-missing"})
        with pytest.raises(TypeError):
            caveat.data["issue"] = "tampered"  # type: ignore[index]
        assert caveat.data == {"issue": "root-missing"}

    def test_caveat_code_must_be_non_empty(self):
        with pytest.raises(ValueError):
            Caveat("", "msg")

HOME = "/home/deck"


def _layout(text):
    return resolve_layout(
        text, keys=SAVEFILE_KEYS, home=HOME, cfg_label="retroarch.cfg", defaults=RETRODECK_SHIPPED
    )


def _build(text, *, content_dir_path=None, content_dir_name=None, library_name=None, **kwargs):
    return build_save_placement(
        layout=_layout(text),
        platform_default_dir="/platform/saves",
        content_dir_path=content_dir_path,
        content_dir_name=content_dir_name,
        library_name=library_name,
        **kwargs,
    )


class TestRoots:
    def test_sorted_by_content_concrete(self):
        p = _build(
            'savefile_directory = "/saves"\nsort_savefiles_by_content_enable = "true"\n',
            content_dir_path="/roms/gba",
            content_dir_name="gba",
        )
        assert p.dir == "/saves/gba"
        assert p.root_kind == ROOT_SAVEFILE_DIRECTORY
        assert p.needs == ()

    def test_in_content_dir_is_content_root(self):
        p = _build(
            'savefile_directory = "/saves"\nsavefiles_in_content_dir = "true"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n',
            content_dir_path="/roms/gba",
            content_dir_name="gba",
        )
        assert p.dir == "/roms/gba"
        assert p.root_kind == ROOT_CONTENT_DIRECTORY

    def test_unset_directory_is_platform_default_root(self):
        # platform_unix.c:2133-2134 — defaults are initialized before config load;
        # an unset key means 'saves' under the config tree, never the ROM dir.
        p = _build(
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n',
            content_dir_path="/roms/gba",
            content_dir_name="gba",
        )
        assert p.dir == "/platform/saves"
        assert p.root_kind == ROOT_SAVEFILE_DIRECTORY
        assert p.needs == ()
        assert any("platform default" in s for s in p.sources)

    def test_content_root_still_sorts(self):
        # runloop.c:8785-8841 — in_content_dir picks the root; enabled sorting
        # stages still append afterwards (REVIEW H6).
        p = _build(
            'savefile_directory = "/saves"\nsavefiles_in_content_dir = "true"\n'
            'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n',
            content_dir_path="/roms/gba",
            content_dir_name="gba",
        )
        assert p.dir == "/roms/gba/gba"
        assert p.root_kind == ROOT_CONTENT_DIRECTORY


class TestHoles:
    def test_missing_content_leaves_hole(self):
        p = _build('savefile_directory = "/saves"\nsort_savefiles_by_content_enable = "true"\n')
        assert p.dir == "/saves/<content_dir>"
        assert p.needs == ("content_dir",)

    def test_missing_library_name_leaves_hole(self):
        p = _build(
            'savefile_directory = "/saves"\n'
            'sort_savefiles_by_content_enable = "false"\n'
            'sort_savefiles_enable = "true"\n'
        )
        assert p.dir == "/saves/<library_name>"
        assert p.needs == ("library_name",)

    def test_content_then_core_order(self):
        # runloop.c:8827 then :8835 — content component first, then core.
        p = _build(
            'savefile_directory = "/saves"\n'
            'sort_savefiles_by_content_enable = "true"\n'
            'sort_savefiles_enable = "true"\n',
            content_dir_name="gba",
            library_name="mGBA",
        )
        assert p.dir == "/saves/gba/mGBA"
        assert p.needs == ()

    def test_unfilled_content_dir_root_is_hole(self):
        p = _build(
            'savefiles_in_content_dir = "true"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
        )
        assert p.dir == "<content_dir>"
        assert p.needs == ("content_dir",)

    def test_one_hole_named_twice_is_named_once(self):
        # L4: the content directory really is nested under itself
        # (runloop.c:8789 then :8827), but the caller fills one value.
        p = _build(
            'savefiles_in_content_dir = "true"\n'
            'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
        )
        assert p.dir == "<content_dir>/<content_dir>"
        assert p.needs == ("content_dir",)

    def test_deduping_keeps_the_order_the_holes_appear_in(self):
        p = _build(
            'savefiles_in_content_dir = "true"\n'
            'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "true"\n'
        )
        assert p.dir == "<content_dir>/<content_dir>/<library_name>"
        assert p.needs == ("content_dir", "library_name")


class TestFileSetHoles:
    """A file-set template leaves holes too, and they join the directory's."""

    def test_a_resolved_file_set_leaves_no_hole(self):
        assert file_set_holes(("vmu_save_A1.bin", "dc_nvmem.bin")) == ()

    def test_the_save_id_token_is_a_hole(self):
        assert file_set_holes(("<save_id>.A1.bin", "<save_id>.B1.bin")) == ("save_id",)

    def test_the_rom_stem_token_is_not_a_hole(self):
        # The resolver fills it from the content path — by the time a file set
        # exists it is either substituted or the set is unknown.
        assert file_set_holes(("<rom_stem>.ps2",)) == ()

    def test_directory_holes_come_first_and_repeat_once(self):
        assert needs_with_file_set(("content_dir",), ("<save_id>.A1.bin",)) == ("content_dir", "save_id")
        assert needs_with_file_set(("save_id",), ("<save_id>.A1.bin",)) == ("save_id",)


class TestFileSetAndProvenance:
    def test_default_file_set_is_unknown_never_guessed(self):
        p = _build('savefile_directory = "/saves"\n')
        assert p.file_set is UNKNOWN_FILE_SET
        assert p.file_set.state == "unknown"
        assert p.file_set.files == ()

    def test_observed_file_set_carried_through(self):
        fs = FileSet(state="observed", files=("a.srm",), provenance="observed on the machine: /saves")
        p = _build('savefile_directory = "/saves"\n', file_set=fs)
        assert p.file_set == fs

    def test_sources_carry_layout_provenance(self):
        p = _build('savefile_directory = "/saves"\nsort_savefiles_by_content_enable = "true"\n')
        joined = "\n".join(p.sources)
        assert 'retroarch.cfg: savefile_directory = "/saves"' in joined
        assert 'retroarch.cfg: sort_savefiles_by_content_enable = "true"' in joined

    def test_caveats_carried_through(self):
        caveat = Caveat("test-code", "something degraded")
        p = _build('savefile_directory = "/saves"\n', caveats=(caveat,))
        assert p.caveats == (caveat,)
        assert p.caveats[0].code == "test-code"


class TestSavestatePlacementIsTheSaveShapeMinusOneField:
    """The fork the answer grammar makes for savestates, and why it holds.

    No core writes a savestate — the libretro API hands it no savestate
    directory and RetroArch serializes the file itself — so no rule card can
    ever state how one groups them. The field is absent rather than permanently
    ``None``, which is what these tests pin down.
    """

    def test_the_type_carries_no_granularity_at_all(self):
        assert not hasattr(_state('savestate_directory = "/states"\n'), "granularity")

    def test_it_carries_every_other_field_a_save_placement_does(self):
        save = set(SavePlacement.__dataclass_fields__)
        state = set(SavestatePlacement.__dataclass_fields__)
        assert save - state == {"granularity"}
        assert state - save == set()

    def test_root_kind_vocabulary_is_its_own(self):
        # The saves root is not a value a savestate placement can hold: a
        # client branching on it must never meet the other question's anchors.
        with pytest.raises(ValueError):
            SavestatePlacement(
                dir="/states",
                root_kind="savefile_directory",  # type: ignore[arg-type]
                needs=(),
                file_set=UNKNOWN_FILE_SET,
                sources=(),
                caveats=(),
            )

    def test_placement_dir_must_be_non_empty(self):
        with pytest.raises(ValueError):
            SavestatePlacement(
                dir="",
                root_kind=ROOT_SAVESTATE_DIRECTORY,
                needs=(),
                file_set=UNKNOWN_FILE_SET,
                sources=(),
                caveats=(),
            )


def _state(text, *, content_dir_path=None, content_dir_name=None, library_name=None, **kwargs):
    return build_savestate_placement(
        layout=resolve_layout(
            text, keys=SAVESTATE_KEYS, home=HOME, cfg_label="retroarch.cfg", defaults=RETRODECK_SHIPPED
        ),
        platform_default_dir="/platform/states",
        content_dir_path=content_dir_path,
        content_dir_name=content_dir_name,
        library_name=library_name,
        **kwargs,
    )


# RetroDECK ships sort-by-content ON for savestates too (its rd_config sets
# sort_savestates_by_content_enable = "true"), so a root test that left the flag
# to the defaults would be testing the sorting stage as well.
UNSORTED = 'sort_savestates_by_content_enable = "false"\n'


class TestSavestateRootsFollowTheSamePathMath:
    """One upstream function places both families (runloop.c:8752-8979), so one port does."""

    def test_the_configured_root_is_the_root(self):
        placement = _state(UNSORTED + 'savestate_directory = "/states"\n')
        assert placement.dir == "/states"
        assert placement.root_kind == ROOT_SAVESTATE_DIRECTORY

    def test_an_unset_root_is_the_platform_default_not_the_content_dir(self):
        placement = _state(UNSORTED, content_dir_path="/roms/gba")
        assert placement.dir == "/platform/states"
        assert placement.root_kind == ROOT_SAVESTATE_DIRECTORY

    def test_in_content_dir_roots_at_the_rom(self):
        placement = _state(
            UNSORTED + 'savestates_in_content_dir = "true"\n', content_dir_path="/roms/gba"
        )
        assert placement.dir == "/roms/gba"
        assert placement.root_kind == STATE_ROOT_CONTENT_DIRECTORY

    def test_sorting_stages_apply_in_upstream_order(self):
        placement = _state(
            'savestate_directory = "/states"\n'
            'sort_savestates_by_content_enable = "true"\n'
            'sort_savestates_enable = "true"\n',
            content_dir_name="gba",
            library_name="mGBA",
        )
        assert placement.dir == "/states/gba/mGBA"

    def test_unfilled_components_stay_holes(self):
        placement = _state(
            'savestate_directory = "/states"\n'
            'sort_savestates_by_content_enable = "true"\n'
            'sort_savestates_enable = "true"\n'
        )
        assert placement.dir == "/states/<content_dir>/<library_name>"
        assert placement.needs == ("content_dir", "library_name")
