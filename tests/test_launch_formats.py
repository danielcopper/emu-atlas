"""The install-first formats table: packaged shape, and a loader that never coerces."""

import json

import pytest

from atlas.launch_formats import load_launch_formats, lookup_install_first


def _table(extension, entry):
    return json.dumps({"schema": 1, "systems": {"ps3": {extension: entry}}})


ENTRY = {"statement": "an installer", "source": "[D] cited"}


class TestThePackagedTable:
    def test_the_psn_package_is_recorded(self):
        record = lookup_install_first("ps3", ".pkg")
        assert record is not None
        assert record.source.startswith("[")

    def test_lookup_is_exact_and_case_sensitive(self):
        # The keys are tokens the way ES-DE derives them — a .PKG file yields
        # a different token, and this table never smooths that over.
        assert lookup_install_first("ps3", ".PKG") is None
        assert lookup_install_first("psx", ".pkg") is None


class TestTheLoaderRefusesWhatItCannotState:
    def test_a_token_without_its_dot_fails(self):
        table = _table("pkg", ENTRY)
        with pytest.raises(ValueError, match="'.'"):
            load_launch_formats(table)

    def test_the_bare_dot_sentinel_is_not_a_format(self):
        table = _table(".", ENTRY)
        with pytest.raises(ValueError, match="names one"):
            load_launch_formats(table)

    def test_an_entry_without_a_source_fails(self):
        table = _table(".pkg", {"statement": "an installer"})
        with pytest.raises(ValueError, match="exactly"):
            load_launch_formats(table)

    def test_an_unknown_schema_fails(self):
        with pytest.raises(ValueError, match="schema"):
            load_launch_formats('{"schema": 9, "systems": {}}')
