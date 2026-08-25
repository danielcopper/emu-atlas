"""The texture and mods tables' byte tripwire (issue #105).

The rule cards' anchors already re-read every recorded save name in the
shipped binaries; these tables carried their evidence in provenance prose
only. Same idea, two lessons baked in: containment is **raw bytes**, never
``strings`` tokens — a tail-merged literal (``load/`` living only inside
``…-emu/load/``) is invisible to every token pass — and the **encoding
travels with the anchor**, because one shipped name (``hires_texture`` in
mupen64plus_next's GLideN64 half) exists only as UTF-32LE.

Skipped where the binaries are not deployed: the Flatpak is not a build
dependency and CI has no emulator installation.
"""

import json
from pathlib import Path

import pytest

from atlas.emulator_settings import DirectoryName, load_emulator_settings
from atlas.mods import load_mod_cards, load_standalone_mod_cards, recorded_mod_words
from atlas.standalone_savestates import (
    load_standalone_savestates,
    recorded_savestate_emulator_words,
)
from atlas.textures import (
    load_standalone_texture_packs,
    load_texture_packs,
    recorded_texture_core_words,
    recorded_texture_emulator_words,
)

DATA = Path(__file__).resolve().parent.parent / "atlas" / "data"
DEPLOYED_CORES = Path(
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components/"
    "retroarch/rd_extras/cores"
)
COMPONENTS = Path(
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
)
FLATPAK_ROOTS = (
    Path.home() / ".local" / "share" / "flatpak" / "app",
    Path("/var/lib/flatpak/app"),
)


def _flatpak_binary(app_id: str, path: str) -> Path:
    """Where a build living in an app of its own sits — user install before system.

    The order flatpak itself resolves in, so a user-installed app wins over a
    system one of the same id.
    """
    for root in FLATPAK_ROOTS:
        candidate = root / app_id / "current" / "active" / "files" / path
        if candidate.is_file():
            return candidate
    return FLATPAK_ROOTS[0] / app_id / "current" / "active" / "files" / path


def _directory_spellings():
    """Every directory name the settings table states, with the build that spells it.

    One row per (emulator, installation): the default the arrangement's own
    build uses, plus one for each app whose build spells it differently.
    ``siblings`` carries the other spellings' literals — a build carrying one
    of those is the rename this tripwire exists to catch.
    """
    for token, entry in load_emulator_settings().items():
        spellings: list[tuple[str | None, DirectoryName]] = [(None, entry.directory.default)]
        spellings.extend(entry.directory.installations.items())
        for app_id, stated in spellings:
            binary = (
                _flatpak_binary(stated.flatpak, stated.binary)
                if stated.flatpak is not None
                else COMPONENTS / stated.binary
            )
            # The encoding travels with the sibling too. Encoding it as bare
            # UTF-8 below would look for the wrong bytes of any name recorded
            # in another encoding, and a flip guard that cannot see the other
            # spelling passes for the wrong reason.
            siblings = tuple(
                (literal, encoding)
                for other_id, other in spellings
                if other_id != app_id
                for _, literal, encoding in other.literals
            )
            yield token, app_id, stated, binary, siblings


DIRECTORY_SPELLINGS = list(_directory_spellings())

TEXTURES_RAW = json.loads((DATA / "texture_packs.json").read_text(encoding="utf-8"))
MODS_RAW = json.loads((DATA / "mods.json").read_text(encoding="utf-8"))
SAVESTATES_RAW = json.loads((DATA / "standalone_savestates.json").read_text(encoding="utf-8"))

_WORDS = {
    ("textures", "cores"): recorded_texture_core_words,
    ("textures", "emulators"): recorded_texture_emulator_words,
    ("mods", "cores"): recorded_mod_words,
    ("mods", "emulators"): recorded_mod_words,
    ("savestates", "emulators"): recorded_savestate_emulator_words,
}


def _rows():
    for table_name, raw in (
        ("textures", TEXTURES_RAW),
        ("mods", MODS_RAW),
        ("savestates", SAVESTATES_RAW),
    ):
        for half in ("cores", "emulators"):
            for key, entry in raw.get(half, {}).items():
                yield table_name, half, key, entry


def _binary_of(half: str, key: str, entry: "dict[str, dict[str, str]]") -> Path:
    if half == "cores":
        return DEPLOYED_CORES / f"{key}_libretro.so"
    return COMPONENTS / entry["anchors"]["binary"]


