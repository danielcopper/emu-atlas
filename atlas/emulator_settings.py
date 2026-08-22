"""Where a standalone emulator keeps a settings file — stated once, read by every route.

Four questions read one emulator's settings file, and until this table each of
them carried the address: a save card, a texture card and a mod card between
them named one path up to three times, with a fourth copy as a constant in the
firmware resolver. Two of those statements had already drifted in shipped
releases — one route reading the config home while another read the data home
for the same file (#250, #256) — and nothing could notice, because nothing
said the two were the same file.

So a card names a file by **name** and the address lives here. What is here is
only *where*: the bases a launch may pick, in the order it picks them, and the
path below. What the file means for a question stays with the card and the
code that reads it — which keys govern a save tree, whether a switch exists,
how a legacy file is migrated.
"""

from __future__ import annotations

import importlib.resources
import json
import os
from dataclasses import dataclass
from typing import Any

EMULATOR_SETTINGS_SCHEMA = 1

_BASES = ("config", "data")


@dataclass(frozen=True, slots=True)
class SettingsFile:
    """One settings file of one emulator: its name, and where a launch opens it.

    ``bases`` holds more than one entry exactly where the root is a property
    of the launch rather than of the emulator — DuckStation's DataRoot is the
    config home where ``XDG_CONFIG_HOME`` is set and absolute and the data
    home otherwise — and a reader probes them in this order.
    """

    token: str
    name: str
    bases: tuple[str, ...]
    path: str
    citation: str

    def locations(self, *, config_home: str, data_home: str) -> tuple[str, ...]:
        """The absolute candidates, in probe order, against one launch's XDG pair.

        The pair is the launch's own — on EmuDeck the picked binary's, which
        is why this takes two homes rather than reading an arrangement's.
        """
        homes = {"config": config_home, "data": data_home}
        return tuple(os.path.join(homes[base], self.path) for base in self.bases)

    def only(self, *, config_home: str, data_home: str) -> str:
        """The single location, for a file whose root does not vary.

        Raises for a file with several candidates rather than silently
        answering the first: a caller that cannot probe must not be handed a
        guess dressed as an address.
        """
        if len(self.bases) != 1:
            raise ValueError(
                f"settings file {self.token}/{self.name} states {len(self.bases)} bases — "
                "its location is decided by the launch, so a caller must probe them in order"
            )
        return self.locations(config_home=config_home, data_home=data_home)[0]


def _file(token: str, name: str, entry: Any) -> SettingsFile:
    where = f"emulator settings {token}/{name}"
    if not isinstance(entry, dict) or set(entry) != {"bases", "path", "citation"}:
        raise ValueError(f"{where}: expected exactly bases/path/citation, got {entry!r}")
    bases = entry["bases"]
    if not isinstance(bases, list) or not bases:
        raise ValueError(f"{where}.bases must be a non-empty list, got {bases!r}")
    for base in bases:
        if base not in _BASES:
            raise ValueError(f"{where}.bases: must be one of {list(_BASES)}, got {base!r}")
    if len(set(bases)) != len(bases):
        raise ValueError(f"{where}.bases repeats a base, so a probe would read one twice")
    path = entry["path"]
    if not isinstance(path, str) or not path or path.startswith("/") or ".." in path.split("/"):
        raise ValueError(f"{where}.path must be a relative path below the base, got {path!r}")
    citation = entry["citation"]
    if not isinstance(citation, str) or not citation:
        raise ValueError(f"{where}.citation: expected a non-empty string, got {citation!r}")
    if os.path.basename(path) != name:
        raise ValueError(
            f"{where}: the key is the file's own name, and {path!r} ends in "
            f"{os.path.basename(path)!r} — two spellings of one file is what this table exists "
            "to prevent"
        )
    return SettingsFile(
        token=token, name=name, bases=tuple(bases), path=path, citation=citation
    )


def load_emulator_settings(text: str | None = None) -> dict[str, dict[str, SettingsFile]]:
    """Load the packaged table (or *text* when supplied, for tests)."""
    if text is None:
        text = (
            importlib.resources.files("atlas")
            .joinpath("data", "emulator_settings.json")
            .read_text(encoding="utf-8")
        )
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("schema") != EMULATOR_SETTINGS_SCHEMA:
        raise ValueError(
            f"emulator_settings: unsupported schema "
            f"{raw.get('schema') if isinstance(raw, dict) else None!r} "
            f"(this atlas reads schema {EMULATOR_SETTINGS_SCHEMA})"
        )
    table: dict[str, dict[str, SettingsFile]] = {}
    for token, entry in raw.get("emulators", {}).items():
        if not isinstance(entry, dict) or not isinstance(entry.get("files"), dict):
            raise ValueError(f"emulator settings {token}: expected a 'files' object, got {entry!r}")
        files = entry["files"]
        if not files:
            raise ValueError(f"emulator settings {token}: states no file at all")
        table[token] = {name: _file(token, name, spec) for name, spec in files.items()}
    return table


_PACKAGED: dict[str, dict[str, SettingsFile]] | None = None


def settings_file(token: str | None, name: str) -> SettingsFile:
    """The named settings file of one emulator — loudly, or not at all.

    A card naming a file this table does not carry is a build mistake in the
    same class the card loaders already refuse: the two shipped out of step,
    and answering from a path nobody stated would be the exact failure this
    table exists to remove.
    """
    global _PACKAGED
    if _PACKAGED is None:
        _PACKAGED = load_emulator_settings()
    files = _PACKAGED.get(token or "")
    if files is None or name not in files:
        raise ValueError(
            f"no settings file {name!r} is stated for {token!r} — a card names it and "
            "atlas/data/emulator_settings.json does not carry it"
        )
    return files[name]


def settings_files(token: str | None) -> dict[str, SettingsFile]:
    """Every settings file stated for one emulator, or an empty mapping."""
    global _PACKAGED
    if _PACKAGED is None:
        _PACKAGED = load_emulator_settings()
    return dict(_PACKAGED.get(token or "", {}))
