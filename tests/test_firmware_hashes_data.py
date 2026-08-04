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

import pytest

# Documented in atlas/data/README.md and README.md. A regeneration that shifts
# this is a data change reviewers must see called out.
EXPECTED_ENTRIES = 388

ENTRY_KEYS = {"md5", "sha1", "size"}
MD5_HEX = re.compile(r"^[0-9a-f]{32}$")
SHA1_HEX = re.compile(r"^[0-9a-f]{40}$")


@pytest.fixture(scope="module")
def table() -> dict[str, Any]:
    text = importlib.resources.files("atlas").joinpath("data", "firmware_hashes.json").read_text(encoding="utf-8")
    return json.loads(text)


class TestTopLevel:
    def test_only_meta_and_files(self, table):
        assert set(table) == {"_meta", "files"}

    def test_meta_has_generated_from_and_version(self, table):
        meta = table["_meta"]
        assert isinstance(meta.get("generated_from"), str) and meta["generated_from"]
        assert isinstance(meta.get("version"), str) and meta["version"]

    def test_files_is_a_nonempty_object(self, table):
        assert isinstance(table["files"], dict)
        assert table["files"]

    def test_entry_count(self, table):
        assert len(table["files"]) == EXPECTED_ENTRIES


class TestEntryShape:
    def test_keys_are_non_empty_strings(self, table):
        for name in table["files"]:
            assert isinstance(name, str) and name, name
            assert not name.startswith("/") and not name.endswith("/"), name

    def test_base_names_are_unique_across_both_key_forms(self, table):
        # Upstream keys some entries by a relative path (dc/dc_boot.bin) and
        # the rest by a bare file name. FirmwareHashes.for_path falls back from
        # the path to the base name, which is only unambiguous while no base
        # name is claimed twice.
        base_names = [name.rsplit("/", 1)[-1] for name in table["files"]]
        duplicates = {n for n in base_names if base_names.count(n) > 1}
        assert not duplicates, f"base name claimed by more than one entry: {sorted(duplicates)}"

    def test_every_entry_is_exactly_the_identity_triple(self, table):
        for name, entry in table["files"].items():
            assert isinstance(entry, dict) and set(entry) == ENTRY_KEYS, name

    def test_identities_are_well_formed(self, table):
        for name, entry in table["files"].items():
            assert MD5_HEX.match(entry["md5"]), f"{name}: md5 {entry['md5']!r}"
            assert SHA1_HEX.match(entry["sha1"]), f"{name}: sha1 {entry['sha1']!r}"
            assert isinstance(entry["size"], int) and entry["size"] > 0, name
