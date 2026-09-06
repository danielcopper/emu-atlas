"""WHDLoad content: which member PUAE launches, and the slave that names its saves.

WHDLoad redirects an installed program's writes to a base directory and gives
each program its own sub directory therein, whose name it derives from the
slave it was started with — "The name of the sub directory can be specified
using the SaveDir/K option or when not set will be derived by WHDLoad from the
infos of the Slave (ws_name or the Slave filename)" (WHDLoad manual,
``opt.html``, ``SavePath/K``). PUAE points that base at a volume of its own,
``SavePath=WHDSaves:`` (``whdload/WHDLoad.prefs:37`` at libretro/libretro-uae
0043cf9), so the per-game directory under the frontend's save root is named
from bytes inside the archive and from nothing else: an install archive's
directory, its slave's file name and its ``ws_name`` are three different
strings (the public Alien Breed install carries ``AlienBreedHD/``,
``AlienBreed.slave`` and ``Alien Breed``). This module reads those bytes.

**Which member is launched.** ``dc_get_image_type`` calls a member WHDLoad
content by its extension alone — ``lha``, ``slave`` or ``info``
(``libretro/libretro-dc.c:855-859``). Inside an extracted archive the core
takes the first such member whose slave or directory is really there
(``libretro-core.c:6332-6351``), and what it mounts as ``DH0:`` is the
archive itself for an ``.lha`` (:5706-5708) but, for a ``.slave`` or
``.info``, the directory beside it: the member's own directory joined with
its stem, falling back to that directory where no such subdirectory exists
(:5688-5700).

**Which slave is selected.** The boot script the core bakes in walks ``DH0:``
(``whdload/WHDLoad_files/S/Startup-Sequence:13`` changes into it; the same
script sits inside the baked ``WHDLoad.hdf``, so both WHDLoad modes select
alike). At :61-82 it does exactly this, and this module mirrors it:

- a file named ``load`` at the root replaces the whole launch, and no slave
  is named at all (:61-62);
- otherwise ``List #?.slav#?`` at the root — every entry whose name carries
  ``.slav``, matched the way AmigaDOS matches, without regard to case (:64);
- with none there and **no** ``.info`` at the root, the script descends into
  the root's directory and lists slaves there (:66-73);
- with none there and an ``.info`` at the root it stays put, and the only
  remaining branch takes a slave named after its own directory —
  ``<dir>/<dir>.slave`` (:74-81);
- anything that gets this far with no slave reaches ``S:WBSelect`` (:176-177),
  an interactive file requester over the ``.info`` files, and which program
  runs is then a person's click.

``List … TO ENV:`` writes *every* match, so two candidate slaves or two
candidate directories leave a value the following ``CD`` and ``WHDLoad``
cannot use; what happens then is not established, and this module answers
"no slave" rather than picking one. The README's "The first one is selected"
(``README.md:360``) describes the intent; the mechanism does not say which
first is meant, so it is not stated as fact here.

**The slave itself** is "a standard AmigaDOS executable" that "MUST consist
of only ONE hunk" (WHDLoad autodoc, ``WHDLoad.Slave/--Overview--``), and the
``WHDLoadSlave`` structure sits at the start of it. The hunk file's own
layout is read the way UAE reads it (``sources/src/debugmem.c:1526-1576`` at
0043cf9): the identifier ``0x3F3``, a zero longword where a resident-library
list would be, the hunk-count table, then hunk blocks — ``0x3E9`` code,
``0x3EA`` data, ``0x3EB`` uninitialised — each with its length in longwords,
memory-attribute bits masked off the type and the size. The structure's own
fields are the autodoc's, big-endian, and everything from ``ws_name`` on
"only evaluated by WHDLoad if ``ws_Version`` is set to >= 10" — so a slave
older than that states no name here, and this module answers ``None`` rather
than substituting the file name the manual mentions, whose spelling (with or
without the extension) the manual does not give.
"""

from __future__ import annotations

import posixpath
import struct
from dataclasses import dataclass
from typing import Iterable, Sequence

# The extensions dc_get_image_type calls WHDLoad content (libretro-dc.c:855-859).
SUFFIXES = ("lha", "slave", "info")

# What the boot script looks for, lower-cased: the AmigaDOS pattern
# ``#?.slav#?`` is ".slav" with anything on either side.
_SLAVE_FRAGMENT = ".slav"
_INFO_SUFFIX = ".info"
_SLAVE_SUFFIX = ".slave"
# A file of this name at the root replaces the launch entirely (:61-62).
_LOAD = "load"

