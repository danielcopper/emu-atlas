#!/usr/bin/env python3
"""Generate atlas/data/duckstation_bios.json from DuckStation's own BIOS table.

DuckStation names no BIOS file. It searches a directory, keeps every file of an
accepted size, and recognises the image by **content** against a table compiled
into the binary — so "is a BIOS here" cannot be answered by looking at names,
and the table is the only thing that turns a directory listing into an answer.
That table is world knowledge under DESIGN.md's boundary rule: nothing on a
running machine states it, so it is packaged here, pinned to the revision it
was read from, and regenerated as a reviewable data diff.

The hashes are ``constexpr`` in upstream's source and compile down to byte
arrays, so they are **not** in the shipped binary's strings — the source at the
pinned revision is the only place they can be read. What the binary does carry,
and what a live check confirms, are the descriptions beside them.

The input is one local git checkout passed as an argument; nothing here touches
the network. Clone it first, then run the generator against the checkout:

    git clone https://github.com/stenzek/duckstation ~/src/duckstation

    python scripts/generate_duckstation_bios.py --source ~/src/duckstation \\
        --revision 64655818e

With no ``-o`` the output defaults to ``atlas/data/duckstation_bios.json``
(resolved relative to the repo root, so the command works from any working
directory); ``-o`` may name another ``.json`` path inside the repository.
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
DEFAULT_OUTPUT = REPO_ROOT / "atlas" / "data" / "duckstation_bios.json"

BIOS_CPP = Path("src") / "core" / "bios.cpp"
BIOS_H = Path("src") / "core" / "bios.h"

# {"SCPH-1001, DTL-H1001 (v2.0 05-07-95 A)", ConsoleRegion::NTSC_U,
#  MakeHashFromString("dc2b…"), ImageInfo::FastBootPatch::Type1, 10},
IMAGE_ENTRY = re.compile(
    r'\{\s*"([^"]+)"\s*,\s*'
    r"ConsoleRegion::(\w+)\s*,\s*"
    r'MakeHashFromString\("([0-9a-fA-F]{32})"\)\s*,\s*'
    r"ImageInfo::FastBootPatch::(\w+)\s*,\s*"
    r"(\d+)\s*\}"
)
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


def resolve_source(raw: str) -> Path:
    """Resolve ``--source`` to the checkout this generator reads two files from.

    The checkout is a clone the user made wherever they keep sources, so no
    base directory bounds it; what identifies a usable one is the pair of
    files below it. Both are resolved and then required to still be inside the
    resolved checkout, so a symlinked ``src/`` cannot point the read at
    something else. Raises ``ValueError`` for the caller to report.
    """
    checkout = Path(raw).expanduser().resolve()
    if not checkout.is_dir():
        raise ValueError(f"not a directory: {checkout}")
    for relative in (BIOS_CPP, BIOS_H):
        target = (checkout / relative).resolve()
        if not target.is_file():
            raise ValueError(f"{checkout} has no {relative} — is this a duckstation checkout?")
        if not target.is_relative_to(checkout):
            raise ValueError(f"{relative} resolves outside {checkout}")
    return checkout


def resolve_output(raw: str) -> Path:
    """Resolve ``-o`` to the JSON file this run writes, and refuse anything else.

    What this generator produces is one packaged data file, so every
    legitimate destination is inside this repository — and confining the write
    to it is what keeps a wandering argument (``../../…``, a symlinked
    directory) from turning a dev-time step into an arbitrary write. The order
    matters: resolve first, then test, since a check applied to the spelling
    rather than to the resolved path is no check at all. Raises ``ValueError``
    like :func:`resolve_source`.
    """
    output = Path(raw).expanduser().resolve()
    if output.suffix != ".json":
        raise ValueError(f"the output must be a .json path, got {raw}")
    if not output.is_relative_to(REPO_ROOT):
        raise ValueError(f"the output must be inside {REPO_ROOT}, got {output}")
    if output.is_dir():
        raise ValueError(f"the output path is a directory: {output}")
    if not output.parent.is_dir():
        raise ValueError(f"no directory to write into: {output.parent}")
    return output


def parse_sizes(header: str) -> dict[str, int]:
    """The three file sizes the search accepts, from ``bios.h``'s own constants.

    A file of any other size is skipped before its bytes are ever read
    (bios.cpp's ``FindBIOSImageInDirectory``), which is why the sizes belong
    beside the table rather than in code: they are the first half of the same
    recognition rule.
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


def parse_images(source: str) -> list[dict[str, object]]:
    """Every ``(description, region, md5, priority)`` row of the table, in order.

    Order is upstream's and is kept: it is not the tie-break (``priority``
    is), but a diff of this file should read like a diff of the table.
    """
    images = []
    for name, region, md5, patch, priority in IMAGE_ENTRY.findall(source):
        if region not in REGIONS:
            raise ValueError(f"unknown ConsoleRegion {region!r} for {name!r}")
        images.append(
            {
                "name": name,
                "region": REGIONS[region],
                "md5": md5.lower(),
                "priority": int(priority),
                "fast_boot_patch": patch.lower(),
            }
        )
    if not images:
        raise ValueError("bios.cpp holds no image table — did the format change?")
    return images


def build(checkout: Path, revision: str) -> tuple[dict[str, object], int]:
    source = (checkout / BIOS_CPP).read_text(encoding="utf-8")
    header = (checkout / BIOS_H).read_text(encoding="utf-8")
    images = parse_images(source)
    hashes = [str(image["md5"]) for image in images]
    duplicates = sorted({md5 for md5 in hashes if hashes.count(md5) > 1})
    if duplicates:
        raise ValueError(f"the table has repeated hashes: {duplicates}")
    table: dict[str, object] = {
        "_meta": {
            "generated_from": f"stenzek/duckstation {BIOS_CPP.as_posix()} and {BIOS_H.as_posix()}",
            "revision": revision,
            "generated_at": datetime.now(timezone.utc).date().isoformat(),
            "images": len(images),
        },
        "sizes": parse_sizes(header),
        "openbios": parse_openbios(source),
        "images": images,
    }
    return table, len(images)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="local duckstation checkout to read")
    parser.add_argument("--revision", required=True, help="the revision the checkout is at")
    parser.add_argument("-o", "--output", default=str(DEFAULT_OUTPUT), help="where to write")
    args = parser.parse_args(argv)
    try:
        checkout = resolve_source(args.source)
        output = resolve_output(args.output)
        table, count = build(checkout, args.revision)
    except ValueError as error:
        parser.error(str(error))
    output.write_text(json.dumps(table, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{output}: {count} images at {args.revision}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
