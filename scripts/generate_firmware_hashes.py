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

Every entry also carries what **kind** of identity it is, because not every one
of them is a whole-file dump — see :data:`ARCHIVE_IDENTITIES`. That statement is
atlas's own, not upstream's: ``System.dat`` says nothing about it, so it is a
curated list here, versioned and dated, and never a guess from the file
extension at run time.

The generator has two modes. Both write through the same serializer, so a run of
either produces no formatting diff against the committed file:

- ``--database <checkout>`` rebuilds the whole table from a local git checkout
  of libretro-database. Nothing here touches the network; clone it first::

      git clone https://github.com/libretro/libretro-database ~/src/libretro-database

      python scripts/generate_firmware_hashes.py --database ~/src/libretro-database

- ``--restamp`` reads the committed table and rewrites only what this script
  states about it — the ``kind``/``archive_reason`` pair and the three ``_meta``
  fields this script owns (the schema version and the curated list's version and
  reviewed date). Identities are copied through untouched, ``generated_at``
  included: no upstream data was read, so a fresh date would be a claim about
  where the identities came from. It is the mode for a change to the curated list
  or the schema, which must not wait on a fresh upstream checkout and must not
  smuggle a data change in beside it.

With no ``-o`` the output defaults to ``atlas/data/firmware_hashes.json``
(resolved relative to the repo root, so the command works from any working
directory), which is also the file ``--restamp`` reads. A regeneration is a
deliberate, reviewable data diff — see ``atlas/data/README.md``.
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# The packaged data file this generator produces, resolved relative to the repo
# root (scripts/ sits at the root) so the default works from any cwd.
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "atlas" / "data" / "firmware_hashes.json"

SCHEMA_VERSION = "6.0.0"
GENERATED_FROM = "libretro-database dat/System.dat"

# The two kinds of identity, and the two reasons an archive is one. Spelled
# here as the generator's own copy of the vocabulary ``atlas/firmware.py``
# parses — scripts/ never imports the package it generates data for.
KIND_FILE = "file"
KIND_ARCHIVE = "archive"
REASON_ROMSET = "romset"
REASON_CORE_BUNDLED = "core-bundled"

# Which identities are not whole-file dumps, and why.
#
# Evidence level [D], derived — **not** [V]. ``System.dat`` states an md5 for
# every one of these and nothing about their shape, so no upstream source
# carries this distinction; it is stated here or nowhere. Each line's evidence
# is one of four, and which one is named in the line. Three of the 24 rest on
# something weaker than a shipped sentence, and none of the three hides it:
#
#   1. the declaring core's own ``firmwareN_desc``, where a shipped ``.info``
#      declares the file (``ecwolf.pk3``, ``freej2me-lr.jar``);
#   2. that core's ``notes`` / ``description``, where its ``.info`` declares no
#      firmware but names the file in prose (``Dinothawr.zip``,
#      ``prboom.wad``);
#   3. **sibling of a declared file from the same release** — no shipped
#      ``.info`` names the file, but the table carries it beside one that is
#      declared, under a name and a size that place it in the same release
#      (``freej2me.jar``, ``freej2me-sdl.jar``);
#   4. atlas's own inference, where no shipped ``.info`` mentions the file at
#      all and no declared sibling stands beside it (``scummvm.zip``). The
#      ScummVM core declares a set of successor files instead, and the reading
#      that the retired single pack was the same kind of thing rests on that
#      and on nothing upstream.
#
# Why it has to be stated at all: a whole-file hash over an archive pins one
# *packaging*, not the content. A ``romset`` archive is a MAME-style BIOS or
# device set, and its bytes follow the romset version and the merge mode it was
# built under (split / merged / non-merged), so two correct copies of the same
# BIOS can hash differently. A ``core-bundled`` archive is a data pack or a
# program archive released and versioned with the project that builds the core,
# so its bytes can change with a core release. In both cases a whole-file
# comparison that fails has established nothing — which is what
# ``atlas.firmware`` answers ``not-comparable`` for. "Bundled" is about
# versioning and not about shipping: three of the seven are program jars the
# user supplies, and one of those is a core's *required* firmware.
#
# Kept as a list rather than a rule about ``.zip``: the extension is a container
# format, not a statement about what the file is for, and nothing in atlas
# decides an identity's kind from it. A new archive name arriving upstream is
# caught by the extension *guard* in tests/test_firmware_hashes_data.py, whose
# whole job is to fail until this list has a reviewed line for it.
ARCHIVE_IDENTITIES_VERSION = "2"  # "1" was the list before the FreeJ2ME jars
ARCHIVE_IDENTITIES_REVIEWED = "2026-09-02"
# What version "2" stands for, as a number rather than as a promise: the sha256
# over every (name, reason) pair in sorted order — see :func:`fingerprint`. A
# test holds this against the list, so a name added or a reason changed fails
# until this digest is deliberately re-pinned — and re-pinning it is the moment
# to bump the version beside it. What the test sees is the digest, never the
# bump. The rationale strings are deliberately outside it: they are
# prose for a reviewer, they reach no consumer, and pinning them would turn
# every wording fix into a data bump.
ARCHIVE_IDENTITIES_FINGERPRINT = "8e76fb56e303d817997945d3129e3d5ada7889848d622d59737ac1899945af21"

