"""The re-recording script: which build it will record from, and the file shape it writes."""

from __future__ import annotations

import json

from atlas.machine import CoreInfo
from scripts import snapshot_core_registrations as snapshot


def _info(library_version: str | None) -> CoreInfo:
    return CoreInfo(library_name="Core", library_version=library_version, valid_extensions=None)


def _entry(retrodeck: object) -> dict[str, object]:
    return {"verdict": "card", "verified": {"retrodeck": retrodeck, "emudeck": None, "bare": None}}


class TestACardPinnedToAnotherBuildIsNotReRecorded:
    """A record pinned to one build must not take another build's registration."""

    def test_no_retrodeck_record_records_the_deployed_build(self):
        assert snapshot.pinned_elsewhere(_entry(None), version="0.10.9b", info=_info("abc")) is None

    def test_matching_pins_record_the_deployed_build(self):
        entry = _entry({"version": "0.10.9b", "core_library_version": "abc", "date": None})
        assert snapshot.pinned_elsewhere(entry, version="0.10.9b", info=_info("abc")) is None

    def test_an_unpinned_core_version_leaves_the_arrangement_version_to_decide(self):
        entry = _entry({"version": "0.10.9b", "core_library_version": None, "date": None})
        assert snapshot.pinned_elsewhere(entry, version="0.10.9b", info=_info("abc")) is None

    def test_another_retrodeck_version_is_skipped(self):
        entry = _entry({"version": "0.10.9b", "core_library_version": None, "date": None})
        reason = snapshot.pinned_elsewhere(entry, version="0.11.0", info=_info("abc"))
        assert reason is not None
        assert "0.10.9b" in reason
        assert "0.11.0" in reason

    def test_another_core_build_is_skipped(self):
        entry = _entry({"version": "0.10.9b", "core_library_version": "abc", "date": None})
        reason = snapshot.pinned_elsewhere(entry, version="0.10.9b", info=_info("def"))
        assert reason is not None
        assert "abc" in reason
        assert "def" in reason


class TestTheSnapshotFileShape:
    def test_each_option_is_one_line_and_the_document_round_trips(self):
        document = {
            "spec": "s",
            "cores": {
                "x": {
                    "build": "v1",
                    "registration": {"x_a": {"default": None, "values": ["on", "off"]}},
                },
                "y": {"build": None, "registration": "not-captured"},
            },
        }
        expected = json.loads(json.dumps(document))
        text = snapshot.serialize_snapshot(document)
        assert json.loads(text) == expected
        assert '        "x_a": {"default": null, "values": ["on", "off"]}\n' in text