LITERAL_ANCHORS = [
    (table, half, key, name, anchor["literal"], anchor.get("encoding", "utf-8"), _binary_of(half, key, entry))
    for table, half, key, entry in _rows()
    for name, anchor in entry.get("anchors", {}).get("names", {}).items()
    if "literal" in anchor
]

_blob_cache: dict[Path, bytes] = {}


def _blob(path: Path) -> bytes | None:
    if path not in _blob_cache:
        _blob_cache[path] = path.read_bytes() if path.is_file() else b""
    return _blob_cache[path] or None


class TestEveryRecordedNameIsAnchored:
    """The data half: shipped rows cover their whole vocabulary, opt-outs are curated."""

    def test_the_shipped_tables_load_with_their_anchors(self):
        # The loaders validate the blocks' shape and drop them; loading the
        # packaged data is the cheapest whole-shape check there is.
        assert load_texture_packs()
        assert load_standalone_texture_packs()
        assert load_mod_cards()
        assert load_standalone_mod_cards()
        assert load_standalone_savestates()

    @pytest.mark.parametrize(
        ("table", "half", "key", "entry"),
        list(_rows()),
        ids=[f"{t}:{h}:{k}" for t, h, k, _ in _rows()],
    )
    def test_every_recorded_name_has_an_anchor(self, table, half, key, entry):
        words = _WORDS[(table, half)](entry)
        anchored = set(entry.get("anchors", {}).get("names", {}))
        assert anchored == set(words), (
            f"{table} row {key!r} records {sorted(set(words) - anchored)} without an anchor "
            f"(or anchors {sorted(anchored - set(words))} it does not record) — every recorded "
            "name is pinned to the bytes it was read from, or opted out with a reason"
        )

    def test_the_opt_outs_are_exactly_the_curated_ones(self):
        unprotected = sorted(
            (table, half, key, name)
            for table, half, key, entry in _rows()
            for name, anchor in entry.get("anchors", {}).get("names", {}).items()
            if "unprotected" in anchor
        )
        assert unprotected == [
            # MAME never stores its config file's whole name: parse_one_ini
            # composes it at run time from get_configname() + ".ini"
            # (mameopts.cpp:125 at mame0287), so the literal is nowhere in the
            # shipped binary (verified raw) and its fragments are too generic
            # to pin.
            ("savestates", "emulators", "MAME", "mame.ini"),
        ], (
            "the set of recorded names no anchor watches has changed — every entry here is a "
            "name the byte tripwire cannot reach, so confirm the new one really cannot be "
            "pinned to a literal (whole or fragment, in any recorded encoding) before "
            "updating this list"
        )


class TestTheAnchorsAreBytesInTheShippedBinaries:
    """The tripwire itself: every literal, re-read raw from the binary it came from.

    Raw containment rather than a NUL-delimited run, deliberately: several of
    these literals exist only tail-merged into longer strings, which a
    delimited needle would miss and call a rename.
    """

    @pytest.mark.parametrize(
        ("table", "half", "key", "name", "literal", "encoding", "binary"),
        LITERAL_ANCHORS,
        ids=[f"{t}:{k}:{n}" for t, _, k, n, _, _, _ in LITERAL_ANCHORS],
    )
    def test_an_anchor_is_raw_bytes_in_its_binary(
        self, table, half, key, name, literal, encoding, binary
    ):
        data = _blob(binary)
        if data is None:
            pytest.skip(f"{binary} is not deployed")
        assert literal.encode(encoding) in data, (
            f"{table} {half} row {key!r} anchors {name!r} to {literal!r} ({encoding}) and the "
            f"shipped {binary.name} carries no such bytes — the emulator's vocabulary moved, so "
            "the row describes names it no longer spells; re-audit before trusting the placement"
        )

    def test_the_anchors_are_really_read_where_the_binaries_exist(self):
        # The all-skip guard: a run that checked nothing must not look clean.
        if not DEPLOYED_CORES.is_dir() and not COMPONENTS.is_dir():
            pytest.skip(f"nothing is deployed at {COMPONENTS}")
        checked = [entry for entry in LITERAL_ANCHORS if _blob(entry[6]) is not None]
        assert checked, (
            f"binaries are deployed below {COMPONENTS} and not one anchor was read from one — "
            "either no shipped row anchors anything any more, or the binaries moved and the "
            "tripwire is silently checking nothing"
        )


