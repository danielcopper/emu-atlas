"""Tests for atlas.reader — the FixtureReader and a FilesystemReader smoke test."""

from __future__ import annotations

from atlas.reader import FilesystemReader, FixtureReader


class TestFixtureReader:
    def test_read_text_known_path(self):
        reader = FixtureReader({"/home/deck/a.cfg": "hello"})
        assert reader.read_text("/home/deck/a.cfg") == "hello"

    def test_read_text_unknown_path_is_none(self):
        reader = FixtureReader({"/home/deck/a.cfg": "hello"})
        assert reader.read_text("/home/deck/missing.cfg") is None

    def test_empty_file_is_empty_string_not_none(self):
        # A present-but-empty file must be distinguishable from an absent one:
        # detection keys off "is not None", so "" must survive as "".
        reader = FixtureReader({"/home/deck/empty.cfg": ""})
        assert reader.read_text("/home/deck/empty.cfg") == ""
        assert reader.read_text("/home/deck/empty.cfg") is not None

    def test_exists(self):
        reader = FixtureReader({"/home/deck/a.cfg": "x"})
        assert reader.exists("/home/deck/a.cfg") is True
        assert reader.exists("/home/deck/nope") is False

    def test_glob_matches_keys_with_fnmatch_sorted(self):
        reader = FixtureReader(
            {
                "/home/deck/.config/retroarch/retroarch.cfg": "x",
                "/home/deck/.config/retroarch/notes.txt": "y",
                "/home/deck/other.cfg": "z",
            }
        )
        # fnmatch on keys: '*' crosses '/', so a '*.cfg' pattern matches by
        # suffix regardless of depth (this is fnmatch, not glob.glob semantics).
        assert reader.glob("*.cfg") == [
            "/home/deck/.config/retroarch/retroarch.cfg",
            "/home/deck/other.cfg",
        ]

    def test_glob_can_scope_by_literal_prefix(self):
        reader = FixtureReader(
            {
                "/home/deck/.config/retroarch/retroarch.cfg": "x",
                "/home/deck/.config/retroarch/notes.txt": "y",
                "/home/deck/other.cfg": "z",
            }
        )
        assert reader.glob("/home/deck/.config/retroarch/*.cfg") == [
            "/home/deck/.config/retroarch/retroarch.cfg"
        ]

    def test_glob_no_match_is_empty(self):
        reader = FixtureReader({"/a": "x"})
        assert reader.glob("/b/*") == []


class TestFilesystemReader:
    def test_smoke_roundtrip(self, tmp_path):
        target = tmp_path / "sub" / "file.cfg"
        target.parent.mkdir(parents=True)
        target.write_text("content", encoding="utf-8")

        reader = FilesystemReader()
        assert reader.read_text(str(target)) == "content"
        assert reader.exists(str(target)) is True
        assert reader.exists(str(tmp_path / "missing")) is False
        assert reader.read_text(str(tmp_path / "missing")) is None
        assert reader.glob(str(tmp_path / "sub" / "*.cfg")) == [str(target)]

    def test_read_directory_returns_none(self, tmp_path):
        # A directory is not text — read_text swallows the OSError and returns None.
        assert FilesystemReader().read_text(str(tmp_path)) is None
