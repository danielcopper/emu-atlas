"""The build tripwire for packaged declarations: an entry must describe the build deployed here.

`atlas/data/core_declarations.json` states, in the place of a missing `.info`,
which firmware a core opens, and cites the core's source at one revision. That
is worth something only while the deployed binary *is* that revision — the
declaration is exactly the kind of thing an upgrade moves.

The version a core reports through `retro_get_system_info` cannot be the
check here: PCEE2 reports the upstream PCSX2 release it is merged up to, the
same string across several of its own commits. What it does carry is its
commit hash as a string constant of its own (`pcsx2/BuildVersion.cpp:14`,
`GIT_HASH`), and the entry states that constant as its `build.stamp`. So this
reads the binary rather than running it, and searches it for exactly the
stamp, as raw bytes bounded by a NUL on each side — the two NULs are what make
a hit the whole constant rather than the head or the tail of a longer one, the
same rule as `tests/test_standalone_build_tripwire.py`.

**What this cannot see.** It establishes which build is deployed, not that any
line the entry cites still says what the entry says — a floor, not a proof.

Skipped where RetroDECK is not deployed, like the tripwires beside it.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from atlas.core_declarations import PackagedCoreDeclaration, packaged_core_declarations

# Where RetroDECK deploys the cores RetroArch loads — the root the core
# firmware tripwire reads too.
DEPLOYED_CORES = Path(
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
    "/retroarch/rd_extras/cores"
)

NO_STAMP = "does not carry the pinned stamp as a whole NUL-delimited constant"


def carries_stamp(binary: bytes, stamp: str) -> bool:
    """Does *binary* hold *stamp* as one whole string constant?"""
    return b"\x00" + stamp.encode() + b"\x00" in binary


def stamp_disagreements(
    entries: tuple[PackagedCoreDeclaration, ...], binaries: Mapping[str, bytes]
) -> list[str]:
    """The entries whose stamp the deployed binary does not carry.

    *binaries* maps an entry's key to the bytes of that core's deployed
    ``.so``. A core that is not deployed is absent from the mapping and is not
    judged — this machine's core set is an accident of one installation, and
    the entries are not.

    Factored out of the assertion so the comparison can be exercised without a
    deployed core: a guard nobody has watched fail is a guard nobody knows
    fires.
    """
    return [
        f"{entry.key}: the entry pins {entry.build.stamp!r} and the deployed binary {NO_STAMP}"
        for entry in entries
        if entry.key in binaries and not carries_stamp(binaries[entry.key], entry.build.stamp)
    ]


def deployed_binaries(directory: Path) -> dict[str, bytes]:
    """The bytes of each entered core deployed here."""
    return {
        entry.key: path.read_bytes()
        for entry in packaged_core_declarations()
        if (path := directory / f"{entry.stem}.so").is_file()
    }


def _binaries_or_skip() -> dict[str, bytes]:
    if not DEPLOYED_CORES.is_dir():
        pytest.skip(f"no cores are deployed at {DEPLOYED_CORES}")
    binaries = deployed_binaries(DEPLOYED_CORES)
    if not binaries:
        pytest.skip("no core this table describes is deployed here")
    return binaries


class TestTheCheckItself:
    """The comparison, over binaries written here — no machine involved."""

    def test_a_binary_carrying_the_stamp_passes(self):
        entries = packaged_core_declarations()
        binaries = {entry.key: b"junk\x00" + entry.build.stamp.encode() + b"\x00junk" for entry in entries}
        assert stamp_disagreements(entries, binaries) == []

    def test_an_entry_whose_stamp_is_absent_is_named(self):
        entries = packaged_core_declarations()
        named = stamp_disagreements(entries, {entry.key: b"\x00deadbeef\x00" for entry in entries})
        assert sorted(line.split(":")[0] for line in named) == sorted(entry.key for entry in entries)

    def test_a_stamp_inside_a_longer_constant_is_not_the_stamp(self):
        entries = packaged_core_declarations()
        binaries = {entry.key: b"\x00" + entry.build.stamp.encode() + b"-dirty\x00" for entry in entries}
        assert len(stamp_disagreements(entries, binaries)) == len(entries)

    def test_a_core_that_is_not_deployed_is_not_judged(self):
        assert stamp_disagreements(packaged_core_declarations(), {}) == []


class TestEveryEntryDescribesTheDeployedBuild:
    """Machine-bound: what the binaries here actually carry."""

    def test_every_deployed_entry_carries_its_stamp(self):
        assert stamp_disagreements(packaged_core_declarations(), _binaries_or_skip()) == []
