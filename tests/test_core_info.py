"""Tests for atlas.core_info — the RetroArch .info reader.

The format half is ``config_file.c``'s grammar (the parser is shared with
``retroarch.cfg``, see ``tests/test_retroarch_cfg.py``); what is tested here is
that ``.info`` files really go through it, plus the firmware enumeration
``core_info_resolve_firmware`` performs on top of it.
"""

from __future__ import annotations

from atlas.core_info import enumerate_firmware, firmware_key, parse_core_info


def test_basic_key_value():
    assert parse_core_info('corename = "mGBA"\n') == {"corename": "mGBA"}


def test_multiple_pairs():
    text = 'corename = "mGBA"\nsupported_extensions = "gba|gb"\nfirmware_count = "1"\n'
    parsed = parse_core_info(text)
    assert parsed["corename"] == "mGBA"
    assert parsed["supported_extensions"] == "gba|gb"
    assert parsed["firmware_count"] == "1"


def test_comments_and_blank_lines_ignored():
    text = "# a comment\n\ncorename = \"mGBA\"\n   # indented comment\n"
    assert parse_core_info(text) == {"corename": "mGBA"}


def test_lines_without_equals_ignored():
    assert parse_core_info("just some prose\ncorename = \"x\"\n") == {"corename": "x"}


def test_empty_key_ignored():
    assert parse_core_info(' = "value"\n') == {}


def test_unquoted_value_kept():
    assert parse_core_info("firmware_count = 2\n") == {"firmware_count": "2"}


def test_a_quoted_value_ends_at_the_next_quote():
    # config_file_extract_value stops at the NEXT quote, not the last one on
    # the line (config_file.c:222-240) — the rest of the line is not the value.
    assert parse_core_info('display_name = "a" and "b"\n') == {"display_name": "a"}


def test_an_unclosed_quote_runs_to_the_end_of_the_line():
    assert parse_core_info('display_name = "a and b\n') == {"display_name": "a and b"}


def test_a_comment_after_a_closed_quote_is_stripped():
    assert parse_core_info('corename = "mGBA" # the good one\n') == {"corename": "mGBA"}


def test_a_hash_inside_a_string_literal_is_not_a_comment():
    assert parse_core_info('display_name = "Game #1"\n') == {"display_name": "Game #1"}


def test_a_key_written_tight_against_the_equals_sets_nothing():
    # The key is the whole graph run, so it swallows the '=' and the line has
    # none left (config_file.c:596-623).
    assert parse_core_info('corename="mGBA"\n') == {}


def test_embedded_whitespace_preserved():
    assert parse_core_info('description = "Nintendo - Game Boy Advance"\n') == {
        "description": "Nintendo - Game Boy Advance"
    }


def test_mixed_line_endings():
    assert parse_core_info('a = "1"\r\nb = "2"\r') == {"a": "1", "b": "2"}


def test_empty_text_is_empty_dict():
    assert parse_core_info("") == {}


def test_first_key_wins():
    # config_file.c:670-676 maps a key only when it is not already mapped.
    assert parse_core_info('k = "1"\nk = "2"\n') == {"k": "1"}


def test_nothing_past_a_nul_is_read():
    assert parse_core_info('a = "1"\n\x00b = "2"\n') == {"a": "1"}


