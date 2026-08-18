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


# ---------------------------------------------------------------------------
# swanstation — each memory-card slot has its own type option, and the mode is
# the pair: slot 1 defaults to the libretro SRAM interface (the frontend's
# .srm), slot 2 to no card. The per-game types name the card after the disc's
# game code or its title (libretro_host_interface.cpp:404-415 at 4d309c0,
# selected in system.cpp:1450-1508). Which other modes an answer lists is the
# rule's judgment: twenty are reachable, so the alternatives are the one-edit
# neighbours — every slot, changed once — rather than the whole product.
# ---------------------------------------------------------------------------

_SWAN_CARD1 = "swanstation_MemoryCards_Card1Type"
_SWAN_CARD2 = "swanstation_MemoryCards_Card2Type"
_SWAN_PLAYLIST = "swanstation_MemoryCards_UsePlaylistTitle"
_SWAN_TYPES = {
    "Libretro": "libretro",
    "Shared": "shared",
    "PerGame": "per-code",
    "PerGameTitle": "per-title",
    "None": "none",
}
_SWAN_CARD1_VALUES = ("Libretro", "Shared", "PerGame", "PerGameTitle", "None")
_SWAN_CARD2_VALUES = ("None", "Shared", "PerGame", "PerGameTitle")


def _swanstation_mode(card1: str, card2: str) -> str:
    return f"card1-{_SWAN_TYPES[card1]}+card2-{_SWAN_TYPES[card2]}"


def _swanstation(reading: RuleReading) -> ModeChoice:
    card1 = reading.option_values[_SWAN_CARD1]
    card2 = reading.option_values[_SWAN_CARD2]
    missing = [
        key for key, value in ((_SWAN_CARD1, card1), (_SWAN_CARD2, card2)) if value is None
    ]
    if missing:
        return ModeChoice(
            None, caveats=tuple(_value_unestablished("swanstation", key) for key in missing)
        )
    alien = [
        (key, value)
        for key, value, known in (
            (_SWAN_CARD1, card1, _SWAN_CARD1_VALUES),
            (_SWAN_CARD2, card2, _SWAN_CARD2_VALUES),
        )
        if value not in known
    ]
    if alien:
        return ModeChoice(
            None,
            caveats=tuple(_unknown_value("swanstation", key, value or "") for key, value in alien),
        )
    if "PerGameTitle" in (card1, card2):
        # The playlist option only moves a title-named card's stem, so it is
        # read — and stated as a reading — exactly where a slot is title-named.
        _ = reading.option_values[_SWAN_PLAYLIST]
    alternatives = []
    for value in _SWAN_CARD1_VALUES:
        if value != card1:
            alternatives.append((_swanstation_mode(value, card2 or ""), ((_SWAN_CARD1, value),)))
    for value in _SWAN_CARD2_VALUES:
        if value != card2:
            alternatives.append((_swanstation_mode(card1 or "", value), ((_SWAN_CARD2, value),)))
    return ModeChoice(_swanstation_mode(card1 or "", card2 or ""), alternatives=tuple(alternatives))


# ---------------------------------------------------------------------------
# beetle psx / beetle psx hw — one implementation, two option prefixes. Slot 0
# is the frontend's .srm through the libretro SRAM interface, or the core's
# own <stem>.<idx>.mcr under the mednafen method; slot 1 adds a second .mcr;
# sharing swaps the stem (libretro.cpp:2145-2163, :2459-2485, :5240-5249 at
# d6383bf). The card-image index options supply the digit in the name
# (:2159-2164), so the recorded names hold for the registered defaults (left
# 0, right 1) and the rule steps aside for any other selection.
# ---------------------------------------------------------------------------


