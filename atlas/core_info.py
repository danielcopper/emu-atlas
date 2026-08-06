"""RetroArch ``.info`` files — the format, and the firmware a core declares.

RetroArch ships a ``<core>.info`` file next to every ``<core>.so`` in its cores
directory. It looks like an INI file, and it is one in the most literal sense:
there is no second parser for it. ``core_info_get_config_file`` hands the path
to ``config_file_new_from_path_to_string`` (``core_info.c:1631-1642``), which
reads the file and walks it through the very ``config_file_parse_line``
pipeline ``retroarch.cfg`` goes through (``config_file.c:829-852``, ``:634-688``).
The grammar is therefore the one ported in :mod:`atlas.retroarch_cfg`, down to
the first-of-a-repeated-key rule and the NUL cut.

What *is* specific to ``.info`` is the firmware enumeration: which keys a core
info read asks for, and how many. That is :func:`enumerate_firmware`, a port of
``core_info_resolve_firmware``. Both halves are pure text in, value objects out
— the caller decides how to interpret a field and never touches the filesystem
here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from atlas.retroarch_cfg import cfg_bool, cfg_uint, parse_cfg_text


def parse_core_info(text: str) -> dict[str, str]:
    """Parse a ``.info`` file's content into a key-value dict.

    This is :func:`atlas.retroarch_cfg.parse_cfg_text` under the name the
    domain uses, because RetroArch reads both file kinds with one parser (see
    the module docstring). What follows from that, and is not what an INI
    reader would do: a key is a run of graph characters that must be followed
    by ``=``, so ``corename="mGBA"`` sets nothing; a quoted value ends at the
    **next** quote, not at the last one on the line; a ``#`` outside a string
    literal starts a comment; the **first** of a repeated key wins; and nothing
    past a NUL byte is read at all.
    """
    return parse_cfg_text(text)


FIRMWARE_COUNT = "firmware_count"

_FIRMWARE = "firmware"
_PATH = "path"
_OPT = "opt"
_DESC = "desc"

# ``char prefix[12]`` holds "firmware" plus what snprintf writes into the four
# bytes behind it (``core_info.c:1577``, ``:1590-1599``).
_PREFIX_ROOM = 3


@dataclass(frozen=True, slots=True)
class FirmwareSlot:
    """One firmware slot of a core, as ``core_info_resolve_firmware`` fills it.

    A slot is an array element, not a line in the file: RetroArch allocates
    ``firmware_count`` of them and then looks up each one's three keys.
    ``path`` is always non-empty here — a slot whose ``firmwareN_path`` is
    absent or empty keeps the NULL its ``calloc`` gave it and is skipped by
    every read that follows (``core_info.c:1610-1614``, ``:2378``), so it
    declares nothing and :func:`enumerate_firmware` does not return one.
    """

    index: int
    path: str
    description: str
    optional: bool


@dataclass(frozen=True, slots=True)
class FirmwareEnumeration:
    """The firmware one ``.info`` declares, bounded by its own ``firmware_count``.

    ``count`` is that field verbatim, as the file spells it — empty when the
    file states none. ``unread`` names the ``firmwareN_path`` keys the file
    states and this enumeration never asked for; they are the gap between what
    the file says and what the emulator does, and nothing else in a firmware
    answer would show it.
    """

    slots: tuple[FirmwareSlot, ...]
    count: str
    unread: tuple[str, ...]


def firmware_key(index: int, field: str) -> str:
    """The key a core info read looks up for one slot — the exact spelling.

    ``core_info_resolve_firmware`` *composes* this key, it never parses one:
    ``"firmware"``, then ``snprintf(prefix + 8, 4, "%u_", i)``, then the field
    name (``core_info.c:1590-1616``). Two consequences worth naming. Nothing
    but this spelling is ever looked up, so ``firmware00_path`` and a
    ``firmware٠_path`` written with non-ASCII digits are keys no read asks for.
    And the four bytes behind the prefix are part of the spelling: from index
    100 on the ``_`` no longer fits, and RetroArch asks for
    ``firmware100path``.
    """
    return f"{_FIRMWARE}{f'{index}_'[:_PREFIX_ROOM]}{field}"


def _slot_of_path_key(key: str) -> str:
    """The slot part of a ``firmware…path`` key — empty when *key* names no slot.

    Only a key that could belong to a slot is worth weighing against the count,
    and that is one whose middle starts with an ASCII digit: ``firmware_path``
    names no slot, ``firmware00_path`` names one badly. Both spellings the
    composer can produce are covered — with the separator, and without it from
    index 100 on (:func:`firmware_key`).
    """
    if not (key.startswith(_FIRMWARE) and key.endswith(_PATH)):
        return ""
    middle = key[len(_FIRMWARE) : -len(_PATH)]
    return middle if middle[:1].isascii() and middle[:1].isdigit() else ""


def _slot_read_by(key: str, limit: int) -> int | None:
    """The slot *key* answers in a run of *limit* slots — ``None`` when no run does.

    The check runs the composer forwards: whatever the key looks like, it is
    read only when it is *exactly* what :func:`firmware_key` would have asked
    for at that index. So ``firmware00_path`` is not slot 0, and a key past the
    count is not read at all.
    """
    digits = _slot_of_path_key(key).rstrip("_")
    if not (digits.isascii() and digits.isdigit()):
        return None
    index = int(digits)
    return index if index < limit and firmware_key(index, _PATH) == key else None


def _slot_at(fields: Mapping[str, str], index: int, path: str) -> FirmwareSlot:
    """Slot *index* as the enumeration fills the rest of it.

    ``firmwareN_opt`` goes through ``config_get_bool``, and a value outside its
    vocabulary is not a false: the write simply does not happen
    (``core_info.c:1603-1604``) and the slot keeps the ``false`` its ``calloc``
    left, which is the *required* end of the scale. So an absent flag, a
    ``TRUE`` and a ``yes`` all mean required — the direction that matters,
    because reading one of them as optional would let an answer go green over a
    file the core will not start without.
    """
    return FirmwareSlot(
        index=index,
        path=path,
        description=fields.get(firmware_key(index, _DESC), ""),
        optional=cfg_bool(fields.get(firmware_key(index, _OPT), "")) is True,
    )


def enumerate_firmware(fields: Mapping[str, str]) -> FirmwareEnumeration:
    """The firmware slots RetroArch enumerates from one ``.info``'s fields.

    ``core_info_resolve_firmware`` (``core_info.c:1572-1629``) reads
    ``firmware_count`` **first** and returns on the spot when that is not an
    unsigned it can read: no count, no firmware — a ``.info`` may list a dozen
    paths and the core is still started without a single one being asked for.
    With a count it allocates exactly that many slots and fills slots
    ``0 .. count-1`` by composing each key (:func:`firmware_key`).

    The count is therefore the enumeration, not a cross-check against it. A
    path declared at or past the count is invisible; a slot inside the count
    that the file says nothing about declares nothing; and a count larger than
    the number of declared paths simply leaves the surplus slots empty.

    That last point is why the walk goes over the paths the file states rather
    than counting up to the bound: a slot the file says nothing about declares
    nothing either way, and a ``firmware_count`` of four billion would
    otherwise be four billion lookups. Upstream never gets there either — it
    ``calloc``s the whole array first and abandons the core's firmware when
    that fails (``core_info.c:1584-1588``).
    """
    count = fields.get(FIRMWARE_COUNT, "")
    enumerated = cfg_uint(count)
    limit = 0 if enumerated is None else enumerated
    read: list[tuple[int, str]] = []
    unread: list[str] = []
    for key, value in fields.items():
        if not value or not _slot_of_path_key(key):
            continue
        index = _slot_read_by(key, limit)
        if index is None:
            unread.append(key)
        else:
            read.append((index, value))
    slots = tuple(_slot_at(fields, index, path) for index, path in sorted(read))
    return FirmwareEnumeration(slots, count, tuple(sorted(unread)))
