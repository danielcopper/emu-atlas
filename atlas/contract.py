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

from typing import Any

from atlas.installations import EmulatorEntry, Installation
from atlas.placement import SavePlacement, Unresolved


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


def emulator_contract(entry: EmulatorEntry) -> dict[str, Any]:
    """The stable form of one catalogue entry."""
    return {
        "label": entry.label,
        "kind": entry.kind,
        "core_so": entry.core_so,
        "selection": entry.selection,
        "caveats": [c.code for c in entry.caveats],
    }
