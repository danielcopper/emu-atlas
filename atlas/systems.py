"""atlas's own system vocabulary — the ids every question about a system takes.

Those ids are **ES-DE's system names** — ``gb``, ``n64``, ``dreamcast`` —
because that is what a frontend catalogue on the machine declares, and a
resolver that reads the machine should speak the machine's names. The ids are
pinned to the ``es_systems.xml`` of a stated build (``data/system_ids.json``),
which is what makes membership checkable rather than an opinion: a name that
file does not declare is not an id, however plausible it looks.

What this module deliberately does **not** do is translate. A consumer holding
some other product's platform identifiers — a library manager's slugs, a
metadata service's ids — owns that mapping, because owning it means owning what
it is worth: those vocabularies are versioned by someone else, they change
without telling atlas, and much of what they name is not an emulated system at
all. A table of them here would be world knowledge atlas cannot verify against
any machine, which is the one kind of knowledge the boundary rule refuses. Two
functions are the whole consumer surface, and both answer about atlas's own
vocabulary: :func:`known_systems` hands over the target set, and
:func:`from_esde_system` says whether one name is in it.

That is enough to check a mapping you own before you use it. The failure it
prevents is the expensive one: an identifier no catalogue declares reaches a
question, the question answers "no emulator for that system", and a vocabulary
mistake has been read as a fact about the machine.
"""

from __future__ import annotations

import importlib.resources
import json

# Packaged-data schema version, strict for the reason the whole loader is: a
# malformed build fails loudly instead of answering out of a list nobody can
# place.
SYSTEM_IDS_SCHEMA = 1


# This check exists verbatim three times, one per packaged-data loader
# (:func:`atlas.evidence._expect_str`, :func:`atlas.oddities._expect_str`). The
# triplication is the deliberate cost of loader independence: each loader reads
# one file and depends on nothing else in atlas, so a defect in one table can
# never fail the load of another — and a fidelity finding about what counts as a
# string belongs in all three.
def _expect_str(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: expected a non-empty string, got {value!r}")
    return value


def load_system_ids(text: str | None = None) -> frozenset[str]:
    """Load the packaged id set (or *text* when supplied, for tests).

    Fail-closed, because every refusal here is a name a question could
    otherwise be asked about: an unreadable schema, an empty list, a non-string
    entry and a repeated id each fail the load rather than shipping a
    vocabulary whose own account of itself does not add up.
    """
    if text is None:
        text = (
            importlib.resources.files("atlas")
            .joinpath("data", "system_ids.json")
            .read_text(encoding="utf-8")
        )
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("schema") != SYSTEM_IDS_SCHEMA:
        raise ValueError(
            f"system_ids: unsupported schema "
            f"{raw.get('schema') if isinstance(raw, dict) else None!r} "
            f"(this atlas reads schema {SYSTEM_IDS_SCHEMA})"
        )
    ids = raw.get("systems")
    if not isinstance(ids, list) or not ids:
        raise ValueError("system_ids: 'systems' must be a non-empty list of canonical ids")
    systems = frozenset(_expect_str(name, "systems entry") for name in ids)
    if len(systems) != len(ids):
        raise ValueError("system_ids: 'systems' carries a duplicate id")
    return systems


_SYSTEM_IDS: frozenset[str] | None = None


def _ids() -> frozenset[str]:
    global _SYSTEM_IDS
    if _SYSTEM_IDS is None:
        _SYSTEM_IDS = load_system_ids()
    return _SYSTEM_IDS


def known_systems() -> tuple[str, ...]:
    """Every canonical system id, sorted — the vocabulary the questions take.

    The target set for a caller who maintains a mapping out of some other
    vocabulary: check yours against this and the day a name stops being an id
    is the day your own tests say so, instead of the day a question comes back
    empty for a system that is right there on the machine.
    """
    return tuple(sorted(_ids()))


def from_esde_system(name: str) -> str | None:
    """*name* if it is a canonical system id, else ``None``.

    ES-DE's names *are* the canonical ids, so this translates nothing — it
    answers "is this an id atlas can be asked about", which is the question a
    caller holding a name from anywhere else actually has. It is also the only
    honest way to check membership from outside: the id set is packaged data
    that moves with the frontend, never a literal a client should carry.

    >>> from_esde_system("dreamcast")
    'dreamcast'
    """
    return name if name in _ids() else None
