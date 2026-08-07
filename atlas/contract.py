"""The canonical serialization of atlas results — the portable contract.

One JSON-shaped form per result type, used by the conformance vectors
(``vectors/``, schema 2) and available to consumers who want answers as plain
data. The rule for what serializes:

- **Structured fields are contractual**: directories, root kinds, holes,
  file-set state/files/completeness, granularity values and option identity,
  caveat codes and caveat data, health issue codes, installation identity.
  Vectors assert them with exact equality; ports must reproduce them.
- **Prose is not**: ``sources``, caveat ``message``, ``option_source``,
  ``FileSet.source`` are human-readable explanations and may change freely —
  they are deliberately absent here.

A port that reproduces these dicts for every vector reads the machine the way
the reference does.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence, TypeVar

from atlas.every_installation import InstallationAnswer
from atlas.firmware import (
    FirmwareAnswer,
    FirmwareIdentification,
    FirmwareIdentity,
    FirmwareRequirement,
)
from atlas.installations import CatalogueAnswer, EmulatorEntry, Installation, SystemsAnswer
from atlas.placement import SavePlacement, Unresolved

AnswerT = TypeVar("AnswerT")


def placement_contract(placement: SavePlacement) -> dict[str, Any]:
    """The stable, JSON-shaped form of a :class:`~atlas.placement.SavePlacement`."""
    granularity = placement.granularity
    return {
        "dir": placement.dir,
        "root_kind": placement.root_kind,
        "needs": list(placement.needs),
        "fallback_dir": placement.fallback_dir,
        "physical_dir": placement.physical_dir,
        "file_set": {
            "state": placement.file_set.state,
            "files": list(placement.file_set.files),
            "complete": placement.file_set.complete,
        },
        "granularity": None
        if granularity is None
        else {
            "value": granularity.value,
            "option_key": granularity.option_key,
            "option_value": granularity.option_value,
            "options_file": granularity.options_file,
            "alternatives": [list(pair) for pair in granularity.alternatives],
        },
        "caveats": [{"code": c.code, "data": dict(c.data)} for c in placement.caveats],
    }


def unresolved_contract(unresolved: Unresolved) -> dict[str, Any]:
    """The stable form of an :class:`~atlas.placement.Unresolved` outcome."""
    return {"unresolved": {"code": unresolved.code, "data": dict(unresolved.data)}}


def installation_contract(installation: Installation) -> dict[str, Any]:
    """The stable identity/health form of an installation handle."""
    return {
        "kind": installation.kind,
        "kinds": list(installation.kinds),
        "root": installation.root(),
        "health": list(installation.health().codes),
    }


def _identity_contract(identity: FirmwareIdentity | None) -> dict[str, Any] | None:
    if identity is None:
        return None
    return {"md5": identity.md5, "sha1": identity.sha1, "size": identity.size}


def _requirement_contract(requirement: FirmwareRequirement) -> dict[str, Any]:
    return {
        "core_so": requirement.core_so,
        "system": requirement.system,
        "system_source": requirement.system_source,
        "need": requirement.need,
        "file_name": requirement.file_name,
        "path": requirement.path,
        "declared": requirement.declared,
        "identity": _identity_contract(requirement.identity),
        "found": requirement.found,
        "present": requirement.present,
        "checked": requirement.checked,
        "satisfied": requirement.satisfied,
    }


def firmware_contract(answer: FirmwareAnswer) -> dict[str, Any]:
    """The stable form of a :class:`~atlas.firmware.FirmwareAnswer`.

    ``description`` is prose and stays out. Three derived fields are in on
    purpose, because a consumer deriving them itself is exactly how the answer
    gets read wrongly: ``declaration`` separates "this core needs nothing" from
    "atlas knows nothing about this core", ``satisfied`` says whether one file
    is actually usable (a *present* file with the wrong bytes is not), and
    ``requirements_met`` is the single number a client renders — never ``true``
    out of ignorance, never ``true`` with a required file known to be wrong.
    Two limits of it belong next to each other, because a consumer that renders
    only this field cannot see either:

    - With ``hash_checked`` false it is ``null`` wherever a required file's
      identity is known and was not verified. Presence is not the question the
      field asks; a caller who wants a green light passes ``verify``.
    - It can be ``true`` over a required file the packaged table does not cover
      at all (``checked`` ``unknown`` with ``identity`` ``null``). Nothing
      further can ever be established about such a file, so withholding the
      answer would withhold it forever — but "in place under the right name" is
      all that was checked. On the reference machine that is three requirements
      across two cores (blueMSX's databases and machine ROMs, Dolphin's
      ``codehandler.bin``).
    ``found`` is the path kind actually read, which ``present`` alone cannot
    carry (a directory at the destination is not a missing file), and
    ``refused`` names declarations atlas would not follow, with the reason,
    so a dropped file can never make a core look complete. ``path`` is the
    **resolved** destination and ``declared`` the string the core spelled: two
    declarations that land on one file are one place, and the name the core
    opens is still stated.
    """
    return {
        "root": answer.root,
        "hash_checked": answer.hash_checked,
        "cores": [
            {
                "core_so": core.core_so,
                "label": core.label,
                "declaration": core.declaration,
                "requirements_met": core.requirements_met,
                "requirements": [_requirement_contract(r) for r in core.requirements],
                "refused": [{"declared": r.declared, "need": r.need, "reason": r.reason} for r in core.refused],
                "caveats": [{"code": c.code, "data": dict(c.data)} for c in core.caveats],
            }
            for core in answer.cores
        ],
        "unclaimed": [
            {
                "path": f.path,
                "identity": _identity_contract(f.identity),
                "known_as": list(f.known_as),
            }
            for f in answer.unclaimed
        ],
        "caveats": [{"code": c.code, "data": dict(c.data)} for c in answer.caveats],
    }


def identification_contract(identification: FirmwareIdentification) -> dict[str, Any]:
    """The stable form of a :class:`~atlas.firmware.FirmwareIdentification`."""
    return {
        "identity": _identity_contract(identification.identity),
        "known_as": list(identification.known_as),
        "requirements": [_requirement_contract(r) for r in identification.requirements],
        "caveats": [{"code": c.code, "data": dict(c.data)} for c in identification.caveats],
    }


def emulator_contract(entry: EmulatorEntry) -> dict[str, Any]:
    """The stable form of one catalogue entry."""
    return {
        "label": entry.label,
        "kind": entry.kind,
        "core_so": entry.core_so,
        "selection": entry.selection,
        "caveats": [c.code for c in entry.caveats],
    }


def catalogue_contract(answer: CatalogueAnswer) -> dict[str, Any]:
    """The stable form of a catalogue answer — entries, and why there are none.

    ``caveats`` carries the whole caveat here, not just its code as the entries
    do: which arrangement could not answer, and whether that is a fact about
    the machine or about atlas, lives in the data.
    """
    return {
        "entries": [emulator_contract(e) for e in answer.entries],
        "caveats": [{"code": c.code, "data": dict(c.data)} for c in answer.caveats],
    }


def systems_contract(answer: SystemsAnswer) -> dict[str, Any]:
    """The stable form of a systems answer — what the catalogue declares, or why nothing."""
    return {
        "systems": list(answer.systems),
        "caveats": [{"code": c.code, "data": dict(c.data)} for c in answer.caveats],
    }


def installation_answers_contract(
    answers: Sequence[InstallationAnswer[AnswerT]],
    serialize: Callable[[AnswerT], dict[str, Any]],
) -> list[dict[str, Any]]:
    """The stable form of an aggregate answer — every installation's answer, labelled.

    Composed rather than defined: the label is :func:`installation_contract`,
    the payload is whatever serializer the question already has, passed in by
    the caller who asked it (``placement_contract`` for ``save_location``,
    ``catalogue_contract`` for ``emulators_for``, …). The aggregate adds no
    fields of its own, so it may not add serialization of its own either — an
    aggregate answer that differed from the handle-route answer in any way
    would be a resolver rule hiding in the fan-out.

    The list is ordered, and the order is contractual: it is detection order,
    the same order :func:`~atlas.detect.detect` states. An empty list is the
    empty machine, and stays a truthful answer rather than an error.
    """
    return [
        {
            "installation": installation_contract(answered.installation),
            "answer": serialize(answered.answer),
        }
        for answered in answers
    ]
