"""Interpretation of ``retroarch.cfg`` and its override chain — the save-layout keys.

RetroArch's on-disk save layout is not a static path: it is governed by live
config values, resolved through a four-layer chain in which later files win
(``config_load_override()``, RetroArch ``configuration.c:7095``):

1. ``retroarch.cfg`` — global
2. ``config/<library_name>/<library_name>.cfg`` — core override
3. ``config/<library_name>/<content_dir>.cfg`` — content-dir override
4. ``config/<library_name>/<rom_name>.cfg`` — game override

This module resolves the four governing keys through that chain and reports both
the resolved value and the provenance of each — which file won, which default
applied. Defaults differ per install flavor and are passed in as
:class:`LayoutDefaults`; the shipped sets below are read from the respective
upstream sources, version-pinned in ``docs/research/retrodeck-save-placement.md``.

Pure text in, value object out. No I/O — the machine seam supplies the texts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

_IN_CONTENT_DIR = "savefiles_in_content_dir"
_SORT_BY_CONTENT = "sort_savefiles_by_content_enable"
_SORT_BY_CORE = "sort_savefiles_enable"
_SAVEFILE_DIRECTORY = "savefile_directory"


@dataclass(frozen=True, slots=True)
class LayoutDefaults:
    """The per-flavor defaults applied when a key is absent from every layer."""

    savefiles_in_content_dir: bool
    sort_by_content: bool
    sort_by_core: bool
    label: str


# RetroArch upstream compile-time defaults (config.def.h:982-989). Note that
# upstream sorts BY CORE by default — a bare install without the key set puts
# saves in per-library_name subdirectories.
UPSTREAM_DEFAULTS = LayoutDefaults(
    savefiles_in_content_dir=False,
    sort_by_content=False,
    sort_by_core=True,
    label="RetroArch upstream default (config.def.h)",
)

# RetroDECK's shipped retroarch.cfg (components/retroarch/rd_config/retroarch.cfg).
RETRODECK_DEFAULTS = LayoutDefaults(
    savefiles_in_content_dir=False,
    sort_by_content=True,
    sort_by_core=False,
    label="RetroDECK shipped default",
)

# EmuDeck's shipped cfg for org.libretro.RetroArch
# (configs/org.libretro.RetroArch/config/retroarch/retroarch.cfg): flat layout.
EMUDECK_DEFAULTS = LayoutDefaults(
    savefiles_in_content_dir=False,
    sort_by_content=False,
    sort_by_core=False,
    label="EmuDeck shipped default",
)


@dataclass(frozen=True, slots=True)
class RetroArchCfg:
    """The save-layout decision resolved through the override chain, with provenance.

    ``savefile_directory`` is the resolved saves-root value with ``~`` expanded,
    or ``None`` when the platform default applies (key absent, blank, or the
    literal ``"default"``). RetroArch initializes platform default directories
    before applying the config — on desktop Unix the SRAM default is ``saves``
    under the RetroArch config tree (``platform_unix.c:2133-2134``; that tree is
    ``$XDG_CONFIG_HOME/retroarch`` or ``$HOME/.config/retroarch``,
    ``platform_unix.c:1943-1957``) — so an unset key means *that* directory,
    never the ROM's directory (the ``runloop.c:8786`` content fallback fires
    only when the effective dir is still empty, which the platform defaults
    prevent on desktop). The caller supplies the concrete platform default.
    ``sources`` records, per governing key, which file (or default) produced
    the value; when an override won, it names it.
    """

    savefiles_in_content_dir: bool
    sort_by_content: bool
    sort_by_core: bool
    savefile_directory: str | None
    sources: tuple[str, ...]


def _strip_trailing_comment(value: str) -> str:
    """Drop a trailing ``#`` comment that sits outside the quoted value."""
    in_quotes = False
    for index, char in enumerate(value):
        if char == '"':
            in_quotes = not in_quotes
        elif char == "#" and not in_quotes:
            return value[:index].rstrip()
    return value


def parse_cfg_text(text: str) -> dict[str, str]:
    """Parse ``key = "value"`` lines the way RetroArch's ``config_file.c`` does.

    Semantics matched against upstream: comment lines (leading ``#``) are
    skipped and trailing comments outside the quotes are stripped
    (``config_file_strip_comment``); the **first** occurrence of a duplicate
    key wins (``config_file.c:496-507`` maps a key only when not already
    present); matching outer double quotes are stripped. The LHS is matched
    exactly (not by prefix) so ``savefile_directory`` and
    ``savefiles_in_content_dir`` never collide. ``#include`` directives are NOT
    resolved yet — a stated gap (task list), not an approximation.
    """
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in result:
            continue
        value = _strip_trailing_comment(value.strip())
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        result[key] = value
    return result