def _beetle_psx_rule(prefix: str, core: str) -> Callable[[RuleReading], ModeChoice]:
    method_key = f"{prefix}use_mednafen_memcard0_method"
    memcard1_key = f"{prefix}enable_memcard1"
    shared_key = f"{prefix}shared_memory_cards"
    left_key = f"{prefix}memcard_left_index"
    right_key = f"{prefix}memcard_right_index"

    def rule(reading: RuleReading) -> ModeChoice:
        method = reading.option_values[method_key]
        memcard1 = reading.option_values[memcard1_key]
        shared = reading.option_values[shared_key]
        missing = [
            key
            for key, value in ((method_key, method), (memcard1_key, memcard1), (shared_key, shared))
            if value is None
        ]
        if missing:
            return ModeChoice(None, caveats=tuple(_value_unestablished(core, key) for key in missing))
        alien = [
            (key, value)
            for key, value, known in (
                (method_key, method, ("libretro", "mednafen")),
                (memcard1_key, memcard1, ("enabled", "disabled")),
                (shared_key, shared, ("enabled", "disabled")),
            )
            if value not in known
        ]
        if alien:
            return ModeChoice(
                None, caveats=tuple(_unknown_value(core, key, value or "") for key, value in alien)
            )
        indexes = []
        if method == "mednafen":
            indexes.append((left_key, reading.option_values[left_key], "0"))
        if memcard1 == "enabled":
            indexes.append((right_key, reading.option_values[right_key], "1"))
        offset = [(key, value) for key, value, default in indexes if value != default]
        if offset:
            names = ", ".join(f'{key} = "{value}"' for key, value in offset)
            return ModeChoice(
                None,
                caveats=(
                    _mode_unestablished(
                        core,
                        f"the card-image index options select other card files than the recorded "
                        f"names cover ({names} — the digit in <stem>.<idx>.mcr is the option's "
                        "value, libretro.cpp:2159-2164 at d6383bf)",
                    ),
                ),
            )
        slot0 = "srm" if method == "libretro" else "mcr"
        second = memcard1 == "enabled"
        is_shared = shared == "enabled" and (slot0 == "mcr" or second)
        mode = f"{slot0}{'+second-card' if second else '-only'}{'-shared' if is_shared else ''}"

        def other(method_v: str, memcard1_v: str, shared_v: str) -> tuple[str, tuple[tuple[str, str], ...]]:
            s0 = "srm" if method_v == "libretro" else "mcr"
            snd = memcard1_v == "enabled"
            shr = shared_v == "enabled" and (s0 == "mcr" or snd)
            name = f"{s0}{'+second-card' if snd else '-only'}{'-shared' if shr else ''}"
            return name, ((method_key, method_v), (memcard1_key, memcard1_v), (shared_key, shared_v))

        flips = [
            other("mednafen" if method == "libretro" else "libretro", memcard1 or "", shared or ""),
            other(method or "", "disabled" if second else "enabled", shared or ""),
            other(method or "", memcard1 or "", "disabled" if shared == "enabled" else "enabled"),
        ]
        alternatives = tuple(
            (name, combo) for name, combo in flips if name != mode
        )
        return ModeChoice(mode, alternatives=alternatives)

    return rule


# ---------------------------------------------------------------------------
# genesis_plus_gx — frontend SRAM is real for the cartridge systems and
# unreachable for the CD ones, whose BRAM tree hangs on three interacting
# options (libretro.c:1385-1500 at 46a5521): the system BRAM is region-keyed
# scd_E/U/J.brm or per-game <stem>.brm, and the backup cart is one shared
# size-keyed file, a per-game one, or absent. Which world applies is the
# content's class, read off the extension the way the loader dispatches.
# ---------------------------------------------------------------------------

_GPGX_SYSTEM = "genesis_plus_gx_system_bram"
_GPGX_CART = "genesis_plus_gx_cart_bram"
_GPGX_SIZE = "genesis_plus_gx_cart_size"
_GPGX_CD = frozenset({"cue", "iso", "chd", "m3u"})
_GPGX_CARTRIDGE = frozenset({"md", "smd", "gen", "sms", "gg", "sg", "68k", "sgd", "mdx", "bms"})
_GPGX_SIZES = ("128k", "256k", "512k", "1meg", "2meg", "4meg")