ARCHIVE_IDENTITIES: dict[str, tuple[str, str]] = {
    # Flycast's Naomi/Atomiswave boards, every one of them a MAME set — the
    # core's own descriptions say "from MAME" in as many words
    # (flycast_libretro.info firmware1..7_desc; firmware0 is the dc_boot.bin
    # dump).
    "dc/airlbios.zip": (REASON_ROMSET, "Naomi Airline Pilots deluxe BIOS set from MAME"),
    "dc/awbios.zip": (REASON_ROMSET, "Atomiswave BIOS set from MAME"),
    "dc/f355bios.zip": (REASON_ROMSET, "Naomi Ferrari F355 Challenge twin/deluxe BIOS set from MAME"),
    "dc/f355dlx.zip": (REASON_ROMSET, "Naomi Ferrari F355 Challenge deluxe BIOS set from MAME"),
    "dc/hod2bios.zip": (REASON_ROMSET, "Naomi The House of the Dead 2 BIOS set from MAME"),
    "dc/naomi.zip": (REASON_ROMSET, "Naomi BIOS set from MAME"),
    "dc/naomi2.zip": (REASON_ROMSET, "Naomi 2 BIOS set from MAME"),
    # FinalBurn Neo's arcade BIOS and device sets: fbneo_libretro.info declares
    # 23, of which these ten are the ones System.dat carries — firmware0_desc
    # plus firmware2..10_desc, firmware1 being fbneo/neocdz.zip, which the table
    # does not have. A "device" set is a single internal ROM shipped in MAME's
    # set form, which makes it a romset like the BIOS ones. Note the spelling:
    # that .info writes every one with an fbneo/ prefix while the table keys are
    # bare, and the only shipped .info declaring a bare neogeo.zip is geolith's.
    "bubsys.zip": (REASON_ROMSET, "Bubble System BIOS set"),
    "cchip.zip": (REASON_ROMSET, "C-Chip internal ROM, as a MAME device set"),
    "decocass.zip": (REASON_ROMSET, "DECO Cassette System BIOS set"),
    "isgsm.zip": (REASON_ROMSET, "ISG Selection Master Type 2006 System BIOS set"),
    "midssio.zip": (REASON_ROMSET, "Midway SSIO sound board internal ROM, as a MAME device set"),
    "neogeo.zip": (REASON_ROMSET, "Neo Geo BIOS set — geolith calls it the Neo Geo MVS system ROM"),
    "nmk004.zip": (REASON_ROMSET, "NMK004 internal ROM, as a MAME device set"),
    "pgm.zip": (REASON_ROMSET, "PGM System BIOS set"),
    "skns.zip": (REASON_ROMSET, "Super Kaneko Nova System BIOS set"),
    "ym2608.zip": (REASON_ROMSET, "YM2608 internal ROM, as a MAME device set"),
    # Data packs and program archives released and versioned with the project
    # that builds the core. Note that "bundled" is about versioning, not about
    # shipping: the RetroDECK Flatpak carries ecwolf.pk3 and prboom.wad under
    # rd_extras/ and not one .jar, and freej2me-lr.jar is declared as REQUIRED
    # firmware — the mark of a file the user supplies.
    "Dinothawr.zip": (
        REASON_CORE_BUNDLED,
        "the Dinothawr core's game content package — its description calls the artwork and puzzle "
        "layouts a separate content package, its notes require the archive by name",
    ),
    "ecwolf.pk3": (REASON_CORE_BUNDLED, "the ECWolf core's system file (ecwolf_libretro.info firmware0_desc)"),
    # A .jar is a zip container. Only freej2me-lr.jar is declared anywhere; the
    # other two are here on route 3, and what places them in the same release is
    # the name stem and the sizes — 552039 / 552042 / 552043, within four bytes of each other.
    # Which front end each of the other two drives is read off its name and
    # nothing else, and the lines say so rather than asserting it.
    "freej2me-lr.jar": (
        REASON_CORE_BUNDLED,
        "a build of FreeJ2ME, declared and required by the core that runs it — freej2me_libretro.info "
        "gives firmware0_desc as the bare name and firmware0_opt = false, and the core's description "
        "('A port of FreeJ2ME to libretro') with the -lr stem is what reads it as the libretro build",
    ),
    "freej2me.jar": (
        REASON_CORE_BUNDLED,
        "a second build of the same FreeJ2ME release, by its name the plain one — no shipped .info "
        "declares it, and it is here because its stem and its size place it beside freej2me-lr.jar",
    ),
    "freej2me-sdl.jar": (
        REASON_CORE_BUNDLED,
        "a third build of the same FreeJ2ME release, by its name the SDL one — undeclared like the "
        "plain build, and placed here the same way",
    ),
    "prboom.wad": (
        REASON_CORE_BUNDLED,
        "the PrBoom core's own resource WAD — a lump archive its notes require by name",
    ),
    "scummvm.zip": (
        REASON_CORE_BUNDLED,
        "the ScummVM core's retired data pack of GUI themes and engine data — atlas's own inference, "
        "the one line here resting on no upstream sentence and no declared sibling: no shipped .info "
        "mentions this file, and the reading rests on the successor files the core declares "
        "individually under scummvm/theme/ (7 files, 4 of them archives) and scummvm/extra/ "
        "(32 files, the engine data)",
    ),
}

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


