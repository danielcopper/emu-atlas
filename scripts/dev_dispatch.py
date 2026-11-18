"""The forced command of the build host's key on a remote target.

setup:remote-target installs this file as ``~/.local/lib/emu-atlas-dev/dispatch.py``
and names it in the key's ``authorized_keys`` line, run as
``/usr/bin/python3 -I <root>/dispatch.py``. sshd runs it in place of whatever
the key's holder asked for and hands that request over in
``SSH_ORIGINAL_COMMAND``; the request alone decides. It is split on blanks and
never handed to a shell. Four requests pass — the setup request
(``<interpreter> -I -``), ``copy``, ``ask <tree> <word>`` and
``probe <tree> <name> [<word>]``, where ``<tree>`` is what the copy answered —
and everything else, an empty request (a login) among it, is refused with
:data:`REFUSED`. What each request does, the exit statuses, and what the setup
request lets the key do: docs/remote-target.md, "What the key may do on the
target".

The file keeps to Python 3.8's grammar and to standard-library names that
exist there, so that an interpreter below the copied tree's floor reaches the
check that refuses it by naming both versions instead of dying on a
``SyntaxError``.
"""

from __future__ import annotations

import base64
import contextlib
import fcntl
import json
import os
import re
import select
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import traceback
from typing import IO, Iterator, Literal

# The dispatcher's own exit statuses, none of which atlas uses (it ends with 0
# or 2); docs/remote-target.md lists what each one covers. A question's or a
# probe's own status is passed through. TREE_GONE is 8 because the build host's
# client (scripts/ask.py) ends with 6 and 7 of its own.
REFUSED = 3
CANNOT_RUN = 4
COPY_FAILED = 5
TREE_GONE = 8

STATUSES = {REFUSED: "REFUSED", CANNOT_RUN: "CANNOT_RUN", COPY_FAILED: "COPY_FAILED", TREE_GONE: "TREE_GONE"}
"""Every status of the dispatcher's own, by its name: the one list the client and the tests read."""

PROBE_NOT_PUT = 2
"""What a probe script that crashed ends with: a question it could not put, never its own 1 for "does not hold"."""

RUN_MARKER = "emu-atlas-dev: the answer follows"
"""The line the bootstrap writes on stdout as its first act inside the sandbox: the client takes nothing in front of it
for the answer, so a shell that prints on stdout cannot land in front of one, and a sandbox or interpreter that fails
to start ends unmarked."""

# Every question and probe runs in this sandbox: the file system read-only but
# for a fresh /dev and /tmp, writable and gone with the run; a process view of
# its own, in which it sees its own processes only; no network; and nothing
# left running once the dispatcher ends. The dispatcher ends the run itself
# once its connection is gone (end_on_hang_up).
BWRAP = "/usr/bin/bwrap"
SANDBOX_OPTIONS = (
    "--ro-bind",
    "/",
    "/",
    "--dev",
    "/dev",
    "--proc",
    "/proc",
    "--tmpfs",
    "/tmp",  # NOSONAR S5443: the sandbox's own fresh tmpfs, a bwrap argument; nothing writes to the host's /tmp
    "--unshare-net",
    "--unshare-pid",
    "--die-with-parent",
)

PREFIX = "emu-atlas-dev"
MANIFEST = "tree.json"
CURRENT = "current"
TREES = "trees"
STAGED = ".current.new"
COPY_LOCK = ".copy.lock"
REMOVING = ".removing-"
PROBES = os.path.join("scripts", "probes")

COULD_NOT_START = "{}: the copy could not start ({}); the current tree is as it was."

MAY = (
    "  This key may only run the setup, copy a tree (copy), ask atlas a question (ask <tree> <word>)\n"
    "  and run a probe script (probe <tree> <name> [<word>]), <tree> being what the copy answered."
)

