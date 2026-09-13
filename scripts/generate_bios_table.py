#!/usr/bin/env python3
"""Generate a packaged PlayStation BIOS recognition table from an emulator's own source.

Two emulators recognise a BIOS by content and neither names the file: they list
a directory, keep every file of an accepted size, and look up what is left by
hashing it against a table compiled into the binary. DuckStation does that as
its whole route (``FindBIOSImageInDirectory``), and SwanStation — a fork of it,
built as a libretro core — does it as the fallback behind a configured name.
Nothing on a running machine states either table, so under DESIGN.md's boundary
rule both are world knowledge: packaged here, pinned to the revision they were
read from, and regenerated as a reviewable data diff.

One script rather than two because the two tables are the same artifact read
out of two forks of one file: the same ``bios.h`` size constants, the same
``MakeHashFromString`` row shape, the same duplicate-hash check and the same
bounded read of a local checkout. What differs is data — which repository, the
row's columns, what the emulator does with an image no row holds, and how much
of a file it hashes — so ``--emulator`` selects that data and everything else
is shared. The script is named for the artifact and not for either emulator,
because a generator named after one of them would be lying about the other.

The hashes are ``constexpr`` in both sources and compile down to byte arrays,
so they are **not** in a shipped binary's strings — the source at the pinned
revision is the only place they can be read. What a binary does carry, and what
a live check confirms, are the descriptions beside them.

The input is one local git checkout passed as an argument — inside your home
directory, which is the only place this tool will read from — and nothing here
touches the network. Clone it first, then run the generator against it:

    git clone https://github.com/stenzek/duckstation ~/src/duckstation

    python scripts/generate_bios_table.py --emulator duckstation \\
        --source ~/src/duckstation --revision 64655818e

    python scripts/generate_bios_table.py --emulator swanstation \\
        --source ~/src/swanstation --revision 4d309c0

The output file is decided by ``--emulator`` and resolved relative to the repo
root, so the command works from any working directory. There is no destination
argument: this generator produces packaged data files, and a writable
destination would be a filesystem write decided by whoever composed the command
line rather than by the package layout.
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# The packaged data files this generator produces, resolved relative to the
# repo root (scripts/ sits at the root) so the default works from any cwd.
REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "atlas" / "data"

BIOS_CPP = os.path.join("src", "core", "bios.cpp")
BIOS_H = os.path.join("src", "core", "bios.h")

# {"SCPH-1001, DTL-H1001 (v2.0 05-07-95 A)", ConsoleRegion::NTSC_U,
#  MakeHashFromString("dc2b…"), ImageInfo::FastBootPatch::Type1, 10},
DUCKSTATION_ENTRY = re.compile(
    r'\{\s*"([^"]+)"\s*,\s*'
    r"ConsoleRegion::(\w+)\s*,\s*"
    r'MakeHashFromString\("([0-9a-fA-F]{32})"\)\s*,\s*'
    r"ImageInfo::FastBootPatch::(\w+)\s*,\s*"
    r"(\d+)\s*\}"
)
# {"SCPH-5500 (v3.0 09-09-96 J)", ConsoleRegion::NTSC_J,
#  MakeHashFromString("8dd7…"), true},
SWANSTATION_ENTRY = re.compile(
    r'\{\s*"([^"]+)"\s*,\s*'
    r"ConsoleRegion::(\w+)\s*,\s*"
    r'MakeHashFromString\("([0-9a-fA-F]{32})"\)\s*,\s*'
    r"(?:true|false)\s*\}"
)
# static ImageInfo s_image_infos[27] = {
SWANSTATION_COUNT = re.compile(r"\bs_image_infos\[(\d+)\]")
# BIOS_SIZE = 0x80000, / BIOS_SIZE_PS2 = 0x400000, / BIOS_SIZE_PS3 = 0x3E66F0
SIZE_ENTRY = re.compile(r"\bBIOS_SIZE(_PS2|_PS3)?\s*=\s*(0x[0-9A-Fa-f]+)")
# static constexpr u32 s_openbios_signature_offset = 0x78;
OPENBIOS_OFFSET = re.compile(r"s_openbios_signature_offset\s*=\s*(0x[0-9A-Fa-f]+)")
OPENBIOS_SIGNATURE = re.compile(r"s_openbios_signature\[\]\s*=\s*\{([^}]*)\}")

# The console the table's own word maps to, in atlas's spelling. ``Auto`` is
# upstream's "no region of its own" — the PS2 development images carry it —
# and stays distinct from a guess: a caller filtering by region must not see
# these as matching one.
REGIONS = {
    "NTSC_J": "ntsc-j",
    "NTSC_U": "ntsc-u",
    "PAL": "pal",
    "Auto": "any",
}


@dataclass(frozen=True)
class Emulator:
    """What one emulator's table is, beside the parsing both of them share.

    ``entry`` is the row shape, and with it the columns: DuckStation's rows
    carry a fast-boot patch variant and a priority, its fork's older rows carry
    a ``patch_compatible`` flag and nothing else. That flag is **not** written
    out, because it gates patching an image the emulator already booted rather
    than recognising one, and a packaged table states what recognition reads —
    the loader refuses a column nothing here reads
    (:func:`atlas.bios_table.load_bios_table`).

    ``hash_scope_class`` names the size class whose constant is how much of a
    file the emulator hashes, or is empty where it hashes the whole file:
    SwanStation allocates ``Image ret(BIOS_SIZE)`` and reads exactly that many
    bytes out of every candidate whatever its length, so its md5 of a 4 MiB
    image is the md5 of the image's first 512 KiB. ``unknown`` is what its
    search does with an image no row holds.
    """

    repository: str
    output: str
    entry: "re.Pattern[str]"
    count: "re.Pattern[str] | None"
    openbios: bool
    hash_scope_class: str
    unknown: str


EMULATORS = {
    "duckstation": Emulator(
        repository="stenzek/duckstation",
        output="duckstation_bios.json",
        entry=DUCKSTATION_ENTRY,
        count=None,
        openbios=True,
        hash_scope_class="",
        unknown="",
    ),
    "swanstation": Emulator(
        repository="libretro/swanstation",
        output="swanstation_bios.json",
        entry=SWANSTATION_ENTRY,
        count=SWANSTATION_COUNT,
        openbios=False,
        hash_scope_class="ps1",
        unknown="refused",
    ),
}


def resolve_source(raw: str) -> tuple[str, str]:
    """Resolve ``--source`` to the two files this generator reads, and nothing else.

    Two bounds, and both are the point rather than ceremony. The checkout has
    to sit inside the home directory — clone it where you keep sources — so
    that a wrong argument cannot walk the generator into system files; and
    only two fixed paths below it are ever opened, each canonicalised and then
    required to still sit under the canonical checkout, so a symlinked
    ``src/`` cannot redirect the read either. The order matters: a check
    applied to the spelling rather than to the resolved path is no check at
    all, and the trailing separator is what keeps a sibling directory whose
    name merely starts the same from passing. Raises ``ValueError`` for the
    caller to report as an argument error.
    """
    base = os.path.realpath(os.path.expanduser("~")) + os.sep
    checkout = os.path.realpath(os.path.expanduser(raw))
    if not checkout.startswith(base):
        raise ValueError(f"the checkout must be inside {base}, got {checkout}")
    if not os.path.isdir(checkout):
        raise ValueError(f"not a directory: {checkout}")
    resolved = []
    for relative in (BIOS_CPP, BIOS_H):
        target = os.path.realpath(os.path.join(checkout, relative))
        if not target.startswith(checkout + os.sep):
            raise ValueError(f"{relative} resolves outside {checkout}")
        if not os.path.isfile(target):
            raise ValueError(f"{checkout} has no {relative} — is this the right checkout?")
        resolved.append(target)
    return resolved[0], resolved[1]


def parse_sizes(header: str) -> dict[str, int]:
    """The three file sizes the search accepts, from ``bios.h``'s own constants.

    A file of any other size is skipped before its bytes are ever read
    (``FindBIOSImageInDirectory``), which is why the sizes belong beside the
    table rather than in code: they are the first half of the same recognition
    rule. Both forks declare the same three constants under the same names.
    """
    sizes = {}
    for suffix, value in SIZE_ENTRY.findall(header):
        key = {"": "ps1", "_PS2": "ps2", "_PS3": "ps3"}[suffix]
        sizes[key] = int(value, 16)
    missing = {"ps1", "ps2", "ps3"} - set(sizes)
    if missing:
        raise ValueError(f"bios.h states no size for {sorted(missing)}")
    return sizes


def parse_openbios(source: str) -> dict[str, object]:
    """OpenBIOS, which the table cannot hold: a signature at a fixed offset.

    Upstream says why in its own comment — the replacement BIOS has no fixed
    hash — so it is recognised by eight bytes at ``0x78`` instead. Recorded
    here as what it is, so the resolver can state it as a known limit rather
    than silently reporting an unrecognised image.
    """
    offset = OPENBIOS_OFFSET.search(source)
    signature = OPENBIOS_SIGNATURE.search(source)
    if offset is None or signature is None:
        raise ValueError("bios.cpp states no OpenBIOS signature")
    letters = re.findall(r"'(.)'", signature.group(1))
    return {"signature": "".join(letters), "offset": int(offset.group(1), 16)}


def parse_images(source: str, emulator: Emulator) -> list[dict[str, object]]:
    """Every row of the table, in order, with the columns this emulator's rows carry.

    Order is upstream's and is kept: for DuckStation it is not the tie-break
    (``priority`` is), for its fork it is the tie-break itself — the search
    takes the first region-valid file the directory hands over — and either
    way a diff of this file should read like a diff of the table.

    Where the source declares how many rows the table has, the parse is held
    against that number: a row the regex silently missed would otherwise ship
    as a table that recognises one image fewer than the emulator does.
    """
    images: list[dict[str, object]] = []
    for row in emulator.entry.findall(source):
        name, region = row[0], row[1]
        if region not in REGIONS:
            raise ValueError(f"unknown ConsoleRegion {region!r} for {name!r}")
        image: dict[str, object] = {
            "name": name,
            "region": REGIONS[region],
            "md5": row[2].lower(),
        }
        if len(row) > 3:
            image["priority"] = int(row[4])
            image["fast_boot_patch"] = row[3].lower()
        images.append(image)
    if not images:
        raise ValueError("bios.cpp holds no image table — did the format change?")
    declared = None if emulator.count is None else emulator.count.search(source)
    if declared is not None and int(declared.group(1)) != len(images):
        raise ValueError(
            f"bios.cpp declares {declared.group(1)} rows and {len(images)} were read — "
            "the row shape moved"
        )
    return images


def build(source: str, header: str, revision: str, emulator: Emulator) -> tuple[dict[str, object], int]:
    images = parse_images(source, emulator)
    hashes = [str(image["md5"]) for image in images]
    duplicates = sorted({md5 for md5 in hashes if hashes.count(md5) > 1})
    if duplicates:
        raise ValueError(f"the table has repeated hashes: {duplicates}")
    sizes = parse_sizes(header)
    table: dict[str, object] = {
        "_meta": {
            "generated_from": f"{emulator.repository} {BIOS_CPP} and {BIOS_H}",
            "revision": revision,
            "generated_at": datetime.now(timezone.utc).date().isoformat(),
            "images": len(images),
        },
        "sizes": sizes,
    }
    if emulator.hash_scope_class:
        # The scope is not a fourth constant: it is the launch-console size
        # itself, because the emulator allocates an image of exactly that
        # length and reads into it.
        table["hash_scope"] = sizes[emulator.hash_scope_class]
    if emulator.unknown:
        table["unknown"] = emulator.unknown
    if emulator.openbios:
        table["openbios"] = parse_openbios(source)
    table["images"] = images
    return table, len(images)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--emulator", required=True, choices=sorted(EMULATORS), help="whose table to read"
    )
    parser.add_argument("--source", required=True, help="local checkout to read")
    parser.add_argument("--revision", required=True, help="the revision the checkout is at")
    args = parser.parse_args(argv)
    emulator = EMULATORS[args.emulator]
    output = DATA_DIR / emulator.output
    try:
        bios_cpp, bios_h = resolve_source(args.source)
        with open(bios_cpp, encoding="utf-8") as handle:
            source = handle.read()
        with open(bios_h, encoding="utf-8") as handle:
            header = handle.read()
        table, count = build(source, header, args.revision, emulator)
    except ValueError as error:
        parser.error(str(error))
    output.write_text(json.dumps(table, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{output}: {count} images at {args.revision}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
