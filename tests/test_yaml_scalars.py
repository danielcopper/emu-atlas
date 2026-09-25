"""Tests for atlas.yaml_scalars — what the reader reads, and what it refuses.

The reader exists to answer two emulators' path questions without a YAML
dependency, so the tests come in two halves: the shapes those files really
have, and the constructs the reader must refuse rather than guess at.
"""

import pytest

from atlas import yaml_scalars
from atlas.yaml_scalars import (
    REFUSAL_ANCHOR,
    REFUSAL_CODES,
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

    def test_a_fallback_fills_a_token_the_file_leaves_empty(self):
        # RPCS3's own rule: an empty $(EmulatorDir) means its config directory
        # (get_emu_dir, system_utils.cpp:146-150, called at System.cpp:395 and
        # passed at :483 into vfs_config.cpp:50 at build 7c6b3dcd), so the
        # caller supplies what the emulator would use and the file's own value
        # still wins where it has one.
        text = '$(EmulatorDir): ""\n/dev_hdd0/: $(EmulatorDir)dev_hdd0/\n'
        read = read_scalars(text, fallbacks={"$(EmulatorDir)": "/config/rpcs3/"})
        assert read.get("/dev_hdd0/") == "/config/rpcs3/dev_hdd0/"

    def test_the_files_own_value_outranks_the_fallback(self):
        text = "$(EmulatorDir): /storage/\n/dev_hdd0/: $(EmulatorDir)dev_hdd0/\n"
        read = read_scalars(text, fallbacks={"$(EmulatorDir)": "/config/rpcs3/"})
        assert read.get("/dev_hdd0/") == "/storage/dev_hdd0/"

    def test_a_token_the_file_never_defines_falls_back_too(self):
        read = read_scalars(
            "/dev_hdd0/: $(EmulatorDir)dev_hdd0/\n",
            fallbacks={"$(EmulatorDir)": "/config/rpcs3/"},
        )
        assert read.get("/dev_hdd0/") == "/config/rpcs3/dev_hdd0/"

    def test_a_substitution_cycle_refuses_instead_of_looping(self):
        read = read_scalars("$(A): $(B)\n$(B): $(A)\n")
        assert read.refusal == REFUSAL_SUBSTITUTION_CYCLE

    def test_many_tokens_on_one_line_are_one_link_not_many(self):
        # Twelve different keys named once each is depth *one* and no cycle at
        # all. The bound used to count replacements, so this refused as one.
        text = "$(v): x\np: " + "$(v)" * 12 + "\n"
        read = read_scalars(text)
        assert read.refusal is None
        assert read.get("p") == "x" * 12

    def test_a_chain_longer_than_the_bound_still_refuses(self):
        # What the bound is actually for: each key refers to the next, so the
        # links are what has to stay countable.
        depth = 9
        lines = [f"$(k{i}): $(k{i + 1})" for i in range(depth)]
        lines += [f"$(k{depth}): end", "p: $(k0)"]
        assert read_scalars("\n".join(lines) + "\n").refusal == REFUSAL_SUBSTITUTION_CYCLE

    def test_a_chain_inside_the_bound_resolves(self):
        lines = [f"$(k{i}): $(k{i + 1})" for i in range(3)]
        lines += ["$(k3): end", "p: $(k0)"]
        assert read_scalars("\n".join(lines) + "\n").get("p") == "end"

    def test_an_unterminated_token_ends_the_chain_rather_than_refusing(self):
        # `$(` with no `)` is not a token; the text stays as written, the way
        # an unterminated quote does.
        assert read_scalars("p: /tmp/$(oops\n").get("p") == "/tmp/$(oops"

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

    def test_a_tab_before_the_hash_opens_a_comment_too(self):
        # YAML asks for whitespace before the '#', not for a space.
        assert read_scalars("path: /tmp/x\t# note\n").get("path") == "/tmp/x"

    def test_two_markers_with_nothing_between_are_still_two_documents(self):
        # An empty first document and a second one: reading the second's lines
        # as the first's answers from a document nobody established is in force.
        read = read_scalars("---\n---\nkey: value\n")
        assert read.refusal == "second-document"
        assert read.values == {}

    def test_an_anchor_in_key_position_refuses_the_file(self):
        # Checking only the value let this through as a key spelled "&anc key".
        assert read_scalars("&anc key: value\n").refusal == "anchor-or-alias"

    def test_a_tag_in_key_position_refuses_the_file(self):
        assert read_scalars("!!str key: value\n").refusal == "tag"

    def test_a_comment_after_a_quoted_scalar_comes_off(self):
        # The quotes close before the '#', so the comment is a comment — the
        # value used to come back as '"/tmp/x" # note', quotes and all.
        assert read_scalars('path: "/tmp/x" # note\n').get("path") == "/tmp/x"
        assert read_scalars("path: '/tmp/x' # note\n").get("path") == "/tmp/x"

    def test_a_hash_without_whitespace_after_a_quoted_scalar_stays_verbatim(self):
        # YAML opens a comment only after whitespace, so this is not one, and
        # the reader states what it cannot read exactly rather than cutting.
        assert read_scalars('path: "/tmp/x"# note\n').get("path") == '"/tmp/x"# note'

    def test_text_after_a_quoted_scalar_stays_verbatim(self):
        assert read_scalars("path: '/tmp/x' and more\n").get("path") == "'/tmp/x' and more"

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
        # And the text it states is named as stated with none, which is the
        # fact ``values`` cannot carry: the empty string is there either way.
        assert read.null == ("empty",)

    def test_a_multi_line_scalar_is_skipped_by_name(self):
        read = read_scalars("note: |\n  first\n  second\nnext: 1\n")
        assert read.skipped == ("note",)
        assert read.get("next") == "1"


class TestTheKeysStatedWithNoValue:
    """The third statement, over the spelling that writes nothing at all.

    What the statement covers is every spelling of a null node, and that is
    :class:`TestTheNullNodeSpellings` below; here the key is written with
    nothing after the colon, which is the spelling the two emulators' own files
    hold. ``values`` answers what the text says and answers the same empty
    string for this spelling and for a quoted empty scalar, because that is
    what both texts say. A caller whose emulator reads the two differently —
    yaml-cpp makes the literal ``null`` of one and the empty string of the
    other — asks the third statement instead.
    """

    def test_a_key_stated_with_no_value_is_named_in_the_order_the_file_states_it(self):
        read = read_scalars("first:\nmiddle: 1\nlast:\n")
        assert read.null == ("first", "last")

    def test_a_quoted_empty_scalar_is_a_value_and_not_one_of_them(self):
        # The discriminating pair: same value in ``values``, different line in
        # the file, and the emulator that reads it may make two values of them.
        read = read_scalars('stated: ""\n')
        assert read.get("stated") == ""
        assert read.null == ()

    def test_a_key_the_file_never_states_is_in_neither(self):
        read = read_scalars("next: 1\n")
        assert read.get("absent") is None
        assert read.null == ()

    def test_a_block_under_the_key_makes_it_skipped_rather_than_valueless(self):
        # Vita3K's own shape for an unread key: nothing after the colon and a
        # nested block below it. The block is a value, so the key states one.
        read = read_scalars("user-id:\n  stored: 00\nnext: 1\n")
        assert read.skipped == ("user-id",)
        assert read.null == ()

    def test_a_key_stated_with_no_value_twice_is_named_once(self):
        assert read_scalars("user-id:\nuser-id:\n").null == ("user-id",)

    def test_a_refused_file_names_none_of_them(self):
        read = read_scalars("user-id:\n---\nuser-id: 00\n")
        assert read.refusal == REFUSAL_SECOND_DOCUMENT
        assert read.null == ()


class TestTheNullNodeSpellings:
    """The five plain scalars yaml-cpp reads as a null node rather than as text.

    ``IsNullString`` folds them (null.cpp:13-16 at external/yaml-cpp@2f86d137,
    the same lines at the fork RPCS3 builds, 51a5d623), and every one of them
    reaches the same conversions, so a reader that named only the valueless
    spelling handed its callers the other four as text. Three of those —
    ``~``, ``Null`` and ``NULL`` — were then ids and paths the emulators
    reading those files never look up; the word ``null`` reached the right id
    for the wrong reason, as the text it is rather than as the node it states.
    """

    @pytest.mark.parametrize("spelling", ["", "~", "null", "Null", "NULL"])
    def test_every_spelling_of_the_node_is_named_and_reads_as_the_empty_string(self, spelling):
        read = read_scalars(f"user-id: {spelling}\nnext: 1\n")
        assert read.null == ("user-id",)
        assert read.get("user-id") == ""

    @pytest.mark.parametrize(
        ("written", "text"),
        [
            ("nUll", "nUll"),
            ('"null"', "null"),
            ("'null'", "null"),
            ('"~"', "~"),
            ("'~'", "~"),
        ],
    )
    def test_a_scalar_that_only_looks_like_the_node_is_a_value(self, written, text):
        # The case is exact, and quoting makes a scalar of anything: each of
        # these is the text it spells and none of them is the node.
        read = read_scalars(f"user-id: {written}\n")
        assert read.get("user-id") == text
        assert read.null == ()

    @pytest.mark.parametrize(
        "line",
        [
            "user-id: null # note",
            "user-id: ~   ",
            "user-id: ~\t# it",
            "user-id: # note",
            "user-id: #note",
            "user-id:   # note",
        ],
    )
    def test_a_comment_never_leaves_a_value_where_the_node_is(self, line):
        # Two facts in one list. A comment after a spelling comes off and the
        # spelling stands; a value that is nothing but a comment states nothing
        # after the colon, which is the empty spelling — and the second of
        # those needs no whitespace before the ``#``, because after ``key: ``
        # no plain scalar can open with one. Both read as a null node at both
        # commits, and the ids ``# note`` and ``#note`` were what the reader
        # answered before.
        read = read_scalars(f"{line}\n")
        assert read.null == ("user-id",)
        assert read.get("user-id") == ""

    def test_the_tuple_holds_each_spelling_once_and_nothing_else(self):
        # What the tuple is, held against the list it was read off: an empty
        # scalar, then ~, null, Null and NULL, each named once. A hand-written
        # copy of the source, so it catches an edit of the tuple and nothing
        # about the parser — what the parser folds is the parametrization
        # above, which reads every spelling through the reader.
        spellings = vars(yaml_scalars)["_NULL_SPELLINGS"]
        assert spellings == ("", "~", "null", "Null", "NULL")
        assert len(set(spellings)) == len(spellings)

    @pytest.mark.parametrize("spelling", ["", "~", "null"])
    def test_an_indented_line_under_the_node_leaves_the_key_unread(self, spelling):
        # The fold does not settle the key on its own line, and what an
        # indented line makes of it depends on the spelling: under the empty
        # one the key heads a nested block, under a word the document does not
        # load at all (``~`` then ``  stored: 00`` is an illegal map value at
        # both commits) — and where the line carries no colon the two are one
        # multi-line plain scalar (``~ more``). This reader reads none of the
        # three, so the key is named unread, which is the one answer true of
        # all of them.
        read = read_scalars(f"user-id: {spelling}\n  stored: 00\nnext: 1\n")
        assert read.skipped == ("user-id",)
        assert read.null == ()
        assert read.get("next") == "1"


class TestAKeyStatedMoreThanOnce:
    """The fourth statement: the first statement is read, and the key is named.

    yaml-cpp keeps every pair of a repeated key, and a lookup answers with the
    first one it finds: ``node_data::get``'s ``std::find_if`` over the pairs
    (detail/impl.h:118-138 at external/yaml-cpp@2f86d137), reached by the
    lookup Vita3K makes on a const node (``update_members``, config.cpp:41-44
    at cb1f592c). RPCS3 reads its own file by iterating every pair instead and
    keeps the last statement it can decode, so the key is named as well as
    read: a caller whose program reads the file that way must be able to refuse
    rather than answer a statement its emulator discards.
    """

    def test_a_key_stated_twice_reads_as_its_first_statement(self):
        read = read_scalars("user-id: 00\nuser-id: 01\n")
        assert read.get("user-id") == "00"
        assert read.repeated == ("user-id",)

    def test_a_node_then_a_value_keeps_the_node(self):
        read = read_scalars("user-id:\nuser-id: 00\n")
        assert read.get("user-id") == ""
        assert read.null == ("user-id",)
        assert read.repeated == ("user-id",)

    def test_a_value_then_a_node_keeps_the_value(self):
        read = read_scalars("user-id: 00\nuser-id: ~\n")
        assert read.get("user-id") == "00"
        assert read.null == ()
        assert read.repeated == ("user-id",)

    def test_a_block_then_a_value_stays_unread(self):
        read = read_scalars("user-id:\n  stored: 00\nuser-id: 01\n")
        assert read.skipped == ("user-id",)
        assert read.repeated == ("user-id",)
        with pytest.raises(KeyError, match="unread, not absent"):
            read.get("user-id")

    def test_a_value_then_a_block_keeps_the_value_and_swallows_the_block(self):
        # The block is the second statement's, so it may not take the first
        # one's value away — and its lines are attributable all the same, so
        # the file is not refused as one this reader cannot read lines from.
        read = read_scalars("user-id: 00\nuser-id:\n  stored: 01\nnext: 1\n")
        assert read.refusal is None
        assert read.get("user-id") == "00"
        assert read.skipped == ()
        assert read.repeated == ("user-id",)
        assert read.get("next") == "1"

    def test_a_key_stated_three_times_is_named_once(self):
        read = read_scalars("user-id: 00\nuser-id: 01\nuser-id: 02\n")
        assert read.get("user-id") == "00"
        assert read.repeated == ("user-id",)

    def test_a_later_statement_carrying_an_anchor_still_refuses_the_file(self):
        # An anchor changes meaning beyond the line it sits on wherever it
        # sits, so the first statement is not safe from a later one either.
        read = read_scalars("user-id: 00\nuser-id: &anc 01\n")
        assert read.refusal == REFUSAL_ANCHOR
        assert read.repeated == ()

    def test_substitution_resolves_against_the_first_statement(self):
        # The reader's own rule, and the only one consistent with the record it
        # keeps: ``values`` holds first statements. The program that writes
        # such tokens does it differently — RPCS3 substitutes the emulator_dir
        # its iteration last decoded — which is why its route refuses wherever
        # that key is stated more than once rather than answering this.
        read = read_scalars("$(E): /first/\n$(E): /second/\n/dev_hdd0/: $(E)dev_hdd0/\n")
        assert read.get("/dev_hdd0/") == "/first/dev_hdd0/"
        assert read.repeated == ("$(E)",)

    def test_a_key_stated_once_is_named_in_none_of_it(self):
        read = read_scalars("user-id: 00\nnext: 1\n")
        assert read.repeated == ()


class TestTheRefusalVocabularyIsEnumerated:
    """A refusal a caller may state to its own client is one this tuple names.

    ``atlas.placement`` builds a caveat vocabulary on top of these
    (``EMULATOR_CONFIG_UNREADABLE_REASONS``) and the guide lists them, so a
    seventh refusal added to the module and not to the tuple would reach a
    client as a value nothing documents.
    """

    def test_every_refusal_constant_is_in_the_tuple(self):
        declared = {
            value
            for name, value in vars(yaml_scalars).items()
            if name.startswith("REFUSAL_") and isinstance(value, str)
        }
        assert declared == set(REFUSAL_CODES)

    def test_the_tuple_names_each_refusal_once(self):
        assert len(set(REFUSAL_CODES)) == len(REFUSAL_CODES)
