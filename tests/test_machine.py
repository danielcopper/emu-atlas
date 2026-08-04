"""Tests for atlas.machine — the seam, fixture semantics, the real prober, and parity.

The parity class executes the same cases against a FixtureMachine and a real
filesystem tree materialized in tmp_path — the fixture is only trustworthy as a
whole-machine model if both agree on every operation outcome.
"""

from __future__ import annotations

import hashlib
import os

import pytest

from atlas.machine import CoreInfo, FixtureMachine, ReadResult, RealMachine


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

    def test_link_cycle_does_not_hang(self):
        m = FixtureMachine({}, symlinks={"/a": "/b", "/b": "/a"})
        assert m.read_text("/a/f.txt") == ReadResult("missing")
        assert m.path_kind("/a/f.txt") == "missing"

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
        assert info is not None and info.options is None

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
        assert info is not None and info.options is not None
        option = info.options["reicast_per_content_vmus"]
        assert option.default == "disabled"
        assert option.values == ("disabled", "VMU A1")

    def test_empty_options_map_means_registered_nothing(self):
        # {} is evidence ("registered, and none are there"), unlike absence.
        m = FixtureMachine({}, cores={"/cores/x_libretro.so": {"library_name": "X", "options": {}}})
        info = m.query_core("/cores/x_libretro.so")
        assert info is not None and info.options == {}

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
        assert info is not None and info.library_name == "mGBA"


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

    def test_operations_agree(self, tmp_path):
        import glob as glob_module

        real = self._real(tmp_path)
        fixture = self._fixture(tmp_path)
        base = str(tmp_path)

        probe_paths = [
            f"{base}/cfg/retroarch.cfg",
            f"{base}/cfg",
            f"{base}/saves/empty",
            f"{base}/saves/missing.srm",
            f"{base}/links/saves",
            f"{base}/links/saves/Game.srm",
            f"{base}/links/dead",
            f"{base}/links/dead/below",
        ]
        for path in probe_paths:
            assert fixture.path_kind(path) == real.path_kind(path), path
            assert fixture.read_text(path) == real.read_text(path), path
            assert fixture.file_size(path) == real.file_size(path), path
            for algorithm in ("md5", "sha1"):
                assert fixture.file_digest(path, algorithm) == real.file_digest(path, algorithm), path

        for path in probe_paths:
            # Fixture links carry absolute targets, so compare resolved-ness only.
            assert (fixture.readlink(path) is None) == (real.readlink(path) is None), path

        patterns = [
            f"{base}/saves/Game.*",
            f"{base}/saves/*",
            f"{base}/links/saves/Game.*",
            f"{base}/saves/empty/*",
            glob_module.escape(f"{base}/saves/Game [USA]") + ".*",
            f"{base}/saves/missing*",
            f"{base}/saves/.*",
        ]
        for pattern in patterns:
            assert fixture.glob(pattern) == real.glob(pattern), pattern
