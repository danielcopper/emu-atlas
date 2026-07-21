"""The reader seam — the one narrow protocol every filesystem access flows through.

atlas never touches the disk directly: detection, config parsing, and override
chains all ask a :class:`Reader`. In production that reader is the real
filesystem (:class:`FilesystemReader`); in tests and conformance vectors it is a
fixture tree (:class:`FixtureReader`) — a plain mapping of absolute paths to file
contents describing a whole machine. One code path, two data sources, so
everything atlas concludes is provable from data.
"""

from __future__ import annotations

import fnmatch
import glob as _glob
import os
from typing import Protocol


class Reader(Protocol):
    """Narrow filesystem port: read a file, glob a pattern, test existence.

    ``read_text`` returns ``None`` when the path does not exist or cannot be
    read as text — callers treat "absent" and "unreadable" the same way at this
    seam (a missing config and an unreadable one both mean "no value here").
    ``glob`` returns the matching paths sorted, so results are deterministic.
    """

    def read_text(self, path: str) -> str | None: ...

    def glob(self, pattern: str) -> list[str]: ...

    def exists(self, path: str) -> bool: ...


class FilesystemReader:
    """The production reader: the real filesystem."""

    def read_text(self, path: str) -> str | None:
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return None

    def glob(self, pattern: str) -> list[str]:
        return sorted(_glob.glob(pattern, recursive=True))

    def exists(self, path: str) -> bool:
        return os.path.exists(path)


class FixtureReader:
    """A reader backed by an in-memory ``{path: content}`` mapping.

    The mapping describes a whole machine. ``read_text`` returns the stored
    content or ``None`` for an unknown path; ``exists`` reports membership;
    ``glob`` matches the mapping's keys with :func:`fnmatch.fnmatch` — enough
    for the directory-and-suffix patterns detection actually issues, no more.
    """

    def __init__(self, files: dict[str, str]) -> None:
        self._files = dict(files)

    def read_text(self, path: str) -> str | None:
        return self._files.get(path)

    def glob(self, pattern: str) -> list[str]:
        return sorted(p for p in self._files if fnmatch.fnmatch(p, pattern))

    def exists(self, path: str) -> bool:
        return path in self._files
