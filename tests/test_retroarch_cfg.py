"""Tests for atlas.retroarch_cfg — cfg interpretation, override chain, provenance."""

from __future__ import annotations

from atlas.retroarch_cfg import (
    EMUDECK_DEFAULTS,
    RETRODECK_DEFAULTS,
    UPSTREAM_DEFAULTS,
    interpret_cfg,
    resolve_save_layout,
)

HOME = "/home/deck"


def _cfg(text):
    return interpret_cfg(text, home=HOME, cfg_label="retroarch.cfg")


class TestDefaults:
    def test_none_text_is_all_defaults(self):
        cfg = _cfg(None)
        assert cfg.savefiles_in_content_dir is False
        assert cfg.sort_by_content is True  # RetroDECK shipped default
        assert cfg.sort_by_core is False
        assert cfg.savefile_directory is None

    def test_empty_text_is_all_defaults(self):
        cfg = _cfg("")
        assert cfg.sort_by_content is True
        assert cfg.savefile_directory is None

    def test_defaults_provenance_marks_default(self):
        cfg = _cfg(None)
        joined = "\n".join(cfg.sources)
        assert "default: sort_savefiles_by_content_enable = true (RetroDECK shipped default)" in joined
        assert "default: savefile_directory unset" in joined

    def test_upstream_defaults_sort_by_core(self):
        # RetroArch's compile-time default sorts by core (config.def.h:982).
        cfg = interpret_cfg(None, home=HOME, cfg_label="retroarch.cfg", defaults=UPSTREAM_DEFAULTS)
        assert cfg.sort_by_core is True
        assert cfg.sort_by_content is False

    def test_emudeck_defaults_flat(self):
        cfg = interpret_cfg(None, home=HOME, cfg_label="retroarch.cfg", defaults=EMUDECK_DEFAULTS)
        assert cfg.sort_by_core is False
        assert cfg.sort_by_content is False


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

    def test_literal_default_resets_to_platform_default(self):
        cfg = _cfg('savefile_directory = "default"\n')
        assert cfg.savefile_directory is None
        assert any("platform default" in s for s in cfg.sources)

    def test_blank_is_unset(self):
        cfg = _cfg('savefile_directory = ""\n')
        assert cfg.savefile_directory is None

    def test_absent_is_unset_with_default_provenance(self):
        cfg = _cfg('sort_savefiles_enable = "true"\n')
        assert cfg.savefile_directory is None
        assert any("default: savefile_directory unset" in s for s in cfg.sources)


class TestOverrideChain:
    def test_core_override_wins_over_global(self):
        cfg = resolve_save_layout(
            'sort_savefiles_by_content_enable = "true"\n',
            home=HOME,
            cfg_label="retroarch.cfg",
            defaults=RETRODECK_DEFAULTS,
            overrides=[("core override PPSSPP/PPSSPP.cfg", 'sort_savefiles_by_content_enable = "false"')],
        )
        assert cfg.sort_by_content is False
        assert any("override wins" in s for s in cfg.sources)

    def test_later_layer_wins_over_earlier(self):
        cfg = resolve_save_layout(
            'sort_savefiles_by_content_enable = "true"\n',
            home=HOME,
            cfg_label="retroarch.cfg",
            defaults=RETRODECK_DEFAULTS,
            overrides=[
                ("core override", 'sort_savefiles_by_content_enable = "false"'),
                ("game override", 'sort_savefiles_by_content_enable = "true"'),
            ],
        )
        assert cfg.sort_by_content is True
        assert any("game override" in s and "override wins" in s for s in cfg.sources)

    def test_override_touches_only_its_keys(self):
        cfg = resolve_save_layout(
            'savefile_directory = "/mnt/saves"\nsort_savefiles_enable = "true"\n',
            home=HOME,
            cfg_label="retroarch.cfg",
            defaults=RETRODECK_DEFAULTS,
            overrides=[("core override", 'sort_savefiles_by_content_enable = "false"')],
        )
        assert cfg.savefile_directory == "/mnt/saves"
        assert cfg.sort_by_core is True
        assert cfg.sort_by_content is False

    def test_override_can_set_savefile_directory(self):
        # configuration.c:7240 — an override file can set the save dir itself.
        cfg = resolve_save_layout(
            'savefile_directory = "/mnt/saves"\n',
            home=HOME,
            cfg_label="retroarch.cfg",
            defaults=RETRODECK_DEFAULTS,
            overrides=[("game override", 'savefile_directory = "/mnt/elsewhere"')],
        )
        assert cfg.savefile_directory == "/mnt/elsewhere"


class TestParsingEdgeCases:
    def test_quotes_optional(self):
        cfg = _cfg("sort_savefiles_by_content_enable = false\n")
        assert cfg.sort_by_content is False

    def test_lines_without_equals_ignored(self):
        cfg = _cfg('this is not a config line\nsort_savefiles_enable = "true"\n')
        assert cfg.sort_by_core is True

    def test_exact_key_match_no_prefix_collision(self):
        # A stray key that merely starts like a real one must not set it.
        cfg = _cfg('savefiles_in_content_dir_extra = "true"\n')
        assert cfg.savefiles_in_content_dir is False

    def test_savefile_directory_and_in_content_dir_do_not_collide(self):
        cfg = _cfg('savefile_directory = "/mnt/saves"\nsavefiles_in_content_dir = "true"\n')
        assert cfg.savefile_directory == "/mnt/saves"
        assert cfg.savefiles_in_content_dir is True

    def test_first_occurrence_wins(self):
        # config_file.c:496-507 — the map only takes a key not already present.
        cfg = _cfg('sort_savefiles_enable = "true"\nsort_savefiles_enable = "false"\n')
        assert cfg.sort_by_core is True

    def test_numeric_one_is_true(self):
        # config_get_bool accepts "1" (config_file.c:1233).
        cfg = _cfg('sort_savefiles_enable = "1"\n')
        assert cfg.sort_by_core is True

    def test_comment_lines_and_trailing_comments(self):
        cfg = _cfg('# sort_savefiles_enable = "true"\nsort_savefiles_by_content_enable = "false" # note\n')
        assert cfg.sort_by_core is False  # commented line must not count
        assert cfg.sort_by_content is False
