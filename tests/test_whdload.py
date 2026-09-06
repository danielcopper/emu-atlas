"""Tests for atlas.whdload — the slave structure, and the boot script's own search.

The hunk files here are built from the field table in the WHDLoad autodoc
(``WHDLoad.Slave/--Overview--``) and the hunk layout UAE reads
(``sources/src/debugmem.c:1526-1576`` at 0043cf9), so the reader is proven
against the format rather than against one slave's habits. The listings the
selection tests use are the shapes the core's boot script branches on
(``whdload/WHDLoad_files/S/Startup-Sequence:61-82``), including the shape a
real install archive has — a drawer icon at the root and the slave one level
down — which that script resolves only when the slave is named after its own
drawer.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from atlas import lha, whdload

DATA = Path(__file__).parent / "data"
SAMPLE = DATA / "whdload-sample.lha"

HUNK_HEADER = 0x3F3
HUNK_CODE = 0x3E9
HUNK_DATA = 0x3EA
BOTH_MEMORY_FLAGS = 0xC0000000
# Where the strings a version-10-and-later structure points at begin: right
# after ws_info, the last field this reader needs.
STRINGS_AT = 42


def slave_body(*, version: int = 17, name: str | None = "Alien Breed", identifier: bytes = b"WHDLOADS") -> bytes:
    """A WHDLoadSlave structure, its fields in the autodoc's order and widths."""
    body = (
        struct.pack(">HH", 0x70FF, 0x4E75)
        + identifier
        + struct.pack(">HH", version, 0)
        + struct.pack(">II", 0, 0)
        + struct.pack(">HHH", 0, 0, 0)
        + b"\x00\x00"
        + struct.pack(">I", 0)
    )
    if version < whdload.NAMED_FROM_VERSION:
        return body
    pointer = STRINGS_AT if name is not None else 0
    return body + struct.pack(">HHH", pointer, 0, 0) + (name.encode("latin-1") + b"\x00" if name else b"")


def hunk_file(
    body: bytes, *, first_type: int = HUNK_CODE, library_word: int = 0, sizes: list[int] | None = None
) -> bytes:
    """A one-hunk AmigaDOS executable holding *body*."""
    padded = body + b"\x00" * (-len(body) % 4)
    longs = len(padded) // 4
    table = [longs] if sizes is None else sizes
    out = struct.pack(">IIIII", HUNK_HEADER, library_word, len(table), 0, len(table) - 1)
    for size in table:
        out += struct.pack(">I", size)
        if size & BOTH_MEMORY_FLAGS == BOTH_MEMORY_FLAGS:
            out += struct.pack(">I", 0)
    return out + struct.pack(">II", first_type, longs) + padded


def test_a_slave_states_its_version_and_its_program_name() -> None:
    assert whdload.read_slave(hunk_file(slave_body())) == whdload.Slave(17, "Alien Breed")


def test_a_slave_older_than_version_ten_states_no_name() -> None:
    # The autodoc: everything from ws_name on is evaluated only for
    # ws_Version >= 10, so an older structure has no such field at all.
    assert whdload.read_slave(hunk_file(slave_body(version=9))) == whdload.Slave(9, None)


def test_an_empty_name_pointer_is_no_name_rather_than_an_empty_one() -> None:
    assert whdload.read_slave(hunk_file(slave_body(name=None))).name is None


def test_a_memory_attribute_on_a_hunk_size_is_read_past() -> None:
    body = slave_body()
    longs = (len(body) + 3) // 4

    slave = whdload.read_slave(hunk_file(body, sizes=[BOTH_MEMORY_FLAGS | longs]))

    assert slave.name == "Alien Breed"


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        pytest.param(b"", "ends before offset", id="empty"),
        pytest.param(b"not an executable at all", "hunk header", id="some-other-file"),
        pytest.param(hunk_file(slave_body(), library_word=1), "resident libraries", id="library-list"),
        pytest.param(hunk_file(slave_body(), first_type=HUNK_DATA), "code hunk", id="first-hunk-is-data"),
        pytest.param(hunk_file(slave_body(identifier=b"NOTASLAV")), "WHDLOADS", id="wrong-identifier"),
        pytest.param(hunk_file(slave_body())[:40], "cut short", id="hunk-data-cut-short"),
        pytest.param(hunk_file(slave_body())[:30], "ends before offset", id="hunk-block-cut-short"),
    ],
)
def test_bytes_that_are_not_a_slave_are_refused(data: bytes, reason: str) -> None:
    with pytest.raises(whdload.NotASlave, match=reason):
        whdload.read_slave(data)


def test_a_name_pointing_outside_the_hunk_is_refused() -> None:
    body = bytearray(slave_body())
    struct.pack_into(">H", body, 36, 4000)

    with pytest.raises(whdload.NotASlave, match="outside"):
        whdload.read_slave(hunk_file(bytes(body)))


# ---------------------------------------------------------------------------
# Which slave the boot script selects.
# ---------------------------------------------------------------------------


def selected(names: list[str]) -> tuple[str | None, str | None, bool]:
    """One selection flattened, so a case reads as (slave, route, ambiguous)."""
    choice = whdload.select_slave(names)
    return choice.slave, choice.route, choice.ambiguous


