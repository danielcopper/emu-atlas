#!/usr/bin/env python3
"""Ask atlas one question, or run a probe script, here or on a remote target.

    mise run ask [--target <name>] -- <question>
    mise run probe [--target <name>] <script> [arguments]

<question> is what ``python -m atlas`` takes, word for word; <script> is a file
name in scripts/probes/. Without --target both run here, on this tree's
interpreter with this tree's atlas. With --target <name>, a target set up with
``mise run setup:remote-target``, this tree's atlas/ and scripts/probes/ are
copied there as they stand and the question or the script runs there, in the
dispatcher's sandbox; its stdout, its stderr and its exit status come back
here unchanged.

--target is read only as the first argument, so a question's own --target
(``save-portability --target <label>``) stays the question's. What is copied,
this client's own exit statuses and what each failure says:
docs/remote-target.md, "Asking a target".
"""

from __future__ import annotations

import base64
import io
import json
import os
import signal
import subprocess
import sys
import tarfile
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# How ssh fails, where the target's entry lives and the dispatcher's statuses
# each have one home; this client reads them there rather than restating them.
from scripts import dev_dispatch  # noqa: E402
from scripts import setup_remote_target as setup  # noqa: E402

PREFIX = setup.PREFIX

TREE = ROOT
"""The tree a question is asked from: its atlas/ here, and the copy's contents there."""

COPIED = ("atlas", dev_dispatch.PROBES)
"""What a copy carries, relative to :data:`TREE`."""

SKIPPED_DIRECTORY = "__pycache__"
SKIPPED_SUFFIX = ".pyc"

# This client's own exit statuses. A usage error ends with 2, as atlas's own
# does, before anything is contacted; the client's other failures end with
# statuses neither atlas (0, 2) nor the dispatcher (3, 4, 5, 8) ends with. A
# question's or a probe's own status is passed through. docs/remote-target.md
# lists what each one covers.
USAGE = 2
NOT_ASKED = 6
BROKE_OFF = 7

# How long ssh waits for a connection before it gives up on an address: a
# device that is asleep answers no connection at all.
CONNECT_TIMEOUT = 15

Mode = Literal["ask", "probe"]
"""Which task called: ``mise run ask`` or ``mise run probe``."""

USAGE_LINES = (
    "usage: mise run ask [--target <name>] -- <question>\n"
    "       mise run probe [--target <name>] <script> [arguments]"
)

NO_SSH = f"{PREFIX}: asking a target needs ssh, and this machine has none on its PATH."
OLDER_DISPATCHER = (
    "The dispatcher there is older than this client; run the setup again, which installs the current one:"
)