def _as_bool(value: str) -> bool:
    # config_get_bool accepts "true" and "1" (config_file.c:1233).
    stripped = value.strip()
    return stripped == "1" or stripped.lower() == "true"


def expand_home(raw: str, *, home: str) -> str | None:
    """Expand ``~`` against *home*; map blank / ``"default"`` to ``None`` (unset)."""
    stripped = raw.strip()
    if stripped == "" or stripped.lower() == "default":
        return None
    if stripped == "~":
        return home
    if stripped.startswith("~/"):
        return home + stripped[1:]
    return stripped


# One layer of the chain: its provenance label, its parsed keys, and whether it
# is an override (the global cfg is not).
_Layer = tuple[str, dict[str, str], bool]


def _resolve_flag(layers: Sequence[_Layer], key: str, *, default: bool, defaults_label: str) -> tuple[bool, str]:
    """One boolean key through the chain — later layers win, provenance follows."""
    value, source = default, f"default: {key} = {str(default).lower()} ({defaults_label})"
    for label, parsed, is_override in layers:
        if key in parsed:
            value = _as_bool(parsed[key])
            source = f'{label}: {key} = "{parsed[key]}"' + (" (override wins)" if is_override else "")
    return value, source


def _resolve_savefile_directory(layers: Sequence[_Layer], *, home: str) -> tuple[str | None, str]:
    """The saves root through the chain — ``None`` when the platform default applies."""
    savefile_directory: str | None = None
    source = (
        f"default: {_SAVEFILE_DIRECTORY} unset — RetroArch platform default applies "
        "(saves under the config tree, platform_unix.c:2133-2134)"
    )
    for label, parsed, is_override in layers:
        if _SAVEFILE_DIRECTORY in parsed:
            raw = parsed[_SAVEFILE_DIRECTORY]
            savefile_directory = expand_home(raw, home=home)
            suffix = " (override wins)" if is_override else ""
            if savefile_directory is None:
                source = (
                    f'{label}: {_SAVEFILE_DIRECTORY} = "{raw}" — resets to the RetroArch '
                    f"platform default{suffix}"
                )
            else:
                source = f'{label}: {_SAVEFILE_DIRECTORY} = "{raw}"{suffix}'
    return savefile_directory, source


def resolve_save_layout(
    global_text: str | None,
    *,
    home: str,
    cfg_label: str,
    defaults: LayoutDefaults,
    overrides: Sequence[tuple[str, str]] = (),
) -> RetroArchCfg:
    """Resolve the save layout through the override chain — later layers win.

    Parameters
    ----------
    global_text:
        The global cfg's content, or ``None`` when no cfg was found (``None``
        and an empty file both yield the all-defaults decision).
    home:
        The machine home, used to expand a leading ``~`` in ``savefile_directory``.
    cfg_label:
        Human-readable label for the global cfg, woven into provenance strings.
    defaults:
        The flavor's defaults, applied when no layer sets a key.
    overrides:
        ``(label, text)`` pairs in load order (core, content-dir, game) —
        exactly the files that exist, already read through the machine seam.
        Each layer overrides only the keys it actually sets.
    """
    layers: list[_Layer] = [
        (cfg_label, parse_cfg_text(global_text) if global_text is not None else {}, False)
    ]
    layers.extend((label, parse_cfg_text(text), True) for label, text in overrides)

    defaults_label = defaults.label
    in_content_dir, s1 = _resolve_flag(
        layers, _IN_CONTENT_DIR, default=defaults.savefiles_in_content_dir, defaults_label=defaults_label
    )
    sort_by_content, s2 = _resolve_flag(
        layers, _SORT_BY_CONTENT, default=defaults.sort_by_content, defaults_label=defaults_label
    )
    sort_by_core, s3 = _resolve_flag(
        layers, _SORT_BY_CORE, default=defaults.sort_by_core, defaults_label=defaults_label
    )
    savefile_directory, dir_source = _resolve_savefile_directory(layers, home=home)

    return RetroArchCfg(
        savefiles_in_content_dir=in_content_dir,
        sort_by_content=sort_by_content,
        sort_by_core=sort_by_core,
        savefile_directory=savefile_directory,
        sources=(s1, s2, s3, dir_source),
    )


def interpret_cfg(
    text: str | None, *, home: str, cfg_label: str, defaults: LayoutDefaults = RETRODECK_DEFAULTS
) -> RetroArchCfg:
    """Interpret a single ``retroarch.cfg`` text (no overrides) — see :func:`resolve_save_layout`."""
    return resolve_save_layout(text, home=home, cfg_label=cfg_label, defaults=defaults)