def resolve_input(raw: str) -> Path:
    """Resolve the table ``--restamp`` reads, which is the file it also writes.

    The mode exists to restate what this script knows about a table that is
    already committed, so it reads that table rather than a second copy of it:
    one file in, the same file out, and nothing to keep in sync. Raises
    ``ValueError`` like the resolvers above.
    """
    input_path = resolve_output(raw)
    if not input_path.is_file():
        raise ValueError(f"nothing to restamp — {input_path} does not exist")
    return input_path


def fingerprint() -> str:
    """The curated list's consumer-visible content, as one sha256.

    Exactly what a consumer can observe is folded in — the names and the reason
    each one carries, in sorted order — and nothing else. The rationale strings
    stay out on purpose: they explain the call to a reviewer and never leave
    this file, so pinning them would make a typo fix look like a data change.
    """
    pairs = "\n".join(f"{name}\t{reason}" for name, (reason, _what) in sorted(ARCHIVE_IDENTITIES.items()))
    return hashlib.sha256(pairs.encode("utf-8")).hexdigest()


def stamp(name: str) -> dict[str, str]:
    """What this script states about the identity called *name*.

    A name the curated list does not carry is a whole-file dump: that is the
    ordinary case, and the guard against it becoming a silent default is the
    extension test over the shipped table, not a rule here.
    """
    entry = ARCHIVE_IDENTITIES.get(name)
    if entry is None:
        return {"kind": KIND_FILE}
    reason, _what = entry
    return {"kind": KIND_ARCHIVE, "archive_reason": reason}


