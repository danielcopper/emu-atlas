"""A directory copy of the package answers under any parent package (issue #327).

Vendoring is a directory copy (the zero-dependency contract, ``pyproject.toml``),
and the copy's owner chooses the parent — a host that keeps foreign code inside
its own package imports the copy as ``_vendor.atlas``, not ``atlas``. Two
mechanisms bind a copy to its own name: an absolute intra-package import, and a
packaged-data read anchored to the literal ``"atlas"``. The static half
enumerates both shapes over the shipped source, so a reintroduction anywhere is
a red test naming its file and line — including the lazy sites a single question
would never execute. The dynamic half is the property itself, not a mock: the
package directory copied under a temporary parent package, imported there by an
isolated interpreter that provably cannot see the installed ``atlas``, and asked
one real question through the contract (detect over an empty home) plus one
packaged read (the systems vocabulary).

The zstd probe (``atlas/squashfs.py``) stays name-based deliberately — it probes
*foreign* module names (``compression.zstd``, ``backports.zstd``), not this
package's own. The core-probe spawn (``atlas/machine.py``) keeps its literal
``"atlas._core_probe"`` argv deliberately too: ``_probe_environment`` prepends
the copy's own parent directory to the child's ``PYTHONPATH``, so the child
resolves the copy as top-level ``atlas`` whatever the host calls it — that
literal is compensated at runtime, not swept here.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import atlas

_PACKAGE_DIR = Path(atlas.__file__).resolve().parent

_PARENT = "_reloc_parent"

# Runs inside `python -I -S`: no site-packages, no PYTHONPATH, no cwd on
# sys.path — the installed atlas is out of reach, and the script proves that
# before touching the copy, so a green run cannot be the installed package
# answering in the copy's place.
_PROBE = """\
import importlib
import io
import json
import sys
from contextlib import redirect_stdout

search_root, home = sys.argv[1], sys.argv[2]

try:
    import atlas
except ModuleNotFoundError:
    pass
else:
    print("top-level atlas is importable — the isolation premise is broken", file=sys.stderr)
    sys.exit(3)

sys.path.insert(0, search_root)
pkg = importlib.import_module("%(parent)s.atlas")
cli = importlib.import_module("%(parent)s.atlas.cli")

stdout = io.StringIO()
with redirect_stdout(stdout):
    rc = cli.run(["detect"], home=home)
print(json.dumps({
    "rc": rc,
    "detect": json.loads(stdout.getvalue()),
    "known_systems": len(pkg.known_systems()),
}))
""" % {"parent": _PARENT}


def _self_references(source: str, filename: str) -> list[str]:
    """The swept binding shapes in *source*, each with its file:line.

    Two node families are enumerated: import nodes naming ``atlas``, and calls
    to ``files``/``import_module`` whose first argument is the literal
    ``"atlas"`` or a dotted name under it.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and (module == "atlas" or module.startswith("atlas.")):
                found.append(f"{filename}:{node.lineno} from {module} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "atlas" or alias.name.startswith("atlas."):
                    found.append(f"{filename}:{node.lineno} import {alias.name}")
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else (
                func.id if isinstance(func, ast.Name) else None
            )
            if name not in ("files", "import_module"):
                continue
            first = node.args[0] if node.args else None
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            if first.value == "atlas" or first.value.startswith("atlas."):
                found.append(f'{filename}:{node.lineno} {name}("{first.value}")')
    return found


def test_no_module_addresses_the_package_by_its_own_name() -> None:
    """The static half: both binding shapes, enumerated over the shipped source.

    Lazy data reads only execute when their table loads, so the dynamic proof
    alone would let a reintroduced ``files("atlas")`` in an unexercised module
    pass — this sweep is what makes every site a red test.
    """
    found: list[str] = []
    for path in sorted(_PACKAGE_DIR.rglob("*.py")):
        relative = str(path.relative_to(_PACKAGE_DIR))
        found.extend(_self_references(path.read_text(encoding="utf-8"), relative))
    assert found == [], "the package addresses itself by name:\n" + "\n".join(found)


def test_directory_copy_answers_under_a_parent_package(tmp_path: Path) -> None:
    """The dynamic half: the copy, under a parent, answers through the contract."""
    parent_dir = tmp_path / _PARENT
    shutil.copytree(
        _PACKAGE_DIR, parent_dir / "atlas", ignore=shutil.ignore_patterns("__pycache__")
    )
    (parent_dir / "__init__.py").write_text("", encoding="utf-8")
    probe = tmp_path / "probe.py"
    probe.write_text(_PROBE, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()

    proc = subprocess.run(
        [sys.executable, "-I", "-S", str(probe), str(tmp_path), str(home)],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    answer = json.loads(proc.stdout)
    assert answer["rc"] == 0
    assert answer["detect"] == []  # the contract's answer for an empty home
    assert answer["known_systems"] > 0  # a packaged read resolved under the parent
