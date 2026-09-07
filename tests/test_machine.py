"""Tests for atlas.machine — the seam, fixture semantics, the real prober, and parity.

The parity class executes the same cases against a FixtureMachine and a real
filesystem tree materialized in tmp_path — the fixture is only trustworthy as a
whole-machine model if both agree on every operation outcome.
"""

from __future__ import annotations

import contextlib
import glob as glob_module
import hashlib
import os
import random
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import atlas.lha
import atlas.machine
from atlas.machine import (
    ARCHIVE_MISSING,
    ARCHIVE_NOT_ARCHIVE,
    ARCHIVE_OK,
    ARCHIVE_UNREADABLE,
    DIGEST_ALGORITHMS,
    GLOB_COMPLETE,
    GLOB_INCOMPLETE,
    SYMLINK_HOPS,
    WHDLOAD_AMBIGUOUS,
    WHDLOAD_MISSING,
    WHDLOAD_NO_SLAVE,
    WHDLOAD_NOT_ARCHIVE,
    WHDLOAD_OK,
    WHDLOAD_SLAVE_UNREADABLE,
    ArchiveListResult,
    GlobResult,
    CoreInfo,
    FixtureMachine,
    ReadResult,
    RealMachine,
    WhdloadSlaveResult,
)


def _matches(machine: FixtureMachine | RealMachine, pattern: str) -> list[str]:
    """The names a pattern selected, from a walk that read everything it needed.

    The status is asserted here instead of in every caller: a test about which
    names a pattern selects has to fail loudly when the walk could not read
    something, rather than quietly comparing against a shorter list — that is
    the confusion the outcome exists to end.
    """
    result = machine.glob(pattern)
    assert result.status == GLOB_COMPLETE, result
    return list(result.matches)


def _assert_same_answers(fixture: FixtureMachine, real: RealMachine, path: str) -> None:
    """Every seam operation answers the same on both machines, for one path."""
    assert fixture.path_kind(path) == real.path_kind(path), path
    assert fixture.read_text(path) == real.read_text(path), path
    assert fixture.file_size(path) == real.file_size(path), path
    assert fixture.readlink(path) == real.readlink(path), path
    for algorithm in DIGEST_ALGORITHMS:
        assert fixture.file_digest(path, algorithm) == real.file_digest(path, algorithm), path


@contextlib.contextmanager
def _mode(path, bits: int):
    """Hold *path* at *bits* for the block, then give it back.

    A directory left at mode 000 makes the tmp_path teardown fail, and the
    failure lands on whichever test runs next.
    """
    path.chmod(bits)
    try:
        yield path
    finally:
        path.chmod(0o700)


def _real_link_chain(tmp_path, length: int) -> str:
    """A chain of *length* real symlinks ending on a file — the head's path."""
    base = tmp_path / f"n{length}"
    base.mkdir()
    (base / f"l{length}").write_text("end")
    for i in reversed(range(length)):
        os.symlink(base / f"l{i + 1}", base / f"l{i}")
    return str(base / "l0")


class TestReadResult:
    def test_ok_requires_text(self):
        with pytest.raises(ValueError):
            ReadResult("ok")

    def test_non_ok_forbids_text(self):
        with pytest.raises(ValueError):
            ReadResult("missing", "text")


class TestFixtureFiles:
    def test_read_text_ok_and_missing(self):
        m = FixtureMachine({"/a/b.txt": "hello"})
        assert m.read_text("/a/b.txt") == ReadResult("ok", "hello")
        assert m.read_text("/a/missing.txt") == ReadResult("missing")

    def test_unreadable_and_invalid_text_files(self):
        m = FixtureMachine({"/a/secret.cfg": {"status": "unreadable"}, "/a/blob.bin": {"status": "invalid-text"}})
        assert m.read_text("/a/secret.cfg") == ReadResult("unreadable")
        assert m.read_text("/a/blob.bin") == ReadResult("invalid-text")
        assert m.path_kind("/a/secret.cfg") == "file"

    def test_an_unreadable_file_may_state_the_size_its_stat_answered(self):
        # The chmod-000 case: the stat succeeds, the bytes do not. Without the
        # size a fixture would answer None for a value the machine states.
        m = FixtureMachine({"/bios/locked.bin": {"status": "unreadable", "size": 4096}})
        assert m.path_kind("/bios/locked.bin") == "file"
        assert m.read_text("/bios/locked.bin") == ReadResult("unreadable")
        assert m.file_size("/bios/locked.bin") == 4096
        assert m.file_digest("/bios/locked.bin", "md5") is None

    @pytest.mark.parametrize("algorithm", ["md5", "sha1"])
    def test_an_unreadable_file_may_not_state_a_digest(self, algorithm):
        # Its bytes are exactly what cannot be read, so a real one answers
        # None — a declared digest would assert a verdict off an unread file.
        with pytest.raises(ValueError, match="unreadable file states no digest"):
            FixtureMachine({"/bios/locked.bin": {"status": "unreadable", algorithm: "abc"}})

    def test_unknown_file_status_is_rejected(self):
        with pytest.raises(ValueError):
            FixtureMachine({"/a/f.txt": {"status": "sideways"}})

    def test_object_spec_without_status_or_identity_is_rejected(self):
        with pytest.raises(ValueError):
            FixtureMachine({"/a/f.bin": {"note": "nothing usable"}})

    def test_path_kind_file_directory_missing(self):
        m = FixtureMachine({"/a/b/c.txt": ""})
        assert m.path_kind("/a/b/c.txt") == "file"
        assert m.path_kind("/a/b") == "directory"
        assert m.path_kind("/a") == "directory"
        assert m.path_kind("/a/x") == "missing"

    def test_explicit_empty_directory(self):
        m = FixtureMachine({}, dirs=["/saves/empty"])
        assert m.path_kind("/saves/empty") == "directory"
        assert m.path_kind("/saves") == "directory"
        assert _matches(m, "/saves/empty/*") == []

    def test_reading_a_directory_is_unreadable(self):
        m = FixtureMachine({"/a/b/c.txt": ""})
        assert m.read_text("/a/b") == ReadResult("unreadable")

    def test_inaccessible_path(self):
        m = FixtureMachine({}, inaccessible=["/locked/dir"])
        assert m.path_kind("/locked/dir") == "inaccessible"
        assert m.read_text("/locked/dir") == ReadResult("unreadable")

    def test_glob_is_sorted_and_deterministic(self):
        m = FixtureMachine({"/s/b.srm": "", "/s/a.srm": "", "/s/a.rtc": ""})
        assert _matches(m, "/s/a.*") == ["/s/a.rtc", "/s/a.srm"]

    def test_glob_star_does_not_cross_separators(self):
        m = FixtureMachine({"/s/a.srm": "", "/s/deep/a.srm": ""})
        assert _matches(m, "/s/a.*") == ["/s/a.srm"]
        assert _matches(m, "/s/*") == ["/s/a.srm", "/s/deep"]

    def test_glob_wildcard_skips_hidden_names(self):
        m = FixtureMachine({"/s/a.srm": "", "/s/.hidden": ""})
        assert _matches(m, "/s/*") == ["/s/a.srm"]
        assert _matches(m, "/s/.*") == ["/s/.hidden"]

    def test_glob_escaped_metacharacters_match_literally(self):
        m = FixtureMachine({"/s/Game [USA].srm": "", "/s/Game U.srm": ""})
        pattern = glob_module.escape("/s/Game [USA]") + ".*"
        assert _matches(m, pattern) == ["/s/Game [USA].srm"]


class TestFixtureIdentity:
    """Size and digests — computed from string content, declared for blobs."""

    def test_string_content_is_measured_and_hashed(self):
        m = FixtureMachine({"/a/f.txt": "hello"})
        assert m.file_size("/a/f.txt") == 5
        assert m.file_digest("/a/f.txt", "md5") == hashlib.md5(b"hello").hexdigest()
        assert m.file_digest("/a/f.txt", "sha1") == hashlib.sha1(b"hello").hexdigest()

    def test_blob_declares_its_identity_and_is_not_text(self):
        m = FixtureMachine({"/bios/scph5501.bin": {"md5": "abc", "sha1": "def", "size": 524288}})
        assert m.path_kind("/bios/scph5501.bin") == "file"
        assert m.read_text("/bios/scph5501.bin") == ReadResult("invalid-text")
        assert m.file_size("/bios/scph5501.bin") == 524288
        assert m.file_digest("/bios/scph5501.bin", "md5") == "abc"
        assert m.file_digest("/bios/scph5501.bin", "sha1") == "def"

    def test_blob_may_declare_only_what_it_knows(self):
        m = FixtureMachine({"/bios/x.bin": {"size": 12}})
        assert m.file_size("/bios/x.bin") == 12
        assert m.file_digest("/bios/x.bin", "md5") is None

    def test_missing_unreadable_and_directories_answer_none(self):
        m = FixtureMachine({"/a/secret": {"status": "unreadable"}, "/a/b/c.txt": "x"})
        for path in ("/a/gone.bin", "/a/secret", "/a/b"):
            assert m.file_size(path) is None, path
            assert m.file_digest(path, "md5") is None, path

    def test_unknown_algorithm_is_none_not_an_error(self):
        m = FixtureMachine({"/a/f.txt": "hello"})
        assert m.file_digest("/a/f.txt", "sha256") is None