_HUNK_HEADER = 0x3F3
_HUNK_CODE = 0x3E9
# The two high bits of a hunk type and of a hunk size are memory attributes,
# and a size carrying both is followed by an extra longword (debugmem.c
# :1550-1553, :1562).
_MEMORY_FLAGS = 0xC0000000
_LONG = 4
# UAE refuses a file whose hunk table is longer than this (debugmem.c:1537),
# and so does this reader — the bound is what keeps a corrupt length from
# being walked as a table.
_MAX_HUNKS = 1000

_WS_ID_OFFSET = 4
_WS_ID = b"WHDLOADS"
_WS_VERSION_OFFSET = 12
_WS_NAME_OFFSET = 36
# The version from which the fields beyond ws_ExpMem exist at all.
NAMED_FROM_VERSION = 10


class NotASlave(Exception):
    """The bytes are not a WHDLoad slave — the message says at which step."""


@dataclass(frozen=True, slots=True)
class Slave:
    """What a slave states about itself: the WHDLoad it needs, and its program's name.

    ``version`` is ``ws_Version``. ``name`` is ``ws_name``, the string
    WHDLoad shows in its splash window and derives the save sub directory
    from — ``None`` for a slave older than :data:`NAMED_FROM_VERSION`, which
    has no such field, and for one whose pointer or string is empty.
    """

    version: int
    name: str | None


def read_slave(data: bytes) -> Slave:
    """The structure at the start of a slave's single code hunk."""
    return _structure(_first_code_hunk(data))


def _first_code_hunk(data: bytes) -> bytes:
    """The bytes of the first code hunk — where the slave structure lives."""
    if _u32(data, 0) != _HUNK_HEADER:
        raise NotASlave("the file does not open with an AmigaDOS hunk header")
    if _u32(data, _LONG) != 0:
        # UAE's own loader reads only executables with no resident-library
        # list, and a slave is one; anything else is not what it would load.
        raise NotASlave("the hunk header names resident libraries, which a slave does not")
    first, last = _u32(data, 3 * _LONG), _u32(data, 4 * _LONG)
    if first > last or last - first + 1 > _MAX_HUNKS:
        raise NotASlave("the hunk header states a hunk range no executable has")
    position = 5 * _LONG
    for _ in range(last - first + 1):
        size = _u32(data, position)
        position += 2 * _LONG if size & _MEMORY_FLAGS == _MEMORY_FLAGS else _LONG
    if _u32(data, position) & ~_MEMORY_FLAGS != _HUNK_CODE:
        raise NotASlave("the first hunk of the file is not a code hunk")
    length = (_u32(data, position + _LONG) & ~_MEMORY_FLAGS) * _LONG
    hunk = data[position + 2 * _LONG : position + 2 * _LONG + length]
    if len(hunk) != length:
        raise NotASlave("the first code hunk is cut short by the end of the file")
    return hunk


def _structure(hunk: bytes) -> Slave:
    """Read ``ws_Version`` and ``ws_name`` out of the structure at the hunk's start."""
    if hunk[_WS_ID_OFFSET : _WS_ID_OFFSET + len(_WS_ID)] != _WS_ID:
        raise NotASlave("the code hunk does not open with the 'WHDLOADS' identifier")
    version = _u16(hunk, _WS_VERSION_OFFSET)
    if version < NAMED_FROM_VERSION:
        return Slave(version, None)
    pointer = _u16(hunk, _WS_NAME_OFFSET)
    return Slave(version, _relative_string(hunk, pointer) if pointer else None)


def _relative_string(hunk: bytes, pointer: int) -> str | None:
    """The NUL-terminated string a structure-relative pointer names."""
    end = hunk.find(b"\x00", pointer)
    if pointer >= len(hunk) or end < 0:
        raise NotASlave("ws_name points outside the slave's own hunk")
    return hunk[pointer:end].decode("latin-1") or None


