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
                "schema": 3,
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


def _standalone_table(text: str) -> dict[str, list[str]]:
    """The committed matrix's standalone rows, as ``{key: [cells]}``."""
    rows: dict[str, list[str]] = {}
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line.startswith("## standalone emulators")
            continue
        if not inside or not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows[cells[0].strip("`")] = cells[1:]
    return rows


def _systems_of(cell: str) -> tuple[set[str], int]:
    """A systems cell as ``(the names it lists, how many the row really serves)``.

    ``systems_summary`` shortens a long list to four names and a count, so the
    two are not the same number and a reader that takes the names for the whole
    set is off by fifty.
    """
    names: set[str] = set()
    total: int | None = None
    for part in cell.split(","):
        word = part.strip()
        if word.startswith("…"):
            total = int(word.removeprefix("…").strip().strip("()"))
            continue
        names.add(word)
    return names, total if total is not None else len(names)


def test_the_standalone_half_counts_every_question(tmp_path, monkeypatch):
    es_systems = tmp_path / "es_systems.xml"
    es_systems.write_text(
        """<systemList>
  <system>
    <name>gc</name>
    <command label="Demo">%EMULATOR_DEMO% %ROM%</command>
  </system>
  <system>
    <name>triforce</name>
    <command label="Demo">%EMULATOR_DEMO% %ROM%</command>
  </system>
  <system>
    <name>quake</name>
    <command label="Nobody">%EMULATOR_NOBODY% %ROM%</command>
  </system>
</systemList>
""",
        encoding="utf-8",
    )
    data = tmp_path / "data"
    data.mkdir()
    (data / "standalone_saves.json").write_text(
        json.dumps({"emulators": {"DEMO": {"saves": {"systems": ["gc"]}}}}), encoding="utf-8"
    )
    (data / "texture_packs.json").write_text(
        json.dumps({"emulators": {"DEMO": {}}}), encoding="utf-8"
    )
    audit_path = tmp_path / "core_audit.json"
    audit_path.write_text(json.dumps({"schema": 3, "cores": {}}), encoding="utf-8")
    output_path = tmp_path / "coverage-matrix.md"
    monkeypatch.setattr(matrix, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(matrix, "DATA_DIR", data)
    monkeypatch.setattr(matrix, "AUDIT_PATH", audit_path)
    monkeypatch.setattr(matrix, "OUTPUT_PATH", output_path)
    monkeypatch.setattr(sys, "argv", ["generate_coverage_matrix.py", str(es_systems)])

    matrix.main()

    output = output_path.read_text(encoding="utf-8")
    assert "| emulator | systems | save | savestate | texture | mod | firmware |" in output
    # The save card names one of the two systems this row serves, and the
    # texture card names none, which is the whole emulator.
    assert "| `demo` | gc, triforce | ✔ 1/2 | ✖ | ✔ | ✖ | ✖ |" in output
    assert "| `nobody` | quake | ✖ | ✖ | ✖ | ✖ | ✖ |" in output
    # A question whose card file does not exist is a column of gaps rather than
    # a crash: that is what an unanswered question looks like.
    assert "savestate 0/2" in output
    assert "save 1/2" in output and "texture 1/2" in output


class TestTheQuestionCell:
    def test_a_card_covering_the_whole_row_is_a_plain_tick(self):
        assert matrix.question_cell({"demo": {"gc", "wii"}}, "demo", {"gc", "wii"}) == "✔"

    def test_a_card_naming_no_systems_covers_the_row(self):
        assert matrix.question_cell({"demo": None}, "demo", {"gc"}) == "✔"

    def test_a_card_covering_part_of_the_row_shows_the_fraction(self):
        assert matrix.question_cell({"demo": {"gc"}}, "demo", {"gc", "wii"}) == "✔ 1/2"

    def test_no_card_is_a_gap(self):
        assert matrix.question_cell({}, "demo", {"gc"}) == "✖"

    def test_a_card_covering_none_of_the_row_is_a_gap_not_a_tick_on_zero(self):
        # "✔ 0/2" would be a tick on nothing — this row is uncovered here.
        assert matrix.question_cell({"demo": {"psx"}}, "demo", {"gc", "wii"}) == "✖"


def test_a_question_nothing_answers_yet_reads_as_a_column_of_gaps(tmp_path, monkeypatch):
    monkeypatch.setattr(matrix, "DATA_DIR", tmp_path)
    assert matrix.load_cards("standalone_savestates.json", ("savestates", "systems")) == {}


class TestTheCommittedMatrixIsRegenerated:
    """The standalone half is derived, so a stale one is a fact nobody re-read.

    The libretro half needs the deployed ``es_systems.xml`` to regenerate; this
    half needs only the packaged cards, so it can be checked anywhere — which
    is what catches "a card landed and the matrix did not".
    """

    def test_every_standalone_row_shows_what_the_cards_answer(self):
        rows = _standalone_table(matrix.OUTPUT_PATH.read_text(encoding="utf-8"))
        assert rows, "the committed matrix has no standalone rows"
        cards = {
            name: matrix.load_cards(filename, at) for name, filename, at in matrix.QUESTIONS
        }
        for key, cells in rows.items():
            named, total = _systems_of(cells[0])
            if len(named) == total:
                fresh = [
                    matrix.question_cell(cards[name], key, named)
                    for name, _, _ in matrix.QUESTIONS
                ]
                assert cells[1:] == fresh, (
                    f"the committed matrix shows {cells[1:]} for {key!r} and the cards answer "
                    f"{fresh} — regenerate with `python scripts/generate_coverage_matrix.py` "
                    "(then `deno fmt`)"
                )
                continue
            # A truncated systems cell does not carry the row's whole set, so
            # recomputing the cell from it would compare the generator's `1/54`
            # against this test's `1/4` and call a correctly regenerated matrix
            # stale. What the cell does carry is checked instead.
            for (name, _, _), shown in zip(matrix.QUESTIONS, cells[1:]):
                answered = key in cards[name]
                assert answered == shown.startswith("✔"), (
                    f"the committed matrix shows {shown!r} for {key!r}'s {name} question and "
                    f"a card {'exists' if answered else 'does not exist'} — regenerate"
                )
                if "/" in shown:
                    assert shown.endswith(f"/{total}"), (
                        f"the committed matrix shows {shown!r} for {key!r}'s {name} question "
                        f"and the row serves {total} systems — regenerate"
                    )

    def test_a_truncated_systems_cell_still_states_the_whole_count(self):
        # What the check above leans on: the row's real size survives the
        # shortening, so a fraction's denominator can be read back off the
        # matrix even where the names cannot.
        assert _systems_of("adam, amstradcpc, apple2, apple2gs, … (54)") == (
            {"adam", "amstradcpc", "apple2", "apple2gs"},
            54,
        )
        assert _systems_of("gc, triforce, wii") == ({"gc", "triforce", "wii"}, 3)

    def test_the_status_line_counts_what_the_cards_answer(self):
        text = matrix.OUTPUT_PATH.read_text(encoding="utf-8")
        rows = _standalone_table(text)
        for name, filename, at in matrix.QUESTIONS:
            answered = sum(1 for key in rows if key in matrix.load_cards(filename, at))
            assert f"{name} {answered}/{len(rows)}" in text, (
                f"the status line does not say {name} {answered}/{len(rows)} — the matrix is "
                "stale, or a card landed for an emulator the row set does not carry"
            )
