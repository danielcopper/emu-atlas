"""Save placements — resolved directories, honest file sets, and provenance.

A :class:`SavePlacement` answers "where does this emulator, configured as it is,
keep the save for this content?". Its shape follows the research findings
(``docs/research/retrodeck-save-placement.md`` §16):

- **Directory and file set are different kinds of knowledge.** The directory
  follows from RetroArch's central path rule and is always resolvable; the file
  set is per-core behaviour with no metadata source. For existing saves atlas
  *observes* the set (``glob("<rom_stem>.*")``); otherwise it is honestly
  ``unknown`` — never guessed. The old fixed ``<rom_stem>.srm`` filename is
  gone: ``.srm`` is only what RetroArch itself writes, and cores like Beetle
  Saturn write ``.bcr``/``.bkr``/``.smpc`` on their own.
- **A hole is not an unknown.** ``needs`` lists holes the caller fills from the
  content at hand (``content_dir``, ``library_name``); *unknown* means atlas
  cannot state the value and refuses to guess. Distinct states, kept distinct.
- **The root varies** — ``savefile_directory``, ``system_directory`` (Flycast
  VMUs), or the ROM's own directory (``savefiles_in_content_dir``, or an unset
  save dir, which RetroArch resolves itself: ``runloop.c:8786``).
- **Filesystem state is part of the answer** — RetroArch silently reverts to the
  unsorted root when a sorted directory cannot be created (``runloop.c:8844``);
  ``caveats`` carries that and every other stated degradation.

Pure compute. No I/O — the installation handles observe the machine and pass
the results in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from atlas.retroarch_cfg import RetroArchCfg

# Root kinds — where the placement's directory is anchored.
ROOT_SAVEFILE_DIRECTORY = "savefile_directory"
ROOT_CONTENT_DIRECTORY = "content_directory"
ROOT_SYSTEM_DIRECTORY = "system_directory"

# Caveat codes — the stable, machine-readable identifiers clients branch on.
# Part of the API contract; messages are for humans and may change freely.
CAVEAT_NO_CORE = "no-core"
CAVEAT_CORE_UNQUERYABLE = "core-unqueryable"
CAVEAT_SORTED_DIR_MISSING = "sorted-dir-missing"
CAVEAT_HEALTH = "health"
CAVEAT_FILENAMES_UNVERIFIED = "filenames-unverified"
CAVEAT_UNKNOWN_OPTION_VALUE = "unknown-option-value"
CAVEAT_SYSTEM_DIR_UNSET = "system-directory-unset"
CAVEAT_PER_GAME_OVERRIDES_PRESENT = "per-game-overrides-present"
CAVEAT_PER_GAME_OVERRIDE = "per-game-override"


@dataclass(frozen=True, slots=True)
class Caveat:
    """A stated degradation — structured, so clients can act on it.

    ``code`` is a stable identifier from the ``CAVEAT_*`` constants and part of
    the API contract: clients branch on it, vectors assert it. ``message`` is
    the human-readable explanation and may change freely. ``data`` carries the
    machine-readable specifics (e.g. the fallback directory of a silent
    revert). Decision-relevant → structured; explanatory → text.
    """

    code: str
    message: str
    data: dict[str, str] = field(default_factory=dict)

_HOLE_CONTENT_DIR = "content_dir"
_HOLE_LIBRARY_NAME = "library_name"


@dataclass(frozen=True, slots=True)
class FileSet:
    """The files a save consists of — observed, declared, or unknown.

    ``state`` is ``"observed"`` (``files`` are real basenames found on disk),
    ``"declared"`` (``files`` come from a verified rule card — world knowledge
    with cited provenance, not a guess), or ``"unknown"`` (``files`` is empty;
    atlas refuses to guess). ``source`` says how the state was reached.
    """

    state: str
    files: tuple[str, ...]
    source: str


@dataclass(frozen=True, slots=True)
class Granularity:
    """How this emulator, configured as it is, groups save data — and how to change it.

    ``value`` is the current granularity (``"shared-card"``,
    ``"per-game-file"``, …), selected by the live-read ``option_value`` of
    ``option_key``; ``option_source`` is its provenance (which file, or the
    core default). ``options_file`` is where a caller would change the option —
    change it, ask again, and the new answer confirms the switch.
    ``alternatives`` lists the other selectable ``(option_value, granularity)``
    pairs from the rule card.
    """

    value: str
    option_key: str
    option_value: str
    option_source: str
    options_file: str | None
    alternatives: tuple[tuple[str, str], ...]


UNKNOWN_FILE_SET = FileSet(
    state="unknown",
    files=(),
    source="file set not stated — no observation available (never guessed)",
)


@dataclass(frozen=True, slots=True)
class SavePlacement:
    """A resolved save location with provenance and stated degradations.

    ``dir`` is concrete when the caller supplied the content path; otherwise it
    is a template whose remaining holes are listed in ``needs``. ``root_kind``
    names the anchor (:data:`ROOT_SAVEFILE_DIRECTORY`,
    :data:`ROOT_CONTENT_DIRECTORY`, :data:`ROOT_SYSTEM_DIRECTORY`).
    ``file_set`` is observed or unknown, never guessed. ``sources`` is the
    provenance trail; ``caveats`` states every degradation explicitly.
    """

    dir: str
    root_kind: str
    needs: tuple[str, ...]
    file_set: FileSet
    sources: tuple[str, ...]
    caveats: tuple[Caveat, ...]
    granularity: Granularity | None = None


def build_save_placement(
    *,
    layout: RetroArchCfg,
    content_dir_path: str | None,
    content_dir_name: str | None,
    library_name: str | None,
    extra_sources: tuple[str, ...] = (),
    caveats: tuple[Caveat, ...] = (),
    file_set: FileSet = UNKNOWN_FILE_SET,
) -> SavePlacement:
    """Compose a :class:`SavePlacement` from a resolved layout and the caller's fills.

    ``content_dir_path`` / ``content_dir_name`` derive from the content path
    when the caller supplied one (the ROM's own directory and its basename);
    when absent the corresponding hole is left in the template and listed in
    ``needs``. ``library_name`` is the core's self-reported name (via
    ``query_core``); when the layout sorts by core and it is absent, the
    ``<library_name>`` hole remains.
    """
    needs: list[str] = []
    all_sources = list(layout.sources) + list(extra_sources)

    if layout.savefiles_in_content_dir or layout.savefile_directory is None:
        root_kind = ROOT_CONTENT_DIRECTORY
        if layout.savefiles_in_content_dir:
            all_sources.append("layout: saves live next to the ROM (savefiles_in_content_dir)")
        else:
            all_sources.append(
                "layout: savefile_directory unset — RetroArch resolves it to the ROM's directory (runloop.c:8786)"
            )
        if content_dir_path is not None:
            directory = content_dir_path
        else:
            directory = "<content_dir>"
            needs.append(_HOLE_CONTENT_DIR)
    else:
        root_kind = ROOT_SAVEFILE_DIRECTORY
        parts = [layout.savefile_directory]
        if layout.sort_by_content:
            if content_dir_name is not None:
                parts.append(content_dir_name)
            else:
                parts.append("<content_dir>")
                needs.append(_HOLE_CONTENT_DIR)
        if layout.sort_by_core:
            if library_name is not None:
                parts.append(library_name)
            else:
                parts.append("<library_name>")
                needs.append(_HOLE_LIBRARY_NAME)
        directory = os.path.join(*parts)

    return SavePlacement(
        dir=directory,
        root_kind=root_kind,
        needs=tuple(needs),
        file_set=file_set,
        sources=tuple(all_sources),
        caveats=tuple(caveats),
    )
