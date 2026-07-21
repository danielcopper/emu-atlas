"""Tests for atlas.core_info — the RetroArch .info parser."""

from __future__ import annotations

from atlas.core_info import parse_core_info


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


def test_only_matching_outer_quotes_stripped():
    assert parse_core_info('display_name = "a" and "b"\n') == {"display_name": 'a" and "b'}


def test_embedded_whitespace_preserved():
    assert parse_core_info('description = "Nintendo - Game Boy Advance"\n') == {
        "description": "Nintendo - Game Boy Advance"
    }


def test_mixed_line_endings():
    assert parse_core_info('a = "1"\r\nb = "2"\r') == {"a": "1", "b": "2"}


def test_empty_text_is_empty_dict():
    assert parse_core_info("") == {}


def test_later_key_wins():
    assert parse_core_info('k = "1"\nk = "2"\n') == {"k": "2"}
