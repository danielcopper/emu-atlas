"""Structural invariants of the packaged firmware_hashes.json.

These pin the *shape* the generator (``scripts/generate_firmware_hashes.py``)
guarantees for the shipped data file, over the raw JSON rather than the
``FirmwareHashes`` view (which would mask a missing key by raising or
defaulting). They are the sanity net for a regeneration: a data diff that drops
a field, changes an entry's type, or shifts the documented count fails here
before it reaches a consumer. ``atlas/data/README.md`` documents the count and
the update discipline.

``test_firmware.py`` covers the load path and the version/count of the bundled
copy; this file covers the per-entry structure it does not.
"""

from __future__ import annotations

import importlib.resources
import json
import re
from typing import Any
from unittest import mock

import pytest

from scripts import generate_firmware_hashes as generator

from scripts.generate_firmware_hashes import (
    ARCHIVE_IDENTITIES,
    ARCHIVE_IDENTITIES_FINGERPRINT,
    ARCHIVE_IDENTITIES_REVIEWED,
    ARCHIVE_IDENTITIES_VERSION,
    fingerprint,
)

# Documented in atlas/data/README.md and README.md. A regeneration that shifts
# this is a data change reviewers must see called out.
EXPECTED_ENTRIES = 388

IDENTITY_KEYS = {"md5", "sha1", "size", "kind"}
ARCHIVE_KEYS = IDENTITY_KEYS | {"archive_reason"}
KINDS = {"file", "archive"}
ARCHIVE_REASONS = {"romset", "core-bundled"}
MD5_HEX = re.compile(r"^[0-9a-f]{32}$")
SHA1_HEX = re.compile(r"^[0-9a-f]{40}$")


@pytest.fixture(scope="module")
def table() -> dict[str, Any]:
    text = importlib.resources.files("atlas").joinpath("data", "firmware_hashes.json").read_text(encoding="utf-8")
    return json.loads(text)


class TestTopLevel:
    def test_only_meta_and_files(self, table):
        assert set(table) == {"_meta", "files"}

    def test_meta_is_exactly_the_five_fields_the_generator_writes(self, table):
        assert set(table["_meta"]) == {
            "generated_from",
            "version",
            "generated_at",
            "archive_identities_version",
            "archive_identities_reviewed",
        }

    def test_meta_has_generated_from_and_version(self, table):
        meta = table["_meta"]
        assert isinstance(meta.get("generated_from"), str)
        assert meta["generated_from"]
        assert isinstance(meta.get("version"), str)
        assert meta["version"]

    def test_files_is_a_nonempty_object(self, table):
        assert isinstance(table["files"], dict)
        assert table["files"]

    def test_entry_count(self, table):
        assert len(table["files"]) == EXPECTED_ENTRIES


class TestEntryShape:
    def test_keys_are_non_empty_strings(self, table):
        for name in table["files"]:
            assert isinstance(name, str), name
            assert name, name
            assert not name.startswith("/"), name
            assert not name.endswith("/"), name

    def test_base_names_are_unique_across_both_key_forms(self, table):
        # Upstream keys some entries by a relative path (dc/dc_boot.bin) and
        # the rest by a bare file name. FirmwareHashes.for_path falls back from
        # the path to the base name, which is only unambiguous while no base
        # name is claimed twice.
        base_names = [name.rsplit("/", 1)[-1] for name in table["files"]]
        duplicates = {n for n in base_names if base_names.count(n) > 1}
        assert not duplicates, f"base name claimed by more than one entry: {sorted(duplicates)}"

    def test_every_entry_is_exactly_the_fields_its_kind_calls_for(self, table):
        # An archive says why its bytes move and a dump has nothing to say, so
        # the two shapes differ by exactly that one key.
        for name, entry in table["files"].items():
            assert isinstance(entry, dict), name
            expected = ARCHIVE_KEYS if entry.get("kind") == "archive" else IDENTITY_KEYS
            assert set(entry) == expected, name

    def test_identities_are_well_formed(self, table):
        for name, entry in table["files"].items():
            assert MD5_HEX.match(entry["md5"]), f"{name}: md5 {entry['md5']!r}"
            assert SHA1_HEX.match(entry["sha1"]), f"{name}: sha1 {entry['sha1']!r}"
            assert isinstance(entry["size"], int), name
            assert entry["size"] > 0, name

    def test_every_entry_states_a_known_kind(self, table):
        for name, entry in table["files"].items():
            assert entry["kind"] in KINDS, f"{name}: kind {entry.get('kind')!r}"

    def test_every_archive_states_a_known_reason(self, table):
        for name, entry in table["files"].items():
            if entry["kind"] == "archive":
                assert entry["archive_reason"] in ARCHIVE_REASONS, name


