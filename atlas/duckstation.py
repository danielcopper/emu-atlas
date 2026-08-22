"""DuckStation's own reads, shared by every route that asks it something.

Three things live here because two routes need them and neither owns them: the
**DataRoot probe** (which of two directories this launch keeps its settings in),
the ``LoadPathFromSettings`` shape (how one settings value becomes a directory),
and the **BIOS recognition table** packaged beside the code.

The last is the one worth naming. DuckStation names no BIOS file: it searches a
directory, skips every file whose size is not one of three, and recognises what
is left by hashing it against a table compiled into the binary
(``FindBIOSImageInDirectory``, bios.cpp:364-400 at the pin). So "is a BIOS here"
is a question about *content*, and answering it at all means carrying the table
— which is why ``atlas/data/duckstation_bios.json`` exists, generated from
upstream's source and pinned to the revision it was read at.
"""

from __future__ import annotations

import importlib.resources
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from atlas import emulator_settings, qt_ini
from atlas.machine import READ_MISSING, READ_OK, Machine

# The emulator's own directory name below whichever XDG base the launch picked,
# and the settings file inside it.
CONFIG_DIRECTORY = "duckstation"
CONFIG_FILENAME = "settings.ini"
# The card token this emulator answers under, and the key its settings file is
# addressed by in atlas/data/emulator_settings.json.
TOKEN = "DUCKSTATION"


@dataclass(frozen=True, slots=True)
class SettingsRead:
    """One DataRoot probe, resolved.

    ``root`` is always usable — the probe falls back to the candidate the
    environment-unset branch picks — and the other three fields say how much
    the machine actually told us: ``stated_path`` is the settings file that
    spoke, ``unreadable`` the one that exists and could not be read, and
    ``ambiguous`` marks the state where neither candidate holds a file, so
    which root the launch would use is decided by an environment variable that
    no file records.
    """

    root: str
    values: Mapping[tuple[str, str], str]
    stated_path: str | None
    unreadable: str | None
    ambiguous: bool


def settings_candidates(*, config_home: str, data_home: str) -> tuple[str, ...]:
    """Where this launch may open ``settings.ini``, in the order the probe reads.

    The order and the two bases are the settings table's statement, not this
    module's: ``$XDG_CONFIG_HOME/duckstation`` where that variable is set and
    absolute, else ``~/.local/share/duckstation`` (qthost.cpp:562-582).
    Nothing on the machine records which branch a launch took, so both are
    candidates and the file that exists speaks for itself.
    """
    return emulator_settings.settings_file(TOKEN, CONFIG_FILENAME).locations(
        config_home=config_home, data_home=data_home
    )


def data_root_candidates(*, config_home: str, data_home: str) -> tuple[str, ...]:
    """The DataRoots those candidates hang off — each settings file's own directory."""
    return tuple(
        os.path.dirname(path)
        for path in settings_candidates(config_home=config_home, data_home=data_home)
    )


def read_settings(machine: Machine, *, config_home: str, data_home: str) -> SettingsRead:
    """Read ``settings.ini`` from whichever DataRoot holds one."""
    candidates = settings_candidates(config_home=config_home, data_home=data_home)
    for path in candidates:
        root = os.path.dirname(path)
        result = machine.read_text(path)
        if result.status == READ_MISSING:
            continue
        if result.status != READ_OK:
            return SettingsRead(
                root=root, values={}, stated_path=None, unreadable=path, ambiguous=False
            )
        return SettingsRead(
            root=root,
            values=qt_ini.values(result.text or ""),
            stated_path=path,
            unreadable=None,
            ambiguous=False,
        )
    return SettingsRead(
        root=os.path.dirname(candidates[-1]),
        values={},
        stated_path=None,
        unreadable=None,
        ambiguous=True,
    )


def load_path(
    values: Mapping[tuple[str, str], str], root: str, section: str, name: str, default: str
) -> str:
    """``EmuFolders::LoadPathFromSettings`` (settings.cpp:1953-1962) as a read.

    An unset *or empty* value is the default, and anything relative — the
    default included — hangs off the DataRoot. Upstream then calls
    ``Path::RealPath``; atlas resolves links at the answer instead, so a
    caller sees both the path the emulator composes and where it lands.
    """
    value = values.get((section, name), "") or default
    return value if os.path.isabs(value) else os.path.join(root, value)


@dataclass(frozen=True, slots=True)
class BiosImage:
    """One row of DuckStation's table: what these bytes are, and how much it wants them.

    ``priority`` is upstream's own de-prioritisation and reads backwards from
    the word: **lower wins**. Launch-console images sit at 50, PS2 ones at 100
    and PAL PS2 ones at 150, each with a comment saying why (bios.cpp:42-45).
    """

    name: str
    region: str
    md5: str
    priority: int
    fast_boot_patch: str


@dataclass(frozen=True, slots=True)
class BiosCandidate:
    """One file the search kept: it is of an accepted size, and this is what it is.

    ``image`` is ``None`` for bytes the table does not know — a state
    DuckStation boots anyway, with a warning, so it belongs among the
    candidates rather than outside them.
    """

    path: str
    image: BiosImage | None


