#!/usr/bin/env python3
"""Ask atlas one question, or run a probe script, here or on a remote target.

    mise run ask [--target <name>] -- <question>
    mise run probe [--target <name>] <script> [arguments]

<question> is what ``python -m atlas`` takes, word for word; <script> is a file
name in scripts/probes/. Without --target both run here, on this interpreter
with this tree's atlas, the way the dispatcher runs them on a target. With
--target <name>, a target set up with ``mise run setup:remote-target``, this
tree's atlas/ and scripts/probes/ are copied there as they stand and the
question or the script runs there, in the dispatcher's sandbox; its answer on
stdout, its stderr and its exit status come back here unchanged. A target that
holds this tree already, named by the digest of its manifest, is asked in one
connection; one that does not gets a copy of the files it lacks first.

--target is read only as the first argument, so a question's own --target
(``save-portability --target <label>``) stays the question's. What is copied,
this client's own exit statuses and what each failure says:
docs/remote-target.md, "Asking a target".
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import re
import select
import signal
import stat
import subprocess
import sys
import tarfile
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final, Literal, NamedTuple, assert_never

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Where the target's entry lives, how ssh's stderr reads and what the
# dispatcher answers each have one home, which this client reads.
from scripts import dev_dispatch  # noqa: E402
from scripts import setup_remote_target as setup  # noqa: E402
from scripts.dev_target_install import DISPATCHER_NAME  # noqa: E402

PREFIX = setup.PREFIX

TREE = ROOT
"""The tree a question is asked from: its atlas/ here, and the copy's contents there."""

COPIED = ("atlas", dev_dispatch.PROBES)
"""What a copy carries, relative to :data:`TREE`."""

_COPIED_ROOTS = tuple(Path(top).as_posix() for top in COPIED)

SKIPPED_DIRECTORY = "__pycache__"
SKIPPED_SUFFIX = ".pyc"

# This client's own exit statuses. A usage error ends with 2, as atlas's own
# does, before anything is contacted; the client's other failures end with
# statuses neither atlas (0, 2) nor the dispatcher (dev_dispatch.STATUSES)
# ends with. A question's or a probe's own status is passed through.
# docs/remote-target.md lists what each one covers.
USAGE = 2
NOT_ASKED = 6
BROKE_OFF = 7

# How long ssh waits for a connection before it gives up on an address: a
# device that is asleep answers no connection at all.
CONNECT_TIMEOUT = 15

# How much may arrive on stdout ahead of the run's marker before the client
# stops waiting for it: the dispatcher writes nothing there before it.
AHEAD_LIMIT = 64 * 1024

Mode = Literal["ask", "probe"]
"""Which task called: ``mise run ask`` or ``mise run probe``."""

USAGE_LINES = (
    "usage: mise run ask [--target <name>] -- <question>\n"
    "       mise run probe [--target <name>] <script> [arguments]"
)

NO_SSH = f"{PREFIX}: asking a target needs ssh, and this machine has none on its PATH."
OLDER_DISPATCHER = (
    "The dispatcher there is older than this client; run the setup again, which installs this checkout's dispatcher:"
)
OTHER_DISPATCHER = (
    "The dispatcher there does not take this client's requests; run the setup again, which installs this checkout's"
    " dispatcher:"
)
SHELL_PRINTS = (
    "  The shell there prints from its startup files for a command run without a terminal, and that would land in"
    " front of every answer. Make them silent for non-interactive shells (in ~/.bashrc, say, return at the top unless"
    " $- holds i)."
)
COPY_CUT_OFF = "{target}'s current tree is this copy whole, or the one before it. Nothing was asked."
COPY_WENT_THROUGH = "The copy went through; nothing was asked."

OLDER_TREE_GONE = 8
"""What a dispatcher older than this client ends a question with for a tree it does not hold — every first question
this client puts to one, since that dispatcher names its trees otherwise. The dispatcher itself never ends with 8."""

COPY_GZIP_LEVEL = 1
"""How hard the files a copy sends are compressed: gzip's fastest level, which takes most of the size off at little
cost."""

# What the target's interpreter says for a dispatcher that is not there.
_NO_DISPATCHER = re.compile(f"can't open file '[^']*/{re.escape(DISPATCHER_NAME)}'")
_TREE_AT_THE_END = re.compile(f"({dev_dispatch.TREE_ID.pattern})\n\\Z", re.ASCII)
_MARKER = (dev_dispatch.RUN_MARKER + "\n").encode("ascii")
_LACKS = dev_dispatch.LACKS.encode("ascii")

PhaseName = Literal[
    "before anything was contacted",
    "while running here",
    "while copying to the target",
    "after the copy went through",
    "while asking the target",
]
"""Where a call is, for what a crash or an interrupt of this client says about it."""

BEFORE_CONTACT: Final = "before anything was contacted"
HERE: Final = "while running here"
COPYING: Final = "while copying to the target"
AFTER_COPY: Final = "after the copy went through"
ASKING: Final = "while asking the target"

class PhaseEnd(NamedTuple):
    """How a call that ends in a phase ends: ``{target}`` in a text stands for the target's name."""

    status: int
    """The status a crash ends the call with."""
    after_a_crash: str
    after_an_interrupt: str


