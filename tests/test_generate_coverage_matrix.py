"""Tests for the generated core-audit coverage matrix."""

from __future__ import annotations

import json
import sys

import pytest

from scripts import generate_coverage_matrix as matrix


def test_matrix_renders_per_game_capability_and_notes(tmp_path, monkeypatch):
    es_systems = tmp_path / "es_systems.xml"
    es_systems.write_text(
        """<systemList>
  <system>
    <name>test</name>
    <command>/cores/yes_libretro.so %ROM%</command>
    <command>/cores/no_libretro.so %ROM%</command>
    <command>/cores/unknown_libretro.so %ROM%</command>
    <command>/cores/unaudited_libretro.so %ROM%</command>
  </system>
</systemList>
""",
        encoding="utf-8",
    )
    audit_path = tmp_path / "core_audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "schema": 2,
                "cores": {
                    "yes": {
                        "verdict": "standard",
                        "per_game_capable": True,
                        "note": "source <file:line> | observed",
                        "verified": {"retrodeck": None, "emudeck": None, "bare": None},
                        "kind": "libretro",
                    },
                    "no": {
                        "verdict": "standard",
                        "per_game_capable": False,
                        "note": "absence proven",
                        "verified": {"retrodeck": None, "emudeck": None, "bare": None},
                        "kind": "libretro",
                    },
                    "unknown": {
                        "verdict": "suspect",
                        "per_game_capable": None,
                        "note": "not established",
                        "verified": {"retrodeck": None, "emudeck": None, "bare": None},
                        "kind": "libretro",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "coverage-matrix.md"
    monkeypatch.setattr(matrix, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(matrix, "AUDIT_PATH", audit_path)
    monkeypatch.setattr(matrix, "OUTPUT_PATH", output_path)
    monkeypatch.setattr(sys, "argv", ["generate_coverage_matrix.py", str(es_systems)])

    matrix.main()

    output = output_path.read_text(encoding="utf-8")
    assert "| emulator | systems | verdict | per-game capable |" in output
    assert "| `yes` | test | standard | yes | ✖ | ? | ? | source &lt;file:line&gt; \\| observed |" in output
    assert "| `no` | test | standard | no | ✖ | ? | ? | absence proven |" in output
    assert "| `unknown` | test | suspect | ? | ✖ | ? | ? | not established |" in output
    assert "| `unaudited` | test | unaudited | ? | ✖ | ? | ? | — |" in output


def test_matrix_rejects_unsupported_audit_schema(tmp_path):
    audit_path = tmp_path / "core_audit.json"
    audit_path.write_text('{"schema": 99, "cores": {}}', encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported schema 99"):
        matrix.load_audit_data(audit_path)
