"""Tests for atlas.machine — the seam, fixture semantics, the real prober, and parity.

The parity class executes the same cases against a FixtureMachine and a real
filesystem tree materialized in tmp_path — the fixture is only trustworthy as a
whole-machine model if both agree on every operation outcome.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import atlas.machine
from atlas.machine import (
    DIGEST_ALGORITHMS,
    SYMLINK_HOPS,
    CoreInfo,
    FixtureMachine,
    ReadResult,
    RealMachine,
)


def _assert_same_answers(fixture: FixtureMachine, real: RealMachine, path: str) -> None:
    """Every seam operation answers the same on both machines, for one path."""
    assert fixture.path_kind(path) == real.path_kind(path), path
    assert fixture.read_text(path) == real.read_text(path), path
    assert fixture.file_size(path) == real.file_size(path), path
    assert fixture.readlink(path) == real.readlink(path), path
    for algorithm in DIGEST_ALGORITHMS:
        assert fixture.file_digest(path, algorithm) == real.file_digest(path, algorithm), path


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
        assert m.glob("/saves/empty/*") == []

    def test_reading_a_directory_is_unreadable(self):
        m = FixtureMachine({"/a/b/c.txt": ""})
        assert m.read_text("/a/b") == ReadResult("unreadable")

    def test_inaccessible_path(self):
        m = FixtureMachine({}, inaccessible=["/locked/dir"])
        assert m.path_kind("/locked/dir") == "inaccessible"
        assert m.read_text("/locked/dir") == ReadResult("unreadable")

    def test_glob_is_sorted_and_deterministic(self):
        m = FixtureMachine({"/s/b.srm": "", "/s/a.srm": "", "/s/a.rtc": ""})
        assert m.glob("/s/a.*") == ["/s/a.rtc", "/s/a.srm"]

    def test_glob_star_does_not_cross_separators(self):
        m = FixtureMachine({"/s/a.srm": "", "/s/deep/a.srm": ""})
        assert m.glob("/s/a.*") == ["/s/a.srm"]
        assert m.glob("/s/*") == ["/s/a.srm", "/s/deep"]

    def test_glob_wildcard_skips_hidden_names(self):
        m = FixtureMachine({"/s/a.srm": "", "/s/.hidden": ""})
        assert m.glob("/s/*") == ["/s/a.srm"]
        assert m.glob("/s/.*") == ["/s/.hidden"]

    def test_glob_escaped_metacharacters_match_literally(self):
        import glob as glob_module

        m = FixtureMachine({"/s/Game [USA].srm": "", "/s/Game U.srm": ""})
        pattern = glob_module.escape("/s/Game [USA]") + ".*"
        assert m.glob(pattern) == ["/s/Game [USA].srm"]


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
        assert machine.glob("/s/*/") == ["/s/sub/"]
        assert machine.glob("/s/*") == ["/s/f.srm", "/s/sub"]
        assert machine.glob("/s/f.srm/") == []

    def test_a_match_keeps_the_spelling_the_pattern_reached_it_through(self):
        machine = FixtureMachine({"/s/f.srm": ""}, dirs=["/s/sub"])
        assert machine.glob("/s/./*.srm") == ["/s/./f.srm"]
        assert machine.glob("/s/sub/../*.srm") == ["/s/sub/../f.srm"]


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
        assert m.glob("/links/saves/Tetris.*") == ["/links/saves/Tetris.srm"]

    def test_glob_lists_dead_links(self):
        m = FixtureMachine({}, symlinks={"/s/dead.srm": "/gone/away.srm"})
        assert m.glob("/s/*.srm") == ["/s/dead.srm"]


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
        import glob as glob_module

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
