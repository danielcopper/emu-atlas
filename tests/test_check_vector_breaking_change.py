"""Tests for scripts/check_vector_breaking_change.py — the gate on the corpus's promises.

The vectors ARE the contract, so this script decides whether a PR needs the
major-bump marker release-please reads. It had no tests, and it is the kind of
gate that fails safe in the wrong direction: a verdict that never fires looks
exactly like a PR that changed nothing.

The contract diff is exercised as a pure function over two contract maps — the
git plumbing that builds them is what `_contract_at` does, and mocking git would
test the mock. What matters here is the verdict.
"""

from __future__ import annotations

import pytest

from scripts import check_vector_breaking_change as gate

FAMILY = "machines"
INPUT_A = '{"files": {}, "home": "/home/deck"}'
INPUT_B = '{"files": {"/x": ""}, "home": "/home/deck"}'
EXPECTED_A = '{"installations": []}'
EXPECTED_B = '{"installations": [{"kind": "retrodeck"}]}'


def _contract(*entries) -> dict[str, dict[str, tuple[str, str]]]:
    """One family's contract: (input, expected, vector name) triples."""
    return {FAMILY: {inp: (expected, name) for inp, expected, name in entries}}


class TestWhatCountsAsBreaking:
    def test_an_unchanged_corpus_is_clean(self):
        base = _contract((INPUT_A, EXPECTED_A, "first"))
        assert gate.diff_contracts(base, base) == []

    def test_a_new_vector_only_grows_the_contract(self):
        base = _contract((INPUT_A, EXPECTED_A, "first"))
        head = _contract((INPUT_A, EXPECTED_A, "first"), (INPUT_B, EXPECTED_B, "second"))
        assert gate.diff_contracts(base, head) == []

    def test_a_changed_expectation_is_breaking(self):
        base = _contract((INPUT_A, EXPECTED_A, "first"))
        head = _contract((INPUT_A, EXPECTED_B, "first"))
        assert [f.split(":")[0] for f in gate.diff_contracts(base, head)] == [FAMILY]

    def test_a_changed_expectation_names_the_vector(self):
        base = _contract((INPUT_A, EXPECTED_A, "first"))
        head = _contract((INPUT_A, EXPECTED_B, "first"))
        assert "expected changed for 'first'" in gate.diff_contracts(base, head)[0]

    def test_a_removed_vector_is_breaking(self):
        base = _contract((INPUT_A, EXPECTED_A, "first"))
        assert "a guaranteed input was removed" in gate.diff_contracts(base, _contract())[0]

    def test_an_edited_fixture_is_reported_as_the_edit_it_is(self):
        # Same vector, different machine: the old guarantee is gone, which IS
        # breaking — but "an input was removed" sent readers looking for a
        # deletion that is not in the diff.
        base = _contract((INPUT_A, EXPECTED_A, "first"))
        head = _contract((INPUT_B, EXPECTED_A, "first"))
        assert "the fixture of 'first' was edited" in gate.diff_contracts(base, head)[0]

    def test_a_rename_alone_is_not_breaking(self):
        # The contract is input → expected. What a vector is called is
        # presentation, and renaming one promises exactly what it promised.
        base = _contract((INPUT_A, EXPECTED_A, "first"))
        head = _contract((INPUT_A, EXPECTED_A, "renamed"))
        assert gate.diff_contracts(base, head) == []

    def test_a_dropped_family_is_breaking(self):
        base = {FAMILY: {INPUT_A: (EXPECTED_A, "first")}}
        assert gate.diff_contracts(base, {}) != []

    def test_every_broken_guarantee_is_reported_not_just_the_first(self):
        base = _contract((INPUT_A, EXPECTED_A, "first"), (INPUT_B, EXPECTED_B, "second"))
        assert len(gate.diff_contracts(base, _contract())) == 2


class TestTheMarkerTheGateAcceptsFromAPr:
    """release-please reads the squash commit, so the marker has to be real.

    Both spellings are the Conventional Commits ones: a ``!`` before the colon
    in the title, or a ``BREAKING CHANGE:`` footer in the body.
    """

    @pytest.mark.parametrize(
        "title", ["feat!: x", "fix!: x", "refactor(api)!: x", "feat(scope)!: something"]
    )
    def test_a_bang_title_carries_the_marker(self, title):
        assert gate.TITLE_BANG.match(title)

    @pytest.mark.parametrize("title", ["feat: x", "fix(api): x", "chore: not! breaking", "Feat!: x"])
    def test_a_plain_title_does_not(self, title):
        assert not gate.TITLE_BANG.match(title)

    @pytest.mark.parametrize(
        "body",
        [
            "BREAKING CHANGE: the answer gained a caveat",
            "some prose\n\nBREAKING CHANGE: the answer gained a caveat",
            "BREAKING-CHANGE: the spec's own synonym",
        ],
    )
    def test_a_footer_carries_the_marker(self, body):
        assert gate.BREAKING_FOOTER.search(body)

    @pytest.mark.parametrize(
        "body",
        [
            "this is not a BREAKING CHANGE, only a new vector",
            "see BREAKING CHANGE in the previous release notes",
            "BREAKING CHANGE without a colon",
            "",
        ],
    )
    def test_a_mention_in_prose_does_not(self, body):
        # The sentence most likely to appear in a body that means the opposite
        # used to pass the gate, because the check was a substring search.
        assert not gate.BREAKING_FOOTER.search(body)


class TestTheBaseRefIsNotAnOption:
    """argv reaches git, so a ref that could pose as a flag is refused up front."""

    @pytest.mark.parametrize(
        "ref", ["origin/main", "v1.2.3", "HEAD~3", "HEAD^", "main@{upstream}", "a1b2c3d"]
    )
    def test_a_usable_ref_is_accepted(self, ref):
        assert gate.REF_NAME.fullmatch(ref)

    @pytest.mark.parametrize("ref", ["--upload-pack=evil", "-n", "", "--all"])
    def test_a_ref_that_could_pose_as_an_option_is_refused(self, ref):
        assert not gate.REF_NAME.fullmatch(ref)
