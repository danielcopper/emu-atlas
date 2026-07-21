"""Save placements — templates with named holes, not resolved paths.

A :class:`SavePlacement` is the answer to "where does this installation keep the
save for this ROM?". It is a template because the concrete path cannot always be
known from configs alone: RetroArch names the save after the ROM file
(``<rom_stem>``), it may nest saves under the ROM's content folder
(``<content_dir>``), and a standalone install may leave its saves root unset
(``<savefile_directory>``). Whoever holds the ROM at hand fills the holes; the
template says exactly which holes remain and why (``sources``).

The directory math is extraction-faithful to decky-romm-sync
(``domain/save_path.py`` ``resolve_save_dir`` and ``compute_local_save_target``):

- ``savefiles_in_content_dir`` → the save lives next to the ROM; the directory is
  the ROM's own folder, an unfilled ``<content_dir>``.
- otherwise the directory is the saves root, with a per-content component appended
  when ``sort_by_content`` (the ROM's folder name) and a per-core component when
  ``sort_by_core`` (the RetroArch core name).
- the filename is always ``<rom_stem>.srm`` — RetroArch keys the save off the ROM
  basename, and ``srm`` is the default extension.

Pure compute. No I/O.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_DEFAULT_SAVE_EXTENSION = "srm"


@dataclass(frozen=True, slots=True)
class SavePlacement:
    """A save location as a template with named holes and provenance.

    ``dir`` and ``filename`` are template strings; ``needs`` lists the holes that
    remain unfilled (in the order they appear from the saves root outward, with
    ``rom_stem`` last since the filename always carries it). ``sources`` is the
    provenance trail: which config value or default produced each governing part.
    """

    dir: str
    filename: str
    needs: tuple[str, ...]
    sources: tuple[str, ...]


def build_save_placement(
    *,
    saves_root: str | None,
    savefiles_in_content_dir: bool,
    sort_by_content: bool,
    sort_by_core: bool,
    core: str | None,
    rom_dir_name: str | None,
    sources: tuple[str, ...],
) -> SavePlacement:
    """Compose a :class:`SavePlacement` from a saves root and the layout decision.

    ``saves_root`` is the concrete saves root when known, or ``None`` when it is
    itself an unfilled ``<savefile_directory>`` hole (a standalone install whose
    cfg leaves ``savefile_directory`` unset). ``core`` and ``rom_dir_name`` are
    the caller's fills for the per-core and per-content holes; when absent the
    corresponding hole is left in the template and listed in ``needs``.
    """
    needs: list[str] = []
    all_sources = list(sources)

    if savefiles_in_content_dir:
        directory = "<content_dir>"
        needs.append("content_dir")
        all_sources.append("layout: saves live next to the ROM; <content_dir> is the ROM's own directory")
    else:
        if saves_root is None:
            parts = ["<savefile_directory>"]
            needs.append("savefile_directory")
        else:
            parts = [saves_root]

        if sort_by_content:
            if rom_dir_name is not None:
                parts.append(rom_dir_name)
            else:
                parts.append("<content_dir>")
                needs.append("content_dir")

        if sort_by_core:
            if core is not None:
                parts.append(core)
            else:
                parts.append("<core>")
                needs.append("core")

        directory = os.path.join(*parts)

    needs.append("rom_stem")
    filename = f"<rom_stem>.{_DEFAULT_SAVE_EXTENSION}"

    return SavePlacement(
        dir=directory,
        filename=filename,
        needs=tuple(needs),
        sources=tuple(all_sources),
    )
