"""Tests for atlas.squashfs — the reader against real mksquashfs output.

The fixtures are genuine mksquashfs images behind a minimal ELF prefix
(``tests/data/make_appimage_fixtures.py``), one per codec: the gzip twin
proves the walk — directories, fragments, multi-block files, all three
symlink shapes — on every interpreter, and the zstd twin proves exactly the
codec gate: readable where a PEP 784 provider exists (``compression.zstd``,
or its published backport ``backports.zstd``), the honest
``capability-missing`` where none does. The two carry identical content,
so nothing about the walk hides behind the codec.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from atlas import squashfs
from atlas.machine import (
    APPIMAGE_CAPABILITY_MISSING,
    APPIMAGE_ENTRY_MISSING,
    APPIMAGE_NOT_APPIMAGE,
    READ_INVALID_TEXT,
    READ_MISSING,
    READ_OK,
    RealMachine,
)

DATA = Path(__file__).parent / "data"
GZIP_IMAGE = str(DATA / "esde-like.gzip.appimage")
ZSTD_IMAGE = str(DATA / "esde-like.zstd.appimage")
CATALOGUE_ENTRY = "usr/share/es-de/resources/systems/linux/es_systems.xml"


def _provider_exists(name: str) -> bool:
    # find_spec raises where the dotted parent itself is absent (a 3.12
    # interpreter has no `compression` package at all).
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


_HAS_STDLIB_ZSTD = _provider_exists("compression.zstd")
_HAS_ZSTD = _HAS_STDLIB_ZSTD or _provider_exists("backports.zstd")


class TestTheReaderWalksARealImage:
    def test_the_catalogue_entry_reads(self):
        data = squashfs.read_appimage_entry(GZIP_IMAGE, CATALOGUE_ENTRY)
        assert b"<systemList>" in data
        assert b"mgba_libretro.so" in data

    def test_a_multi_block_file_reads_byte_exact(self):
        # 10 KiB over three 4-KiB blocks — the block list and the tail both
        # have to be walked correctly for this to come back identical.
        expected = b"".join(bytes([i % 251]) * 64 for i in range(160))
        assert squashfs.read_appimage_entry(GZIP_IMAGE, "usr/share/big.bin") == expected

    def test_an_absolute_symlink_resolves(self):
        direct = squashfs.read_appimage_entry(GZIP_IMAGE, CATALOGUE_ENTRY)
        assert squashfs.read_appimage_entry(GZIP_IMAGE, "usr/links/absolute") == direct

    def test_an_updir_symlink_resolves(self):
        direct = squashfs.read_appimage_entry(GZIP_IMAGE, CATALOGUE_ENTRY)
        assert squashfs.read_appimage_entry(GZIP_IMAGE, "usr/links/updir") == direct

    def test_a_directory_symlink_mid_path_resolves(self):
        direct = squashfs.read_appimage_entry(GZIP_IMAGE, CATALOGUE_ENTRY)
        via_link = "usr/esde-dir/resources/systems/linux/es_systems.xml"
        assert squashfs.read_appimage_entry(GZIP_IMAGE, via_link) == direct

    def test_a_missing_entry_is_its_own_refusal(self):
        with pytest.raises(squashfs.EntryNotFound):
            squashfs.read_appimage_entry(GZIP_IMAGE, "usr/share/nope.xml")

    def test_a_directory_is_not_a_file(self):
        with pytest.raises(squashfs.EntryNotFound):
            squashfs.read_appimage_entry(GZIP_IMAGE, "usr/share")

    def test_a_file_that_is_no_appimage_is_a_structure_error(self, tmp_path):
        impostor = tmp_path / "impostor.AppImage"
        impostor.write_bytes(b"MZ this is not an ELF at all")
        with pytest.raises(squashfs.SquashfsError):
            squashfs.read_appimage_entry(str(impostor), CATALOGUE_ENTRY)


class TestTheCodecGate:
    def test_the_zstd_twin_reads_where_the_codec_exists(self):
        if not _HAS_ZSTD:
            pytest.skip("compression.zstd needs Python >= 3.14")
        assert squashfs.read_appimage_entry(
            ZSTD_IMAGE, CATALOGUE_ENTRY
        ) == squashfs.read_appimage_entry(GZIP_IMAGE, CATALOGUE_ENTRY)

    def test_without_the_codec_the_refusal_names_the_capability(self):
        if _HAS_ZSTD:
            pytest.skip("this interpreter has the codec — the gate cannot fire")
        with pytest.raises(squashfs.CodecUnavailable):
            squashfs.read_appimage_entry(ZSTD_IMAGE, CATALOGUE_ENTRY)

    def test_with_no_provider_at_all_the_gate_fires_on_any_interpreter(self, monkeypatch):
        # A None entry in sys.modules makes the import raise ModuleNotFoundError,
        # so the both-absent state is testable even where the stdlib module exists.
        monkeypatch.setitem(sys.modules, "compression.zstd", None)
        monkeypatch.setitem(sys.modules, "backports.zstd", None)
        with pytest.raises(squashfs.CodecUnavailable):
            squashfs.read_appimage_entry(ZSTD_IMAGE, CATALOGUE_ENTRY)

    def test_the_backport_serves_where_the_stdlib_module_is_absent(self, monkeypatch):
        # backports.zstd is the same code under another name — aliasing the real
        # module to that name is exactly the state a host that vendors it creates.
        if not _HAS_STDLIB_ZSTD:
            pytest.skip("proving the fallback needs the real stdlib codec to alias")
        real = importlib.import_module("compression.zstd")
        monkeypatch.setitem(sys.modules, "compression.zstd", None)
        monkeypatch.setitem(sys.modules, "backports.zstd", real)
        assert squashfs.read_appimage_entry(
            ZSTD_IMAGE, CATALOGUE_ENTRY
        ) == squashfs.read_appimage_entry(GZIP_IMAGE, CATALOGUE_ENTRY)

    def test_the_stdlib_module_outranks_the_backport(self, monkeypatch):
        # A poisoned backport proves the probe never reaches it while the
        # stdlib module answers.
        if not _HAS_STDLIB_ZSTD:
            pytest.skip("the stdlib module must exist to outrank anything")

        def poisoned(_data: bytes) -> bytes:
            raise AssertionError("the probe must prefer compression.zstd")

        monkeypatch.setitem(
            sys.modules, "backports.zstd", types.SimpleNamespace(decompress=poisoned)
        )
        assert b"<systemList>" in squashfs.read_appimage_entry(ZSTD_IMAGE, CATALOGUE_ENTRY)


class TestTheSeamMapsEveryOutcome:
    """RealMachine.read_appimage_text — one status per distinct failure, never collapsed."""

    def test_a_readable_entry_is_ok(self):
        result = RealMachine().read_appimage_text(GZIP_IMAGE, CATALOGUE_ENTRY)
        assert result.status == READ_OK
        assert result.text is not None
        assert "<systemList>" in result.text

    def test_an_absent_appimage_is_missing(self):
        result = RealMachine().read_appimage_text("/nowhere/none.AppImage", CATALOGUE_ENTRY)
        assert result.status == READ_MISSING

    def test_an_absent_entry_is_entry_missing(self):
        result = RealMachine().read_appimage_text(GZIP_IMAGE, "nope/nope.xml")
        assert result.status == APPIMAGE_ENTRY_MISSING

    def test_an_impostor_is_not_appimage(self, tmp_path):
        impostor = tmp_path / "impostor.AppImage"
        impostor.write_bytes(b"\x7fELF but truncated")
        result = RealMachine().read_appimage_text(str(impostor), CATALOGUE_ENTRY)
        assert result.status == APPIMAGE_NOT_APPIMAGE

    def test_binary_entry_bytes_are_invalid_text(self):
        result = RealMachine().read_appimage_text(GZIP_IMAGE, "not-text.bin")
        assert result.status == READ_INVALID_TEXT

    def test_the_zstd_image_without_codec_is_capability_missing(self):
        if _HAS_ZSTD:
            pytest.skip("this interpreter has the codec — the gate cannot fire")
        result = RealMachine().read_appimage_text(ZSTD_IMAGE, CATALOGUE_ENTRY)
        assert result.status == APPIMAGE_CAPABILITY_MISSING
