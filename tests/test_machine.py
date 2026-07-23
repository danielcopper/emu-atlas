"""Tests for atlas.machine — the seam, fixture semantics, and the real prober."""

from __future__ import annotations

import os

from atlas.machine import CoreInfo, FixtureMachine, RealMachine


class TestFixtureFiles:
    def test_read_text_and_exists(self):
        m = FixtureMachine({"/a/b.txt": "hello"})
        assert m.read_text("/a/b.txt") == "hello"
        assert m.exists("/a/b.txt")
        assert m.read_text("/a/missing.txt") is None
        assert not m.exists("/a/missing.txt")

    def test_directory_prefix_exists(self):
        m = FixtureMachine({"/a/b/c.txt": ""})
        assert m.exists("/a/b")
        assert m.exists("/a")
        assert not m.exists("/a/x")

    def test_glob_is_sorted_and_deterministic(self):
        m = FixtureMachine({"/s/b.srm": "", "/s/a.srm": "", "/s/a.rtc": ""})
        assert m.glob("/s/a.*") == ["/s/a.rtc", "/s/a.srm"]


class TestFixtureSymlinks:
    def test_read_through_link(self):
        m = FixtureMachine({"/real/f.txt": "x"}, symlinks={"/link": "/real"})
        assert m.read_text("/link/f.txt") == "x"
        assert m.exists("/link/f.txt")

    def test_direct_file_link(self):
        m = FixtureMachine({"/real/f.txt": "x"}, symlinks={"/alias.txt": "/real/f.txt"})
        assert m.read_text("/alias.txt") == "x"

    def test_dead_link_is_visible_but_not_existing(self):
        # The applewin case: readlink shows the link, exists says no.
        m = FixtureMachine({}, symlinks={"/cores": "/app/cores"})
        assert m.readlink("/cores") == "/app/cores"
        assert not m.exists("/cores")
        assert m.read_text("/cores/x.so") is None

    def test_readlink_on_regular_path_is_none(self):
        m = FixtureMachine({"/a.txt": ""})
        assert m.readlink("/a.txt") is None

    def test_relative_link_target(self):
        m = FixtureMachine({"/data/real/f.txt": "x"}, symlinks={"/data/link": "real"})
        assert m.read_text("/data/link/f.txt") == "x"

    def test_link_cycle_does_not_hang(self):
        m = FixtureMachine({}, symlinks={"/a": "/b", "/b": "/a"})
        assert m.read_text("/a/f.txt") is None
        assert not m.exists("/a/f.txt")


class TestFixtureCores:
    def test_query_core_returns_info(self):
        m = FixtureMachine({}, cores={"/cores/mgba_libretro.so": {"library_name": "mGBA"}})
        info = m.query_core("/cores/mgba_libretro.so")
        assert info == CoreInfo(library_name="mGBA", library_version=None, valid_extensions=None)

    def test_unloadable_core_is_none(self):
        m = FixtureMachine({}, cores={"/cores/applewin_libretro.so": None})
        assert m.query_core("/cores/applewin_libretro.so") is None

    def test_missing_core_is_none(self):
        m = FixtureMachine({})
        assert m.query_core("/cores/nope.so") is None

    def test_core_path_exists(self):
        m = FixtureMachine({}, cores={"/cores/mgba_libretro.so": {"library_name": "mGBA"}})
        assert m.exists("/cores/mgba_libretro.so")

    def test_query_core_through_symlinked_dir(self):
        m = FixtureMachine(
            {},
            symlinks={"/config/cores": "/deploy/cores"},
            cores={"/deploy/cores/mgba_libretro.so": {"library_name": "mGBA"}},
        )
        info = m.query_core("/config/cores/mgba_libretro.so")
        assert info is not None and info.library_name == "mGBA"


class TestRealMachine:
    def test_readlink_and_exists_on_dead_link(self, tmp_path):
        link = tmp_path / "dead"
        os.symlink("/nonexistent/target", link)
        m = RealMachine()
        assert m.readlink(str(link)) == "/nonexistent/target"
        assert not m.exists(str(link))

    def test_readlink_on_regular_file_is_none(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("x")
        m = RealMachine()
        assert m.readlink(str(f)) is None
        assert m.exists(str(f))

    def test_query_core_on_non_library_is_none(self, tmp_path):
        not_a_core = tmp_path / "fake.so"
        not_a_core.write_text("not an ELF")
        m = RealMachine()
        assert m.query_core(str(not_a_core)) is None

    def test_query_core_on_missing_path_is_none(self):
        m = RealMachine()
        assert m.query_core("/nonexistent/core.so") is None
