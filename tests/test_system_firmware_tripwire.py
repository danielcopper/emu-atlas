"""The system-firmware tripwire: a system whose cores disagree must be recorded.

A libretro ``.info`` declares firmware one file at a time and cannot say "one
of these three", so a core that knows its system needs a BIOS must either mark
every image required or mark every image optional. Both are lossy and the
deployed catalogue takes both directions for one machine: Beetle PSX marks the
three PlayStation region images required, SwanStation marks all five of its
images optional. Where the two moves collide on one system, the machine is
already carrying the answer — the *other* core's entry says the firmware is not
decoration. This module recomputes those collisions from the deployed
catalogue and fails when it finds a system ``atlas/data/system_firmware.json``
does not record, with any verdict at all, ``open`` included.

**This is a canary-tier test, and that is the point of it.** If you arrived
here from the weekly drift issue, the failure is not about your machine.
``.github/workflows/canary.yml`` deploys the *latest* RetroDECK from Flathub at
exactly the paths read below, refuses a deploy that would let this tier
silently skip, and files or updates the drift issue when the suite goes red. So
a release that ships a core introducing a new disagreement turns the canary red
within the week and opens the issue by itself, days before any reference
machine updates. What that failure asks for is a **verdict**, and the honest
one is usually ``open``: recording the system as open takes it out of this
tripwire and puts it where a person can see it, without anyone claiming to know
whether the system boots.

**What this cannot see.** The derivation finds a system only where its cores
*disagree*. Where every core of a system understates, there is no disagreement
to find and this file has nothing to say. Three systems are in exactly that
position in the catalogue deployed here — ``3DO`` (opera), ``SNK Neo Geo CD``
(neocd) and ``PC-98`` (np2kai), each declaring firmware and each declaring all
of it optional, with no second core to contradict them. They are not recorded
and this tripwire will never ask for them. It is a **floor on what is
recorded**, not a proof that the record is complete, and a later reader must
not take a green run for the second thing.

Skipped where RetroDECK is not deployed, by the same path check that silences
the rest of the machine-bound tier.
"""

from pathlib import Path
from typing import Iterable, Mapping

import pytest

from atlas.core_info import enumerate_firmware, parse_core_info
from atlas.system_firmware import load_system_firmware

# Where RetroDECK deploys the cores RetroArch loads, and the ``.info`` files it
# reads their declarations from. The same root the rule-card audits probe.
DEPLOYED_CORES = Path(
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
    "/retroarch/rd_extras/cores"
)
INFO_SUFFIX = "_libretro.info"

# What the derivation reads: each system against the cores declaring firmware
# under it, and for each core whether every slot it declares is optional. A
# core declaring no firmware at all states nothing about its system and is not
# in here.
Catalogue = Mapping[str, Mapping[str, bool]]


def _all_optional(text: str) -> bool | None:
    """Does this ``.info`` declare firmware, and is every slot of it optional?

    ``None`` where it declares none. The enumeration is RetroArch's own
    (:func:`atlas.core_info.enumerate_firmware`), so a path the emulator never
    reaches — outside ``firmware_count``, spelled a way nothing composes — is
    not a declaration here either, and a file this catalogue states and the
    emulator ignores cannot create a disagreement that is not really there.
    """
    slots = enumerate_firmware(parse_core_info(text)).slots
    return all(slot.optional for slot in slots) if slots else None


def read_catalogue(directory: Path) -> dict[str, dict[str, bool]]:
    """Every deployed ``.info`` that declares firmware, grouped by its ``systemname``.

    Grouped by the raw ``systemname`` rather than by an atlas system id: that
    is the unit the catalogue itself groups firmware by, and translating would
    decide a question this cut deliberately leaves open (one ``systemname``
    covers two catalogue systems for ``Game Boy/Game Boy Color``).

    A core stating **no** ``systemname`` is left out. An empty string names no
    system, so lumping such cores together would invent one and could
    manufacture a disagreement between two unrelated emulators;
    :func:`atlas.firmware.system_decision` answers ``_unknown`` for the same
    input and for the same reason.

    The walk covers every ``.info`` deployed, whether or not the matching
    ``.so`` is there. The two sets differ — 291 catalogue entries against 211
    binaries on the reference machine — and the declarations are what this
    derivation is about, so filtering to the cores this deployment happens to
    be able to load would make the evidence an accident of one installation.
    It is a large accident: 96 of the 118 declaring entries here have a binary,
    and holding the derivation to those loses three of the seven disagreements.
    """
    catalogue: dict[str, dict[str, bool]] = {}
    for path in sorted(directory.glob(f"*{INFO_SUFFIX}")):
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        optional = _all_optional(text)
        system = parse_core_info(text).get("systemname", "")
        if optional is None or not system:
            continue
        catalogue.setdefault(system, {})[path.name[: -len(INFO_SUFFIX)]] = optional
    return catalogue


