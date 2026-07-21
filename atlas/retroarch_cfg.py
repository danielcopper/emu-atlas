"""Pure interpretation of ``retroarch.cfg`` — the save-layout keys and their defaults.

RetroArch's on-disk save layout is not a static path: it is governed by live
config values. This module reads the four keys that decide it out of the cfg
text and reports both the resolved value and the provenance of each — which came
from the file and which fell back to a default.

The three save-layout keys are extraction-faithful to decky-romm-sync
(``adapters/retroarch_config.py``):

- ``savefiles_in_content_dir`` — saves live next to the ROM (default ``false``).
- ``sort_savefiles_by_content_enable`` — a per-content subdirectory (default
  ``true``, the RetroDECK default decky applies to every flavor it probes).
- ``sort_savefiles_enable`` — a per-core subdirectory (default ``false``).

The fourth key, ``savefile_directory``, is *not* extracted from decky: decky gets
its saves root from RetroDECK's ``retrodeck.json`` and never needs the cfg value.
Standalone RetroArch installs have no RetroDECK config, so their saves root comes
from this key. It is read minimally and honestly — ``~`` is expanded against the
machine's home, and an absent value or the literal ``"default"`` is reported as
*unset* (``savefile_directory is None``), never guessed at.

Pure text in, value object out. No I/O — the reader supplies the text.
"""

from __future__ import annotations

from dataclasses import dataclass

_IN_CONTENT_DIR = "savefiles_in_content_dir"
_SORT_BY_CONTENT = "sort_savefiles_by_content_enable"
_SORT_BY_CORE = "sort_savefiles_enable"
_SAVEFILE_DIRECTORY = "savefile_directory"

# decky's per-flavor defaults, applied when a key line is absent from the cfg.
_DEFAULT_IN_CONTENT_DIR = False
_DEFAULT_SORT_BY_CONTENT = True
_DEFAULT_SORT_BY_CORE = False


@dataclass(frozen=True, slots=True)
class RetroArchCfg:
    """The save-layout decision read from ``retroarch.cfg``, with provenance.

    ``savefile_directory`` is the raw saves-root value with ``~`` expanded, or
    ``None`` when the cfg leaves it unset (absent, blank, or ``"default"``) —
    an unfilled ``<savefile_directory>`` hole for the caller to resolve.
    ``sources`` records, per governing key, whether the value came from the file
    or from a default.
    """

    savefiles_in_content_dir: bool
    sort_by_content: bool
    sort_by_core: bool
    savefile_directory: str | None
    sources: tuple[str, ...]


def _parse_lines(text: str) -> dict[str, str]:
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


def _resolve_savefile_directory(raw: str | None, *, home: str) -> str | None:
    """Expand ``~`` against *home*; map absent / blank / ``"default"`` to ``None``."""
    if raw is None:
        return None
    stripped = raw.strip()
    if stripped == "" or stripped.lower() == "default":
        return None
    if stripped == "~":
        return home
    if stripped.startswith("~/"):
        return home + stripped[1:]
    return stripped


def interpret_cfg(text: str | None, *, home: str, cfg_label: str) -> RetroArchCfg:
    """Interpret ``retroarch.cfg`` text into the save-layout decision.

    Parameters
    ----------
    text:
        The cfg file's content, or ``None`` when no cfg was found. ``None`` and
        an empty file both yield the all-defaults decision.
    home:
        The machine home, used to expand a leading ``~`` in ``savefile_directory``.
    cfg_label:
        A human-readable label for the cfg source (its path), woven into the
        provenance strings so a consumer can see which file governed each value.
    """
    parsed = _parse_lines(text) if text is not None else {}
    sources: list[str] = []

    def _flag(key: str, default: bool) -> bool:
        if key in parsed:
            resolved = _as_bool(parsed[key])
            sources.append(f'{cfg_label}: {key} = "{parsed[key]}"')
            return resolved
        sources.append(f"default: {key} = {str(default).lower()} (RetroDECK default)")
        return default

    in_content_dir = _flag(_IN_CONTENT_DIR, _DEFAULT_IN_CONTENT_DIR)
    sort_by_content = _flag(_SORT_BY_CONTENT, _DEFAULT_SORT_BY_CONTENT)
    sort_by_core = _flag(_SORT_BY_CORE, _DEFAULT_SORT_BY_CORE)

    raw_savefile_dir = parsed.get(_SAVEFILE_DIRECTORY)
    savefile_directory = _resolve_savefile_directory(raw_savefile_dir, home=home)
    if raw_savefile_dir is None:
        sources.append(f"default: {_SAVEFILE_DIRECTORY} unset (unfilled <savefile_directory> hole)")
    elif savefile_directory is None:
        sources.append(f'{cfg_label}: {_SAVEFILE_DIRECTORY} = "{raw_savefile_dir}" (unfilled <savefile_directory> hole)')
    else:
        sources.append(f'{cfg_label}: {_SAVEFILE_DIRECTORY} = "{raw_savefile_dir}"')

    return RetroArchCfg(
        savefiles_in_content_dir=in_content_dir,
        sort_by_content=sort_by_content,
        sort_by_core=sort_by_core,
        savefile_directory=savefile_directory,
        sources=tuple(sources),
    )
