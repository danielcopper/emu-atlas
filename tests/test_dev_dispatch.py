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

The copied tree is a tar built here by hand, as whatever sends a copy has to
build it: the files, then the manifest ``tree.json`` last.

``HOME``, ``XDG_CONFIG_HOME`` and ``PATH`` are moved for the whole module
(``isolated_module``). The dispatcher runs with no audit hook of its own; it
works under each test's own ``home``, which :func:`environment` hands it as
``HOME``, and takes its root from where :func:`install` put it there.
"""

from __future__ import annotations

import ast
import base64
import errno
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from scripts import dev_dispatch, setup_remote_target
from tests.remote_target import GUARDED_VARIABLE, fake_path
from tests.remote_target_suite import DISPATCHER, INSTALL_SCRIPT, REPO, ROOT, contents, isolated_module, snapshot

pytestmark = pytest.mark.usefixtures(isolated_module.__name__)

REAL_BWRAP = "/usr/bin/bwrap"
MANIFEST = b'{"requires_python": "3.12"}'

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


def install(home: Path, *, bwrap: str = REAL_BWRAP, root: Path = ROOT) -> Path:
    """The dispatcher, installed under *home* as the setup installs it, naming *bwrap* for its sandbox."""
    installed = home / root / "dispatch.py"
    installed.parent.mkdir(parents=True, exist_ok=True)
    source = DISPATCHER.read_text(encoding="utf-8")
    line = f'BWRAP = "{REAL_BWRAP}"\n'
    assert source.count(line) == 1
    installed.write_text(source.replace(line, f"BWRAP = {bwrap!r}\n"), encoding="utf-8")
    return installed


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
    second = copied(home, {"scripts/probes/linger.py": _LINGERING_PROBE})
    assert not first.exists()
    assert sorted(path.name for path in (home / ROOT / "trees").iterdir()) == [second.name]


# Starts the dispatcher with its output on a pipe only this process reads,
# prints the dispatcher's pid, passes on the line the probe writes once it
# runs, and waits to be killed, the pipe still open: the parent that goes.
_PARENT = (
    "import os, subprocess, sys, time\n"
    "child = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)\n"
    "print(child.pid, flush=True)\n"
    "for line in child.stdout:\n"
    "    if line == b'started\\n':\n"
    "        print('started', flush=True)\n"
    "        break\n"
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


def tar(files: dict[str, bytes], *, manifest: bytes | None = MANIFEST, end: bool = True) -> bytes:
    """A tar stream of *files*, in order, and the manifest last; without its end-of-archive blocks where *end*
    is false."""
    buffer = io.BytesIO()
    archive = tarfile.open(fileobj=buffer, mode="w")
    for name, data in [*files.items(), *([("tree.json", manifest)] if manifest is not None else [])]:
        member = tarfile.TarInfo(name)
        member.size = len(data)
        member.mode = 0o644
        archive.addfile(member, io.BytesIO(data))
    if end:
        archive.close()
    return buffer.getvalue()


def atlas_files() -> dict[str, bytes]:
    """This checkout's ``atlas`` package, as a copy carries it."""
    package = REPO / "atlas"
    return {
        path.relative_to(REPO).as_posix(): path.read_bytes()
        for path in sorted(package.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def copied(home: Path, files: dict[str, bytes], *, manifest: bytes | None = MANIFEST) -> Path:
    """*files* copied in through the dispatcher; the tree ``current`` then names, whose id the copy answered with."""
    result = dispatch(home, "copy", tar(files, manifest=manifest))
    assert result.returncode == 0, result.stderr
    tree = (home / ROOT / "current").resolve()
    assert result.stdout == f"{tree.name}\n".encode()
    return tree


MARKER_LINE = (dev_dispatch.RUN_MARKER + "\n").encode()


def answered(result: subprocess.CompletedProcess[bytes]) -> bytes:
    """What a run wrote on stdout: what follows the marker the dispatcher writes right before the run starts."""
    assert result.stdout.startswith(MARKER_LINE), result.stdout
    return result.stdout[len(MARKER_LINE) :]


def latest(home: Path) -> str:
    """The id of the tree the last copy into *home* made."""
    return (home / ROOT / "current").resolve().name


def sandbox_unavailable() -> str | None:
    """Why the real sandbox cannot be shown here, or ``None`` where it starts: the dispatcher's own trial."""
    failure = dev_dispatch.sandbox_failure(REAL_BWRAP, sys.executable)
    return None if failure is None else f"{REAL_BWRAP} cannot start a sandbox here: {failure[1]}"


@pytest.fixture
def sandbox_home() -> Iterator[Path]:
    """A home outside ``/tmp`` — the sandbox puts an empty ``/tmp`` of its own over it — or a skip saying why
    the sandbox cannot be shown here."""
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
            pytest.param("copy now", id="copy with an argument"),
            pytest.param("copy; touch pwned", id="copy with a command after it"),
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
            pytest.param("probe one.py", id="probe without a tree"),
            pytest.param("ask current {word}", id="ask naming current"),
            pytest.param("ask ../trees/tree-x1 {word}", id="ask naming a path"),
            pytest.param("ask .removing-tree-x1 {word}", id="ask naming a tree set aside"),
            pytest.param("probe tree-x1/.. one.py", id="probe naming a path"),
            pytest.param("cat .ssh/authorized_keys", id="another command"),
        ],
    )
    def test_anything_else_is_refused_and_runs_nothing(self, home: Path, request_text: str | None) -> None:
        install(home)
        if request_text is not None:
            request_text = request_text.format(
                python=sys.executable, tree="tree-x1", word=word(["detect"]), nul=word(["\x00"])
            )

        result = dispatch(home, request_text, b"import pathlib; pathlib.Path('pwned').touch()\n")

        assert result.returncode == dev_dispatch.REFUSED
        assert result.stdout == b""
        message = result.stderr.decode()
        assert message.startswith(f"emu-atlas-dev: refused: {request_text or '(a login)'}\n")
        assert "This key may only run the setup, copy a tree (copy), ask atlas a question" in message
        assert not (home / "pwned").exists()
        assert sorted(path.name for path in (home / ROOT).iterdir()) == ["dispatch.py"]

    @pytest.mark.parametrize(
        "malformed",
        ["WyJ0b3VjaCJd!", "e30", "WzFd", "bm90IGpzb24", "{nul}"],
        ids=["outside base64url", "an object", "a number", "no JSON", "a NUL"],
    )
    def test_a_malformed_argument_word_is_named(self, home: Path, malformed: str) -> None:
        install(home)
        request = f"ask tree-x1 {malformed.format(nul=word(['\x00']))}"

        result = dispatch(home, request)

        assert result.returncode == dev_dispatch.REFUSED
        assert result.stderr.decode().startswith(
            f"emu-atlas-dev: refused: {request}\n  Its argument word is no JSON array of strings, base64url-encoded.\n"
        )

    def test_its_statuses_are_none_atlas_uses_and_differ(self) -> None:
        statuses = {dev_dispatch.REFUSED, dev_dispatch.CANNOT_RUN, dev_dispatch.COPY_FAILED, dev_dispatch.TREE_GONE}
        assert len(statuses) == 4
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


