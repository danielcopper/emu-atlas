"""Tests for atlas.retroarch_cfg — the cfg-text interpretation and provenance."""

from __future__ import annotations

from atlas.retroarch_cfg import interpret_cfg

HOME = "/home/deck"


def _cfg(text):
    return interpret_cfg(text, home=HOME, cfg_label="retroarch.cfg")


class TestDefaults:
    def test_none_text_is_all_defaults(self):
        cfg = _cfg(None)
        assert cfg.savefiles_in_content_dir is False
        assert cfg.sort_by_content is True  # RetroDECK default
        assert cfg.sort_by_core is False
        assert cfg.savefile_directory is None

    def test_empty_text_is_all_defaults(self):
        cfg = _cfg("")
        assert cfg.sort_by_content is True
        assert cfg.savefile_directory is None

    def test_defaults_provenance_marks_default(self):
        cfg = _cfg(None)
        joined = "\n".join(cfg.sources)
        assert "default: sort_savefiles_by_content_enable = true (RetroDECK default)" in joined
        assert "default: savefile_directory unset" in joined


class TestSaveLayoutFlags:
    def test_all_flags_true(self):
        cfg = _cfg(
            'savefiles_in_content_dir = "true"\n'
            'sort_savefiles_by_content_enable = "true"\n'
            'sort_savefiles_enable = "true"\n'
        )
        assert cfg.savefiles_in_content_dir is True
        assert cfg.sort_by_content is True
        assert cfg.sort_by_core is True

    def test_sort_by_content_false(self):
        cfg = _cfg('sort_savefiles_by_content_enable = "false"\n')
        assert cfg.sort_by_content is False

    def test_found_flag_provenance_cites_file(self):
        cfg = _cfg('sort_savefiles_enable = "true"\n')
        assert 'retroarch.cfg: sort_savefiles_enable = "true"' in cfg.sources


class TestSavefileDirectory:
    def test_tilde_expands_against_home(self):
        cfg = _cfg('savefile_directory = "~/RetroArch/saves"\n')
        assert cfg.savefile_directory == "/home/deck/RetroArch/saves"

    def test_bare_tilde_is_home(self):
        cfg = _cfg('savefile_directory = "~"\n')
        assert cfg.savefile_directory == HOME

    def test_absolute_path_kept(self):
        cfg = _cfg('savefile_directory = "/mnt/saves"\n')
        assert cfg.savefile_directory == "/mnt/saves"

    def test_literal_default_is_unset_hole(self):
        cfg = _cfg('savefile_directory = "default"\n')
        assert cfg.savefile_directory is None
        assert any("unfilled <savefile_directory> hole" in s for s in cfg.sources)

    def test_blank_is_unset_hole(self):
        cfg = _cfg('savefile_directory = ""\n')
        assert cfg.savefile_directory is None

    def test_absent_is_unset_hole_with_default_provenance(self):
        cfg = _cfg('sort_savefiles_enable = "true"\n')
        assert cfg.savefile_directory is None
        assert any("default: savefile_directory unset" in s for s in cfg.sources)


class TestParsingEdgeCases:
    def test_quotes_optional(self):
        cfg = _cfg("sort_savefiles_by_content_enable = false\n")
        assert cfg.sort_by_content is False

    def test_lines_without_equals_ignored(self):
        cfg = _cfg("this is not a config line\nsort_savefiles_enable = \"true\"\n")
        assert cfg.sort_by_core is True

    def test_exact_key_match_no_prefix_collision(self):
        # A stray key that merely starts like a real one must not set it.
        cfg = _cfg('savefiles_in_content_dir_extra = "true"\n')
        assert cfg.savefiles_in_content_dir is False

    def test_savefile_directory_and_in_content_dir_do_not_collide(self):
        cfg = _cfg('savefile_directory = "/mnt/saves"\nsavefiles_in_content_dir = "true"\n')
        assert cfg.savefile_directory == "/mnt/saves"
        assert cfg.savefiles_in_content_dir is True

    def test_last_occurrence_wins(self):
        cfg = _cfg('sort_savefiles_enable = "true"\nsort_savefiles_enable = "false"\n')
        assert cfg.sort_by_core is False
