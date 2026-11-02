"""The build tripwire for the libretro Dolphin core: the answer must describe the deployed build.

The core's save answer (:func:`atlas.installations._dolphin_core_placement`)
cites every source fact at libretro/dolphin fd1aca3a and reads two facts of
that build that the source alone does not state. Both are held to the
deployed ``dolphin_libretro.so`` here, by reading its bytes — nothing is
loaded or run:

* **the revision.** The build states it through its SCM revision, one whole
  NUL-delimited constant ``fd1aca3a`` — the short hash its ``library_version``
  reports — so the binary is searched for exactly that constant as raw bytes,
  a NUL on each side, the way ``tests/test_standalone_build_tripwire.py`` holds
  a standalone build's stamp. An upgraded core carries another hash and fails
  here instead of being described by stale lines.
* **the strtoll.** A slot device id goes through ``strtoll`` in base 0
  (StringUtil.h:62-99 at fd1aca3a), and which C function that is depends on
  the glibc the build was linked against: this core imports the plain
  ``strtoll``, which stops at a ``0b`` prefix, and not C23's
  ``__isoc23_strtoll``, which reads it — the opposite of every standalone
  Dolphin-family build (``tests/test_dolphin_strtoll_tripwire.py``). So the
  core's tree reads no binary prefix (``_DolphinTree.binary_prefix``), and a
  build that changed its import fails here.

**What this cannot see.** A stamp is a floor, not a proof that the save
behaviour is unchanged: a revision can move without the save code moving, and
the save code cannot move without the revision moving only as long as the
build keeps stamping it. The import table says which ``strtoll`` the binary
can call, conclusive only because it holds exactly one of the two.

Skipped where the core is not deployed, like the tripwires beside it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.installations import (
    _DOLPHIN_CORE_LINES,  # pyright: ignore[reportPrivateUsage] - the citation table this pins
    _DOLPHIN_CORE_REVISION,  # pyright: ignore[reportPrivateUsage] - the revision this pins
    _DOLPHIN_CITATION_SLOTS,  # pyright: ignore[reportPrivateUsage] - the slots a build's table fills
)
from tests.test_dolphin_strtoll_tripwire import (
    C23_STRTOLL,
    PLAIN_STRTOLL,
    _synthetic_elf,  # pyright: ignore[reportPrivateUsage] - the reader's own test binaries
    undefined_dynamic_symbols,
)

DEPLOYED_CORE = Path(
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
    "/retroarch/rd_extras/cores/dolphin_libretro.so"
)


def stamp_disagreements(binary: bytes, revision: str) -> list[str]:
    """Why *binary* is not the build *revision* names — empty where it is."""
    if b"\x00" + revision.encode() + b"\x00" in binary:
        return []
    return [f"the core carries no whole constant {revision!r}: it is another build than the one cited"]


def strtoll_disagreements(imports: frozenset[str]) -> list[str]:
    """Why the core's ``strtoll`` is not the plain one its tree reads slots with."""
    wrong: list[str] = []
    if PLAIN_STRTOLL not in imports:
        wrong.append(f"the core imports no plain {PLAIN_STRTOLL}")
    if C23_STRTOLL in imports:
        wrong.append(f"the core imports {C23_STRTOLL}, which reads a 0b prefix its tree does not")
    return wrong


def _deployed_or_skip() -> bytes:
    if not DEPLOYED_CORE.is_file():
        pytest.skip(f"the Dolphin core is not deployed at {DEPLOYED_CORE}")
    return DEPLOYED_CORE.read_bytes()


class TestTheCheckItself:
    """The comparisons, over bytes written here — no machine involved."""

    def test_a_binary_carrying_the_revision_whole_passes(self):
        assert stamp_disagreements(b"junk\x00fd1aca3a\x00junk", "fd1aca3a") == []

    def test_a_binary_carrying_another_revision_is_named(self):
        assert stamp_disagreements(b"junk\x00deadbeef\x00junk", "fd1aca3a") != []

    def test_a_revision_inside_a_longer_constant_is_not_the_stamp(self):
        assert stamp_disagreements(b"\x00Dolphin/fd1aca3a15\x00", "fd1aca3a") != []

    def test_the_plain_import_alone_passes(self):
        assert strtoll_disagreements(undefined_dynamic_symbols(_synthetic_elf((PLAIN_STRTOLL,), ()))) == []

    def test_the_c23_import_is_named_twice(self):
        named = strtoll_disagreements(undefined_dynamic_symbols(_synthetic_elf((C23_STRTOLL,), ())))
        assert len(named) == 2

    def test_both_imports_are_named(self):
        named = strtoll_disagreements(
            undefined_dynamic_symbols(_synthetic_elf((PLAIN_STRTOLL, C23_STRTOLL), ()))
        )
        assert named == [f"the core imports {C23_STRTOLL}, which reads a 0b prefix its tree does not"]


class TestTheCitationTable:
    """The core's table names the build it pins and fills every slot the readings ask for."""

    def test_the_table_fills_exactly_the_readings_slots(self):
        assert set(_DOLPHIN_CORE_LINES) == set(_DOLPHIN_CITATION_SLOTS)

    def test_the_build_line_names_the_pinned_revision(self):
        assert _DOLPHIN_CORE_LINES["build"].endswith(f" {_DOLPHIN_CORE_REVISION}")


class TestTheDeployedCore:
    """Machine-bound: what the deployed core's bytes say."""

    def test_the_deployed_core_is_the_cited_revision(self):
        assert stamp_disagreements(_deployed_or_skip(), _DOLPHIN_CORE_REVISION) == []

    def test_the_deployed_core_links_the_plain_strtoll(self):
        assert strtoll_disagreements(undefined_dynamic_symbols(_deployed_or_skip())) == []
