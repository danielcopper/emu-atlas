"""The BIOS registry — which firmware files each platform and core want.

The registry data (``data/bios_registry.json``) is copied verbatim from
decky-romm-sync; this module exposes the *registry semantics* that stand alone,
independent of decky's UI-status value objects:

- **entry lookup** per ``(platform slug, filename)``;
- **required classification** with the per-core override taking precedence over
  the entry's top-level ``required`` flag — extracted from decky's
  ``domain/bios.py`` ``classify_firmware_file``;
- a **required-set query** (``required_bios(platform, core=None)``).

The readiness/label formatting decky layers on top (``compute_bios_level`` and
friends) is a UI concern and deliberately does not live here.
"""

from __future__ import annotations

import importlib.resources
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class BiosEntry:
    """One firmware file the registry knows about, for one platform.

    ``required`` is the entry's top-level flag (the answer when no core is in
    play). ``cores`` maps a libretro core (``opera_libretro``) to whether *that*
    core requires the file — the per-core override. ``md5`` / ``sha1`` / ``size``
    are the file's identity, carried through verbatim; any may be absent.
    """

    file_name: str
    description: str
    required: bool
    firmware_path: str
    cores: dict[str, bool]
    md5: str | None
    sha1: str | None
    size: int | None


def _entry_from_raw(file_name: str, raw: dict[str, Any]) -> BiosEntry:
    raw_cores = raw.get("cores") or {}
    cores = {core_so: bool(info.get("required", True)) for core_so, info in raw_cores.items()}
    return BiosEntry(
        file_name=file_name,
        description=raw.get("description", file_name),
        required=bool(raw.get("required", True)),
        firmware_path=raw.get("firmware_path", file_name),
        cores=cores,
        md5=raw.get("md5"),
        sha1=raw.get("sha1"),
        size=raw.get("size"),
    )


class BiosRegistry:
    """Read-only view over the firmware registry: lookup and required-set queries."""

    def __init__(self, platforms: dict[str, dict[str, BiosEntry]], meta: dict[str, Any]) -> None:
        self._platforms = platforms
        self._meta = meta

    @property
    def meta(self) -> dict[str, Any]:
        """The registry's ``_meta`` block (generation source and version)."""
        return dict(self._meta)

    def platforms(self) -> tuple[str, ...]:
        """Every platform slug the registry covers, sorted."""
        return tuple(sorted(self._platforms))

    def files(self, platform: str) -> tuple[BiosEntry, ...]:
        """Every firmware entry for *platform* (empty tuple when unknown)."""
        return tuple(self._platforms.get(platform, {}).values())

    def entry(self, platform: str, file_name: str) -> BiosEntry | None:
        """The entry for ``(platform, file_name)``, or ``None`` when unknown."""
        return self._platforms.get(platform, {}).get(file_name)

    def is_required(self, platform: str, file_name: str, core: str | None = None) -> bool:
        """Whether ``file_name`` is required for *platform*, honoring the active core.

        The per-core override wins over the top-level flag: when *core* is given
        and the entry lists it, that core's requirement decides; when *core* is
        given but the entry lists other cores and not this one, the file is *not*
        required for it; when *core* is absent (or the entry carries no per-core
        table), the entry's top-level ``required`` flag decides. Extraction-faithful
        to decky's ``classify_firmware_file``. An unknown entry is not required.
        """
        entry = self.entry(platform, file_name)
        if entry is None:
            return False
        # Branching on a NON-EMPTY cores map is a deliberate simplification of
        # the source semantics (which branch on the key's presence): the shipped
        # registry contains no entry with a present-but-empty cores map, so the
        # two never diverge on real data. If a registry regeneration ever
        # introduces one, an empty map here falls back to the top-level flag.
        if core is not None and entry.cores:
            return entry.cores.get(core, False)
        return entry.required

    def required_bios(self, platform: str, core: str | None = None) -> tuple[BiosEntry, ...]:
        """The subset of *platform*'s entries required under *core* (or top-level)."""
        return tuple(entry for entry in self.files(platform) if self.is_required(platform, entry.file_name, core))


def load_registry(text: str | None = None) -> BiosRegistry:
    """Load the packaged registry (or *text* when supplied, for tests).

    With no argument the bundled ``data/bios_registry.json`` is read from the
    installed package. Reading packaged data is not the machine-filesystem seam
    the :class:`~atlas.reader.Reader` guards, so it does not route through a
    reader — it is the library reading its own bundled knowledge.
    """
    if text is None:
        text = importlib.resources.files("atlas").joinpath("data", "bios_registry.json").read_text(encoding="utf-8")
    data = json.loads(text)
    raw_platforms: dict[str, dict[str, Any]] = data.get("platforms", {})
    platforms = {
        slug: {file_name: _entry_from_raw(file_name, raw) for file_name, raw in files.items()}
        for slug, files in raw_platforms.items()
    }
    return BiosRegistry(platforms, data.get("_meta", {}))
