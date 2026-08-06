"""Save placements — resolved directories, honest file sets, and provenance.

A :class:`SavePlacement` answers "where does this emulator, configured as it is,
keep the save for this content?". Its shape follows the research findings
(``docs/research/retrodeck-save-placement.md`` §16):

- **Directory and file set are different kinds of knowledge.** The directory
  follows from RetroArch's central path rule and is always resolvable; the file
  set is per-core behaviour with no metadata source. For existing saves atlas
  *observes* the set (``glob("<rom_stem>.*")``); otherwise it is honestly
  ``unknown`` — never guessed. An observation in the ROM's own directory says so
  (``content-dir-observation``): there the content shares the name and no source
  tells the two apart. The old fixed ``<rom_stem>.srm`` filename is
  gone: ``.srm`` is only what RetroArch itself writes, and cores like Beetle
  Saturn write ``.bcr``/``.bkr``/``.smpc`` on their own.
- **A hole is not an unknown.** ``needs`` lists holes the caller fills from the
  content at hand (``content_dir``, ``library_name``, ``save_id``); *unknown*
  means atlas cannot state the value and refuses to guess. Distinct states,
  kept distinct. A hole is not confined to the directory: where a rule card
  names the save's files through the content's platform-native id, the file
  set is a template too and the same hole vocabulary carries it.
- **The root varies** — ``savefile_directory`` (explicit, or the RetroArch
  platform default when unset/reset), ``system_directory`` (Flycast VMUs), or
  the ROM's own directory (``savefiles_in_content_dir``). Sorting stages apply
  after root selection regardless of which root was chosen
  (``runloop.c:8785-8841``).
- **Filesystem state is part of the answer** — RetroArch silently reverts to the
  unsorted root when a sorted directory cannot be created (``runloop.c:8844``);
  ``caveats`` carries that and every other stated degradation.

Pure compute. No I/O — the installation handles observe the machine and pass
the results in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Iterable, Literal, Mapping

from atlas.retroarch_cfg import RetroArchCfg

# Root kinds — where the placement's directory is anchored. The closed
# vocabularies are Literal types so an invalid state is a type error first
# and a constructor error second (REVIEW M10).
RootKind = Literal["savefile_directory", "content_directory", "system_directory"]
FileSetState = Literal["observed", "declared", "unknown"]

ROOT_SAVEFILE_DIRECTORY: RootKind = "savefile_directory"
ROOT_CONTENT_DIRECTORY: RootKind = "content_directory"
ROOT_SYSTEM_DIRECTORY: RootKind = "system_directory"

_ROOT_KINDS = ("savefile_directory", "content_directory", "system_directory")
_FILE_SET_STATES = ("observed", "declared", "unknown")


def _freeze(mapping: Mapping[str, str]) -> Mapping[str, str]:
    """A read-only copy — frozen dataclasses stay deeply immutable."""
    return MappingProxyType(dict(mapping))

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
CAVEAT_UNVERIFIED_VERSION = "unverified-version"
CAVEAT_INVALID_SAVE_DIRECTORY = "invalid-save-directory"
CAVEAT_CORE_SUSPECT = "core-suspect"
CAVEAT_CORE_UNAUDITED = "core-unaudited"
CAVEAT_CORE_MULTI_OPTION = "core-multi-option"
CAVEAT_CARD_MODE_UNCONFIRMED = "card-mode-unconfirmed"
CAVEAT_CARD_GENERATION_MISMATCH = "card-generation-mismatch"
CAVEAT_SORTED_DIR_UNCREATABLE = "sorted-dir-uncreatable"
CAVEAT_DEAD_SYMLINK = "dead-symlink"
CAVEAT_SANDBOX_PATH_UNTRANSLATED = "sandbox-path-untranslated"
CAVEAT_APP_RELATIVE_PATH_UNEXPANDED = "app-relative-path-unexpanded"
CAVEAT_CFG_LINE_DROPPED = "cfg-line-dropped"
CAVEAT_CFG_VALUE_REJECTED = "cfg-value-rejected"
CAVEAT_CONTENT_DIR_OBSERVATION = "content-dir-observation"
CAVEAT_CONTENT_PATH_UNNAMED = "content-path-unnamed"


@dataclass(frozen=True, slots=True)
class Caveat:
    """A stated degradation — structured, so clients can act on it.

    ``code`` is a stable identifier from the ``CAVEAT_*`` constants and part of
    the API contract: clients branch on it, vectors assert it. ``message`` is
    the human-readable explanation and may change freely. ``data`` carries the
    machine-readable specifics (e.g. the fallback directory of a silent
    revert) as a read-only mapping. Decision-relevant → structured;
    explanatory → text.
    """

    code: str
    message: str
    data: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("Caveat: code must be a non-empty stable identifier")
        object.__setattr__(self, "data", _freeze(self.data))

_HOLE_CONTENT_DIR = "content_dir"
_HOLE_LIBRARY_NAME = "library_name"
_HOLE_SAVE_ID = "save_id"

# The template tokens a rule card's declared file names may carry, and the hole
# each leaves behind. ``<rom_stem>`` the resolver fills itself from the content
# path; ``<save_id>`` it never can — a core that names the save after the
# content's platform-native id (Flycast's per-game VMUs are the disc's product
# number, ``oslib.cpp:44-52`` at flycast@1dac369) reads that id out of the ROM
# itself, which atlas does not do: identifying content is not locating a save.
# So the id stays a hole for whoever knows it, exactly like ``content_dir``.
TEMPLATE_ROM_STEM = "<rom_stem>"
TEMPLATE_SAVE_ID = "<save_id>"
_FILE_NAME_HOLES: Mapping[str, str] = MappingProxyType({TEMPLATE_SAVE_ID: _HOLE_SAVE_ID})


def _holes(named: list[str]) -> tuple[str, ...]:
    """The distinct holes of a template, in the order they first appear.

    One template can name the same hole twice — ``savefiles_in_content_dir``
    roots at the content directory and ``sort_savefiles_by_content_enable``
    appends its name again (``runloop.c:8789`` then ``:8827``), so the directory
    really is ``<content_dir>/<content_dir>``. The caller still fills one value,
    and ``needs`` is the list of things to fill, not of positions to substitute
    (REVIEW L4).
    """
    return tuple(dict.fromkeys(named))


def file_set_holes(files: Iterable[str]) -> tuple[str, ...]:
    """The holes a declared file-set template still carries, in template order.

    A card may name a save's files through a fact only the content carries.
    Those names are stated as they are — the template is the answer — and the
    hole travels to ``needs`` so a caller sees what is left to fill instead of
    reading a literal ``<save_id>`` off a resolved-looking name.
    """
    return _holes([hole for name in files for token, hole in _FILE_NAME_HOLES.items() if token in name])


def needs_with_file_set(needs: Iterable[str], files: Iterable[str]) -> tuple[str, ...]:
    """Every hole of an answer: the directory template's, then the file names'."""
    return _holes([*needs, *file_set_holes(files)])


@dataclass(frozen=True, slots=True)
class FileSet:
    """The files a save consists of — observed, declared, or unknown.

    ``state`` is ``"observed"`` (``files`` are real basenames found on disk),
    ``"declared"`` (``files`` come from a verified rule card — world knowledge
    with cited provenance, not a guess), or ``"unknown"`` (``files`` is empty;
    atlas refuses to guess). ``source`` says how the state was reached.

    A declared set can itself be a template: where the card names the files
    through the content's own id, the names keep their ``<save_id>`` hole and
    :data:`SavePlacement.needs` lists it. Stating the shape in full is not the
    same as claiming the resolved name — it is the directory grammar applied to
    file names.

    *Observed* means a snapshot of matching files currently seen — it never
    implies the whole save. ``complete`` is the explicit completeness claim:
    ``True`` only when a source-verified rule card closes the candidate
    universe for the active mode; the generic observation can never earn it.
    """

    state: FileSetState
    files: tuple[str, ...]
    source: str
    complete: bool = False

    def __post_init__(self) -> None:
        if self.state not in _FILE_SET_STATES:
            raise ValueError(f"FileSet: state must be one of {_FILE_SET_STATES}, got {self.state!r}")
        if self.state == "unknown" and (self.files or self.complete):
            raise ValueError("FileSet: an unknown set carries no files and no completeness claim")


@dataclass(frozen=True, slots=True)
class Granularity:
    """How this emulator, configured as it is, groups save data — and how to change it.

    ``value`` is the current granularity (``"shared-card"``,
    ``"per-game-file"``, …), selected by the live-read ``option_value`` of
    ``option_key``; ``option_source`` is its provenance (which file, or the
    core default). ``options_file`` is where a caller would change the option —
    change it, ask again, and the new answer confirms the switch.
    ``alternatives`` lists the other selectable ``(option_value, granularity)``
    pairs from the rule card. A core with fixed behaviour (no governing option,
    e.g. LRPS2) carries ``option_key=None`` and no alternatives.
    """

    value: str
    option_key: str | None
    option_value: str | None
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
    is a template whose remaining holes are listed in ``needs`` — as are the
    holes a declared file-set template keeps, so ``needs`` is the answer's
    holes, not the directory's alone. ``root_kind``
    names the anchor (:data:`ROOT_SAVEFILE_DIRECTORY`,
    :data:`ROOT_CONTENT_DIRECTORY`, :data:`ROOT_SYSTEM_DIRECTORY`).
    ``file_set`` is observed or unknown, never guessed. ``sources`` is the
    provenance trail; ``caveats`` states every degradation explicitly.

    ``granularity`` is ``None`` wherever no rule card states it. That alone
    does not separate "nothing to report" from "atlas deliberately does not
    state this", so the separation is a caveat, not an empty field: a core
    whose granularity depends on options atlas does not interpret carries
    :data:`CAVEAT_CORE_MULTI_OPTION` naming those options.

    A placement can be *conditional*: when ``dir`` does not exist yet,
    RetroArch attempts to create it on first save and silently reverts to the
    unsorted root when creation fails — ``fallback_dir`` names that root, so
    the two possible outcomes are structural, not prose (REVIEW H5).
    ``physical_dir`` is the fully link-resolved backing directory when ``dir``
    reaches its files through symlinks (RetroDECK's ``dir_prep`` pattern) —
    the emulator-side path and the physical path are two truthful answers to
    different questions (REVIEW M7); a dead link is a ``dead-symlink`` caveat
    instead.
    """

    dir: str
    root_kind: RootKind
    needs: tuple[str, ...]
    file_set: FileSet
    sources: tuple[str, ...]
    caveats: tuple[Caveat, ...]
    granularity: Granularity | None = None
    fallback_dir: str | None = None
    physical_dir: str | None = None

    def __post_init__(self) -> None:
        if not self.dir:
            raise ValueError("SavePlacement: dir must be non-empty (an unanswerable placement is Unresolved)")
        if self.root_kind not in _ROOT_KINDS:
            raise ValueError(f"SavePlacement: root_kind must be one of {_ROOT_KINDS}, got {self.root_kind!r}")


