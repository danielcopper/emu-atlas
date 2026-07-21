"""Structural invariants of the packaged bios_registry.json.

These pin the *shape* the generator (``scripts/generate_bios_registry.py``)
guarantees for the shipped data file, over the raw JSON rather than the
``BiosRegistry`` view (which fills defaults and would mask a missing key). They
are the sanity net for a regeneration: a data diff that drops a field, changes an
entry's type, or shifts the documented counts fails here before it reaches a
consumer. ``atlas/data/README.md`` documents the counts and the update discipline.

``test_bios.py`` already covers the load path and the version/platform-count of the
bundled copy; this file covers the per-entry structure and the full 548/54/122
counts it does not.
"""

from __future__ import annotations

import importlib.resources
import json
from typing import Any

import pytest

# Documented in atlas/data/README.md and README.md. A regeneration that shifts
# these is a data change reviewers must see called out.
EXPECTED_PLATFORMS = 54
EXPECTED_ENTRIES = 548
EXPECTED_CORES = 122

# The complete set of keys an entry may carry. description/required/firmware_path
# are always present; the md5/sha1/size hash triple is present only for files the
# generator found in libretro-database's System.dat; cores is present when at
# least one .info file referenced the file.
ENTRY_KEYS = {"description", "required", "firmware_path", "cores", "md5", "sha1", "size"}
ALWAYS_PRESENT = {"description", "required", "firmware_path"}
HASH_TRIPLE = {"md5", "sha1", "size"}


@pytest.fixture(scope="module")
def registry() -> dict[str, Any]:
    text = importlib.resources.files("atlas").joinpath("data", "bios_registry.json").read_text(encoding="utf-8")
    return json.loads(text)


def _entries(registry: dict[str, Any]):
    """Yield (slug, filename, entry) for every entry across every platform."""
    for slug, files in registry["platforms"].items():
        for filename, entry in files.items():
            yield slug, filename, entry


class TestTopLevel:
    def test_only_meta_and_platforms(self, registry):
        assert set(registry) == {"_meta", "platforms"}

    def test_meta_has_generated_from_and_version(self, registry):
        meta = registry["_meta"]
        assert isinstance(meta.get("generated_from"), str) and meta["generated_from"]
        assert isinstance(meta.get("version"), str) and meta["version"]

    def test_platforms_is_nonempty_object(self, registry):
        assert isinstance(registry["platforms"], dict)
        assert registry["platforms"]


class TestCounts:
    def test_platform_count(self, registry):
        assert len(registry["platforms"]) == EXPECTED_PLATFORMS

    def test_entry_count(self, registry):
        total = sum(len(files) for files in registry["platforms"].values())
        assert total == EXPECTED_ENTRIES

    def test_distinct_core_count(self, registry):
        cores = {
            core for _, _, entry in _entries(registry) for core in (entry.get("cores") or {})
        }
        assert len(cores) == EXPECTED_CORES


class TestEntryShape:
    def test_every_entry_has_required_fields(self, registry):
        for slug, filename, entry in _entries(registry):
            missing = ALWAYS_PRESENT - set(entry)
            assert not missing, f"{slug}/{filename} missing {sorted(missing)}"
            assert isinstance(entry["description"], str)
            assert isinstance(entry["required"], bool)
            assert isinstance(entry["firmware_path"], str) and entry["firmware_path"]

    def test_no_unexpected_entry_keys(self, registry):
        for slug, filename, entry in _entries(registry):
            extra = set(entry) - ENTRY_KEYS
            assert not extra, f"{slug}/{filename} has unexpected keys {sorted(extra)}"

    def test_hash_triple_is_all_or_nothing(self, registry):
        # md5/sha1/size are written together (only when the file is in
        # System.dat), so a partial triple would be a generator regression.
        for slug, filename, entry in _entries(registry):
            present = HASH_TRIPLE & set(entry)
            assert present in (set(), HASH_TRIPLE), f"{slug}/{filename} has partial hashes {sorted(present)}"

    def test_present_hashes_have_correct_types(self, registry):
        for slug, filename, entry in _entries(registry):
            if "md5" in entry:
                where = f"{slug}/{filename}"
                assert isinstance(entry["md5"], str) and entry["md5"], where
                assert isinstance(entry["sha1"], str) and entry["sha1"], where
                assert isinstance(entry["size"], int) and entry["size"] > 0, where


class TestPerCoreShape:
    def test_every_core_entry_has_required_bool(self, registry):
        for slug, filename, entry in _entries(registry):
            for core, info in (entry.get("cores") or {}).items():
                assert isinstance(info, dict), f"{slug}/{filename}/{core} core info not an object"
                assert "required" in info, f"{slug}/{filename}/{core} missing 'required'"
                assert isinstance(info["required"], bool)