PHASES: dict[PhaseName, PhaseEnd] = {
    BEFORE_CONTACT: PhaseEnd(NOT_ASKED, "Nothing was contacted.", "Nothing was contacted."),
    HERE: PhaseEnd(
        NOT_ASKED,
        "No target was contacted; whatever ran here printed above, and its status is lost.",
        "The run here was interrupted as well.",
    ),
    COPYING: PhaseEnd(BROKE_OFF, COPY_CUT_OFF, COPY_CUT_OFF),
    AFTER_COPY: PhaseEnd(NOT_ASKED, COPY_WENT_THROUGH, COPY_WENT_THROUGH),
    ASKING: PhaseEnd(
        BROKE_OFF,
        "What the run printed is above; its status is lost.",
        "The run on {target} ends with its connection.",
    ),
}


class Refusal(Exception):
    """The call ends here, with *message* on stderr and *status*."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Request:
    """One call: what it runs, where, and its words — the question, or the script and its arguments."""

    mode: Mode
    target: str | None
    words: tuple[str, ...]


class Phase:
    """Where a call is, and with which target, for what a crash or an interrupt says."""

    def __init__(self) -> None:
        self.now: PhaseName = BEFORE_CONTACT
        self.target = "the target"

    @property
    def end(self) -> PhaseEnd:
        return PHASES[self.now]


def usage(message: str) -> Refusal:
    return Refusal(USAGE, f"{PREFIX}: {message}\n{USAGE_LINES}")


def split_target(argv: list[str]) -> tuple[str | None, list[str]]:
    """The leading ``--target <name>`` (or ``--target=<name>``) of *argv*, if any, and the words after it.

    A ``--`` right after it, or leading where there is none, is dropped: mise
    drops the first ``--`` it is given, so one that arrives here was meant to
    separate the words. Every later ``--`` is the question's.
    """
    target = None
    rest = list(argv)
    if rest[:1] == ["--target"]:
        target = rest[1] if len(rest) > 1 else ""
        rest = rest[2:]
    elif rest[:1] and rest[0].startswith("--target="):
        target = rest[0].partition("=")[2]
        rest = rest[1:]
    if rest[:1] == ["--"]:
        rest = rest[1:]
    return target, rest


def probes_directory() -> Path:
    return TREE / dev_dispatch.PROBES


def check_probe(script: str) -> None:
    """Refuse a probe script that is no bare file name (the dispatcher's own rule) or that scripts/probes/ lacks."""
    if not dev_dispatch.PROBE_NAME.fullmatch(script):
        raise usage(
            f"a probe script is a file name of scripts/probes/, letters, digits and '_' then .py — not {script!r}."
        )
    if not (probes_directory() / script).is_file():
        held = sorted(path.name for path in probes_directory().glob("*.py"))
        raise usage(f"scripts/probes/ holds no {script}; it holds: {', '.join(held) or 'nothing'}.")


def parse(mode: Mode, argv: list[str]) -> Request:
    """The call *argv* makes of the task *mode*; a usage error before anything is contacted."""
    target, words = split_target(argv)
    if target is not None:
        try:
            setup.check_name(target)
        except setup.SetupError as error:
            raise usage(str(error).removeprefix(f"{PREFIX}: ")) from None
    if not words:
        raise usage("no question to ask." if mode == "ask" else "no probe script to run.")
    if mode == "probe":
        check_probe(words[0])
    return Request(mode, target, tuple(words))


def run_here(request: Request) -> int:
    """The question or the probe script run here, through the dispatcher's own bootstrap on this interpreter.

    Run the way the dispatcher runs it on a target — :data:`TREE` on
    ``sys.path`` behind the standard library, no site-packages, a probe's
    crash ending with 2 — so a probe meets the same imports and crash handling
    here as there; only the sandbox is missing here.
    """
    if request.mode == "ask":
        kind, target, arguments = "module", "atlas", list(request.words)
    else:
        kind, target, arguments = "path", str(probes_directory() / request.words[0]), list(request.words[1:])
    command = dev_dispatch.bootstrapped(sys.executable, str(TREE), kind, target, arguments)
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        ran = subprocess.run(command, check=False)
    except OSError as error:
        raise Refusal(
            NOT_ASKED, f"{PREFIX}: {sys.executable} could not be started: {setup.reason(error)}. Nothing ran."
        ) from error
    return dev_dispatch.status_of(ran.returncode)


def copy_members(tree: Path) -> tuple[list[Path], list[Path]]:
    """What a copy of *tree* carries, in a stable order, and what it leaves out for being no file or directory.

    Every file and directory under :data:`COPIED`, parents before what they
    hold, without ``__pycache__`` and ``*.pyc``; links are not followed.
    """
    members: list[Path] = []
    left_out: list[Path] = []
    for top in COPIED:
        if (tree / top).exists() or (tree / top).is_symlink():
            _visit(tree / top, members, left_out)
    return members, left_out


def _visit(path: Path, members: list[Path], left_out: list[Path]) -> None:
    """*path* and what it holds into *members*, or into *left_out* where it is no file or directory."""
    if path.name == SKIPPED_DIRECTORY or path.name.endswith(SKIPPED_SUFFIX):
        return
    if path.is_symlink() or not (path.is_dir() or path.is_file()):
        left_out.append(path)
        return
    members.append(path)
    if path.is_dir():
        for child in sorted(path.iterdir(), key=lambda child: child.name):
            _visit(child, members, left_out)


def member_name(tree: Path, path: Path) -> str:
    """*path*'s name in a copy of *tree*: relative to it, inside one of :data:`COPIED`, with no ``..`` in it.

    The walk of :func:`copy_members` follows no link, so every name it gives
    holds already; this says so where the archive is written.
    """
    name = path.relative_to(tree).as_posix()
    inside = any(name == root or name.startswith(f"{root}/") for root in _COPIED_ROOTS)
    if not inside or ".." in name.split("/"):
        raise ValueError(f"{name!r} lies outside what a copy carries")
    return name


@dataclass(frozen=True)
class Copy:
    """What a copy of a tree carries: its manifest as sent, and the content of each of its files by its sha256."""

    manifest: bytes
    contents: dict[str, bytes]

    @property
    def tree_id(self) -> str:
        """The tree's id: ``tree-`` and the sha256 of its manifest, so the same content has the same id anywhere."""
        return dev_dispatch.TREE_PREFIX + hashlib.sha256(self.manifest).hexdigest()

    def archive(self, hashes: list[str]) -> bytes:
        """The gzip tar of the files *hashes* names, each member named by its sha256: what a copy sends of them."""
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz", compresslevel=COPY_GZIP_LEVEL) as archive:
            for digest in hashes:
                data = self.contents[digest]
                info = tarfile.TarInfo(digest)
                info.size = len(data)
                info.mode = 0o600
                archive.addfile(info, io.BytesIO(data))  # NOSONAR S6096: writes, never unpacks; names are hashes
        return buffer.getvalue()


def build_copy(tree: Path, floor: str) -> Copy:
    """The copy of *tree*: the manifest of :func:`copy_members`'s files, and their contents.

    The manifest is one line of compact JSON with sorted keys, so the same
    files give the same bytes and the same id: ``requires_python``, *floor*,
    and ``files``, each file's path, sha256 and whether it is executable,
    sorted by path. Directories are implied by the files they hold.
    """
    members, left_out = copy_members(tree)
    for path in left_out:
        say(f"{PREFIX}: not copied, being no file or directory: {path.relative_to(tree)}")
    files: list[dict[str, object]] = []
    contents: dict[str, bytes] = {}
    for path in members:
        name = member_name(tree, path)
        if not path.is_file():
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        contents[digest] = data
        executable = bool(path.stat().st_mode & stat.S_IXUSR)
        files.append({"path": name, "sha256": digest, "executable": executable})
    files.sort(key=lambda file: str(file["path"]))
    manifest = json.dumps({"files": files, "requires_python": floor}, sort_keys=True, separators=(",", ":"))
    return Copy(manifest.encode("ascii"), contents)


def word(arguments: tuple[str, ...]) -> str:
    """*arguments* as the dispatcher reads them: a JSON array of strings, UTF-8, base64url without padding."""
    return base64.urlsafe_b64encode(json.dumps(list(arguments)).encode("utf-8")).decode("ascii").rstrip("=")


def remote_request(request: Request, tree: str) -> list[str]:
    """The dispatcher's request for *request*, run from *tree*: ``ask <tree> <word>``, or ``probe <tree> <name>`` with
    a word for its arguments."""
    if request.mode == "ask":
        return ["ask", tree, word(request.words)]
    script, arguments = request.words[0], request.words[1:]
    return ["probe", tree, script, *([word(arguments)] if arguments else [])]


def setup_line(name: str, destination: setup.Destination | None, address: str = "") -> str:
    """The setup's command line for *name*: with *address*, or the entry's own, where no hop is in the way."""
    if destination is None or destination.via:
        where = "<destination>"
    else:
        where = f"{destination.user}@{address or destination.hostname}"
    return f"    mise run setup:remote-target {name} {where}"


@dataclass(frozen=True)
class Target:
    """A target as this machine's own SSH config records it: its entry, and the config that holds it."""

    destination: setup.Destination
    config: Path

    @property
    def name(self) -> str:
        """The target's name: the entry's, read back (:func:`setup.read_entry`)."""
        return self.destination.text

    @property
    def known_hosts(self) -> Path:
        return self.config.with_name(setup.KNOWN_HOSTS_NAME)

    @property
    def key(self) -> Path:
        return self.config.with_name(setup.KEY_NAME)

    def ssh(self, words: list[str]) -> list[str]:
        """``ssh`` reading the entry from this tool's config alone, so with the restricted key alone, asking *words*."""
        return [
            "ssh",
            "-F",
            str(self.config),
            "-T",
            "-o",
            f"ConnectTimeout={CONNECT_TIMEOUT}",
            "--",
            self.name,
            *words,
        ]


def find_target(name: str) -> Target:
    """The target *name* as this machine records it, or why it cannot be asked, before anything is contacted."""
    config = setup.config_dir() / setup.SSH_CONFIG_NAME
    text = setup.read_text(config) if config.exists() else ""
    try:
        destination = setup.read_entry(text, name)
    except setup.SetupError as error:
        raise Refusal(
            NOT_ASKED,
            f"{error.args[0].removesuffix('.')} in {config}.\n  Run the setup again:\n{setup_line(name, None)}\n"
            "  Nothing was contacted.",
        ) from None
    if destination is None:
        held = f"{config} has no entry for it" if config.exists() else f"there is no {config}"
        raise Refusal(
            NOT_ASKED,
            f"{PREFIX}: no target named {name} is set up here — {held}.\n"
            f"  Set it up first:\n{setup_line(name, None)}\n"
            "  Nothing was contacted.",
        )
    target = Target(destination, config)
    if not target.key.exists():
        raise Refusal(
            NOT_ASKED,
            f"{PREFIX}: this machine's key for targets, {target.key}, is not there, so {name} could not take it.\n"
            f"  Run the setup again, which makes a new one and puts it on {name}:\n{setup_line(name, destination)}\n"
            "  Nothing was contacted.",
        )
    return target


def _key_refused(target: Target) -> str:
    return (
        f"{PREFIX}: {target.name} refused this machine's key. It takes the key only from the address it saw this"
        " machine at when it was set up, and that may have changed — or the key's line there was removed.\n"
        f"  Run the setup again:\n{setup_line(target.name, target.destination)}"
    )


def _target_message(failure: setup.SshFailure, target: Target) -> str | None:
    """This client's message for a failure ``ssh`` placed on the target itself; ``None`` for one the setup words."""
    name, destination, kind = target.name, target.destination, failure.kind
    if kind == setup.KIND_UNRESOLVED:
        return (
            f"{PREFIX}: this machine cannot resolve {destination.hostname}, where {name}'s entry says it is.\n"
            f"  Run the setup with the target's address, or a name the network resolves:\n"
            f"{setup_line(name, destination, '<address>')}"
        )
    if kind == setup.KIND_NO_SERVER:
        return (
            f"{PREFIX}: no SSH server answers at {destination.hostname} port {destination.port}, where {name}'s entry"
            f" says it is.\n  {name} may be asleep: wake it and ask again. Or it has a new address: run the setup"
            f" with that one:\n{setup_line(name, destination, '<new address>')}"
        )
    if kind == setup.KIND_HOST_KEY_NOT_ACCEPTED:
        return (
            f"{PREFIX}: this machine holds no record of {name}'s host key, so no connection was made.\n"
            f"  Run the setup again, which shows the fingerprint and records it:\n{setup_line(name, destination)}"
        )
    if kind == setup.KIND_NO_LOGIN:
        return _key_refused(target)
    if kind in (setup.KIND_HOST_KEY_CHANGED, setup.KIND_UNKNOWN):
        return None
    assert_never(kind)


def not_reached(stderr: str, target: Target) -> str | None:
    """What to say for an ``ssh`` that ended before the login, from the setup's one reading of it; ``None`` otherwise.

    A failure on the target itself is worded with this client's repair; one on
    a hop, and a host key that is not the recorded one, as the setup words it.
    """
    failure = setup.ssh_failure(stderr, name=target.name, destination=target.destination)
    if failure.where == "target":
        own = _target_message(failure, target)
        if own is not None:
            return own
    worded = setup.failure_text(
        failure, name=target.name, destination=target.destination, known_hosts=(target.known_hosts,)
    )
    if worded is not None:
        return worded
    if failure.kind == setup.KIND_NO_LOGIN:
        return _key_refused(target)
    return None


def ssh_not_started(error: OSError) -> str:
    if isinstance(error, FileNotFoundError):
        return NO_SSH
    return f"{PREFIX}: ssh could not be started: {setup.reason(error)}."


def write_all(descriptor: int, data: bytes) -> None:
    """All of *data* on *descriptor*: a write that took part of it, or found a full non-blocking pipe, goes on."""
    view = memoryview(data)
    while view:
        try:
            view = view[os.write(descriptor, view) :]
        except BlockingIOError:
            select.select([], [descriptor], [])


def pass_on(data: bytes) -> None:
    """*data* on this process's stderr, byte for byte, after whatever was written there before."""
    sys.stderr.flush()
    write_all(sys.stderr.fileno(), data)


def say(message: str) -> None:
    """*message* on stderr; where stderr itself fails, nothing is left to tell, and the status still comes back."""
    try:
        print(message, file=sys.stderr, flush=True)
    except (OSError, ValueError):
        pass


def dispatcher_status(status: int, name: str) -> str:
    """The line this client adds below a status of the dispatcher's own, which ran nothing of the request's."""
    line = f"{PREFIX}: that was {name}'s dispatcher ({status}, {dev_dispatch.STATUSES[status]}), not atlas or a probe."
    if status == dev_dispatch.REFUSED:
        line += f"\n  {OTHER_DISPATCHER}\n{setup_line(name, None)}"
    return line


def shown(text: str) -> str:
    """*text* as this client quotes what arrived where nothing should: each line escaped, a blank arrival named."""
    if not text.strip():
        return f"    (only blank space: {text!r})"
    return "\n".join(f"    {line!r}" for line in text.splitlines())


def tree_named(stdout: bytes, target: Target) -> str:
    """The id of the tree the copy made, the line its dispatcher answers with on stdout and the last there.

    Anything ahead of it, glued onto it or after it is refused before a
    question is asked: it is what the target's shell prints for a command run
    without a terminal, and it would land in front of every answer. No id at
    all is what a dispatcher older than this client answers.
    """
    text = stdout.decode("utf-8", "replace")
    at_the_end = _TREE_AT_THE_END.search(text)
    if at_the_end is not None and at_the_end.start() == 0:
        return at_the_end.group(1)
    went_through = f"  {COPY_WENT_THROUGH}"
    older = f"  {OLDER_DISPATCHER}\n{setup_line(target.name, target.destination)}"
    if not text:
        raise Refusal(
            NOT_ASKED,
            f"{PREFIX}: {target.name}'s dispatcher took the copy but named no tree to ask from.\n"
            f"{older}\n{went_through}",
        )
    arrived = text if at_the_end is None else text[: at_the_end.start()]
    named_none = "" if dev_dispatch.TREE_ID.search(text) else f"\n  It named no tree either.\n{older}"
    raise Refusal(
        NOT_ASKED,
        f"{PREFIX}: {target.name} wrote this on stdout beside its dispatcher's answer to the copy:\n{shown(arrived)}\n"
        f"{SHELL_PRINTS}{named_none}\n{went_through}",
    )


class Sent(NamedTuple):
    """What came back from a copy's one connection."""

    ahead: bytes
    """What arrived on stdout ahead of the dispatcher's lacks line."""
    lacking: list[str] | None
    """The sha256 of each file the lacks line named; ``None`` where no such line came."""
    after: bytes
    """What arrived on stdout after the lacks line: the tree's id."""
    status: int
    stderr: bytes


def keep(pipe: IO[bytes], kept: bytearray) -> None:
    """Keep all of *pipe* in *kept*, passing nothing on."""
    for chunk in iter(lambda: os.read(pipe.fileno(), 65536), b""):
        kept.extend(chunk)


def send(pipe: IO[bytes], data: bytes, *, close: bool) -> threading.Thread:
    """Write *data* to *pipe* in a thread of its own, then close it where *close*: what reads the other pipes meanwhile
    never waits on the write, nor the write on them."""

    def write() -> None:
        try:
            pipe.write(data)
            pipe.flush()
        except (OSError, ValueError):
            pass  # the dispatcher stopped reading: its status says why
        finally:
            if close:
                with contextlib.suppress(OSError, ValueError):
                    pipe.close()

    thread = threading.Thread(target=write, daemon=True)
    thread.start()
    return thread


def read_lacking(pipe: IO[bytes]) -> tuple[bytes, list[str] | None, bytes]:
    """Read *pipe* through the copy's lacks line: what came ahead of it, the sha256 it names, and what came after it.

    No line at all, ``None``, where the stream ends first or more than
    :data:`AHEAD_LIMIT` arrives ahead of one.
    """
    buffer = b""
    while True:
        at = buffer.find(_LACKS)
        if at >= 0 and b"\n" in buffer[at:]:
            line, _, after = buffer[at:].partition(b"\n")
            return buffer[:at], line[len(_LACKS) :].decode("ascii", "replace").split(), after
        if at < 0 and len(buffer) > AHEAD_LIMIT:
            return buffer, None, b""
        chunk = os.read(pipe.fileno(), 65536)
        if not chunk:
            return buffer, None, b""
        buffer += chunk


def read_all(pipe: IO[bytes]) -> bytes:
    return b"".join(iter(lambda: os.read(pipe.fileno(), 65536), b""))


def exchange(target: Target, copy: Copy) -> Sent:
    """The copy's one connection: the manifest out, the lacks line back, the files it names out, the tree's id back.

    Where anything arrives ahead of the lacks line, or none comes, no file is
    sent; the dispatcher then finds the stream closed.
    """
    try:
        process = subprocess.Popen(
            target.ssh(["copy", copy.tree_id]), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except OSError as error:
        raise Refusal(NOT_ASKED, f"{ssh_not_started(error)}\n  Nothing was done on {target.name}.") from error
    said = bytearray()
    try:
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        errors = threading.Thread(target=keep, args=(process.stderr, said), daemon=True)
        errors.start()
        manifest_sent = send(process.stdin, copy.manifest + b"\n", close=False)
        ahead, lacking, after = read_lacking(process.stdout)
        manifest_sent.join()
        wanted = [] if ahead or lacking is None else [digest for digest in lacking if digest in copy.contents]
        files_sent = send(process.stdin, copy.archive(wanted) if wanted else b"", close=True)
        after += read_all(process.stdout)
        status = dev_dispatch.status_of(process.wait())
        files_sent.join()
        errors.join()
    except BaseException:
        # The copy ends with its connection: the dispatcher finds its stream
        # cut off and leaves the current tree as it was.
        process.kill()
        process.wait()
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None:
                with contextlib.suppress(OSError):
                    pipe.close()
        raise
    return Sent(ahead, lacking, after, status, bytes(said))


def copy_tree(target: Target, phase: Phase, copy: Copy) -> None:
    """Copy to *target* the files of *copy* it lacks, in one connection; else a :class:`Refusal` with the status this
    call ends with."""
    phase.now = COPYING
    sent = exchange(target, copy)
    name = target.name
    if sent.ahead:
        pass_on(sent.stderr)
        raise Refusal(
            NOT_ASKED,
            f"{PREFIX}: {name} wrote this on stdout ahead of its dispatcher's answer to the copy:\n"
            f"{shown(sent.ahead.decode('utf-8', 'replace'))}\n{SHELL_PRINTS}\n"
            f"  No file was sent. {COPY_CUT_OFF.format(target=name)}",
        )
    if sent.status == 0 and sent.lacking is not None:
        phase.now = AFTER_COPY
        pass_on(sent.stderr)
        named = tree_named(sent.after, target)
        if named != copy.tree_id:
            raise Refusal(
                NOT_ASKED,
                f"{PREFIX}: {name}'s dispatcher answered the copy of {copy.tree_id} with another tree, {named}.\n"
                f"  {OTHER_DISPATCHER}\n{setup_line(name, None)}\n  {COPY_WENT_THROUGH}",
            )
        return
    stderr = sent.stderr.decode("utf-8", "replace")
    if sent.status == setup.SSH_FAILED and sent.lacking is None:
        message = not_reached(stderr, target)
        if message is not None:
            raise Refusal(NOT_ASKED, f"{message}\n  Nothing was done on {name}.")
    pass_on(sent.stderr)
    if sent.status == 0:
        raise Refusal(
            NOT_ASKED,
            f"{PREFIX}: {name}'s dispatcher answered the copy without naming the files it lacks.\n"
            f"  {OLDER_DISPATCHER}\n{setup_line(name, target.destination)}\n  Nothing was asked.",
        )
    raise copy_failed(sent.status, stderr, target)


def copy_failed(status: int, stderr: str, target: Target) -> Refusal:
    """The refusal for a copy that ended with *status*, *stderr* said, and is no failure before the login."""
    if status in dev_dispatch.STATUSES:
        return Refusal(status, dispatcher_status(status, target.name) + "\n  Nothing was asked.")
    unstarted = dispatcher_not_started(status, stderr, target)
    if unstarted is not None:
        return Refusal(NOT_ASKED, f"{unstarted}\n  Nothing was done on {target.name}.")
    return Refusal(
        BROKE_OFF,
        f"{PREFIX}: the copy to {target.name} ended with {status} without a word from its dispatcher — the connection"
        " broke, or the dispatcher there did not run; what ssh said is above.\n"
        f"  {COPY_CUT_OFF.format(target=target.name)}",
    )


NOT_EXECUTABLE = 126
"""What a POSIX shell ends with for a command it found but could not run."""


def dispatcher_not_started(status: int, stderr: str, target: Target) -> str | None:
    """What to say for a request whose dispatcher never started — its interpreter or the dispatcher itself gone —
    and how to repair it; ``None`` for any other ending."""
    repair = f"  Run the setup again:\n{setup_line(target.name, target.destination)}"
    if status in (setup.NOT_FOUND, NOT_EXECUTABLE):
        return (
            f"{PREFIX}: {target.name} could not start the interpreter its dispatcher runs on (the shell there ended"
            f" with {status}): {setup.TARGET_PYTHON} may be gone or not executable, after a system update say; what"
            f" ssh said is above.\n{repair}"
        )
    if _NO_DISPATCHER.search(stderr):
        return (
            f"{PREFIX}: {target.name}'s dispatcher is gone: its interpreter could not open it; what it said is"
            f" above.\n{repair}"
        )
    return None


class Sink:
    """One of this process's own descriptors, written to until a write fails, then left alone."""

    def __init__(self, descriptor: int) -> None:
        self.descriptor = descriptor
        self.failed: OSError | None = None

    def write(self, data: bytes) -> None:
        if self.failed is not None or not data:
            return
        try:
            write_all(self.descriptor, data)
        except OSError as error:
            self.failed = error


@dataclass(frozen=True)
class Answer:
    """What came back on the request's stdout: what arrived ahead of the run's marker, and whether it came."""

    ahead: bytes
    marked: bool
    write_failed: OSError | None = None
    """Why passing the answer on to this process's stdout stopped, where it did."""


def collect(pipe: IO[bytes], sink: Sink, kept: bytearray) -> None:
    """Pass *pipe* on to *sink* as it arrives, and keep it all in *kept*."""
    for chunk in iter(lambda: os.read(pipe.fileno(), 65536), b""):
        sink.write(chunk)
        kept.extend(chunk)


def read_answer(pipe: IO[bytes], sink: Sink) -> Answer:
    """Read *pipe* up to the run's marker, then pass the rest on to *sink*: nothing ahead of the marker is passed on.

    What arrives ahead of it is kept and the reading stops there: where it is
    anything, the call ends without taking an answer.
    """
    buffer = b""
    while _MARKER not in buffer and len(buffer) <= AHEAD_LIMIT:
        chunk = os.read(pipe.fileno(), 65536)
        if not chunk:
            return Answer(buffer, marked=False)
        buffer += chunk
    if _MARKER not in buffer:
        return Answer(buffer, marked=False)
    ahead, _, rest = buffer.partition(_MARKER)
    if ahead:
        return Answer(ahead, marked=True)
    sink.write(rest)
    for chunk in iter(lambda: os.read(pipe.fileno(), 65536), b""):
        sink.write(chunk)
    return Answer(b"", marked=True, write_failed=sink.failed)


def ask_there(target: Target, request: Request, tree: str, *, copied: bool) -> int | None:
    """Send the request for *tree*: its answer passed on after the run's marker, its stderr as it comes and kept.

    ``None`` where the target does not hold *tree*: nothing ran, and the
    dispatcher said so with :data:`dev_dispatch.TREE_ABSENT` alone. *copied*
    says whether this call's copy went through before.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    command = target.ssh(remote_request(request, tree))
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as error:
        after = not_asked(request, target, copied=True) if copied else "  Nothing was contacted."
        raise Refusal(NOT_ASKED, f"{ssh_not_started(error)}\n{after}") from error
    said = bytearray()
    try:
        assert process.stdout is not None and process.stderr is not None
        errors = threading.Thread(target=collect, args=(process.stderr, Sink(sys.stderr.fileno()), said), daemon=True)
        errors.start()
        answer = read_answer(process.stdout, Sink(sys.stdout.fileno()))
        if answer.ahead:
            # The run ends with its connection: ssh gone and its output no
            # longer read, the dispatcher sees its output hang up.
            process.kill()
            process.stdout.close()
        status = dev_dispatch.status_of(process.wait())
        errors.join()
    except BaseException:
        # The run ends with its connection: ssh gone and both pipes closed, the
        # dispatcher sees its output hang up.
        process.kill()
        process.wait()
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()
        raise
    if status == dev_dispatch.TREE_ABSENT and not answer.marked and not answer.ahead:
        return None
    return judge(status, bytes(said).decode("utf-8", "replace"), target, request, answer, copied=copied)


def what_runs(request: Request) -> str:
    return "the question" if request.mode == "ask" else f"the probe {request.words[0]}"


def not_asked(request: Request, target: Target, *, copied: bool) -> str:
    """The line that ends a failure of the request: whether this call's copy went through before it."""
    if copied:
        return f"  {what_runs(request).capitalize()} was not asked; the copy went through."
    return f"  Nothing was done on {target.name}."


def judge(status: int, stderr: str, target: Target, request: Request, answer: Answer, *, copied: bool) -> int:
    """This call's status for a request that ended with *status*: the run's own, the dispatcher's or the client's."""
    if answer.ahead:
        return _output_ahead(status, target, request, answer, copied=copied)
    if status == OLDER_TREE_GONE and not answer.marked:
        say(
            f"{PREFIX}: {target.name}'s dispatcher ended with {status}, which only one older than this client ends a"
            f" question with.\n  {OLDER_DISPATCHER}\n{setup_line(target.name, target.destination)}\n"
            f"{not_asked(request, target, copied=copied)}"
        )
        return NOT_ASKED
    if status in dev_dispatch.STATUSES and status != dev_dispatch.COPY_FAILED:
        say(dispatcher_status(status, target.name))
        return status
    if answer.marked:
        if answer.write_failed is not None:
            return _not_written(answer.write_failed, status, target)
        return status if status != setup.SSH_FAILED else _broke_off(request, target)
    return _unmarked(status, stderr, target, request, copied=copied)


def _output_ahead(status: int, target: Target, request: Request, answer: Answer, *, copied: bool) -> int:
    """The status for a request with output on stdout where the run's answer was not: ahead of the marker, or with none.

    With the marker, that output is the shell's, and the run is ended unanswered.
    Without one, a status of the dispatcher's own stands, the output named
    beside it; any other run's output cannot be told apart from the shell's.
    """
    arrived = shown(answer.ahead.decode("utf-8", "replace"))
    if answer.marked:
        say(
            f"{PREFIX}: {target.name} wrote this on stdout ahead of the run's answer:\n{arrived}\n{SHELL_PRINTS}\n"
            f"  The run was ended and no answer taken from it; {what_runs(request)} gave none here."
        )
        return NOT_ASKED
    if status == dev_dispatch.TREE_ABSENT:
        say(
            f"{PREFIX}: {target.name} wrote this on stdout beside its dispatcher's answer:\n{arrived}\n{SHELL_PRINTS}\n"
            f"{not_asked(request, target, copied=copied)}"
        )
        return NOT_ASKED
    if status in dev_dispatch.STATUSES:
        say(f"{PREFIX}: {target.name} wrote this on stdout as well:\n{arrived}\n{SHELL_PRINTS}")
        say(dispatcher_status(status, target.name))
        return status
    say(
        f"{PREFIX}: no marker came from {target.name}, so what arrived on stdout cannot be told apart from what its"
        f" shell prints:\n{arrived}\n  {OLDER_DISPATCHER}\n{setup_line(target.name, target.destination)}\n"
        f"  No answer was taken; {what_runs(request)} gave none here."
    )
    return NOT_ASKED


def _not_written(error: OSError, status: int, target: Target) -> int:
    """The status for an answer this process could not write out whole: never the run's own."""
    if isinstance(error, BrokenPipeError):
        say(
            f"{PREFIX}: what read this command's output stopped before the answer ended, so the rest of it was not"
            f" written; the run on {target.name} ended with {status}."
        )
    else:
        say(
            f"{PREFIX}: the answer could not be written here ({setup.reason(error)}), so what was written of it is cut"
            f" short; the run on {target.name} ended with {status}."
        )
    return BROKE_OFF


def _broke_off(request: Request, target: Target) -> int:
    say(
        f"{PREFIX}: ssh ended with {setup.SSH_FAILED} while {what_runs(request)} ran on {target.name}: the connection"
        f" broke, or the run itself ended with {setup.SSH_FAILED}; what it printed is above."
    )
    return BROKE_OFF


def _unmarked(status: int, stderr: str, target: Target, request: Request, *, copied: bool) -> int:
    """The status for a request that ended before the run's marker came, and ended with no dispatcher's status."""
    if status == setup.SSH_FAILED:
        message = not_reached(stderr, target)
        if message is not None:
            say(f"{message}\n{not_asked(request, target, copied=copied)}")
            return NOT_ASKED
    unstarted = dispatcher_not_started(status, stderr, target)
    if unstarted is not None:
        say(f"{unstarted}\n{not_asked(request, target, copied=copied)}")
        return NOT_ASKED
    if status == 0:
        say(
            f"{PREFIX}: {target.name}'s dispatcher ran the request but wrote no marker ahead of its answer, so none was"
            f" taken.\n  {OLDER_DISPATCHER}\n{setup_line(target.name, target.destination)}"
        )
        return NOT_ASKED
    say(
        f"{PREFIX}: {what_runs(request)} on {target.name} ended with {status} before its run started: the connection"
        " broke, or the dispatcher or its sandbox there failed to start; what ssh and the target said is above."
    )
    return BROKE_OFF


def prepared_copy() -> Copy:
    """This tree's copy, built before anything is contacted."""
    try:
        return build_copy(TREE, setup.python_floor())
    except OSError as error:
        raise Refusal(
            NOT_ASKED, f"{PREFIX}: the copy could not be built here: {error}. Nothing was contacted."
        ) from error


def run_there(request: Request, phase: Phase) -> int:
    """Ask the target from this tree, copying first what it lacks where it does not hold the tree; the run's own
    status, the dispatcher's or ours.

    A target that holds the tree is asked in one connection. One that does not
    gets the copy, then the question once more; where it holds no tree even
    then, the call ends with that said rather than copying again.
    """
    assert request.target is not None
    target = find_target(request.target)
    phase.target = target.name
    copy = prepared_copy()
    phase.now = ASKING
    status = ask_there(target, request, copy.tree_id, copied=False)
    if status is not None:
        return status
    copy_tree(target, phase, copy)
    phase.now = ASKING
    status = ask_there(target, request, copy.tree_id, copied=True)
    if status is None:
        raise Refusal(
            NOT_ASKED,
            f"{PREFIX}: {target.name} does not hold the tree {copy.tree_id} its copy just made current: another copy"
            f" replaced it in between, or it was removed.\n{not_asked(request, target, copied=True)} Ask again.",
        )
    return status


def crashed(phase: Phase) -> int:
    """Print the traceback of the exception being handled, say where the call was, and end with a status of ours.

    A crash must not end this process with Python's 1, which a probe uses for
    what it checks not holding: where stderr itself is what failed, the
    status still comes back.
    """
    end = phase.end
    try:
        traceback.print_exc()
        after = end.after_a_crash.format(target=phase.target)
        say(f"{PREFIX}: this client failed {phase.now} (the traceback is above). {after}")
    except Exception:  # stderr itself failed: nothing is left to tell, and the status still comes back
        pass
    return end.status


def interrupted(phase: Phase) -> int:
    """Say where an interrupt found the call, and end with the status a shell gives one."""
    try:
        say(f"{PREFIX}: interrupted {phase.now}. {phase.end.after_an_interrupt.format(target=phase.target)}")
    except Exception:  # stderr itself failed: nothing is left to tell, and the status still comes back
        pass
    return 128 + signal.SIGINT


def main(mode: Mode, argv: list[str]) -> int:
    phase = Phase()
    try:
        request = parse(mode, argv)
        if request.target is None:
            phase.now = HERE
            return run_here(request)
        return run_there(request, phase)
    except Refusal as refusal:
        say(refusal.message)
        return refusal.status
    except setup.SetupError as error:
        say(f"{error}\n  Nothing was contacted.")
        return NOT_ASKED
    except KeyboardInterrupt:
        return interrupted(phase)
    except Exception:
        return crashed(phase)


if __name__ == "__main__":
    if sys.argv[1:2] not in (["ask"], ["probe"]):
        say(f"{PREFIX}: run through `mise run ask` or `mise run probe`.\n{USAGE_LINES}")
        sys.exit(USAGE)
    sys.exit(main("ask" if sys.argv[1] == "ask" else "probe", sys.argv[2:]))
