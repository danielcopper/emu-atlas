"""Tests for atlas.placement — the placement type, its invariants, and layout math."""

from __future__ import annotations

import pytest

from atlas.placement import (
    Caveat,
    ROOT_CONTENT_DIRECTORY,
    ROOT_SAVEFILE_DIRECTORY,
    UNKNOWN_FILE_SET,
    FileSet,
    SavePlacement,
    build_save_placement,
)
from atlas.retroarch_cfg import RETRODECK_DEFAULTS, resolve_save_layout


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
    return resolve_save_layout(text, home=HOME, cfg_label="retroarch.cfg", defaults=RETRODECK_DEFAULTS)


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
        # platform_unix.c:1844 — defaults are initialized before config load;
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


class TestFileSetAndProvenance:
    def test_default_file_set_is_unknown_never_guessed(self):
        p = _build('savefile_directory = "/saves"\n')
        assert p.file_set is UNKNOWN_FILE_SET
        assert p.file_set.state == "unknown"
        assert p.file_set.files == ()

    def test_observed_file_set_carried_through(self):
        fs = FileSet(state="observed", files=("a.srm",), source="observed on the machine: /saves")
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
