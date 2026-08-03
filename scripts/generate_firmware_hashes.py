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
import os
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


def parse_system_dat(database_dir):
    """Parse ``dat/System.dat`` into ``{filename: {"md5", "sha1", "size"}}``."""
    dat_path = os.path.join(database_dir, "dat", "System.dat")
    if not os.path.isfile(dat_path):
        print(f"Error: {dat_path} not found", file=sys.stderr)
        raise SystemExit(1)

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


def build_table(database_dir):
    """Build the complete identity table, file names sorted."""
    print(f"Parsing System.dat from {database_dir}...", file=sys.stderr)
    hashes = parse_system_dat(database_dir)
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

    if not os.path.isdir(args.database):
        parser.error(f"database directory not found: {args.database}")

    table = build_table(args.database)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8", newline="\n") as f:
        json.dump(table, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Wrote {args.output} ({len(table['files'])} firmware identities)", file=sys.stderr)


if __name__ == "__main__":
    main()
