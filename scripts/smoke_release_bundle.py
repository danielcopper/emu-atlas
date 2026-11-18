"""Prove a built bundle answers before it ships — the artifact, not the checkout.

Run against the tarball ``build_release_bundle.py`` produced. Everything here
exercises the *extracted bundle*: its launcher, its interpreter, its installed
copy of the library. Five claims, each its own check:

1. the launcher runs and the CLI answers (``--help``, then ``detect`` against
   an empty home returning the empty aggregate — exit 0, valid contract JSON);
2. the bundled interpreter carries the zstd capability and the installed
   library uses it — the committed zstd fixture AppImage is read end to end;
3. the bundle would probe cores under its own interpreter with nothing
   registered — ``core_probe_interpreter()`` is asked what it reports, and no
   core is probed: the shipped runtime is a plain CPython, so the derived stage
   must answer, and a later tightening of that rule cannot silently take
   probing away from our own release artifact;
4. ``import atlas`` inside the bundle resolves to the bundle's site-packages,
   never to a checkout that happens to sit nearby;
5. the full test suite — unit tests, machine-vector runner, CLI conformance —
   passes under the bundle's interpreter against its installed library
   (pytest is installed into the scratch copy only; the shipped tarball is
   already written and stays untouched).

A maintainer/CI tool, not part of the library. stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ZSTD_FIXTURE = REPO_ROOT / "tests" / "data" / "esde-like.zstd.appimage"
CATALOGUE_ENTRY = "usr/share/es-de/resources/systems/linux/es_systems.xml"


def _confined(path: Path) -> Path:
    """Canonicalize a CLI-named path and confine it to the invocation directory.

    The tarball this tool smokes lives under the tree it is run from — the CI
    workspace, or a local working directory. Confining the argument there
    keeps a faulty invocation (a mistyped flag, an agent-driven call) from
    reaching anything else on the machine.
    """
    resolved = os.path.realpath(path)
    base = os.path.realpath(os.getcwd())
    if resolved != base and not resolved.startswith(base + os.sep):
        raise SystemExit(f"path {path} resolves outside the invocation directory {base}")
    return Path(resolved)


def _run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, cwd=cwd)


# How much of a failed check's output is shown, from each end. A tool states
# what went wrong at the end of its output: pytest prints a progress dot per
# test first and the failures, their tracebacks and the summary last, so the
# tail is the larger share. It keeps the closing count line and the failures
# that come last. On CI pytest does not cut a comparison's explanation short,
# so one failed comparison's traceback and its summary entry can each run to
# several thousand characters and the tail may hold only a few failures;
# where more was dropped, the stated cut says so. The head keeps what a crash
# before the first test prints.
SHOWN_HEAD = 2000
SHOWN_TAIL = 20000


def _excerpt(text: str) -> str:
    """*text* whole where it is short, else its head and tail with the cut stated."""
    if len(text) <= SHOWN_HEAD + SHOWN_TAIL:
        return text
    omitted = len(text) - SHOWN_HEAD - SHOWN_TAIL
    return f"{text[:SHOWN_HEAD]}\n[... {omitted} characters omitted ...]\n{text[-SHOWN_TAIL:]}"


def _fail(check: str, result: subprocess.CompletedProcess[str]) -> None:
    raise SystemExit(
        f"FAIL {check}\n  exit {result.returncode}\n"
        f"  stdout: {_excerpt(result.stdout)}\n  stderr: {_excerpt(result.stderr)}"
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


def _check_core_probe_interpreter(python: Path) -> None:
    probe = (
        "import atlas\n"
        "chosen = atlas.core_probe_interpreter()\n"
        "assert chosen is not None, 'the bundle names no interpreter to probe cores with'\n"
        "assert chosen.registered is False, chosen\n"
    )
    result = _run([str(python), "-c", probe])
    if result.returncode != 0:
        _fail("the bundle names its own interpreter as the one a probe would run under", result)
    print("ok: the bundle would probe under its own interpreter, no registration needed")


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
    # Short tracebacks keep each failure small. -rfE restates pytest's
    # default, so a configuration cannot drop the summary line per failure
    # and error.
    result = _run(
        [str(python), "-m", "pytest", str(REPO_ROOT / "tests"), "-q", "-rfE", "--tb=short"],
        cwd=scratch,
    )
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
        help=(
            "skip the full-test-suite check "
            "(launcher, codec, probe-interpreter and import checks always run)"
        ),
    )
    args = parser.parse_args(argv)
    tarball = _confined(args.tarball)
    if not tarball.exists():
        raise SystemExit(f"bundle not found: {tarball}")

    with tempfile.TemporaryDirectory(prefix="emu-atlas-smoke-") as scratch_dir:
        scratch = Path(scratch_dir)
        with tarfile.open(tarball, "r:gz") as tar:
            tar.extractall(scratch, filter="tar")
        roots = [path for path in scratch.iterdir() if path.is_dir()]
        if len(roots) != 1:
            raise SystemExit(f"expected one bundle root in the tarball, found {len(roots)}")
        bundle = roots[0]
        python = bundle / "python" / "bin" / "python3"

        _check_launcher(bundle, scratch)
        _check_zstd_capability(python)
        _check_core_probe_interpreter(python)
        _check_import_origin(python, bundle)
        if args.skip_suite:
            print("skipped: full suite (--skip-suite)")
        else:
            _check_full_suite(python, scratch)
    print(f"SMOKE OK: {args.tarball.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
