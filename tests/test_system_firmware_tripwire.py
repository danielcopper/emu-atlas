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

**A slot is written three ways and read two.** ``firmware<N>_opt`` may say
optional, may say required, or may be absent — and absent means required, by
RetroArch's own documented default. The derivation is binary because the
enumeration already applies that default, not because the third shape does not
exist; :func:`_all_optional` states the rule with its citation, and a test
holds it. One deployed core is written that way today.

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

**What closing it takes, concretely.** Add one entry to
``atlas/data/system_firmware.json``, keyed by the ``systemname`` this failure
names, verbatim. It carries three fields — ``verdict``
(``cannot-run-without-firmware``, ``runs-without-firmware`` or ``open``),
``evidence`` (``[V]``, ``[D]`` or ``[O]``, and the loader refuses any pairing
but ``open``/``[O]`` and not-``open``/not-``[O]``), and ``source``, saying what
that level read. For an ``open`` entry the source is the disagreement itself:
which cores declare a required file and which declare everything optional. A
stated verdict needs an observation or a source read, never the disagreement
alone — a catalogue contradiction is what makes the question worth asking, not
an answer to it.

**What this cannot see.** The derivation finds a system only where its cores
*disagree*. Where every core of a system understates, there is no disagreement
to find and this file has nothing to say — which is the case for **36** systems
in the catalogue deployed here, every declaring core of each saying
all-optional.

Three of those 36 are worth naming, and what is claimed about them is narrow.
``3DO`` (opera), ``SNK Neo Geo CD`` (neocd) and ``PC-98`` (np2kai) are each the
**only** core declaring firmware under their ``systemname``, so no second entry
*could* contradict them — that much is derived from the catalogue and is
checkable. Whether any of the three actually needs firmware is **unestablished**
here, and the other 33 have not been examined either: naming three is not a
claim that the remaining 33 were checked and cleared. How many of the 36
understate is exactly what this derivation cannot say.

It is a **floor on what is recorded**, not a proof that the record is complete,
and a later reader must not take a green run for the second thing.

Skipped where RetroDECK is not deployed, by the same path check that silences
the rest of the machine-bound tier.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

import pytest

from atlas.core_info import enumerate_firmware, parse_core_info
from atlas.system_firmware import CoreAlternative, SystemFirmware, load_system_firmware

# Where RetroDECK deploys the cores RetroArch loads, and the ``.info`` files it
# reads their declarations from. The same root the rule-card audits probe.
DEPLOYED_CORES = Path(
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
    "/retroarch/rd_extras/cores"
)
INFO_SUFFIX = "_libretro.info"

# Floors for the "did the walk really walk?" guard, not expectations. The
# deployed tree holds 291 catalogue entries yielding 65 systems today, so both
# numbers sit far below anything ordinary churn reaches; they exist to make a
# structural collapse — the catalogue moving, the suffix changing, the walk
# reading an empty tree — fail loudly instead of passing a bare non-empty
# check. Raise them only against a measurement, never to track today's value.
MINIMUM_INFO_FILES = 50
MINIMUM_SYSTEMS = 20

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

    **A slot can be written three ways and read only two.** ``firmware<N>_opt``
    may say optional, may say required, or may be absent — and absent is
    **required**, which is the reading a person guesses wrong.

    The rule is in RetroArch's source, not in a comment: ``core_info.c``
    ``calloc``s the slot array at ``:1584-1585`` so ``optional`` starts false,
    and ``:1603-1604`` writes the flag **only** when ``config_get_bool``
    succeeds — an absent or unparseable value leaves that zero standing. Both
    citations are at the pinned revision ``a79435a``.
    :func:`atlas.core_info._slot_at` ports exactly that, so the third shape
    needs no third branch here and lands on the required side of the derivation
    by itself.

    libretro's template says the same thing in words, on one line at
    ``00_example_libretro.info:47``::

        # Is firmware optional or not, if not defined RetroArch will assume it is required

    That line is documentation rather than the citation, and it is nearly gone:
    two of the 291 files matching ``*_libretro.info`` — the glob this walk uses
    — still carry it, the template and ``puzzlescript_libretro.info:45``.

    The rule is stated here rather than left to the port because reading an
    absent flag as optional is what would let a
    system disappear from this tripwire silently. One deployed core is written
    that way today (``ecwolf``), and
    :meth:`TestTheDerivationItself.test_a_slot_that_states_no_opt_reads_as_required`
    is what holds the reading.
    """
    slots = enumerate_firmware(parse_core_info(text)).slots
    return all(slot.optional for slot in slots) if slots else None


def read_catalogue(directory: Path) -> dict[str, dict[str, bool]]:
    """Every deployed ``.info`` that declares firmware, grouped by its ``systemname``.

    Grouped by the raw ``systemname`` rather than by an atlas system id: that
    is the unit the catalogue itself groups firmware by, and it is the unit
    ``atlas/data/system_firmware.json`` is keyed on. The translation into
    atlas's own ids happens where an answer reads that table
    (:func:`atlas.firmware.system_firmware_system`) rather than here, so this
    derivation and the file it checks speak one vocabulary.

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


