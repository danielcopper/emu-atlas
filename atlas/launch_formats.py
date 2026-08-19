"""Install-first formats — the launchability question's world knowledge (issue #36).

A launchability 'no' has more than one meaning, and the accept-list can only
state one of them: the frontend will not scan this file. For some files the
honest continuation is "because it is an installer" — a PSN ``.pkg`` is the
distribution form of the content itself, and the emulator has to install it
before anything can launch. That fact is written nowhere on the machine, so
it lives here: marked, versioned, cited, keyed by the atlas system id and the
exact extension token ES-DE would derive. The resolver consults it only where
the extension is already outside the machine's own accept-list — a read is
never overridden by a table.
"""

from __future__ import annotations

import importlib.resources
import json
from dataclasses import dataclass
from typing import Any

FORMATS_SCHEMA = 1


@dataclass(frozen=True, slots=True)
class InstallFirstFormat:
    """One format that needs an installation step before anything can launch."""

    system: str
    extension: str
    statement: str
    source: str


def _expect_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: expected a non-empty string, got {value!r}")
    return value


def load_launch_formats(text: str | None = None) -> tuple[InstallFirstFormat, ...]:
    """Load the packaged install-first formats (or *text* when supplied, for tests)."""
    if text is None:
        text = (
            importlib.resources.files("atlas")
            .joinpath("data", "launch_formats.json")
            .read_text(encoding="utf-8")
        )
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("schema") != FORMATS_SCHEMA:
        raise ValueError(
            f"launch_formats: unsupported schema "
            f"{raw.get('schema') if isinstance(raw, dict) else None!r} "
            f"(this atlas reads schema {FORMATS_SCHEMA})"
        )
    systems = raw.get("systems", {})
    if not isinstance(systems, dict):
        raise ValueError(f"launch_formats: systems must be an object, got {systems!r}")
    formats: list[InstallFirstFormat] = []
    for system, entries in systems.items():
        where = f"launch format system {system!r}"
        if not isinstance(entries, dict) or not entries:
            raise ValueError(f"{where}: expected a non-empty object, got {entries!r}")
        for extension, entry in entries.items():
            formats.append(_format(system, extension, entry, f"{where}: {extension!r}"))
    return tuple(formats)


def _format(system: str, extension: str, entry: Any, at: str) -> InstallFirstFormat:
    """One record — validated, never coerced."""
    if not extension.startswith(".") or extension == ".":
        # The key must be a token esde_extension() can ever answer: every
        # derived extension starts with the dot it was cut at, and the
        # bare-dot sentinel names "no extension", which is not a format.
        raise ValueError(f"{at}: an extension token starts with '.' and names one")
    if not isinstance(entry, dict) or set(entry) != {"statement", "source"}:
        raise ValueError(f"{at}: an entry names exactly 'statement' and 'source', got {entry!r}")
    return InstallFirstFormat(
        system=system,
        extension=extension,
        statement=_expect_str(entry["statement"], f"{at}: statement"),
        source=_expect_str(entry["source"], f"{at}: source"),
    )


_PACKAGED: tuple[InstallFirstFormat, ...] | None = None


def lookup_install_first(system: str, extension: str) -> InstallFirstFormat | None:
    """The packaged record for one (system, extension) — exact match, or ``None``."""
    global _PACKAGED
    if _PACKAGED is None:
        _PACKAGED = load_launch_formats()
    return next(
        (f for f in _PACKAGED if f.system == system and f.extension == extension), None
    )