# A probe script's name: a bare file name in the tree's scripts/probes/. The
# build host's client (scripts/ask.py) checks a name against this same pattern.
PROBE_NAME = re.compile(r"\w+\.py", re.ASCII)
# A copied tree's id, the name of its directory under trees/: what a copy
# answers on stdout, and what a question names to run from exactly that tree.
TREE_PREFIX = "tree-"
TREE_ID = re.compile(re.escape(TREE_PREFIX) + r"\w+", re.ASCII)
_WORD = re.compile(r"[A-Za-z0-9_-]*={0,2}")
_FLOOR = re.compile(r"(\d+)\.(\d+)", re.ASCII)

# What the sandboxed interpreter runs, and what the build host's client runs
# for a question asked there: the tree the request names put on ``sys.path``
# behind the standard library — ``-I`` keeps the working directory and every
# PYTHON* variable out, ``-S`` the site-packages — and then the question as
# ``python -m atlas`` or the probe script as ``python <script>``. On a target
# it first writes RUN_MARKER, its first argument; asked here, that is empty.
BOOTSTRAP = (
    "import runpy, sys\n"
    "marker, tree, kind, target = sys.argv[1:5]\n"
    "if marker:\n"
    "    sys.stdout.write(marker + '\\n'); sys.stdout.flush()\n"
    "sys.argv = [target] + sys.argv[5:]\n"
    "sys.path.append(tree)\n"
    "if kind == 'module':\n"
    "    runpy.run_module(target, run_name='__main__', alter_sys=True)\n"
    "else:\n"
    "    try:\n"
    "        runpy.run_path(target, run_name='__main__')\n"
    "    except Exception:\n"
    "        import os, traceback\n"
    "        traceback.print_exc()\n"
    "        sys.stderr.write('emu-atlas-dev: the probe %s crashed (the traceback is above): a question it could not'\n"
    "                         ' put.\\n' % os.path.basename(target))\n"
    "        sys.exit(" + str(PROBE_NOT_PUT) + ")\n"
)

Kind = Literal["module", "path"]
"""How :data:`BOOTSTRAP` runs its target: as a module of the tree, or as a script by its path."""


class Stop(Exception):
    """The request ends here, with *message* on stderr and *status*."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def refused(request: str, why: str = "") -> Stop:
    lines = ["{}: refused: {}".format(PREFIX, request or "(a login)")]
    if why:
        lines.append("  " + why)
    return Stop(REFUSED, "\n".join(lines + [MAY]))


def reason(error: OSError) -> str:
    return error.strerror or str(error)


def root_directory() -> str:
    """Where this file lies: the install's root, whatever path it was installed under."""
    return os.path.dirname(os.path.abspath(__file__))


def sandboxed(bwrap: str, command: list[str]) -> list[str]:
    """*command* as ``bwrap`` starts it in the sandbox."""
    return [bwrap] + list(SANDBOX_OPTIONS) + ["--"] + command


def decode(word: str, request: str) -> list[str]:
    """The argument list *word* encodes, or a refusal of *request* that names the word."""
    not_a_list = refused(request, "Its argument word is no JSON array of strings, base64url-encoded.")
    if not _WORD.fullmatch(word):
        raise not_a_list
    bare = word.rstrip("=")
    try:
        data = base64.urlsafe_b64decode((bare + "=" * (-len(bare) % 4)).encode("ascii"))
        value = json.loads(data.decode("utf-8"))
    except ValueError:  # base64's own error is one as well
        raise not_a_list from None
    if not isinstance(value, list) or not all(isinstance(item, str) and "\x00" not in item for item in value):
        raise not_a_list
    return [str(item) for item in value]


