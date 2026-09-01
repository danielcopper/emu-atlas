"""Generate docs/research/coverage-matrix.md from the audit data + es_systems.xml.

The coverage matrix is the entry point for the core-by-core pass: every emulator
the RetroDECK matrix references (libretro core or standalone runner), which
systems it serves, its audit verdict, whether per-game saves are a proven
capability, and per arrangement (RetroDECK / EmuDeck / bare RetroArch) which
version the knowledge was verified against.

Facts come from ``atlas/data/core_audit.json`` for the libretro half and from
the packaged cards for the standalone one; the row set comes from
``es_systems.xml`` so uncovered emulators appear automatically as the work
list — nothing here is hand-maintained.

The two halves are counted differently because they are different records.
``core_audit.json`` is the libretro **save audit** and holds no standalone
entry, so reading a standalone verdict out of it could only ever say
"unaudited" — which is what it said for every row, including the emulators
whose answers are live-verified on two arrangements (#228). The standalone
half now shows one column per question, filled from the card file that answers
it, so a gap is generated rather than surveyed by hand.

Usage: ``python scripts/generate_coverage_matrix.py [path-to-es_systems.xml]``
(default: the RetroDECK Flatpak deployment's bundled file). Stdlib only —
including ``xml.etree``: the input is a local, trusted config file, modern
expat rejects entity-expansion attacks, and zero dependencies is a design
contract (``atlas/esde.py`` documents the same reasoning). The package itself
reaches expat through ``atlas/_xml.py`` instead, because a vendored copy has to
run on runtimes that ship the parser without the wrapper package; a maintainer
script runs here, on a full Python.
"""

from __future__ import annotations

import hashlib
import json
import re
import string
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "atlas" / "data"
AUDIT_PATH = DATA_DIR / "core_audit.json"
OUTPUT_PATH = REPO_ROOT / "docs" / "research" / "coverage-matrix.md"
DEFAULT_ES_SYSTEMS = Path(
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/"
    "components/es-de/share/es-de/resources/systems/linux/es_systems.xml"
)

_CORE_SO_SUFFIX = "_libretro.so"
_CORE_NAME_CHARS = frozenset(string.ascii_letters + string.digits + "_-[]")
_RUNNER_RE = re.compile(r"%EMULATOR_([A-Z0-9_\-]+)%")

ARRANGEMENTS = ("retrodeck", "emudeck", "bare")
ARRANGEMENT_HEADERS = {"retrodeck": "RetroDECK", "emudeck": "EmuDeck", "bare": "RetroArch (bare)"}
AUDIT_SCHEMA = 3

# One column per question a standalone entry can be asked, and the card file
# that answers it. ``systems`` is the path to the list a card states where it
# answers only for some of them — a save card covers Dolphin's gc and wii and
# not the triforce row beside them — and ``None`` where the card is about the
# emulator rather than about a system. A file that does not exist yet answers
# nothing and reads as a column of gaps, which is the honest state of
# savestates (#225): the day the card file lands, the column fills itself.
QUESTIONS: tuple[tuple[str, str, tuple[str, ...] | None], ...] = (
    ("save", "standalone_saves.json", ("saves", "systems")),
    ("savestate", "standalone_savestates.json", ("savestates", "systems")),
    ("texture", "texture_packs.json", None),
    ("mod", "mods.json", None),
    ("firmware", "standalone_firmware.json", ("systems",)),
)


def core_short_name(command: str) -> str | None:
    """The ``<name>`` of the leftmost ``<name>_libretro.so`` in *command*, or None.

    The name's characters include the '_' that opens the suffix, so a regex
    ``[...]+_libretro\\.so`` has to try the run at every position of the command
    line and give it back again — quadratic, and measurably so. Locating the
    literal suffix and walking the name backwards is one pass.
    """
    suffix_at = command.find(_CORE_SO_SUFFIX)
    while suffix_at != -1:
        start = suffix_at
        while start and command[start - 1] in _CORE_NAME_CHARS:
            start -= 1
        if start != suffix_at:
            return command[start:suffix_at]
        # A suffix with no name before it (e.g. a bare "_libretro.so") is not a
        # core; the next occurrence still can be.
        suffix_at = command.find(_CORE_SO_SUFFIX, suffix_at + 1)
    return None


