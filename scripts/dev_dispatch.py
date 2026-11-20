"""The forced command of the build host's key on a remote target.

setup:remote-target installs this file as ``~/.local/lib/emu-atlas-dev/dispatch.py``
and names it in the key's ``authorized_keys`` line, run as
``/usr/bin/python3 -I <root>/dispatch.py``. sshd runs it in place of whatever
the key's holder asked for and hands that request over in
``SSH_ORIGINAL_COMMAND``; the request alone decides. It is split on blanks and
never handed to a shell. Four requests pass — the setup request
(``<interpreter> -I -``), ``copy <tree>``, ``ask <tree> <word>`` and
``probe <tree> <name> [<word>]``, where ``<tree>`` is ``tree-`` and the sha256
of the tree's manifest — and everything else, an empty request (a login) among
it, is refused with :data:`REFUSED`. What each request does, the exit statuses,
and what the setup request lets the key do: docs/remote-target.md, "What the
key may do on the target".

The file keeps to Python 3.8's grammar and to standard-library names that
exist there, so that an interpreter below the copied tree's floor reaches the
check that refuses it by naming both versions instead of dying on a
``SyntaxError``.
"""

from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
import py_compile
import re
import select
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import traceback
from typing import IO, Final, Iterator, Literal

# The dispatcher's own exit statuses, none of which atlas uses (it ends with 0
# or 2); docs/remote-target.md lists what each one covers. A question's or a
# probe's own status is passed through. The build host's client
# (scripts/ask.py) ends with 6 and 7 of its own, and takes 8 for a dispatcher
# older than itself, which answered 8 for a tree it did not hold: so
# TREE_ABSENT is 9.
REFUSED = 3
CANNOT_RUN = 4
COPY_FAILED = 5
TREE_ABSENT = 9

STATUSES = {REFUSED: "REFUSED", CANNOT_RUN: "CANNOT_RUN", COPY_FAILED: "COPY_FAILED", TREE_ABSENT: "TREE_ABSENT"}
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
BUILDING = ".building-"
PROBES = os.path.join("scripts", "probes")

COULD_NOT_START = "{}: the copy could not start ({}); the current tree is as it was."
AS_IT_WAS = "the current tree is as it was."

MAY = (
    "  This key may only run the setup, copy a tree (copy <tree>), ask atlas a question (ask <tree> <word>)\n"
    "  and run a probe script (probe <tree> <name> [<word>]), <tree> being tree- and the sha256 of its manifest."
)

# A probe script's name: a bare file name in the tree's scripts/probes/. The
# build host's client (scripts/ask.py) checks a name against this same pattern.
PROBE_NAME = re.compile(r"\w+\.py", re.ASCII)
# A tree's id, the name of its directory under trees/: tree- and the sha256 of
# its manifest, which the copy checks. A request names it to run from exactly
# that tree, so an answer is the answer of the content the id names.
TREE_PREFIX = "tree-"
TREE_ID = re.compile(re.escape(TREE_PREFIX) + r"[0-9a-f]{64}", re.ASCII)
SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_WORD = re.compile(r"[A-Za-z0-9_-]*={0,2}")
_FLOOR = re.compile(r"(\d+)\.(\d+)", re.ASCII)

LACKS = "emu-atlas-dev: lacks"
"""The start of the copy's first line on stdout: the hashes the target lacks follow it, each after a blank."""

MANIFEST_LIMIT = 4 * 1024 * 1024
"""The longest manifest line a copy reads, its newline included."""

_CHUNK = 1024 * 1024
FILE_MODE = 0o600
EXECUTABLE_MODE = 0o700
_CHECKED_HASH_FLAGS = 0b11
"""A pyc's flags word for a hash-based file whose source is checked at import (PEP 552)."""

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


def floor_of(manifest: object) -> tuple[int, int] | None:
    """The Python floor a parsed manifest states as ``requires_python``, ``X.Y``; ``None`` where it states none."""
    floor = manifest.get("requires_python") if isinstance(manifest, dict) else None
    match = _FLOOR.fullmatch(floor) if isinstance(floor, str) else None
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def read_floor(tree: str) -> tuple[tuple[int, int] | None, str]:
    """The Python floor *tree*'s manifest states, or ``None`` and why it states none readably."""
    try:
        with open(os.path.join(tree, MANIFEST), encoding="utf-8") as handle:
            manifest = json.load(handle)
    except OSError as error:
        return None, reason(error)
    except ValueError as error:
        return None, "no JSON: {}".format(error)
    floor = floor_of(manifest)
    if floor is None:
        return None, "no requires_python of the form X.Y"
    return floor, ""


