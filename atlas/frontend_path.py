"""The PATH a frontend on the host searches — the find-rules lookup's world knowledge (#84 part 3).

ES-DE searches a ``systempath`` find rule over the directories of its own
process's ``PATH``, and a frontend on the host gets that ``PATH`` from the
environment it was started in, which no file on the machine states. So the
list is packaged here, keyed by the frontend's installation kind, each
directory with the citation of the line that puts it there
(``atlas/data/frontend_path.json``). An answer that searched it says so: the
lookup carries ``launch-path-assumed`` beside its verdict.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ._data import packaged_text

FRONTEND_PATH_SCHEMA = 1

# A directory under the user's home is spelled with this prefix in the table,
# because the home is the machine's and the table is not.
_HOME_PREFIX = "~/"


@dataclass(frozen=True, slots=True)
class PathDirectory:
    """One directory of an assumed ``PATH``, and the line that puts it there."""

    directory: str
    citation: str


def load_frontend_path(text: str | None = None) -> Mapping[str, tuple[PathDirectory, ...]]:
    """Load the packaged search paths by frontend (or *text* when supplied, for tests)."""
    if text is None:
        text = packaged_text("frontend_path.json")
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("schema") != FRONTEND_PATH_SCHEMA:
        raise ValueError(
            f"frontend_path: unsupported schema "
            f"{raw.get('schema') if isinstance(raw, dict) else None!r} "
            f"(this atlas reads schema {FRONTEND_PATH_SCHEMA})"
        )
    frontends = raw.get("frontends")
    if not isinstance(frontends, dict) or not frontends:
        raise ValueError(f"frontend_path: expected a non-empty 'frontends' object, got {frontends!r}")
    return {name: _search_path(name, entry) for name, entry in frontends.items()}


def _search_path(name: str, entry: Any) -> tuple[PathDirectory, ...]:
    where = f"frontend_path {name!r}"
    if not isinstance(entry, dict) or set(entry) != {"search_path"}:
        raise ValueError(f"{where}: an entry names exactly 'search_path', got {entry!r}")
    directories = entry["search_path"]
    if not isinstance(directories, list) or not directories:
        raise ValueError(f"{where}: search_path must be a non-empty list")
    return tuple(_directory(f"{where} search_path[{index}]", item) for index, item in enumerate(directories))


def _directory(where: str, item: Any) -> PathDirectory:
    """One directory — validated, never coerced: a spelling the lookup can join a rule entry to, and its citation."""
    if not isinstance(item, dict) or set(item) != {"directory", "citation"}:
        raise ValueError(f"{where}: a directory names exactly 'directory' and 'citation', got {item!r}")
    directory = item["directory"]
    citation = item["citation"]
    if not isinstance(directory, str) or not (directory.startswith("/") or directory.startswith(_HOME_PREFIX)):
        raise ValueError(f"{where}: a directory is absolute or under ~/, got {directory!r}")
    if not isinstance(citation, str) or not citation.strip():
        raise ValueError(f"{where}: every directory carries a citation")
    return PathDirectory(directory, citation)


_PACKAGED: Mapping[str, tuple[PathDirectory, ...]] | None = None


def frontend_search_path(frontend: str, home: str) -> tuple[str, ...]:
    """The packaged ``PATH`` of *frontend*'s process, in order, with ``~/`` expanded against *home*.

    Raises ``KeyError`` for a frontend the table does not carry: only a
    handle whose frontend has an entry asks.
    """
    global _PACKAGED
    if _PACKAGED is None:
        _PACKAGED = load_frontend_path()
    return tuple(_expanded(entry.directory, home) for entry in _PACKAGED[frontend])


def _expanded(directory: str, home: str) -> str:
    if directory.startswith(_HOME_PREFIX):
        return os.path.join(home, directory[len(_HOME_PREFIX) :])
    return directory
