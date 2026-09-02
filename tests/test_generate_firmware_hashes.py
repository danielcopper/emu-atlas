"""Both of the firmware-table generator's modes, exercised at the function level.

``tests/test_firmware_hashes_data.py`` checks the shipped file; this checks the
script that produces it. The two modes disagree about exactly one thing — where
the identities come from — and agree about everything the script itself states,
so each is exercised over the same synthetic table and the same assertions are
made about what came out.

Nothing here touches the network or needs a libretro-database clone: the
``--database`` path is fed a ``dat/System.dat`` written here, and the
``--restamp`` path a JSON table written here.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from scripts import generate_firmware_hashes as generator

# One dump, one romset archive and one core-bundled archive is the whole range
# the stamping has to tell apart.
DUMP = "scph5501.bin"
ROMSET = "neogeo.zip"
BUNDLED = "ecwolf.pk3"


def _identity(index: int) -> dict[str, object]:
    return {"md5": f"{index:032x}", "sha1": f"{index:040x}", "size": 1024 + index}


def _every_curated_name() -> dict[str, dict[str, object]]:
    """A parsed System.dat carrying one dump plus every name the curated list has.

    The list must be fully covered, because the generator refuses a curated line
    for a name the table does not carry — that guard is what keeps the list from
    drifting out of reach, and a fixture that tripped it would be testing the
    guard instead of the stamping.
    """
    names = [DUMP, *sorted(generator.ARCHIVE_IDENTITIES)]
    return {name: _identity(index) for index, name in enumerate(names)}


def _write_system_dat(directory, hashes: dict[str, dict[str, object]]) -> None:
    lines = [
        f'game ( name "{name}" rom ( name "{name}" size {entry["size"]} '
        f'crc AABBCCDD md5 {entry["md5"]} sha1 {entry["sha1"]} ) )'
        for name, entry in hashes.items()
    ]
    dat_dir = directory / "dat"
    dat_dir.mkdir(parents=True)
    (dat_dir / "System.dat").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def built(tmp_path) -> dict[str, Any]:
    """The ``--database`` path: a synthetic System.dat parsed and stamped."""
    checkout = tmp_path / "libretro-database"
    checkout.mkdir()
    _write_system_dat(checkout, _every_curated_name())
    return generator.build_table(generator.resolve_database(str(checkout)))


@pytest.fixture
def restamped(tmp_path) -> dict[str, Any]:
    """The ``--restamp`` path: an existing table read back and re-stamped."""
    table = {
        "_meta": {
            "generated_from": "somewhere upstream",
            "version": "5.0.0",
            "generated_at": "2020-01-01",
        },
        "files": {name: dict(entry) for name, entry in _every_curated_name().items()},
    }
    path = tmp_path / "firmware_hashes.json"
    path.write_text(json.dumps(table), encoding="utf-8")
    return generator.restamp_table(generator.resolve_input(str(path)))


class TestBothModesStateWhatTheScriptOwns:
    """The schema version and the curated list's version and date, on either path."""

    @pytest.fixture(params=["built", "restamped"])
    def table(self, request) -> dict[str, Any]:
        return request.getfixturevalue(request.param)

    def test_the_schema_version_is_the_scripts_own(self, table):
        assert table["_meta"]["version"] == generator.SCHEMA_VERSION

    def test_the_list_version_is_stamped(self, table):
        assert table["_meta"]["archive_identities_version"] == generator.ARCHIVE_IDENTITIES_VERSION

    def test_the_review_date_is_stamped(self, table):
        assert table["_meta"]["archive_identities_reviewed"] == generator.ARCHIVE_IDENTITIES_REVIEWED

    def test_meta_carries_nothing_else(self, table):
        assert set(table["_meta"]) == {
            "generated_from",
            "version",
            "generated_at",
            "archive_identities_version",
            "archive_identities_reviewed",
        }

    def test_a_dump_is_stamped_as_a_file_with_no_reason(self, table):
        assert table["files"][DUMP] == {**_identity(0), "kind": "file"}

    def test_every_curated_name_is_stamped_as_an_archive(self, table):
        kinds = {name: table["files"][name]["kind"] for name in generator.ARCHIVE_IDENTITIES}
        assert set(kinds.values()) == {"archive"}

    def test_every_archive_carries_the_reason_the_list_gives_it(self, table):
        stamped = {
            name: table["files"][name]["archive_reason"] for name in generator.ARCHIVE_IDENTITIES
        }
        assert stamped == {name: reason for name, (reason, _what) in generator.ARCHIVE_IDENTITIES.items()}

    def test_a_romset_archive_is_stamped_romset(self, table):
        assert table["files"][ROMSET]["archive_reason"] == generator.REASON_ROMSET

    def test_a_core_bundled_archive_is_stamped_core_bundled(self, table):
        assert table["files"][BUNDLED]["archive_reason"] == generator.REASON_CORE_BUNDLED

    def test_names_come_out_sorted(self, table):
        assert list(table["files"]) == sorted(table["files"])