def _gpgx_cd_mode(system: str, cart: str, size: str) -> str:
    base = "cd-bios-bram" if system == "per bios" else "cd-game-bram"
    if size == "disabled":
        return base
    suffix = f"+cart-{size}" if cart == "per cart" else f"+cart-{size}-per-game"
    return base + suffix


def _genesis_plus_gx(reading: RuleReading) -> ModeChoice:
    extension = reading.content_extension
    if extension is None:
        return ModeChoice(
            None,
            caveats=(
                _mode_unestablished(
                    "genesis_plus_gx",
                    "the save story splits on the content's class (a cartridge fills the "
                    "frontend's SRAM interface, a CD writes the core's own BRAM files), and no "
                    "content was named",
                ),
            ),
        )
    if extension in _GPGX_CARTRIDGE:
        return ModeChoice("cartridge")
    if extension == "bin":
        # A raw .bin is a cartridge dump in every ordinary library, and the
        # loader would still boot one carrying a CD header as a disc — the
        # mode's scoped file list says so instead of a second class guess.
        return ModeChoice("cartridge-raw-image")
    if extension not in _GPGX_CD:
        return ModeChoice(
            None,
            caveats=(
                _mode_unestablished(
                    "genesis_plus_gx",
                    f"the content's extension {extension!r} is outside both recorded classes "
                    "(cartridge: md/smd/gen/sms/gg/sg/68k/sgd/mdx/bms and raw .bin; CD: "
                    "cue/iso/chd/m3u), so which save story applies was never established",
                ),
            ),
        )
    system = reading.option_values[_GPGX_SYSTEM]
    cart = reading.option_values[_GPGX_CART]
    size = reading.option_values[_GPGX_SIZE]
    missing = [
        key
        for key, value in ((_GPGX_SYSTEM, system), (_GPGX_CART, cart), (_GPGX_SIZE, size))
        if value is None
    ]
    if missing:
        return ModeChoice(
            None, caveats=tuple(_value_unestablished("genesis_plus_gx", key) for key in missing)
        )
    alien = [
        (key, value)
        for key, value, known in (
            (_GPGX_SYSTEM, system, ("per bios", "per game")),
            (_GPGX_CART, cart, ("per cart", "per game")),
            (_GPGX_SIZE, size, ("disabled", *_GPGX_SIZES)),
        )
        if value not in known
    ]
    if alien:
        return ModeChoice(
            None,
            caveats=tuple(_unknown_value("genesis_plus_gx", key, value or "") for key, value in alien),
        )
    mode = _gpgx_cd_mode(system or "", cart or "", size or "")
    alternatives = [
        (
            _gpgx_cd_mode("per game" if system == "per bios" else "per bios", cart or "", size or ""),
            ((_GPGX_SYSTEM, "per game" if system == "per bios" else "per bios"),),
        )
    ]
    if size != "disabled":
        alternatives.append(
            (
                _gpgx_cd_mode(system or "", "per game" if cart == "per cart" else "per cart", size or ""),
                ((_GPGX_CART, "per game" if cart == "per cart" else "per cart"),),
            )
        )
    for other_size in ("disabled", *_GPGX_SIZES):
        if other_size != size:
            alternatives.append(
                (_gpgx_cd_mode(system or "", cart or "", other_size), ((_GPGX_SIZE, other_size),))
            )
    return ModeChoice(mode, alternatives=tuple(dict.fromkeys(alternatives)))


# The registry the card loader validates against: a card stating a
# ``governing_rule`` must have its function here, and the test suite holds
# the mirror claim — a rule with no card would be code describing nothing.
RULES: Mapping[str, Callable[[RuleReading], ModeChoice]] = {
    "mednafen_saturn": _mednafen_saturn,
    "hatari": _hatari,
    "scummvm": _scummvm,
    "swanstation": _swanstation,
    "mednafen_psx": _beetle_psx_rule("beetle_psx_", "mednafen_psx"),
    "mednafen_psx_hw": _beetle_psx_rule("beetle_psx_hw_", "mednafen_psx_hw"),
    "genesis_plus_gx": _genesis_plus_gx,
}