def stale_exemptions(
    catalogue: Catalogue, recorded: Mapping[str, SystemFirmware]
) -> list[str]:
    """Exempted cores the catalogue no longer has declaring everything optional.

    The other direction of the exemption field. An exemption matches nothing
    when the core's entry changed, when the exemption names the wrong core, or
    when that system left the catalogue — and one matching nothing excuses
    nothing while still reading, to anyone scanning the table, as a core
    somebody cleared.

    Factored out for the same reason :func:`unrecorded_disagreements` is: the
    comparison lived inline in its test, so nothing could watch it go red
    without a deployed catalogue to break.

    The forward direction is the resolver's:
    :func:`atlas.firmware._system_firmware_state` reads the same field to
    report an exempted core ``core-supplies-an-alternative`` rather than
    ``cannot-run-without-firmware``. The disagreement derivation reads it
    nowhere, so deleting the field changes no verdict *there* and only leaves
    this function with nothing to compare.
    """
    return sorted(
        f"{system}/{alternative.core}"
        for system, entry in recorded.items()
        for alternative in entry.alternatives
        if catalogue.get(system, {}).get(alternative.core) is not True
    )


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

    def test_a_slot_that_states_no_opt_reads_as_required(self):
        # The third shape, and the one a reader guesses wrong: `firmware<N>_opt`
        # may say optional, may say required, or may be ABSENT — and absent is
        # required, by RetroArch's own documented default (quoted in
        # `_all_optional`, and live at puzzlescript_libretro.info:45). Reading
        # it as optional would let a core that states a hard requirement pass
        # for one that states none, and a system could then leave this tripwire
        # with nobody noticing. `ecwolf` is written exactly this way today.
        stated_optional = 'firmware_count = "1"\nfirmware0_path = "a.bin"\nfirmware0_opt = "true"\n'
        stated_required = (
            'firmware_count = "1"\nfirmware0_path = "a.bin"\nfirmware0_opt = "false"\n'
        )
        no_flag = 'firmware_count = "1"\nfirmware0_path = "a.bin"\n'
        assert _all_optional(stated_optional) is True
        assert _all_optional(stated_required) is False
        assert _all_optional(no_flag) is False
        # And the consequence the derivation actually rests on: a core written
        # the third way sits on the required side, so it contradicts an
        # all-optional sibling instead of falling between the two.
        assert systems_whose_cores_disagree(
            {"Demo System": {"quiet": _all_optional(no_flag) is True, "lax": True}}
        ) == {"Demo System": (["quiet"], ["lax"])}


