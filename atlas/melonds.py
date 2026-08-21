"""melonDS's configuration, read the way the emulator reads it.

One module for the read chain both questions share — the save placement and
the firmware expectations both hang off ``melonDS.toml``, and the emulator
reads that file one way (``Config::Load``, Config.cpp:785-803 at 1.1): the
TOML under ``<config home>/melonDS`` wherever it exists — even unparseable,
because the emulator catches the syntax error and runs on factory defaults
rather than falling back — and the pre-1.0 ``melonDS.ini`` beside it, line by
line, only where no TOML exists (the built-in migration, :682-775). A
``portable`` directory beside the executable would outrank the config home
(pathInit, main.cpp:180-214), and neither arrangement has one.

The accessors mirror melonDS's own ``Table`` reads (Config.cpp:555-598): a
value of the wrong type falls to the compiled default — the empty string,
``false``, ``0`` for everything this module reads, none of which appear in
the emulator's default lists — and ``Emu.ConsoleType`` is clamped to its
declared range (:562-568, IntRanges :79).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from typing import Any, Mapping

from atlas.machine import Machine, READ_MISSING, READ_OK

CONFIG_DIRNAME = "melonDS"
CONFIG_FILENAME = "melonDS.toml"
LEGACY_FILENAME = "melonDS.ini"

# Which branch of Config::Load produced the document — stated on the read so
# a resolver's provenance can say which file spoke without re-deriving it.
SOURCE_TOML = "toml"
SOURCE_TOML_INVALID = "toml-invalid"
SOURCE_LEGACY = "legacy"
SOURCE_DEFAULTS = "defaults"

# The slice of melonDS's legacy-key table this module reads (``LegacyFile``,
# Config.cpp at 1.1): legacy name -> (TOML path, type), the type as the table
# spells it (0 int, 1 bool, 2 string; LoadLegacyFile :747-767 converts with
# strtol). ``SaveFilePath`` is instance-unique and lands in ``Instance0``
# when read from the base file (:301, :688-700); the rest are global.
_LEGACY_KEYS: Mapping[str, tuple[str, int]] = {
    "SaveFilePath": ("Instance0.SaveFilePath", 2),  # :301
    "ExternalBIOSEnable": ("Emu.ExternalBIOSEnable", 1),  # :237
    "ConsoleType": ("Emu.ConsoleType", 0),  # :226
    "BIOS9Path": ("DS.BIOS9Path", 2),  # :239
    "BIOS7Path": ("DS.BIOS7Path", 2),  # :240
    "FirmwarePath": ("DS.FirmwarePath", 2),  # :241
    "DSiBIOS9Path": ("DSi.BIOS9Path", 2),  # :243
    "DSiBIOS7Path": ("DSi.BIOS7Path", 2),  # :244
    "DSiFirmwarePath": ("DSi.FirmwarePath", 2),  # :245
    "DSiNANDPath": ("DSi.NANDPath", 2),  # :246
}

# The one range that matters here (IntRanges, Config.cpp:79): a console type
# outside {0, 1} reads as the nearest bound, never as a refusal.
_CONSOLE_TYPE_RANGE = (0, 1)


@dataclass(frozen=True, slots=True)
class MelonConfig:
    """The document ``Config::Load`` produced, and where it came from.

    ``document`` is TOML-shaped whichever file spoke — a legacy read is
    mapped through the emulator's own key table first, so one set of
    accessors serves both. ``stated_file`` is the file that was read
    (``None`` where neither exists), ``source`` the branch of ``Load()``
    that produced the document.
    """

    document: Mapping[str, Any]
    stated_file: str | None
    source: str


@dataclass(frozen=True, slots=True)
class MelonConfigRead:
    """One config read: the document, or the path that could not be read."""

    config: MelonConfig | None
    unreadable: str | None


def config_dir(config_home: str) -> str:
    """``emuDirectory`` for a non-portable launch: ``<config home>/melonDS``."""
    return os.path.join(config_home, CONFIG_DIRNAME)


def _strtol(raw: str) -> int:
    """``strtol(raw, nullptr, 10)`` — sign, leading digits, 0 where none."""
    text = raw.lstrip(" \t")
    sign = 1
    if text[:1] in ("+", "-"):
        sign = -1 if text[0] == "-" else 1
        text = text[1:]
    digits = ""
    for ch in text:
        if ch not in "0123456789":
            break
        digits += ch
    return sign * int(digits) if digits else 0


def _legacy_scan(text: str) -> dict[str, str]:
    """Every recognized legacy line's raw value, a later line overwriting.

    One scan per line the way ``LoadLegacyFile`` scans (Config.cpp:705-717):
    the key, an immediate ``=``, then everything up to a tab or the line's
    end — at least one byte of it, because an empty value fails the scan and
    leaves the entry untouched.
    """
    values: dict[str, str] = {}
    for line in text.splitlines():
        name, separator, rest = line.partition("=")
        if not separator or name not in _LEGACY_KEYS:
            continue
        candidate = rest.partition("\t")[0]
        if candidate:
            values[name] = candidate
    return values


def _legacy_document(text: str) -> dict[str, Any]:
    """The legacy INI as the TOML-shaped tree ``LoadLegacyFile`` builds."""
    document: dict[str, Any] = {}
    for name, raw in _legacy_scan(text).items():
        path, kind = _LEGACY_KEYS[name]
        value: Any = raw
        if kind == 0:
            value = _strtol(raw)
        elif kind == 1:
            value = _strtol(raw) != 0
        node = document
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return document


def read_config(machine: Machine, config_home: str) -> MelonConfigRead:
    """``Config::Load``, performed as reads — the TOML, the legacy INI, or defaults."""
    toml_path = os.path.join(config_dir(config_home), CONFIG_FILENAME)
    result = machine.read_text(toml_path)
    if result.status not in (READ_OK, READ_MISSING):
        return MelonConfigRead(config=None, unreadable=toml_path)
    if result.status == READ_OK:
        try:
            document: Mapping[str, Any] = tomllib.loads(result.text or "")
        except tomllib.TOMLDecodeError:
            return MelonConfigRead(
                config=MelonConfig({}, toml_path, SOURCE_TOML_INVALID), unreadable=None
            )
        return MelonConfigRead(
            config=MelonConfig(document, toml_path, SOURCE_TOML), unreadable=None
        )
    ini_path = os.path.join(config_dir(config_home), LEGACY_FILENAME)
    legacy = machine.read_text(ini_path)
    if legacy.status not in (READ_OK, READ_MISSING):
        return MelonConfigRead(config=None, unreadable=ini_path)
    if legacy.status == READ_OK:
        return MelonConfigRead(
            config=MelonConfig(_legacy_document(legacy.text or ""), ini_path, SOURCE_LEGACY),
            unreadable=None,
        )
    return MelonConfigRead(config=MelonConfig({}, None, SOURCE_DEFAULTS), unreadable=None)


def _resolve(document: Mapping[str, Any], path: str) -> Any:
    node: Any = document
    for part in path.split("."):
        if not isinstance(node, Mapping):
            return None
        node = node.get(part)
    return node


def raw_value(config: MelonConfig, path: str) -> Any:
    """The value at *path* as the document holds it — for provenance, not reads."""
    return _resolve(config.document, path)


def get_string(config: MelonConfig, path: str) -> str:
    """``Table::GetString`` — a non-string value reads as the empty default."""
    value = _resolve(config.document, path)
    return value if isinstance(value, str) else ""


def get_bool(config: MelonConfig, path: str) -> bool:
    """``Table::GetBool`` — a non-boolean value reads as ``false``."""
    value = _resolve(config.document, path)
    return value if isinstance(value, bool) else False


def console_type(config: MelonConfig) -> int:
    """``Emu.ConsoleType`` the way ``GetInt`` reads it: default 0, clamped to {0, 1}.

    ``bool`` is excluded the way toml11 keeps integers and booleans apart —
    a TOML ``true`` is not an integer, so it falls to the default.
    """
    value = _resolve(config.document, "Emu.ConsoleType")
    if not isinstance(value, int) or isinstance(value, bool):
        value = 0
    low, high = _CONSOLE_TYPE_RANGE
    return min(max(value, low), high)


# The subsets of path keys ``verifySetup`` probes, in the emulator's own order
# (EmuInstance.cpp:633-667 at 1.1): the DS BIOS pair whenever the external-BIOS
# switch is on; then in DSi mode the DSi BIOS pair, the DSi firmware (external
# BIOS only) and the NAND; and in DS mode the DS firmware (external BIOS only).
# What is not probed is not required, and an answer says so by leaving it out.
_DS_BIOS_KEYS = ("DS.BIOS9Path", "DS.BIOS7Path")
_DS_FIRMWARE_KEY = "DS.FirmwarePath"
_DSI_KEYS_EXTBIOS = ("DSi.BIOS9Path", "DSi.BIOS7Path", "DSi.FirmwarePath", "DSi.NANDPath")
_DSI_KEYS_BUILTIN = ("DSi.BIOS9Path", "DSi.BIOS7Path", "DSi.NANDPath")


def probed_firmware_keys(extbios: bool, console: int) -> tuple[str, ...]:
    """``verifySetup``'s probe set for the two switches, in its own order.

    The emulator's gating, not the firmware model's: which files a launch
    really opens is decided here, and the model states requirements for
    exactly those.
    """
    keys: tuple[str, ...] = _DS_BIOS_KEYS if extbios else ()
    if console == 1:
        return keys + (_DSI_KEYS_EXTBIOS if extbios else _DSI_KEYS_BUILTIN)
    if extbios:
        return keys + (_DS_FIRMWARE_KEY,)
    return keys


def local_file_path(config_home: str, value: str) -> str:
    """``Platform::GetLocalFilePath`` — absolute stays, anything else joins the config dir.

    Platform.cpp:157-172 at 1.1: a relative spelling (the empty string
    included) is opened below ``emuDirectory``, which for a non-portable
    launch is the melonDS config directory. This is the door the BIOS and
    firmware paths go through (``OpenLocalFile``, :176-178) — unlike the
    save path, which is opened verbatim by the process and therefore
    anchors at the launch's working directory.
    """
    if os.path.isabs(value):
        return value
    directory = config_dir(config_home)
    return os.path.join(directory, value) if value else directory
