"""Tests for atlas.yaml_scalars — what the reader reads, and what it refuses.

The reader exists to answer two emulators' path questions without a YAML
dependency, so the tests come in two halves: the shapes those files really
have, and the constructs the reader must refuse rather than guess at.
"""

import pytest

from atlas.yaml_scalars import (
    REFUSAL_ANCHOR,
    REFUSAL_NOT_A_FLAT_MAPPING,
    REFUSAL_SECOND_DOCUMENT,
    REFUSAL_SUBSTITUTION_CYCLE,
    REFUSAL_SUBSTITUTION_UNKNOWN,
    REFUSAL_TAG,
    read_scalars,
)

# RPCS3's vfs.yml, shortened to the shape that matters: a variable key, paths
# composed off it, an empty quoted scalar, and one nested block.
RPCS3_VFS = """$(EmulatorDir): /storage/rpcs3/
/dev_hdd0/: $(EmulatorDir)dev_hdd0/
/games/: /roms/ps3
/app_home/: ""
/dev_usb***/:
  /dev_usb000:
    Path: $(EmulatorDir)dev_usb000/
    Serial: ""
"""

# Vita3K's config.yml opens with a document marker and mixes scalars with lists.
VITA3K_CONFIG = """---
initial-setup: true
pref-path: /storage/psvita/Vita3K/
lle-modules: []
controller-binds:
  - a
  - b
resolution-multiplier: 1
"""


class TestTheFilesItExistsFor:
    def test_rpcs3_composes_every_device_path_off_its_variable(self):
        read = read_scalars(RPCS3_VFS)
        assert read.refusal is None
        assert read.get("/dev_hdd0/") == "/storage/rpcs3/dev_hdd0/"
        assert read.get("/games/") == "/roms/ps3"

    def test_an_empty_quoted_scalar_is_the_empty_string(self):
        # Not None: the file states a value and it is empty, which is a
        # different fact from a key nobody wrote.
        assert read_scalars(RPCS3_VFS).get("/app_home/") == ""

    def test_the_nested_block_is_skipped_by_name(self):
        read = read_scalars(RPCS3_VFS)
        assert read.skipped == ("/dev_usb***/",)
        # Its own lines never become keys of their own.
        assert "Path" not in read.values
        assert "/dev_usb000" not in read.values

    def test_asking_for_a_skipped_key_raises_rather_than_answering_none(self):
        # The discriminating case: unread is not absent, and a caller that
        # cannot tell them apart would state a default nobody configured.
        read = read_scalars(RPCS3_VFS)
        with pytest.raises(KeyError, match="unread, not absent"):
            read.get("/dev_usb***/")

    def test_vita3k_opens_with_a_document_marker_and_still_reads(self):
        read = read_scalars(VITA3K_CONFIG)
        assert read.refusal is None
        assert read.get("pref-path") == "/storage/psvita/Vita3K/"
        assert read.get("resolution-multiplier") == "1"

    def test_lists_are_skipped_whichever_way_they_are_written(self):
        read = read_scalars(VITA3K_CONFIG)
        # A block list and a flow list are the same fact about the value, and
        # neither is a scalar — reporting the flow one as the two-character
        # string "[]" would state a value nothing configured.
        assert "controller-binds" in read.skipped
        assert "lle-modules" in read.skipped
        with pytest.raises(KeyError):
            read.get("lle-modules")

    def test_a_flow_mapping_is_skipped_too(self):
        read = read_scalars("led: {r: 1, g: 2}\nnext: 1\n")
        assert read.skipped == ("led",)
        assert read.get("next") == "1"


class TestWhatItRefusesWholesale:
    @pytest.mark.parametrize(
        ("text", "code"),
        [
            ("a: &anchor 1\nb: *anchor\n", REFUSAL_ANCHOR),
            ("a: !!str 1\n", REFUSAL_TAG),
            ("a: 1\n---\na: 2\n", REFUSAL_SECOND_DOCUMENT),
            ("- just\n- a list\n", REFUSAL_NOT_A_FLAT_MAPPING),
            ("  indented: under nothing\n", REFUSAL_NOT_A_FLAT_MAPPING),
            ("$(A): $(B)x\nb: 1\n", REFUSAL_SUBSTITUTION_UNKNOWN),
        ],
    )
    def test_a_construct_beyond_the_reader_refuses_the_file(self, text, code):
        read = read_scalars(text)
        assert read.refusal == code
        # A refused file reports no lines at all: an alias can change what a
        # plain-looking line above it means.
        assert read.values == {}

    def test_a_substitution_cycle_refuses_instead_of_looping(self):
        read = read_scalars("$(A): $(B)\n$(B): $(A)\n")
        assert read.refusal == REFUSAL_SUBSTITUTION_CYCLE

    def test_content_after_a_document_end_marker_is_not_read(self):
        read = read_scalars("a: 1\n...\nb: 2\n")
        assert read.refusal is None
        assert read.get("a") == "1"
        assert read.get("b") is None


class TestScalarsAsWritten:
    def test_a_hash_inside_a_word_stays_in_the_value(self):
        # '#' opens a comment only after whitespace, so a path or a colour
        # keeps it.
        assert read_scalars("colour: ff#00aa\n").get("colour") == "ff#00aa"

    def test_a_trailing_comment_comes_off_a_bare_scalar(self):
        assert read_scalars("path: /tmp/x # where it goes\n").get("path") == "/tmp/x"

    def test_a_quoted_scalar_keeps_what_the_quotes_wrap(self):
        assert read_scalars("path: '/tmp/x # y'\n").get("path") == "/tmp/x # y"

    def test_an_unterminated_quote_stays_verbatim(self):
        # Visibly odd rather than invented — the same stance the marker and
        # melonDS readers take.
        assert read_scalars('path: "/tmp/x\n').get("path") == '"/tmp/x'

    def test_a_comment_line_and_a_blank_line_are_not_keys(self):
        read = read_scalars("# a note\n\nkey: value\n")
        assert read.values == {"key": "value"}

    def test_a_key_with_no_value_and_no_block_is_the_empty_string(self):
        read = read_scalars("empty:\nnext: 1\n")
        assert read.get("empty") == ""
        assert "empty" not in read.skipped

    def test_a_multi_line_scalar_is_skipped_by_name(self):
        read = read_scalars("note: |\n  first\n  second\nnext: 1\n")
        assert read.skipped == ("note",)
        assert read.get("next") == "1"