@dataclass(frozen=True, slots=True)
class BiosPick:
    """The file a launch would boot, and every file that ranks exactly with it."""

    chosen: BiosCandidate
    tied: tuple[BiosCandidate, ...]

    @property
    def decided(self) -> bool:
        """Did the files alone decide it? ``False`` when only directory order would."""
        return len(self.tied) == 1


class BiosTable:
    """The packaged recognition table, read-only, by content.

    ``sizes`` is the pre-filter and is part of the same rule: a file of any
    other size is skipped before a byte of it is read.
    """

    def __init__(
        self,
        images: tuple[BiosImage, ...],
        sizes: tuple[int, ...],
        openbios: Mapping[str, Any],
        meta: Mapping[str, Any],
    ) -> None:
        self._images = images
        self._by_md5 = {image.md5: image for image in images}
        self._sizes = sizes
        self._openbios = dict(openbios)
        self._meta = dict(meta)

    @property
    def meta(self) -> dict[str, Any]:
        """The table's ``_meta`` block — upstream revision and generation date."""
        return dict(self._meta)

    @property
    def images(self) -> tuple[BiosImage, ...]:
        """Every row, in upstream's order."""
        return self._images

    @property
    def sizes(self) -> tuple[int, ...]:
        """The file sizes the search accepts, ascending."""
        return self._sizes

    @property
    def openbios(self) -> dict[str, Any]:
        """The signature-recognised replacement BIOS: its bytes and their offset."""
        return dict(self._openbios)

    def accepts_size(self, size: int | None) -> bool:
        """Would the search keep a file of this size? ``None`` (unknown) is not a yes."""
        return size is not None and size in self._sizes

    def identify(self, md5: str) -> BiosImage | None:
        """The row these bytes are, or ``None`` — which is not a verdict on the file.

        DuckStation boots an unrecognised image and says so in a warning
        (``Using an unknown BIOS: {}``), so "not in the table" means unknown,
        never wrong.
        """
        return self._by_md5.get(md5.lower())

    def pick(self, candidates: Sequence[BiosCandidate], region: str) -> BiosPick | None:
        """Which candidate the console of *region* would boot, and what ties with it.

        DuckStation's own three tests, in its own order (bios.cpp:387-395): a
        known image is never displaced by an unknown one, a region match is
        never displaced by a mismatch, and between two known images the lower
        ``priority`` number holds. What upstream does with what is left is the
        part atlas cannot copy — it keeps the **last** equally ranked file the
        directory handed it, so two of them make the answer a property of
        ``readdir`` order. The seam enumerates sorted, so a tie is reported as
        a tie rather than resolved into a claim.
        """
        if not candidates:
            return None
        ranked = sorted(candidates, key=lambda c: self._rank(c, region))
        best = self._rank(ranked[0], region)
        tied = tuple(c for c in ranked if self._rank(c, region) == best)
        return BiosPick(chosen=tied[0], tied=tied)

    def _rank(self, candidate: BiosCandidate, region: str) -> tuple[int, int, int]:
        image = candidate.image
        if image is None:
            return (1, 1, 0)
        return (0, 0 if self.matches_region(image, region) else 1, image.priority)

    @staticmethod
    def matches_region(image: BiosImage, region: str) -> bool:
        """``IsValidBIOSForRegion`` (bios.cpp:228-231): ``any`` on either side matches."""
        return region == "any" or image.region == "any" or image.region == region


def _image(entry: Any, index: int) -> BiosImage:
    where = f"duckstation_bios: images[{index}]"
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: expected an object, got {entry!r}")
    missing = {"name", "region", "md5", "priority", "fast_boot_patch"} - set(entry)
    if missing:
        raise ValueError(f"{where}: missing {sorted(missing)}")
    return BiosImage(
        name=str(entry["name"]),
        region=str(entry["region"]),
        md5=str(entry["md5"]).lower(),
        priority=int(entry["priority"]),
        fast_boot_patch=str(entry["fast_boot_patch"]),
    )


def load_bios_table(text: str | None = None) -> BiosTable:
    """Load the packaged table (or *text* when supplied, for tests)."""
    if text is None:
        text = (
            importlib.resources.files("atlas")
            .joinpath("data", "duckstation_bios.json")
            .read_text(encoding="utf-8")
        )
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("duckstation_bios: expected an object at the top level")
    images = raw.get("images")
    if not isinstance(images, list) or not images:
        raise ValueError("duckstation_bios: images must be a non-empty list")
    sizes = raw.get("sizes")
    if not isinstance(sizes, dict) or not sizes:
        raise ValueError("duckstation_bios: sizes must be a non-empty object")
    return BiosTable(
        images=tuple(_image(entry, index) for index, entry in enumerate(images)),
        sizes=tuple(sorted(int(size) for size in sizes.values())),
        openbios=raw.get("openbios", {}),
        meta=raw.get("_meta", {}),
    )


_TABLE: BiosTable | None = None


def bios_table() -> BiosTable:
    """The packaged table, loaded once."""
    global _TABLE
    if _TABLE is None:
        _TABLE = load_bios_table()
    return _TABLE
