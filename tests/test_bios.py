"""Tests for atlas.bios — registry load, lookup, and required-classification."""

from __future__ import annotations

import json

import pytest

from atlas.bios import BiosEntry, load_registry

FIXTURE = json.dumps(
    {
        "_meta": {"version": "test"},
        "platforms": {
            "psx": {
                "scph5501.bin": {
                    "description": "PS1 BIOS (US)",
                    "required": True,
                    "firmware_path": "scph5501.bin",
                    "cores": {"swanstation": {"required": True}, "pcsx_rearmed": {"required": True}},
                    "md5": "abc",
                    "sha1": "def",
                    "size": 524288,
                },
                "optional.bin": {
                    "description": "optional file",
                    "required": False,
                    "cores": {"swanstation": {"required": False}},
                },
            },
            "gba": {
                # Top-level not required, but one core requires it — the override case.
                "gba_bios.bin": {
                    "description": "GBA BIOS",
                    "required": False,
                    "cores": {"mgba": {"required": False}, "vba_next": {"required": True}},
                },
            },
            "nocores": {
                "top.bin": {"description": "top-level only", "required": True},
            },
        },
    }
)


@pytest.fixture
def registry():
    return load_registry(FIXTURE)


class TestLoad:
    def test_meta_and_platforms(self, registry):
        assert registry.meta["version"] == "test"
        assert registry.platforms() == ("gba", "nocores", "psx")

    def test_entry_is_biosentry_with_identity(self, registry):
        entry = registry.entry("psx", "scph5501.bin")
        assert isinstance(entry, BiosEntry)
        assert entry.md5 == "abc"
        assert entry.sha1 == "def"
        assert entry.size == 524288
        assert entry.cores == {"swanstation": True, "pcsx_rearmed": True}

    def test_files_lists_platform_entries(self, registry):
        names = {e.file_name for e in registry.files("psx")}
        assert names == {"scph5501.bin", "optional.bin"}


class TestLookupBadPaths:
    def test_unknown_platform_entry_is_none(self, registry):
        assert registry.entry("nope", "x.bin") is None

    def test_unknown_file_is_none(self, registry):
        assert registry.entry("psx", "missing.bin") is None

    def test_files_unknown_platform_is_empty(self, registry):
        assert registry.files("nope") == ()


class TestIsRequired:
    def test_top_level_required_without_core(self, registry):
        assert registry.is_required("psx", "scph5501.bin") is True

    def test_top_level_not_required_without_core(self, registry):
        assert registry.is_required("gba", "gba_bios.bin") is False

    def test_per_core_override_beats_top_level_false(self, registry):
        # gba_bios.bin is not required top-level, but vba_next requires it.
        assert registry.is_required("gba", "gba_bios.bin", core="vba_next") is True

    def test_per_core_not_required(self, registry):
        assert registry.is_required("gba", "gba_bios.bin", core="mgba") is False

    def test_core_given_but_not_listed_is_not_required(self, registry):
        assert registry.is_required("psx", "scph5501.bin", core="unlisted_core") is False

    def test_entry_without_cores_falls_to_top_level(self, registry):
        assert registry.is_required("nocores", "top.bin", core="anything") is True

    def test_unknown_entry_is_not_required(self, registry):
        assert registry.is_required("psx", "missing.bin", core="swanstation") is False


class TestRequiredBios:
    def test_top_level_required_set(self, registry):
        names = {e.file_name for e in registry.required_bios("psx")}
        assert names == {"scph5501.bin"}

    def test_per_core_required_set(self, registry):
        names = {e.file_name for e in registry.required_bios("psx", core="swanstation")}
        assert names == {"scph5501.bin"}

    def test_override_makes_optional_file_required(self, registry):
        names = {e.file_name for e in registry.required_bios("gba", core="vba_next")}
        assert names == {"gba_bios.bin"}

    def test_no_required_when_core_makes_all_optional(self, registry):
        assert registry.required_bios("gba", core="mgba") == ()


class TestPackagedRegistry:
    def test_loads_bundled_data(self):
        registry = load_registry()
        assert registry.meta["version"] == "4.0.0"
        assert len(registry.platforms()) == 54
        # A stable, known entry from the vendored copy.
        assert "3do" in registry.platforms()
        assert registry.entry("3do", "panafz10.bin") is not None