def version_text(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def read_floor(tree: str) -> tuple[tuple[int, int] | None, str]:
    """The Python floor *tree*'s manifest states, or ``None`` and why it states none readably."""
    try:
        with open(os.path.join(tree, MANIFEST), encoding="utf-8") as handle:
            manifest = json.load(handle)
    except OSError as error:
        return None, reason(error)
    except ValueError as error:
        return None, "no JSON: {}".format(error)
    floor = manifest.get("requires_python") if isinstance(manifest, dict) else None
    match = _FLOOR.fullmatch(floor) if isinstance(floor, str) else None
    if match is None:
        return None, "no requires_python of the form X.Y"
    return (int(match.group(1)), int(match.group(2))), ""


def tree_floor(tree: str) -> tuple[int, int]:
    floor, why = read_floor(tree)
    if floor is None:
        raise Stop(
            CANNOT_RUN,
            "{}: the copied tree states no Python floor ({}: {}); nothing ran.\n"
            "  Ask again: every call copies first.".format(PREFIX, MANIFEST, why),
        )
    return floor


def check_floor(floor: tuple[int, int]) -> None:
    have = tuple(sys.version_info[:3])
    if have[:2] < floor:
        raise Stop(
            CANNOT_RUN,
            "{}: this target's Python is {}, below the {} the copied tree needs; nothing ran.".format(
                PREFIX, version_text(have), version_text(floor)
            ),
        )


SandboxFailure = Literal["missing", "not executable", "did not start", "failed"]
"""How a ``bwrap`` fails the trial: none there, not executable, not started, or unable to start a sandbox."""


def sandbox_failure(bwrap: str, python: str) -> tuple[SandboxFailure, str] | None:
    """How *bwrap* fails to start the sandbox here with *python* in it, and why; ``None`` where it can.

    The one trial both this dispatcher and the setup's install script run. The
    kind, not the wording of the reason, decides the repair (:func:`sandbox_repair`).
    """
    if not os.path.lexists(bwrap):
        return "missing", "there is none"
    if not os.access(bwrap, os.X_OK):
        return "not executable", "it is not executable"
    trial = sandboxed(bwrap, [python, "-I", "-S", "-c", "pass"])
    try:
        result = subprocess.run(trial, stdin=subprocess.DEVNULL, capture_output=True, check=False)
    except OSError as error:
        return "did not start", "it did not start: {}".format(reason(error))
    if result.returncode != 0:
        return "failed", "it said: {}".format(result.stderr.decode("utf-8", "replace").strip() or result.returncode)
    return None


def sandbox_repair(kind: SandboxFailure) -> str:
    """What makes a ``bwrap`` that failed the trial as *kind* start the sandbox; the setup's install says the same.

    A missing or not executable one is installed. Where one is there but did
    not start or started no sandbox, a kernel that forbids unprivileged user
    namespaces is named beside a reinstall, the reason quoting what ``bwrap``
    said where it said anything.
    """
    if kind in ("missing", "not executable"):
        return "install bubblewrap"
    return "install bubblewrap, or allow unprivileged user namespaces"


def no_sandbox(bwrap: str, kind: SandboxFailure, why: str) -> Stop:
    """The refusal for a *bwrap* that starts no sandbox: what it is, that nothing ran, and the repair."""
    return Stop(
        CANNOT_RUN,
        "{}: no sandbox at {} — {}; nothing ran.\n  To repair: {} on this target.".format(
            PREFIX, bwrap, why, sandbox_repair(kind)
        ),
    )


def check_sandbox(bwrap: str) -> None:
    """Refuse unless *bwrap* starts a sandbox here: nothing runs unsandboxed, ever."""
    failure = sandbox_failure(bwrap, sys.executable)
    if failure is not None:
        raise no_sandbox(bwrap, failure[0], failure[1])


def _locked_if_same(descriptor: int, tree: str) -> bool:
    """Lock *descriptor* shared, and say whether it is still the directory at *tree*; close it where not."""
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        same = os.path.samestat(os.fstat(descriptor), os.stat(tree))
    except (FileNotFoundError, NotADirectoryError):
        same = False
    except OSError:
        os.close(descriptor)
        raise
    if not same:
        os.close(descriptor)
    return same


def tree_gone(tree_id: str) -> Stop:
    return Stop(
        TREE_GONE,
        "{}: the tree {} is not here: a later copy replaced it, or it was never copied; nothing ran.\n"
        "  Ask again: every call copies first.".format(PREFIX, tree_id),
    )


def _lock_tree(root: str, *, tree_id: str) -> tuple[str, int]:
    """The tree *tree_id*, and a descriptor of its directory holding a shared lock on it.

    *tree_id* is a :data:`TREE_ID`, checked here as well as in :func:`dispatch`:
    nothing else may become a path under ``trees/``. A tree a copy removed —
    before this request, or between opening it and taking the lock — is
    refused with :data:`TREE_GONE`; no other tree stands in for it. Any other
    error ends the request.
    """
    if not TREE_ID.fullmatch(tree_id):
        raise ValueError("{!r} is no tree id".format(tree_id))
    tree = os.path.join(root, TREES, tree_id)
    try:
        descriptor = os.open(tree, os.O_RDONLY | os.O_DIRECTORY)
    except (FileNotFoundError, NotADirectoryError):
        raise tree_gone(tree_id) from None
    if not _locked_if_same(descriptor, tree):
        raise tree_gone(tree_id)
    return tree, descriptor


@contextlib.contextmanager
def held_tree(root: str, *, tree_id: str) -> Iterator[str]:
    """The tree *tree_id*, held for as long as the block runs: a copy removes no held tree (:func:`remove_others`).

    The lock lives on a descriptor of the tree's directory that this process
    keeps, and a sandboxed program must not inherit: a directory descriptor
    opened outside the sandbox writes through its read-only view. That is why
    the dispatcher waits for ``bwrap`` rather than becoming it.
    """
    tree, descriptor = _lock_tree(root, tree_id=tree_id)
    try:
        yield tree
    finally:
        os.close(descriptor)


def end_on_hang_up(process: subprocess.Popen[bytes], descriptor: int) -> None:
    """Kill *process* once nothing reads *descriptor* any more: the connection that asked for the run is gone.

    sshd sends a command without a terminal no signal when its connection
    drops, so the hang-up of the dispatcher's own stdout is the sign. Killing
    ``bwrap`` ends the sandboxed program with it (``--die-with-parent``).
    """
    poller = select.poll()
    poller.register(descriptor, 0)
    for _, events in poller.poll():
        if events & (select.POLLHUP | select.POLLERR):
            process.kill()


def run_held(tree: str, kind: Kind, target: str, arguments: list[str]) -> int:
    """Run the question or the probe script *target* of *tree* in the sandbox, and wait for it."""
    check_floor(tree_floor(tree))
    if kind == "path":
        target = os.path.join(tree, PROBES, target)
        if not os.path.isfile(target):
            raise Stop(
                CANNOT_RUN, "{}: the copied tree has no {}; nothing ran.".format(PREFIX, os.path.relpath(target, tree))
            )
    check_sandbox(BWRAP)
    command = bootstrapped(sys.executable, tree, kind, target, arguments, marker=RUN_MARKER)
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        process = subprocess.Popen(sandboxed(BWRAP, command))
    except OSError as error:
        # The trial just started bwrap, so this is no missing sandbox: an
        # argument list too long, or no process to be had.
        raise Stop(CANNOT_RUN, "{}: the run could not be started ({}); nothing ran.".format(PREFIX, reason(error)))
    try:
        threading.Thread(target=end_on_hang_up, args=(process, sys.stdout.fileno()), daemon=True).start()
    except RuntimeError as error:
        process.kill()
        process.wait()
        raise Stop(CANNOT_RUN, "{}: could not watch the connection ({}); the run was ended.".format(PREFIX, error))
    return status_of(process.wait())


def status_of(returncode: int) -> int:
    """A process's exit status as a shell gives it: 128 plus the signal's number for one a signal ended."""
    return returncode if returncode >= 0 else 128 - returncode


def bootstrapped(
    python: str, tree: str, kind: Kind, target: str, arguments: list[str], *, marker: str = ""
) -> list[str]:
    """The command line that runs the question or the probe script *target* of *tree* on *python*: :data:`BOOTSTRAP`,
    writing *marker* first where it is given."""
    return [python, "-I", "-S", "-c", BOOTSTRAP, marker, tree, kind, target] + arguments


def run_sandboxed(root: str, *, tree_id: str, kind: Kind, target: str, arguments: list[str]) -> int:
    """Run the question or the probe script *target* of the tree *tree_id* in the sandbox; its own status."""
    with held_tree(root, tree_id=tree_id) as tree:
        return run_held(tree, kind, target, arguments)


def unpack(stream: IO[bytes], destination: str) -> None:
    """Unpack the tar *stream* into *destination*: files and directories only, ending with the manifest.

    The manifest is the stream's last member, so a stream that breaks off
    anywhere before it arrived whole leaves a tree without one, which is
    refused here — tarfile reads a stream that ends between two members as one
    that ended. Every error extracting a member counts, setting a mode or a
    time among them.
    """
    last = None
    with tarfile.open(fileobj=stream, mode="r|", errorlevel=2) as archive:
        for member in archive:
            if not (member.isreg() or member.isdir()):
                raise tarfile.TarError("{} is neither a file nor a directory".format(member.name))
            archive.extract(member, destination, filter="data")
            last = os.path.normpath(member.name)
    if last != MANIFEST:
        raise tarfile.TarError("the stream ended before its last member, {}".format(MANIFEST))
    floor, why = read_floor(destination)
    if floor is None:
        raise tarfile.TarError("its {} states no Python floor: {}".format(MANIFEST, why))


def make_current(root: str, tree: str) -> None:
    """Point ``current`` at *tree* with one rename."""
    staged = os.path.join(root, STAGED)
    if os.path.lexists(staged):
        os.unlink(staged)
    os.symlink(os.path.join(TREES, os.path.basename(tree)), staged)
    try:
        os.replace(staged, os.path.join(root, CURRENT))
    except OSError:
        os.unlink(staged)
        raise


def discard(path: str) -> str | None:
    """Remove the tree at *path*; what is left of it where that fails, and why."""
    failures: list[str] = []

    def record(failed_path: str, error: BaseException) -> None:
        reason_text = error.strerror if isinstance(error, OSError) and error.strerror else str(error)
        failures.append("{}: {}".format(failed_path, reason_text))

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=lambda function, failed_path, error: record(failed_path, error))
    else:
        shutil.rmtree(path, onerror=lambda function, failed_path, info: record(failed_path, info[1]))
    if os.path.lexists(path):
        why = failures[0] if failures else "unknown"
        return "could not remove all of {} ({}); a later copy tries again".format(path, why)
    return None


