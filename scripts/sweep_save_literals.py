"""Sweep every record-covered core's shipped binary for own-save-file literals.

A save-memory record states which libretro memory ids a core fills — and where
that list is empty, the answer rides with ``core-own-writes-unestablished``:
whether the core writes save files of its own was left open. This sweep is the
mechanical half of closing that: it scans each record-covered ``.so`` for
strings that look like a core naming its own save files — a path-format join
ending in a save suffix (``%s%c%s.eep``), a bare save suffix stored whole
(``.dsv``), or a save-directory word — and prints every core whose binary
carries any. Each hit is a queue position for the full audit method
(``docs/research/core-audit.md``), never a verdict.

Two bounds to keep in mind reading the output:

- **A hit is not a writer.** The string may sit in a path the build never
  reaches (NooDS), behind a variable nothing reads (boom3), or belong to a
  different feature. Only the chain read to the write call settles it.
- **Silence is not a clean core.** Pattern triage produces false negatives —
  ``genesis_plus_gx``'s BRAM strings start mid-word and matched nothing in the
  2026-07-24 pass. A quiet core earns "expected standard" only after its
  source check.

Cards are not scanned: a card already states the core's own writers, and the
anchor tests re-read those against the shipped bytes.

Usage: ``python scripts/sweep_save_literals.py [cores-dir]``
(default: the RetroDECK Flatpak deployment's cores directory; a directory
given must lie inside the repository, the home directory, the temporary
directory or the system Flatpak installation). Stdlib only — zero
dependencies is a design contract.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RECORDS_PATH = REPO_ROOT / "atlas" / "data" / "save_memory.json"
SYSTEM_FLATPAK = Path("/var/lib/flatpak")
DEFAULT_CORES_DIR = (
    SYSTEM_FLATPAK
    / "app/net.retrodeck.retrodeck/current/active/files/retrodeck/components/retroarch/rd_extras/cores"
)

# Printable ASCII plus tab/newline/CR, cut only on anything else (NUL included):
# the scan defect that dropped newline-bearing literals is documented in
# docs/research/core-audit.md, and this spelling is the fix.
_STRING_RUN = re.compile(rb"[\x20-\x7e\t\n\r]{3,}")

# Endings a core's own save file tends to have. Deliberately broader than the
# extensions RetroArch itself writes — the point is what the *core* names.
_SAVE_SUFFIXES = (
    ".sav",
    ".srm",
    ".mcr",
    ".ngf",
    ".dsv",
    ".fs",
    ".hi",
    ".nv",
    ".eep",
    ".eeprom",
    ".eeprom128",
    ".eeprom256",
    ".brm",
    ".fla",
    ".mpk",
    ".mem",
    ".bkr",
    ".memcard",
    ".card",
    ".ram",
)

# Words a core only carries when it takes the frontend's save directory at all.
# Presence alone proves an *ask*, not a write — superbroswar takes the
# directory into the boilerplate block and reads it nowhere.
_SAVE_DIR_WORDS = ("retro_get_save_dir", "save_directory", "savedir", "save_dir")

# Families of strings that end in the same letters for other reasons.
_NOISE = re.compile(r"\.state|cht|cheat|shader|overlay|remap|playlist", re.IGNORECASE)


def is_save_literal(text: str) -> bool:
    """Whether *text* reads like a core naming its own save file or directory."""
    if _NOISE.search(text):
        return False
    lowered = text.lower()
    if any(word in lowered for word in _SAVE_DIR_WORDS):
        return True
    return lowered.endswith(_SAVE_SUFFIXES)


def hits_in(binary: bytes) -> list[str]:
    """Every distinct save-looking string in *binary*, sorted."""
    return sorted(
        {
            text
            for match in _STRING_RUN.finditer(binary)
            if is_save_literal(text := match.group().decode("ascii"))
        }
    )


def resolve_cores_dir(raw: str) -> Path:
    """*raw* resolved, symlinks followed, and refused unless it lies under one of four roots.

    The sweep reads every record core's binary out of the directory the
    command line names, so that directory is bounded to where a cores
    directory to sweep can sit: the repository, the user's home directory
    (which holds a per-user Flatpak installation too), the system's temporary
    directory as ``tempfile`` names it (``TMPDIR`` moves it), and the system
    Flatpak installation the default lies in. A mistyped argument cannot
    point the read anywhere else. A path ``resolve()`` raises on — an
    embedded NUL, or a symlink loop on Python 3.12 — is the same argument
    error. Raises ``ValueError`` for the caller to report.
    """
    try:
        path = Path(raw).resolve()
    except (ValueError, OSError, RuntimeError) as error:
        raise ValueError(f"cores directory {raw!r} cannot be resolved: {error}") from error
    roots = (
        REPO_ROOT.resolve(),
        Path.home().resolve(),
        Path(tempfile.gettempdir()).resolve(),
        SYSTEM_FLATPAK.resolve(),
    )
    if not any(path.is_relative_to(root) for root in roots):
        raise ValueError(
            f"cores directory {path} lies outside the repository, the home directory,"
            " the temporary directory and the system Flatpak installation"
        )
    return path


def main(argv: list[str]) -> int:
    try:
        cores_dir = resolve_cores_dir(argv[1] if len(argv) > 1 else str(DEFAULT_CORES_DIR))
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    if not cores_dir.is_dir():
        print(f"cores directory not found: {cores_dir}", file=sys.stderr)
        return 2

    with RECORDS_PATH.open() as f:
        records: dict[str, object] = json.load(f)["cores"]

    missing: list[str] = []
    flagged = 0
    for key in sorted(records):
        so_path = cores_dir / f"{key}_libretro.so"
        if not so_path.is_file():
            missing.append(key)
            continue
        hits = hits_in(so_path.read_bytes())
        if hits:
            flagged += 1
            shown = ", ".join(repr(hit) for hit in hits[:8])
            more = f" (+{len(hits) - 8} more)" if len(hits) > 8 else ""
            print(f"{key}: {shown}{more}")

    scanned = len(records) - len(missing)
    print()
    print(f"{flagged} of {scanned} scanned record cores carry save-looking literals")
    if missing:
        print(f"no binary deployed for: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
