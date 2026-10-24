"""The build tripwire: an entry must describe the build that is actually deployed.

`atlas/data/core_firmware.json` states how a core locates firmware and cites
upstream source at one pinned revision. That citation is only worth anything
while the deployed binary *is* that revision, and a core's route is exactly the
kind of thing that moves: Beetle PSX grew a second door after the pinned
commit — a SHA1 directory search (`search_firmware`) that turns a `by-name`
core into a `by-name-then-content` one — and a build carrying it would be
described by an entry that says otherwise, in a table whose whole value is that
it does not guess.

So where the core is deployed, this asks the binary what it is. A libretro core
reports a version string through `retro_get_system_info`
(:attr:`atlas.machine.CoreInfo.library_version`), and the three cores entered
today put their short commit hash in it — SwanStation as `1.0.0 <hash>`, both
Beetle builds as `0.9.44.1 <hash>`. The entry's `build.revision` has to appear
in that string. A build that moved on fails here rather than being quietly
described by the wrong source.

**What this cannot see.** A core whose version string does not carry a hash
cannot be checked this way at all, and an entry for one would need a different
kind of evidence; today every entry carries one, and
:meth:`TestTheCheckItself.test_an_entry_whose_revision_is_absent_is_named` is
what holds the check honest by watching it fail. The check is also not a proof
that the *route* is unchanged — a revision can move without the version string
moving if upstream does not bump it — it is a floor: the cheapest question that
catches the ordinary case, which is an upgrade.

Skipped where RetroDECK is not deployed, by the same path check that silences
the rest of the machine-bound tier.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pytest

from atlas.core_firmware import CoreFirmwareCard, core_firmware_cards
from atlas.machine import RealMachine

# Where RetroDECK deploys the cores RetroArch loads — the same root the rule-card
# audits and the system-firmware tripwire probe.
DEPLOYED_CORES = Path(
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
    "/retroarch/rd_extras/cores"
)


def stamp_disagreements(
    cards: tuple[CoreFirmwareCard, ...], stamps: Mapping[str, str | None]
) -> list[str]:
    """The entries whose pinned revision the deployed stamp does not carry.

    *stamps* maps a card's key to what that core reported, ``None`` where the
    core is deployed and answered nothing. A core that is not deployed at all
    is simply absent from the mapping and is not judged here — this machine's
    core set is an accident of one installation, and the entries are not.

    Factored out of the assertion so the comparison can be exercised without a
    deployed core: a guard nobody has watched fail is a guard nobody knows
    fires.
    """
    wrong: list[str] = []
    for card in cards:
        if card.key not in stamps:
            continue
        reported = stamps[card.key]
        if reported is None:
            wrong.append(f"{card.key}: the deployed core reported no version at all")
        elif card.build.revision not in reported:
            wrong.append(
                f"{card.key}: the entry pins {card.build.revision!r} and the deployed "
                f"build reports {reported!r}"
            )
    return wrong


def reported_stamp(card: CoreFirmwareCard, binary: bytes, version: str | None) -> str | None:
    """What one deployed core says it is: its pinned constant where the entry states one, its version otherwise.

    An entry states ``build.stamp`` where the version names no commit (PCEE2
    reports the upstream PCSX2 release). Its binary is then searched for the
    stamp as raw bytes with a NUL on each side, so a hit is the whole constant;
    a binary without it answers with its version, which the revision is not in.
    """
    stamp = card.build.stamp
    if stamp is not None and b"\x00" + stamp.encode() + b"\x00" in binary:
        return stamp
    return version


def deployed_stamps(directory: Path) -> dict[str, str | None]:
    """What each entered core deployed here reports as its own version, or carries as its stamp."""
    machine = RealMachine()
    stamps: dict[str, str | None] = {}
    for card in core_firmware_cards():
        path = directory / card.so_name
        if not path.is_file():
            continue
        info = machine.query_core(str(path))
        version = None if info is None else info.library_version
        binary = path.read_bytes() if card.build.stamp is not None else b""
        stamps[card.key] = reported_stamp(card, binary, version)
    return stamps


def _stamps_or_skip() -> dict[str, str | None]:
    if not DEPLOYED_CORES.is_dir():
        pytest.skip(f"no cores are deployed at {DEPLOYED_CORES}")
    stamps = deployed_stamps(DEPLOYED_CORES)
    if not stamps:
        pytest.skip("no core this table describes is deployed here")
    return stamps


class TestTheCheckItself:
    """The comparison, over stamps written here — no machine involved."""

    def _cards(self) -> tuple[CoreFirmwareCard, ...]:
        return core_firmware_cards()

    def test_a_stamp_carrying_the_pinned_revision_passes(self):
        stamps = {card.key: f"9.9.9 {card.build.revision}" for card in self._cards()}
        assert stamp_disagreements(self._cards(), stamps) == []

    def test_an_entry_whose_revision_is_absent_is_named(self):
        stamps = {card.key: "9.9.9 deadbee" for card in self._cards()}
        named = stamp_disagreements(self._cards(), stamps)
        assert sorted(n.split(":")[0] for n in named) == sorted(
            card.key for card in self._cards()
        )

    def test_a_core_that_reported_nothing_is_named(self):
        one = self._cards()[0]
        named = stamp_disagreements(self._cards(), {one.key: None})
        assert named == [f"{one.key}: the deployed core reported no version at all"]

    def test_a_core_that_is_not_deployed_is_not_judged(self):
        assert stamp_disagreements(self._cards(), {}) == []

    def _stamped(self) -> CoreFirmwareCard:
        card = next((card for card in self._cards() if card.build.stamp is not None), None)
        assert card is not None, "no entry states a stamp — the checks below would prove nothing"
        return card

    def test_a_binary_carrying_the_stamp_reports_it(self):
        card = self._stamped()
        assert card.build.stamp is not None
        binary = b"junk\x00" + card.build.stamp.encode() + b"\x00junk"
        reported = reported_stamp(card, binary, "v9.9.9")
        assert reported == card.build.stamp
        assert stamp_disagreements((card,), {card.key: reported}) == []

    def test_a_binary_without_the_stamp_reports_its_version_and_is_named(self):
        card = self._stamped()
        reported = reported_stamp(card, b"\x00deadbeef\x00", "v9.9.9")
        assert reported == "v9.9.9"
        assert [line.split(":")[0] for line in stamp_disagreements((card,), {card.key: reported})] == [card.key]

    def test_a_stamp_inside_a_longer_constant_is_not_the_stamp(self):
        card = self._stamped()
        assert card.build.stamp is not None
        binary = b"\x00" + card.build.stamp.encode() + b"-dirty\x00"
        assert reported_stamp(card, binary, "v9.9.9") == "v9.9.9"

    def test_an_entry_without_a_stamp_reports_its_version(self):
        card = next(card for card in self._cards() if card.build.stamp is None)
        assert reported_stamp(card, b"", f"1.0 {card.build.revision}") == f"1.0 {card.build.revision}"


class TestEveryEntryDescribesTheDeployedBuild:
    """Machine-bound: what the binaries here actually say."""

    def test_every_deployed_entry_carries_its_pinned_revision(self):
        stamps = _stamps_or_skip()
        assert stamp_disagreements(core_firmware_cards(), stamps) == []

    def test_the_probe_really_read_something(self):
        # Without this, a probe that answered `None` for every core would make
        # the check above vacuous in the one direction a skip does not cover.
        stamps = _stamps_or_skip()
        assert [key for key, value in stamps.items() if value], (
            "every deployed core this table describes answered no version — the probe read "
            "nothing, so the check above proved nothing"
        )
