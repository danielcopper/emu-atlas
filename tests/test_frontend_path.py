"""The assumed PATH of a frontend on the host: the packaged table's shape, and a loader that never coerces."""

import json

import pytest

from atlas.frontend_path import frontend_search_path, load_frontend_path


def _table(*directories: object) -> str:
    return json.dumps({"schema": 1, "spec": "s", "frontends": {"emudeck": {"search_path": list(directories)}}})


class TestThePackagedTable:
    def test_every_directory_carries_a_citation(self):
        for name, directories in load_frontend_path().items():
            assert directories, name
            for entry in directories:
                assert entry.citation.strip(), f"{name}: {entry.directory} is uncited"

    def test_emudeck_s_path_puts_the_backend_first_and_the_shipped_default_after(self):
        path = frontend_search_path("emudeck", "/home/u")
        assert path[0] == "/home/u/.config/EmuDeck/backend//tools/binaries/"
        assert path[1:4] == ("/usr/local/sbin", "/usr/local/bin", "/usr/bin")
        assert path[4:6] == ("/home/u/.local/share/flatpak/exports/bin", "/var/lib/flatpak/exports/bin")
        assert path[6:] == ("/usr/bin/site_perl", "/usr/bin/vendor_perl", "/usr/bin/core_perl")

    def test_a_frontend_the_table_does_not_carry_is_an_error(self):
        with pytest.raises(KeyError):
            frontend_search_path("retrodeck", "/home/u")


class TestTheLoaderRefusesWhatItCannotMean:
    def test_another_schema_is_refused(self):
        table = json.dumps({"schema": 2, "frontends": {}})
        with pytest.raises(ValueError, match="unsupported schema"):
            load_frontend_path(table)

    def test_no_frontends_is_refused(self):
        table = json.dumps({"schema": 1, "frontends": {}})
        with pytest.raises(ValueError, match="non-empty 'frontends'"):
            load_frontend_path(table)

    def test_an_entry_names_only_its_search_path(self):
        table = json.dumps({"schema": 1, "frontends": {"emudeck": {"search_path": [], "extra": 1}}})
        with pytest.raises(ValueError, match="exactly 'search_path'"):
            load_frontend_path(table)

    def test_an_empty_search_path_is_refused(self):
        table = _table()
        with pytest.raises(ValueError, match="non-empty list"):
            load_frontend_path(table)

    def test_a_directory_without_a_citation_is_refused(self):
        blank = _table({"directory": "/usr/bin", "citation": " "})
        absent = _table({"directory": "/usr/bin"})
        with pytest.raises(ValueError, match="carries a citation"):
            load_frontend_path(blank)
        with pytest.raises(ValueError, match="exactly 'directory' and 'citation'"):
            load_frontend_path(absent)

    def test_a_directory_neither_absolute_nor_under_home_is_refused(self):
        table = _table({"directory": "bin", "citation": "c"})
        with pytest.raises(ValueError, match="absolute or under ~/"):
            load_frontend_path(table)

    def test_a_home_directory_is_kept_as_the_table_spells_it(self):
        loaded = load_frontend_path(_table({"directory": "~/bin", "citation": "c"}))
        assert [entry.directory for entry in loaded["emudeck"]] == ["~/bin"]