class TestTheFirmwareEnumeration:
    """``core_info_resolve_firmware`` (core_info.c:1572-1629) — the count is the bound."""

    def test_a_declared_slot_is_read_whole(self):
        fields = parse_core_info(
            'firmware_count = "1"\n'
            'firmware0_path = "dc/dc_boot.bin"\n'
            'firmware0_desc = "dc_boot.bin (Dreamcast BIOS)"\n'
            'firmware0_opt = "false"\n'
        )
        (slot,) = enumerate_firmware(fields).slots
        assert (slot.index, slot.path, slot.description, slot.optional) == (
            0,
            "dc/dc_boot.bin",
            "dc_boot.bin (Dreamcast BIOS)",
            False,
        )

    def test_without_a_count_nothing_is_enumerated(self):
        fields = parse_core_info('firmware0_path = "a.bin"\nfirmware1_path = "b.bin"\n')
        enumeration = enumerate_firmware(fields)
        assert enumeration.slots == ()
        assert enumeration.count == ""
        assert enumeration.unread == ("firmware0_path", "firmware1_path")

    def test_a_count_that_is_not_a_number_enumerates_nothing(self):
        fields = parse_core_info('firmware_count = "two"\nfirmware0_path = "a.bin"\n')
        enumeration = enumerate_firmware(fields)
        assert enumeration.slots == ()
        assert enumeration.count == "two"
        assert enumeration.unread == ("firmware0_path",)

    def test_declarations_past_the_count_are_invisible(self):
        fields = parse_core_info(
            'firmware_count = "1"\nfirmware0_path = "a.bin"\nfirmware1_path = "b.bin"\n'
        )
        enumeration = enumerate_firmware(fields)
        assert [slot.path for slot in enumeration.slots] == ["a.bin"]
        assert enumeration.unread == ("firmware1_path",)

    def test_a_count_larger_than_the_declared_paths_leaves_the_surplus_empty(self):
        # The shape FreeIntvTSOverlay ships: count 2, one path, slot 1 zeroed.
        fields = parse_core_info(
            'firmware_count = "2"\nfirmware0_path = "exec.bin"\nfirmware1_desc = "grom.bin"\n'
        )
        enumeration = enumerate_firmware(fields)
        assert [slot.path for slot in enumeration.slots] == ["exec.bin"]
        assert enumeration.unread == ()

    def test_an_empty_path_declares_nothing(self):
        fields = parse_core_info('firmware_count = "1"\nfirmware0_path = ""\n')
        enumeration = enumerate_firmware(fields)
        assert enumeration.slots == ()
        assert enumeration.unread == ()

    def test_only_retroarchs_own_boolean_vocabulary_means_optional(self):
        for raw, optional in (
            ("true", True),
            ("1", True),
            ("false", False),
            ("0", False),
            ("TRUE", False),
            ("True", False),
            (" true ", False),
            ("yes", False),
        ):
            fields = parse_core_info(f'firmware_count = "1"\nfirmware0_path = "a.bin"\nfirmware0_opt = "{raw}"\n')
            (slot,) = enumerate_firmware(fields).slots
            assert slot.optional is optional, raw

    def test_a_missing_opt_means_required(self):
        fields = parse_core_info('firmware_count = "1"\nfirmware0_path = "a.bin"\n')
        (slot,) = enumerate_firmware(fields).slots
        assert slot.optional is False

    def test_an_index_spelled_any_other_way_is_never_looked_up(self):
        # RetroArch composes the key, so only the exact spelling is read;
        # 'firmware00_path' still reads as a declaration to a human, hence
        # 'unread'.
        fields = parse_core_info('firmware_count = "2"\nfirmware00_path = "a.bin"\n')
        enumeration = enumerate_firmware(fields)
        assert enumeration.slots == ()
        assert enumeration.unread == ("firmware00_path",)

    def test_a_non_ascii_index_never_becomes_a_key_at_all(self):
        # A key is a run of graph characters in the C locale, and the line then
        # has no '=' behind it, so the parser drops it (config_file.c:596-623).
        assert parse_core_info('firmware٠_path = "b.bin"\n') == {}

    def test_a_repeated_declaration_is_read_once_as_the_first_one(self):
        fields = parse_core_info(
            'firmware_count = "2"\nfirmware0_path = "exec.bin"\nfirmware0_path = "grom.bin"\n'
        )
        assert [slot.path for slot in enumerate_firmware(fields).slots] == ["exec.bin"]

    def test_the_count_accepts_what_strtoul_accepts(self):
        for raw, paths in (
            ("1", ["a.bin"]),
            ("0x1", ["a.bin"]),
            ("01", ["a.bin"]),
            (" 1", ["a.bin"]),
            ("+1", ["a.bin"]),
            ("1 ", []),
            ("1 file", []),
            ("-1", []),
            ("", []),
        ):
            fields = parse_core_info(f'firmware_count = "{raw}"\nfirmware0_path = "a.bin"\n')
            assert [slot.path for slot in enumerate_firmware(fields).slots] == paths, raw

    def test_an_unreachable_count_is_not_walked(self):
        # Four billion slots is a calloc upstream refuses outright
        # (core_info.c:1584-1588); what the file declares is still read, and a
        # high index the count does not reach still is not.
        fields = parse_core_info('firmware_count = "4000000000"\nfirmware0_path = "a.bin"\n')
        assert [slot.path for slot in enumerate_firmware(fields).slots] == ["a.bin"]
        sparse = parse_core_info('firmware_count = "2"\nfirmware999999999_path = "a.bin"\n')
        enumeration = enumerate_firmware(sparse)
        assert enumeration.slots == ()
        assert enumeration.unread == ("firmware999999999_path",)

    def test_the_key_loses_its_separator_past_index_99(self):
        # char prefix[12] holds "firmware" plus three bytes (core_info.c:1577).
        assert firmware_key(0, "path") == "firmware0_path"
        assert firmware_key(99, "opt") == "firmware99_opt"
        assert firmware_key(100, "path") == "firmware100path"
        # And that spelling is the one a run of 101 slots reads.
        fields = parse_core_info('firmware_count = "101"\nfirmware100path = "a.bin"\n')
        (slot,) = enumerate_firmware(fields).slots
        assert (slot.index, slot.path) == (100, "a.bin")
