"""Prove a built bundle answers before it ships — the artifact, not the checkout.

Run against the tarball ``build_release_bundle.py`` produced. Everything here
exercises the *extracted bundle*: its launcher, its interpreter, its installed
copy of the library. Four claims, each its own check:

1. the launcher runs and the CLI answers (``--help``, then ``detect`` against
   an empty home returning the empty aggregate — exit 0, valid contract JSON);
2. the bundled interpreter carries the zstd capability and the installed
   library uses it — the committed zstd fixture AppImage is read end to end;
3. ``import atlas`` inside the bundle resolves to the bundle's site-packages,
   never to a checkout that happens to sit nearby;
4. the full test suite — unit tests, machine-vector runner, CLI conformance —
   passes under the bundle's interpreter against its installed library
   (pytest is installed into the scratch copy only; the shipped tarball is
   already written and stays untouched).

A maintainer/CI tool, not part of the library. stdlib only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ZSTD_FIXTURE = REPO_ROOT / "tests" / "data" / "esde-like.zstd.appimage"
CATALOGUE_ENTRY = "usr/share/es-de/resources/systems/linux/es_systems.xml"


def _run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, cwd=cwd)


def _fail(check: str, result: subprocess.CompletedProcess[str]) -> None:
    raise SystemExit(
        f"FAIL {check}\n  exit {result.returncode}\n"
        f"  stdout: {result.stdout[:2000]}\n  stderr: {result.stderr[:2000]}"
    )


def _check_launcher(bundle: Path, scratch: Path) -> None:
    launcher = str(bundle / "emu-atlas")
    result = _run([launcher, "--help"])
    if result.returncode != 0:
        _fail("launcher --help", result)
    empty_home = scratch / "empty-home"
    empty_home.mkdir()
    result = _run([launcher, "detect", "--home", str(empty_home)])
    if result.returncode != 0 or json.loads(result.stdout) != []:
        _fail("detect on an empty home answers the empty aggregate", result)
    print("ok: launcher runs, detect answers contract JSON")


def _check_zstd_capability(python: Path) -> None:
    probe = (
        "from atlas import squashfs\n"
        f"data = squashfs.read_appimage_entry({str(ZSTD_FIXTURE)!r}, {CATALOGUE_ENTRY!r})\n"
        "assert b'<systemList>' in data\n"
    )
    result = _run([str(python), "-c", probe])
    if result.returncode != 0:
        _fail("the bundled runtime reads the zstd fixture AppImage", result)
    print("ok: zstd capability is present and the installed library uses it")


def _check_import_origin(python: Path, bundle: Path) -> None:
    probe = (
        "import atlas, sys\n"
        f"assert atlas.__file__.startswith({str(bundle)!r}), atlas.__file__\n"
        f"assert sys.prefix.startswith({str(bundle)!r}), sys.prefix\n"
    )
    # A neutral cwd, so a checkout beside us cannot shadow the installed copy.
    result = _run([str(python), "-c", probe], cwd=bundle.parent)
    if result.returncode != 0:
        _fail("import atlas resolves inside the bundle", result)
    print("ok: import atlas resolves to the bundle's site-packages")


def _check_full_suite(python: Path, scratch: Path) -> None:
    result = _run([str(python), "-m", "pip", "--version"])
    if result.returncode != 0:
        result = _run([str(python), "-m", "ensurepip", "--upgrade"])
        if result.returncode != 0:
            _fail("bootstrapping pip in the scratch copy", result)
    result = _run([str(python), "-m", "pip", "install", "--quiet", "pytest"])
    if result.returncode != 0:
        _fail("installing pytest into the scratch copy", result)
    result = _run([str(python), "-m", "pytest", str(REPO_ROOT / "tests"), "-q"], cwd=scratch)
    if result.returncode != 0:
        _fail("the full suite under the bundle interpreter", result)
    tail = [line for line in result.stdout.splitlines() if line.strip()][-1:]
    print(f"ok: full suite passes under the bundle interpreter — {' '.join(tail)}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Prove a built bundle answers before it ships."
    )
    parser.add_argument("tarball", type=Path, help="the bundle tarball to smoke")
    parser.add_argument(
        "--skip-suite",
        action="store_true",
        help="skip the full-test-suite check (launcher, codec and import checks always run)",
    )
    args = parser.parse_args(argv)
    if not args.tarball.exists():
        raise SystemExit(f"bundle not found: {args.tarball}")

    with tempfile.TemporaryDirectory(prefix="emu-atlas-smoke-") as scratch_dir:
        scratch = Path(scratch_dir)
        with tarfile.open(args.tarball, "r:gz") as tar:
            tar.extractall(scratch, filter="tar")
        roots = [path for path in scratch.iterdir() if path.is_dir()]
        if len(roots) != 1:
            raise SystemExit(f"expected one bundle root in the tarball, found {len(roots)}")
        bundle = roots[0]
        python = bundle / "python" / "bin" / "python3"

        _check_launcher(bundle, scratch)
        _check_zstd_capability(python)
        _check_import_origin(python, bundle)
        if args.skip_suite:
            print("skipped: full suite (--skip-suite)")
        else:
            _check_full_suite(python, scratch)
    print(f"SMOKE OK: {args.tarball.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