class TestKindProvenance:
    """The kinds in the table are the curated list, and nothing else decides them.

    The list lives in ``scripts/generate_firmware_hashes.py`` with a reviewed
    date, because ``System.dat`` states nothing about it: what a firmware file
    *is* — a dump, a MAME romset, a core's data pack — is atlas's own reading.
    These two directions keep the shipped data and that reading from drifting
    apart in either direction.
    """

    def test_the_shipped_archives_are_exactly_the_curated_ones(self, table):
        archives = {name for name, entry in table["files"].items() if entry["kind"] == "archive"}
        assert archives == set(ARCHIVE_IDENTITIES)

    def test_every_archive_carries_the_reason_the_list_gives_it(self, table):
        stamped = {
            name: entry["archive_reason"]
            for name, entry in table["files"].items()
            if entry["kind"] == "archive"
        }
        assert stamped == {name: reason for name, (reason, _what) in ARCHIVE_IDENTITIES.items()}

    def test_every_curated_line_says_what_the_file_is(self):
        # The reason is the machine-readable half; the sentence beside it is
        # the provenance a reviewer reads. A line without one states a verdict
        # nobody can check.
        assert [name for name, (_reason, what) in ARCHIVE_IDENTITIES.items() if not what.strip()] == []

    def test_no_name_that_looks_like_an_archive_is_stamped_as_a_dump(self, table):
        """The guard: the only place an extension decides anything about an IDENTITY.

        Nothing in atlas reads a suffix to settle what kind of thing a firmware
        identity is — that is the whole point of stamping the kind in the table,
        because ``.zip`` is a container format and not a statement about a
        file's role. (Suffixes are read elsewhere in the repo for other
        questions: which content files a system launches, whether a core file is
        a ``.so``.) But a regeneration that pulls a new archive name out of a
        newer ``System.dat`` would stamp it ``file`` and answer ``mismatch``
        over it in silence, and nothing else would notice. So this test does
        what the resolver must not: it reads the suffix, and fails until the
        curated list carries a reviewed line for the name.
        """
        container_suffixes = (".zip", ".pk3", ".wad", ".jar")
        by_suffix = {
            name for name in table["files"] if name.lower().endswith(container_suffixes)
        }
        archives = {name for name, entry in table["files"].items() if entry["kind"] == "archive"}
        assert by_suffix == archives

    def test_the_shipped_table_states_the_list_version_that_stamped_it(self, table):
        # The version is the generator's and the data file is where a consumer
        # reads it, so a restamp that forgot to carry it fails here.
        assert table["_meta"]["archive_identities_version"] == ARCHIVE_IDENTITIES_VERSION

    def test_the_version_still_describes_the_list_it_names(self):
        """A change to the list fails here until the pin is deliberately re-made.

        The version is a promise, and a promise nothing checks decays: today's
        list could swap a name or flip a reason and keep calling itself "2",
        under which some consumer already pinned different data. So the
        fingerprint pins what the version stands for.

        What it sees: every name in the list and the ``archive_reason`` each one
        carries, sorted. What it deliberately does not see: the rationale
        strings. Those are prose for a reviewer, they reach no consumer, and
        folding them in would turn a wording fix into a data bump — the kind of
        false alarm that teaches people to re-pin without looking.

        So this fails in exactly two situations, and both are right: a name or a
        reason moved and the pin did not, or the pin was re-made without
        recomputing. What it sees is the digest, never the version: re-pinning
        is the moment to bump ``ARCHIVE_IDENTITIES_VERSION`` beside it, which
        is why the pin sits right under the version. Fix it by bumping the
        version and pasting the new digest.
        """
        assert fingerprint() == ARCHIVE_IDENTITIES_FINGERPRINT

    def test_the_fingerprint_ignores_the_rationale_prose(self):
        # The other half of the claim above: rewording a line must NOT demand a
        # bump. Proven against the real list rather than asserted in prose.
        reworded = {
            name: (reason, f"reworded — {what}") for name, (reason, what) in ARCHIVE_IDENTITIES.items()
        }
        with mock.patch.object(generator, "ARCHIVE_IDENTITIES", reworded):
            assert fingerprint() == ARCHIVE_IDENTITIES_FINGERPRINT

    def test_the_fingerprint_sees_a_changed_reason(self):
        # And the half that must fire: neogeo.zip turning core-bundled is a
        # consumer-visible change to what the table says.
        flipped = dict(ARCHIVE_IDENTITIES)
        flipped["neogeo.zip"] = ("core-bundled", flipped["neogeo.zip"][1])
        with mock.patch.object(generator, "ARCHIVE_IDENTITIES", flipped):
            assert fingerprint() != ARCHIVE_IDENTITIES_FINGERPRINT

    def test_the_fingerprint_sees_a_new_name(self):
        grown = dict(ARCHIVE_IDENTITIES)
        grown["namcoc69.zip"] = ("romset", "Namco C69 BIOS set")
        with mock.patch.object(generator, "ARCHIVE_IDENTITIES", grown):
            assert fingerprint() != ARCHIVE_IDENTITIES_FINGERPRINT

    def test_the_shipped_table_states_the_date_the_list_was_reviewed(self, table):
        assert table["_meta"]["archive_identities_reviewed"] == ARCHIVE_IDENTITIES_REVIEWED
