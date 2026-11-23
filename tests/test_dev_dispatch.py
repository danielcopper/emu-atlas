"""Tests for ``scripts/dev_dispatch.py`` — the forced command of the build host's key on a remote target.

It decides from ``SSH_ORIGINAL_COMMAND`` alone, so each case runs the real file
with ``python -I`` under a home of its own — copied to where setup:remote-target
installs it, since it finds its root from where it lies — with a request in
that variable. A request it lets through is checked by what it ran; a refusal
by its message, its status, and nothing having run.

Where a case is about what reaches the sandbox rather than the sandbox, the
installed copy names the ``bwrap`` stand-in of ``tests/remote_target.py``
instead of ``/usr/bin/bwrap``, which runs the command with no sandbox. The
cases about the sandbox itself run the real ``/usr/bin/bwrap`` and skip, with
the reason, where it cannot start one — GitHub's runners may forbid the
unprivileged user namespaces it needs.

A copy is sent here by hand, as whatever sends one has to: the manifest on one
line, then a gzip tar of the files the dispatcher's lacks line names
(``tests/remote_target_suite.py``, :func:`copy_exchange`).

``HOME``, ``XDG_CONFIG_HOME`` and ``PATH`` are moved for the whole module
(``isolated_module``). The dispatcher runs with no audit hook of its own; it
works under each test's own ``home``, which :func:`environment` hands it as
``HOME``, and takes its root from where :func:`install` put it there.
"""

from __future__ import annotations

import ast
import base64
import errno
import importlib.util
import io
import json
import os
import py_compile
import signal
import stat
import subprocess
import sys
import tarfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from scripts import dev_dispatch, setup_remote_target
from tests.remote_target import GUARDED_VARIABLE, fake_path
from tests.remote_target_suite import (
    DISPATCHER,
    INSTALL_SCRIPT,
    REAL_BWRAP,
    REPO,
    ROOT,
    Entry,
    Exchanged,
    contents,
    copy_exchange,
    end_what_is_left,
    gone_within,
    gz_tar,
    isolated_module,
    manifest_line,
    processes_holding,
    sandbox_home,
    sha256,
    snapshot,
    tree_id,
    without_caches,
)
from tests.remote_target_suite import install_dispatcher as install

pytestmark = pytest.mark.usefixtures(isolated_module.__name__)

A_TREE = tree_id(b"no manifest a copy sent")
"""An id of the right form, for a request that names a tree nothing copied."""

DEPLOYED_CORES = Path(
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
    "/retroarch/rd_extras/cores"
)

# An atlas that says where it was imported from, what it was asked, and ends
# with the status its first argument names.
_SAYING_ATLAS = {
    "atlas/__init__.py": b"",
    "atlas/__main__.py": (
        b"import json, sys\n"
        b"import atlas\n"
        b"print(json.dumps({'file': atlas.__file__, 'argv': sys.argv[1:], 'path': sys.path}))\n"
        b"sys.exit(int(sys.argv[1]) if sys.argv[1:] and sys.argv[1].isdigit() else 0)\n"
    ),
}


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


@pytest.fixture
def fakes(tmp_path: Path) -> Path:
    tools = tmp_path / "tools"
    tools.mkdir()
    return Path(fake_path(tools))


def word(arguments: list[str]) -> str:
    """The argument list as a request carries it: JSON, UTF-8, base64url without padding."""
    return base64.urlsafe_b64encode(json.dumps(arguments).encode("utf-8")).decode("ascii").rstrip("=")


def environment(home: Path, request: str | None) -> dict[str, str]:
    """What sshd hands the forced command: the home, and the request; ``None`` for a login."""
    env = {"HOME": str(home), "PATH": os.environ["PATH"], GUARDED_VARIABLE: os.environ[GUARDED_VARIABLE]}
    if request is not None:
        env["SSH_ORIGINAL_COMMAND"] = request
    return env


def dispatch(
    home: Path, request: str | None, stdin: bytes = b"", *, installed: Path | None = None
) -> subprocess.CompletedProcess[bytes]:
    """*request* as sshd hands it to the installed dispatcher, run to its end."""
    script = installed or home / ROOT / "dispatch.py"
    return subprocess.run(
        [sys.executable, "-I", str(script)],
        cwd=home,
        input=stdin,
        capture_output=True,
        check=False,
        env=environment(home, request),
    )


def start(home: Path, request: str) -> subprocess.Popen[bytes]:
    """*request* as sshd hands it to the installed dispatcher, left running."""
    return subprocess.Popen(
        [sys.executable, "-I", str(home / ROOT / "dispatch.py")],
        cwd=home,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment(home, request),
    )


# A probe that says it started, waits for the file its argument names, then
# prints the marker of the tree it runs from.
_WAITING_PROBE = (
    b"import os, sys, time\n"
    b"print('started', flush=True)\n"
    b"for _ in range(1200):\n"
    b"    if os.path.exists(sys.argv[1]):\n"
    b"        break\n"
    b"    time.sleep(0.05)\n"
    b"here = os.path.dirname(os.path.abspath(sys.argv[0]))\n"
    b"print(open(os.path.join(here, '..', '..', 'marker.txt')).read().strip())\n"
)

# A probe that says it started and then waits far longer than any test, its
# argument a token that finds it among the machine's processes.
_LINGERING_PROBE = b"import sys, time\nprint('started', flush=True)\ntime.sleep(600)\n"


def a_probe_running_while_a_copy_lands(home: Path, go: Path) -> None:
    """A copy lands while a probe runs from the tree before it; the probe finishes from that tree."""
    first = copied(home, {"marker.txt": b"first\n", "scripts/probes/wait.py": _WAITING_PROBE})
    running = start(home, f"probe {first.name} wait.py {word([str(go)])}")
    try:
        assert running.stdout is not None
        assert running.stdout.readline() == MARKER_LINE
        assert running.stdout.readline() == b"started\n"
        second = copied(home, {"marker.txt": b"second\n"})
        assert first.is_dir()
        assert (home / ROOT / "current").resolve() == second
        go.touch()
        out, err = running.communicate(timeout=60)
    finally:
        if running.poll() is None:
            running.kill()
            running.wait()
    assert running.returncode == 0, err
    assert out == b"first\n"
    third = copied(home, {"marker.txt": b"third\n"})
    assert sorted(path.name for path in (home / ROOT / "trees").iterdir()) == [third.name]


def a_probe_whose_caller_hangs_up(home: Path) -> None:
    """A probe is running; nothing reads the dispatcher's output any more; the probe ends and its tree is free."""
    first = copied(home, {"scripts/probes/linger.py": _LINGERING_PROBE})
    token = uuid.uuid4().hex
    running = start(home, f"probe {first.name} linger.py {word([token])}")
    try:
        assert running.stdout is not None
        assert running.stdout.readline() == MARKER_LINE
        assert running.stdout.readline() == b"started\n"
        running.stdout.close()
        assert running.wait(timeout=30) == 128 + signal.SIGKILL
        assert gone_within(token, 30), processes_holding(token)
    finally:
        if running.poll() is None:
            running.kill()
            running.wait()
        end_what_is_left(token)
    second = copied(home, {"scripts/probes/linger.py": _LINGERING_PROBE, "marker.txt": b"second\n"})
    assert not first.exists()
    assert sorted(path.name for path in (home / ROOT / "trees").iterdir()) == [second.name]


# Starts the dispatcher with its output on a pipe only this process reads,
# prints the dispatcher's pid, says whether the probe wrote the line it writes
# once it runs — within a minute, or before that output ended — and waits to
# be killed, the pipe still open: the parent that goes.
_PARENT = (
    "import os, select, subprocess, sys, time\n"
    "child = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)\n"
    "print(child.pid, flush=True)\n"
    "deadline, seen = time.monotonic() + 60, b''\n"
    "def ready():\n"
    "    return select.select([child.stdout], [], [], max(0, deadline - time.monotonic()))[0]\n"
    "while b'started\\n' not in seen and ready():\n"
    "    chunk = os.read(child.stdout.fileno(), 4096)\n"
    "    if not chunk:\n"
    "        break\n"
    "    seen += chunk\n"
    "print('started' if b'started\\n' in seen else 'never started', flush=True)\n"
    "time.sleep(600)\n"
)


def a_probe_whose_caller_goes(home: Path) -> None:
    """A probe is running; the process holding the other end of the dispatcher's output dies; the probe ends."""
    tree = copied(home, {"scripts/probes/linger.py": _LINGERING_PROBE})
    token = uuid.uuid4().hex
    parent = subprocess.Popen(
        [sys.executable, "-I", "-c", _PARENT, sys.executable, "-I", str(home / ROOT / "dispatch.py")],
        cwd=home,
        stdout=subprocess.PIPE,
        env=environment(home, f"probe {tree.name} linger.py {word([token])}"),
    )
    try:
        assert parent.stdout is not None
        dispatcher = int(parent.stdout.readline())
        assert parent.stdout.readline() == b"started\n"
        assert processes_holding(token)
        parent.kill()
        parent.wait()
        assert gone_within(token, 30), processes_holding(token)
        assert gone_within(f"{home / ROOT / 'dispatch.py'}", 30), dispatcher
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait()
        end_what_is_left(token)
        end_what_is_left(str(home / ROOT / "dispatch.py"))