class TestTheCopy:
    def test_a_copy_becomes_the_current_tree(self, home: Path) -> None:
        install(home)

        tree = copied(home, {"atlas/__init__.py": b"one\n", "scripts/probes/one.py": b"print(1)\n"})

        assert tree.parent == (home / ROOT / "trees").resolve()
        assert os.readlink(home / ROOT / "current") == f"trees/{tree.name}"
        assert contents(snapshot(tree)) == {
            "atlas": None,
            "atlas/__init__.py": b"one\n",
            "scripts": None,
            "scripts/probes": None,
            "scripts/probes/one.py": b"print(1)\n",
            "tree.json": MANIFEST,
        }

    def test_the_next_copy_replaces_it_and_takes_deleted_files_along(self, home: Path) -> None:
        install(home)
        first = copied(home, {"atlas/__init__.py": b"one\n", "atlas/gone.py": b"\n"})

        second = copied(home, {"atlas/__init__.py": b"two\n"})

        assert second != first
        assert not first.exists()
        assert sorted(path.name for path in (home / ROOT / "trees").iterdir()) == [second.name]
        assert contents(snapshot(second)) == {"atlas": None, "atlas/__init__.py": b"two\n", "tree.json": MANIFEST}

    @pytest.mark.parametrize(
        "cut",
        [
            pytest.param(lambda stream, boundary: stream[: boundary + 700], id="inside a member's data"),
            pytest.param(lambda stream, boundary: stream[:boundary], id="between two members"),
            pytest.param(lambda stream, boundary: stream[: boundary + 100], id="inside a header"),
            pytest.param(lambda stream, boundary: b"", id="before anything"),
        ],
    )
    def test_a_copy_that_breaks_off_leaves_the_tree_that_was_there(
        self, home: Path, cut: Callable[[bytes, int], bytes]
    ) -> None:
        install(home)
        copied(home, {"atlas/__init__.py": b"the tree that was there\n"})
        before = snapshot(home / ROOT)
        stream = tar({"atlas/__init__.py": b"new\n", "atlas/big.py": b"x" * 2048})
        boundary = 512 + 512  # the first member's header and its one block of data

        result = dispatch(home, "copy", cut(stream, boundary))

        assert result.returncode == dev_dispatch.COPY_FAILED
        assert "the copy did not arrive whole" in result.stderr.decode()
        assert "the current tree is as it was" in result.stderr.decode()
        assert snapshot(home / ROOT) == before

    @pytest.mark.parametrize(
        "cut",
        [
            pytest.param(lambda stream: stream[: -512 - 100], id="inside the manifest's header"),
            pytest.param(lambda stream: stream[:-512], id="after the manifest's header"),
            pytest.param(lambda stream: stream[:-500], id="inside the manifest's data"),
        ],
    )
    def test_a_copy_that_breaks_off_in_the_manifest_leaves_the_tree_that_was_there(
        self, home: Path, cut: Callable[[bytes], bytes]
    ) -> None:
        install(home)
        copied(home, {"atlas/__init__.py": b"the tree that was there\n"})
        before = snapshot(home / ROOT)
        stream = tar({"atlas/__init__.py": b"new\n"}, end=False)

        result = dispatch(home, "copy", cut(stream))

        assert result.returncode == dev_dispatch.COPY_FAILED
        assert snapshot(home / ROOT) == before

    def test_a_whole_manifest_without_the_end_of_archive_blocks_is_a_whole_copy(self, home: Path) -> None:
        install(home)

        result = dispatch(home, "copy", tar({"atlas/__init__.py": b"new\n"}, end=False))

        assert result.returncode == 0, result.stderr
        assert (home / ROOT / "current" / "atlas" / "__init__.py").read_bytes() == b"new\n"

    @pytest.mark.parametrize(
        ("kind", "name"),
        [
            (tarfile.SYMTYPE, "atlas/link"),
            (tarfile.LNKTYPE, "atlas/hard"),
            (tarfile.FIFOTYPE, "atlas/fifo"),
            (tarfile.CHRTYPE, "atlas/device"),
        ],
        ids=["a symlink", "a hard link", "a fifo", "a device"],
    )
    def test_a_member_that_is_no_file_or_directory_leaves_the_tree(self, home: Path, kind: bytes, name: str) -> None:
        install(home)
        copied(home, {"atlas/__init__.py": b"kept\n"})
        before = snapshot(home / ROOT)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.linkname = "atlas/__init__.py"
            archive.addfile(member)
            manifest = tarfile.TarInfo("tree.json")
            manifest.size = len(MANIFEST)
            archive.addfile(manifest, io.BytesIO(MANIFEST))

        result = dispatch(home, "copy", buffer.getvalue())

        assert result.returncode == dev_dispatch.COPY_FAILED
        assert f"{name} is neither a file nor a directory" in result.stderr.decode()
        assert snapshot(home / ROOT) == before

    @pytest.mark.parametrize("name", ["../outside.py", "atlas/../../outside.py"])
    def test_a_member_the_data_filter_refuses_leaves_the_tree(self, home: Path, name: str) -> None:
        install(home)
        copied(home, {"atlas/__init__.py": b"kept\n"})
        before = snapshot(home / ROOT)

        result = dispatch(home, "copy", tar({name: b"escaped\n"}))

        assert result.returncode == dev_dispatch.COPY_FAILED
        assert "outside the destination" in result.stderr.decode()
        assert snapshot(home / ROOT) == before

    def test_an_absolute_member_lands_inside_the_new_tree(self, home: Path, tmp_path: Path) -> None:
        absolute = tmp_path / "absolute.py"
        install(home)

        tree = copied(home, {str(absolute): b"inside\n"})

        assert not absolute.exists()
        assert (tree / str(absolute).lstrip("/")).read_bytes() == b"inside\n"

    @pytest.mark.parametrize(
        "manifest",
        [None, b"not json", b'{"requires_python": 3.12}', b'{"requires_python": ">=3.12"}', b"[]"],
        ids=["no manifest", "no JSON", "a number", "a specifier", "a list"],
    )
    def test_a_copy_whose_manifest_states_no_floor_leaves_the_tree(self, home: Path, manifest: bytes | None) -> None:
        install(home)
        copied(home, {"atlas/__init__.py": b"kept\n"})
        before = snapshot(home / ROOT)

        result = dispatch(home, "copy", tar({"atlas/__init__.py": b"new\n"}, manifest=manifest))

        assert result.returncode == dev_dispatch.COPY_FAILED
        assert snapshot(home / ROOT) == before

    def test_a_manifest_that_is_not_the_last_member_is_not_the_end(self, home: Path) -> None:
        install(home)

        result = dispatch(home, "copy", tar({"tree.json": MANIFEST, "atlas/__init__.py": b"\n"}, manifest=None))

        assert result.returncode == dev_dispatch.COPY_FAILED
        assert "ended before its last member, tree.json" in result.stderr.decode()
        assert not (home / ROOT / "current").exists()

    def test_a_swap_that_fails_leaves_no_copy_behind(self, home: Path) -> None:
        install(home)
        (home / ROOT / "current").mkdir()
        before = snapshot(home / ROOT)

        result = dispatch(home, "copy", tar({"atlas/__init__.py": b"\n"}))

        assert result.returncode == dev_dispatch.COPY_FAILED
        assert "the copy arrived whole but could not be made current" in result.stderr.decode()
        assert "the current tree is as it was" in result.stderr.decode()
        assert snapshot(home / ROOT) == {**before, ".copy.lock": (0o600, b""), "trees": (0o700, None)}

    @pytest.mark.skipif(os.geteuid() == 0, reason="root removes a file whatever its directory's mode")
    def test_an_older_tree_that_cannot_be_removed_is_moved_aside_and_named(self, home: Path) -> None:
        install(home)
        first = copied(home, {"atlas/stuck/inside.py": b"\n"})
        (first / "atlas" / "stuck").chmod(0o500)
        try:
            result = dispatch(home, "copy", tar({"atlas/__init__.py": b"\n"}))
            names = sorted(path.name for path in (home / ROOT / "trees").iterdir())
        finally:
            for stuck in (home / ROOT / "trees").glob("*/atlas/stuck"):
                stuck.chmod(0o700)

        assert result.returncode == 0, result.stderr
        second = (home / ROOT / "current").resolve()
        assert names == sorted([second.name, f".removing-{first.name}"])
        assert not first.exists()
        assert "emu-atlas-dev: the copy is current, but could not remove all of " in result.stderr.decode()
        assert "inside.py: Permission denied); a later copy tries again." in result.stderr.decode()
        third = copied(home, {"atlas/__init__.py": b"\n"})
        assert sorted(path.name for path in (home / ROOT / "trees").iterdir()) == [third.name]

    def test_a_copy_that_cannot_start_leaves_the_root_as_it_was(self, home: Path) -> None:
        install(home)
        (home / ROOT / "trees").write_text("", encoding="utf-8")
        before = snapshot(home / ROOT)

        result = dispatch(home, "copy", tar({"atlas/__init__.py": b"\n"}))

        assert result.returncode == dev_dispatch.COPY_FAILED
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
            result = dispatch(home, "copy", tar({"atlas/__init__.py": b"new\n"}))
            after = snapshot(home / ROOT)
        finally:
            trees.chmod(0o700)

        assert result.returncode == dev_dispatch.COPY_FAILED, result.stderr
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

        status = dev_dispatch.copy(str(tmp_path), io.BytesIO(tar({"atlas/__init__.py": b"\n"})))

        assert status == 0
        assert (tmp_path / "current" / "atlas" / "__init__.py").is_file()
        assert capsys.readouterr().err == (
            f"emu-atlas-dev: the copy is current, but could not list {trees} to remove the older trees:"
            " Permission denied.\n"
        )

    def test_an_older_tree_the_file_system_will_not_lock_stays_and_is_named(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        dev_dispatch.copy(str(tmp_path), io.BytesIO(tar({"atlas/__init__.py": b"one\n"})))
        first = (tmp_path / "current").resolve()
        real_flock = dev_dispatch.fcntl.flock

        def no_locks_without_waiting(descriptor: int, operation: int) -> None:
            if operation & dev_dispatch.fcntl.LOCK_NB:
                raise OSError(errno.ENOLCK, "No locks available")
            real_flock(descriptor, operation)

        monkeypatch.setattr(dev_dispatch.fcntl, "flock", no_locks_without_waiting)
        capsys.readouterr()

        status = dev_dispatch.copy(str(tmp_path), io.BytesIO(tar({"atlas/__init__.py": b"two\n"})))

        assert status == 0
        assert (tmp_path / "current" / "atlas" / "__init__.py").read_bytes() == b"two\n"
        assert first.is_dir()
        assert capsys.readouterr().err == (
            f"emu-atlas-dev: the copy is current, but could not remove {first}: No locks available.\n"
        )

    def test_a_python_without_the_data_filter_is_refused_before_anything_is_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delattr(tarfile, "data_filter")
        have = ".".join(str(part) for part in sys.version_info[:3])

        root, stream = str(tmp_path), io.BytesIO(tar({"atlas/__init__.py": b"\n"}))

        with pytest.raises(dev_dispatch.Stop) as stopped:
            dev_dispatch.copy(root, stream)

        assert stopped.value.status == dev_dispatch.CANNOT_RUN
        assert f"this target's Python, {have}, has no tarfile data filter" in stopped.value.message
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize("error", [ValueError("not a tar error"), KeyboardInterrupt()], ids=["error", "interrupt"])
    def test_whatever_ends_an_unpack_takes_the_new_tree_away(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: BaseException
    ) -> None:
        (tmp_path / "trees").mkdir()

        def failing(stream: object, destination: str) -> None:
            Path(destination, "half").write_text("", encoding="utf-8")
            raise error

        monkeypatch.setattr(dev_dispatch, "unpack", failing)

        root, stream = str(tmp_path), io.BytesIO(b"")
        expected = type(error) if isinstance(error, KeyboardInterrupt) else dev_dispatch.Stop

        with pytest.raises(expected) as raised:
            dev_dispatch.take(root, stream)

        if isinstance(raised.value, dev_dispatch.Stop):
            assert raised.value.status == dev_dispatch.COPY_FAILED
            assert "the copy did not arrive whole (not a tar error)" in raised.value.message
        assert list((tmp_path / "trees").iterdir()) == []

    def test_a_member_whose_time_cannot_be_set_is_no_whole_copy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def no_time(self: tarfile.TarFile, tarinfo: tarfile.TarInfo, targetpath: str) -> None:
            raise tarfile.ExtractError("could not change modification time")

        monkeypatch.setattr(tarfile.TarFile, "utime", no_time)

        stream, destination = io.BytesIO(tar({"atlas/__init__.py": b"\n"})), str(tmp_path)

        with pytest.raises(tarfile.ExtractError, match="could not change modification time"):
            dev_dispatch.unpack(stream, destination)

    def test_installed_elsewhere_it_takes_its_root_from_where_it_lies(self, home: Path) -> None:
        elsewhere = install(home, root=Path("other"))

        result = dispatch(home, "copy", tar({"atlas/__init__.py": b"\n"}), installed=elsewhere)

        assert result.returncode == 0, result.stderr
        assert (home / "other" / "current" / "tree.json").read_bytes() == MANIFEST
        assert not (home / ROOT).exists()


class TestWhatCannotRun:
    """A question or a probe that cannot run here ends with the reason, and nothing runs."""

    def _cannot_run(self, home: Path, request: str) -> str:
        result = dispatch(home, request)
        assert result.returncode == dev_dispatch.CANNOT_RUN
        assert result.stdout == b""
        return result.stderr.decode()

    @pytest.mark.parametrize("request_text", [f"ask tree-none {word(['detect'])}", "probe tree-none one.py"])
    def test_before_any_copy_a_tree_it_names_is_not_there(self, home: Path, request_text: str) -> None:
        install(home)

        result = dispatch(home, request_text)

        assert result.returncode == dev_dispatch.TREE_GONE
        assert result.stdout == b""
        assert result.stderr.decode() == (
            "emu-atlas-dev: the tree tree-none is not here: a later copy replaced it, or it was never copied;"
            " nothing ran.\n  Ask again: every call copies first.\n"
        )

    def test_a_tree_without_a_readable_manifest_says_to_ask_again(self, home: Path) -> None:
        install(home)
        tree = copied(home, _SAYING_ATLAS)
        (tree / "tree.json").write_text("{", encoding="utf-8")

        assert "states no Python floor (tree.json: no JSON: Expecting" in self._cannot_run(
            home, f"ask {tree.name} {word([])}"
        )
        (tree / "tree.json").unlink()
        assert self._cannot_run(home, f"ask {tree.name} {word([])}") == (
            "emu-atlas-dev: the copied tree states no Python floor (tree.json: No such file or directory); nothing"
            " ran.\n  Ask again: every call copies first.\n"
        )


    @pytest.mark.skipif(os.geteuid() == 0, reason="root opens a directory whatever its mode")
    def test_a_tree_it_may_not_read_ends_the_request_with_the_error_not_a_retry(self, home: Path) -> None:
        install(home)
        tree = copied(home, _SAYING_ATLAS)
        tree.chmod(0o000)
        try:
            message = self._cannot_run(home, f"ask {latest(home)} {word([])}")
        finally:
            tree.chmod(0o700)

        assert message == f"emu-atlas-dev: [Errno {errno.EACCES}] Permission denied: '{tree}'; nothing was changed.\n"

    def test_an_interpreter_below_the_trees_floor_is_refused_naming_both_versions(self, home: Path) -> None:
        install(home)
        copied(home, _SAYING_ATLAS, manifest=b'{"requires_python": "99.0"}')
        have = ".".join(str(part) for part in sys.version_info[:3])

        message = self._cannot_run(home, f"ask {latest(home)} {word([])}")

        assert f"this target's Python is {have}, below the 99.0 the copied tree needs; nothing ran." in message

    def test_without_bwrap_nothing_runs_unsandboxed(self, home: Path, tmp_path: Path) -> None:
        missing = tmp_path / "no-bwrap"
        install(home, bwrap=str(missing))
        copied(home, _SAYING_ATLAS)

        assert f"no sandbox at {missing} — there is none; nothing ran." in self._cannot_run(home, f"ask {latest(home)} {word([])}")

    def test_a_bwrap_that_cannot_start_a_sandbox_runs_nothing(self, home: Path, fakes: Path) -> None:
        install(home, bwrap=str(fakes / "bwrap-broken"))
        copied(home, _SAYING_ATLAS)

        message = self._cannot_run(home, f"ask {latest(home)} {word([])}")

        assert f"no sandbox at {fakes / 'bwrap-broken'} — it said: bwrap: setting up uid map: Permission denied;" in message

    def test_a_bwrap_that_is_not_executable_is_named_as_such(self, home: Path, tmp_path: Path) -> None:
        not_executable = tmp_path / "bwrap"
        not_executable.write_text("", encoding="utf-8")
        not_executable.chmod(0o644)
        install(home, bwrap=str(not_executable))
        copied(home, _SAYING_ATLAS)

        message = self._cannot_run(home, f"ask {latest(home)} {word([])}")

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
        copied(home, _SAYING_ATLAS)
        monkeypatch.setattr(dev_target_install, "DISPATCHER_SOURCE", DISPATCHER.read_text(encoding="utf-8"))
        monkeypatch.setattr(dev_target_install, "VALUES", {"lacks": ""})

        message = self._cannot_run(home, f"ask {latest(home)} {word([])}")
        with pytest.raises(dev_target_install.Stop) as lacking:
            dev_target_install.check_sandbox(str(bwrap), sys.executable)

        assert message.endswith(f"; nothing ran.\n  To repair: {repair} on this target.\n")
        assert lacking.value.line.endswith(f"; {repair}")

    def test_a_lock_the_file_system_refuses_ends_the_question_with_the_error(
        self, home: Path, fakes: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        install(home, bwrap=str(fakes / "bwrap"))
        copied(home, _SAYING_ATLAS)

        def no_locks(descriptor: object, operation: int) -> None:
            raise OSError(errno.ENOLCK, "No locks available")

        monkeypatch.setattr(dev_dispatch.fcntl, "flock", no_locks)
        monkeypatch.setattr(dev_dispatch, "root_directory", lambda: str(home / ROOT))
        monkeypatch.setenv("SSH_ORIGINAL_COMMAND", f"ask {latest(home)} {word([])}")

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
        copied(home, _SAYING_ATLAS)

        assert "the copied tree has no scripts/probes/absent.py; nothing ran." in self._cannot_run(
            home, f"probe {latest(home)} absent.py"
        )


class TestWhatReachesTheSandbox:
    """With the ``bwrap`` stand-in, which runs the command it is handed unsandboxed."""

    def test_a_question_imports_atlas_from_the_current_tree_alone(self, home: Path, fakes: Path) -> None:
        install(home, bwrap=str(fakes / "bwrap"))
        tree = copied(home, _SAYING_ATLAS)

        result = dispatch(home, f"ask {latest(home)} {word(['detect'])}")

        assert result.returncode == 0, result.stderr
        said = json.loads(answered(result))
        assert said["file"] == str(tree / "atlas" / "__init__.py")
        assert said["argv"] == ["detect"]
        assert said["path"][-1] == str(tree)
        assert not any("site-packages" in entry for entry in said["path"])

    def test_words_with_blanks_quotes_and_parentheses_arrive_as_they_were(self, home: Path, fakes: Path) -> None:
        install(home, bwrap=str(fakes / "bwrap"))
        copied(home, _SAYING_ATLAS)
        arguments = ["savefile-location", "/run/media/card/roms/A Game (Europe) 'Rev 1' \"x\".chd", "--core", "ü"]

        result = dispatch(home, f"ask {latest(home)} {word(arguments)}")

        assert result.returncode == 0, result.stderr
        assert json.loads(answered(result))["argv"] == arguments

    @pytest.mark.parametrize("status", [0, 2, 7])
    def test_the_questions_own_status_is_passed_through(self, home: Path, fakes: Path, status: int) -> None:
        install(home, bwrap=str(fakes / "bwrap"))
        copied(home, _SAYING_ATLAS)

        assert dispatch(home, f"ask {latest(home)} {word([str(status)])}").returncode == status

    @pytest.mark.parametrize("padded", [False, True], ids=["unpadded", "padded"])
    def test_a_probe_script_runs_with_its_words(self, home: Path, fakes: Path, padded: bool) -> None:
        install(home, bwrap=str(fakes / "bwrap"))
        probe = b"import sys, atlas\nprint(sys.argv[0].endswith('/scripts/probes/two_answers.py'), sys.argv[1:])\n"
        copied(home, {**_SAYING_ATLAS, "scripts/probes/two_answers.py": probe})
        encoded = word(["a b", "(c)"])
        encoded += "=" * (-len(encoded) % 4) if padded else ""

        result = dispatch(home, f"probe {latest(home)} two_answers.py {encoded}")

        assert result.returncode == 0, result.stderr
        assert answered(result) == b"True ['a b', '(c)']\n"

    def test_a_run_ended_by_a_signal_answers_128_plus_its_number(self, home: Path, fakes: Path) -> None:
        install(home, bwrap=str(fakes / "bwrap"))
        copied(home, {"scripts/probes/ends.py": b"import os, signal\nos.kill(os.getpid(), signal.SIGTERM)\n"})

        assert dispatch(home, f"probe {latest(home)} ends.py").returncode == 128 + signal.SIGTERM

    def test_a_probe_without_words_gets_none(self, home: Path, fakes: Path) -> None:
        install(home, bwrap=str(fakes / "bwrap"))
        copied(home, {"scripts/probes/bare.py": b"import sys\nprint(sys.argv[1:])\n"})

        result = dispatch(home, f"probe {latest(home)} bare.py")

        assert result.returncode == 0, result.stderr
        assert answered(result) == b"[]\n"


class TestACopyWhileAProbeRuns:
    def test_the_tree_a_probe_runs_from_stays_until_it_ends(self, home: Path, fakes: Path, tmp_path: Path) -> None:
        """With the ``bwrap`` stand-in; :class:`TestTheRealSandbox` shows the same through ``/usr/bin/bwrap``."""
        install(home, bwrap=str(fakes / "bwrap"))

        a_probe_running_while_a_copy_lands(home, tmp_path / "go")


class TestTheTreeARequestNames:
    """A question runs from the tree its copy answered with, and from no other: never from ``current``."""

    def test_a_copy_answers_with_the_id_of_the_tree_it_made(self, home: Path) -> None:
        install(home)

        result = dispatch(home, "copy", tar({"atlas/__init__.py": b"\n"}))

        assert result.returncode == 0, result.stderr
        assert result.stdout == f"{latest(home)}\n".encode()
        assert dev_dispatch.TREE_ID.fullmatch(latest(home))

    def test_a_question_whose_tree_another_copy_replaced_runs_nowhere(self, home: Path, fakes: Path) -> None:
        """Two clients: the first copies, the second copies, then the first asks."""
        install(home, bwrap=str(fakes / "bwrap"))
        first = copied(home, {**_SAYING_ATLAS, "marker.txt": b"first\n"})
        second = copied(home, {**_SAYING_ATLAS, "marker.txt": b"second\n"})
        assert not first.exists()

        result = dispatch(home, f"ask {first.name} {word(['detect'])}")

        assert result.returncode == dev_dispatch.TREE_GONE
        assert result.stdout == b""
        assert result.stderr.decode() == (
            f"emu-atlas-dev: the tree {first.name} is not here: a later copy replaced it, or it was never copied;"
            " nothing ran.\n  Ask again: every call copies first.\n"
        )
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
            second = copied(home, files)
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

        assert dispatch(home, f"ask {latest(home)} {word(['detect'])}").returncode == 0

        argv = json.loads(record.read_text(encoding="utf-8"))
        command = argv[argv.index("--") + 1 :]
        assert command[:4] == [sys.executable, "-I", "-S", "-c"]
        assert command[5:] == [str(tree), "module", "atlas", "detect"]


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

        result = dispatch(home, f"probe {latest(home)} write.py {word(targets)}")

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
        copied(sandbox_home, {"scripts/probes/processes.py": probe})

        result = dispatch(sandbox_home, f"probe {latest(sandbox_home)} processes.py")

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

        result = dispatch(sandbox_home, f"probe {latest(sandbox_home)} descriptors.py")

        assert result.returncode == 0, result.stderr
        assert json.loads(answered(result)) == []
        assert not list(tree.rglob("escaped"))

    def test_a_question_answers_as_it_does_unsandboxed(self, sandbox_home: Path) -> None:
        home = sandbox_home
        install(home)
        copied(home, atlas_files())
        env = {"HOME": str(home), "PATH": os.environ["PATH"]}
        direct = subprocess.run(
            [sys.executable, "-m", "atlas", "detect"], cwd=REPO, capture_output=True, check=True, env=env
        )

        result = dispatch(home, f"ask {latest(home)} {word(['detect'])}")

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
        copied(home, {**atlas_files(), "scripts/probes/read_core.py": probe})

        result = dispatch(home, f"probe {latest(home)} read_core.py {word([so_path])}")

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

        assert dispatch(home, f"probe tree-x1 {name}").returncode == dev_dispatch.REFUSED

    def test_a_floor_with_a_non_ascii_digit_is_no_floor(self, home: Path) -> None:
        install(home)

        result = dispatch(home, "copy", tar({"atlas/__init__.py": b"\n"}, manifest='{"requires_python": "\u0663.12"}'.encode()))

        assert result.returncode == dev_dispatch.COPY_FAILED
        assert "states no Python floor: no requires_python of the form X.Y" in result.stderr.decode()


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
    "json",
    "os",
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
    "typing.IO",
    "typing.Iterator",
    "typing.Literal",
    "typing.cast",
}

