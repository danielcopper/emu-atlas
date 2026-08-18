"""Per-core mode-selection rules for cards no single option can govern.

A rule card (``governing_rule`` in ``data/core_oddities.json``) keeps its modes
as data — what *can* exist — and the function here, keyed by the card key, is
what decides which of them holds on this machine: it reads the options the
card declared, and whatever else it stated it needs (a content class, a config
file of the emulator's own), and returns the mode's name. Several interacting
options are a product no single option's value can name, so the format grows
code plus a card referencing it, never a DSL (issue #163).

The boundary rule holds unchanged: everything a rule decides on is read off
the running machine, handed in by the resolver through :class:`RuleReading`.
A rule that cannot decide returns no mode and says why, as structured
caveats — the card then steps aside exactly as it does for a generation
nobody could confirm, and the standard answer below says what it can.

The resolver records which of the declared options a rule actually consulted,
and those readings — plus any the rule adds itself, like ScummVM's
``savepath`` — become the answer's :class:`~atlas.placement.Granularity`
readings: the caller sees every switch that went into the selection, its
live value, and where to change it.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Callable, Mapping

from atlas.placement import (
    CAVEAT_CORE_GENERATION_MISMATCH,
    CAVEAT_CORE_MODE_UNESTABLISHED,
    CAVEAT_CORE_OPTION_VALUE_UNESTABLISHED,
    CAVEAT_SAVE_ROOT_REDIRECTED,
    Caveat,
    OptionReading,
)

# How a rule saw a file it asked for. Three states rather than text-or-None,
# because two different absences must not collapse: a file that is not there
# is a machine fact a rule may decide on (a fresh ScummVM has no ini and the
# default applies), a file that is there and cannot be read leaves the rule
# unable to decide — and deciding anyway would be the guess this project
# refuses.
FILE_READ = "read"
FILE_ABSENT = "absent"
FILE_UNREADABLE = "unreadable"


@dataclass(frozen=True, slots=True)
class FileLookup:
    """One file the resolver read for a rule: its text, its state, its path."""

    text: str | None
    status: str
    path: str | None


@dataclass(frozen=True, slots=True)
class RuleReading:
    """Everything the resolver hands a rule to decide with — all machine reads.

    ``option_values`` maps the card's declared rule options to their live
    values (``None`` where nothing on the machine states one and the core
    registered no default). The mapping records which keys the rule consults,
    so the answer's readings list is exactly the switches that mattered here —
    hatari reads one of its two write-protect options, never both, because
    which one governs is the content's class.

    ``content_extension`` is the loaded content's extension, lowered, without
    the dot — ``None`` when the question named no content. ``system_file``
    reads a file from the directory RetroArch hands cores as the system
    directory. ``save_dirs`` is every spelling the frontend's save root can
    have reached the core under (the configured root, and the sorted
    directory RetroArch redirects to), for a rule that must compare a
    configured path against it, and ``is_directory`` answers whether an
    emulator-spelled path is a directory on this machine — ``None`` where
    that could not be established (the path did not translate to a host
    view).
    """

    option_values: Mapping[str, str | None]
    content_extension: str | None
    system_file: Callable[[str], FileLookup]
    save_dirs: tuple[str, ...]
    is_directory: Callable[[str], bool | None]


@dataclass(frozen=True, slots=True)
class ModeChoice:
    """What a rule decided: the mode, the reachable others, and any degradation.

    ``alternatives`` is one ``(mode, ((option, value), ...))`` per other mode a
    caller could switch to — the option combination that selects it, in the
    card's own vocabulary. The resolver turns them into the answer's
    :class:`~atlas.placement.ModeAlternative` by adding each mode's own
    granularity, which the rule deliberately does not know. ``readings`` is
    for switches that are not core options at all (ScummVM's ``savepath``
    lives in the emulator's own ini); the consulted core options are recorded
    by the resolver and need no restating here.
    """

    mode: str | None
    alternatives: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = ()
    caveats: tuple[Caveat, ...] = ()
    readings: tuple[OptionReading, ...] = ()


def _value_unestablished(core: str, option_key: str) -> Caveat:
    """Nothing on this machine, and nothing in the core, states this option."""
    return Caveat(
        CAVEAT_CORE_OPTION_VALUE_UNESTABLISHED,
        f"core {core!r} is recorded as placing its saves outside the standard layout under "
        f"option {option_key!r}, and which value governs it here was never established — no "
        "configuration on this machine states one and the installed core declared no default, so "
        "the recorded behaviour is not applied; the standard answer below may miss the real save "
        "stack",
        {"core": core, "option_key": option_key},
    )


def _unknown_value(core: str, option_key: str, value: str) -> Caveat:
    """A live value the recorded behaviour cannot interpret — the record lags."""
    return Caveat(
        CAVEAT_CORE_GENERATION_MISMATCH,
        f'core option {option_key} = "{value}" is a value the recorded save behaviour of core '
        f"{core!r} cannot interpret — the record lags this core's generation; the configured "
        "save behaviour is unknown until re-audited, and the standard answer below may miss the "
        "real save stack",
        {"core": core, "option_key": option_key, "value": value},
    )


def _mode_unestablished(core: str, reason: str) -> Caveat:
    """The rule as a whole could not decide — the reason travels with the code."""
    return Caveat(
        CAVEAT_CORE_MODE_UNESTABLISHED,
        f"core {core!r} is recorded as selecting between save behaviours by a rule, and the rule "
        f"could not decide here: {reason} — the recorded behaviour is not applied; the standard "
        "answer below may miss the real save stack",
        {"core": core, "reason": reason},
    )


# ---------------------------------------------------------------------------
# mednafen_saturn — two independent sharing switches over one three-file set.
# shared_int swaps the stem of the internal pair (.bkr backup RAM and .smpc
# RTC/language, both written through MDFNMKF_SAV), shared_ext the cartridge's
# .bcr (MDFNMKF_CART) — libretro.cpp:1045-1073 at ccba526, options read at
# :257-276. The four combinations are the four modes, nothing else selects.
# ---------------------------------------------------------------------------

_SATURN_INT = "beetle_saturn_shared_int"
_SATURN_EXT = "beetle_saturn_shared_ext"
_SATURN_MODES: dict[tuple[str, str], str] = {
    ("disabled", "disabled"): "per-game",
    ("enabled", "disabled"): "internal-shared",
    ("disabled", "enabled"): "cartridge-shared",
    ("enabled", "enabled"): "both-shared",
}


def _mednafen_saturn(reading: RuleReading) -> ModeChoice:
    values = (reading.option_values[_SATURN_INT], reading.option_values[_SATURN_EXT])
    missing = [key for key, value in zip((_SATURN_INT, _SATURN_EXT), values) if value is None]
    if missing:
        return ModeChoice(
            None, caveats=tuple(_value_unestablished("mednafen_saturn", key) for key in missing)
        )
    alien = [
        (key, value)
        for key, value in zip((_SATURN_INT, _SATURN_EXT), values)
        if value not in ("enabled", "disabled")
    ]
    if alien:
        return ModeChoice(
            None,
            caveats=tuple(
                _unknown_value("mednafen_saturn", key, value or "") for key, value in alien
            ),
        )
    int_value, ext_value = values
    mode = _SATURN_MODES[(int_value or "", ext_value or "")]
    return ModeChoice(
        mode,
        alternatives=tuple(
            (other, ((_SATURN_INT, combo[0]), (_SATURN_EXT, combo[1])))
            for combo, other in _SATURN_MODES.items()
            if other != mode
        ),
    )


# ---------------------------------------------------------------------------
# hatari — which write-protect option governs is the content's class. Floppy
# images are written back into themselves at eject (floppy.c:599-634 at
# 7008194); hard-disk content takes writes in place; either class's protect
# option set to 'on' discards the changes instead. The classes are the
# dispatch in retro_load_game (libretro.c:1597-1652): st/msa/stx/dim/ipf and
# an .m3u playlist of them load as floppies (a .zip reaches the same writer,
# floppy.c:626-627), ide/vhd/gem attach as hard disks.
# ---------------------------------------------------------------------------

_HATARI_FLOPPY = frozenset({"st", "msa", "stx", "dim", "ipf", "zip", "m3u"})
_HATARI_HARD_DISK = frozenset({"ide", "vhd", "gem"})
_HATARI_FLOPPY_KEY = "hatari_writeprotect_floppy"
_HATARI_HD_KEY = "hatari_writeprotect_hd"


def _hatari(reading: RuleReading) -> ModeChoice:
    extension = reading.content_extension
    if extension is None:
        return ModeChoice(
            None,
            caveats=(
                _mode_unestablished(
                    "hatari",
                    "which write-protect option governs depends on the content's class (a floppy "
                    "image is written back into itself, a hard-disk image takes writes in place), "
                    "and no content was named",
                ),
            ),
        )
    if extension in _HATARI_FLOPPY:
        option_key, prefix = _HATARI_FLOPPY_KEY, "floppy"
    elif extension in _HATARI_HARD_DISK:
        option_key, prefix = _HATARI_HD_KEY, "hard-disk"
    else:
        return ModeChoice(
            None,
            caveats=(
                _mode_unestablished(
                    "hatari",
                    f"the content's extension {extension!r} is outside both recorded classes "
                    "(floppy: st/msa/stx/dim/ipf/zip/m3u; hard disk: ide/vhd/gem), so which "
                    "write-protect option governs was never established",
                ),
            ),
        )
    value = reading.option_values[option_key]
    if value is None:
        return ModeChoice(None, caveats=(_value_unestablished("hatari", option_key),))
    if value in ("off", "auto"):
        mode = f"{prefix}-writeback"
        alternatives = ((f"{prefix}-discarded", ((option_key, "on"),)),)
    elif value == "on":
        mode = f"{prefix}-discarded"
        alternatives = ((f"{prefix}-writeback", ((option_key, "off"),)),)
    else:
        return ModeChoice(None, caveats=(_unknown_value("hatari", option_key, value),))
    return ModeChoice(mode, alternatives=alternatives)


# ---------------------------------------------------------------------------
# scummvm — the save directory is ScummVM's own 'savepath' setting, persisted
# in <system dir>/scummvm.ini (libretro-os-utils.cpp:64-69 at 686cdd1). Set
# and pointing at an existing directory it governs; unset, or set to something
# that is not a directory, the emulator removes the key and falls back to the
# frontend's save directory (checkPathSetting, :169-181, applied at :218-221).
# ---------------------------------------------------------------------------

_SCUMMVM_INI = "scummvm.ini"
_SCUMMVM_DEFAULT_MODE = "frontend-save-dir"


def _scummvm_ini_savepath(text: str) -> str | None:
    """The application domain's ``savepath``, parsed the way ConfMan spells it.

    Only the ``[scummvm]`` section is read: that is the application domain the
    backend writes the setting into. A per-target section may carry its own
    override, but which target the loaded content maps to is launcher
    configuration atlas does not read — the card's own prose says so.
    """
    in_scummvm = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            in_scummvm = line == "[scummvm]"
            continue
        if not in_scummvm or "=" not in line or line.startswith("#") or line.startswith(";"):
            continue
        key, _, value = line.partition("=")
        if key.strip() == "savepath" and value.strip():
            return value.strip()
    return None


def _scummvm(reading: RuleReading) -> ModeChoice:
    ini = reading.system_file(_SCUMMVM_INI)
    if ini.status == FILE_UNREADABLE:
        return ModeChoice(
            None,
            caveats=(
                _mode_unestablished(
                    "scummvm",
                    "the save directory is ScummVM's own 'savepath' setting in scummvm.ini, and "
                    "the ini could not be read — whether the saves were routed elsewhere is "
                    "unknowable here",
                ),
            ),
        )
    savepath = _scummvm_ini_savepath(ini.text) if ini.status == FILE_READ and ini.text else None
    if savepath is None:
        provenance = (
            "scummvm.ini states no savepath — the registered default is the frontend's save "
            "directory, flat (libretro-os-utils.cpp:212-216 at 686cdd1)"
            if ini.status == FILE_READ
            else "no scummvm.ini exists yet — the registered default is the frontend's save "
            "directory, flat (libretro-os-utils.cpp:212-216 at 686cdd1)"
        )
        return ModeChoice(
            _SCUMMVM_DEFAULT_MODE,
            readings=(OptionReading("savepath", None, provenance, ini.path),),
        )
    a_directory = reading.is_directory(savepath)
    if a_directory is None:
        return ModeChoice(
            None,
            caveats=(
                _mode_unestablished(
                    "scummvm",
                    f"scummvm.ini sets savepath to {savepath!r}, which no view of this machine "
                    "translates to a host path — whether it governs cannot be established",
                ),
            ),
            readings=(
                OptionReading(
                    "savepath", savepath, f'scummvm.ini: savepath = "{savepath}"', ini.path
                ),
            ),
        )
    if not a_directory:
        return ModeChoice(
            _SCUMMVM_DEFAULT_MODE,
            readings=(
                OptionReading(
                    "savepath",
                    savepath,
                    f'scummvm.ini: savepath = "{savepath}" — set, but not a directory on this '
                    "machine, so the emulator removes the key at startup and falls back to the "
                    "frontend's save directory (checkPathSetting, libretro-os-utils.cpp:169-181 "
                    "at 686cdd1)",
                    ini.path,
                ),
            ),
        )
    if any(_same_path(savepath, save_dir) for save_dir in reading.save_dirs):
        return ModeChoice(
            _SCUMMVM_DEFAULT_MODE,
            readings=(
                OptionReading(
                    "savepath",
                    savepath,
                    f'scummvm.ini: savepath = "{savepath}" — the frontend\'s save directory, '
                    "spelled out (the backend re-writes its default into the ini)",
                    ini.path,
                ),
            ),
        )
    return ModeChoice(
        None,
        caveats=(
            Caveat(
                CAVEAT_SAVE_ROOT_REDIRECTED,
                f"ScummVM's own configuration routes its saves to {savepath!r} "
                f"(scummvm.ini: savepath), a directory outside every root kind this answer "
                "states — the standard answer below is where the frontend would look, not where "
                "this emulator writes; the slot files there are named per engine from the "
                "launcher target, which atlas does not read",
                {
                    "core": "scummvm",
                    "key": "savepath",
                    "path": savepath,
                    "options_file": ini.path or "",
                },
            ),
        ),
        readings=(
            OptionReading("savepath", savepath, f'scummvm.ini: savepath = "{savepath}"', ini.path),
        ),
    )


def _same_path(left: str, right: str) -> bool:
    """One directory spelled twice? Normalized string equality, nothing cleverer.

    Both spellings are the emulator's own view, so no host translation
    belongs here — and a false *inequality* only costs a redirect caveat
    about a path that happens to be the save directory, which is still a
    true statement about the configuration.
    """
    return posixpath.normpath(left.rstrip("/")) == posixpath.normpath(right.rstrip("/"))


# The registry the card loader validates against: a card stating a
# ``governing_rule`` must have its function here, and the test suite holds
# the mirror claim — a rule with no card would be code describing nothing.
RULES: Mapping[str, Callable[[RuleReading], ModeChoice]] = {
    "mednafen_saturn": _mednafen_saturn,
    "hatari": _hatari,
    "scummvm": _scummvm,
}