def systems_whose_cores_disagree(catalogue: Catalogue) -> dict[str, tuple[list[str], list[str]]]:
    """Systems one core declares required firmware for and another declares all-optional.

    The two lists are the cores on each side, sorted — the failure message's
    whole substance, because "which entry contradicts which" is what a reader
    needs to reach a verdict.
    """
    disagreeing: dict[str, tuple[list[str], list[str]]] = {}
    for system, cores in catalogue.items():
        optional = sorted(core for core, is_optional in cores.items() if is_optional)
        required = sorted(core for core, is_optional in cores.items() if not is_optional)
        if optional and required:
            disagreeing[system] = (required, optional)
    return disagreeing


def unrecorded_disagreements(catalogue: Catalogue, recorded: Iterable[str]) -> list[str]:
    """The disagreeing systems *recorded* does not carry — what turns this file red.

    Factored out of the assertion so that the check itself can be tested
    without a deployed catalogue: a guard nobody has watched fail is a guard
    nobody knows fires.
    """
    return sorted(set(systems_whose_cores_disagree(catalogue)) - set(recorded))


def _catalogue_or_skip() -> dict[str, dict[str, bool]]:
    if not DEPLOYED_CORES.is_dir():
        pytest.skip(f"no cores are deployed at {DEPLOYED_CORES}")
    return read_catalogue(DEPLOYED_CORES)


class TestTheDerivationItself:
    """The comparison, over a catalogue written here — no machine involved."""

    def test_a_system_whose_cores_disagree_is_named(self):
        catalogue = {"Demo System": {"strict": False, "lax": True}}
        assert systems_whose_cores_disagree(catalogue) == {
            "Demo System": (["strict"], ["lax"])
        }

    def test_a_system_whose_cores_agree_is_not(self):
        catalogue = {
            "All Optional": {"one": True, "two": True},
            "All Required": {"three": False, "four": False},
        }
        assert systems_whose_cores_disagree(catalogue) == {}

    def test_an_unrecorded_disagreement_is_what_fails(self):
        # The bite, held explicitly: this is the value the machine-bound test
        # below asserts is empty, so a mistake that made it always empty would
        # be caught here rather than by a green run that checked nothing.
        catalogue = {"Demo System": {"strict": False, "lax": True}}
        assert unrecorded_disagreements(catalogue, []) == ["Demo System"]
        assert unrecorded_disagreements(catalogue, ["Demo System"]) == []

    def test_a_core_declaring_no_firmware_takes_no_part(self):
        # `read_catalogue` drops these before the derivation sees them; the
        # derivation is stated over the same shape all the same, because a
        # core that declares nothing contradicts nothing.
        assert _all_optional('corename = "Demo"\n') is None
        assert _all_optional('firmware_count = "1"\nfirmware0_path = "a.bin"\n') is False
        assert (
            _all_optional(
                'firmware_count = "1"\nfirmware0_path = "a.bin"\nfirmware0_opt = "true"\n'
            )
            is True
        )


class TestEveryDisagreementIsRecorded:
    """The tripwire: the deployed catalogue against the shipped table."""

    def test_no_disagreeing_system_is_missing_from_the_table(self):
        catalogue = _catalogue_or_skip()
        missing = unrecorded_disagreements(catalogue, load_system_firmware())
        assert missing == [], (
            f"the deployed catalogue disagrees with itself about {missing} and "
            "atlas/data/system_firmware.json records no verdict for them — one core of each "
            "declares a firmware file required while another declares every file optional, so "
            "the machine already says the firmware is not decoration and nothing in atlas says "
            "so. Add an entry per system; 'open' is a verdict and is the honest one until "
            "somebody watches the system start with no firmware present"
        )

    def test_an_exempted_core_really_declares_everything_optional(self):
        catalogue = _catalogue_or_skip()
        stale: list[str] = []
        for system, entry in load_system_firmware().items():
            cores = catalogue.get(system, {})
            for alternative in entry.alternatives:
                if cores.get(alternative.core) is not True:
                    stale.append(f"{system}/{alternative.core}")
        assert stale == [], (
            f"{stale} are recorded as cores whose all-optional declaration is correct because "
            "they supply an alternative, and the deployed catalogue no longer has them "
            "declaring everything optional for that system — either the core's entry changed "
            "or the exemption names the wrong core. An exemption that matches nothing excuses "
            "nothing and hides the next real understatement"
        )

    def test_the_catalogue_is_really_read_where_cores_are_deployed(self):
        # The all-skip guard the rest of this tier carries: a run that derived
        # nothing must not read as a run that found nothing.
        catalogue = _catalogue_or_skip()
        assert catalogue, (
            f"cores are deployed at {DEPLOYED_CORES} and not one .info was read as declaring "
            "firmware under a systemname — either the catalogue moved out of this directory or "
            "this tripwire is silently deriving over nothing"
        )
