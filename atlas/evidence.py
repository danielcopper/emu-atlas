"""Which arrangements atlas has seen alive — the evidence behind every answer.

Reading a config the way the emulator reads it is one thing; having watched a
real machine of that arrangement do it is another. Atlas's resolver rules are
source-verified against pinned upstream for every arrangement, but only
RetroDECK has been observed end to end on a live installation
(``docs/research/``, and the per-core column in ``docs/research/coverage-matrix.md``).
An answer from an arrangement nobody has observed is derived, not confirmed —
and derived is worth saying out loud, machine-readably, on the answer itself.

Two rules decide the shape here, both from CLAUDE.md's boundary rule:

- **The claim is about atlas, never about the machine.** The caveat says a live
  observation of the *arrangement* is missing. It does not say the configs were
  guessed (they are read the same way everywhere), and it does not say this
  installation is broken — that is what :class:`~atlas.installations.Health`
  answers, which is why the evidence note deliberately stays out of it.
- **The status is data, not code.** It lives in ``data/arrangement_evidence.json``
  — marked, versioned, source-cited — so the day an arrangement is verified on a
  reference machine, one file changes and the caveat retires by itself. No
  resolver mentions any arrangement's evidence state.

An arrangement absent from the file is unverified: a missing record is not
evidence, and the safe direction is to say so rather than to fall silent. A
handle kind that never gained an entry is a build mistake all the same, and the
test suite refuses it there instead of here.
"""

from __future__ import annotations

import importlib.resources
import json
from dataclasses import dataclass
from typing import Any

from atlas.placement import Caveat

# Packaged-data schema version. The loader is strict for the same reason the
# rule-card loaders are: a malformed build fails loudly instead of resolving
# with knowledge nobody can place.
ARRANGEMENT_EVIDENCE_SCHEMA = 1

# The answer-level caveat every unverified arrangement attaches. Sits next to
# ``unverified-version`` (a rule card's pinned versions differ from this
# machine's) and reads the same way: a statement about what atlas has
# established, not about what the machine is doing.
CAVEAT_ARRANGEMENT_UNVERIFIED = "arrangement-unverified"


def _expect_str(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: expected a non-empty string, got {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class LiveVerification:
    """The installation an arrangement's knowledge was confirmed against."""

    version: str
    date: str | None
    reference: str


@dataclass(frozen=True, slots=True)
class ArrangementEvidence:
    """What atlas has established about one arrangement, beyond reading its configs.

    ``verified`` is ``None`` for an arrangement no live installation has
    confirmed — the state that produces the caveat. ``note`` carries the
    evidence level and what it rests on, in the register of the research
    documents; ``label`` is how the arrangement is named in prose.
    """

    kind: str
    label: str
    note: str
    verified: LiveVerification | None = None


def _live_verification(record: Any, where: str) -> LiveVerification | None:
    """One arrangement's verification record — ``None`` stays *never observed*.

    A present record must pin the ``version`` it was observed on and cite the
    installation, for the reason the core audit pins its own: a record that
    names nothing would read as verified everywhere and forever while
    establishing nothing, which is worse than never verified because it claims
    the opposite.
    """
    if record is None:
        return None
    if not isinstance(record, dict):
        raise ValueError(f"{where}: expected an object or null, got {record!r}")
    date = record.get("date")
    if date is not None and not isinstance(date, str):
        raise ValueError(f"{where}.date: expected a string or null, got {date!r}")
    return LiveVerification(
        version=_expect_str(record.get("version"), f"{where}.version"),
        date=date,
        reference=_expect_str(record.get("reference"), f"{where}.reference"),
    )


def load_arrangement_evidence(text: str | None = None) -> dict[str, ArrangementEvidence]:
    """Load the packaged evidence records (or *text* when supplied, for tests)."""
    if text is None:
        text = (
            importlib.resources.files("atlas")
            .joinpath("data", "arrangement_evidence.json")
            .read_text(encoding="utf-8")
        )
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("schema") != ARRANGEMENT_EVIDENCE_SCHEMA:
        raise ValueError(
            f"arrangement_evidence: unsupported schema "
            f"{raw.get('schema') if isinstance(raw, dict) else None!r} "
            f"(this atlas reads schema {ARRANGEMENT_EVIDENCE_SCHEMA})"
        )
    records: dict[str, ArrangementEvidence] = {}
    for kind, entry in raw.get("arrangements", {}).items():
        where = f"arrangement {kind!r}"
        if not isinstance(entry, dict):
            raise ValueError(f"{where}: expected an object, got {entry!r}")
        records[kind] = ArrangementEvidence(
            kind=kind,
            label=_expect_str(entry.get("label"), f"{where}: label"),
            note=_expect_str(entry.get("note"), f"{where}: note"),
            verified=_live_verification(entry.get("verified"), f"{where}: verified"),
        )
    return records


_EVIDENCE: dict[str, ArrangementEvidence] | None = None


def lookup_arrangement(kind: str) -> ArrangementEvidence | None:
    """The packaged evidence record for an installation kind, if there is one."""
    global _EVIDENCE
    if _EVIDENCE is None:
        _EVIDENCE = load_arrangement_evidence()
    return _EVIDENCE.get(kind)


def arrangement_caveats(kind: str) -> tuple[Caveat, ...]:
    """What every answer from *kind* must state about its own evidence.

    Empty for an arrangement observed on a live installation — a verified
    arrangement says nothing, exactly as an installation with no health issues
    says nothing. Otherwise one caveat, on the answer itself, so a client
    branching on codes learns it without knowing which arrangements exist.

    The message states what is missing and what is not: the missing part is a
    live observation of the arrangement; the part that is *not* missing is the
    config reading, which is source-verified against pinned upstream wherever it
    runs. A client that renders this as "atlas guessed" would report something
    nobody claimed.
    """
    record = lookup_arrangement(kind)
    if record is not None and record.verified is not None:
        return ()
    label = record.label if record is not None else f"the {kind!r} arrangement"
    return (
        Caveat(
            CAVEAT_ARRANGEMENT_UNVERIFIED,
            f"no live installation of {label} has been observed by atlas — how its configs are read is "
            "source-verified against pinned upstream, but nothing has confirmed the wiring end to end on "
            "a running machine, so this answer is derived rather than verified "
            "(docs/how-to-use.md, 'What atlas has actually seen')",
            {"kind": kind},
        ),
    )