class TestGlobOutcome:
    """"Nothing there" and "could not look" stop being the same empty list.

    The distinction is the point of the type: a save directory on a card that
    stopped answering globs to nothing, and so does an empty one, and a caller
    that cannot tell them apart will report a save as gone.
    """

    def test_the_invariant_holds_both_ways(self):
        with pytest.raises(ValueError):
            GlobResult(GLOB_COMPLETE, (), ("/s",))
        with pytest.raises(ValueError):
            GlobResult(GLOB_INCOMPLETE, ("/s/a.srm",))

    def test_an_empty_directory_is_complete(self):
        m = FixtureMachine({}, dirs=["/s"])
        assert m.glob("/s/*") == GlobResult(GLOB_COMPLETE)

    @pytest.mark.parametrize("pattern", ["/gone/*", "/s/f.srm/*", "/s/dead/*"])
    def test_a_truthful_negative_is_complete(self, pattern):
        # Not there, not a directory, and a dead link are answers, not
        # failures — the walk read everything it needed to say so.
        m = FixtureMachine({"/s/f.srm": "x"}, symlinks={"/s/dead": "/nowhere"})
        assert m.glob(pattern) == GlobResult(GLOB_COMPLETE)

    def test_a_directory_that_cannot_be_listed_is_named(self):
        m = FixtureMachine({}, unlistable=["/s"])
        assert m.glob("/s/*") == GlobResult(GLOB_INCOMPLETE, (), ("/s",))

    def test_a_path_cannot_be_in_both_unreadable_lists(self):
        """The two answer opposite things about one question — does the stat succeed?

        The machine refuses it for the same reason the validator does, and the
        reason the file grammar refuses a digest on an unreadable file: the
        tempting reading is that both together spell mode 000, and resolving it
        by precedence would be a documented way to describe a machine nobody
        wrote down.
        """
        with pytest.raises(ValueError, match="both 'inaccessible' and 'unlistable'"):
            FixtureMachine({}, inaccessible=["/saves"], unlistable=["/saves"])

    def test_the_two_unreadable_lists_are_fine_apart(self):
        machine = FixtureMachine({}, dirs=["/mnt"], inaccessible=["/mnt/card"], unlistable=["/saves"])
        assert machine.path_kind("/mnt/card") == "inaccessible"
        assert machine.path_kind("/saves") == "directory"

    def test_an_inaccessible_directory_is_named(self):
        m = FixtureMachine({}, dirs=["/mnt"], inaccessible=["/mnt/card"])
        assert m.glob("/mnt/card/*") == GlobResult(GLOB_INCOMPLETE, (), ("/mnt/card",))

    def test_a_link_cycle_is_named(self):
        m = FixtureMachine({}, symlinks={"/a": "/b", "/b": "/a"})
        assert m.glob("/a/*") == GlobResult(GLOB_INCOMPLETE, (), ("/a",))

    def test_a_partly_readable_walk_keeps_what_it_found(self):
        """The answer is not all-or-nothing, because the machine's is not.

        One pattern can need several directories. Dropping the matches because
        one of them failed would throw away a true answer; dropping the failure
        because there were matches would state a partial list as the whole.
        """
        m = FixtureMachine(
            {"/s/open/a.srm": "x", "/s/shut/b.srm": "x"},
            unlistable=["/s/shut"],
        )
        assert m.glob("/s/*/*.srm") == GlobResult(
            GLOB_INCOMPLETE, ("/s/open/a.srm",), ("/s/shut",)
        )

    def test_a_relative_pattern_matches_nothing(self):
        # The working directory is not a fact about the machine being read.
        assert FixtureMachine({"/s/a.srm": "x"}).glob("s/*.srm") == GlobResult(GLOB_COMPLETE)


class TestFixturePathSpelling:
    """One path per file in the store, every spelling of it from the machine.

    A fixture is keyed by literal strings while a filesystem answers for
    whatever spelling reaches it, so ``.``, ``..``, repeated separators and a
    trailing slash have to resolve here too — a trailing one reaches production
    straight from a cfg value. The kernel's answers were observed in a scratch
    directory and :class:`TestFixtureRealParity` runs the same spellings against
    a real tree; these tests localize a break to the fixture.
    """

    def _machine(self) -> FixtureMachine:
        # /link points *into* the tree, so '..' through it lands somewhere a
        # lexical reading never would.
        return FixtureMachine({"/a/f.txt": "hello"}, dirs=["/a/sub"], symlinks={"/link": "/a/sub"})

    @pytest.mark.parametrize(
        "spelling, kind",
        [
            ("/a/f.txt", "file"),
            ("/a//f.txt", "file"),
            ("/a/./f.txt", "file"),
            ("/a/sub/../f.txt", "file"),
            ("/link/../f.txt", "file"),
            # ENOTDIR: the walk may only step through directories. Observed as
            # *missing*, because os.stat raises NotADirectoryError.
            ("/a/f.txt/", "missing"),
            ("/a/f.txt/.", "missing"),
            ("/a/f.txt/../f.txt", "missing"),
            ("/a/gone/../f.txt", "missing"),
            ("/a", "directory"),
            ("/a/", "directory"),
            ("/a///", "directory"),
            ("/a/.", "directory"),
            ("/a/sub/..", "directory"),
            ("/link/", "directory"),
            # The root: no ancestor walk ever reaches it, so nothing declares
            # it, and a spelling that climbs far enough lands there.
            ("/", "directory"),
            ("/.", "directory"),
            ("/..", "directory"),
            ("/a/../..", "directory"),
        ],
    )
    def test_a_spelling_answers_what_the_kernel_answers(self, spelling, kind):
        assert self._machine().path_kind(spelling) == kind

    def test_a_relative_path_names_nothing_a_fixture_can_answer_for(self):
        """A real machine resolves it against the working directory of the process.

        That is not a fact about the machine being described, so a fixture has
        nowhere to start — and a cfg does reach here: a relative
        ``system_directory`` is checked for being a directory before it is
        refused for not being an absolute root.
        """
        assert self._machine().path_kind("a/f.txt") == "missing"

    def test_dotdot_is_resolution_and_not_lexical_normalization(self):
        """The two readings disagree exactly where a symlink sits in front of ``..``.

        ``normpath`` collapses the spelling and drops ``/link``; the kernel
        resolves ``/link`` first and climbs from where it landed. The machine
        opens the file the kernel names, so that is the one the fixture answers
        — and a fixture that normalized would hand over the other file, which
        also exists here.
        """
        machine = FixtureMachine(
            {"/a/f.txt": "hello", "/f.txt": "the file a lexical reading would reach"},
            dirs=["/a/sub"],
            symlinks={"/link": "/a/sub"},
        )
        assert machine.read_text("/link/../f.txt") == ReadResult("ok", "hello")
        assert os.path.normpath("/link/../f.txt") == "/f.txt"

    @pytest.mark.parametrize("spelling", ["/link/", "/link/.", "/link/.."])
    def test_a_last_component_the_kernel_follows_is_not_a_link(self, spelling):
        # Observed on both machines: a link to a directory answers its target
        # spelled bare, and None the moment the spelling forces it to be
        # followed rather than named.
        assert self._machine().readlink(spelling) is None

    @pytest.mark.parametrize("spelling", ["/link", "/a/../link"])
    def test_readlink_names_the_link_that_is_the_last_component(self, spelling):
        assert self._machine().readlink(spelling) == "/a/sub"

    def test_a_pattern_ending_in_a_slash_matches_directories_only(self):
        # Observed: '<dir>/*/' answers the subdirectory alone, spelled with the
        # slash, and a pattern naming a regular file that way answers nothing.
        machine = FixtureMachine({"/s/f.srm": ""}, dirs=["/s/sub"])
        assert _matches(machine, "/s/*/") == ["/s/sub/"]
        assert _matches(machine, "/s/*") == ["/s/f.srm", "/s/sub"]
        assert _matches(machine, "/s/f.srm/") == []

    def test_a_match_keeps_the_spelling_the_pattern_reached_it_through(self):
        machine = FixtureMachine({"/s/f.srm": ""}, dirs=["/s/sub"])
        assert _matches(machine, "/s/./*.srm") == ["/s/./f.srm"]
        assert _matches(machine, "/s/sub/../*.srm") == ["/s/sub/../f.srm"]