def _u16(data: bytes, offset: int) -> int:
    if offset + 2 > len(data):
        raise NotASlave(f"the slave structure ends before offset {offset}")
    return struct.unpack_from(">H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    if offset + _LONG > len(data):
        raise NotASlave(f"the hunk file ends before offset {offset}")
    return struct.unpack_from(">I", data, offset)[0]


# ---------------------------------------------------------------------------
# What the core launches out of an archive, and what it mounts for it.
# ---------------------------------------------------------------------------


def launched_member(names: Sequence[str]) -> str | None:
    """The WHDLoad member PUAE takes out of an extracted archive (:6332-6351).

    The core's walk reads the extracted tree's top level and accepts the
    first entry whose extension says WHDLoad *and* whose slave or directory
    is really there — an ``.info`` counts only where the drawer it belongs to
    or a same-named ``.slave`` exists beside it. It then stops looking, so a
    second candidate never wins; but the walk is a directory listing in the
    filesystem's own order, so where two could be accepted which one is is
    not established, and this answers ``None`` rather than choosing.
    """
    entries = _top_level(names)
    accepted = [name for name in entries.files if _accepted_whdload(name, entries)]
    return accepted[0] if len(accepted) == 1 else None


def _accepted_whdload(name: str, entries: "_TopLevel") -> bool:
    stem, _, suffix = name.rpartition(".")
    if suffix.lower() not in SUFFIXES:
        return False
    if suffix.lower() != "info":
        return True
    # An info is accepted only where its drawer or a same-named slave exists.
    return stem in entries.directories or f"{stem}{_SLAVE_SUFFIX}" in entries.files


def mounted_root(member: str, names: Sequence[str]) -> str:
    """The prefix of *names* the core mounts as ``DH0:`` for that member (:5688-5700).

    An ``.lha`` member is mounted as itself, which this cannot express — the
    caller knows it holds a second archive. For a ``.slave`` or an ``.info``
    the core joins the member's directory with the member's stem and mounts
    that where it is a directory, else its parent: inside an extracted
    archive that is the drawer beside the member, or the whole extracted tree
    where there is none.
    """
    stem = posixpath.splitext(member)[0]
    prefix = f"{stem}/"
    return prefix if any(name.startswith(prefix) for name in names) else ""


def select_slave(names: Iterable[str]) -> str | None:
    """The slave the boot script selects from a mounted ``DH0:`` (:61-82).

    *names* are the paths under the mounted root, ``/``-separated. ``None``
    is every outcome that names no slave: the ``load`` override, a search
    that finds none, and a listing whose candidates the script's own
    mechanism cannot tell apart.
    """
    root = _top_level(names)
    if _has(root.files, _LOAD):
        return None
    here = _slaves(root.files)
    if here:
        return here[0] if len(here) == 1 else None
    if len(root.directories) != 1:
        return None
    directory = root.directories[0]
    if any(name.lower().endswith(_INFO_SUFFIX) for name in root.files):
        return _named_after(directory, directory, names)
    return _inside_first_directory(directory, names)


def _inside_first_directory(directory: str, names: Iterable[str]) -> str | None:
    """With no ``.info`` at the root the script descends first, then searches (:68-73)."""
    inner = _top_level(_under(directory, names))
    found = _slaves(inner.files)
    if found:
        return f"{directory}/{found[0]}" if len(found) == 1 else None
    if len(inner.directories) != 1:
        return None
    deeper = inner.directories[0]
    return _named_after(f"{directory}/{deeper}", deeper, names)


def _named_after(directory: str, stem: str, names: Iterable[str]) -> str | None:
    """The last branch: a slave named exactly after the directory holding it (:77-78)."""
    candidate = f"{directory}/{stem}{_SLAVE_SUFFIX}"
    return candidate if _has(names, candidate) else None


@dataclass(frozen=True, slots=True)
class _TopLevel:
    """One directory listing split the way ``List`` and ``List DIRS`` split it."""

    files: tuple[str, ...]
    directories: tuple[str, ...]


def _top_level(names: Iterable[str]) -> _TopLevel:
    """The entries directly in a listing: names without a separator, and first segments with one."""
    files: list[str] = []
    directories: list[str] = []
    for name in names:
        head, separator, _ = name.partition("/")
        target = directories if separator else files
        if head and head not in target:
            target.append(head)
    return _TopLevel(tuple(files), tuple(directories))


def _under(directory: str, names: Iterable[str]) -> list[str]:
    """The names inside one directory, relative to it."""
    prefix = f"{directory}/"
    return [name[len(prefix) :] for name in names if name.startswith(prefix)]


def _slaves(names: Iterable[str]) -> list[str]:
    """Every entry ``#?.slav#?`` matches, sorted so one listing gives one answer."""
    return sorted(name for name in names if _SLAVE_FRAGMENT in name.lower())


def _has(names: Iterable[str], wanted: str) -> bool:
    """Does the listing hold this name? AmigaDOS compares without regard to case."""
    lowered = wanted.lower()
    return any(name.lower() == lowered for name in names)