class TestTheAnchorBlockShape:
    """The loader refuses what the tests could otherwise silently skip."""

    def _texture_table(self, anchors) -> str:
        return json.dumps(
            {
                "schema": TEXTURES_RAW["schema"],
                "cores": {
                    "demo": {
                        "identifiers": {"library_name": ["Demo"]},
                        "textures": {
                            "root": "savefile_directory",
                            "subdir": "demo/textures",
                            "keying": None,
                            "replacement_option": None,
                        },
                        "anchors": anchors,
                        "provenance": {"source": "[V] a citation"},
                    }
                },
            }
        )

    def test_an_anchor_for_an_unrecorded_name_is_refused(self):
        table = self._texture_table({"names": {"elsewhere": {"literal": "elsewhere"}}})
        with pytest.raises(ValueError, match="does not record"):
            load_texture_packs(table)

    def test_a_core_row_stating_a_binary_is_refused(self):
        table = self._texture_table({"binary": "demo/bin/demo", "names": {}})
        with pytest.raises(ValueError, match="derived from its key"):
            load_texture_packs(table)

    def test_an_unknown_encoding_is_refused(self):
        table = self._texture_table({"names": {"demo": {"literal": "demo", "encoding": "utf-7"}}})
        with pytest.raises(ValueError, match="encoding"):
            load_texture_packs(table)

    def test_an_opt_out_with_an_encoding_is_refused(self):
        table = self._texture_table(
            {"names": {"demo": {"unprotected": "why", "encoding": "utf-8"}}}
        )
        with pytest.raises(ValueError, match="belongs to a literal"):
            load_texture_packs(table)

    def test_an_anchor_that_is_both_kinds_at_once_is_refused(self):
        table = self._texture_table(
            {"names": {"demo": {"literal": "demo", "unprotected": "and also not"}}}
        )
        with pytest.raises(ValueError, match="literal"):
            load_texture_packs(table)


# The directory rows whose literal is *not* a lone constant in the binary that
# spells it, so a rename would leave the other occurrences behind and this wire
# could not say a word. Every one of them is a literal that is also the
# program's own name, which a binary carries for a hundred unrelated reasons —
# log tags, window titles, its own paths. Listed rather than quietly tolerated,
# because "the tripwire covers the settings table" was read off the class below
# and was true of four rows out of twelve.
#
# Moving a row out of this list means its build started spelling the directory
# somewhere it does not spell itself, which is worth the read it takes to
# confirm. Moving one in means a name that was watched no longer is.
DIRECTORY_ANCHORS_THE_WIRE_CANNOT_WATCH = {
    ("XEMU", None),
    ("CEMU", None),
    ("AZAHAR", None),
    ("DUCKSTATION", None),
    ("PCSX2", None),
    ("MELONDS", None),
    ("RPCS3", None),
    ("VITA3K", None),
}


