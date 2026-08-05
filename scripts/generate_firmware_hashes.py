#!/usr/bin/env python3
"""Generate atlas/data/firmware_hashes.json from libretro-database's System.dat.

This is the offline, dev-time build step for the packaged firmware **identity**
table — the ``md5``/``sha1``/``size`` triple that says what a correct firmware
file's bytes are. That is world knowledge: no config on the machine states it.

What a core *wants* is deliberately not here. Those declarations live in the
``.info`` files RetroArch ships next to its cores, and atlas reads them off the
running machine (``atlas/firmware.py``); an offline snapshot of them would drift
against the cores an installation actually has. The split is the boundary rule
made visible, and the filename is where it shows.

The input is one local git checkout passed as an argument; nothing here touches
the network. Clone it first, then run the generator against the checkout:

    git clone https://github.com/libretro/libretro-database ~/src/libretro-database

    python scripts/generate_firmware_hashes.py --database ~/src/libretro-database

With no ``-o`` the output defaults to ``atlas/data/firmware_hashes.json``
(resolved relative to the repo root, so the command works from any working
directory). A regeneration is a deliberate, reviewable data diff — see
``atlas/data/README.md``.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# The packaged data file this generator produces, resolved relative to the repo
# root (scripts/ sits at the root) so the default works from any cwd.
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "atlas" / "data" / "firmware_hashes.json"

SCHEMA_VERSION = "5.0.0"
GENERATED_FROM = "libretro-database dat/System.dat"

# rom ( name "filename.bin" size 524288 crc AABBCCDD md5 ... sha1 ... )
# The name may or may not be quoted.
ROM_ENTRY = re.compile(
    r'rom\s*\(\s*name\s+"?([^")\s]+)"?\s+'
    r"size\s+(\d+)\s+"
    r"crc\s+([0-9A-Fa-f]+)\s+"
    r"md5\s+([0-9a-f]+)\s+"
    r"sha1\s+([0-9a-f]+)\s*\)"
)


def resolve_database(raw: str) -> Path:
    """Resolve ``--database`` to the ``dat/System.dat`` this generator reads.

    The checkout is a clone the user made wherever they keep sources, so no
    base directory bounds it; what identifies a usable one is the file below
    it. Resolving the checkout and the file separately and then requiring the
    file to still be inside the checkout keeps a symlinked ``dat/`` from
    pointing the read at something else. Raises ``ValueError`` for the caller
    to report as an argument error.
    """
    database_dir = Path(raw).resolve()
    if not database_dir.is_dir():
        raise ValueError(f"database directory not found: {raw}")
    dat_path = (database_dir / "dat" / "System.dat").resolve()
    if not dat_path.is_relative_to(database_dir):
        raise ValueError(f"dat/System.dat leaves the checkout {database_dir} (resolves to {dat_path})")
    if not dat_path.is_file():
        raise ValueError(f"{dat_path} not found — is {raw} a libretro-database checkout?")
    return dat_path


def resolve_output(raw: str) -> Path:
    """Resolve ``-o`` to the JSON file to write.

    The generator writes one file into a directory that already exists; it
    never creates a directory tree, so a mistyped path is refused here instead
    of scattering directories. Raises ``ValueError`` like ``resolve_database``.
    """
    output_path = Path(raw).resolve()
    if output_path.suffix != ".json":
        raise ValueError(f"output must be a .json path, got {raw}")
    if output_path.is_dir():
        raise ValueError(f"output path is a directory: {output_path}")
    if not output_path.parent.is_dir():
        raise ValueError(f"output directory does not exist: {output_path.parent}")
    return output_path


def parse_system_dat(dat_path):
    """Parse ``dat/System.dat`` into ``{filename: {"md5", "sha1", "size"}}``."""
    try:
        with open(dat_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as e:
        print(f"Error: cannot read {dat_path}: {e}", file=sys.stderr)
        raise SystemExit(1) from None

    hashes = {}
    for match in ROM_ENTRY.finditer(content):
        name, size, md5, sha1 = match.group(1, 2, 4, 5)
        hashes[name] = {"md5": md5, "sha1": sha1, "size": int(size)}
    return hashes


def build_table(dat_path):
    """Build the complete identity table, file names sorted."""
    print(f"Parsing System.dat from {dat_path}...", file=sys.stderr)
    hashes = parse_system_dat(dat_path)
    print(f"  Found {len(hashes)} firmware identities", file=sys.stderr)
    return {
        "_meta": {
            "generated_from": GENERATED_FROM,
            "version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        },
        "files": dict(sorted(hashes.items())),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate the packaged firmware identity table from libretro-database.",
        epilog=(
            "Example:\n"
            "  python scripts/generate_firmware_hashes.py \\\n"
            "    --database ~/src/libretro-database"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--database",
        required=True,
        help="Path to a libretro-database checkout",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output JSON path (default: atlas/data/firmware_hashes.json)",
    )
    args = parser.parse_args()

    # Both paths a run takes from its arguments are resolved and checked before
    # anything opens them, and only the resolved forms are used from here on.
    try:
        dat_path = resolve_database(args.database)
        output_path = resolve_output(args.output)
    except ValueError as exc:
        parser.error(str(exc))

    table = build_table(dat_path)

    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(table, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Wrote {output_path} ({len(table['files'])} firmware identities)", file=sys.stderr)


if __name__ == "__main__":
    main()
