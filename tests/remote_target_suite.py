"""What the remote-target test modules share: the isolation, a snapshot of a tree, the paths, the dispatcher
installed with the ``bwrap`` it names, a copy sent to it as a client sends one, and a home the real sandbox can be
shown in.

Kept apart from ``tests/remote_target.py``, which every stand-in imports when it
starts: this module imports ``pytest``, which a stand-in has no use for.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NamedTuple

import pytest

from scripts import dev_dispatch, setup_remote_target
from tests.remote_target import GUARDED, GUARDED_VARIABLE, guard_real_home

REPO = setup_remote_target.REPO
DISPATCHER = setup_remote_target.DISPATCHER
INSTALL_SCRIPT = setup_remote_target.INSTALL_SCRIPT
ROOT = Path(setup_remote_target.TARGET_ROOT)
REAL_BWRAP = "/usr/bin/bwrap"
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


def without_caches(entries: dict[str, Entry]) -> dict[str, Entry]:
    """A :func:`snapshot` without what any ``__pycache__`` holds: what a tree holds beside its bytecode."""
    return {name: entry for name, entry in entries.items() if "__pycache__" not in Path(name).parts}


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


def install_dispatcher(home: Path, *, bwrap: str = REAL_BWRAP, root: Path = ROOT) -> Path:
    """The dispatcher, installed under *home* as the setup installs it, naming *bwrap* for its sandbox."""
    installed = home / root / "dispatch.py"
    installed.parent.mkdir(parents=True, exist_ok=True)
    source = DISPATCHER.read_text(encoding="utf-8")
    line = f'BWRAP = "{REAL_BWRAP}"\n'
    assert source.count(line) == 1
    installed.write_text(source.replace(line, f"BWRAP = {bwrap!r}\n"), encoding="utf-8")
    return installed


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_line(files: dict[str, bytes], *, floor: str = "3.12", executable: frozenset[str] = frozenset()) -> bytes:
    """The manifest of *files*, as whatever sends a copy has to write it: one line of JSON, without its newline."""
    entries = [
        {"executable": name in executable, "path": name, "sha256": sha256(data)} for name, data in sorted(files.items())
    ]
    return json.dumps({"files": entries, "requires_python": floor}, sort_keys=True, separators=(",", ":")).encode()


def tree_id(manifest: bytes) -> str:
    return dev_dispatch.TREE_PREFIX + sha256(manifest)


def gz_tar(members: dict[str, bytes]) -> bytes:
    """A gzip tar of *members*, regular files in this order, as a copy sends the files a target lacks."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(data))
    return buffer.getvalue()


class Exchanged(NamedTuple):
    """How a copy's one connection ended: the dispatcher's status, the sha256 its lacks line named (``None`` for no
    such line), all it wrote on stdout and on stderr."""

    status: int
    lacking: list[str] | None
    stdout: bytes
    stderr: bytes


def copy_exchange(
    command: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
    manifest: bytes,
    contents: dict[str, bytes],
    files: Callable[[list[str]], bytes] | None = None,
) -> Exchanged:
    """A copy through *command*, the dispatcher: the manifest line out, the lacks line back, then what *files*
    makes of the sha256 it names — by default a gzip tar of those of *contents* — and nothing where it names none."""
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=cwd
    )
    assert process.stdin is not None and process.stdout is not None
    try:
        process.stdin.write(manifest + b"\n")
        process.stdin.flush()
    except BrokenPipeError:
        pass
    # The lacks line read off the descriptor itself: a buffered readline can take
    # the id that follows it along, which communicate() then never sees.
    head = b""
    while b"\n" not in head:
        chunk = os.read(process.stdout.fileno(), 65536)
        if not chunk:
            break
        head += chunk
    line, newline, _ = head.partition(b"\n")
    lacks = dev_dispatch.LACKS.encode()
    lacking = line[len(lacks) :].decode().split() if newline and line.startswith(lacks) else None
    sent = b""
    if lacking:
        sent = files(lacking) if files is not None else gz_tar({digest: contents[digest] for digest in lacking})
    out, err = process.communicate(sent)
    return Exchanged(process.returncode, lacking, head + out, err)


def sandbox_unavailable() -> str | None:
    """Why the real sandbox cannot be shown here, or ``None`` where it starts: the dispatcher's own trial."""
    failure = dev_dispatch.sandbox_failure(REAL_BWRAP, sys.executable)
    return None if failure is None else f"{REAL_BWRAP} cannot start a sandbox here: {failure[1]}"


@pytest.fixture
def sandbox_home() -> Iterator[Path]:
    """A home outside ``/tmp`` — the sandbox puts an empty ``/tmp`` of its own over it — beside a directory
    ``outside``, or a skip saying why the sandbox cannot be shown here."""
    unavailable = sandbox_unavailable()
    if unavailable is not None:
        pytest.skip(unavailable)
    base = Path(tempfile.mkdtemp(prefix="emu-atlas-dev-test-", dir="/var/tmp"))
    try:
        (base / "home").mkdir()
        (base / "outside").mkdir()
        yield base / "home"
    finally:
        shutil.rmtree(base)


def processes_holding(token: str) -> list[int]:
    """The processes on this machine whose command line holds *token*.

    A process in the middle of an ``execve`` reads as an empty command line, so
    a scan can miss one that is still starting — the ``bwrap`` stand-in becoming
    the probe's interpreter, or ``bwrap`` starting it. Wait for a line the probe
    writes before scanning for it.
    """
    found = []
    for name in os.listdir("/proc"):
        if not name.isdigit() or int(name) == os.getpid():
            continue
        try:
            command_line = Path("/proc", name, "cmdline").read_bytes()
        except OSError:
            continue
        if token.encode() in command_line:
            found.append(int(name))
    return found


def end_what_is_left(token: str) -> None:
    """Kill, by its pid, every process of this test's that still holds *token*: nothing outlives a failed test."""
    for pid in processes_holding(token):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue


def gone_within(token: str, seconds: float) -> bool:
    """Whether every process holding *token* has ended within *seconds*."""
    deadline = time.monotonic() + seconds
    while processes_holding(token):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True
