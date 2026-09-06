"""Tests for atlas.squashfs — the reader against real mksquashfs output.

The fixtures are genuine mksquashfs images behind a minimal ELF prefix
(``tests/data/make_appimage_fixtures.py``), one per codec: the gzip twin
proves the walk — directories, fragments, multi-block files, all three
symlink shapes — on every interpreter, and the zstd twin proves exactly the
codec gate: readable where a PEP 784 provider exists (one a host registered,
``compression.zstd``, or its published backport ``backports.zstd``), the
honest ``capability-missing`` where none does. The two carry identical
content, so nothing about the walk hides behind the codec.

The registration tests never need a real codec: a provider whose
``decompress`` raises a marker proves *which* object the read called, which
is the whole claim precedence makes, and it makes that claim on every
interpreter rather than only on one that ships zstd.
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

_VENDORED = "_vendor.backports.zstd"


class _ProviderReached(Exception):
    """A marked provider's decompress ran — the read chose this object."""


def _marked(name: str) -> types.SimpleNamespace:
    """A provider that answers by raising its own name, so a read says which one it called."""

    def decompress(_data: bytes) -> bytes:
        raise _ProviderReached(name)

    return types.SimpleNamespace(decompress=decompress, __name__=name)


def _poisoned(name: str) -> types.SimpleNamespace:
    """A provider that must never be reached — reaching it fails the test that placed it."""

    def decompress(_data: bytes) -> bytes:
        raise AssertionError(f"{name} was reached and something outranks it")

    return types.SimpleNamespace(decompress=decompress)


@pytest.fixture(autouse=True)
def _leave_no_provider_registered():
    """The registry is process-global: no test in this file may hand one to the next."""
    yield
    squashfs.register_zstd_provider(None)


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
            pytest.skip("no zstd provider is importable here")
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


class TestAHostHandsOverItsProvider:
    """``register_zstd_provider`` — the codec handed over, not found by name (issue #400).

    A host that vendors the backport under its own root imports it as
    ``_vendor.backports.zstd``, which neither probed name matches; the
    registration is that host's seam, and it is tried before both names.
    """

    def test_a_registered_provider_serves_where_neither_name_imports(self, monkeypatch):
        # Both canonical names dead — nothing but the registration can answer,
        # and the marker proves the read reached it rather than the gate.
        monkeypatch.setitem(sys.modules, "compression.zstd", None)
        monkeypatch.setitem(sys.modules, "backports.zstd", None)
        squashfs.register_zstd_provider(_marked(_VENDORED))
        with pytest.raises(_ProviderReached, match=_VENDORED):
            squashfs.read_appimage_entry(ZSTD_IMAGE, CATALOGUE_ENTRY)

    def test_the_registered_provider_outranks_both_names(self, monkeypatch):
        # Both names poisoned and both importable: reaching either fails the
        # test, so the marker's exception is the precedence itself.
        monkeypatch.setitem(sys.modules, "compression.zstd", _poisoned("compression.zstd"))
        monkeypatch.setitem(sys.modules, "backports.zstd", _poisoned("backports.zstd"))
        squashfs.register_zstd_provider(_marked(_VENDORED))
        with pytest.raises(_ProviderReached, match=_VENDORED):
            squashfs.read_appimage_entry(ZSTD_IMAGE, CATALOGUE_ENTRY)

    def test_clearing_the_registration_hands_the_names_back_the_decision(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "compression.zstd", _marked("compression.zstd"))
        squashfs.register_zstd_provider(_marked(_VENDORED))
        squashfs.register_zstd_provider(None)
        with pytest.raises(_ProviderReached, match="compression.zstd"):
            squashfs.read_appimage_entry(ZSTD_IMAGE, CATALOGUE_ENTRY)

    def test_the_registered_provider_reads_the_zstd_twin(self, monkeypatch):
        # The end-to-end proof where a real codec exists: registered by object
        # while both names are dead, the image reads byte-for-byte.
        if not _HAS_ZSTD:
            pytest.skip("reading through a registered provider needs a real codec to register")
        real = importlib.import_module(
            "compression.zstd" if _HAS_STDLIB_ZSTD else "backports.zstd"
        )
        monkeypatch.setitem(sys.modules, "compression.zstd", None)
        monkeypatch.setitem(sys.modules, "backports.zstd", None)
        squashfs.register_zstd_provider(real)
        assert squashfs.read_appimage_entry(
            ZSTD_IMAGE, CATALOGUE_ENTRY
        ) == squashfs.read_appimage_entry(GZIP_IMAGE, CATALOGUE_ENTRY)

    def test_an_object_with_no_decompress_is_refused_where_it_was_handed_over(self):
        with pytest.raises(TypeError, match="decompress"):
            squashfs.register_zstd_provider(object())

    def test_a_refused_object_never_becomes_the_provider(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "compression.zstd", None)
        monkeypatch.setitem(sys.modules, "backports.zstd", None)
        with pytest.raises(TypeError):
            squashfs.register_zstd_provider(object())
        assert squashfs.zstd_provider() is None

    def test_the_refusal_names_all_three_routes(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "compression.zstd", None)
        monkeypatch.setitem(sys.modules, "backports.zstd", None)
        with pytest.raises(squashfs.CodecUnavailable) as refusal:
            squashfs.read_appimage_entry(ZSTD_IMAGE, CATALOGUE_ENTRY)
        message = str(refusal.value)
        assert "register_zstd_provider" in message
        assert "compression.zstd" in message
        assert "backports.zstd" in message


class TestTheRuntimeCanNameItsProvider:
    """``zstd_provider`` — which provider zstd images go through here, and by which route."""

    def test_a_registered_module_is_named_by_its_own_name(self):
        squashfs.register_zstd_provider(_marked(_VENDORED))
        assert squashfs.zstd_provider() == squashfs.ZstdProvider(_VENDORED, True)

    def test_a_nameless_provider_is_named_by_its_type(self):
        class HostZstd:
            def decompress(self, data: bytes) -> bytes:
                return data

        squashfs.register_zstd_provider(HostZstd())
        assert squashfs.zstd_provider() == squashfs.ZstdProvider("HostZstd", True)

    def test_the_route_is_read_from_the_registry_not_guessed_from_the_name(self):
        # A host may hand over the very module the probe would have imported.
        # The name is then a probed name and the route is still registration —
        # inferring one from the other would report this as an import.
        squashfs.register_zstd_provider(_marked("compression.zstd"))
        assert squashfs.zstd_provider() == squashfs.ZstdProvider("compression.zstd", True)

    def test_an_imported_provider_is_named_by_the_probed_name(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "compression.zstd", None)
        monkeypatch.setitem(sys.modules, "backports.zstd", _marked(_VENDORED))
        # The name is the one the probe asked for, not the object's own: what
        # answered here is whatever sits under `backports.zstd`.
        assert squashfs.zstd_provider() == squashfs.ZstdProvider("backports.zstd", False)

    def test_no_provider_at_all_is_named_by_nothing(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "compression.zstd", None)
        monkeypatch.setitem(sys.modules, "backports.zstd", None)
        assert squashfs.zstd_provider() is None


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