_DISPATCHER_STATUSES = {
    dev_dispatch.REFUSED: "REFUSED",
    dev_dispatch.CANNOT_RUN: "CANNOT_RUN",
    dev_dispatch.COPY_FAILED: "COPY_FAILED",
    dev_dispatch.TREE_GONE: "TREE_GONE",
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
    words: list[str]


BEFORE_CONTACT = "before anything was contacted"
HERE = "while running here"
COPYING = "while copying to the target"
ASKING = "while asking the target"

_AFTER_A_CRASH = {
    BEFORE_CONTACT: (NOT_ASKED, "Nothing was contacted."),
    HERE: (NOT_ASKED, "No target was contacted; whatever ran here printed above, and its status is lost."),
    COPYING: (BROKE_OFF, "The target's current tree is this copy whole, or the one before it. Nothing was asked."),
    ASKING: (BROKE_OFF, "What the run printed is above; its status is lost."),
}


class Phase:
    """Where a call is, for the message a crash of this client ends it with."""

    def __init__(self) -> None:
        self.now = BEFORE_CONTACT


def usage(message: str) -> Refusal:
    return Refusal(USAGE, f"{PREFIX}: {message}\n{USAGE_LINES}")


def split_target(argv: list[str]) -> tuple[str | None, list[str]]:
    """The leading ``--target <name>`` (or ``--target=<name>``) of *argv*, if any, and the words after it.

    A ``--`` right after it, or leading where there is none, is dropped: mise
    drops the first ``--`` it is given, so one that arrives here was meant to
    separate the words.
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
    return Request(mode, target, words)


def status_of(returncode: int) -> int:
    """A process's exit status as a shell gives it: 128 plus the signal's number for one a signal ended."""
    return returncode if returncode >= 0 else 128 - returncode


def run_here(request: Request) -> int:
    """The question or the probe script run here, on this interpreter, with :data:`TREE`'s atlas imported first.

    ``-P`` keeps the working directory and the script's own directory off
    ``sys.path``, as the dispatcher's bootstrap does; the tree comes in through
    ``PYTHONPATH``, ahead of anything installed.
    """
    path = [str(TREE), *([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else [])]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(path)}
    if request.mode == "ask":
        command = [sys.executable, "-P", "-m", "atlas", *request.words]
    else:
        command = [sys.executable, "-P", str(probes_directory() / request.words[0]), *request.words[1:]]
    sys.stdout.flush()
    sys.stderr.flush()
    return status_of(subprocess.run(command, env=env, check=False).returncode)


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


def build_copy(tree: Path, floor: str) -> bytes:
    """The uncompressed tar stream a ``copy`` sends: :func:`copy_members` of *tree*, then the manifest last.

    The manifest states *floor*, the Python the tree needs. Owners are left
    out: the target unpacks as its own user.
    """
    members, left_out = copy_members(tree)
    for path in left_out:
        print(f"{PREFIX}: not copied, being no file or directory: {path.relative_to(tree)}", file=sys.stderr)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in members:
            info = archive.gettarinfo(str(path), arcname=path.relative_to(tree).as_posix())
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            if info.isreg():
                with open(path, "rb") as handle:
                    archive.addfile(info, handle)
            else:
                archive.addfile(info)
        manifest = json.dumps({"requires_python": floor}).encode("utf-8")
        info = tarfile.TarInfo(dev_dispatch.MANIFEST)
        info.size = len(manifest)
        info.mode = 0o644
        info.mtime = int(time.time())
        archive.addfile(info, io.BytesIO(manifest))
    return buffer.getvalue()


def word(arguments: list[str]) -> str:
    """*arguments* as the dispatcher reads them: a JSON array of strings, UTF-8, base64url without padding."""
    return base64.urlsafe_b64encode(json.dumps(arguments).encode("utf-8")).decode("ascii").rstrip("=")


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
    """A target as this machine's own SSH config records it: its name, its entry, and the files the entry names."""

    name: str
    destination: setup.Destination
    config: Path

    @property
    def known_hosts(self) -> Path:
        return self.config.with_name(setup.KNOWN_HOSTS_NAME)

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
    """The target *name* as this machine records it, or "no such target" before anything is contacted."""
    config = setup.config_dir() / setup.SSH_CONFIG_NAME
    try:
        text = config.read_text(encoding="utf-8")
    except FileNotFoundError:
        text, held = "", f"there is no {config}"
    except (OSError, UnicodeError) as error:
        raise Refusal(
            NOT_ASKED, f"{PREFIX}: could not read {config}: {setup.reason(error)}. Nothing was contacted."
        ) from error
    else:
        held = f"{config} has no entry for it"
    destination = setup.read_entry(text, name)
    if destination is None:
        raise Refusal(
            NOT_ASKED,
            f"{PREFIX}: no target named {name} is set up here — {held}.\n"
            f"  Set it up first:\n{setup_line(name, None)}\n"
            "  Nothing was contacted.",
        )
    return Target(name, destination, config)


def _key_refused(target: Target) -> str:
    return (
        f"{PREFIX}: {target.name} refused this machine's key. It takes the key only from the address it saw this"
        " machine at when it was set up, and that may have changed — or the key's line there was removed.\n"
        f"  Run the setup again:\n{setup_line(target.name, target.destination)}"
    )


def _target_message(kind: setup.FailureKind, target: Target) -> str:
    """The message for a failure ``ssh`` placed on the target itself, with the repair it needs here."""
    name, destination = target.name, target.destination
    if kind == "unresolved":
        return (
            f"{PREFIX}: this machine cannot resolve {destination.hostname}, where {name}'s entry says it is.\n"
            f"  Run the setup with the target's address, or a name the network resolves:\n"
            f"{setup_line(name, destination, '<address>')}"
        )
    if kind == "no server":
        return (
            f"{PREFIX}: no SSH server answers at {destination.hostname} port {destination.port}, where {name}'s entry"
            f" says it is.\n  {name} may be asleep: wake it and ask again. Or it has a new address: run the setup"
            f" with that one:\n{setup_line(name, destination, '<new address>')}"
        )
    if kind == "host key not accepted":
        return (
            f"{PREFIX}: this machine holds no record of {name}'s host key, so no connection was made.\n"
            f"  Run the setup again, which shows the fingerprint and records it:\n{setup_line(name, destination)}"
        )
    return _key_refused(target)


def not_reached(stderr: str, target: Target) -> str | None:
    """What to say for an ``ssh`` that ended before the login, from the setup's one reading of it; ``None`` otherwise.

    A failure on the target itself is worded with this client's repair; one on
    a hop, and a host key that is not the recorded one, as the setup words it.
    """
    failure = setup.ssh_failure(stderr, name=target.name, destination=target.destination)
    if failure.kind == "unknown":
        return None
    if failure.where == "target" and failure.kind != "host key changed":
        return _target_message(failure.kind, target)
    worded = setup.failure_text(
        failure, name=target.name, destination=target.destination, known_hosts=(target.known_hosts,)
    )
    return worded if worded is not None else _key_refused(target)


def ssh_not_started(error: OSError) -> str:
    if isinstance(error, FileNotFoundError):
        return NO_SSH
    return f"{PREFIX}: ssh could not be started: {setup.reason(error)}."


def run_ssh(command: list[str], stdin: bytes | None) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(command, input=stdin, capture_output=True, check=False)
    except OSError as error:
        raise Refusal(NOT_ASKED, f"{ssh_not_started(error)}\n  Nothing was contacted.") from error


def pass_on(data: bytes) -> None:
    """*data* on this process's stderr, byte for byte, after whatever was written there before."""
    sys.stderr.flush()
    os.write(sys.stderr.fileno(), data)


def say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def dispatcher_status(status: int, name: str) -> str:
    """The line this client adds below a status of the dispatcher's own, which ran nothing of the request's."""
    line = f"{PREFIX}: that was {name}'s dispatcher ({status}, {_DISPATCHER_STATUSES[status]}), not atlas or a probe."
    if status == dev_dispatch.REFUSED:
        line += f"\n  {OLDER_DISPATCHER}\n{setup_line(name, None)}"
    return line


def indented(text: str) -> str:
    return "\n".join(f"    {line}" for line in text.splitlines())


def tree_named(stdout: bytes, target: Target) -> str:
    """The id of the tree the copy made, the one line its dispatcher answers with on stdout.

    Anything else on that stdout is refused before a question is asked: it
    is what the target's shell prints for a command run without a terminal,
    and it would land in front of every answer. No line at all is what a
    dispatcher older than this client answers.
    """
    text = stdout.decode("utf-8", "replace")
    lines = text.splitlines()
    tree = lines[-1] if lines and dev_dispatch.TREE_ID.fullmatch(lines[-1]) else None
    if tree is not None and text == f"{tree}\n":
        return tree
    current = f"  The copy is current on {target.name}; nothing was asked."
    if not text:
        raise Refusal(
            NOT_ASKED,
            f"{PREFIX}: {target.name}'s dispatcher took the copy but named no tree to ask from.\n"
            f"  {OLDER_DISPATCHER}\n{setup_line(target.name, target.destination)}\n{current}",
        )
    repair = setup_line(target.name, target.destination)
    older = "" if tree is not None else f"\n  It named no tree either. {OLDER_DISPATCHER}\n{repair}"
    arrived = indented(text if tree is None else "\n".join(lines[:-1]))
    raise Refusal(
        NOT_ASKED,
        f"{PREFIX}: {target.name} wrote this on stdout ahead of its dispatcher's answer to the copy:\n{arrived}\n"
        "  The shell there prints from its startup files for a command run without a terminal, and that would land"
        " in front of every answer. Make them silent for non-interactive shells (in ~/.bashrc, say, return at the"
        f" top unless $- holds i).{older}\n{current}",
    )


def copy_tree(target: Target, phase: Phase) -> str:
    """Send the copy; the id of the tree it made, else a :class:`Refusal` with the status this call ends with."""
    try:
        stream = build_copy(TREE, setup.python_floor())
    except OSError as error:
        raise Refusal(
            NOT_ASKED, f"{PREFIX}: the copy could not be built here: {error}. Nothing was contacted."
        ) from error
    phase.now = COPYING
    copied = run_ssh(target.ssh(["copy"]), stream)
    if copied.returncode == 0:
        pass_on(copied.stderr)
        return tree_named(copied.stdout, target)
    if copied.returncode == setup.SSH_FAILED:
        message = not_reached(copied.stderr.decode("utf-8", "replace"), target)
        if message is not None:
            raise Refusal(NOT_ASKED, f"{message}\n  Nothing was done on {target.name}.")
    pass_on(copied.stderr)
    if copied.returncode in _DISPATCHER_STATUSES:
        raise Refusal(copied.returncode, dispatcher_status(copied.returncode, target.name) + "\n  Nothing was asked.")
    raise Refusal(
        BROKE_OFF,
        f"{PREFIX}: the copy to {target.name} ended with {copied.returncode} without a word from its dispatcher —"
        " the connection broke, or the dispatcher there did not run; what ssh said is above.\n"
        f"  {target.name}'s current tree is this copy whole, or the one before it. Nothing was asked.",
    )


def ask_there(target: Target, request: Request, tree: str) -> int:
    """Send the request for *tree*: its stdout straight through, its stderr passed on as it comes and kept."""
    sys.stdout.flush()
    sys.stderr.flush()
    command = target.ssh(remote_request(request, tree))
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except OSError as error:
        raise Refusal(NOT_ASKED, f"{ssh_not_started(error)}\n{not_asked(request, target)}") from error
    pipe = process.stderr
    assert pipe is not None
    said = bytearray()
    for chunk in iter(lambda: os.read(pipe.fileno(), 65536), b""):
        pass_on(chunk)
        said += chunk
    pipe.close()
    status = status_of(process.wait())
    return judge(status, bytes(said).decode("utf-8", "replace"), target, request)


def what_runs(request: Request) -> str:
    return "the question" if request.mode == "ask" else f"the probe {request.words[0]}"


def not_asked(request: Request, target: Target) -> str:
    return f"  {what_runs(request).capitalize()} was not asked; the copy made just before is current on {target.name}."


def judge(status: int, stderr: str, target: Target, request: Request) -> int:
    """This call's status for a request that ended with *status*: the run's own, the dispatcher's or the client's."""
    if status in _DISPATCHER_STATUSES and status != dev_dispatch.COPY_FAILED:
        say(dispatcher_status(status, target.name))
        return status
    if status != setup.SSH_FAILED:
        return status
    message = not_reached(stderr, target)
    if message is not None:
        say(f"{message}\n{not_asked(request, target)}")
        return NOT_ASKED
    say(
        f"{PREFIX}: ssh ended with {setup.SSH_FAILED} while {what_runs(request)} ran on {target.name}: the connection"
        f" broke, or the run itself ended with {setup.SSH_FAILED}; what it printed is above."
    )
    return BROKE_OFF


def run_there(request: Request, phase: Phase) -> int:
    """Copy this tree to the target, then ask there from that copy; the run's own status, the dispatcher's or ours."""
    assert request.target is not None
    target = find_target(request.target)
    tree = copy_tree(target, phase)
    phase.now = ASKING
    return ask_there(target, request, tree)


def crashed(phase: Phase) -> int:
    """Print the traceback of the exception being handled, say where the call was, and end with a status of ours.

    A crash must not end this process with Python's 1, which a probe uses for
    what it checks not holding.
    """
    traceback.print_exc()
    status, after = _AFTER_A_CRASH[phase.now]
    say(f"{PREFIX}: this client failed {phase.now} (the traceback is above). {after}")
    return status


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
        say(
            f"{PREFIX}: interrupted. A copy cut off leaves the target's current tree as it was; a question cut off"
            " ends there with its connection."
        )
        return 128 + signal.SIGINT
    except Exception:
        return crashed(phase)


if __name__ == "__main__":
    if sys.argv[1:2] not in (["ask"], ["probe"]):
        say(f"{PREFIX}: run through `mise run ask` or `mise run probe`.\n{USAGE_LINES}")
        sys.exit(USAGE)
    sys.exit(main("ask" if sys.argv[1] == "ask" else "probe", sys.argv[2:]))