# Unresolved outcome codes — stable identifiers like caveat codes.
UNRESOLVED_STANDALONE = "standalone-unsupported"


@dataclass(frozen=True, slots=True)
class Unresolved:
    """A question atlas cannot answer for this entry — a domain outcome, not an error.

    Returned where an answer route exists but the subject is outside the
    resolver's current coverage (e.g. a standalone emulator entry before the
    standalone block lands). ``code`` is a stable identifier clients branch
    on; ``message`` says why; ``data`` carries the specifics. Callers switch
    on the result type — nothing raises at runtime (REVIEW M8).
    """

    code: str
    message: str
    data: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("Unresolved: code must be a non-empty stable identifier")
        object.__setattr__(self, "data", _freeze(self.data))


def build_save_placement(
    *,
    layout: RetroArchCfg,
    platform_default_dir: str,
    content_dir_path: str | None,
    content_dir_name: str | None,
    library_name: str | None,
    extra_sources: tuple[str, ...] = (),
    caveats: tuple[Caveat, ...] = (),
    file_set: FileSet = UNKNOWN_FILE_SET,
) -> SavePlacement:
    """Compose a :class:`SavePlacement` from a resolved layout and the caller's fills.

    ``platform_default_dir`` is the arrangement's RetroArch platform default
    saves directory (``saves`` under the config tree, ``platform_unix.c:2133-2134``)
    — the effective root whenever ``savefile_directory`` is unset or reset.
    ``content_dir_path`` / ``content_dir_name`` derive from the content path
    when the caller supplied one (the ROM's own directory and its basename);
    when absent the corresponding hole is left in the template and listed in
    ``needs``. ``library_name`` is the core's self-reported name (via
    ``query_core``); when the layout sorts by core and it is absent, the
    ``<library_name>`` hole remains.
    """
    needs: list[str] = []
    all_sources = list(layout.sources) + list(extra_sources)

    # Root selection first (runloop.c:8785-8813), then the sorting stages run
    # regardless of how the root was selected (runloop.c:8822-8841) — content
    # component first, then library_name.
    if layout.savefiles_in_content_dir:
        root_kind = ROOT_CONTENT_DIRECTORY
        all_sources.append("layout: root is the ROM's own directory (savefiles_in_content_dir)")
        if content_dir_path is not None:
            parts = [content_dir_path]
        else:
            parts = ["<content_dir>"]
            needs.append(_HOLE_CONTENT_DIR)
    elif layout.savefile_directory is None:
        root_kind = ROOT_SAVEFILE_DIRECTORY
        parts = [platform_default_dir]
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
        needs=_holes(needs),
        file_set=file_set,
        sources=tuple(all_sources),
        caveats=tuple(caveats),
    )