class TestTheStalenessCheckBites:
    """The exemption's own guard, over a catalogue written here.

    Its comparison used to live inline in the machine-bound test, so nothing
    could watch it go red without breaking a deployed catalogue — and a check
    nobody has seen fail is a check nobody knows fires. These are the three
    ways an exemption stops matching.
    """

    def _recorded(self, system: str, core: str) -> dict[str, SystemFirmware]:
        return {
            system: SystemFirmware(
                system=system,
                verdict="cannot-run-without-firmware",
                evidence="[V]",
                source="[V-live] watched it refuse",
                alternatives=(CoreAlternative(core=core, reason="[V-live] watched it run"),),
            )
        }

    def test_an_exemption_matching_an_all_optional_core_is_not_stale(self):
        recorded = self._recorded("Demo System", "lax")
        assert stale_exemptions({"Demo System": {"lax": True, "strict": False}}, recorded) == []

    def test_an_exempted_core_that_now_declares_required_firmware_is_stale(self):
        recorded = self._recorded("Demo System", "lax")
        assert stale_exemptions({"Demo System": {"lax": False}}, recorded) == [
            "Demo System/lax"
        ]

    def test_an_exemption_naming_a_core_the_catalogue_does_not_have_is_stale(self):
        recorded = self._recorded("Demo System", "ghost")
        assert stale_exemptions({"Demo System": {"lax": True}}, recorded) == [
            "Demo System/ghost"
        ]

    def test_an_exemption_whose_system_left_the_catalogue_is_stale(self):
        recorded = self._recorded("Demo System", "lax")
        assert stale_exemptions({"Other System": {"lax": True}}, recorded) == [
            "Demo System/lax"
        ]

    def test_two_stale_exemptions_are_both_reported_and_sorted(self):
        # Accumulation and ordering, which every single-entry case above leaves
        # unproven: a function returning only the first stale exemption, or
        # returning them in mapping order, passes all of them. Two systems,
        # each with a stale core, recorded in reverse of the expected order.
        recorded = {
            "Zeta System": SystemFirmware(
                system="Zeta System",
                verdict="cannot-run-without-firmware",
                evidence="[V]",
                source="[V-live] watched it refuse",
                alternatives=(CoreAlternative(core="zed", reason="[V-live] watched it run"),),
            ),
            "Alpha System": SystemFirmware(
                system="Alpha System",
                verdict="cannot-run-without-firmware",
                evidence="[V]",
                source="[V-live] watched it refuse",
                alternatives=(CoreAlternative(core="aye", reason="[V-live] watched it run"),),
            ),
        }
        catalogue = {"Zeta System": {"zed": False}, "Alpha System": {"aye": False}}
        assert stale_exemptions(catalogue, recorded) == ["Alpha System/aye", "Zeta System/zed"]

    def test_two_exemptions_on_one_system_report_independently(self):
        # The other axis of accumulation: one entry carrying two exempted
        # cores, one of them stale. A loop that stopped at the first match
        # would report nothing here.
        recorded = {
            "Demo System": SystemFirmware(
                system="Demo System",
                verdict="cannot-run-without-firmware",
                evidence="[V]",
                source="[V-live] watched it refuse",
                alternatives=(
                    CoreAlternative(core="good", reason="[V-live] still all-optional"),
                    CoreAlternative(core="gone", reason="[V-live] no longer all-optional"),
                ),
            )
        }
        catalogue = {"Demo System": {"good": True, "gone": False}}
        assert stale_exemptions(catalogue, recorded) == ["Demo System/gone"]

    def test_an_entry_with_no_exemption_is_never_stale(self):
        recorded = {
            "Demo System": SystemFirmware(
                system="Demo System",
                verdict="open",
                evidence="[O]",
                source="[O] nobody looked",
                alternatives=(),
            )
        }
        assert stale_exemptions({}, recorded) == []


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
        stale = stale_exemptions(catalogue, load_system_firmware())
        assert stale == [], (
            f"{stale} are recorded as cores whose all-optional declaration is correct because "
            "they supply an alternative, and the deployed catalogue no longer has them "
            "declaring everything optional for that system — either the core's entry changed "
            "or the exemption names the wrong core. An exemption that matches nothing excuses "
            "nothing and hides the next real understatement"
        )

    def test_the_catalogue_is_really_read_where_cores_are_deployed(self):
        # The all-skip guard the rest of this tier carries, with a floor rather
        # than a bare non-empty check: a walk that collapsed to a handful of
        # entries would satisfy `assert catalogue` and derive over almost
        # nothing, which is the failure this guard exists to make loud.
        catalogue = _catalogue_or_skip()
        infos = list(DEPLOYED_CORES.glob(f"*{INFO_SUFFIX}"))
        assert len(infos) >= MINIMUM_INFO_FILES, (
            f"cores are deployed at {DEPLOYED_CORES} and it holds {len(infos)} catalogue entries "
            f"— fewer than the {MINIMUM_INFO_FILES} floor, so this is not the cores tree the "
            "derivation was written against and the walk is reading somewhere else"
        )
        assert len(catalogue) >= MINIMUM_SYSTEMS, (
            f"{len(infos)} catalogue entries are deployed and only {len(catalogue)} systems came "
            f"out of them, under the {MINIMUM_SYSTEMS} floor — either the declarations moved out "
            "of these files or the walk is silently deriving over almost nothing"
        )
