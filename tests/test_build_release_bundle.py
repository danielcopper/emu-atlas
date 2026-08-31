"""Tests for scripts/build_release_bundle.py — the SHA256SUMS release manifest.

Only the manifest half runs here: assembling the bundle needs the pinned
runtime archive and is proven by the CI smoke (``smoke_release_bundle.py``).
What these tests guard is the anchor the release publishes for directory-copy
vendoring (issue #329): every artifact listed, every wheel-internal file
listed under its in-archive path, every digest the file's real digest — and
the whole thing reproducible from the artifacts alone. The expected set and
digests are re-derived independently here, so a manifest that omits a file or
misstates a digest fails.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from scripts import build_release_bundle as bundle

# A wheel is a zip of the installed tree — three files stand in for it, one
# per kind a consumer copies out: code, packaged data, dist-info.
WHEEL_FILES = {
    "atlas/__init__.py": b'__version__ = "0.0.0"\n',
    "atlas/data/core_audit.json": b"{}\n",
    "emu_atlas-0.0.0.dist-info/METADATA": b"Metadata-Version: 2.1\n",
}


def _make_wheel(directory: Path) -> Path:
    wheel = directory / "emu_atlas-0.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in WHEEL_FILES.items():
            archive.writestr(name, content)
    return wheel


def _make_artifact(directory: Path, name: str, content: bytes) -> Path:
    artifact = directory / name
    artifact.write_bytes(content)
    return artifact


def _write_manifest(tmp_path: Path) -> tuple[Path, list[Path]]:
    wheel = _make_wheel(tmp_path)
    tarball = _make_artifact(
        tmp_path, "emu-atlas-v0.0.0-x86_64-linux.tar.gz", b"stand-in bundle bytes"
    )
    vectors = _make_artifact(tmp_path, "emu-atlas-vectors-v0.0.0.tar.gz", b"stand-in vector bytes")
    manifest = bundle.write_manifest([tarball, wheel, vectors], wheel, tmp_path)
    return manifest, [tarball, wheel, vectors]


def _parse(manifest: Path) -> dict[str, str]:
    entries = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        entries[name] = digest
    return entries


class TestManifestShape:
    def test_every_artifact_and_every_wheel_file_is_listed(self, tmp_path):
        manifest, artifacts = _write_manifest(tmp_path)
        expected = {artifact.name for artifact in artifacts} | set(WHEEL_FILES)
        assert set(_parse(manifest)) == expected

    def test_digests_are_the_files_digests(self, tmp_path):
        manifest, artifacts = _write_manifest(tmp_path)
        entries = _parse(manifest)
        for artifact in artifacts:
            assert entries[artifact.name] == hashlib.sha256(artifact.read_bytes()).hexdigest()
        for name, content in WHEEL_FILES.items():
            assert entries[name] == hashlib.sha256(content).hexdigest()

    def test_lines_are_sha256sum_native(self, tmp_path):
        manifest, _ = _write_manifest(tmp_path)
        text = manifest.read_text(encoding="utf-8")
        assert text.endswith("\n")
        for line in text.splitlines():
            digest, separator, name = line[:64], line[64:66], line[66:]
            assert set(digest) <= set("0123456789abcdef")
            assert separator == "  "
            assert name
            assert not name.startswith(" ")

    def test_artifacts_come_first_then_wheel_files_each_group_sorted(self, tmp_path):
        manifest, artifacts = _write_manifest(tmp_path)
        names = list(_parse(manifest))
        artifact_names = sorted(artifact.name for artifact in artifacts)
        assert names == artifact_names + sorted(WHEEL_FILES)

    def test_the_manifest_is_reproducible_regardless_of_argument_order(self, tmp_path):
        manifest, artifacts = _write_manifest(tmp_path)
        first = manifest.read_bytes()
        wheel = next(a for a in artifacts if a.suffix == ".whl")
        again = bundle.write_manifest(list(reversed(artifacts)), wheel, tmp_path)
        assert again.read_bytes() == first

    def test_a_directory_entry_in_the_wheel_is_not_listed(self, tmp_path):
        wheel = _make_wheel(tmp_path)
        with zipfile.ZipFile(wheel, "a") as archive:
            archive.writestr("atlas/data/", b"")
        manifest = bundle.write_manifest([wheel], wheel, tmp_path)
        assert "atlas/data/" not in _parse(manifest)


class TestMainWiring:
    def test_a_missing_artifact_refuses_before_building(self, tmp_path, monkeypatch):
        # The check fires before build(), so nothing is downloaded here.
        monkeypatch.chdir(tmp_path)
        wheel = _make_wheel(tmp_path)
        with pytest.raises(SystemExit, match="artifact not found"):
            bundle.main(
                ["--tag", "v0.0.0", "--wheel", wheel.name, "--artifact", "no-such-artifact.tar.gz"]
            )


class TestConsumerCheck:
    """The consumer one-liner, run for real: ``sha256sum -c`` in a release dir."""

    @pytest.fixture
    def release_dir(self, tmp_path):
        manifest, _ = _write_manifest(tmp_path)
        wheel = tmp_path / "emu_atlas-0.0.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(tmp_path)
        return manifest.parent

    @pytest.fixture
    def vendored_dir(self, tmp_path):
        """Only ``atlas/`` copied out of the wheel, plus the manifest."""
        manifest, _ = _write_manifest(tmp_path)
        vendored = tmp_path / "vendored"
        with zipfile.ZipFile(tmp_path / "emu_atlas-0.0.0-py3-none-any.whl") as archive:
            for name in archive.namelist():
                if name.startswith("atlas/"):
                    archive.extract(name, vendored)
        shutil.copy(manifest, vendored / "SHA256SUMS")
        return vendored

    def _check(
        self, release_dir: Path, ignore_missing: bool = False
    ) -> subprocess.CompletedProcess[str]:
        tool = shutil.which("sha256sum")
        if tool is None:
            pytest.skip("sha256sum (coreutils) is not on PATH")
        flags = ["--ignore-missing"] if ignore_missing else []
        return subprocess.run(
            [tool, "-c", *flags, "SHA256SUMS"], cwd=release_dir, capture_output=True, text=True
        )

    def test_the_intact_release_passes(self, release_dir):
        result = self._check(release_dir)
        assert result.returncode == 0, result.stderr

    def test_a_tampered_file_fails(self, release_dir):
        tampered = release_dir / "atlas" / "data" / "core_audit.json"
        tampered.write_bytes(tampered.read_bytes() + b" ")
        result = self._check(release_dir)
        assert result.returncode != 0
        assert "core_audit.json" in result.stdout

    def test_a_vendored_atlas_copy_passes_with_ignore_missing(self, vendored_dir):
        result = self._check(vendored_dir, ignore_missing=True)
        assert result.returncode == 0, result.stderr

    def test_a_tampered_vendored_file_fails(self, vendored_dir):
        tampered = vendored_dir / "atlas" / "data" / "core_audit.json"
        tampered.write_bytes(tampered.read_bytes() + b" ")
        result = self._check(vendored_dir, ignore_missing=True)
        assert result.returncode != 0
        assert "core_audit.json" in result.stdout
