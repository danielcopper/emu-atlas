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

# The keys this module resolves — the ones that decide where a save lands. A
# line RetroArch drops is stated only when it aims at one of these: elsewhere a
# dropped line is simply a key that is not set, which is what atlas reports.
_GOVERNING_KEYS = (_IN_CONTENT_DIR, _SORT_BY_CONTENT, _SORT_BY_CORE, _SAVEFILE_DIRECTORY)


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


# What a setting's spelling can do wrong: the parser refuses the line, or the
# typed getter refuses the value. Either way the setting is not applied.
IGNORED_LINE_DROPPED = "line-dropped"
IGNORED_VALUE_REJECTED = "value-rejected"


@dataclass(frozen=True, slots=True)
class IgnoredSetting:
    """A setting a config states and RetroArch does not apply.

    ``kind`` is :data:`IGNORED_LINE_DROPPED` (the parser refused the line) or
    :data:`IGNORED_VALUE_REJECTED` (the value is outside the setting's
    vocabulary, so the value from before this layer stands). ``layer`` names
    the file it was written in, ``key`` the setting it aims at, and ``text``
    the offending spelling — the whole line for a dropped line, the value for
    a rejected one. atlas behaves exactly as RetroArch does; consumers state
    these as caveats because the gap between what the file says and what the
    emulator does is invisible in the answer itself.
    """

    kind: str
    layer: str
    key: str
    text: str


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
    the value; when an override won, it names it. ``ignored`` carries the
    governing settings the configs *state* and RetroArch does not apply — a
    line its parser drops, a value its typed getter refuses — so a caller sees
    why the file and the answer disagree.
    """

    savefiles_in_content_dir: bool
    sort_by_content: bool
    sort_by_core: bool
    savefile_directory: str | None
    sources: tuple[str, ...]
    ignored: tuple[IgnoredSetting, ...] = ()


# RetroArch's parser, ported line for line from ``config_file.c`` (pinned
# revision a79435a). Its grammar is not "key = value with optional quotes": a
# key is a run of isgraph() characters, and the next non-whitespace character
# after it must be '='. Since '=' is itself a graph character, a key written
# tight against it (``savefile_directory="/x"``) swallows the '=' and the line
# is dropped — reading such a line would attest a path this emulator never uses.
_WHITESPACE = " \t\r\n"
_QUOTE = '"'
_COMMENT = "#"
_NUL = "\x00"


def _is_graph(char: str) -> bool:
    """``isgraph`` in the C locale: printable ASCII, space excluded.

    Bytes outside ASCII are not graph characters in the C locale, and a
    non-ASCII codepoint here stands for exactly those bytes — a run ends at
    the first one either way (``config_file.c:249``, ``:600``).
    """
    return "!" <= char <= "~"


def _graph_run(text: str) -> str:
    """The leading run of graph characters — what the C scan loops measure."""
    for index, char in enumerate(text):
        if not _is_graph(char):
            return text[:index]
    return text


def _strip_comment(line: str) -> tuple[str, bool]:
    """``config_file_strip_comment`` (``config_file.c:159-206``).

    Returns ``(line, is_comment_line)``. Only the **first two** quotes of the
    whole line are weighed against the first ``#``, before any key/value split:
    when the literal they open closes *after* the ``#``, the ``#`` is inside a
    string and the line is left untouched; otherwise everything from the ``#``
    is cut. A ``#`` in column zero (the raw line is not trimmed first) makes
    the whole line a comment — RetroArch reads ``include`` and ``reference``
    directives there, which atlas does not resolve: a stated gap (task list),
    not an approximation.
    """
    comment = line.find(_COMMENT)
    if comment < 0:
        return line, False
    if comment == 0:
        return "", True
    literal_start = line.find(_QUOTE)
    if 0 <= literal_start < comment and line.find(_QUOTE, literal_start + 1) > comment:
        return line, False
    return line[:comment], False


def _extract_value(text: str) -> str:
    """``config_file_extract_value`` (``config_file.c:208-260``).

    A quoted value ends at the *next* quote — a missing closing quote is not
    an error, the rest of the line is then the value. An unquoted value ends
    at the first non-graph character, so an unquoted ``/mnt/my saves`` is
    ``/mnt/my``. An empty value is a value: the key is set, to ``""``.
    """
    value = text.lstrip(_WHITESPACE)
    if value.startswith(_QUOTE):
        end = value.find(_QUOTE, 1)
        return value[1:] if end < 0 else value[1:end]
    return _graph_run(value)


@dataclass(frozen=True, slots=True)
class DroppedLine:
    """A line RetroArch's parser refuses — it sets nothing there, so nothing here.

    ``key`` is the setting the line appears to aim at (the key run up to its
    first ``=``), ``line`` the line as written. The common cause is a missing
    space before the ``=``: ``savefile_directory="/x"`` scans as one key named
    ``savefile_directory="/x"`` with no ``=`` behind it, and
    ``config_file_parse_line`` drops the entry (``config_file.c:596-623``).
    """

    key: str
    line: str


@dataclass(frozen=True, slots=True)
class ParsedCfg:
    """One cfg text as RetroArch reads it: the settings it makes, the lines it drops."""

    values: dict[str, str]
    dropped: tuple[DroppedLine, ...] = ()


def parse_cfg(text: str) -> ParsedCfg:
    """Parse a cfg text through RetroArch's own line pipeline.

    Strip the comment, take the key run, require the ``=``, extract the value
    — ``config_file_parse_line`` (``config_file.c:524-632``), one line at a
    time split on ``\\n`` alone as the C loop does. The **first** occurrence of
    a duplicate key wins (``config_file.c:496-507`` maps a key only when not
    already present). The key is the whole graph run, so ``savefile_directory``
    and ``savefiles_in_content_dir`` never collide.

    Nothing past the first NUL is read at all: the C parses a NUL-terminated
    buffer and cuts each line with ``strchr(line, '\\n')``, which stops at the
    NUL — that line ends there, and the missing newline then breaks the loop
    (``config_file.c:461-517``, identically ``:644-686`` for the from-string
    path). A cfg NUL-padded by an unclean shutdown therefore sets only what
    stands before the padding; reading on would attest settings RetroArch
    never applies.
    """
    # Everything past the first NUL is invisible to the C parser (see above).
    text = text.partition(_NUL)[0]
    values: dict[str, str] = {}
    dropped: list[DroppedLine] = []
    for raw_line in text.split("\n"):
        line, is_comment = _strip_comment(raw_line)
        if is_comment:
            continue
        line = line.lstrip(_WHITESPACE)
        key = _graph_run(line)
        if not key:
            continue
        rest = line[len(key) :].lstrip(_WHITESPACE)
        if not rest.startswith("="):
            aimed_at = key.split("=", 1)[0]
            if aimed_at:
                dropped.append(DroppedLine(aimed_at, raw_line.strip()))
            continue
        values.setdefault(key, _extract_value(rest[1:]))
    return ParsedCfg(values, tuple(dropped))


def parse_cfg_text(text: str) -> dict[str, str]:
    """The settings a cfg text makes — :func:`parse_cfg` without the dropped lines."""
    return parse_cfg(text).values


_BOOL_TRUE = ("1", "true")
_BOOL_FALSE = ("0", "false")


def cfg_bool(raw: str) -> bool | None:
    """RetroArch's boolean vocabulary — ``None`` when the value is outside it.

    ``config_get_bool`` accepts exactly ``1``, ``true``, ``0`` and ``false``,
    case-sensitively, and reports failure for anything else
    (``config_file.c:1227-1262``); the loop that applies boolean settings then
    writes nothing (``configuration.c:6412-6417``), so the setting keeps the
    value it already had. ``"TRUE"`` and ``"yes"`` are not booleans: they set
    nothing at all, they do not mean false.
    """
    if raw in _BOOL_TRUE:
        return True
    if raw in _BOOL_FALSE:
        return False
    return None


_APP_DIR_PREFIX = ":"
_UNSET_VALUE = "default"


def is_app_relative(raw: str) -> bool:
    """Does this path value carry RetroArch's application-directory prefix?

    ``fill_pathname_expand_special`` expands a leading ``:`` against the
    directory of the *running* RetroArch executable (``file_path.c:1066-1101``
    → ``fill_pathname_application_dir``, ``file_path.c:1447-1455``, which reads
    ``/proc/<pid>/exe``, ``file_path.c:1421-1441``). Which executable that is
    is a property of the running process, not of anything on disk, so atlas
    states such a value unexpanded rather than inventing a directory for it.
    """
    return raw.startswith(_APP_DIR_PREFIX)


def expand_home(raw: str, *, home: str) -> str | None:
    """Expand a leading ``~`` against *home*; ``None`` when the value sets no path.

    Blank and the literal ``default`` are RetroArch's "unset" spellings, the
    latter compared case-sensitively (``string_is_equal``,
    ``configuration.c:6918``, ``:6825``). ``~`` is expanded by
    ``config_get_path`` → ``fill_pathname_expand_special``
    (``config_file.c:1202-1216``, ``file_path.c:1066-1101``) on desktop builds.
    The value is used exactly as parsed: a quoted value may legitimately carry
    leading or trailing spaces, and RetroArch does not trim them.

    This is the one place the port knowingly departs from upstream: once a home
    directory is filled, ``in_path += 2`` runs unconditionally
    (``file_path.c:1096``), so RetroArch turns ``~foo`` into ``<home>/oo`` and
    reads one byte past the terminator for a bare ``~``. atlas expands the
    ``~`` and ``~/`` spellings and leaves every other one alone — the upstream
    answer there is an out-of-bounds read, which is not a save directory atlas
    can state.
    """
    if raw in ("", _UNSET_VALUE):
        return None
    if raw == "~":
        return home
    if raw.startswith("~/"):
        return home + raw[1:]
    return raw


# One layer of the chain: its provenance label, its parsed content, and whether
# it is an override (the global cfg is not).
_Layer = tuple[str, ParsedCfg, bool]


def _resolve_flag(
    layers: Sequence[_Layer], key: str, *, default: bool, defaults_label: str
) -> tuple[bool, str, tuple[IgnoredSetting, ...]]:
    """One boolean key through the chain — later layers win, provenance follows.

    A layer whose value is outside RetroArch's boolean vocabulary is a no-op
    *at that layer*: the value from the layer before it (or the default)
    stands, exactly as the bool loop leaves it (``configuration.c:6412-6417``).
    """
    value, source = default, f"default: {key} = {str(default).lower()} ({defaults_label})"
    ignored: list[IgnoredSetting] = []
    for label, parsed, is_override in layers:
        raw = parsed.values.get(key)
        if raw is None:
            continue
        decoded = cfg_bool(raw)
        if decoded is None:
            ignored.append(IgnoredSetting(IGNORED_VALUE_REJECTED, label, key, raw))
            continue
        value = decoded
        source = f'{label}: {key} = "{raw}"' + (" (override wins)" if is_override else "")
    return value, source, tuple(ignored)


def _resolve_savefile_directory(layers: Sequence[_Layer], *, home: str) -> tuple[str | None, str]:
    """The saves root through the chain — ``None`` when the platform default applies.

    An application-relative value (``:`` prefix) is kept as written: it names a
    directory only the running process knows, and the consumer states that
    rather than expanding it into a host path atlas cannot verify.
    """
    savefile_directory: str | None = None
    source = (
        f"default: {_SAVEFILE_DIRECTORY} unset — RetroArch platform default applies "
        "(saves under the config tree, platform_unix.c:2133-2134)"
    )
    for label, parsed, is_override in layers:
        raw = parsed.values.get(_SAVEFILE_DIRECTORY)
        if raw is None:
            continue
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


def _dropped_governing_lines(layers: Sequence[_Layer]) -> tuple[IgnoredSetting, ...]:
    """Lines RetroArch dropped that aimed at one of the save-layout keys.

    RetroArch drops them silently and so does atlas — but a dropped line
    naming the very setting this answer is about is the one case where the
    file and the emulator disagree about the save location, so it is stated.
    """
    return tuple(
        IgnoredSetting(IGNORED_LINE_DROPPED, label, line.key, line.line)
        for label, parsed, _ in layers
        for line in parsed.dropped
        if line.key in _GOVERNING_KEYS
    )


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
    empty = ParsedCfg({})
    layers: list[_Layer] = [
        (cfg_label, parse_cfg(global_text) if global_text is not None else empty, False)
    ]
    layers.extend((label, parse_cfg(text), True) for label, text in overrides)

    defaults_label = defaults.label
    in_content_dir, s1, i1 = _resolve_flag(
        layers, _IN_CONTENT_DIR, default=defaults.savefiles_in_content_dir, defaults_label=defaults_label
    )
    sort_by_content, s2, i2 = _resolve_flag(
        layers, _SORT_BY_CONTENT, default=defaults.sort_by_content, defaults_label=defaults_label
    )
    sort_by_core, s3, i3 = _resolve_flag(
        layers, _SORT_BY_CORE, default=defaults.sort_by_core, defaults_label=defaults_label
    )
    savefile_directory, dir_source = _resolve_savefile_directory(layers, home=home)

    return RetroArchCfg(
        savefiles_in_content_dir=in_content_dir,
        sort_by_content=sort_by_content,
        sort_by_core=sort_by_core,
        savefile_directory=savefile_directory,
        sources=(s1, s2, s3, dir_source),
        ignored=(*_dropped_governing_lines(layers), *i1, *i2, *i3),
    )


def interpret_cfg(
    text: str | None, *, home: str, cfg_label: str, defaults: LayoutDefaults = RETRODECK_DEFAULTS
) -> RetroArchCfg:
    """Interpret a single ``retroarch.cfg`` text (no overrides) — see :func:`resolve_save_layout`."""
    return resolve_save_layout(text, home=home, cfg_label=cfg_label, defaults=defaults)