class TestTheModesDifferOnlyInWhereIdentitiesComeFrom:
    def test_a_restamp_leaves_every_identity_untouched(self, restamped):
        original = _every_curated_name()
        carried = {
            name: {key: entry[key] for key in ("md5", "sha1", "size")}
            for name, entry in restamped["files"].items()
        }
        assert carried == original

    def test_a_restamp_keeps_the_generation_date_it_found(self, restamped):
        # No upstream data was read, so a fresh date would be a claim about
        # where the identities came from.
        assert restamped["_meta"]["generated_at"] == "2020-01-01"

    def test_a_restamp_keeps_the_upstream_source_it_found(self, restamped):
        assert restamped["_meta"]["generated_from"] == "somewhere upstream"

    def test_a_build_names_the_upstream_it_parsed(self, built):
        assert built["_meta"]["generated_from"] == generator.GENERATED_FROM

    def test_a_restamp_drops_a_key_the_schema_no_longer_has(self, tmp_path):
        # Rebuilding each entry from the three identity fields is what makes a
        # restamp's output shaped exactly like a generation's.
        files = {name: dict(entry) for name, entry in _every_curated_name().items()}
        files[DUMP]["retired_field"] = "gone"
        path = tmp_path / "firmware_hashes.json"
        path.write_text(json.dumps({"_meta": {}, "files": files}), encoding="utf-8")
        restamped = generator.restamp_table(generator.resolve_input(str(path)))
        assert set(restamped["files"][DUMP]) == {"md5", "sha1", "size", "kind"}


class TestTheGuardsRefuseRatherThanGuess:
    def test_a_table_missing_a_curated_name_is_refused(self, tmp_path):
        # A curated line for a name the table does not carry describes nothing,
        # and leaving it in would put the list out of reach of every check.
        table = {"_meta": {}, "files": {DUMP: _identity(0)}}  # every archive name absent
        path = tmp_path / "firmware_hashes.json"
        path.write_text(json.dumps(table), encoding="utf-8")
        resolved = generator.resolve_input(str(path))
        with pytest.raises(SystemExit, match="the curated archive list names"):
            generator.restamp_table(resolved)

    def test_an_entry_without_an_identity_is_refused(self, tmp_path):
        table = {"_meta": {}, "files": {DUMP: {"md5": "0" * 32}}}
        path = tmp_path / "firmware_hashes.json"
        path.write_text(json.dumps(table), encoding="utf-8")
        resolved = generator.resolve_input(str(path))
        with pytest.raises(SystemExit, match="this is not an identity table"):
            generator.restamp_table(resolved)

    def test_restamping_a_file_that_is_not_there_is_refused(self, tmp_path):
        missing = str(tmp_path / "nothing.json")
        with pytest.raises(ValueError, match="nothing to restamp"):
            generator.resolve_input(missing)


def test_the_two_reasons_are_both_represented_in_the_shipped_list():
    # The fixtures above assert per-name agreement with the list; this is what
    # makes that meaningful — a list that had collapsed to one reason would
    # still pass them.
    reasons = {reason for reason, _what in generator.ARCHIVE_IDENTITIES.values()}
    assert reasons == {generator.REASON_ROMSET, generator.REASON_CORE_BUNDLED}


def test_the_curated_names_are_exactly_the_ones_stamped_as_archives(built):
    archives = {name for name, entry in built["files"].items() if entry["kind"] == "archive"}
    assert archives == set(generator.ARCHIVE_IDENTITIES)
