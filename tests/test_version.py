"""One version, two spellings — pyproject.toml is the source, ``atlas.__version__`` its copy.

The package states its own version so a vendored directory copy — which carries
no dist-info to ask — can still answer "which tag is this copy?". A second
spelling of the version is acceptable only mechanized: release-please rewrites
the ``__init__.py`` line at release time (the ``extra-files`` entry in
release-please-config.json points its generic updater at the
``x-release-please-version`` annotation on that line), and this module is the
tripwire that makes drift a red test: the release PR runs it, so a release
that bumps one spelling without the other cannot merge. Only the two repo
spellings are compared here — dist-info deliberately is not: an installed
distribution's recorded version lags the tree in an editable venv right after
a version bump (a false alarm, not drift), and the clean-venv comparison of
dist-info against pyproject already lives in CI's package job, which owns it.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import atlas

_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_the_package_speaks_the_pyproject_version() -> None:
    project = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert project["version"] == atlas.__version__
