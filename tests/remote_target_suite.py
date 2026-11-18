"""What both remote-target test modules share: the module's isolation, a snapshot of a tree, and the paths.

Kept apart from ``tests/remote_target.py``, which every stand-in imports when it
starts: this module imports ``pytest``, which a stand-in has no use for.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

from scripts import setup_remote_target
from tests.remote_target import GUARDED, GUARDED_VARIABLE, guard_real_home

REPO = setup_remote_target.REPO
DISPATCHER = setup_remote_target.DISPATCHER
INSTALL_SCRIPT = setup_remote_target.INSTALL_SCRIPT
ROOT = Path(setup_remote_target.TARGET_ROOT)
FLOOR = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["requires-python"].lstrip(
    ">= "
)
"""The package's Python floor, ``X.Y``, as the setup reads it."""

Entry = tuple[int, bytes | str | None]
"""One entry of a :func:`snapshot`: its mode, and a file's bytes, a link's target or ``None`` for a directory."""


def snapshot(root: Path) -> dict[str, Entry]:
    """Every entry under *root*, links not followed, each with its mode: "as it was" means all of it."""
    tree: dict[str, Entry] = {}
    if not root.exists():
        return tree
    for directory, folders, names in os.walk(root):
        for name in [*folders, *names]:
            path = Path(directory) / name
            mode = stat.S_IMODE(path.lstat().st_mode)
            relative = str(path.relative_to(root))
            if path.is_symlink():
                tree[relative] = (mode, os.readlink(path))
            elif path.is_dir():
                tree[relative] = (mode, None)
            else:
                tree[relative] = (mode, path.read_bytes())
    return tree


def contents(entries: dict[str, Entry]) -> dict[str, bytes | str | None]:
    """A :func:`snapshot` without its modes, for a test that states what a tree holds."""
    return {name: entry[1] for name, entry in entries.items()}


@pytest.fixture(scope="module")
def isolated_module(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """``HOME``, ``XDG_CONFIG_HOME`` and ``PATH`` moved for a whole module, and no name looked up on the network.

    In this process the real home is guarded (:func:`tests.remote_target.guard_real_home`;
    what that covers and what it leaves out, ``tests/remote_target.py`` says),
    and the module fails on any touch the guard refused. ``getaddrinfo``
    resolves numeric addresses only, so no lookup leaves this machine.
    """
    real_getaddrinfo = socket.getaddrinfo

    def numeric_only(host: str, port: str | int | None, *_: object, **__: object) -> object:
        return real_getaddrinfo(host, port, 0, socket.SOCK_STREAM, 0, socket.AI_NUMERICHOST)

    home = tmp_path_factory.mktemp("module-home")
    empty = home / "empty-path"
    empty.mkdir()
    with pytest.MonkeyPatch.context() as patch, guard_real_home() as touched:
        patch.setenv("HOME", str(home))
        patch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
        patch.setenv("PATH", str(empty))
        patch.setenv(GUARDED_VARIABLE, json.dumps(GUARDED))
        for name in ("SSH_AUTH_SOCK", "SSH_CLIENT", "SSH_CONNECTION", "SSH_ORIGINAL_COMMAND"):
            patch.delenv(name, raising=False)
        patch.setattr(socket, "getaddrinfo", numeric_only)
        yield
    assert touched == []