class TestFixtureSymlinks:
    def test_read_through_link(self):
        m = FixtureMachine({"/real/f.txt": "x"}, symlinks={"/link": "/real"})
        assert m.read_text("/link/f.txt") == ReadResult("ok", "x")
        assert m.path_kind("/link/f.txt") == "file"
        assert m.path_kind("/link") == "directory"

    def test_direct_file_link(self):
        m = FixtureMachine({"/real/f.txt": "x"}, symlinks={"/alias.txt": "/real/f.txt"})
        assert m.read_text("/alias.txt") == ReadResult("ok", "x")

    def test_dead_link_is_visible_but_missing(self):
        # The applewin case: readlink shows the link, path_kind says missing.
        m = FixtureMachine({}, symlinks={"/cores": "/app/cores"})
        assert m.readlink("/cores") == "/app/cores"
        assert m.path_kind("/cores") == "missing"
        assert m.read_text("/cores/x.so") == ReadResult("missing")

    def test_readlink_on_regular_path_is_none(self):
        m = FixtureMachine({"/a.txt": ""})
        assert m.readlink("/a.txt") is None

    def test_readlink_through_linked_parent(self):
        m = FixtureMachine(
            {"/real/target/f.txt": "x"},
            symlinks={"/via": "/real", "/real/inner": "/real/target"},
        )
        assert m.readlink("/via/inner") == "/real/target"

    def test_relative_link_target(self):
        m = FixtureMachine({"/data/real/f.txt": "x"}, symlinks={"/data/link": "real"})
        assert m.read_text("/data/link/f.txt") == ReadResult("ok", "x")

    def test_a_link_cycle_answers_inaccessible_not_missing(self, tmp_path):
        """ELOOP has to be representable in a fixture, and answer as the kernel does.

        A cycle is not an absent file: the real machine's ``os.stat`` raises
        ``OSError(ELOOP)``, which the seam reports as *inaccessible*. A fixture
        that returned the half-resolved path instead would answer ``missing``,
        and every vector built on it would assert the safe-looking wrong thing.
        """
        fixture = FixtureMachine({}, symlinks={"/a": "/b", "/b": "/a"})
        os.symlink(tmp_path / "b", tmp_path / "a")
        os.symlink(tmp_path / "a", tmp_path / "b")
        real = RealMachine()
        for machine, base in ((fixture, "/a"), (real, str(tmp_path / "a"))):
            path = f"{base}/f.txt"
            assert machine.path_kind(path) == "inaccessible", machine
            assert machine.read_text(path).status == "unreadable", machine
            assert machine.file_size(path) is None, machine
            assert machine.file_digest(path, "md5") is None, machine

    @pytest.mark.parametrize(
        "length, resolves",
        [(SYMLINK_HOPS - 1, True), (SYMLINK_HOPS, True), (SYMLINK_HOPS + 1, False)],
    )
    def test_the_hop_limit_matches_the_kernel_on_both_machines(self, tmp_path, length, resolves):
        """A chain the kernel follows must resolve in a fixture, and vice versa.

        The boundary itself is the case, and it is why this runs at exactly
        ``SYMLINK_HOPS``: chains of 38, 39 and 40 links built in a scratch
        directory all stat and open fine, and 41 answers ELOOP — so *this many*
        hops resolve. A fixture that gave up one hop early answered
        *inaccessible* for a file the machine hands over, and every resolver had
        to agree with the kernel in a window one hop wide, which is exactly
        where a test that probes 39 and 41 does not look.
        """
        from atlas.firmware import resolve_links

        fixture = FixtureMachine(
            {f"/c/l{length}": "end"},
            symlinks={f"/c/l{i}": f"/c/l{i + 1}" for i in range(length)},
        )
        head = _real_link_chain(tmp_path, length)
        real = RealMachine()
        assert (real.path_kind(head) != "inaccessible") is resolves
        assert (fixture.path_kind("/c/l0") != "inaccessible") is resolves
        assert (resolve_links(real, head) is not None) is resolves
        assert (resolve_links(fixture, "/c/l0") is not None) is resolves
        assert fixture.read_text("/c/l0") == real.read_text(head)

    def test_glob_through_link_keeps_link_spelling(self):
        # A real filesystem's glob returns the pattern-side spelling, not the target.
        m = FixtureMachine({"/data/real-saves/Tetris.srm": "s"}, symlinks={"/links/saves": "/data/real-saves"})
        assert _matches(m, "/links/saves/Tetris.*") == ["/links/saves/Tetris.srm"]

    def test_glob_lists_dead_links(self):
        m = FixtureMachine({}, symlinks={"/s/dead.srm": "/gone/away.srm"})
        assert _matches(m, "/s/*.srm") == ["/s/dead.srm"]


_FUZZ_NAMES = ["a", "ab", "b.txt", "a.srm", ".hidden", "Game [USA].srm", "c-d", "x"]
_FUZZ_DIRS = ["a", "sub", "deep", ".dotdir"]
_FUZZ_SEGMENTS = ["*", "a*", "*.txt", "?", "[ab]*", "a", "sub", ".", "..", ".*", "deep", "linkdir", "*.srm"]


def _fuzz_tree(root, rng):
    """One random tree, built on disk and described as fixture data.

    Nested a level below *root*'s parent so a pattern climbing with ``..``
    still lands inside what the fixture was told about — otherwise the two
    machines are describing different trees and the comparison proves nothing.
    """
    files: dict[str, str] = {}
    dirs = [str(root.parent), str(root)]
    links: dict[str, str] = {}
    made = [""]
    for name in _FUZZ_DIRS:
        if rng.random() < 0.8:
            rel = f"{rng.choice(made)}/{name}".lstrip("/")
            (root / rel).mkdir(parents=True, exist_ok=True)
            made.append(rel)
            dirs.append(str(root / rel))
    for name in _FUZZ_NAMES:
        if rng.random() < 0.85:
            rel = f"{rng.choice(made)}/{name}".lstrip("/")
            if (root / rel).is_dir():
                continue
            (root / rel).write_text(name)
            files[str(root / rel)] = name
    if len(made) > 1:
        target = rng.choice(made[1:])
        os.symlink(root / target, root / "linkdir")
        links[str(root / "linkdir")] = str(root / target)
    os.symlink(root / "nowhere", root / "deadlink")
    links[str(root / "deadlink")] = str(root / "nowhere")
    return FixtureMachine(files, dirs=dirs, symlinks=links)


def _fuzz_patterns(root, rng) -> list[str]:
    """Random patterns over one tree, capped at three segments deep.

    The cap is load-bearing, not a budget. ``..`` is one of the segments and
    *root* sits two levels below ``tmp_path``, so with three segments a
    wildcard can follow at most two climbs — landing on ``tmp_path``, whose
    contents the fixture was told about. A fourth segment allows three climbs
    before a wildcard, which reaches pytest's shared basetemp: measured there,
    the real machine lists every other test's directory and the fixture lists
    one, and the comparison would flake on whatever ran alongside it.
    """
    out = []
    for depth in (1, 2, 3):
        for _ in range(12):
            parts = [rng.choice(_FUZZ_SEGMENTS) for _ in range(depth)]
            separator = rng.choice(["/", "/", "/", "//"])
            trailing = rng.choice(["", "", "", "/"])
            out.append(f"{root}/{separator.join(parts)}{trailing}")
    return [*out, str(root), f"{root}/", f"{root}//", f"{root}/*", f"{root}/*/", f"{root}/*/*"]


class TestGlobAgainstTheStandardLibrary:
    """The stdlib defines these semantics, so on a healthy tree both machines match it.

    ``RealMachine`` no longer calls ``glob.glob``: it cannot, because the
    stdlib swallows exactly the errors the outcome exists to report
    (``glob.py:173`` returns on any ``OSError`` from ``scandir``). Hand-rolling
    the walk means the semantics — including which separator survives in a
    match's spelling — are now atlas's to get right, and this is what says it
    did. Where something *does* fail, the two must part company, which is the
    second test class below.
    """

    @pytest.mark.parametrize("seed", range(12))
    def test_both_machines_answer_what_the_stdlib_answers(self, tmp_path, seed):
        rng = random.Random(seed)
        root = tmp_path / "only" / "root"
        root.mkdir(parents=True)
        fixture = _fuzz_tree(root, rng)
        real = RealMachine()
        for pattern in _fuzz_patterns(root, rng):
            expected = sorted(glob_module.glob(pattern))
            assert _matches(real, pattern) == expected, pattern
            assert _matches(fixture, pattern) == expected, pattern


