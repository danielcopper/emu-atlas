"""Tests for atlas.retroarch_cfg — cfg interpretation, override chain, provenance."""

from __future__ import annotations

from atlas.retroarch_cfg import (
    EMUDECK_DEFAULTS,
    IGNORED_LINE_DROPPED,
    IGNORED_VALUE_REJECTED,
    RETRODECK_DEFAULTS,
    UPSTREAM_DEFAULTS,
    cfg_bool,
    interpret_cfg,
    is_app_relative,
    parse_cfg,
    parse_cfg_text,
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

    def test_uppercase_default_is_not_the_unset_spelling(self):
        # string_is_equal is case-sensitive (configuration.c:6918): "DEFAULT" is
        # an ordinary (relative, non-existent) path, not a reset.
        cfg = _cfg('savefile_directory = "DEFAULT"\n')
        assert cfg.savefile_directory == "DEFAULT"

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


class TestParserFidelity:
    """The line grammar of ``config_file_parse_line`` (config_file.c:524-632).

    Every expectation here was compared against the real parser: the pinned
    ``config_file.c`` compiled around a harness and fed the same lines.
    """

    def test_key_tight_against_equals_drops_the_line(self):
        # '=' is a graph character, so the key scan swallows it and the next
        # non-whitespace character is not '=' — the entry is refused (:596-623).
        assert parse_cfg_text('savefile_directory="/mnt/saves"\n') == {}

    def test_space_only_after_the_key_drops_the_line(self):
        assert parse_cfg_text('savefile_directory= "/mnt/saves"\n') == {}

    def test_space_only_before_the_equals_is_accepted(self):
        assert parse_cfg_text('savefile_directory ="/mnt/saves"\n') == {"savefile_directory": "/mnt/saves"}

    def test_tab_separates_the_key_from_the_equals(self):
        assert parse_cfg_text('savefile_directory\t= "/x"\n') == {"savefile_directory": "/x"}

    def test_unquoted_value_stops_at_the_first_space(self):
        # config_file.c:249 — an unquoted value is a run of graph characters.
        assert parse_cfg_text("savefile_directory = /mnt/my saves\n") == {"savefile_directory": "/mnt/my"}

    def test_quoted_value_stops_at_the_next_quote(self):
        assert parse_cfg_text('savefile_directory = "/mnt/x" junk\n') == {"savefile_directory": "/mnt/x"}

    def test_unterminated_quote_takes_the_rest_of_the_line(self):
        assert parse_cfg_text('savefile_directory = "/mnt/x\n') == {"savefile_directory": "/mnt/x"}

    def test_empty_value_sets_the_key_to_empty(self):
        assert parse_cfg_text('savefile_directory = ""\n') == {"savefile_directory": ""}

    def test_hash_inside_the_first_literal_is_not_a_comment(self):
        assert parse_cfg_text('savefile_directory = "/mnt/a#b"\n') == {"savefile_directory": "/mnt/a#b"}

    def test_third_quote_does_not_reopen_a_literal(self):
        # config_file_strip_comment weighs only the FIRST TWO quotes against the
        # first '#' (config_file.c:180-196), so the '#' here is a comment and
        # the value ends at the closing quote of the first literal.
        assert parse_cfg_text('savefile_directory = "/mnt/a" "b#c"\n') == {"savefile_directory": "/mnt/a"}

    def test_indented_hash_truncates_instead_of_commenting_the_line(self):
        # Only a '#' in column zero makes the whole line a comment: the raw line
        # is not trimmed first (config_file.c:170).
        assert parse_cfg_text('  # savefile_directory = "/x"\n') == {}

    def test_carriage_returns_do_not_leak_into_values(self):
        assert parse_cfg_text('savefile_directory = "/x"\r\nsort_savefiles_enable = true\r\n') == {
            "savefile_directory": "/x",
            "sort_savefiles_enable": "true",
        }

    def test_dropped_line_records_the_key_it_aimed_at(self):
        dropped = parse_cfg('savefile_directory="/mnt/saves"\n').dropped
        assert [(d.key, d.line) for d in dropped] == [
            ("savefile_directory", 'savefile_directory="/mnt/saves"')
        ]

    def test_accepted_lines_are_not_reported_as_dropped(self):
        assert parse_cfg('savefile_directory = "/mnt/saves"\n').dropped == ()


class TestNulTerminatedText:
    """A NUL ends the file, not just its line (``config_file.c:461-517``).

    The C parses a NUL-terminated buffer and cuts each line with
    ``strchr(line, '\\n')``, which stops at the first NUL: that line ends there,
    and the missing newline then breaks the loop, so the rest of the file is
    never read. The seam can deliver such a text — a cfg NUL-padded by an
    unclean shutdown decodes as UTF-8 without complaint.
    """

    def test_nul_ends_the_value_it_appears_in(self):
        assert parse_cfg_text('savefile_directory = "a\x00b"\n') == {"savefile_directory": "a"}

    def test_nothing_after_the_nul_is_read(self):
        assert parse_cfg_text('savefile_directory = "/x"\x00\nsort_savefiles_enable = true\n') == {
            "savefile_directory": "/x"
        }

    def test_the_nul_line_is_dropped_and_earlier_lines_survive(self):
        parsed = parse_cfg(
            'sort_savefiles_enable = true\nsavefile_directory\x00 = "/x"\nsavefiles_in_content_dir = true\n'
        )
        assert parsed.values == {"sort_savefiles_enable": "true"}
        assert [(d.key, d.line) for d in parsed.dropped] == [("savefile_directory", "savefile_directory")]

    def test_a_leading_nul_makes_the_whole_file_empty(self):
        # `while (line && *line)` never enters its body (config_file.c:462).
        assert parse_cfg_text('\x00savefile_directory = "/x"\n') == {}

    def test_a_key_past_the_nul_does_not_reach_the_answer(self):
        # The unsafe direction: reading on would attest a layout RetroArch never
        # applies. RetroDECK ships sort-by-content, so the key past the NUL
        # must not turn sort-by-core on.
        cfg = _cfg('savefile_directory = "/mnt/saves"\x00\nsort_savefiles_enable = "true"\n')
        assert cfg.savefile_directory == "/mnt/saves"
        assert cfg.sort_by_core is False


class TestBooleanVocabulary:
    def test_accepted_spellings(self):
        # config_file.c:1227-1262 — exactly these four, case-sensitively.
        assert cfg_bool("1") is True
        assert cfg_bool("true") is True
        assert cfg_bool("0") is False
        assert cfg_bool("false") is False

    def test_everything_else_is_refused(self):
        for value in ("TRUE", "True", "yes", "on", "", "2", " true"):
            assert cfg_bool(value) is None, value

    def test_unknown_value_keeps_the_default_it_does_not_mean_false(self):
        # RetroDECK ships sort-by-content true; "yes" must not turn it off.
        cfg = _cfg('sort_savefiles_by_content_enable = "yes"\n')
        assert cfg.sort_by_content is True

    def test_uppercase_true_is_not_true(self):
        cfg = interpret_cfg(
            'sort_savefiles_enable = "TRUE"\n',
            home=HOME,
            cfg_label="retroarch.cfg",
            defaults=EMUDECK_DEFAULTS,
        )
        assert cfg.sort_by_core is False  # the EmuDeck default stands, unchanged

    def test_rejected_override_value_leaves_the_previous_layer_standing(self):
        cfg = resolve_save_layout(
            'sort_savefiles_by_content_enable = "false"\n',
            home=HOME,
            cfg_label="retroarch.cfg",
            defaults=RETRODECK_DEFAULTS,
            overrides=[("core override", 'sort_savefiles_by_content_enable = "yes"')],
        )
        assert cfg.sort_by_content is False


class TestIgnoredSettings:
    def test_rejected_value_is_reported_with_layer_key_and_value(self):
        cfg = resolve_save_layout(
            "",
            home=HOME,
            cfg_label="retroarch.cfg",
            defaults=RETRODECK_DEFAULTS,
            overrides=[("core override", 'sort_savefiles_enable = "yes"')],
        )
        assert [(i.kind, i.layer, i.key, i.text) for i in cfg.ignored] == [
            (IGNORED_VALUE_REJECTED, "core override", "sort_savefiles_enable", "yes")
        ]

    def test_dropped_line_aiming_at_a_governing_key_is_reported(self):
        cfg = _cfg('savefile_directory="/mnt/saves"\n')
        assert cfg.savefile_directory is None
        assert [(i.kind, i.key, i.text) for i in cfg.ignored] == [
            (IGNORED_LINE_DROPPED, "savefile_directory", 'savefile_directory="/mnt/saves"')
        ]

    def test_dropped_line_for_another_key_is_not_reported(self):
        # RetroArch is silent about it and the answer does not depend on it.
        assert _cfg('video_driver="gl"\n').ignored == ()

    def test_a_clean_cfg_reports_nothing(self):
        assert _cfg('savefile_directory = "/mnt/saves"\nsort_savefiles_enable = "true"\n').ignored == ()


class TestApplicationRelativePaths:
    def test_colon_prefix_is_recognised(self):
        assert is_app_relative(":/saves") is True
        assert is_app_relative("/saves") is False
        assert is_app_relative("~/saves") is False

    def test_colon_value_is_kept_as_configured_not_read_as_unset(self):
        cfg = _cfg('savefile_directory = ":/saves"\n')
        assert cfg.savefile_directory == ":/saves"
