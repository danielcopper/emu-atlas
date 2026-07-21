"""Tests for atlas.placement — the save-directory template math."""

from __future__ import annotations

from atlas.placement import build_save_placement


def _placement(
    *,
    saves_root: str | None = "/saves",
    savefiles_in_content_dir: bool = False,
    sort_by_content: bool = False,
    sort_by_core: bool = False,
    core: str | None = None,
    rom_dir_name: str | None = None,
    sources: tuple[str, ...] = (),
):
    return build_save_placement(
        saves_root=saves_root,
        savefiles_in_content_dir=savefiles_in_content_dir,
        sort_by_content=sort_by_content,
        sort_by_core=sort_by_core,
        core=core,
        rom_dir_name=rom_dir_name,
        sources=sources,
    )


class TestFilename:
    def test_filename_is_always_rom_stem_srm(self):
        assert _placement().filename == "<rom_stem>.srm"

    def test_rom_stem_always_in_needs(self):
        assert "rom_stem" in _placement().needs


class TestFlatLayout:
    def test_no_sorts_is_flat_saves_root(self):
        p = _placement(sort_by_content=False, sort_by_core=False)
        assert p.dir == "/saves"
        assert p.needs == ("rom_stem",)


class TestSortByContent:
    def test_unfilled_content_hole(self):
        p = _placement(sort_by_content=True)
        assert p.dir == "/saves/<content_dir>"
        assert p.needs == ("content_dir", "rom_stem")

    def test_content_hole_filled_by_rom_dir_name(self):
        p = _placement(sort_by_content=True, rom_dir_name="gba")
        assert p.dir == "/saves/gba"
        assert p.needs == ("rom_stem",)


class TestSortByCore:
    def test_core_provided_is_appended(self):
        p = _placement(sort_by_core=True, core="mgba_libretro")
        assert p.dir == "/saves/mgba_libretro"
        assert p.needs == ("rom_stem",)

    def test_core_absent_is_hole(self):
        p = _placement(sort_by_core=True, core=None)
        assert p.dir == "/saves/<core>"
        assert p.needs == ("core", "rom_stem")

    def test_content_and_core_together(self):
        p = _placement(sort_by_content=True, sort_by_core=True, rom_dir_name="gba", core="mgba_libretro")
        assert p.dir == "/saves/gba/mgba_libretro"
        assert p.needs == ("rom_stem",)


class TestContentDirMode:
    def test_saves_next_to_rom(self):
        p = _placement(savefiles_in_content_dir=True, saves_root="/saves", sort_by_content=True)
        # content-dir mode ignores the saves root and every sort flag.
        assert p.dir == "<content_dir>"
        assert p.needs == ("content_dir", "rom_stem")

    def test_content_dir_mode_provenance(self):
        p = _placement(savefiles_in_content_dir=True)
        assert any("next to the ROM" in s for s in p.sources)


class TestSavefileDirectoryHole:
    def test_saves_root_none_is_savefile_directory_hole(self):
        p = _placement(saves_root=None, sort_by_content=True)
        assert p.dir == "<savefile_directory>/<content_dir>"
        assert p.needs == ("savefile_directory", "content_dir", "rom_stem")

    def test_needs_ordering_savefile_content_core_stem(self):
        p = _placement(saves_root=None, sort_by_content=True, sort_by_core=True)
        assert p.dir == "<savefile_directory>/<content_dir>/<core>"
        assert p.needs == ("savefile_directory", "content_dir", "core", "rom_stem")

    def test_flat_savefile_directory_hole(self):
        p = _placement(saves_root=None, sort_by_content=False, sort_by_core=False)
        assert p.dir == "<savefile_directory>"
        assert p.needs == ("savefile_directory", "rom_stem")


class TestSources:
    def test_sources_passed_through(self):
        p = _placement(sources=("retrodeck.json: paths.saves_path",))
        assert "retrodeck.json: paths.saves_path" in p.sources
