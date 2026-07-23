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
    or ``None`` when every layer leaves it unset (absent, blank, or
    ``"default"``). Unset is **not** an unfilled hole: RetroArch resolves an
    empty save dir to the ROM's own directory (``runloop.c:8786``) — the caller
    applies that rule. ``sources`` records, per governing key, which file (or
    default) produced the value; when an override won, the source names it.
    """

    savefiles_in_content_dir: bool
    sort_by_content: bool
    sort_by_core: bool
    savefile_directory: str | None
    sources: tuple[str, ...]


def parse_cfg_text(text: str) -> dict[str, str]:
    """Parse ``key = "value"`` lines into a dict, exact-keyed and quote-stripped.

    RetroArch's cfg is ``key = "value"`` per line. Lines without ``=`` are
    ignored; the LHS is matched exactly (not by prefix) so ``savefile_directory``
    and ``savefiles_in_content_dir`` never collide. Matching outer double quotes
    are stripped; a later occurrence of a key wins.
    """
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        result[key] = value
    return result


def _as_bool(value: str) -> bool:
    return value.strip().lower() == "true"


def _expand_home(raw: str, *, home: str) -> str | None:
    """Expand ``~`` against *home*; map blank / ``"default"`` to ``None`` (unset)."""
    stripped = raw.strip()
    if stripped == "" or stripped.lower() == "default":
        return None
    if stripped == "~":
        return home
    if stripped.startswith("~/"):
        return home + stripped[1:]
    return stripped


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
    layers: list[tuple[str, dict[str, str], bool]] = [
        (cfg_label, parse_cfg_text(global_text) if global_text is not None else {}, False)
    ]
    layers.extend((label, parse_cfg_text(text), True) for label, text in overrides)

    def _flag(key: str, default: bool) -> tuple[bool, str]:
        value, source = default, f"default: {key} = {str(default).lower()} ({defaults.label})"
        for label, parsed, is_override in layers:
            if key in parsed:
                value = _as_bool(parsed[key])
                source = f'{label}: {key} = "{parsed[key]}"' + (" (override wins)" if is_override else "")
        return value, source

    in_content_dir, s1 = _flag(_IN_CONTENT_DIR, defaults.savefiles_in_content_dir)
    sort_by_content, s2 = _flag(_SORT_BY_CONTENT, defaults.sort_by_content)
    sort_by_core, s3 = _flag(_SORT_BY_CORE, defaults.sort_by_core)

    savefile_directory: str | None = None
    dir_source = f"default: {_SAVEFILE_DIRECTORY} unset (RetroArch resolves an unset save dir to the ROM's directory)"
    for label, parsed, is_override in layers:
        if _SAVEFILE_DIRECTORY in parsed:
            raw = parsed[_SAVEFILE_DIRECTORY]
            savefile_directory = _expand_home(raw, home=home)
            suffix = " (override wins)" if is_override else ""
            if savefile_directory is None:
                dir_source = f'{label}: {_SAVEFILE_DIRECTORY} = "{raw}" (unset — resolves to the ROM\'s directory){suffix}'
            else:
                dir_source = f'{label}: {_SAVEFILE_DIRECTORY} = "{raw}"{suffix}'

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