def collect_rows(es_systems_path: Path) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Return ``(libretro, standalone)`` as ``{emulator_key: {systems}}``."""
    libretro: dict[str, set[str]] = defaultdict(set)
    standalone: dict[str, set[str]] = defaultdict(set)
    root = ET.parse(es_systems_path).getroot()
    for system_el in root.findall("system"):
        name = (system_el.findtext("name") or "").strip()
        for command_el in system_el.findall("command"):
            command = (command_el.text or "").strip()
            core = core_short_name(command)
            if core:
                libretro[core].add(name)
                continue
            runner = _RUNNER_RE.search(command)
            if runner and runner.group(1) != "RETROARCH":
                standalone[runner.group(1).lower()].add(name)
    return dict(libretro), dict(standalone)


def load_audit_data(path: Path) -> tuple[int, dict[str, dict[str, object]]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != AUDIT_SCHEMA:
        schema = raw.get("schema") if isinstance(raw, dict) else None
        raise ValueError(f"core_audit: unsupported schema {schema!r} (generator reads schema {AUDIT_SCHEMA})")
    cores_raw = raw.get("cores")
    if not isinstance(cores_raw, dict):
        raise ValueError("core_audit: 'cores' must be an object")
    cores: dict[str, dict[str, object]] = {}
    for key, entry in cores_raw.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            raise ValueError("core_audit: every core key must be a string and every entry an object")
        cores[key] = entry
    return AUDIT_SCHEMA, cores


def load_cards(filename: str, systems_at: tuple[str, ...] | None) -> dict[str, set[str] | None]:
    """``{row key: the systems that card answers for}`` for one question.

    ``None`` as a value means the card answers for the emulator rather than
    for a list of systems. A missing file is a question nothing answers yet,
    not an error: the row set is the work list and an empty column is what an
    unanswered question looks like.
    """
    path = DATA_DIR / filename
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    emulators = raw.get("emulators")
    if not isinstance(emulators, dict):
        raise ValueError(f"{filename}: 'emulators' must be an object")
    cards: dict[str, set[str] | None] = {}
    for token, entry in emulators.items():
        systems: set[str] | None = None
        if systems_at is not None:
            node: object = entry
            for step in systems_at:
                node = node.get(step) if isinstance(node, dict) else None
            if node is not None:
                if not isinstance(node, list):
                    raise ValueError(f"{filename}: {token} states a non-list {'.'.join(systems_at)}")
                systems = {str(s) for s in node}
        cards[token.lower()] = systems
    return cards


def question_cell(cards: dict[str, set[str] | None], key: str, systems: set[str]) -> str:
    """Whether a card answers this question here, and for how much of the row.

    A card that names systems answers only those: Dolphin's save card covers
    gc and wii and the row also serves triforce, so the cell says 2/3 rather
    than claiming the row. That fraction is the part of the work list nothing
    else records.
    """
    if key not in cards:
        return "✖"
    stated = cards[key]
    if stated is None or not systems:
        return "✔"
    covered = systems & stated
    if not covered:
        # A card that names only systems this row does not serve answers none
        # of it. "✔ 0/3" is a tick on nothing — the row is uncovered here, and
        # the mark has to say so. No shipped card is in this state; the branch
        # exists so the first one that is cannot read as coverage.
        return "✖"
    return "✔" if covered == systems else f"✔ {len(covered)}/{len(systems)}"


def cell(audit_entry: dict[str, object] | None, arrangement: str, kind: str) -> str:
    if kind == "standalone" and arrangement == "bare":
        return "—"
    verified = None
    if audit_entry is not None:
        verified_map = audit_entry.get("verified")
        verified = verified_map.get(arrangement) if isinstance(verified_map, dict) else None
    if verified is not None:
        version = verified.get("version") or "?"
        return f"✔ {version}"
    # The row set comes from RetroDECK's shipped matrix, so existence there is
    # certain (✖ = present but unverified). EmuDeck ships its OWN emulator set
    # (unresearched, issue #11) and bare-RetroArch cores are user-installed —
    # availability itself is unknown there, not merely the verification.
    return "✖" if arrangement == "retrodeck" else "?"


def per_game_cell(audit_entry: dict[str, object] | None) -> str:
    if audit_entry is None:
        return "?"
    if "per_game_capable" not in audit_entry:
        raise ValueError("audit entry is missing required field 'per_game_capable'")
    value = audit_entry["per_game_capable"]
    if value is True:
        return "yes"
    if value is False:
        return "no"
    if value is None:
        return "?"
    raise ValueError(f"per_game_capable must be a boolean or null, got {value!r}")


def note_cell(audit_entry: dict[str, object] | None) -> str:
    if audit_entry is None:
        return "—"
    note = audit_entry.get("note")
    if not isinstance(note, str) or not note:
        raise ValueError(f"audit note must be a non-empty string, got {note!r}")
    return note.replace("|", "\\|").replace("\n", " ").replace("<", "&lt;").replace(">", "&gt;")


def systems_summary(systems: set[str], limit: int = 4) -> str:
    ordered = sorted(systems)
    if len(ordered) <= limit:
        return ", ".join(ordered)
    return ", ".join(ordered[:limit]) + f", … ({len(ordered)})"


def es_systems_source(argv: list[str]) -> Path:
    """The es_systems.xml to read: the one named on the command line, or the
    RetroDECK deployment's.

    A passed path must be an existing ``.xml`` file; no base directory bounds
    it, because the file belongs to whichever deployment is being read.
    Resolve, then check, then use only the resolved path — a check against the
    raw string could pass while the read that follows takes a different path
    through a '..' segment or a symlink.
    """
    if len(argv) <= 1:
        if not DEFAULT_ES_SYSTEMS.is_file():
            print(f"es_systems.xml not found at {DEFAULT_ES_SYSTEMS} — pass a path as argv[1]")
            raise SystemExit(1)
        return DEFAULT_ES_SYSTEMS
    path = Path(argv[1]).resolve()
    if path.suffix != ".xml":
        print(f"not an es_systems.xml: {argv[1]} (expected a .xml file)")
        raise SystemExit(1)
    if not path.is_file():
        print(f"es_systems.xml not found at {path} — pass a readable file as argv[1]")
        raise SystemExit(1)
    return path


def main() -> None:
    es_systems_path = es_systems_source(sys.argv)
    audit_schema, audit = load_audit_data(AUDIT_PATH)
    libretro, standalone = collect_rows(es_systems_path)
    # Full source identity for exact reproduction: content hashes name the
    # exact inputs, independent of where or when the script ran.
    es_sha = hashlib.sha256(es_systems_path.read_bytes()).hexdigest()[:12]
    audit_sha = hashlib.sha256(AUDIT_PATH.read_bytes()).hexdigest()[:12]

    # Audit-only keys (e.g. cards for cores not in the matrix) still get rows.
    for key, entry in audit.items():
        if entry.get("kind") == "libretro":
            libretro.setdefault(key, set())
        else:
            standalone.setdefault(key, set())

    lines: list[str] = []
    lines.append("# Coverage matrix — GENERATED, do not edit")
    lines.append("")
    lines.append("Regenerate with `python scripts/generate_coverage_matrix.py` (then `deno fmt`). Facts come from")
    lines.append("`atlas/data/core_audit.json` for the libretro half and from the packaged cards for the standalone")
    lines.append("one; the row set comes from RetroDECK's bundled `es_systems.xml`, so an emulator nothing covers yet")
    lines.append("appears automatically as the work list. Cells: ✔ verified (with the arrangement")
    lines.append("version the knowledge was proven against), ✖ present but not verified, ? availability unknown there")
    lines.append("(EmuDeck ships its own emulator set — unresearched; bare-RetroArch cores are user-installed), — not")
    lines.append("applicable. Per-game capable: yes = at least one mode is proven by source, binary, or observation; no =")
    lines.append("absence is proven; ? = not established. This is capability, not the active mode on one machine. The row")
    lines.append("set is RetroDECK's shipped matrix. Verdicts and evidence levels are defined in")
    lines.append("`docs/research/core-audit.md`. Those columns describe the libretro half; the")
    lines.append("standalone half is one column per question, filled from the packaged cards.")
    lines.append("")
    lines.append(
        f"Source identity: `es_systems.xml` sha256 `{es_sha}` · `core_audit.json` "
        f"(schema {audit_schema}) sha256 `{audit_sha}`."
    )
    lines.append("")

    def entry_for(key: str, kind: str):
        entry = audit.get(key)
        # Same short name can exist as core AND standalone runner (pcsx2):
        # an audit entry only applies to its own kind.
        if entry is not None and entry.get("kind", "libretro") != kind:
            return None
        return entry

    cards = {name: load_cards(filename, at) for name, filename, at in QUESTIONS}
    answered = {
        name: sum(1 for key in standalone if key in cards[name]) for name, _, _ in QUESTIONS
    }
    audited_lib = sum(1 for k in libretro if entry_for(k, "libretro"))
    lines.append(
        f"**Status:** libretro {audited_lib}/{len(libretro)} audited · standalone "
        + " · ".join(f"{name} {answered[name]}/{len(standalone)}" for name, _, _ in QUESTIONS)
    )
    lines.append("")

    lines.append("## libretro cores")
    lines.append("")
    lines.append(
        "| emulator | systems | verdict | per-game capable | RetroDECK | EmuDeck | RetroArch (bare) | note |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for key in sorted(libretro, key=lambda k: (entry_for(k, "libretro") is None, k)):
        entry = entry_for(key, "libretro")
        verdict = entry.get("verdict", "?") if entry else "unaudited"
        cells = " | ".join(cell(entry, a, "libretro") for a in ARRANGEMENTS)
        lines.append(
            f"| `{key}` | {systems_summary(libretro[key])} | {verdict} | {per_game_cell(entry)} | "
            f"{cells} | {note_cell(entry)} |"
        )
    lines.append("")

    lines.append("## standalone emulators")
    lines.append("")
    lines.append(
        "One column per question. ✔ a packaged card answers it; ✔ n/m the card answers for n of "
        "the m systems this row serves; ✖ nothing answers it yet. A ✔ includes cards whose "
        "answer is a stated, cited no — the savestate family can state \"this emulator has no "
        "savestates\" (`no_savestates`, #284), and that is an answer, not a gap. There is no "
        "verdict column here: `core_audit.json` is the libretro save audit and holds no "
        "standalone entry, so the answer to \"is this covered\" is the cards themselves."
    )
    lines.append("")
    header = " | ".join(name for name, _, _ in QUESTIONS)
    lines.append(f"| emulator | systems | {header} |")
    lines.append("| --- | --- | " + " | ".join("---" for _ in QUESTIONS) + " |")
    covered = {
        key: sum(1 for name, _, _ in QUESTIONS if key in cards[name]) for key in standalone
    }
    for key in sorted(standalone, key=lambda k: (covered[k] == 0, k)):
        answers = " | ".join(
            question_cell(cards[name], key, standalone[key]) for name, _, _ in QUESTIONS
        )
        lines.append(f"| `{key}` | {systems_summary(standalone[key])} | {answers} |")
    lines.append("")

    OUTPUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"written: {OUTPUT_PATH.relative_to(REPO_ROOT)} ({len(libretro)} libretro, {len(standalone)} standalone)")


if __name__ == "__main__":
    main()