class TestTheStatedDirectoryIsTheOneTheBuildSpells:
    """The settings table's half: an emulator's own directory, re-read as bytes.

    The name is a compiled-in constant rather than anything on disk, so the
    binary that carries it is the only honest check. It matters most where the
    name belongs to the build rather than to the emulator: PrimeHack renamed
    its user directory and later renamed it back, so the revision RetroDECK
    ships and the one Flathub ships spell it differently (#246), and a name
    that outlived its build would send every path below it somewhere nothing
    writes to.

    **What it does not cover.** Containment only catches a rename where the
    literal was the *only* reason the binary carried those bytes. Four rows
    are like that, and each occurs exactly once in its build: Dolphin's
    ``.dolphin-emu/``, PrimeHack's ``.primehack/``, PPSSPP's ``.ppsspp/``, and
    ``.dolphin-emu/`` again for the PrimeHack build Flathub ships, which spells
    the directory the way the emulator it forked does. Eight rows are not, and
    they are the ones :data:`DIRECTORY_ANCHORS_THE_WIRE_CANNOT_WATCH` lists —
    ``azahar-emu`` occurs 20 times, ``duckstation`` 29, ``Cemu`` 110,
    ``PCSX2`` 135, ``melonDS`` 183, ``xemu`` 197, ``Vita3K`` 763 and ``rpcs3``
    1322, because the binary says its own name for a hundred unrelated
    reasons. For those the wire is a presence check and nothing more, and the
    constant says so out loud rather than letting this docstring imply
    otherwise.
    """

    @pytest.mark.parametrize(
        ("token", "app_id", "stated", "binary", "siblings"),
        DIRECTORY_SPELLINGS,
        ids=[f"{t}:{a or 'default'}" for t, a, _, _, _ in DIRECTORY_SPELLINGS],
    )
    def test_the_build_carries_the_name_the_table_states(
        self, token, app_id, stated, binary, siblings
    ):
        data = _blob(binary)
        if data is None:
            pytest.skip(f"{binary} is not deployed")
        for segment, literal, encoding in stated.literals:
            assert literal.encode(encoding) in data, (
                f"the settings table states {token}'s own directory as {stated.name!r} "
                f"({segment!r} anchored to {literal!r}) and the shipped {binary.name} carries no "
                "such bytes — the build renamed it, so every path below it now points at a "
                "directory nothing writes to; re-audit the name before trusting any answer"
            )

    @pytest.mark.parametrize(
        ("token", "app_id", "stated", "binary", "siblings"),
        [row for row in DIRECTORY_SPELLINGS if row[4]],
        ids=[f"{t}:{a or 'default'}" for t, a, _, _, s in DIRECTORY_SPELLINGS if s],
    )
    def test_a_build_does_not_carry_another_installations_name(
        self, token, app_id, stated, binary, siblings
    ):
        # The flip guard, and what makes a pair of names trustworthy: each
        # build carries exactly one of them. RetroDECK's build repository has
        # already moved to a PrimeHack revision that spells the directory the
        # other way; the day that ships, this is what says so.
        data = _blob(binary)
        if data is None:
            pytest.skip(f"{binary} is not deployed")
        for literal, encoding in siblings:
            assert literal.encode(encoding) not in data, (
                f"the settings table states {token}'s own directory as {stated.name!r} for "
                f"{app_id or 'the arrangement own build'}, and the shipped {binary.name} carries "
                f"{literal!r} — the other installation's spelling — so this build is no longer "
                "the one that name was read from"
            )

    def test_the_directory_anchors_are_really_read_somewhere(self):
        # The all-skip guard, as on the card anchors above.
        if not COMPONENTS.is_dir():
            pytest.skip(f"nothing is deployed at {COMPONENTS}")
        checked = [row for row in DIRECTORY_SPELLINGS if _blob(row[3]) is not None]
        assert checked, (
            f"binaries are deployed below {COMPONENTS} and not one directory name was read from "
            "one — either the binaries moved or the tripwire is silently checking nothing"
        )

    def test_every_segment_of_a_stated_directory_is_anchored(self):
        # The coverage guard the card tables have and this table did not: an
        # empty `names` block loads, and then the wire above iterates zero
        # times and passes. A row protected by nothing must not read as a row
        # that passed.
        for token, app_id, stated, _binary, _siblings in DIRECTORY_SPELLINGS:
            segments = {segment for segment in stated.name.split("/") if segment}
            anchored = set(stated.anchors["names"])
            assert anchored == segments, (
                f"the settings table states {token}'s directory ({app_id or 'default'}) as "
                f"{stated.name!r} and anchors {sorted(anchored)} — every segment is pinned to "
                "the bytes it was read from, or this wire watches a name nobody checked"
            )

    def test_the_rows_this_wire_cannot_watch_are_exactly_the_listed_ones(self):
        # Derived from the binaries, not asserted about them: a literal that
        # occurs once is the constant itself, so removing it is a rename this
        # wire sees. One that occurs many times is the program's own name, and
        # a rename would leave every other occurrence in place.
        if not COMPONENTS.is_dir():
            pytest.skip(f"nothing is deployed at {COMPONENTS}")
        unwatchable = set()
        read_any = False
        for token, app_id, stated, binary, _siblings in DIRECTORY_SPELLINGS:
            data = _blob(binary)
            if data is None:
                continue
            read_any = True
            for _segment, literal, encoding in stated.literals:
                if data.count(literal.encode(encoding)) > 1:
                    unwatchable.add((token, app_id))
        assert read_any, "no directory anchor was read, so this proves nothing"
        assert unwatchable == DIRECTORY_ANCHORS_THE_WIRE_CANNOT_WATCH, (
            "which directory names this tripwire can actually watch has changed — a row that "
            "left the list now spells its directory somewhere it does not spell its own name, "
            "and a row that joined it stopped being watched at all"
        )