def test_a_slave_at_the_root_is_the_one_the_script_selects() -> None:
    assert selected(["Game.slave", "Game.info", "data/level1"]) == ("Game.slave", "script", False)


def test_the_pattern_matches_without_regard_to_case_or_a_trailing_extension() -> None:
    # `#?.slav#?` is ".slav" with anything on either side.
    assert selected(["GAME.SLAVE"])[0] == "GAME.SLAVE"
    assert selected(["game.slav"])[0] == "game.slav"


def test_with_no_info_at_the_root_the_search_descends_into_the_directory() -> None:
    assert selected(["Game/AlienBreed.slave", "Game/data/level1"]) == (
        "Game/AlienBreed.slave",
        "script",
        False,
    )


def test_with_an_info_at_the_root_the_script_takes_a_slave_named_after_its_drawer() -> None:
    assert selected(["Game.info", "Game/Game.slave", "Game/ReadMe", "Game/Other.slave"]) == (
        "Game/Game.slave",
        "script",
        False,
    )


def test_the_deepest_branch_takes_a_slave_named_after_its_own_subdirectory() -> None:
    assert selected(["Game/Inner/Inner.slave", "Game/Inner/data", "Game/Inner/Other.slave"]) == (
        "Game/Inner/Inner.slave",
        "script",
        False,
    )


def test_a_drawer_slave_the_script_misses_is_still_the_only_one_whdload_could_run() -> None:
    # The shape a public install archive has. The script resolves nothing and
    # hands the launch to the .info selector, where whichever icon is picked
    # WHDLoad has this one slave to run — so the name follows from the set.
    assert selected(["AlienBreedHD.info", "AlienBreedHD/AlienBreed.slave", "AlienBreedHD/ReadMe"]) == (
        "AlienBreedHD/AlienBreed.slave",
        "only-slave",
        False,
    )


def test_two_slaves_the_script_cannot_tell_apart_name_neither() -> None:
    assert selected(["One.slave", "Two.slave"]) == (None, None, True)


def test_two_candidate_directories_name_no_slave() -> None:
    assert selected(["One/One.slave", "Two/Two.slave"]) == (None, None, True)


def test_an_icon_beside_a_slave_does_not_count_as_a_second_one() -> None:
    # The inference asks what WHDLoad could be handed, and an .info is not
    # that — the script's own '.slav' pattern would match both.
    assert selected(["Game.info", "Game/AlienBreed.slave", "Game/AlienBreed.slave.info"]) == (
        "Game/AlienBreed.slave",
        "only-slave",
        False,
    )


def test_a_load_file_at_the_root_replaces_the_launch_and_names_no_slave() -> None:
    # The archive's own command runs instead, and it need not run WHDLoad at
    # all — so the only-slave inference is kept out of this one.
    assert selected(["load", "Game.slave"]) == (None, None, False)


def test_a_listing_with_nothing_to_find_names_no_slave() -> None:
    assert selected(["ReadMe", "Game/level1"]) == (None, None, False)


def test_a_named_slave_carries_the_route_that_named_it() -> None:
    with pytest.raises(ValueError, match="carries the route"):
        whdload.Selection("Game.slave", None)


# ---------------------------------------------------------------------------
# Which member the core launches out of an archive, and what it mounts.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        pytest.param(["Game.lha"], "Game.lha", id="a-nested-archive"),
        pytest.param(["Game.slave"], "Game.slave", id="a-bare-slave"),
        pytest.param(["Game.info", "Game/Game.slave"], "Game.info", id="an-info-with-its-drawer"),
        pytest.param(["Game.info", "Game.slave"], None, id="two-candidates-name-neither"),
        pytest.param([".hidden.slave", "Game.m3u"], None, id="names-the-walk-passes-over"),
        pytest.param(["Game.info"], None, id="an-info-with-neither"),
        pytest.param(["Game.adf", "Notes.txt"], None, id="nothing-whdload-about-it"),
    ],
)
def test_the_accepted_members_are_the_ones_the_core_would_take(names: list[str], expected: str | None) -> None:
    accepted = whdload.accepted_members(names)
    assert (accepted[0] if len(accepted) == 1 else None) == expected


def test_the_mounted_root_is_the_drawer_beside_the_member_where_there_is_one() -> None:
    assert whdload.mounted_root("Game.info", ["Game.info", "Game/Game.slave"]) == "Game/"


def test_the_mounted_root_is_the_whole_tree_where_the_member_has_no_drawer() -> None:
    assert whdload.mounted_root("Game.slave", ["Game.slave", "ReadMe"]) == ""


def test_the_sample_archive_resolves_end_to_end() -> None:
    data = SAMPLE.read_bytes()
    members = lha.members(data)

    choice = whdload.select_slave([member.name for member in members])
    member = next(m for m in members if m.name == choice.slave)

    assert (choice.slave, choice.route) == ("TestGame/TestGame.slave", "script")
    assert whdload.read_slave(lha.extract(data, member)) == whdload.Slave(17, "Test Game")
