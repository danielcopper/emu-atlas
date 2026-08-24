"""Tests for atlas.qt_ini — the Qt settings read, and the one reading beside it.

``values`` interprets nothing, so its tests are about the text. ``from_chars_bool``
mirrors an upstream function two emulators share, so its tests are about that
function's corners — including the ones a reasonable ini reader would not have.
"""

import pytest

from atlas.qt_ini import from_chars_bool, unescape_section, values


class TestSectionsAndValues:
    def test_a_section_name_keeps_its_escaped_space(self):
        assert unescape_section("Data%20Storage") == "Data Storage"

    def test_an_invalid_escape_stays_as_written(self):
        assert unescape_section("Data%2ZStorage") == "Data%2ZStorage"

    def test_keys_are_read_under_the_section_above_them(self):
        read = values("[MemoryCards]\nSlot1_Enable = true\n[Folders]\nBios = /b\n")
        assert read == {
            ("MemoryCards", "Slot1_Enable"): "true",
            ("Folders", "Bios"): "/b",
        }

    def test_comments_and_blank_lines_are_not_keys(self):
        assert values("; a note\n\n# another\n[S]\nk = v\n") == {("S", "k"): "v"}

    def test_a_value_keeps_every_equals_sign_after_the_first(self):
        assert values("[S]\nk = a=b\n") == {("S", "k"): "a=b"}


class TestTheBooleanTwoEmulatorsShare:
    """``StringUtil::FromChars<bool>``, as PCSX2 v2.6.3 and DuckStation apply it."""

    @pytest.mark.parametrize("word", ["true", "yes", "on", "1", "enabled"])
    def test_every_spelling_of_true_reads_as_true(self, word):
        assert from_chars_bool(word) is True

    @pytest.mark.parametrize("word", ["false", "no", "off", "0", "disabled"])
    def test_every_spelling_of_false_reads_as_false(self, word):
        assert from_chars_bool(word) is False

    @pytest.mark.parametrize("word", ["TRUE", "True", "Yes", "ON", "Disabled", "OFF"])
    def test_the_comparison_ignores_case(self, word):
        assert from_chars_bool(word) is not None

    def test_a_hand_edited_one_is_on(self):
        # The case this mirror was written for: `= 1` used to read as off,
        # because atlas compared the value against the word "true".
        assert from_chars_bool("1") is True

    @pytest.mark.parametrize("prefix,expected", [("t", True), ("tr", True), ("y", True), ("d", False), ("f", False)])
    def test_a_prefix_of_a_spelling_matches_it(self, prefix, expected):
        # Strncasecmp compares only as many characters as the value has, so
        # the test is a prefix match. Upstream's, not an approximation of it.
        assert from_chars_bool(prefix) is expected

    def test_a_lone_o_is_true_because_the_true_list_is_tested_first(self):
        # It is a prefix of both "on" and "off"; the true branch runs first.
        assert from_chars_bool("o") is True

    def test_an_empty_value_compares_nothing_and_so_matches(self):
        # Zero characters compared is a match, and the true list is first.
        # SimpleIni hands back "" rather than a null pointer, so GetBoolValue
        # gets this far.
        assert from_chars_bool("") is True

    def test_an_absent_key_is_not_a_value_at_all(self):
        assert from_chars_bool(None) is None

    @pytest.mark.parametrize("word", ["truthy", "nope", "2", "-1", "onwards", "sure"])
    def test_a_value_that_is_neither_leaves_the_default_standing(self, word):
        # GetBoolValue returns false without touching the caller's variable,
        # so the compiled default governs — which is not "false".
        assert from_chars_bool(word) is None
