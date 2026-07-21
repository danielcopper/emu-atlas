"""Tests for atlas.detect — installation discovery over the reader seam."""

from __future__ import annotations

import json
import os

from atlas.detect import detect
from atlas.installations import (
    NATIVE_CFG_SUFFIX,
    RETRODECK_JSON_SUFFIX,
    STANDALONE_FLATPAK_CFG_SUFFIX,
)
from atlas.reader import FixtureReader

HOME = "/home/deck"
RD_JSON = os.path.join(HOME, RETRODECK_JSON_SUFFIX)
SA_CFG = os.path.join(HOME, STANDALONE_FLATPAK_CFG_SUFFIX)
NA_CFG = os.path.join(HOME, NATIVE_CFG_SUFFIX)

RD_CONFIG = json.dumps({"paths": {"rd_home_path": "/home/deck/retrodeck"}})


def _detect(files):
    return detect(HOME, FixtureReader(files))


class TestSingleFlavor:
    def test_retrodeck_by_json_marker(self):
        installs = _detect({RD_JSON: RD_CONFIG})
        assert [i.kind for i in installs] == ["retrodeck"]

    def test_standalone_flatpak_by_cfg_marker(self):
        installs = _detect({SA_CFG: "\n"})
        assert [i.kind for i in installs] == ["standalone_retroarch_flatpak"]

    def test_native_by_cfg_marker(self):
        installs = _detect({NA_CFG: "\n"})
        assert [i.kind for i in installs] == ["native_retroarch"]


class TestCoexistenceAndOrder:
    def test_retrodeck_and_native(self):
        installs = _detect({RD_JSON: RD_CONFIG, NA_CFG: "\n"})
        assert [i.kind for i in installs] == ["retrodeck", "native_retroarch"]

    def test_all_three_probe_order(self):
        installs = _detect({RD_JSON: RD_CONFIG, SA_CFG: "\n", NA_CFG: "\n"})
        assert [i.kind for i in installs] == [
            "retrodeck",
            "standalone_retroarch_flatpak",
            "native_retroarch",
        ]


class TestEdgeCases:
    def test_nothing_installed(self):
        assert _detect({"/home/deck/unrelated.txt": "x"}) == []

    def test_empty_dict_detects_nothing(self):
        assert _detect({}) == []

    def test_empty_retrodeck_json_still_detected(self):
        # A present-but-empty marker file is still a marker.
        installs = _detect({RD_JSON: ""})
        assert [i.kind for i in installs] == ["retrodeck"]

    def test_malformed_retrodeck_json_still_detected(self):
        installs = _detect({RD_JSON: "{not json"})
        assert [i.kind for i in installs] == ["retrodeck"]
        # Falls back to the ~/retrodeck root.
        assert installs[0].root() == os.path.join(HOME, "retrodeck")


class TestFilesystemDefaultReader:
    def test_detect_uses_real_filesystem_when_reader_omitted(self, tmp_path):
        cfg = tmp_path / ".config" / "retroarch" / "retroarch.cfg"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('sort_savefiles_enable = "true"\n', encoding="utf-8")

        installs = detect(str(tmp_path))
        assert [i.kind for i in installs] == ["native_retroarch"]
        assert installs[0].root() == str(tmp_path / ".config" / "retroarch")
