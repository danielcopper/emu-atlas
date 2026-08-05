"""Generate docs/research/coverage-matrix.md from the audit data + es_systems.xml.

The coverage matrix is the entry point for the core-by-core pass: every emulator
the RetroDECK matrix references (libretro core or standalone runner), which
systems it serves, its audit verdict, whether per-game saves are a proven
capability, and per arrangement (RetroDECK / EmuDeck / bare RetroArch) which
version the knowledge was verified against.

Facts come from ``atlas/data/core_audit.json`` (maintained, test-enforced); the
row set comes from ``es_systems.xml`` so unaudited emulators appear
automatically as the work list — nothing here is hand-maintained.

Usage: ``python scripts/generate_coverage_matrix.py [path-to-es_systems.xml]``
(default: the RetroDECK Flatpak deployment's bundled file). Stdlib only —
including ``xml.etree``: the input is a local, trusted config file, modern
expat rejects entity-expansion attacks, and zero dependencies is a design
contract (see ``atlas/esde.py`` for the same call).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = REPO_ROOT / "atlas" / "data" / "core_audit.json"
OUTPUT_PATH = REPO_ROOT / "docs" / "research" / "coverage-matrix.md"
DEFAULT_ES_SYSTEMS = Path(
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/"
    "components/es-de/share/es-de/resources/systems/linux/es_systems.xml"
)

_CORE_SO_RE = re.compile(r"([A-Za-z0-9_\-\[\]]+)_libretro\.so")
_RUNNER_RE = re.compile(r"%EMULATOR_([A-Z0-9_\-]+)%")

ARRANGEMENTS = ("retrodeck", "emudeck", "bare")
ARRANGEMENT_HEADERS = {"retrodeck": "RetroDECK", "emudeck": "EmuDeck", "bare": "RetroArch (bare)"}
AUDIT_SCHEMA = 3


def collect_rows(es_systems_path: Path) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Return ``(libretro, standalone)`` as ``{emulator_key: {systems}}``."""
    libretro: dict[str, set[str]] = defaultdict(set)
    standalone: dict[str, set[str]] = defaultdict(set)
    root = ET.parse(es_systems_path).getroot()
    for system_el in root.findall("system"):
        name = (system_el.findtext("name") or "").strip()
        for command_el in system_el.findall("command"):
            command = (command_el.text or "").strip()
            core = _CORE_SO_RE.search(command)
            if core:
                libretro[core.group(1)].add(name)
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


def main() -> None:
    es_systems_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ES_SYSTEMS
    if not es_systems_path.exists():
        print(f"es_systems.xml not found at {es_systems_path} — pass a path as argv[1]")
        raise SystemExit(1)

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
    lines.append("`atlas/data/core_audit.json`; the row set comes from RetroDECK's bundled `es_systems.xml`, so")
    lines.append("unaudited emulators appear automatically as the work list. Cells: ✔ verified (with the arrangement")
    lines.append("version the knowledge was proven against), ✖ present but not verified, ? availability unknown there")
    lines.append("(EmuDeck ships its own emulator set — unresearched; bare-RetroArch cores are user-installed), — not")
    lines.append("applicable. Per-game capable: yes = at least one mode is proven by source, binary, or observation; no =")
    lines.append("absence is proven; ? = not established. This is capability, not the active mode on one machine. The row")
    lines.append("set is RetroDECK's shipped matrix. Verdicts and evidence levels are defined in")
    lines.append("`docs/research/core-audit.md`.")
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

    audited_lib = sum(1 for k in libretro if entry_for(k, "libretro"))
    audited_sa = sum(1 for k in standalone if entry_for(k, "standalone"))
    lines.append(
        f"**Status:** libretro {audited_lib}/{len(libretro)} audited · standalone {audited_sa}/{len(standalone)} audited"
    )
    lines.append("")

    for title, rows, kind in (
        ("libretro cores", libretro, "libretro"),
        ("standalone emulators", standalone, "standalone"),
    ):
        lines.append(f"## {title}")
        lines.append("")
        lines.append(
            "| emulator | systems | verdict | per-game capable | RetroDECK | EmuDeck | RetroArch (bare) | note |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for key in sorted(rows, key=lambda k: (entry_for(k, kind) is None, k)):
            entry = entry_for(key, kind)
            verdict = entry.get("verdict", "?") if entry else "unaudited"
            cells = " | ".join(cell(entry, a, kind) for a in ARRANGEMENTS)
            lines.append(
                f"| `{key}` | {systems_summary(rows[key])} | {verdict} | {per_game_cell(entry)} | "
                f"{cells} | {note_cell(entry)} |"
            )
        lines.append("")

    OUTPUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"written: {OUTPUT_PATH.relative_to(REPO_ROOT)} ({len(libretro)} libretro, {len(standalone)} standalone)")


if __name__ == "__main__":
    main()