def atlas_files() -> dict[str, bytes]:
    """This checkout's ``atlas`` package, as a copy carries it."""
    package = REPO / "atlas"
    return {
        path.relative_to(REPO).as_posix(): path.read_bytes()
        for path in sorted(package.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def send_copy(
    home: Path,
    files: dict[str, bytes],
    *,
    floor: str = "3.12",
    manifest: bytes | None = None,
    sent: Callable[[list[str]], bytes] | None = None,
    installed: Path | None = None,
) -> Exchanged:
    """*files* sent as a copy to the installed dispatcher: their manifest — or *manifest* — then the files lacked,
    or what *sent* makes of their sha256."""
    manifest = manifest if manifest is not None else manifest_line(files, floor=floor)
    script = installed or home / ROOT / "dispatch.py"
    return copy_exchange(
        [sys.executable, "-I", str(script)],
        env=environment(home, f"copy {tree_id(manifest)}"),
        cwd=home,
        manifest=manifest,
        contents={sha256(data): data for data in files.values()},
        files=sent,
    )


def copied(home: Path, files: dict[str, bytes], *, floor: str = "3.12") -> Path:
    """*files* copied in through the dispatcher; the tree their manifest's id names, which ``current`` names."""
    result = send_copy(home, files, floor=floor)
    assert result.status == 0, result.stderr
    named = result.stdout.decode().splitlines()[-1]
    assert named == tree_id(manifest_line(files, floor=floor))
    tree = home / ROOT / "trees" / named
    assert tree.is_dir()
    assert (home / ROOT / "current").resolve() == tree.resolve()
    return tree.resolve()


def stream_of(files: dict[str, bytes], *, lacking: list[str] | None = None) -> tuple[str, io.BytesIO]:
    """The id of *files*' tree, and what a client writes for it to a target that lacks *lacking* — every file by
    default — for a copy run in this process."""
    manifest = manifest_line(files)
    contents = {sha256(data): data for data in files.values()}
    wanted = sorted(contents) if lacking is None else lacking
    return tree_id(manifest), io.BytesIO(manifest + b"\n" + gz_tar({digest: contents[digest] for digest in wanted}))


def copy_here(root: str, files: dict[str, bytes], *, lacking: list[str] | None = None, out: io.StringIO | None = None) -> int:
    """*files* copied into *root* by the dispatcher's own ``copy``, run in this process, by a client that sends
    *lacking*."""
    named, stream = stream_of(files, lacking=lacking)
    return dev_dispatch.copy(root, stream, out if out is not None else io.StringIO(), tree_id=named)


def compiled(source: Path) -> Path:
    return Path(importlib.util.cache_from_source(str(source)))


MARKER_LINE = (dev_dispatch.RUN_MARKER + "\n").encode()


def answered(result: subprocess.CompletedProcess[bytes]) -> bytes:
    """What a run wrote on stdout: what follows ``RUN_MARKER``'s line."""
    assert result.stdout.startswith(MARKER_LINE), result.stdout
    return result.stdout[len(MARKER_LINE) :]


class TestTheRefusals:
    """Anything but the setup request, the copy, a question and a probe is refused, and nothing runs."""

    @pytest.mark.parametrize(
        "request_text",
        [
            pytest.param(None, id="a login"),
            pytest.param("", id="an empty request"),
            pytest.param("sh", id="a shell"),
            pytest.param("sh -s", id="a shell on stdin"),
            pytest.param("/usr/bin/python3 -c print(1)", id="python with a command"),
            pytest.param("{python} -I - extra", id="the setup request with an argument"),
            pytest.param("{python} -", id="the setup request without -I"),
            pytest.param("/elsewhere/python3 -I -", id="the setup request with another interpreter"),
            pytest.param("setup", id="setup by name"),
            pytest.param("copy", id="copy without a tree"),
            pytest.param("copy now", id="copy with an argument that is no tree"),
            pytest.param("copy {tree} {tree}", id="copy with two trees"),
            pytest.param("copy; touch pwned", id="copy with a command after it"),
            pytest.param("copy tree-{short}", id="copy naming a tree by less than its digest"),
            pytest.param("ask {tree}", id="ask with no word"),
            pytest.param("ask {tree} {word} {word}", id="ask with two words"),
            pytest.param("ask {tree} $(touch pwned)", id="ask with a substitution"),
            pytest.param("ask {tree} WyJ0b3VjaCJd!", id="ask with a word outside base64url"),
            pytest.param("ask {tree} e30", id="ask with an object"),
            pytest.param("ask {tree} WzFd", id="ask with a number"),
            pytest.param("ask {tree} bm90IGpzb24", id="ask with no JSON"),
            pytest.param("ask {tree} {nul}", id="ask with a NUL in a word"),
            pytest.param("probe {tree}", id="probe with no name"),
            pytest.param("probe {tree} ../escape.py", id="probe with a path"),
            pytest.param("probe {tree} sub/one.py", id="probe in a subdirectory"),
            pytest.param("probe {tree} one.sh", id="probe that is no Python file"),
            pytest.param("probe {tree} .py", id="probe with an empty name"),
            pytest.param("probe {tree} one.py {word} {word}", id="probe with two words"),
            pytest.param("probe {tree} one.py e30", id="probe with an object"),
            pytest.param("ask {word}", id="ask without a tree"),
            pytest.param("ask tree-x1 {word}", id="ask naming no digest"),
            pytest.param("ask tree-{upper} {word}", id="ask naming a digest in capitals"),
            pytest.param("probe one.py", id="probe without a tree"),
            pytest.param("ask current {word}", id="ask naming current"),
            pytest.param("ask ../trees/{tree} {word}", id="ask naming a path"),
            pytest.param("ask .removing-{tree} {word}", id="ask naming a tree set aside"),
            pytest.param("ask .building-{tree} {word}", id="ask naming a tree being built"),
            pytest.param("probe {tree}/.. one.py", id="probe naming a path"),
            pytest.param("cat .ssh/authorized_keys", id="another command"),
        ],
    )
    def test_anything_else_is_refused_and_runs_nothing(self, home: Path, request_text: str | None) -> None:
        install(home)
        if request_text is not None:
            request_text = request_text.format(
                python=sys.executable,
                tree=A_TREE,
                word=word(["detect"]),
                nul=word(["\x00"]),
                short=A_TREE[5:-1],
                upper=A_TREE[5:].upper(),
            )

        result = dispatch(home, request_text, b"import pathlib; pathlib.Path('pwned').touch()\n")

        assert result.returncode == dev_dispatch.REFUSED
        assert result.stdout == b""
        message = result.stderr.decode()
        assert message.startswith(f"emu-atlas-dev: refused: {request_text or '(a login)'}\n")
        assert "This key may only run the setup, copy a tree (copy <tree>), ask atlas a question" in message
        assert not (home / "pwned").exists()
        assert sorted(path.name for path in (home / ROOT).iterdir()) == ["dispatch.py"]

    @pytest.mark.parametrize(
        "malformed",
        ["WyJ0b3VjaCJd!", "e30", "WzFd", "bm90IGpzb24", "{nul}"],
        ids=["outside base64url", "an object", "a number", "no JSON", "a NUL"],
    )
    def test_a_malformed_argument_word_is_named(self, home: Path, malformed: str) -> None:
        install(home)
        request = f"ask {A_TREE} {malformed.format(nul=word(['\x00']))}"

        result = dispatch(home, request)

        assert result.returncode == dev_dispatch.REFUSED
        assert result.stderr.decode().startswith(
            f"emu-atlas-dev: refused: {request}\n  Its argument word is no JSON array of strings, base64url-encoded.\n"
        )

    def test_its_statuses_are_none_atlas_uses_and_differ(self) -> None:
        statuses = set(dev_dispatch.STATUSES)
        named = [dev_dispatch.REFUSED, dev_dispatch.CANNOT_RUN, dev_dispatch.COPY_FAILED, dev_dispatch.TREE_ABSENT]
        assert len(set(named)) == len(named)
        assert statuses == set(named)
        assert all(getattr(dev_dispatch, name) == status for status, name in dev_dispatch.STATUSES.items())
        assert statuses.isdisjoint({0, 1, 2})


class TestTheSetupRequest:
    def test_it_runs_the_script_on_its_stdin_in_the_home(self, home: Path) -> None:
        """#810 D8: setup:remote-target run again repairs the target through the key."""
        install(home)

        result = dispatch(
            home, f"{sys.executable} -I -", b"import os, sys; print(os.getcwd(), sys.flags.isolated, sys.argv)\n"
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.decode() == f"{home} 1 ['-']\n"


def twice(digest: str) -> bytes:
    """A gzip tar holding the file *digest* names twice: the dispatcher's own content of ``new``."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for _ in range(2):
            member = tarfile.TarInfo(digest)
            member.size = 4
            archive.addfile(member, io.BytesIO(b"new\n"))
    return buffer.getvalue()


class TestTheCopy:
    """#818 D3, D4: the manifest, the hashes the current tree lacks, a gzip tar of those, a tree built beside."""

    def test_a_copy_becomes_the_current_tree(self, home: Path) -> None:
        install(home)
        files = {"atlas/__init__.py": b"one\n", "scripts/probes/one.py": b"print(1)\n"}

        tree = copied(home, files)

        assert tree.parent == (home / ROOT / "trees").resolve()
        assert os.readlink(home / ROOT / "current") == f"trees/{tree.name}"
        assert contents(without_caches(snapshot(tree))) == {
            "atlas": None,
            "atlas/__init__.py": b"one\n",
            "scripts": None,
            "scripts/probes": None,
            "scripts/probes/one.py": b"print(1)\n",
            "tree.json": manifest_line(files),
        }

    def test_each_file_has_the_mode_its_manifest_names_whatever_was_sent(self, home: Path) -> None:
        install(home)
        files = {"atlas/__init__.py": b"\n", "scripts/probes/run.py": b"print(1)\n"}
        manifest = manifest_line(files, executable=frozenset({"scripts/probes/run.py"}))

        result = send_copy(home, files, manifest=manifest)

        assert result.status == 0, result.stderr
        tree = home / ROOT / "trees" / tree_id(manifest)
        modes = {name: stat.S_IMODE((tree / name).stat().st_mode) for name in files}
        assert modes == {"atlas/__init__.py": 0o600, "scripts/probes/run.py": 0o700}

    def test_the_next_copy_replaces_it_and_takes_deleted_files_along(self, home: Path) -> None:
        install(home)
        first = copied(home, {"atlas/__init__.py": b"one\n", "atlas/gone.py": b"\n"})

        second = copied(home, {"atlas/__init__.py": b"two\n"})

        assert second != first
        assert not first.exists()
        assert sorted(path.name for path in (home / ROOT / "trees").iterdir()) == [second.name]
        assert contents(without_caches(snapshot(second))) == {
            "atlas": None,
            "atlas/__init__.py": b"two\n",
            "tree.json": manifest_line({"atlas/__init__.py": b"two\n"}),
        }

    def test_it_asks_only_for_what_the_current_tree_lacks(self, home: Path) -> None:
        """#818 D3: one changed file goes over alone; a new file whose content the tree holds elsewhere does not."""
        install(home)
        first = {"atlas/__init__.py": b"same\n", "atlas/data.json": b"old\n"}
        copied(home, first)
        second = {**first, "atlas/data.json": b"new\n", "atlas/again.py": b"same\n"}

        result = send_copy(home, second)

        assert result.status == 0, result.stderr
        assert result.lacking == [sha256(b"new\n")]
        tree = (home / ROOT / "current").resolve()
        assert contents(without_caches(snapshot(tree))) == {
            "atlas": None,
            **{name: data for name, data in second.items()},
            "tree.json": manifest_line(second),
        }

    def test_the_same_content_at_two_paths_is_asked_for_once(self, home: Path) -> None:
        install(home)
        files = {"atlas/__init__.py": b"", "atlas/data/__init__.py": b"", "atlas/x.py": b"x\n"}

        result = send_copy(home, files)

        assert result.status == 0, result.stderr
        assert result.lacking == sorted([sha256(b""), sha256(b"x\n")])
        assert (home / ROOT / "current" / "atlas" / "data" / "__init__.py").read_bytes() == b""

    def test_a_tree_it_holds_lacks_nothing_and_stays_as_it_is(self, home: Path) -> None:
        install(home)
        files = {"atlas/__init__.py": b"\n"}
        tree = copied(home, files)
        before = snapshot(home / ROOT)

        result = send_copy(home, files, sent=lambda lacking: pytest.fail(f"asked for {lacking}"))

        assert result.status == 0, result.stderr
        assert result.lacking == []
        assert result.stdout.decode().splitlines()[-1] == tree.name
        assert snapshot(home / ROOT) == before

    def test_a_tree_it_holds_that_is_not_current_becomes_current_as_it_is(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The older tree stays where a question still runs from it; a copy naming it again takes it as it is."""
        root = str(tmp_path)
        first = {"atlas/__init__.py": b"one\n"}
        monkeypatch.setattr(dev_dispatch, "remove_others", lambda trees, tree: [])
        copy_here(root, first)
        copy_here(root, {"atlas/__init__.py": b"two\n"})
        named, stream = stream_of(first, lacking=[])
        held = snapshot(tmp_path / "trees" / named)
        out = io.StringIO()

        assert dev_dispatch.copy(root, stream, out, tree_id=named) == 0

        assert out.getvalue() == f"{dev_dispatch.LACKS}\n{named}\n"
        assert os.readlink(tmp_path / "current") == f"trees/{named}"
        assert snapshot(tmp_path / "trees" / named) == held

    def test_the_files_it_builds_from_are_copies_not_links(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#818 D3: a file of the new tree is its own, so nothing done to one tree reaches the other."""
        root = str(tmp_path)
        monkeypatch.setattr(dev_dispatch, "remove_others", lambda trees, tree: [])
        first = {"atlas/__init__.py": b"kept\n", "atlas/x.py": b"one\n"}
        second = {**first, "atlas/x.py": b"two\n"}
        old, stream = stream_of(first)
        dev_dispatch.copy(root, stream, io.StringIO(), tree_id=old)
        new, stream = stream_of(second, lacking=[sha256(b"two\n")])

        dev_dispatch.copy(root, stream, io.StringIO(), tree_id=new)

        kept = [tmp_path / "trees" / name / "atlas" / "__init__.py" for name in (old, new)]
        assert kept[0].read_bytes() == kept[1].read_bytes() == b"kept\n"
        assert not os.path.samestat(kept[0].stat(), kept[1].stat())
        assert kept[1].stat().st_nlink == 1

    def test_a_current_file_that_is_not_what_its_manifest_says_is_asked_for(self, home: Path) -> None:
        """What the current tree holds is read and hashed, not taken on its manifest's word."""
        install(home)
        files = {"atlas/__init__.py": b"one\n", "atlas/x.py": b"x\n"}
        first = copied(home, files)
        (first / "atlas" / "__init__.py").write_bytes(b"changed on the target\n")

        result = send_copy(home, {**files, "atlas/y.py": b"y\n"})

        assert result.status == 0, result.stderr
        assert result.lacking == sorted([sha256(b"one\n"), sha256(b"y\n")])
        assert (home / ROOT / "current" / "atlas" / "__init__.py").read_bytes() == b"one\n"

    @pytest.mark.parametrize(
        ("sent", "said"),
        [
            pytest.param(lambda archive, lacking: archive[: len(archive) // 2], "", id="cut off in the middle"),
            pytest.param(lambda archive, lacking: b"", "", id="before anything"),
            pytest.param(
                lambda archive, lacking: gz_tar({lacking[0]: b"new\n"}),
                "the stream ended with 1 of the 2 files lacked still to come",
                id="a file short",
            ),
            pytest.param(
                lambda archive, lacking: gz_tar({lacking[0]: b"not what its name says\n", lacking[1]: b"x" * 2048}),
                "is not the file its manifest names: its sha256 differs",
                id="a file that is not its sha256",
            ),
            pytest.param(
                lambda archive, lacking: gz_tar({sha256(b"asked for by no one\n"): b"asked for by no one\n"}),
                "is no file the copy lacked",
                id="a file not asked for",
            ),
            pytest.param(lambda archive, lacking: twice(lacking[0]), "is no file the copy lacked", id="a file twice"),
            pytest.param(lambda archive, lacking: archive.replace(b"\x1f\x8b", b"no", 1), "", id="no gzip"),
        ],
    )
    def test_files_that_do_not_arrive_whole_leave_the_tree_that_was_there(
        self, home: Path, sent: Callable[[bytes, list[str]], bytes], said: str
    ) -> None:
        install(home)
        copied(home, {"atlas/__init__.py": b"the tree that was there\n"})
        before = snapshot(home / ROOT)
        files = {"atlas/__init__.py": b"new\n", "atlas/big.py": b"x" * 2048}
        by_hash = {sha256(data): data for data in files.values()}

        result = send_copy(
            home, files, sent=lambda lacking: sent(gz_tar({digest: by_hash[digest] for digest in lacking}), lacking)
        )

        assert result.status == dev_dispatch.COPY_FAILED, result.stderr
        message = result.stderr.decode()
        assert message.startswith("emu-atlas-dev: the copy did not arrive whole (")
        assert said in message
        assert message.endswith("; the current tree is as it was.\n")
        assert snapshot(home / ROOT) == before

    @pytest.mark.parametrize(
        ("kind", "name"),
        [(tarfile.SYMTYPE, "a symlink"), (tarfile.LNKTYPE, "a hard link"), (tarfile.DIRTYPE, "a directory")],
        ids=["a symlink", "a hard link", "a directory"],
    )
    def test_a_member_that_is_no_file_leaves_the_tree(self, home: Path, kind: bytes, name: str) -> None:
        install(home)
        copied(home, {"atlas/__init__.py": b"kept\n"})
        before = snapshot(home / ROOT)

        def no_file(lacking: list[str]) -> bytes:
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
                member = tarfile.TarInfo(lacking[0])
                member.type = kind
                member.linkname = "/etc/passwd"
                archive.addfile(member)
            return buffer.getvalue()

        result = send_copy(home, {"atlas/__init__.py": b"new\n"}, sent=no_file)

        assert result.status == dev_dispatch.COPY_FAILED
        assert f"{sha256(b'new\n')} is no file" in result.stderr.decode(), name
        assert snapshot(home / ROOT) == before

    @pytest.mark.parametrize(
        ("manifest", "said"),
        [
            (b"not json", "no JSON"),
            (b"[]", "no requires_python of the form X.Y"),
            (b'{"requires_python": 3.12, "files": []}', "no requires_python of the form X.Y"),
            (b'{"requires_python": ">=3.12", "files": []}', "no requires_python of the form X.Y"),
            (b'{"requires_python": "3.12"}', "no list of files"),
            (b'{"requires_python": "3.12", "files": ["atlas/x.py"]}', "a file that is no JSON object"),
            *[
                (
                    json.dumps(
                        {"requires_python": "3.12", "files": [{"path": path, "sha256": sha256(b""), "executable": False}]}
                    ).encode(),
                    "is no path",
                )
                for path in (
                    "../outside.py",
                    "atlas/../../outside.py",
                    "/etc/x.py",
                    "atlas//x.py",
                    "atlas/./x.py",
                    "atlas/__pycache__/x.py",
                    "atlas/x.cpython-312.pyc",
                    "tree.json",
                    "",
                    "atlas/\x00.py",
                )
            ],
            (
                json.dumps({"requires_python": "3.12", "files": [{"path": 3, "sha256": "", "executable": False}]})
                .encode(),
                "3 is no path",
            ),
            (
                json.dumps(
                    {"requires_python": "3.12", "files": [{"path": "atlas/x.py", "sha256": "AB", "executable": False}]}
                ).encode(),
                "atlas/x.py: no sha256 in lowercase hex",
            ),
            (
                json.dumps(
                    {"requires_python": "3.12", "files": [{"path": "atlas/x.py", "sha256": sha256(b""), "executable": 1}]}
                ).encode(),
                "atlas/x.py: executable is neither true nor false",
            ),
            (
                json.dumps(
                    {
                        "requires_python": "3.12",
                        "files": [{"path": "atlas/x.py", "sha256": sha256(b""), "executable": False}] * 2,
                    }
                ).encode(),
                "a path listed twice",
            ),
        ],
    )
    def test_a_manifest_that_lists_no_tree_leaves_the_root_as_it_was(
        self, home: Path, manifest: bytes, said: str
    ) -> None:
        install(home)
        copied(home, {"atlas/__init__.py": b"kept\n"})
        before = snapshot(home / ROOT)

        result = send_copy(home, {}, manifest=manifest)

        assert result.status == dev_dispatch.COPY_FAILED
        assert result.lacking is None
        message = result.stderr.decode()
        assert message.startswith("emu-atlas-dev: the copy was refused: its manifest lists no tree: ")
        assert said in message
        assert message.endswith("; the current tree is as it was.\n")
        assert snapshot(home / ROOT) == before

    def test_a_manifest_that_is_not_the_one_the_id_names_is_refused(self, home: Path) -> None:
        install(home)
        manifest = manifest_line({"atlas/__init__.py": b"\n"})

        result = copy_exchange(
            [sys.executable, "-I", str(home / ROOT / "dispatch.py")],
            env=environment(home, f"copy {A_TREE}"),
            cwd=home,
            manifest=manifest,
            contents={},
        )

        assert result.status == dev_dispatch.COPY_FAILED
        assert result.stderr.decode() == (
            f"emu-atlas-dev: the copy was refused: its manifest is not the one {A_TREE} names: their sha256 differs;"
            " the current tree is as it was.\n"
        )
        assert sorted(path.name for path in (home / ROOT).iterdir()) == ["dispatch.py"]

    def test_a_manifest_that_breaks_off_is_refused(self, home: Path) -> None:
        install(home)
        manifest = manifest_line({"atlas/__init__.py": b"\n"})

        result = dispatch(home, f"copy {tree_id(manifest)}", manifest[:-5])

        assert result.returncode == dev_dispatch.COPY_FAILED
        assert result.stderr.decode() == (
            "emu-atlas-dev: the copy was refused: its manifest did not arrive whole: it broke off; the current tree"
            " is as it was.\n"
        )
        assert sorted(path.name for path in (home / ROOT).iterdir()) == ["dispatch.py"]

    def test_a_manifest_longer_than_the_limit_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dev_dispatch, "MANIFEST_LIMIT", 16)
        named, stream = stream_of({"atlas/__init__.py": b"\n"})

        with pytest.raises(dev_dispatch.Stop) as stopped:
            dev_dispatch.copy("/nonexistent", stream, io.StringIO(), tree_id=named)

        assert stopped.value.status == dev_dispatch.COPY_FAILED
        assert "its manifest did not arrive whole: it is longer than 16 bytes" in stopped.value.message

    def test_a_file_and_a_directory_of_one_name_leave_the_tree(self, home: Path) -> None:
        install(home)
        copied(home, {"atlas/__init__.py": b"kept\n"})
        before = snapshot(home / ROOT)

        result = send_copy(home, {"atlas/x": b"a file\n", "atlas/x/y.py": b"inside it\n"})

        assert result.status == dev_dispatch.COPY_FAILED
        assert result.stderr.decode() == (
            "emu-atlas-dev: the copy was refused: its manifest lists no tree: atlas/x is a file and a directory at"
            " once; the current tree is as it was.\n"
        )
        assert snapshot(home / ROOT) == before

    def test_a_swap_that_fails_leaves_no_copy_behind(self, home: Path) -> None:
        install(home)
        (home / ROOT / "current").mkdir()
        before = snapshot(home / ROOT)

        result = send_copy(home, {"atlas/__init__.py": b"\n"})

        assert result.status == dev_dispatch.COPY_FAILED
        assert "the copy arrived whole but could not be made current" in result.stderr.decode()
        assert "the current tree is as it was" in result.stderr.decode()
        assert snapshot(home / ROOT) == {**before, ".copy.lock": (0o600, b""), "trees": (0o700, None)}

    @pytest.mark.skipif(os.geteuid() == 0, reason="root removes a file whatever its directory's mode")
    def test_an_older_tree_that_cannot_be_removed_is_moved_aside_and_named(self, home: Path) -> None:
        install(home)
        first = copied(home, {"atlas/stuck/inside.py": b"\n"})
        (first / "atlas" / "stuck").chmod(0o500)
        try:
            result = send_copy(home, {"atlas/__init__.py": b"\n"})
            names = sorted(path.name for path in (home / ROOT / "trees").iterdir())
        finally:
            for stuck in (home / ROOT / "trees").glob("*/atlas/stuck"):
                stuck.chmod(0o700)

        assert result.status == 0, result.stderr
        second = (home / ROOT / "current").resolve()
        assert names == sorted([second.name, f".removing-{first.name}"])
        assert not first.exists()
        assert "emu-atlas-dev: the copy is current, but could not remove all of " in result.stderr.decode()
        assert ": Permission denied); a later copy tries again." in result.stderr.decode()
        third = copied(home, {"atlas/__init__.py": b"three\n"})
        assert sorted(path.name for path in (home / ROOT / "trees").iterdir()) == [third.name]

    def test_a_tree_a_killed_copy_left_half_built_is_removed_by_the_next(self, home: Path) -> None:
        install(home)
        copied(home, {"atlas/__init__.py": b"one\n"})
        left = home / ROOT / "trees" / ".building-x1"
        (left / "atlas").mkdir(parents=True)

        second = copied(home, {"atlas/__init__.py": b"two\n"})

        assert sorted(path.name for path in (home / ROOT / "trees").iterdir()) == [second.name]

    def test_a_copy_that_cannot_start_leaves_the_root_as_it_was(self, home: Path) -> None:
        install(home)
        (home / ROOT / "trees").write_text("", encoding="utf-8")
        before = snapshot(home / ROOT)

        result = send_copy(home, {"atlas/__init__.py": b"\n"})

        assert result.status == dev_dispatch.COPY_FAILED
        assert "emu-atlas-dev: the copy could not start (" in result.stderr.decode()
        assert "the current tree is as it was." in result.stderr.decode()
        assert snapshot(home / ROOT) == before

    @pytest.mark.skipif(os.geteuid() == 0, reason="root creates a directory whatever its parent's mode")
    def test_a_trees_directory_that_takes_no_new_tree_leaves_the_root_as_it_was(self, home: Path) -> None:
        """The directory is there, so the copy gets past creating it and fails making the new tree in it."""
        install(home)
        copied(home, {"atlas/__init__.py": b"the tree that was there\n"})
        trees = home / ROOT / "trees"
        trees.chmod(0o500)
        try:
            before = snapshot(home / ROOT)
            result = send_copy(home, {"atlas/__init__.py": b"new\n"})
            after = snapshot(home / ROOT)
        finally:
            trees.chmod(0o700)

        assert result.status == dev_dispatch.COPY_FAILED, result.stderr
        message = result.stderr.decode()
        assert message.startswith(f"emu-atlas-dev: the copy could not start ([Errno {errno.EACCES}] Permission denied")
        assert message.endswith("); the current tree is as it was.\n")
        assert after == before

    def test_older_trees_that_cannot_be_listed_leave_the_copy_current_and_named(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        real_listdir = os.listdir
        trees = str(tmp_path / "trees")

        def listdir(path: str) -> list[str]:
            if path == trees:
                raise PermissionError(errno.EACCES, "Permission denied", path)
            return real_listdir(path)

        monkeypatch.setattr(os, "listdir", listdir)

        status = copy_here(str(tmp_path), {"atlas/__init__.py": b"\n"})

        assert status == 0
        assert (tmp_path / "current" / "atlas" / "__init__.py").is_file()
        assert capsys.readouterr().err == (
            f"emu-atlas-dev: the copy is current, but could not list {trees} to remove the older trees:"
            " Permission denied.\n"
        )

    def test_an_older_tree_the_file_system_will_not_lock_stays_and_is_named(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        copy_here(str(tmp_path), {"atlas/__init__.py": b"one\n"})
        first = (tmp_path / "current").resolve()
        real_flock = dev_dispatch.fcntl.flock

        def no_locks_without_waiting(descriptor: int, operation: int) -> None:
            if operation & dev_dispatch.fcntl.LOCK_NB:
                raise OSError(errno.ENOLCK, "No locks available")
            real_flock(descriptor, operation)

        monkeypatch.setattr(dev_dispatch.fcntl, "flock", no_locks_without_waiting)
        capsys.readouterr()

        status = copy_here(str(tmp_path), {"atlas/__init__.py": b"two\n"})

        assert status == 0
        assert (tmp_path / "current" / "atlas" / "__init__.py").read_bytes() == b"two\n"
        assert first.is_dir()
        assert capsys.readouterr().err == (
            f"emu-atlas-dev: the copy is current, but could not remove {first}: No locks available.\n"
        )

    @pytest.mark.parametrize(
        ("error", "said"),
        [
            (dev_dispatch.Broken("a stream cut off"), "the copy did not arrive whole (a stream cut off)"),
            (tarfile.ReadError("invalid compressed data"), "the copy did not arrive whole (invalid compressed data)"),
            (EOFError("the stream ended"), "the copy did not arrive whole (the stream ended)"),
            (
                OSError(errno.ENOSPC, "No space left on device"),
                "the copy could not be built here while taking the files ([Errno 28] No space left on device)",
            ),
            (AttributeError("a bug of the dispatcher's own"), None),
            (KeyboardInterrupt(), None),
        ],
        ids=["broken", "no gzip", "cut short", "a full disk", "a bug", "an interrupt"],
    )
    def test_whatever_ends_a_build_takes_the_new_tree_away(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: BaseException, said: str | None
    ) -> None:
        """A broken stream and an error of this machine end the copy, each saying which; anything else goes on up."""
        (tmp_path / "trees").mkdir()

        def failing(staging: str, *_: object) -> None:
            Path(staging, "half").write_text("", encoding="utf-8")
            raise error

        monkeypatch.setattr(dev_dispatch, "build", failing)
        named, stream = stream_of({"atlas/__init__.py": b"\n"})
        manifest = dev_dispatch.receive_manifest(stream, tree_id=named)
        expected = dev_dispatch.Stop if said is not None else type(error)

        with pytest.raises(expected) as raised:
            dev_dispatch.take(str(tmp_path), manifest, stream, io.StringIO())

        if said is not None:
            assert isinstance(raised.value, dev_dispatch.Stop)
            assert raised.value.status == dev_dispatch.COPY_FAILED
            assert raised.value.message == f"emu-atlas-dev: {said}; the current tree is as it was."
        else:
            assert raised.value is error
        assert list((tmp_path / "trees").iterdir()) == []

    @pytest.mark.parametrize(
        ("error", "status", "said"),
        [
            (
                OSError(errno.ENOSPC, "No space left on device"),
                dev_dispatch.COPY_FAILED,
                "emu-atlas-dev: the copy could not be built here while compiling the tree ([Errno 28] No space left"
                " on device); the current tree is as it was.\n",
            ),
            (
                AttributeError("a bug of the dispatcher's own"),
                dev_dispatch.CANNOT_RUN,
                "AttributeError: a bug of the dispatcher's own\n"
                "emu-atlas-dev: the dispatcher failed (the traceback is above).\n",
            ),
        ],
        ids=["a full disk", "a bug"],
    )
    def test_a_build_that_fails_here_says_so_and_a_bug_shows_its_traceback(
        self,
        home: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        error: BaseException,
        status: int,
        said: str,
    ) -> None:
        """Through ``main``, as sshd runs it: neither is told as a copy that did not arrive whole."""
        install(home)
        named, stream = stream_of({"atlas/__init__.py": b"\n"})

        def fails(*_: object, **__: object) -> None:
            raise error

        monkeypatch.setattr(dev_dispatch.py_compile, "compile", fails)
        monkeypatch.setattr(dev_dispatch, "root_directory", lambda: str(home / ROOT))
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(stream))
        monkeypatch.setenv("SSH_ORIGINAL_COMMAND", f"copy {named}")

        assert dev_dispatch.main() == status

        err = capsys.readouterr().err
        assert err.endswith(said)
        assert ("Traceback (most recent call last):" in err) is (status == dev_dispatch.CANNOT_RUN)
        assert sorted(path.name for path in (home / ROOT / "trees").iterdir()) == []
        assert not (home / ROOT / "current").exists()

    def test_a_tree_that_cannot_be_put_at_its_id_leaves_the_tree_that_was_there(self, home: Path) -> None:
        """Something that is no directory stands where the tree is to go: the rename into place fails."""
        install(home)
        copied(home, {"atlas/__init__.py": b"the tree that was there\n"})
        files = {"atlas/__init__.py": b"new\n"}
        (home / ROOT / "trees" / tree_id(manifest_line(files))).write_text("in the way\n", encoding="utf-8")
        before = snapshot(home / ROOT)

        result = send_copy(home, files)

        assert result.status == dev_dispatch.COPY_FAILED
        message = result.stderr.decode()
        assert message.startswith("emu-atlas-dev: the copy arrived whole but could not be put in place (")
        assert message.endswith("; the current tree is as it was.\n")
        assert snapshot(home / ROOT) == before

    def test_installed_elsewhere_it_takes_its_root_from_where_it_lies(self, home: Path) -> None:
        elsewhere = install(home, root=Path("other"))
        files = {"atlas/__init__.py": b"\n"}

        result = send_copy(home, files, installed=elsewhere)

        assert result.status == 0, result.stderr
        assert (home / "other" / "current" / "tree.json").read_bytes() == manifest_line(files)
        assert not (home / ROOT).exists()


class TestTheBytecode:
    """#818 D5: the copy compiles the new tree with the target's own interpreter into checked-hash bytecode."""

    def test_every_source_has_checked_hash_bytecode_of_this_interpreter_after_a_copy(self, home: Path) -> None:
        install(home)
        files = {**_SAYING_ATLAS, "atlas/data/table.json": b"{}\n", "scripts/probes/say.py": b"print(1)\n"}

        tree = copied(home, files)

        for name, data in files.items():
            pyc = compiled(tree / name)
            if not name.endswith(".py"):
                assert not pyc.exists(), name
                continue
            header = pyc.read_bytes()[:16]
            assert header[:4] == importlib.util.MAGIC_NUMBER, name
            assert int.from_bytes(header[4:8], "little") == 0b11, name  # hash-based, checked at import
            assert header[8:16] == importlib.util.source_hash(data), name

    def test_only_a_changed_source_is_compiled_the_rest_comes_over(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = str(tmp_path)
        first = {"atlas/__init__.py": b"x = 1\n", "atlas/kept.py": b"y = 2\n"}
        copy_here(root, first)
        before = compiled(tmp_path / "current" / "atlas" / "kept.py").read_bytes()
        real_compile = py_compile.compile
        compiled_now: list[str] = []

        def recording(source: str, **kwargs: Any) -> object:
            compiled_now.append(Path(source).name)
            return real_compile(source, **kwargs)

        monkeypatch.setattr(dev_dispatch.py_compile, "compile", recording)
        second = {**first, "atlas/__init__.py": b"x = 3\n"}

        copy_here(root, second, lacking=[sha256(b"x = 3\n")])

        assert compiled_now == ["__init__.py"]
        tree = (tmp_path / "current").resolve()
        assert compiled(tree / "atlas" / "kept.py").read_bytes() == before
        assert compiled(tree / "atlas" / "__init__.py").read_bytes()[8:16] == importlib.util.source_hash(b"x = 3\n")

    def test_bytecode_the_current_tree_holds_that_is_not_checked_hash_is_compiled_anew(
        self, tmp_path: Path
    ) -> None:
        root = str(tmp_path)
        files = {"atlas/__init__.py": b"x = 1\n"}
        copy_here(root, files)
        old = compiled(tmp_path / "current" / "atlas" / "__init__.py")
        old.write_bytes(old.read_bytes()[:4] + (0).to_bytes(4, "little") + old.read_bytes()[8:])
        second = {**files, "atlas/x.py": b"\n"}

        copy_here(root, second, lacking=[sha256(b"\n")])

        header = compiled((tmp_path / "current").resolve() / "atlas" / "__init__.py").read_bytes()[:16]
        assert int.from_bytes(header[4:8], "little") == 0b11

    def test_bytecode_the_current_tree_holds_of_another_source_is_compiled_anew(self, tmp_path: Path) -> None:
        """Checked-hash bytecode of this interpreter comes over only where its header names this very source."""
        root = str(tmp_path)
        files = {"atlas/__init__.py": b"x = 1\n"}
        copy_here(root, files)
        old = compiled(tmp_path / "current" / "atlas" / "__init__.py")
        held = old.read_bytes()
        old.write_bytes(held[:8] + importlib.util.source_hash(b"x = 2\n") + held[16:])
        second = {**files, "atlas/x.py": b"\n"}

        copy_here(root, second, lacking=[sha256(b"\n")])

        header = compiled((tmp_path / "current").resolve() / "atlas" / "__init__.py").read_bytes()[:16]
        assert int.from_bytes(header[4:8], "little") == 0b11
        assert header[8:16] == importlib.util.source_hash(b"x = 1\n")

    def test_compiling_runs_nothing(self, home: Path, tmp_path: Path) -> None:
        install(home)
        ran = tmp_path / "ran"
        source = f"open({str(ran)!r}, 'w').close()\n".encode()

        copied(home, {"atlas/__init__.py": source, "scripts/probes/side_effect.py": source})

        assert not ran.exists()

    def test_a_source_that_does_not_compile_gets_none_and_still_fails_as_it_did(
        self, home: Path, fakes: Path
    ) -> None:
        install(home, bwrap=str(fakes / "bwrap"))
        tree = copied(home, {"scripts/probes/broken.py": b"def broken(:\n"})

        result = dispatch(home, f"probe {tree.name} broken.py")

        assert not compiled(tree / "scripts" / "probes" / "broken.py").exists()
        assert result.returncode == dev_dispatch.PROBE_NOT_PUT
        assert b"SyntaxError" in result.stderr

    def test_a_question_that_finds_bytecode_stale_or_missing_answers_from_the_source(
        self, home: Path, fakes: Path, tmp_path: Path
    ) -> None:
        """Bytecode of another source in ``__main__``'s place is checked against the source and not run."""
        install(home, bwrap=str(fakes / "bwrap"))
        tree = copied(home, _SAYING_ATLAS)
        asked = dispatch(home, f"ask {tree.name} {word(['detect'])}")
        other = tmp_path / "__main__.py"
        other.write_text("print('stale bytecode ran')\n", encoding="utf-8")
        py_compile.compile(
            str(other),
            cfile=str(compiled(tree / "atlas" / "__main__.py")),
            doraise=True,
            invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
        )
        compiled(tree / "atlas" / "__init__.py").unlink()

        again = dispatch(home, f"ask {tree.name} {word(['detect'])}")

        assert again.returncode == asked.returncode == 0
        assert again.stdout == asked.stdout
        assert b"stale" not in again.stdout


class TestWhatCannotRun:
    """A question or a probe that cannot run here ends with the reason, or with TREE_ABSENT alone, and nothing runs."""

    def _cannot_run(self, home: Path, request: str) -> str:
        result = dispatch(home, request)
        assert result.returncode == dev_dispatch.CANNOT_RUN
        assert result.stdout == b""
        return result.stderr.decode()

    @pytest.mark.parametrize("request_text", [f"ask {A_TREE} {word(['detect'])}", f"probe {A_TREE} one.py"])
    def test_a_tree_it_does_not_hold_is_answered_with_its_status_alone(self, home: Path, request_text: str) -> None:
        """#818 D2: the client's cue to copy, so it adds nothing to the stderr the client passes on."""
        install(home)

        result = dispatch(home, request_text)

        assert result.returncode == dev_dispatch.TREE_ABSENT
        assert result.stdout == b""
        assert result.stderr == b""

    def test_a_tree_without_a_readable_manifest_says_to_remove_it(self, home: Path) -> None:
        install(home)
        tree = copied(home, _SAYING_ATLAS)
        (tree / "tree.json").write_text("{", encoding="utf-8")

        assert "states no Python floor (tree.json: no JSON: Expecting" in self._cannot_run(
            home, f"ask {tree.name} {word([])}"
        )
        (tree / "tree.json").unlink()
        assert self._cannot_run(home, f"ask {tree.name} {word([])}") == (
            "emu-atlas-dev: the copied tree states no Python floor (tree.json: No such file or directory); nothing"
            f" ran.\n  The tree is damaged: remove {tree} on this target, and the next call copies it anew.\n"
        )


    @pytest.mark.skipif(os.geteuid() == 0, reason="root opens a directory whatever its mode")
    def test_a_tree_it_may_not_read_ends_the_request_with_the_error_not_a_retry(self, home: Path) -> None:
        install(home)
        tree = copied(home, _SAYING_ATLAS)
        tree.chmod(0o000)
        try:
            message = self._cannot_run(home, f"ask {tree.name} {word([])}")
        finally:
            tree.chmod(0o700)

        assert message == f"emu-atlas-dev: [Errno {errno.EACCES}] Permission denied: '{tree}'; nothing was changed.\n"

    def test_an_interpreter_below_the_trees_floor_is_refused_naming_both_versions(self, home: Path) -> None:
        install(home)
        tree = copied(home, _SAYING_ATLAS, floor="99.0")
        have = ".".join(str(part) for part in sys.version_info[:3])

        message = self._cannot_run(home, f"ask {tree.name} {word([])}")

        assert f"this target's Python is {have}, below the 99.0 the copied tree needs; nothing ran." in message

    def test_without_bwrap_nothing_runs_unsandboxed(self, home: Path, tmp_path: Path) -> None:
        missing = tmp_path / "no-bwrap"
        install(home, bwrap=str(missing))
        tree = copied(home, _SAYING_ATLAS)

        message = self._cannot_run(home, f"ask {tree.name} {word([])}")

        assert f"no sandbox at {missing} — there is none; nothing ran." in message

    def test_a_bwrap_that_cannot_start_a_sandbox_runs_nothing(self, home: Path, fakes: Path) -> None:
        install(home, bwrap=str(fakes / "bwrap-broken"))
        tree = copied(home, _SAYING_ATLAS)

        message = self._cannot_run(home, f"ask {tree.name} {word([])}")

        assert f"no sandbox at {fakes / 'bwrap-broken'} — it said: bwrap: setting up uid map: Permission denied;" in message

    def test_a_bwrap_that_is_not_executable_is_named_as_such(self, home: Path, tmp_path: Path) -> None:
        not_executable = tmp_path / "bwrap"
        not_executable.write_text("", encoding="utf-8")
        not_executable.chmod(0o644)
        install(home, bwrap=str(not_executable))
        tree = copied(home, _SAYING_ATLAS)

        message = self._cannot_run(home, f"ask {tree.name} {word([])}")

        assert f"no sandbox at {not_executable} — it is not executable; nothing ran." in message

    @pytest.mark.parametrize(
        ("which", "repair"),
        [
            ("missing", "install bubblewrap"),
            ("not executable", "install bubblewrap"),
            ("no sandbox", "install bubblewrap, or allow unprivileged user namespaces"),
        ],
    )
    def test_its_refusal_names_the_repair_the_setup_names(
        self, home: Path, tmp_path: Path, fakes: Path, monkeypatch: pytest.MonkeyPatch, which: str, repair: str
    ) -> None:
        from scripts import dev_target_install

        bwrap = {"missing": tmp_path / "no-bwrap", "no sandbox": fakes / "bwrap-broken"}.get(which, tmp_path / "bwrap")
        if which == "not executable":
            bwrap.write_text("", encoding="utf-8")
            bwrap.chmod(0o644)
        install(home, bwrap=str(bwrap))
        tree = copied(home, _SAYING_ATLAS)
        monkeypatch.setattr(dev_target_install, "DISPATCHER_SOURCE", DISPATCHER.read_text(encoding="utf-8"))
        monkeypatch.setattr(dev_target_install, "VALUES", {"lacks": ""})

        message = self._cannot_run(home, f"ask {tree.name} {word([])}")
        with pytest.raises(dev_target_install.Stop) as lacking:
            dev_target_install.check_sandbox(str(bwrap), sys.executable)

        assert message.endswith(f"; nothing ran.\n  To repair: {repair} on this target.\n")
        assert lacking.value.line.endswith(f"; {repair}")

    def test_a_lock_the_file_system_refuses_ends_the_question_with_the_error(
        self, home: Path, fakes: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        install(home, bwrap=str(fakes / "bwrap"))
        tree = copied(home, _SAYING_ATLAS)

        def no_locks(descriptor: object, operation: int) -> None:
            raise OSError(errno.ENOLCK, "No locks available")

        monkeypatch.setattr(dev_dispatch.fcntl, "flock", no_locks)
        monkeypatch.setattr(dev_dispatch, "root_directory", lambda: str(home / ROOT))
        monkeypatch.setenv("SSH_ORIGINAL_COMMAND", f"ask {tree.name} {word([])}")

        status = dev_dispatch.main()

        assert status == dev_dispatch.CANNOT_RUN
        assert capsys.readouterr().err == "emu-atlas-dev: [Errno 37] No locks available; nothing was changed.\n"

    def test_a_run_whose_connection_cannot_be_watched_is_ended(
        self, home: Path, fakes: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(home, bwrap=str(fakes / "bwrap"))
        tree = copied(home, {"scripts/probes/linger.py": _LINGERING_PROBE})
        token = uuid.uuid4().hex
        monkeypatch.setattr(dev_dispatch, "BWRAP", str(fakes / "bwrap"))

        def no_thread(self: object) -> None:
            raise RuntimeError("can't start new thread")

        monkeypatch.setattr(dev_dispatch.threading.Thread, "start", no_thread)
        try:
            with pytest.raises(dev_dispatch.Stop) as stopped:
                dev_dispatch.run_held(str(tree), "path", "linger.py", [token])
        finally:
            end_what_is_left(token)

        assert stopped.value.status == dev_dispatch.CANNOT_RUN
        assert stopped.value.message == (
            "emu-atlas-dev: could not watch the connection (can't start new thread); the run was ended."
        )
        assert processes_holding(token) == []

    def test_a_probe_the_tree_does_not_hold_is_named(self, home: Path, fakes: Path) -> None:
        install(home, bwrap=str(fakes / "bwrap"))
        tree = copied(home, _SAYING_ATLAS)

        assert "the copied tree has no scripts/probes/absent.py; nothing ran." in self._cannot_run(
            home, f"probe {tree.name} absent.py"
        )


class TestWhatReachesTheSandbox:
    """With the ``bwrap`` stand-in, which runs the command it is handed unsandboxed."""

    def test_a_question_imports_atlas_from_the_current_tree_alone(self, home: Path, fakes: Path) -> None:
        install(home, bwrap=str(fakes / "bwrap"))
        tree = copied(home, _SAYING_ATLAS)

        result = dispatch(home, f"ask {tree.name} {word(['detect'])}")

        assert result.returncode == 0, result.stderr
        said = json.loads(answered(result))
        assert said["file"] == str(tree / "atlas" / "__init__.py")
        assert said["argv"] == ["detect"]
        assert said["path"][-1] == str(tree)
        assert not any("site-packages" in entry for entry in said["path"])

    def test_words_with_blanks_quotes_and_parentheses_arrive_as_they_were(self, home: Path, fakes: Path) -> None:
        install(home, bwrap=str(fakes / "bwrap"))
        tree = copied(home, _SAYING_ATLAS)
        arguments = ["savefile-location", "/run/media/card/roms/A Game (Europe) 'Rev 1' \"x\".chd", "--core", "ü"]

        result = dispatch(home, f"ask {tree.name} {word(arguments)}")

        assert result.returncode == 0, result.stderr
        assert json.loads(answered(result))["argv"] == arguments

    @pytest.mark.parametrize("status", [0, 2, 7])
    def test_the_questions_own_status_is_passed_through(self, home: Path, fakes: Path, status: int) -> None:
        install(home, bwrap=str(fakes / "bwrap"))
        tree = copied(home, _SAYING_ATLAS)

        assert dispatch(home, f"ask {tree.name} {word([str(status)])}").returncode == status

    @pytest.mark.parametrize("padded", [False, True], ids=["unpadded", "padded"])
    def test_a_probe_script_runs_with_its_words(self, home: Path, fakes: Path, padded: bool) -> None:
        install(home, bwrap=str(fakes / "bwrap"))
        probe = b"import sys, atlas\nprint(sys.argv[0].endswith('/scripts/probes/two_answers.py'), sys.argv[1:])\n"
        tree = copied(home, {**_SAYING_ATLAS, "scripts/probes/two_answers.py": probe})
        encoded = word(["a b", "(c)"])
        encoded += "=" * (-len(encoded) % 4) if padded else ""

        result = dispatch(home, f"probe {tree.name} two_answers.py {encoded}")

        assert result.returncode == 0, result.stderr
        assert answered(result) == b"True ['a b', '(c)']\n"

    def test_a_run_ended_by_a_signal_answers_128_plus_its_number(self, home: Path, fakes: Path) -> None:
        install(home, bwrap=str(fakes / "bwrap"))
        tree = copied(home, {"scripts/probes/ends.py": b"import os, signal\nos.kill(os.getpid(), signal.SIGTERM)\n"})

        assert dispatch(home, f"probe {tree.name} ends.py").returncode == 128 + signal.SIGTERM

    def test_a_probe_that_crashes_ends_with_2_and_its_traceback(self, home: Path, fakes: Path) -> None:
        install(home, bwrap=str(fakes / "bwrap"))
        tree = copied(home, {"scripts/probes/crashes.py": b"import no_such_module\n"})

        result = dispatch(home, f"probe {tree.name} crashes.py")

        assert result.returncode == dev_dispatch.PROBE_NOT_PUT == 2
        assert answered(result) == b""
        assert b"ModuleNotFoundError: No module named 'no_such_module'" in result.stderr
        assert result.stderr.endswith(
            b"emu-atlas-dev: the probe crashes.py crashed (the traceback is above): a question it could not put.\n"
        )

    def test_a_question_that_crashes_keeps_atlas_s_own_status(self, home: Path, fakes: Path) -> None:
        """Only a probe's crash is turned into 2: atlas's own ending stands."""
        install(home, bwrap=str(fakes / "bwrap"))
        tree = copied(home, {"atlas/__init__.py": b"", "atlas/__main__.py": b"raise KeyError('x')\n"})

        result = dispatch(home, f"ask {tree.name} {word([])}")

        assert result.returncode == 1
        assert b"KeyError: 'x'" in result.stderr

    def test_a_probe_without_words_gets_none(self, home: Path, fakes: Path) -> None:
        install(home, bwrap=str(fakes / "bwrap"))
        tree = copied(home, {"scripts/probes/bare.py": b"import sys\nprint(sys.argv[1:])\n"})

        result = dispatch(home, f"probe {tree.name} bare.py")

        assert result.returncode == 0, result.stderr
        assert answered(result) == b"[]\n"


class TestACopyWhileAProbeRuns:
    def test_the_tree_a_probe_runs_from_stays_until_it_ends(self, home: Path, fakes: Path, tmp_path: Path) -> None:
        """With the ``bwrap`` stand-in; :class:`TestTheRealSandbox` shows the same through ``/usr/bin/bwrap``."""
        install(home, bwrap=str(fakes / "bwrap"))

        a_probe_running_while_a_copy_lands(home, tmp_path / "go")


class TestTheTreeARequestNames:
    """A question runs from the tree its id names, and from no other: never from ``current``."""

    def test_a_copy_answers_with_the_id_of_the_tree_it_made(self, home: Path) -> None:
        """#818 D2: ``tree-`` and the sha256 of the manifest, the directory's name and the last line on stdout."""
        install(home)
        files = {"atlas/__init__.py": b"\n"}

        result = send_copy(home, files)

        assert result.status == 0, result.stderr
        named = tree_id(manifest_line(files))
        assert result.stdout == f"{dev_dispatch.LACKS} {sha256(b'\n')}\n{named}\n".encode()
        assert dev_dispatch.TREE_ID.fullmatch(named)
        assert (home / ROOT / "trees" / named / "atlas" / "__init__.py").is_file()
        assert (home / ROOT / "trees" / named / "tree.json").read_bytes() == manifest_line(files)

    def test_a_question_whose_tree_another_copy_replaced_runs_nowhere(self, home: Path, fakes: Path) -> None:
        """Two clients: the first copies, the second copies, then the first asks."""
        install(home, bwrap=str(fakes / "bwrap"))
        first = copied(home, {**_SAYING_ATLAS, "marker.txt": b"first\n"})
        second = copied(home, {**_SAYING_ATLAS, "marker.txt": b"second\n"})
        assert not first.exists()

        result = dispatch(home, f"ask {first.name} {word(['detect'])}")

        assert result.returncode == dev_dispatch.TREE_ABSENT
        assert result.stdout == b""
        assert result.stderr == b""
        assert json.loads(answered(dispatch(home, f"ask {second.name} {word(['detect'])}")))["file"] == str(
            second / "atlas" / "__init__.py"
        )

    @pytest.mark.parametrize("request_form", ["ask {tree} {word}", "probe {tree} say.py {word}"])
    def test_the_tree_a_request_names_is_the_one_it_runs_from_whatever_is_current(
        self, home: Path, fakes: Path, request_form: str
    ) -> None:
        """A tree a run still holds outlives the next copy; a request naming it runs there, not from ``current``."""
        install(home, bwrap=str(fakes / "bwrap"))
        say_probe = b"import atlas, json\nprint(json.dumps({'file': atlas.__file__}))\n"
        files = {**_SAYING_ATLAS, "scripts/probes/linger.py": _LINGERING_PROBE, "scripts/probes/say.py": say_probe}
        first = copied(home, files)
        token = uuid.uuid4().hex
        holding = start(home, f"probe {first.name} linger.py {word([token])}")
        try:
            assert holding.stdout is not None
            assert holding.stdout.readline() == MARKER_LINE
            assert holding.stdout.readline() == b"started\n"
            second = copied(home, {**files, "marker.txt": b"second\n"})
            assert first.is_dir()

            asked = [
                dispatch(home, request_form.format(tree=tree.name, word=word(["detect"]))) for tree in (first, second)
            ]
        finally:
            holding.kill()
            holding.wait()
            end_what_is_left(token)

        assert [json.loads(answered(result))["file"] for result in asked] == [
            str(first / "atlas" / "__init__.py"),
            str(second / "atlas" / "__init__.py"),
        ]


class TestTheLockOnTheTree:
    """``_lock_tree``: only a tree id becomes a path, and a tree a copy removes while the lock is awaited is gone."""

    def test_a_tree_removed_between_opening_it_and_locking_it_runs_nowhere(
        self, home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The race a copy runs against a question: it took the exclusive lock first and set the tree aside."""
        install(home)
        tree = copied(home, _SAYING_ATLAS)
        real_flock = dev_dispatch.fcntl.flock
        removed: list[str] = []

        def a_copy_gets_there_first(descriptor: int, operation: int) -> None:
            if operation == dev_dispatch.fcntl.LOCK_SH and not removed:
                aside = tree.with_name(".removing-" + tree.name)
                tree.rename(aside)
                removed.append(str(aside))
            real_flock(descriptor, operation)

        def no_sandbox_may_start(*_: object) -> None:
            raise AssertionError("a run started from a tree that is gone")

        monkeypatch.setattr(dev_dispatch.fcntl, "flock", a_copy_gets_there_first)
        monkeypatch.setattr(dev_dispatch, "run_held", no_sandbox_may_start)

        with pytest.raises(dev_dispatch.Stop) as stop:
            dev_dispatch.run_sandboxed(
                str(home / ROOT), tree_id=tree.name, kind="module", target="atlas", arguments=[]
            )

        assert removed
        assert stop.value.status == dev_dispatch.TREE_ABSENT
        assert stop.value.message == ""

    @pytest.mark.parametrize("named", ["../trees", "/etc", "current", "tree-é", "tree-x1"])
    def test_nothing_but_a_tree_id_becomes_a_path(self, home: Path, named: str) -> None:
        install(home)

        with pytest.raises(ValueError, match="is no tree id"):
            dev_dispatch.run_sandboxed(str(home / ROOT), tree_id=named, kind="module", target="atlas", arguments=[])

    def test_a_non_ascii_tree_id_is_refused(self, home: Path) -> None:
        install(home)

        result = dispatch(home, f"ask tree-\u00e9 {word([])}")

        assert result.returncode == dev_dispatch.REFUSED


class TestTheDispatchersOwnCrash:
    def test_an_exception_of_its_own_ends_with_cannot_run_not_python_s_1(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def crashes(request: str, root: str) -> int:
            raise RuntimeError("an unforeseen state")

        monkeypatch.setattr(dev_dispatch, "dispatch", crashes)

        status = dev_dispatch.main()

        err = capsys.readouterr().err
        assert status == dev_dispatch.CANNOT_RUN
        assert "RuntimeError: an unforeseen state\n" in err
        assert err.endswith("emu-atlas-dev: the dispatcher failed (the traceback is above).\n")

    def test_a_run_that_cannot_be_started_after_the_trial_is_no_missing_sandbox(
        self, home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        install(home)
        tree = copied(home, _SAYING_ATLAS)

        def too_long(*_: object, **__: object) -> object:
            raise OSError(errno.E2BIG, "Argument list too long")

        monkeypatch.setattr(dev_dispatch, "check_sandbox", lambda bwrap: None)
        monkeypatch.setattr(dev_dispatch.subprocess, "Popen", too_long)

        with pytest.raises(dev_dispatch.Stop) as stop:
            dev_dispatch.run_held(str(tree), "module", "atlas", [])

        assert stop.value.status == dev_dispatch.CANNOT_RUN
        assert stop.value.message == (
            "emu-atlas-dev: the run could not be started (Argument list too long); nothing ran."
        )
        assert capsys.readouterr().out == ""


class TestACallerThatIsGone:
    """The dispatcher ends a run once nothing reads its output any more, and the run's tree is free again."""

    def test_when_its_output_is_closed(self, home: Path, fakes: Path) -> None:
        install(home, bwrap=str(fakes / "bwrap"))

        a_probe_whose_caller_hangs_up(home)

    def test_when_the_process_holding_its_output_dies(self, home: Path, fakes: Path) -> None:
        install(home, bwrap=str(fakes / "bwrap"))

        a_probe_whose_caller_goes(home)


class TestTheSandboxCommandLine:
    """How the dispatcher starts ``bwrap``, checked without one."""

    def test_the_whole_file_system_read_only_no_network_and_nothing_left_running(self) -> None:
        assert dev_dispatch.sandboxed("/usr/bin/bwrap", ["/usr/bin/python3", "-I"]) == [
            "/usr/bin/bwrap",
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
            "--unshare-net",
            "--unshare-pid",
            "--die-with-parent",
            "--",
            "/usr/bin/python3",
            "-I",
        ]

    def test_the_bwrap_it_starts_is_the_one_the_setup_checks(self) -> None:
        assert dev_dispatch.BWRAP == setup_remote_target.TARGET_BWRAP == REAL_BWRAP

    def test_the_question_runs_isolated_from_site_packages_and_python_variables(
        self, home: Path, fakes: Path, tmp_path: Path
    ) -> None:
        recorder = tmp_path / "bwrap"
        record = tmp_path / "argv.json"
        recorder.write_text(
            f"#!{sys.executable} -I\nimport json, sys\nopen({str(record)!r}, 'w').write(json.dumps(sys.argv[1:]))\n",
            encoding="utf-8",
        )
        recorder.chmod(0o755)
        install(home, bwrap=str(recorder))
        tree = copied(home, _SAYING_ATLAS)

        assert dispatch(home, f"ask {tree.name} {word(['detect'])}").returncode == 0

        argv = json.loads(record.read_text(encoding="utf-8"))
        command = argv[argv.index("--") + 1 :]
        assert command[:4] == [sys.executable, "-I", "-S", "-c"]
        assert command[5:] == [dev_dispatch.RUN_MARKER, str(tree), "module", "atlas", "detect"]


class TestTheRealSandbox:
    """Through ``/usr/bin/bwrap`` itself, where it can start a sandbox."""

    def test_a_probe_cannot_write_and_has_no_network(self, sandbox_home: Path) -> None:
        probe = (
            b"import json, socket, sys\n"
            b"refused = []\n"
            b"for target in sys.argv[1:]:\n"
            b"    try:\n"
            b"        open(target, 'w').close()\n"
            b"    except OSError as error:\n"
            b"        refused.append(error.errno)\n"
            b"print(json.dumps({'refused': refused, 'interfaces': [name for _, name in socket.if_nameindex()]}))\n"
        )
        home = sandbox_home
        install(home)
        tree = copied(home, {"scripts/probes/write.py": probe})
        targets = [str(home / "written"), str(tree / "written"), str(home.parent / "outside" / "written")]

        result = dispatch(home, f"probe {tree.name} write.py {word(targets)}")

        assert result.returncode == 0, result.stderr
        said = json.loads(answered(result))
        assert said == {"refused": [30, 30, 30], "interfaces": ["lo"]}  # 30: EROFS
        assert not any(Path(target).exists() for target in targets)

    def test_a_probe_sees_its_own_processes_and_none_of_the_devices(self, sandbox_home: Path) -> None:
        probe = (
            b"import json, os\n"
            b"seen = {}\n"
            b"for name in os.listdir('/proc'):\n"
            b"    if name.isdigit():\n"
            b"        try:\n"
            b"            seen[name] = open('/proc/%s/cmdline' % name, 'rb').read().decode('utf-8', 'replace')\n"
            b"        except OSError:\n"
            b"            pass\n"
            b"print(json.dumps(seen))\n"
        )
        install(sandbox_home)
        tree = copied(sandbox_home, {"scripts/probes/processes.py": probe})

        result = dispatch(sandbox_home, f"probe {tree.name} processes.py")

        assert result.returncode == 0, result.stderr
        seen = json.loads(answered(result))
        assert 1 <= len(seen) <= 3, seen
        assert all("processes.py" in line or "bwrap" in line for line in seen.values()), seen

    def test_a_run_whose_caller_hangs_up_ends(self, sandbox_home: Path) -> None:
        install(sandbox_home)

        a_probe_whose_caller_hangs_up(sandbox_home)

    def test_a_run_whose_caller_goes_ends(self, sandbox_home: Path) -> None:
        install(sandbox_home)

        a_probe_whose_caller_goes(sandbox_home)

    def test_the_tree_a_probe_runs_from_stays_until_it_ends(self, sandbox_home: Path) -> None:
        install(sandbox_home)

        a_probe_running_while_a_copy_lands(sandbox_home, sandbox_home.parent / "outside" / "go")

    def test_no_descriptor_that_writes_past_the_read_only_view_reaches_the_probe(self, sandbox_home: Path) -> None:
        """The tree's lock stays with the dispatcher (``held_tree`` says why)."""
        probe = (
            b"import json, os\n"
            b"written = []\n"
            b"for name in os.listdir('/proc/self/fd'):\n"
            b"    try:\n"
            b"        os.close(os.open('escaped', os.O_CREAT | os.O_WRONLY, 0o600, dir_fd=int(name)))\n"
            b"        written.append(name)\n"
            b"    except OSError:\n"
            b"        pass\n"
            b"print(json.dumps(written))\n"
        )
        install(sandbox_home)
        tree = copied(sandbox_home, {"scripts/probes/descriptors.py": probe})

        result = dispatch(sandbox_home, f"probe {tree.name} descriptors.py")

        assert result.returncode == 0, result.stderr
        assert json.loads(answered(result)) == []
        assert not list(tree.rglob("escaped"))

    def test_a_question_answers_as_it_does_unsandboxed(self, sandbox_home: Path) -> None:
        home = sandbox_home
        install(home)
        tree = copied(home, atlas_files())
        env = {"HOME": str(home), "PATH": os.environ["PATH"]}
        direct = subprocess.run(
            [sys.executable, "-m", "atlas", "detect"], cwd=REPO, capture_output=True, check=True, env=env
        )

        result = dispatch(home, f"ask {tree.name} {word(['detect'])}")

        assert result.returncode == 0, result.stderr
        assert answered(result) == direct.stdout

    def test_a_core_probe_loads_a_deployed_core_in_the_sandbox(self, sandbox_home: Path) -> None:
        """#810 D3: atlas starts the core probe as a child of ``sys.executable``, inside the sandbox too."""
        so_path = _a_core_that_answers_here()
        probe = (
            b"import json, sys\n"
            b"from atlas.machine import RealMachine\n"
            b"reading = RealMachine().read_core(sys.argv[1])\n"
            b"print(json.dumps({'status': reading.status, 'unloadable': reading.unloadable}))\n"
        )
        home = sandbox_home
        install(home)
        tree = copied(home, {**atlas_files(), "scripts/probes/read_core.py": probe})

        result = dispatch(home, f"probe {tree.name} read_core.py {word([so_path])}")

        assert result.returncode == 0, result.stderr
        assert json.loads(answered(result)) == {"status": "answered", "unloadable": None}


def _a_core_that_answers_here() -> str:
    """A deployed core the probe reads unsandboxed on this machine, or a skip saying why there is none."""
    from atlas.machine import CORE_READ_ANSWERED, RealMachine

    if not DEPLOYED_CORES.is_dir():
        pytest.skip(f"no cores are deployed at {DEPLOYED_CORES}")
    machine = RealMachine()
    for core in sorted(DEPLOYED_CORES.glob("*_libretro.so"))[:12]:
        if machine.read_core(str(core)).status == CORE_READ_ANSWERED:
            return str(core)
    pytest.skip(f"none of the first deployed cores at {DEPLOYED_CORES} loads here, sandbox or not")


class TestNonAsciiDigitsAndLetters:
    """The dispatcher's patterns take ASCII digits and letters only, as they always did."""

    @pytest.mark.parametrize("name", ["\u00e9t\u00e9.py", "\u0663.py"], ids=["a letter", "a digit"])
    def test_a_probe_name_with_a_non_ascii_character_is_refused(self, home: Path, name: str) -> None:
        install(home)

        assert dispatch(home, f"probe {A_TREE} {name}").returncode == dev_dispatch.REFUSED

    def test_a_floor_with_a_non_ascii_digit_is_no_floor(self, home: Path) -> None:
        install(home)

        result = send_copy(home, {"atlas/__init__.py": b"\n"}, floor="\u0663.12")

        assert result.status == dev_dispatch.COPY_FAILED
        assert "its manifest lists no tree: no requires_python of the form X.Y" in result.stderr.decode()


class TestTheOldSyntax:
    """#810 D6: both files that run on the target parse on an interpreter below the floor."""

    @pytest.mark.parametrize("script", [DISPATCHER, INSTALL_SCRIPT], ids=lambda path: path.name)
    def test_it_parses_as_python_3_8(self, script: Path) -> None:
        ast.parse(script.read_text(encoding="utf-8"), feature_version=(3, 8))

    def test_they_import_nothing_python_3_8_lacks(self) -> None:
        imported = _imports(DISPATCHER) | _imports(INSTALL_SCRIPT)

        assert imported == _PYTHON_3_8_NAMES


def _imports(script: Path) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(ast.parse(script.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported |= {f"{node.module}.{alias.name}" for alias in node.names}
    return imported


# Every name the two target-side files import, each present in Python 3.8's
# standard library; held equal to what they import, so a name they no longer
# import leaves the list too.
_PYTHON_3_8_NAMES = {
    "__future__.annotations",
    "base64",
    "binascii",
    "contextlib",
    "errno",
    "fcntl",
    "glob",
    "hashlib",
    "importlib.util",
    "json",
    "os",
    "py_compile",
    "re",
    "select",
    "shlex",
    "shutil",
    "stat",
    "subprocess",
    "sys",
    "tarfile",
    "tempfile",
    "threading",
    "traceback",
    "typing.Callable",
    "typing.Final",
    "typing.IO",
    "typing.Iterator",
    "typing.Literal",
    "typing.NamedTuple",
    "typing.cast",
}