def tree_floor(tree: str) -> tuple[int, int]:
    floor, why = read_floor(tree)
    if floor is None:
        raise Stop(
            CANNOT_RUN,
            "{}: the copied tree states no Python floor ({}: {}); nothing ran.\n"
            "  The tree is damaged: remove {} on this target, and the next call copies it anew.".format(
                PREFIX, MANIFEST, why, tree
            ),
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

SANDBOX_MISSING: Final = "missing"
SANDBOX_NOT_EXECUTABLE: Final = "not executable"
SANDBOX_DID_NOT_START: Final = "did not start"
SANDBOX_FAILED: Final = "failed"


def sandbox_failure(bwrap: str, python: str) -> tuple[SandboxFailure, str] | None:
    """How *bwrap* fails to start the sandbox here with *python* in it, and why; ``None`` where it can.

    The one trial both this dispatcher and the setup's install script run. The
    kind, not the wording of the reason, decides the repair (:func:`sandbox_repair`).
    """
    if not os.path.lexists(bwrap):
        return SANDBOX_MISSING, "there is none"
    if not os.access(bwrap, os.X_OK):
        return SANDBOX_NOT_EXECUTABLE, "it is not executable"
    trial = sandboxed(bwrap, [python, "-I", "-S", "-c", "pass"])
    try:
        result = subprocess.run(trial, stdin=subprocess.DEVNULL, capture_output=True, check=False)
    except OSError as error:
        return SANDBOX_DID_NOT_START, "it did not start: {}".format(reason(error))
    if result.returncode != 0:
        said = result.stderr.decode("utf-8", "replace").strip() or result.returncode
        return SANDBOX_FAILED, "it said: {}".format(said)
    return None


def sandbox_repair(kind: SandboxFailure) -> str:
    """What makes a ``bwrap`` that failed the trial as *kind* start the sandbox; the setup's install says the same.

    A missing or not executable one is installed. Where one is there but did
    not start or started no sandbox, a kernel that forbids unprivileged user
    namespaces is named beside a reinstall, the reason quoting what ``bwrap``
    said where it said anything.
    """
    if kind in (SANDBOX_MISSING, SANDBOX_NOT_EXECUTABLE):
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


def tree_absent() -> Stop:
    """The answer for a tree this target does not hold: :data:`TREE_ABSENT` alone, with nothing on stderr.

    It is no failure but the client's cue to copy the tree and ask again, so it
    adds nothing to the stderr the client passes on.
    """
    return Stop(TREE_ABSENT, "")


def _lock_tree(root: str, *, tree_id: str) -> tuple[str, int]:
    """The tree *tree_id*, and a descriptor of its directory holding a shared lock on it.

    *tree_id* is a :data:`TREE_ID`, checked here as well as in :func:`dispatch`:
    nothing else may become a path under ``trees/``. A tree this target does
    not hold — never copied, or removed by a copy before this request or
    between opening it and taking the lock — is answered with
    :data:`TREE_ABSENT`; no other tree stands in for it. Any other error ends
    the request.
    """
    if not TREE_ID.fullmatch(tree_id):
        raise ValueError("{!r} is no tree id".format(tree_id))
    tree = os.path.join(root, TREES, tree_id)
    try:
        descriptor = os.open(tree, os.O_RDONLY | os.O_DIRECTORY)
    except (FileNotFoundError, NotADirectoryError):
        raise tree_absent() from None
    if not _locked_if_same(descriptor, tree):
        raise tree_absent()
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


class Entry:
    """One file a manifest lists: its path in the tree, its sha256, and whether it is executable."""

    __slots__ = ("path", "sha256", "executable")

    def __init__(self, path: str, sha256: str, executable: bool) -> None:
        self.path = path
        self.sha256 = sha256
        self.executable = executable

    @property
    def mode(self) -> int:
        """The mode the file has in a tree here, whatever it had where it was sent from."""
        return EXECUTABLE_MODE if self.executable else FILE_MODE


class Broken(Exception):
    """A copy that did not arrive whole, and why."""


def path_problem(path: object) -> str | None:
    """Why *path* cannot be a file of a tree; ``None`` where it can.

    A relative POSIX path of plain names: none of them empty, ``.``, ``..`` or
    ``__pycache__``, no ``.pyc`` file and not the manifest, so that a file lands
    inside the new tree and never where its bytecode or its manifest goes.
    """
    if not isinstance(path, str) or not path or "\x00" in path:
        return "{!r} is no path".format(path)
    names = path.split("/")
    if path == MANIFEST or names[-1].endswith(".pyc") or any(name in ("", ".", "..", "__pycache__") for name in names):
        return "{!r} is no path a file of a tree may have".format(path)
    return None


def entry_of(item: object) -> Entry:
    """The file one item of a manifest's ``files`` names; a ``ValueError`` saying what it lacks."""
    if not isinstance(item, dict):
        raise ValueError("a file that is no JSON object")
    path, digest, executable = item.get("path"), item.get("sha256"), item.get("executable")
    problem = path_problem(path)
    if problem is not None:
        raise ValueError(problem)
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        raise ValueError("{}: no sha256 in lowercase hex".format(path))
    if not isinstance(executable, bool):
        raise ValueError("{}: executable is neither true nor false".format(path))
    return Entry(str(path), digest, executable)


def parse_manifest(data: bytes) -> list[Entry]:
    """The files the manifest *data* lists, each path once; a ``ValueError`` saying what makes it none.

    A manifest is a JSON object: ``requires_python``, ``X.Y``, and ``files``, a
    list of ``{"path", "sha256", "executable"}`` — every file of the tree, its
    directories implied.
    """
    try:
        manifest = json.loads(data.decode("utf-8"))
    except ValueError as error:  # a UnicodeDecodeError is one as well
        raise ValueError("no JSON: {}".format(error)) from None
    if not isinstance(manifest, dict) or floor_of(manifest) is None:
        raise ValueError("no requires_python of the form X.Y")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("no list of files")
    entries = [entry_of(item) for item in files]
    if len({entry.path for entry in entries}) != len(entries):
        raise ValueError("a path listed twice")
    return entries


def copy_refused(why: str) -> Stop:
    return Stop(COPY_FAILED, "{}: the copy was refused: {}; {}".format(PREFIX, why, AS_IT_WAS))


def receive_manifest(stream: IO[bytes], tree_id: str) -> tuple[bytes, list[Entry]]:
    """The manifest line at the head of *stream*, without its newline, and its files: the tree *tree_id*'s, or a
    refusal."""
    line = stream.readline(MANIFEST_LIMIT)
    if not line.endswith(b"\n"):
        why = "it is longer than {} bytes".format(MANIFEST_LIMIT) if len(line) >= MANIFEST_LIMIT else "it broke off"
        raise copy_refused("its manifest did not arrive whole: {}".format(why))
    data = line[:-1]
    if TREE_PREFIX + hashlib.sha256(data).hexdigest() != tree_id:
        raise copy_refused("its manifest is not the one {} names: their sha256 differs".format(tree_id))
    try:
        return data, parse_manifest(data)
    except ValueError as error:
        raise copy_refused("its manifest lists no tree: {}".format(error)) from None


class Held:
    """The current tree's files whose content is what its manifest says: a path for each sha256, and each path's."""

    def __init__(self, tree: str | None = None) -> None:
        self.tree = tree
        self.by_hash: dict[str, str] = {}
        self.by_path: dict[str, str] = {}


def file_digest(path: str) -> str:
    """The sha256 of the file at *path*, in hex; a link is not followed."""
    digest = hashlib.sha256()
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def current_files(root: str) -> Held:
    """What the current tree holds that a new one can be built from, each file read and hashed, not taken on trust.

    A file that is not what its manifest says, a manifest this dispatcher
    cannot read, or no current tree at all leaves those files out, so the copy
    asks for them.
    """
    try:
        tree = os.path.join(root, os.readlink(os.path.join(root, CURRENT)))
        with open(os.path.join(tree, MANIFEST), "rb") as handle:
            entries = parse_manifest(handle.read())
    except (OSError, ValueError):
        return Held()
    held = Held(tree)
    for entry in entries:
        path = os.path.join(tree, entry.path)
        try:
            digest = file_digest(path)
        except OSError:
            continue
        if digest == entry.sha256:
            held.by_hash.setdefault(digest, path)
            held.by_path[entry.path] = digest
    return held


def created(path: str, mode: int) -> IO[bytes]:
    """A new file at *path* with *mode*, whatever the umask, open for writing: nothing there is replaced or followed."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    os.fchmod(descriptor, mode)
    return os.fdopen(descriptor, "wb")


def copy_file(source: str, destination: str, mode: int) -> None:
    """A copy of the file *source* at *destination*, with *mode*: its own bytes, never a link to the source."""
    with os.fdopen(os.open(source, os.O_RDONLY | os.O_NOFOLLOW), "rb") as reading, created(destination, mode) as writing:
        shutil.copyfileobj(reading, writing, _CHUNK)


def answer_lacking(out: IO[str], lacking: list[str]) -> None:
    """The copy's first line on *out*: :data:`LACKS` and each sha256 the target lacks, then the client sends those."""
    out.write(LACKS + "".join(" " + digest for digest in lacking) + "\n")
    out.flush()


def receive(stream: IO[bytes], staging: str, first: dict[str, Entry]) -> dict[str, str]:
    """The files *first* names by sha256, read from the gzip tar *stream* into *staging*; where each was written.

    Each member is named by the sha256 of its content and lands at the first
    path the manifest gives that content. A member that is no file, one that
    was not asked for or came twice, and a stream that ends before every file
    came, are a copy that did not arrive whole.
    """
    written: dict[str, str] = {}
    with tarfile.open(fileobj=stream, mode="r|gz") as archive:
        for member in archive:
            entry = first.get(member.name)
            if entry is None or member.name in written:
                raise Broken("{!r} is no file the copy lacked".format(member.name))
            data = archive.extractfile(member) if member.isreg() else None
            if data is None:
                raise Broken("{} is no file".format(member.name))
            path = os.path.join(staging, entry.path)
            with created(path, entry.mode) as handle:
                shutil.copyfileobj(data, handle, _CHUNK)
            written[member.name] = path
    if len(written) != len(first):
        raise Broken("the stream ended with {} of the {} files lacked still to come".format(
            len(first) - len(written), len(first)
        ))
    return written


def build(staging: str, entries: list[Entry], held: Held, stream: IO[bytes], lacking: set[str]) -> None:
    """Every file of *entries* in *staging*: those *lacking* read from *stream*, the others copied from *held*."""
    for entry in entries:
        os.makedirs(os.path.dirname(os.path.join(staging, entry.path)), exist_ok=True)
    first: dict[str, Entry] = {}
    for entry in entries:
        if entry.sha256 in lacking:
            first.setdefault(entry.sha256, entry)
    written = receive(stream, staging, first) if first else {}
    for entry in entries:
        path = os.path.join(staging, entry.path)
        if written.get(entry.sha256) != path:
            copy_file(written.get(entry.sha256) or held.by_hash[entry.sha256], path, entry.mode)


def verify(staging: str, entries: list[Entry]) -> None:
    """Refuse the tree in *staging* unless every file is a plain file with its mode and the content its sha256 names."""
    for entry in entries:
        path = os.path.join(staging, entry.path)
        found = os.lstat(path)
        if not stat.S_ISREG(found.st_mode) or stat.S_IMODE(found.st_mode) != entry.mode:
            raise Broken("{} is no file of mode {:o}".format(entry.path, entry.mode))
        if file_digest(path) != entry.sha256:
            raise Broken("{} is not the file its manifest names: its sha256 differs".format(entry.path))


def checked_against(compiled: bytes, source: bytes) -> bool:
    """Whether *compiled* is this interpreter's checked-hash bytecode of *source* (PEP 552)."""
    return (
        compiled[:4] == importlib.util.MAGIC_NUMBER
        and int.from_bytes(compiled[4:8], "little") == _CHECKED_HASH_FLAGS
        and compiled[8:16] == importlib.util.source_hash(source)
    )


def reuse_compiled(staging: str, entry: Entry, held: Held) -> bool:
    """Bring the current tree's bytecode of *entry* over to *staging* where its source is unchanged; whether it came.

    It comes only where it is this interpreter's checked-hash bytecode of the
    very source: anything else, a missing file among it, is compiled anew.
    """
    if held.tree is None or held.by_path.get(entry.path) != entry.sha256:
        return False
    source = os.path.join(staging, entry.path)
    try:
        with open(importlib.util.cache_from_source(os.path.join(held.tree, entry.path)), "rb") as handle:
            compiled = handle.read()
        with open(source, "rb") as handle:
            source_bytes = handle.read()
    except OSError:
        return False
    if not checked_against(compiled, source_bytes):
        return False
    target = importlib.util.cache_from_source(source)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with created(target, FILE_MODE) as handle:
        handle.write(compiled)
    return True


def compile_tree(staging: str, final: str, entries: list[Entry], held: Held) -> None:
    """Checked-hash bytecode for every ``.py`` file of the tree in *staging*, which becomes *final*: compiled, never run.

    Each goes into its directory's ``__pycache__`` under this interpreter's own
    tag — the interpreter the sandbox runs, which reads the files there and
    checks each against its source. The bytecode of a source the current tree
    holds unchanged comes over from it; every other source is compiled. A
    source that does not compile gets none: a question compiles it in memory
    and fails on it as it would without, and so does one that finds bytecode
    stale or missing.
    """
    if getattr(sys.implementation, "cache_tag", None) is None:  # an interpreter that keeps no bytecode files
        return
    for entry in entries:
        if not entry.path.endswith(".py") or reuse_compiled(staging, entry, held):
            continue
        source = os.path.join(staging, entry.path)
        try:
            py_compile.compile(
                source,
                cfile=importlib.util.cache_from_source(source),
                dfile=os.path.join(final, entry.path),
                doraise=True,
                invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
            )
        except py_compile.PyCompileError:
            continue


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


def left_note(left: str | None) -> str:
    return " It " + left + "." if left else ""


def build_beside(root: str, final: str, manifest: bytes, entries: list[Entry], stream: IO[bytes], out: IO[str]) -> str:
    """Build the tree *entries* list beside the current one; the directory it was built in, ready to be renamed to
    *final*.

    It answers on *out* with the sha256 of every file the current tree lacks,
    reads those from *stream*, copies the rest from the current tree, writes
    *manifest*, checks every file and compiles the tree. An error or an
    interrupt removes what it built and leaves the current tree as it was; a
    copy killed outright leaves its directory for the next copy to remove.
    """
    try:
        staging = tempfile.mkdtemp(prefix=BUILDING, dir=os.path.dirname(final))
    except OSError as error:
        raise Stop(COPY_FAILED, COULD_NOT_START.format(PREFIX, error)) from None
    try:
        held = current_files(root)
        lacking = sorted({entry.sha256 for entry in entries} - set(held.by_hash))
        answer_lacking(out, lacking)
        build(staging, entries, held, stream, set(lacking))
        with created(os.path.join(staging, MANIFEST), FILE_MODE) as handle:
            handle.write(manifest)
        verify(staging, entries)
        compile_tree(staging, final, entries, held)
    except BaseException as error:
        left = discard(staging)
        if not isinstance(error, Exception):
            raise
        raise Stop(
            COPY_FAILED,
            "{}: the copy did not arrive whole ({}); {}{}".format(PREFIX, error, AS_IT_WAS, left_note(left)),
        ) from None
    return staging


def take(root: str, tree_id: str, manifest: bytes, entries: list[Entry], stream: IO[bytes], out: IO[str]) -> str:
    """Make the tree *tree_id* current, built beside the current one where this target does not hold it yet; the tree.

    A tree it holds already is made current as it is, and the client is told
    it lacks nothing. A new tree comes into place with one rename once it is
    whole and compiled, and becomes current with another; one that cannot be
    made current is removed again, unless a question already runs from it.
    """
    final = os.path.join(root, TREES, tree_id)
    built = not (os.path.isdir(final) and not os.path.islink(final))
    if built:
        staging = build_beside(root, final, manifest, entries, stream, out)
        try:
            os.rename(staging, final)
        except OSError as error:
            raise Stop(
                COPY_FAILED,
                "{}: the copy arrived whole but could not be put in place ({}); {}{}".format(
                    PREFIX, error, AS_IT_WAS, left_note(discard(staging))
                ),
            ) from None
    else:
        answer_lacking(out, [])
    try:
        make_current(root, final)
    except OSError as error:
        left = remove_unless_held(final) if built else None
        raise Stop(
            COPY_FAILED,
            "{}: the copy arrived whole but could not be made current ({}); {}{}".format(
                PREFIX, error, AS_IT_WAS, left_note(left)
            ),
        ) from None
    return final


def copy(root: str, tree_id: str, stream: IO[bytes], out: IO[str]) -> int:
    """Take the tree *tree_id* from *stream* and answer its id on *out*; a copy that does not arrive whole leaves the
    current tree as it was.

    The exchange, in one connection: the client sends the manifest on one
    line; this answers with the line :func:`answer_lacking` writes; where that
    names any, the client sends those files as a gzip tar and closes its end.
    The tree's id, the last line on *out*, is what a question names to run from
    this tree and no other (:func:`held_tree`).
    """
    manifest, entries = receive_manifest(stream, tree_id)
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
        tree = take(root, tree_id, manifest, entries, stream, out)
        notes = remove_others(trees, tree)
    for note in notes:
        sys.stderr.write("{}: the copy is current, but {}.\n".format(PREFIX, note))
    out.write(tree_id + "\n")
    return 0


def dispatch(request: str, root: str) -> int:
    words = request.split()
    if words == [sys.executable, "-I", "-"]:
        os.chdir(os.path.expanduser("~"))
        os.execv(sys.executable, [sys.executable, "-I", "-"])
    if len(words) == 2 and words[0] == "copy" and TREE_ID.fullmatch(words[1]):
        return copy(root, words[1], sys.stdin.buffer, sys.stdout)
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
        if stop.message:
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