class TestGlobStatesWhatTheStandardLibraryHides:
    """On a tree with something unreadable in it, the stdlib's answer is a lie by omission.

    Every case here was observed on a real tree first: a mode-000 directory
    globs to ``[]``, a link cycle globs to ``[]``, and so does a directory that
    genuinely holds nothing — three states, one answer, and the resolver has to
    tell them apart to avoid reporting a save as gone when the card is simply
    unreadable.
    """

    def _tree(self, tmp_path):
        (tmp_path / "open").mkdir()
        (tmp_path / "open" / "a.srm").write_text("s")
        (tmp_path / "shut").mkdir()
        (tmp_path / "shut" / "b.srm").write_text("s")
        (tmp_path / "empty").mkdir()
        os.symlink(tmp_path / "loop2", tmp_path / "loop1")
        os.symlink(tmp_path / "loop1", tmp_path / "loop2")
        return RealMachine()

    def test_an_unreadable_directory_is_not_an_empty_one(self, tmp_path):
        real = self._tree(tmp_path)
        with _mode(tmp_path / "shut", 0):
            shut = real.glob(f"{tmp_path}/shut/*")
            stdlib = glob_module.glob(f"{tmp_path}/shut/*")
        assert stdlib == []
        assert shut == GlobResult(GLOB_INCOMPLETE, (), (f"{tmp_path}/shut",))
        assert real.glob(f"{tmp_path}/empty/*") == GlobResult(GLOB_COMPLETE)

    def test_a_link_cycle_is_not_an_empty_one(self, tmp_path):
        real = self._tree(tmp_path)
        assert glob_module.glob(f"{tmp_path}/loop1/*") == []
        assert real.glob(f"{tmp_path}/loop1/*") == GlobResult(
            GLOB_INCOMPLETE, (), (f"{tmp_path}/loop1",)
        )

    def test_a_partly_readable_walk_states_both_halves(self, tmp_path):
        real = self._tree(tmp_path)
        with _mode(tmp_path / "shut", 0):
            answer = real.glob(f"{tmp_path}/*/*.srm")
            stdlib = glob_module.glob(f"{tmp_path}/*/*.srm")
        assert stdlib == [f"{tmp_path}/open/a.srm"]
        assert answer.matches == (f"{tmp_path}/open/a.srm",)
        # The unreadable directory, and both halves of the cycle: a wildcard in
        # the middle of a pattern has to decide whether each name is a
        # directory it may descend, and a link that loops cannot be decided.
        assert answer.unreadable == (
            f"{tmp_path}/loop1",
            f"{tmp_path}/loop2",
            f"{tmp_path}/shut",
        )


class TestFixtureCores:
    def test_query_core_returns_info(self):
        m = FixtureMachine({}, cores={"/cores/mgba_libretro.so": {"library_name": "mGBA"}})
        info = m.query_core("/cores/mgba_libretro.so")
        assert info == CoreInfo(library_name="mGBA", library_version=None, valid_extensions=None)

    def test_absent_options_are_unknown_not_empty(self):
        m = FixtureMachine({}, cores={"/cores/mgba_libretro.so": {"library_name": "mGBA"}})
        info = m.query_core("/cores/mgba_libretro.so")
        assert info is not None
        assert info.options is None

    def test_registered_options_are_captured(self):
        m = FixtureMachine(
            {},
            cores={
                "/cores/flycast_libretro.so": {
                    "library_name": "Flycast",
                    "options": {
                        "reicast_per_content_vmus": {
                            "default": "disabled",
                            "values": ["disabled", "VMU A1"],
                        }
                    },
                }
            },
        )
        info = m.query_core("/cores/flycast_libretro.so")
        assert info is not None
        assert info.options is not None
        option = info.options["reicast_per_content_vmus"]
        assert option.default == "disabled"
        assert option.values == ("disabled", "VMU A1")

    def test_empty_options_map_means_registered_nothing(self):
        # {} is evidence ("registered, and none are there"), unlike absence.
        m = FixtureMachine({}, cores={"/cores/x_libretro.so": {"library_name": "X", "options": {}}})
        info = m.query_core("/cores/x_libretro.so")
        assert info is not None
        assert info.options == {}

    def test_unloadable_core_is_none(self):
        m = FixtureMachine({}, cores={"/cores/applewin_libretro.so": None})
        assert m.query_core("/cores/applewin_libretro.so") is None

    def test_missing_core_is_none(self):
        m = FixtureMachine({})
        assert m.query_core("/cores/nope.so") is None

    def test_core_path_is_a_file(self):
        m = FixtureMachine({}, cores={"/cores/mgba_libretro.so": {"library_name": "mGBA"}})
        assert m.path_kind("/cores/mgba_libretro.so") == "file"
        assert m.read_text("/cores/mgba_libretro.so") == ReadResult("invalid-text")

    def test_query_core_through_symlinked_dir(self):
        m = FixtureMachine(
            {},
            symlinks={"/config/cores": "/deploy/cores"},
            cores={"/deploy/cores/mgba_libretro.so": {"library_name": "mGBA"}},
        )
        info = m.query_core("/config/cores/mgba_libretro.so")
        assert info is not None
        assert info.library_name == "mGBA"


