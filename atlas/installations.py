"""Installation handles — every question is asked *of an installation*.

Detection produces these; each one answers ``save_placement`` (and, for
RetroDECK, ``bios_dir`` / ``roms_dir``) for its own install flavor, reading the
configs that govern it through the injected reader. Three flavors carry
production-proven knowledge from decky-romm-sync:

- :class:`RetroDeck` — the RetroDECK Flatpak, whose ``retrodeck.json`` supplies
  the saves / ROMs / BIOS roots and whose bundled RetroArch supplies the layout.
- :class:`StandaloneRetroArchFlatpak` — the ``org.libretro.RetroArch`` Flatpak.
- :class:`NativeRetroArch` — a native ``~/.config/retroarch`` install.

The two bare-RetroArch flavors differ only in where their config lives, so they
share :class:`_RetroArchInstall`; RetroDECK differs enough (its own home and
path config) to stand alone.
"""

from __future__ import annotations

import json
import os
from typing import Any

from atlas.placement import SavePlacement, build_save_placement
from atlas.reader import Reader
from atlas.retroarch_cfg import interpret_cfg

# Config markers, as ``home``-relative suffixes.
RETRODECK_JSON_SUFFIX = os.path.join(
    ".var", "app", "net.retrodeck.retrodeck", "config", "retrodeck", "retrodeck.json"
)
RETRODECK_CFG_SUFFIX = os.path.join(
    ".var", "app", "net.retrodeck.retrodeck", "config", "retroarch", "retroarch.cfg"
)
STANDALONE_FLATPAK_CFG_SUFFIX = os.path.join(
    ".var", "app", "org.libretro.RetroArch", "config", "retroarch", "retroarch.cfg"
)
NATIVE_CFG_SUFFIX = os.path.join(".config", "retroarch", "retroarch.cfg")


def _parse_retrodeck_config(text: str | None) -> dict[str, Any]:
    """Parse ``retrodeck.json`` best-effort — malformed or absent yields ``{}``."""
    if text is None:
        return {}
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class RetroDeck:
    """A RetroDECK installation, rooted on its ``retrodeck.json`` path config."""

    kind = "retrodeck"

    def __init__(self, home: str, reader: Reader, retrodeck_json: str | None) -> None:
        self._home = home
        self._reader = reader
        self._config = _parse_retrodeck_config(retrodeck_json)

    def _config_path(self, key: str, fallback_subdir: str) -> tuple[str, str]:
        """Resolve a RetroDECK path and its provenance.

        Returns ``(path, source)``: the configured value from ``retrodeck.json``'s
        ``paths`` block, or the ``<home>/retrodeck/<subdir>`` fallback when the key
        is absent or blank — mirroring decky-romm-sync's best-effort resolution.
        """
        paths = self._config.get("paths")
        if isinstance(paths, dict):
            value = paths.get(key, "")
            if value:
                return value, f"retrodeck.json: paths.{key}"
        fallback = os.path.join(self._home, "retrodeck", fallback_subdir) if fallback_subdir else os.path.join(
            self._home, "retrodeck"
        )
        return fallback, f"default: {key} unset → {fallback}"

    def root(self) -> str:
        """The RetroDECK home directory (``rd_home_path`` or the fallback)."""
        return self._config_path("rd_home_path", "")[0]

    def saves_root(self) -> str:
        """The RetroDECK saves root (``saves_path`` or the fallback)."""
        return self._config_path("saves_path", "saves")[0]

    def bios_dir(self) -> str:
        """The RetroDECK BIOS directory (``bios_path`` or the fallback)."""
        return self._config_path("bios_path", "bios")[0]

    def roms_dir(self) -> str:
        """The RetroDECK ROMs directory (``roms_path`` or the fallback)."""
        return self._config_path("roms_path", "roms")[0]

    def save_placement(
        self, system: str, core: str | None = None, rom_dir_name: str | None = None
    ) -> SavePlacement:
        """Where RetroDECK's RetroArch keeps the save for a ROM of *system*."""
        saves_root, saves_source = self._config_path("saves_path", "saves")
        cfg_path = os.path.join(self._home, RETRODECK_CFG_SUFFIX)
        cfg = interpret_cfg(self._reader.read_text(cfg_path), home=self._home, cfg_label="retroarch.cfg")
        return build_save_placement(
            saves_root=saves_root,
            savefiles_in_content_dir=cfg.savefiles_in_content_dir,
            sort_by_content=cfg.sort_by_content,
            sort_by_core=cfg.sort_by_core,
            core=core,
            rom_dir_name=rom_dir_name,
            sources=(saves_source, *cfg.sources),
        )


class _RetroArchInstall:
    """Shared behavior for a bare RetroArch install (standalone Flatpak or native).

    The saves root comes from the cfg's ``savefile_directory`` (an unfilled
    ``<savefile_directory>`` hole when unset), not from a RetroDECK path config.
    Subclasses set ``kind`` and their config path; this base owns the rest.
    """

    def __init__(self, home: str, reader: Reader, cfg_suffix: str) -> None:
        self._home = home
        self._reader = reader
        self._cfg_suffix = cfg_suffix

    def _cfg_path(self) -> str:
        return os.path.join(self._home, self._cfg_suffix)

    def root(self) -> str:
        """The RetroArch config directory (the folder holding ``retroarch.cfg``)."""
        return os.path.dirname(self._cfg_path())

    def save_placement(
        self, system: str, core: str | None = None, rom_dir_name: str | None = None
    ) -> SavePlacement:
        """Where this RetroArch install keeps the save for a ROM of *system*."""
        cfg = interpret_cfg(self._reader.read_text(self._cfg_path()), home=self._home, cfg_label="retroarch.cfg")
        return build_save_placement(
            saves_root=cfg.savefile_directory,
            savefiles_in_content_dir=cfg.savefiles_in_content_dir,
            sort_by_content=cfg.sort_by_content,
            sort_by_core=cfg.sort_by_core,
            core=core,
            rom_dir_name=rom_dir_name,
            sources=cfg.sources,
        )


class StandaloneRetroArchFlatpak(_RetroArchInstall):
    """The ``org.libretro.RetroArch`` Flatpak install."""

    kind = "standalone_retroarch_flatpak"

    def __init__(self, home: str, reader: Reader) -> None:
        super().__init__(home, reader, STANDALONE_FLATPAK_CFG_SUFFIX)


class NativeRetroArch(_RetroArchInstall):
    """A native ``~/.config/retroarch`` install."""

    kind = "native_retroarch"

    def __init__(self, home: str, reader: Reader) -> None:
        super().__init__(home, reader, NATIVE_CFG_SUFFIX)


Installation = RetroDeck | StandaloneRetroArchFlatpak | NativeRetroArch
