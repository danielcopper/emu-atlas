"""Tests for atlas.standalone_firmware — the card loader fails closed."""

import json

import pytest

from atlas.standalone_firmware import (
    load_standalone_firmware,
    lookup_standalone_firmware_card,
)


def _doc(**overrides):
    entry = {
        "systems": ["wiiu"],
        "files": [
            {
                "name": "keys.txt",
                "base": "data",
                "subdir": "Cemu",
                "need": "optional",
                "purpose": "keys",
                "citation": "KeyCache.cpp:63 at 2.6",
            }
        ],
        "provenance": {"source": "Cemu 2.6"},
    }
    entry.update(overrides)
    return json.dumps({"schema": 1, "emulators": {"CEMU": entry}})


class TestTheLoaderFailsClosed:
    def test_the_packaged_file_loads_and_looks_up(self):
        card = lookup_standalone_firmware_card("CEMU")
        assert card is not None
        assert "wiiu" in card.systems
        assert card.files[0].name == "keys.txt"
        assert lookup_standalone_firmware_card("NOPE") is None
        assert lookup_standalone_firmware_card(None) is None

    def test_a_wrong_schema_is_refused(self):
        with pytest.raises(ValueError, match="unsupported schema"):
            load_standalone_firmware('{"schema": 2, "emulators": {}}')

    def test_an_unknown_base_is_refused(self):
        bad = _doc(
            files=[
                {
                    "name": "keys.txt",
                    "base": "cache",
                    "subdir": "Cemu",
                    "need": "optional",
                    "purpose": "keys",
                    "citation": "x",
                }
            ]
        )
        with pytest.raises(ValueError, match="base must be one of"):
            load_standalone_firmware(bad)

    def test_an_unknown_need_is_refused(self):
        bad = _doc(
            files=[
                {
                    "name": "keys.txt",
                    "base": "data",
                    "subdir": "Cemu",
                    "need": "mandatory",
                    "purpose": "keys",
                    "citation": "x",
                }
            ]
        )
        with pytest.raises(ValueError, match="need must be one of"):
            load_standalone_firmware(bad)

    def test_a_stray_key_is_refused(self):
        bad = _doc(
            files=[
                {
                    "name": "keys.txt",
                    "base": "data",
                    "subdir": "Cemu",
                    "need": "optional",
                    "purpose": "keys",
                    "citation": "x",
                    "note": "stray",
                }
            ]
        )
        with pytest.raises(ValueError, match="expected exactly"):
            load_standalone_firmware(bad)

    def test_an_empty_file_list_is_refused(self):
        with pytest.raises(ValueError, match="files must be a non-empty list"):
            load_standalone_firmware(_doc(files=[]))

    def test_missing_provenance_is_refused(self):
        with pytest.raises(ValueError, match="provenance.source"):
            load_standalone_firmware(_doc(provenance={}))