def stamp_all(hashes):
    """Every identity, in name order, with its kind stamped on.

    Refuses a curated line for a name the table does not carry: such a line
    describes nothing, and leaving it in would let the list drift out of reach
    of every check that reads the table.
    """
    stale = sorted(set(ARCHIVE_IDENTITIES) - set(hashes))
    if stale:
        raise SystemExit(
            f"Error: the curated archive list names {len(stale)} identities this table does not "
            f"carry: {', '.join(stale)}"
        )
    return {name: {**hashes[name], **stamp(name)} for name in sorted(hashes)}


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
            "archive_identities_version": ARCHIVE_IDENTITIES_VERSION,
            "archive_identities_reviewed": ARCHIVE_IDENTITIES_REVIEWED,
        },
        "files": stamp_all(hashes),
    }


def restamp_table(input_path):
    """Re-state the kinds over an existing table, identities untouched.

    ``generated_from`` and ``generated_at`` are carried through: no upstream
    data was read, so claiming a fresh generation date would be a lie about
    where the identities came from. The three fields this script owns — the
    schema version and the curated list's version and reviewed date — are
    rewritten from its own constants, which is the point of the mode. Each entry
    is rebuilt from exactly the three identity fields, so the output is shaped
    exactly like a generation's and a key the schema dropped does not survive a
    restamp.
    """
    print(f"Restamping {input_path}...", file=sys.stderr)
    try:
        table = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"Error: cannot read {input_path}: {e}") from None
    meta = table.get("_meta", {})
    hashes = {}
    for name, entry in table.get("files", {}).items():
        missing = [key for key in ("md5", "sha1", "size") if key not in entry]
        if missing:
            raise SystemExit(f"Error: {name} carries no {', '.join(missing)} — this is not an identity table")
        hashes[name] = {"md5": entry["md5"], "sha1": entry["sha1"], "size": entry["size"]}
    print(f"  Read {len(hashes)} firmware identities", file=sys.stderr)
    return {
        "_meta": {
            "generated_from": meta.get("generated_from", GENERATED_FROM),
            "version": SCHEMA_VERSION,
            "generated_at": meta.get("generated_at", ""),
            "archive_identities_version": ARCHIVE_IDENTITIES_VERSION,
            "archive_identities_reviewed": ARCHIVE_IDENTITIES_REVIEWED,
        },
        "files": stamp_all(hashes),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate the packaged firmware identity table from libretro-database.",
        epilog=(
            "Examples:\n"
            "  python scripts/generate_firmware_hashes.py \\\n"
            "    --database ~/src/libretro-database\n"
            "  python scripts/generate_firmware_hashes.py --restamp"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # A run either reads upstream or reads the committed table — never both,
    # and never neither: which identities the output carries is the one thing
    # the two modes disagree about, so it is decided by the command line.
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--database",
        help="Path to a libretro-database checkout — rebuild every identity from its System.dat",
    )
    source.add_argument(
        "--restamp",
        action="store_true",
        help="Rewrite kinds and the schema version over the existing table, identities untouched",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output JSON path (default: atlas/data/firmware_hashes.json)",
    )
    args = parser.parse_args()

    # Every path a run takes from its arguments is resolved and checked before
    # anything opens it, and only the resolved forms are used from here on.
    try:
        output_path = resolve_output(args.output)
        source_path = resolve_input(args.output) if args.restamp else resolve_database(args.database)
    except ValueError as exc:
        parser.error(str(exc))

    table = restamp_table(source_path) if args.restamp else build_table(source_path)

    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(table, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Wrote {output_path} ({len(table['files'])} firmware identities)", file=sys.stderr)


if __name__ == "__main__":
    main()
