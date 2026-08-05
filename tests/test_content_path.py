"""Tests for atlas.content_path — the port of RetroArch's content naming."""

from __future__ import annotations

from atlas.content_path import archive_delimiter, content_file_name, split_content_path


class TestSplitContentPath:
    """M5/L11: content is named the way ``runloop_path_set_basename`` names it."""

    def test_plain_path_splits_at_the_last_dot(self):
        assert split_content_path("/roms/gba/Golden Sun (USA).zip") == (
            "/roms/gba",
            "gba",
            "Golden Sun (USA)",
        )

    def test_archive_entry_is_named_after_the_entry(self):
        # runloop.c:8702-8705 — the archive delimiter decides directory and name
        # before the extension is cut, so the ROM is 'Game' in the archive's dir.
        assert split_content_path("/roms/n64/Pack.zip#Game.n64") == ("/roms/n64", "n64", "Game")

    def test_seven_zip_is_an_archive_too(self):
        assert split_content_path("/roms/n64/Pack.7z#Game.n64") == ("/roms/n64", "n64", "Game")

    def test_archive_extension_matches_case_insensitively(self):
        assert split_content_path("/roms/n64/Pack.ZIP#Game.n64") == ("/roms/n64", "n64", "Game")

    def test_folder_inside_the_archive_becomes_the_content_directory(self):
        # path_basename cuts at the delimiter, not at the last slash
        # (file_path.c:692-700), so the in-archive folder lands in the path.
        assert split_content_path("/roms/n64/Pack.7z#disc/Game.n64") == (
            "/roms/n64/disc",
            "disc",
            "Game",
        )

    def test_hash_without_a_compression_extension_is_part_of_the_name(self):
        assert split_content_path("/roms/gb/Game #2.gb") == ("/roms/gb", "gb", "Game #2")

    def test_trailing_slash_names_the_same_file(self):
        # L11: the trailing slash disappears in RetroArch's own math —
        # path_basename returns nothing and the last dot is cut all the same.
        assert split_content_path("/roms/psx/Game.cue/") == split_content_path("/roms/psx/Game.cue")

    def test_path_without_a_name_leaves_the_stem_empty(self):
        # No dot to cut and no last component: RetroArch would write '.srm'
        # into that directory (file_path.c:345-358).
        assert split_content_path("/roms/psx/Game/") == ("/roms/psx/Game", "psx", "")

    def test_dot_in_a_directory_truncates_the_whole_path(self):
        # runloop.c:8710-8711 truncates at the last dot of the PATH, guarded
        # only by "not at index 0" — with an extensionless ROM under a dotted
        # directory the cut lands in the directory name, and the save with it.
        assert split_content_path("/roms/My.Games/rom") == ("/roms", "roms", "My")

    def test_the_leading_dot_guard_only_protects_index_zero(self):
        # runloop.c:8710-8711 guards 'dst - path > 0', so a path whose only dot
        # is its first character keeps its name...
        assert split_content_path(".config/rom") == (".config", ".config", "rom")
        # ...while a bare relative name gets './' put in front of it first
        # (file_path.c:1335-1337), which moves that dot off index 0 — RetroArch
        # ends up with no name at all.
        assert split_content_path(".hidden") == (".", "", "")

    def test_a_relative_name_answers_the_current_directory(self):
        # path_basedir names './' where os.path.dirname would say nothing at all
        # (file_path.c:625-640) — atlas states the directory upstream states.
        assert split_content_path("Game.n64") == (".", ".", "Game")

    def test_one_non_ascii_character_is_two_bytes(self):
        # path_basedir_wrapper's early return tests s[1] == '\0' — bytes, not
        # characters (file_path.c:1325), so a single accented letter goes
        # through the normal path and gets './' in front of it.
        assert split_content_path("é") == (".", ".", "é")

    def test_multi_disc_folder_keeps_its_dotted_name(self):
        assert split_content_path("/roms/psx/Title.m3u/Title.m3u") == (
            "/roms/psx/Title.m3u",
            "Title.m3u",
            "Title",
        )


class TestArchiveDelimiter:
    def test_delimiter_needs_a_character_before_the_extension(self):
        # file_path.c:184/194 — the 'd > 3' / 'd > 4' guards, counted from the
        # start of the whole path.
        zipped = "/roms/n64/a.zip#Game.n64"
        seven_zipped = "a.7z#Game.n64"
        assert archive_delimiter(zipped) == zipped.index("#")
        assert archive_delimiter(seven_zipped) == seven_zipped.index("#")
        assert archive_delimiter(".zip#Game.n64") == -1
        assert archive_delimiter(".7z#Game.n64") == -1

    def test_the_first_delimiting_hash_wins(self):
        path = "/roms/gb/Pack #1.zip#Game #2.gb"
        assert archive_delimiter(path) == path.index(".zip#") + len(".zip")

    def test_every_known_archive_extension_delimits(self):
        for suffix in (".7z", ".zip", ".zst", ".apk"):
            path = f"/roms/gb/Pack{suffix}#Game.gb"
            assert archive_delimiter(path) == path.index("#"), suffix


class TestContentFileName:
    def test_a_plain_path_names_its_own_file(self):
        assert content_file_name("/roms/gb/Game.gb") == "Game.gb"

    def test_an_archive_path_names_the_archive(self):
        # What lies behind the '#' is inside the archive, not next to it.
        assert content_file_name("/roms/gb/Game.zip#Game.gb") == "Game.zip"

    def test_a_trailing_slash_names_the_same_file(self):
        # The same equivalence split_content_path states for the name: without
        # it the ROM would not be filtered out of its own directory.
        assert content_file_name("/roms/psx/Game.cue/") == "Game.cue"
        assert content_file_name("/roms/gb/Game.zip#Game.gb/") == "Game.zip"
