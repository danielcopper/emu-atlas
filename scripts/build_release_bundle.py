"""Assemble the self-contained CLI bundle — pinned CPython + the wheel + a launcher.

The release artifact for consumers who own no Python (issue #202): one
tarball carrying an unmodified python-build-standalone runtime, the release
wheel unpacked into its ``site-packages``, and a launcher script short enough
to read in passing. The bundle is deliberately a directory of plain files —
nothing self-extracts at run time, nothing is written outside the tree, and
the tree answers from wherever the consumer unpacks it. That keeps the
artifact inspectable the way the library is vendorable: what you audit is
what runs.

The runtime is pinned by release, filename and checksum. 3.14 is chosen on
purpose: it is the first interpreter whose stdlib carries ``compression.zstd``
(PEP 784), so the AppImage reader's codec gate is open and the bundle answers
with every capability the library has. The checksum is verified on every
build — a provided local archive gets the same treatment as a download.

A maintainer/CI tool, not part of the library. stdlib only, like everything
else in the tree.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

RUNTIME_RELEASE = "20260814"
RUNTIME_ARCHIVE = "cpython-3.14.7+20260814-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
RUNTIME_SHA256 = "cefba034445d2875408d1fd4d5700ae6731563aeb54dcb39fd8164ab5c457533"
RUNTIME_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    f"{RUNTIME_RELEASE}/{urllib.parse.quote(RUNTIME_ARCHIVE)}"
)

BUNDLE_PLATFORM = "x86_64-linux"

# A tag names the bundle directory and the tarball — it is a name, never a
# path, and anything a release tag legitimately contains matches this.
_TAG_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")

_LAUNCHER = """\
#!/bin/sh
# emu-atlas — answers with the interpreter bundled beside this script; no
# installed Python is consulted. Contract shapes: docs/how-to-use.md in the
# emu-atlas repository.
here="$(dirname "$(readlink -f "$0")")"
exec "$here/python/bin/python3" -m atlas "$@"
"""


def _confined(path: Path) -> Path:
    """Canonicalize a CLI-named path and confine it to the invocation directory.

    Every path this tool takes lives under the tree it is run from — the CI
    workspace, or a local working directory. Confining the arguments there
    keeps a faulty invocation (a mistyped flag, an agent-driven call) from
    reaching anything else on the machine.
    """
    resolved = os.path.realpath(path)
    base = os.path.realpath(os.getcwd())
    if resolved != base and not resolved.startswith(base + os.sep):
        raise SystemExit(f"path {path} resolves outside the invocation directory {base}")
    return Path(resolved)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_archive(provided: Path | None, cache_dir: Path) -> Path:
    """The pinned runtime archive, downloaded unless provided — verified either way."""
    if provided is not None:
        archive = provided
    else:
        archive = cache_dir / RUNTIME_ARCHIVE
        if not archive.exists():
            print(f"fetching {RUNTIME_URL}")
            cache_dir.mkdir(parents=True, exist_ok=True)
            # The URL is a constant of this script — release, name and scheme
            # are pinned above, and the checksum below vouches for the bytes.
            with urllib.request.urlopen(RUNTIME_URL) as response:
                with archive.open("wb") as out:
                    shutil.copyfileobj(response, out)
    actual = _sha256(archive)
    if actual != RUNTIME_SHA256:
        raise SystemExit(
            f"runtime archive checksum mismatch for {archive}:\n"
            f"  expected {RUNTIME_SHA256}\n  actual   {actual}"
        )
    return archive


def _extract_runtime(archive: Path, staging: Path) -> None:
    """The archive's ``python/`` tree, placed unmodified into the staging root."""
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(staging, filter="tar")
    if not (staging / "python" / "bin" / "python3").exists():
        raise SystemExit(f"{archive} did not contain the expected python/bin/python3")


def _install_wheel(wheel: Path, staging: Path) -> None:
    """The wheel's contents, unpacked into the runtime's site-packages.

    A wheel is a zip of the installed tree — unpacking it there is the whole
    install, with no tool run inside the bundle and nothing else touched.
    """
    candidates = sorted(staging.glob("python/lib/python3.*/site-packages"))
    if not candidates:
        raise SystemExit("the runtime carries no site-packages directory")
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(candidates[0])


def _write_launcher(bundle_root: Path) -> None:
    launcher = bundle_root / "emu-atlas"
    launcher.write_text(_LAUNCHER, encoding="utf-8")
    launcher.chmod(
        launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )


def _pack(bundle_root: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    tarball = out_dir / f"{bundle_root.name}.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(bundle_root, arcname=bundle_root.name)
    return tarball


def build(tag: str, wheel: Path, out_dir: Path, runtime: Path | None) -> Path:
    archive = _runtime_archive(runtime, out_dir / "runtime-cache")
    with tempfile.TemporaryDirectory(prefix="emu-atlas-bundle-") as scratch:
        bundle_root = Path(scratch) / f"emu-atlas-{tag}-{BUNDLE_PLATFORM}"
        bundle_root.mkdir()
        _extract_runtime(archive, bundle_root)
        _install_wheel(wheel, bundle_root)
        _write_launcher(bundle_root)
        tarball = _pack(bundle_root, out_dir)
    print(f"bundle: {tarball} ({tarball.stat().st_size / (1 << 20):.1f} MiB)")
    return tarball


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Assemble the self-contained CLI bundle — pinned CPython + wheel + launcher."
    )
    parser.add_argument("--tag", required=True, help="release tag naming the bundle (e.g. v0.3.0)")
    parser.add_argument("--wheel", required=True, type=Path, help="the built emu-atlas wheel")
    parser.add_argument(
        "--out", type=Path, default=Path("dist-bundle"), help="output directory for the tarball"
    )
    parser.add_argument(
        "--runtime-archive",
        type=Path,
        default=None,
        help="use this local runtime archive instead of downloading (still checksum-verified)",
    )
    args = parser.parse_args(argv)
    if not _TAG_PATTERN.match(args.tag):
        raise SystemExit(f"tag {args.tag!r} is not a plain release-tag name")
    wheel = _confined(args.wheel)
    out_dir = _confined(args.out)
    runtime = None if args.runtime_archive is None else _confined(args.runtime_archive)
    if not wheel.exists():
        raise SystemExit(f"wheel not found: {wheel}")
    build(args.tag, wheel, out_dir, runtime)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