def remove_unless_held(path: str) -> str | None:
    """Remove the tree at *path* unless a question or a probe holds it (:func:`held_tree`); what went wrong.

    Under the exclusive lock the tree is first renamed out of the way, so a
    request that waited for the lock finds no half-removed tree at the old path.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except FileNotFoundError:
        return None
    except OSError as error:
        return "could not open {} to remove it: {}".format(path, reason(error))
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        aside = path
        if not os.path.basename(path).startswith(REMOVING):
            aside = os.path.join(os.path.dirname(path), REMOVING + os.path.basename(path))
            os.rename(path, aside)
        return discard(aside)
    except BlockingIOError:
        return None  # held: a later copy removes it
    except OSError as error:
        return "could not remove {}: {}".format(path, reason(error))
    finally:
        os.close(descriptor)


def remove_others(trees: str, tree: str) -> list[str]:
    """Remove every tree in *trees* but *tree*: older copies, and what a copy cut off by a signal left behind.

    A tree a question or a probe still runs from stays, for a later copy to
    remove. What could not be removed comes back as one line each.
    """
    try:
        names = os.listdir(trees)
    except OSError as error:
        return ["could not list {} to remove the older trees: {}".format(trees, reason(error))]
    notes = []
    for name in names:
        if name != os.path.basename(tree):
            note = remove_unless_held(os.path.join(trees, name))
            if note is not None:
                notes.append(note)
    return notes


def take(root: str, stream: IO[bytes]) -> str:
    """Unpack *stream* beside the current tree and make it current; the new tree.

    An error or an interrupt before the swap removes the new tree again and
    leaves the current one as it was; a copy killed outright leaves its tree
    for the next copy to remove.
    """
    try:
        tree = tempfile.mkdtemp(prefix=TREE_PREFIX, dir=os.path.join(root, TREES))
    except OSError as error:
        raise Stop(
            COPY_FAILED, COULD_NOT_START.format(PREFIX, error)
        ) from None
    try:
        unpack(stream, tree)
    except BaseException as error:
        left = discard(tree)
        if not isinstance(error, Exception):
            raise
        raise Stop(
            COPY_FAILED,
            "{}: the copy did not arrive whole ({}); the current tree is as it was.{}".format(
                PREFIX, error, " It " + left + "." if left else ""
            ),
        ) from None
    try:
        make_current(root, tree)
    except OSError as error:
        left = discard(tree)
        raise Stop(
            COPY_FAILED,
            "{}: the copy arrived whole but could not be made current ({}); the current tree is as it was.{}".format(
                PREFIX, error, " It " + left + "." if left else ""
            ),
        ) from None
    return tree


def copy(root: str, stream: IO[bytes]) -> int:
    """Take the tree on *stream* and answer its id on stdout; a copy that does not arrive whole leaves the current
    one as it was.

    The id, the one line this request writes on stdout, is what a question
    names to run from this tree and no other (:func:`held_tree`).
    """
    if not hasattr(tarfile, "data_filter"):
        raise Stop(
            CANNOT_RUN,
            "{}: this target's Python, {}, has no tarfile data filter to unpack a copy with; nothing changed.".format(
                PREFIX, version_text(tuple(sys.version_info[:3]))
            ),
        )
    trees = os.path.join(root, TREES)
    try:
        os.makedirs(trees, exist_ok=True)
        lock = open(os.path.join(root, COPY_LOCK), "a")
    except OSError as error:
        raise Stop(COPY_FAILED, COULD_NOT_START.format(PREFIX, error)) from None
    with lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
        except OSError as error:
            raise Stop(COPY_FAILED, COULD_NOT_START.format(PREFIX, error)) from None
        tree = take(root, stream)
        notes = remove_others(trees, tree)
    for note in notes:
        sys.stderr.write("{}: the copy is current, but {}.\n".format(PREFIX, note))
    sys.stdout.write(os.path.basename(tree) + "\n")
    return 0


def dispatch(request: str, root: str) -> int:
    words = request.split()
    if words == [sys.executable, "-I", "-"]:
        os.chdir(os.path.expanduser("~"))
        os.execv(sys.executable, [sys.executable, "-I", "-"])
    if words == ["copy"]:
        return copy(root, sys.stdin.buffer)
    if len(words) < 3 or not TREE_ID.fullmatch(words[1]):
        raise refused(request)
    if len(words) == 3 and words[0] == "ask":
        arguments = decode(words[2], request)
        return run_sandboxed(root, tree_id=words[1], kind="module", target="atlas", arguments=arguments)
    if len(words) in (3, 4) and words[0] == "probe" and PROBE_NAME.fullmatch(words[2]):
        arguments = decode(words[3], request) if len(words) == 4 else []
        return run_sandboxed(root, tree_id=words[1], kind="path", target=words[2], arguments=arguments)
    raise refused(request)


def main() -> int:
    os.umask(0o077)
    try:
        return dispatch(os.environ.get("SSH_ORIGINAL_COMMAND", ""), root_directory())
    except Stop as stop:
        sys.stderr.write(stop.message + "\n")
        return stop.status
    except OSError as error:
        sys.stderr.write("{}: {}; nothing was changed.\n".format(PREFIX, error))
        return CANNOT_RUN
    except Exception:
        # An exception of the dispatcher's own ends with a status of its own,
        # never Python's 1.
        traceback.print_exc()
        sys.stderr.write("{}: the dispatcher failed (the traceback is above).\n".format(PREFIX))
        return CANNOT_RUN


if __name__ == "__main__":
    sys.exit(main())