class TestRealMachine:
    def test_read_text_statuses(self, tmp_path):
        (tmp_path / "ok.txt").write_text("hello")
        (tmp_path / "blob.bin").write_bytes(b"\xff\xfe\x00broken")
        m = RealMachine()
        assert m.read_text(str(tmp_path / "ok.txt")) == ReadResult("ok", "hello")
        assert m.read_text(str(tmp_path / "missing.txt")) == ReadResult("missing")
        assert m.read_text(str(tmp_path / "blob.bin")) == ReadResult("invalid-text")
        assert m.read_text(str(tmp_path)) == ReadResult("unreadable")

    def test_read_text_permission_denied(self, tmp_path):
        locked = tmp_path / "locked.txt"
        locked.write_text("secret")
        locked.chmod(0)
        try:
            assert RealMachine().read_text(str(locked)) == ReadResult("unreadable")
        finally:
            locked.chmod(0o600)

    def test_path_kind(self, tmp_path):
        (tmp_path / "f.txt").write_text("x")
        (tmp_path / "d").mkdir()
        m = RealMachine()
        assert m.path_kind(str(tmp_path / "f.txt")) == "file"
        assert m.path_kind(str(tmp_path / "d")) == "directory"
        assert m.path_kind(str(tmp_path / "nope")) == "missing"

    def test_readlink_and_kind_on_dead_link(self, tmp_path):
        link = tmp_path / "dead"
        os.symlink("/nonexistent/target", link)
        m = RealMachine()
        assert m.readlink(str(link)) == "/nonexistent/target"
        assert m.path_kind(str(link)) == "missing"

    def test_readlink_on_regular_file_is_none(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("x")
        m = RealMachine()
        assert m.readlink(str(f)) is None
        assert m.path_kind(str(f)) == "file"

    def test_file_size_and_digest(self, tmp_path):
        (tmp_path / "f.bin").write_bytes(b"\x00\x01\x02")
        (tmp_path / "d").mkdir()
        m = RealMachine()
        assert m.file_size(str(tmp_path / "f.bin")) == 3
        assert m.file_digest(str(tmp_path / "f.bin"), "md5") == hashlib.md5(b"\x00\x01\x02").hexdigest()
        for path in (str(tmp_path / "gone.bin"), str(tmp_path / "d")):
            assert m.file_size(path) is None, path
            assert m.file_digest(path, "md5") is None, path

    def test_file_digest_rejects_unlisted_algorithm(self, tmp_path):
        (tmp_path / "f.bin").write_bytes(b"x")
        assert RealMachine().file_digest(str(tmp_path / "f.bin"), "sha256") is None

    def test_file_digest_never_blocks_on_a_path_that_is_not_a_regular_file(self, tmp_path):
        """The seam promises regular files only, and a hang is not an answer.

        Opening a FIFO with no writer blocks forever, and this runs inside a
        library entry point that hashes whatever a config points at — one such
        node at a declared firmware path would take the whole answer with it.
        The guard is checked before the open, so both the size and the digest
        come back as "cannot tell". A regression does not fail this test, it
        hangs it — which is the failure mode being guarded against.
        """
        fifo = tmp_path / "scph5501.bin"
        os.mkfifo(fifo)
        m = RealMachine()
        assert m.file_digest(str(fifo), "md5") is None
        assert m.file_size(str(fifo)) is None
        # A character device is the same class of trap.
        assert m.file_digest("/dev/zero", "md5") is None

    def test_query_core_on_non_library_is_none(self, tmp_path):
        not_a_core = tmp_path / "fake.so"
        not_a_core.write_text("not an ELF")
        m = RealMachine()
        assert m.query_core(str(not_a_core)) is None

    def test_query_core_on_missing_path_is_none(self):
        m = RealMachine()
        assert m.query_core("/nonexistent/core.so") is None


def _fake_core(tmp_path):
    """A path that passes the stat query_core does — the probe itself is stubbed."""
    so = tmp_path / "mgba_libretro.so"
    so.write_bytes(b"\x7fELF")
    return str(so)


def _stub_probe(monkeypatch, *, stdout=b"", returncode=0, raises=None):
    """Answer the probe spawn without running it; returns the captured calls."""
    calls = []

    def fake_run(argv, **kwargs):
        calls.append({"argv": argv, **kwargs})
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(argv, returncode, stdout, b"")

    monkeypatch.setattr(
        atlas.machine,
        "subprocess",
        SimpleNamespace(run=fake_run, TimeoutExpired=subprocess.TimeoutExpired),
    )
    return calls


class TestCoreProbeAnswer:
    """What the probe printed is the answer — however the process ended.

    The subprocess exists because cores crash inside ``retro_set_environment``,
    and the phase-1 line carrying ``library_name`` is printed *before* that risk
    is taken. A crash, a hang or a traceback afterwards must not discard a read
    that already succeeded: the caller would see *unknown* for a value the
    machine had already answered.
    """

    BASE = b'{"library_name": "mGBA", "library_version": "0.10.5", "valid_extensions": "gb|gba"}\n'
    ENRICHED = (
        b'{"library_name": "mGBA", "library_version": "0.10.5", "valid_extensions": "gb|gba", '
        b'"options": {"mgba_gb_model": {"default": "Autodetect", "values": ["Autodetect", "Game Boy"]}}}\n'
    )
    MGBA = CoreInfo(library_name="mGBA", library_version="0.10.5", valid_extensions="gb|gba")

    def test_clean_exit_yields_the_base_answer(self, tmp_path, monkeypatch):
        _stub_probe(monkeypatch, stdout=self.BASE)
        assert RealMachine().query_core(_fake_core(tmp_path)) == self.MGBA

    def test_crash_after_the_base_line_keeps_it(self, tmp_path, monkeypatch):
        # -11 is a SIGSEGV in retro_set_environment: precisely the crash the
        # subprocess isolates, taken after the base line was already delivered.
        _stub_probe(monkeypatch, stdout=self.BASE, returncode=-11)
        assert RealMachine().query_core(_fake_core(tmp_path)) == self.MGBA

    def test_crash_after_the_options_line_keeps_the_options(self, tmp_path, monkeypatch):
        _stub_probe(monkeypatch, stdout=self.BASE + self.ENRICHED, returncode=1)
        info = RealMachine().query_core(_fake_core(tmp_path))
        assert info is not None
        assert info.options is not None
        assert info.options["mgba_gb_model"].default == "Autodetect"

    def test_trailing_garbage_does_not_displace_the_base_line(self, tmp_path, monkeypatch):
        _stub_probe(monkeypatch, stdout=self.BASE + b"Segmentation fault (core dumped)\n", returncode=-11)
        assert RealMachine().query_core(_fake_core(tmp_path)) == self.MGBA

    def test_timeout_keeps_what_was_printed_before_the_hang(self, tmp_path, monkeypatch):
        expired = subprocess.TimeoutExpired(cmd=["probe"], timeout=15, output=self.BASE)
        _stub_probe(monkeypatch, raises=expired)
        assert RealMachine().query_core(_fake_core(tmp_path)) == self.MGBA

    def test_timeout_before_anything_was_printed_is_unknown(self, tmp_path, monkeypatch):
        _stub_probe(monkeypatch, raises=subprocess.TimeoutExpired(cmd=["probe"], timeout=15))
        assert RealMachine().query_core(_fake_core(tmp_path)) is None

    def test_probe_that_cannot_be_spawned_is_unknown(self, tmp_path, monkeypatch):
        _stub_probe(monkeypatch, raises=OSError("cannot spawn"))
        assert RealMachine().query_core(_fake_core(tmp_path)) is None

    def test_output_without_a_json_line_is_unknown(self, tmp_path, monkeypatch):
        _stub_probe(monkeypatch, stdout=b"cannot load core: libGL.so.1: cannot open shared object file\n")
        assert RealMachine().query_core(_fake_core(tmp_path)) is None

    def test_nothing_printed_at_all_is_unknown(self, tmp_path, monkeypatch):
        _stub_probe(monkeypatch, stdout=b"", returncode=1)
        assert RealMachine().query_core(_fake_core(tmp_path)) is None

    def test_line_without_a_library_name_is_unknown(self, tmp_path, monkeypatch):
        _stub_probe(monkeypatch, stdout=b'{"library_version": "0.10.5"}\n')
        assert RealMachine().query_core(_fake_core(tmp_path)) is None


class TestCoreProbeEnvironment:
    """The probe child must reach the atlas that spawned it.

    Vendoring is a directory copy the host puts on ``sys.path`` at runtime —
    nothing on the child's default path leads back to it. A child that cannot
    import ``atlas._core_probe`` answers *unknown* for every core, for a reason
    that has nothing to do with the cores.
    """

    def test_child_is_pointed_at_this_package(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PYTHONPATH", raising=False)
        monkeypatch.setenv("ATLAS_UNRELATED_VARIABLE", "kept")
        calls = _stub_probe(monkeypatch, stdout=b"")
        RealMachine().query_core(_fake_core(tmp_path))
        env = calls[0]["env"]
        first = env["PYTHONPATH"].split(os.pathsep)[0]
        assert os.path.isfile(os.path.join(first, "atlas", "_core_probe.py"))
        assert env["ATLAS_UNRELATED_VARIABLE"] == "kept"

    def test_inherited_pythonpath_is_kept_behind_it(self, tmp_path, monkeypatch):
        inherited = os.pathsep.join(("/host/py_modules", "/host/extra"))
        monkeypatch.setenv("PYTHONPATH", inherited)
        calls = _stub_probe(monkeypatch, stdout=b"")
        RealMachine().query_core(_fake_core(tmp_path))
        entries = calls[0]["env"]["PYTHONPATH"].split(os.pathsep)
        assert os.path.isfile(os.path.join(entries[0], "atlas", "_core_probe.py"))
        assert os.pathsep.join(entries[1:]) == inherited

    def test_a_package_with_no_file_behind_it_changes_nothing(self, tmp_path, monkeypatch):
        """A frozen build states no location, so the child inherits the environment.

        Nothing can be pointed at when there is no path to point at — and a
        guessed one would send the child to somebody else's atlas.
        """
        monkeypatch.delattr(atlas.machine, "__file__")
        calls = _stub_probe(monkeypatch, stdout=b"")
        RealMachine().query_core(_fake_core(tmp_path))
        # env=None is how subprocess spells "inherit the parent's environment".
        assert calls[0]["env"] is None

    def test_a_vendored_copy_probes_with_its_own_module(self, tmp_path):
        """The load-bearing case: atlas copied into a host, reachable only via its sys.path.

        The copy's probe module is replaced by a marker, so the answer names
        which atlas the grandchild imported. Without the environment the child
        falls back to whatever ``atlas`` its interpreter happens to find — here,
        none at all in a real vendored deployment.
        """
        vendored = tmp_path / "py_modules"
        shutil.copytree(
            Path(__file__).resolve().parents[1] / "atlas",
            vendored / "atlas",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        (vendored / "atlas" / "_core_probe.py").write_text(
            'import json\n\nprint(json.dumps({"library_name": "VENDORED"}), flush=True)\n'
        )
        fake_core = tmp_path / "not_a_core.so"
        fake_core.write_bytes(b"not an ELF")
        program = (
            f"import sys; sys.path.insert(0, {str(vendored)!r})\n"
            "from atlas.machine import RealMachine\n"
            f"print(RealMachine().query_core({str(fake_core)!r}))\n"
        )
        host_env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        proc = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            cwd="/",
            env=host_env,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert "VENDORED" in proc.stdout, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"


class TestFixtureRealParity:
    """The same machine described twice — fixture data vs a real tmp_path tree.

    Every operation the resolver uses must produce identical outcomes on both,
    otherwise vector proofs would not transfer to reality.
    """

    FILES = {
        "cfg/retroarch.cfg": 'savefile_directory = "~/saves"\n',
        "saves/Game.srm": "s",
        "saves/Game.rtc": "r",
        "saves/Game [USA].srm": "u",
        "saves/deep/Game.srm": "d",
        "saves/.hidden": "h",
    }
    DIRS = ["saves/empty"]
    SYMLINKS = {"links/saves": "saves", "links/dead": "gone-away"}

    def _real(self, tmp_path):
        for rel, content in self.FILES.items():
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        for rel in self.DIRS:
            (tmp_path / rel).mkdir(parents=True, exist_ok=True)
        for rel, target in self.SYMLINKS.items():
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(tmp_path / target, path)
        return RealMachine()

    def _fixture(self, tmp_path):
        base = str(tmp_path)
        return FixtureMachine(
            {f"{base}/{rel}": content for rel, content in self.FILES.items()},
            dirs=[f"{base}/{rel}" for rel in self.DIRS],
            symlinks={f"{base}/{rel}": f"{base}/{target}" for rel, target in self.SYMLINKS.items()},
        )

    PROBE_PATHS = [
        "cfg/retroarch.cfg",
        "cfg",
        "saves/empty",
        "saves/missing.srm",
        "links/saves",
        "links/saves/Game.srm",
        "links/dead",
        "links/dead/below",
    ]
    # The same files under every spelling a config, a link target or a caller
    # can hand over — a fixture is keyed by one string per file, a filesystem
    # is not, and a trailing slash arrives from a cfg value in production.
    SPELLINGS = [
        "saves//Game.srm",
        "saves/./Game.srm",
        "saves/deep/../Game.srm",
        "saves/Game.srm/",
        "saves/Game.srm/.",
        "saves/Game.srm/../Game.rtc",
        "saves/gone/../Game.srm",
        "saves/",
        "saves///",
        "saves/.",
        "saves/deep/..",
        "links/saves/",
        "links/saves/../saves/Game.srm",
        "links/dead/",
        "links/dead/../saves",
    ]

    def test_operations_agree(self, tmp_path):
        real = self._real(tmp_path)
        fixture = self._fixture(tmp_path)
        for rel in self.PROBE_PATHS:
            _assert_same_answers(fixture, real, f"{tmp_path}/{rel}")

    def test_path_spellings_agree(self, tmp_path):
        real = self._real(tmp_path)
        fixture = self._fixture(tmp_path)
        for rel in self.SPELLINGS:
            _assert_same_answers(fixture, real, f"{tmp_path}/{rel}")

    def test_the_root_agrees(self, tmp_path):
        """The one directory nothing declares, and every long enough climb reaches.

        A fixture learns its directories from the paths it was given, and that
        walk stops at ``/`` — so the root was the one place both machines were
        certain about and only one of them could say so.
        """
        real = self._real(tmp_path)
        fixture = self._fixture(tmp_path)
        for spelling in ("/", "/.", "/..", "/../..", f"{tmp_path}/../.."):
            _assert_same_answers(fixture, real, spelling)

    def test_globs_agree(self, tmp_path):
        real = self._real(tmp_path)
        fixture = self._fixture(tmp_path)
        base = str(tmp_path)
        patterns = [
            f"{base}/saves/Game.*",
            f"{base}/saves/*",
            f"{base}/links/saves/Game.*",
            f"{base}/saves/empty/*",
            glob_module.escape(f"{base}/saves/Game [USA]") + ".*",
            f"{base}/saves/missing*",
            f"{base}/saves/.*",
            f"{base}/saves/./*.srm",
            f"{base}/saves/deep/../*.srm",
            f"{base}/links/saves/../saves/*.srm",
            f"{base}/saves/*/",
            f"{base}/*/",
            f"{base}/saves/Game.srm/",
            "/",
        ]
        for pattern in patterns:
            assert fixture.glob(pattern) == real.glob(pattern), pattern

    def test_a_file_whose_bytes_cannot_be_read_agrees(self, tmp_path):
        """A chmod-000 file: the stat succeeds and the read does not.

        Observed: kind ``file``, read ``unreadable``, the real size, and no
        digest — the size comes from the stat and the digest from bytes nobody
        can get at. ``{"status": "unreadable"}`` alone answers *no size*, which
        is a different machine (a FIFO, a device node), and a firmware vector
        written that way would take the unknown branch where the real machine
        settles the file on its size alone.
        """
        locked = tmp_path / "locked.bin"
        locked.write_bytes(b"\x00" * 4096)
        locked.chmod(0)
        fixture = FixtureMachine({str(locked): {"status": "unreadable", "size": 4096}})
        try:
            _assert_same_answers(fixture, RealMachine(), str(locked))
        finally:
            locked.chmod(0o600)

    def test_inaccessible_paths_agree(self, tmp_path):
        """A chmod-000 directory is two states, not one.

        Observed: the directory itself still stats — it is a directory that
        cannot be listed, read ``unreadable`` — while every path below it fails
        the stat outright and is *inaccessible*. A fixture states the paths
        below explicitly, which is what ``inaccessible`` is for.
        """
        locked = tmp_path / "locked"
        (locked / "inside").mkdir(parents=True)
        (locked / "inside" / "x.txt").write_text("x")
        locked.chmod(0)
        base = str(tmp_path)
        below = [f"{base}/locked/inside", f"{base}/locked/inside/x.txt"]
        fixture = FixtureMachine({}, dirs=[f"{base}/locked"], inaccessible=below)
        try:
            real = RealMachine()
            for path in (f"{base}/locked", *below):
                _assert_same_answers(fixture, real, path)
        finally:
            locked.chmod(0o700)

    def test_an_inaccessible_subtree_agrees_down_to_the_leaves(self, tmp_path):
        """One declaration, every consequence — the card that stopped answering.

        A path whose ``stat`` fails is what ``inaccessible`` means, and below a
        mode-000 parent that is true of the whole subtree: each descendant
        answers *inaccessible*, and a glob that needed to list any of them says
        so instead of returning an empty list. Naming every descendant in the
        fixture instead would mean listing what an unreadable card contains in
        order to say it cannot be read.
        """
        (tmp_path / "mount" / "card" / "saves" / "deep").mkdir(parents=True)
        (tmp_path / "mount" / "card" / "saves" / "Game.srm").write_text("s")
        base = str(tmp_path)
        fixture = FixtureMachine(
            {}, dirs=[f"{base}/mount"], inaccessible=[f"{base}/mount/card"]
        )
        with _mode(tmp_path / "mount", 0):
            real = RealMachine()
            for rel in ("mount/card", "mount/card/saves", "mount/card/saves/Game.srm"):
                _assert_same_answers(fixture, real, f"{base}/{rel}")
            for rel in ("mount/card/*", "mount/card/saves/*", "mount/card/saves/*.srm"):
                assert fixture.glob(f"{base}/{rel}") == real.glob(f"{base}/{rel}"), rel

    def test_a_directory_that_is_there_and_cannot_be_listed_agrees(self, tmp_path):
        """The other unreadable state, and the reason it needs its own list.

        A mode-111 directory was observed to be a directory whose ``stat``
        succeeds, whose listing fails, and inside which a name atlas already
        knows still answers — so a wildcard finds nothing there while the
        literal path finds the file. A resolver only reaches this state by
        passing an "is it a directory?" check first, which an inaccessible path
        fails, so the two cannot be one declaration.
        """
        (tmp_path / "saves").mkdir()
        (tmp_path / "saves" / "Game.srm").write_text("s")
        base = str(tmp_path)
        fixture = FixtureMachine({f"{base}/saves/Game.srm": "s"}, unlistable=[f"{base}/saves"])
        with _mode(tmp_path / "saves", 0o111):
            real = RealMachine()
            _assert_same_answers(fixture, real, f"{base}/saves")
            _assert_same_answers(fixture, real, f"{base}/saves/Game.srm")
            for rel in ("saves/*", "saves/*.srm", "saves/Game.srm"):
                assert fixture.glob(f"{base}/{rel}") == real.glob(f"{base}/{rel}"), rel

    def test_one_file_described_two_ways_answers_the_same(self, tmp_path):
        """Two ways to write one sized non-text file must not be two states.

        A blob states its identity and reads as ``invalid-text``; the same file
        may say so outright and carry the size. This machine accepts both,
        because a hand-written unit fixture should not have to pick — but a
        spelling the fixture accepts and answers *differently* would be a
        second state hiding behind one file, so both are held to the machine's
        own answer. (``scripts/validate_vectors.py`` is deliberately stricter
        and admits only the blob: the conformance corpus keeps one canonical
        spelling per state, so this is not an invitation to vector authors.)
        """
        content = b"\xff\xfe\x00binary"
        blob = tmp_path / "firmware.bin"
        blob.write_bytes(content)
        identity: dict[str, str | int] = {
            "size": len(content),
            "md5": hashlib.md5(content).hexdigest(),
            "sha1": hashlib.sha1(content).hexdigest(),
        }
        real = RealMachine()
        for spec in (identity, {"status": "invalid-text", **identity}):
            _assert_same_answers(FixtureMachine({str(blob): spec}), real, str(blob))


# ---------------------------------------------------------------------------
# The two archive reads.
# ---------------------------------------------------------------------------

SAMPLE_ARCHIVE = str(Path(__file__).parent / "data" / "whdload-sample.lha")
SAMPLE_MEMBERS = ("TestGame.info", "TestGame/TestGame.slave", "TestGame/ReadMe")
SAMPLE_SLAVE = "TestGame/TestGame.slave"


def _stored_lha(members: dict[str, bytes]) -> bytes:
    """A level-0 LhA archive, every member stored — enough to be listed and read."""
    out = b""
    for name, content in members.items():
        encoded = name.encode("latin-1")
        body = (
            b"-lh0-"
            + struct.pack("<II", len(content), len(content))
            + struct.pack("<HH", 0, 0)
            + b"\x20\x00"
            + bytes([len(encoded)])
            + encoded
            + struct.pack("<H", atlas.lha.crc16(content))
        )
        out += bytes([len(body), sum(body) & 0xFF]) + body + content
    return out + b"\x00"


def _slave_bytes() -> bytes:
    """The slave member the committed fixture archive carries, decompressed."""
    data = Path(SAMPLE_ARCHIVE).read_bytes()
    member = next(m for m in atlas.lha.members(data) if m.name == SAMPLE_SLAVE)
    return atlas.lha.extract(data, member)


def _zip_of(path: Path, members: dict[str, bytes]) -> str:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return str(path)


class TestArchiveReads:
    """RealMachine's two archive reads, and the fixture's model of them."""

    def test_an_lha_lists_its_members_in_the_archives_own_order(self):
        assert RealMachine().list_archive(SAMPLE_ARCHIVE) == ArchiveListResult(
            ARCHIVE_OK, SAMPLE_MEMBERS
        )

    def test_a_zip_lists_its_members_through_the_stdlib(self, tmp_path):
        path = _zip_of(tmp_path / "Game.zip", {"Disk1.adf": b"a", "notes/read.me": b"b"})

        assert RealMachine().list_archive(path) == ArchiveListResult(
            ARCHIVE_OK, ("Disk1.adf", "notes/read.me")
        )

    def test_an_archive_that_is_not_there_is_missing_rather_than_unreadable(self, tmp_path):
        assert RealMachine().list_archive(str(tmp_path / "gone.lha")).status == ARCHIVE_MISSING

    def test_a_directory_spelled_like_an_archive_is_still_a_directory(self, tmp_path):
        # The core tests path_is_directory before it looks at the suffix, so a
        # directory called Game.zip is a directory to it.
        (tmp_path / "Game.zip").mkdir()
        (tmp_path / "Game.zip" / "Disk1.adf").write_bytes(b"a")

        assert RealMachine().list_archive(str(tmp_path / "Game.zip")) == ArchiveListResult(
            ARCHIVE_OK, ("Disk1.adf",)
        )

    def test_a_container_that_is_not_there_is_missing_whatever_it_is_called(self, tmp_path):
        for name in ("gone.zip", "gone.txt", "gone"):
            assert RealMachine().list_archive(str(tmp_path / name)).status == ARCHIVE_MISSING
            assert FixtureMachine({}).list_archive(f"/roms/{name}").status == ARCHIVE_MISSING

    def test_a_file_whose_bytes_cannot_be_read_is_unreadable(self, tmp_path):
        path = tmp_path / "Game.zip"
        path.write_bytes(b"PK\x03\x04")

        with _mode(path, 0):
            assert RealMachine().list_archive(str(path)).status == ARCHIVE_UNREADABLE

    @pytest.mark.parametrize(
        ("name", "content"),
        [
            pytest.param("Game.txt", b"plain text", id="a-suffix-no-reader-claims"),
            pytest.param("Game.7z", b"7z\xbc\xaf'\x1c", id="a-format-atlas-reads-none-of"),
            pytest.param("Game.zip", b"not a zip at all", id="a-zip-that-is-not-one"),
            pytest.param("Game.lha", b"not an lha at all", id="an-lha-that-is-not-one"),
        ],
    )
    def test_what_is_no_archive_says_so_rather_than_failing_a_read(self, tmp_path, name, content):
        path = tmp_path / name
        path.write_bytes(content)

        assert RealMachine().list_archive(str(path)).status == ARCHIVE_NOT_ARCHIVE

    def test_the_slave_inside_an_lha_states_its_version_and_name(self):
        assert RealMachine().read_whdload_slave(SAMPLE_ARCHIVE) == WhdloadSlaveResult(
            WHDLOAD_OK, SAMPLE_SLAVE, 17, "Test Game", "script"
        )

    def test_the_slave_inside_a_zip_is_found_under_the_drawer_the_core_mounts(self, tmp_path):
        data = Path(SAMPLE_ARCHIVE).read_bytes()
        members = {member.name: atlas.lha.extract(data, member) for member in atlas.lha.members(data)}
        path = _zip_of(tmp_path / "Game.zip", members)

        assert RealMachine().read_whdload_slave(path) == WhdloadSlaveResult(
            WHDLOAD_OK, SAMPLE_SLAVE, 17, "Test Game", "script"
        )

    def test_a_whdload_archive_inside_a_zip_is_a_container_this_seam_does_not_open(self, tmp_path):
        path = _zip_of(tmp_path / "Game.zip", {"Game.lha": Path(SAMPLE_ARCHIVE).read_bytes()})

        assert RealMachine().read_whdload_slave(path).status == WHDLOAD_NO_SLAVE

    def test_an_archive_the_boot_script_selects_nothing_from_has_no_slave(self, tmp_path):
        path = _zip_of(tmp_path / "Game.zip", {"Disk1.adf": b"a", "Disk2.adf": b"b"})

        assert RealMachine().read_whdload_slave(path).status == WHDLOAD_NO_SLAVE

    def test_a_selected_member_whose_bytes_are_no_slave_says_so(self, tmp_path):
        path = tmp_path / "Game.lha"
        path.write_bytes(_stored_lha({"Game.slave": b"not an executable"}))

        assert RealMachine().read_whdload_slave(str(path)).status == WHDLOAD_SLAVE_UNREADABLE

    def test_a_member_compressed_by_a_method_this_reader_lacks_says_so(self, tmp_path):
        broken = bytearray(_stored_lha({"Game.slave": b"whatever"}))
        broken[2:7] = b"-lh7-"
        path = tmp_path / "Game.lha"
        path.write_bytes(bytes(broken))

        assert RealMachine().read_whdload_slave(str(path)).status == WHDLOAD_SLAVE_UNREADABLE

    def test_an_archive_that_is_not_there_carries_through_to_the_slave_read(self, tmp_path):
        assert RealMachine().read_whdload_slave(str(tmp_path / "gone.lha")).status == WHDLOAD_MISSING


class TestFixtureArchiveReads:
    """The fixture's model of the two reads: data in, the same words out."""

    def _machine(self, **extra):
        return FixtureMachine({"/roms/Game.lha": {"status": "invalid-text"}}, **extra)

    def test_a_declared_listing_is_what_the_walk_answers(self):
        machine = self._machine(archives={"/roms/Game.lha": list(SAMPLE_MEMBERS)})

        assert machine.list_archive("/roms/Game.lha") == ArchiveListResult(ARCHIVE_OK, SAMPLE_MEMBERS)

    @pytest.mark.parametrize("state", ["unreadable", "not-archive"])
    def test_a_declared_state_is_what_the_walk_answers(self, state):
        machine = self._machine(archives={"/roms/Game.lha": state})

        assert machine.list_archive("/roms/Game.lha").status == state

    def test_a_file_nobody_called_an_archive_is_not_one(self):
        assert self._machine().list_archive("/roms/Game.lha").status == ARCHIVE_NOT_ARCHIVE

    def test_a_path_that_is_not_there_is_missing(self):
        assert self._machine().list_archive("/roms/Other.lha").status == ARCHIVE_MISSING

    def test_a_declared_slave_answers_its_two_fields(self):
        machine = self._machine(
            archives={"/roms/Game.lha": list(SAMPLE_MEMBERS)},
            whdload_slaves={
                "/roms/Game.lha": {
                    "slave": SAMPLE_SLAVE, "version": 17, "name": "Test Game", "selected_by": "script"
                }
            },
        )

        assert machine.read_whdload_slave("/roms/Game.lha") == WhdloadSlaveResult(
            WHDLOAD_OK, SAMPLE_SLAVE, 17, "Test Game", "script"
        )

    def test_a_slave_older_than_ten_states_no_name(self):
        machine = self._machine(
            archives={"/roms/Game.lha": list(SAMPLE_MEMBERS)},
            whdload_slaves={
                "/roms/Game.lha": {
                    "slave": SAMPLE_SLAVE, "version": 8, "name": None, "selected_by": "only-slave"
                }
            },
        )

        assert machine.read_whdload_slave("/roms/Game.lha").name is None

    @pytest.mark.parametrize("state", ["no-slave", "slave-unreadable"])
    def test_a_declared_slave_state_is_what_the_read_answers(self, state):
        machine = self._machine(
            archives={"/roms/Game.lha": list(SAMPLE_MEMBERS)}, whdload_slaves={"/roms/Game.lha": state}
        )

        assert machine.read_whdload_slave("/roms/Game.lha").status == state

    def test_a_listed_archive_nobody_read_a_slave_out_of_has_none(self):
        machine = self._machine(archives={"/roms/Game.lha": list(SAMPLE_MEMBERS)})

        assert machine.read_whdload_slave("/roms/Game.lha").status == WHDLOAD_NO_SLAVE

    def test_the_containers_own_failure_carries_through_to_the_slave_read(self):
        machine = self._machine(archives={"/roms/Game.lha": "not-archive"})

        assert machine.read_whdload_slave("/roms/Game.lha").status == WHDLOAD_NOT_ARCHIVE

    @pytest.mark.parametrize(
        ("kwargs", "reason"),
        [
            pytest.param({"archives": {"/roms/Nope.lha": []}}, "no file is declared", id="no-such-file"),
            pytest.param({"archives": {"/roms/Game.lha": "gone"}}, "state must be one of", id="bad-state"),
            pytest.param(
                {"archives": {"/roms/Game.lha": ["/absolute"]}},
                "archive-internal path",
                id="absolute-member",
            ),
            pytest.param(
                {"archives": {"/roms/Game.lha": ["a/../b"]}},
                "archive-internal path",
                id="climbing-member",
            ),
            pytest.param(
                {"whdload_slaves": {"/roms/Game.lha": "no-slave"}},
                "neither an archive with a member list nor a directory",
                id="slave-without-archive",
            ),
            pytest.param(
                {
                    "archives": {"/roms/Game.lha": ["a.slave"]},
                    "whdload_slaves": {
                        "/roms/Game.lha": {
                            "slave": "b.slave", "version": 17, "name": "X", "selected_by": "script"
                        }
                    },
                },
                "not in this archive's listing",
                id="slave-outside-the-listing",
            ),
            pytest.param(
                {
                    "archives": {"/roms/Game.lha": ["a.slave"]},
                    "whdload_slaves": {
                        "/roms/Game.lha": {
                            "slave": "a.slave", "version": 8, "name": "X", "selected_by": "script"
                        }
                    },
                },
                "ws_name field at all",
                id="a-name-an-old-slave-cannot-have",
            ),
        ],
    )
    def test_a_fixture_that_describes_no_machine_fails_to_build(self, kwargs, reason):
        with pytest.raises(ValueError, match=reason):
            self._machine(**kwargs)

    def test_a_listing_beside_an_unreadable_file_is_refused(self):
        with pytest.raises(ValueError, match="states no member list"):
            FixtureMachine(
                {"/roms/Game.lha": {"status": "unreadable"}}, archives={"/roms/Game.lha": ["a"]}
            )

    def test_the_fixture_and_the_real_machine_answer_an_archive_alike(self):
        real = RealMachine().read_whdload_slave(SAMPLE_ARCHIVE)
        fixture = FixtureMachine(
            {SAMPLE_ARCHIVE: {"status": "invalid-text"}},
            archives={SAMPLE_ARCHIVE: list(SAMPLE_MEMBERS)},
            whdload_slaves={
                SAMPLE_ARCHIVE: {
                    "slave": SAMPLE_SLAVE, "version": 17, "name": "Test Game", "selected_by": "script"
                }
            },
        ).read_whdload_slave(SAMPLE_ARCHIVE)

        assert fixture == real


class TestTheRouteThatNamedTheSlave:
    """The two ways a member gets named, and the states that name none."""

    def test_the_only_slave_route_names_what_the_script_missed(self, tmp_path):
        # A public install archive's own shape, mounted whole because it is an
        # .lha: a drawer icon at the root and the slave inside under another
        # name, which the boot script's search resolves to nothing.
        path = tmp_path / "AlienBreed.lha"
        path.write_bytes(
            _stored_lha(
                {
                    "AlienBreedHD.info": b"icon",
                    "AlienBreedHD/AlienBreed.slave": _slave_bytes(),
                    "AlienBreedHD/ReadMe": b"notes",
                }
            )
        )

        answer = RealMachine().read_whdload_slave(str(path))

        assert (answer.status, answer.selected_by) == (WHDLOAD_OK, "only-slave")
        assert answer.slave == "AlienBreedHD/AlienBreed.slave"

    def test_a_listing_the_script_cannot_tell_apart_is_its_own_state(self, tmp_path):
        path = tmp_path / "Game.lha"
        path.write_bytes(_stored_lha({"One.slave": _slave_bytes(), "Two.slave": _slave_bytes()}))

        assert RealMachine().read_whdload_slave(str(path)).status == WHDLOAD_AMBIGUOUS

    def test_a_fixture_states_the_route_a_vector_asserts(self):
        machine = FixtureMachine(
            {"/roms/Game.lha": {"status": "invalid-text"}},
            archives={"/roms/Game.lha": ["Game.info", "Game/Other.slave"]},
            whdload_slaves={
                "/roms/Game.lha": {
                    "slave": "Game/Other.slave",
                    "version": 17,
                    "name": "Alien Breed",
                    "selected_by": "only-slave",
                }
            },
        )

        assert machine.read_whdload_slave("/roms/Game.lha").selected_by == "only-slave"

    def test_a_fixture_route_outside_the_two_is_refused(self):
        with pytest.raises(ValueError, match="selected_by must be one of"):
            FixtureMachine(
                {"/roms/Game.lha": {"status": "invalid-text"}},
                archives={"/roms/Game.lha": ["Game.slave"]},
                whdload_slaves={
                    "/roms/Game.lha": {
                        "slave": "Game.slave", "version": 17, "name": "X", "selected_by": "guessed"
                    }
                },
            )


def test_a_member_written_with_a_leading_dot_slash_is_the_name_it_extracts_to(tmp_path):
    """``./Disk1.adf`` lands on disk as ``Disk1.adf``, and the core's walk sees that.

    Left as written it would be passed over as a name starting with a dot —
    the one shape of member the walk deliberately skips.
    """
    path = _zip_of(tmp_path / "Game.zip", {"./Disk1.adf": b"a", "/Disk2.adf": b"b"})

    assert RealMachine().list_archive(path) == ArchiveListResult(
        ARCHIVE_OK, ("Disk1.adf", "Disk2.adf")
    )


class TestAMemberIsOpenedByTheNameItsContainerWrote:
    """The listing is normalised; the bytes still have to be asked for by the real name."""

    def test_a_slave_written_with_a_leading_dot_slash_is_still_read(self, tmp_path):
        path = _zip_of(tmp_path / "Game.zip", {"./Game.slave": _slave_bytes()})

        answer = RealMachine().read_whdload_slave(path)

        assert (answer.status, answer.slave) == (WHDLOAD_OK, "Game.slave")

    def test_a_slave_written_with_a_leading_slash_is_still_read(self, tmp_path):
        path = _zip_of(tmp_path / "Game.zip", {"/Game.slave": _slave_bytes()})

        assert RealMachine().read_whdload_slave(path).status == WHDLOAD_OK

    def test_a_custom_file_written_with_a_leading_dot_slash_is_still_read(self, tmp_path):
        path = _zip_of(
            tmp_path / "Game.zip",
            {"Game.slave": _slave_bytes(), "./custom": b"SavePath=DH1:Mine\n"},
        )

        assert RealMachine().read_whdload_slave(path).custom == "SavePath=DH1:Mine\n"

    def test_a_load_file_leaves_the_custom_file_unread(self, tmp_path):
        # The script executes the volume's own command and skips the block
        # that would have read `custom` along with everything else.
        path = _zip_of(
            tmp_path / "Game.zip",
            {"load": b"C:Run Game\n", "Game.slave": _slave_bytes(), "custom": b"SavePath=DH1:x\n"},
        )

        answer = RealMachine().read_whdload_slave(path)

        assert (answer.status, answer.custom) == (WHDLOAD_NO_SLAVE, None)
