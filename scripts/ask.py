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

# This client's own exit statuses, none of which atlas (0, 2) or the
# dispatcher (3, 4, 5) ends with; a question's or a probe's own status is
# passed through. USAGE is atlas's own status for a question that could not
# be put. docs/remote-target.md lists what each one covers.
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

_DISPATCHER_STATUSES = {
    dev_dispatch.REFUSED: "REFUSED",
    dev_dispatch.CANNOT_RUN: "CANNOT_RUN",
    dev_dispatch.COPY_FAILED: "COPY_FAILED",
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
    """Refuse a probe script that is no bare file name of scripts/probes/ — the dispatcher's own rule — or is not there."""
    if not dev_dispatch.PROBE_NAME.fullmatch(script):
        raise usage(f"a probe script is a file name of scripts/probes/, letters, digits and '_' then .py — not {script!r}.")
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


def remote_request(request: Request) -> list[str]:
    """The dispatcher's request for *request*: ``ask <word>``, or ``probe <name>`` with a word for its arguments."""
    if request.mode == "ask":
        return ["ask", word(request.words)]
    script, arguments = request.words[0], request.words[1:]
    return ["probe", script, *([word(arguments)] if arguments else [])]


def setup_line(name: str, destination: setup.Destination | None, address: str = "") -> str:
    """The setup's command line for *name*: with *address* for the user's, or the entry's own where no hop is in the way."""
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
        """``ssh`` reading the target's entry from this tool's config alone — the restricted key alone — with *words* as the request."""
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
        raise Refusal(NOT_ASKED, f"{PREFIX}: could not read {config}: {setup.reason(error)}. Nothing was contacted.") from error
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
    """What to say for an ``ssh`` that ended before the login, from the setup's reading of it; ``None`` otherwise.

    A failure on the target itself is worded with this client's repair; one on
    a hop, and a host key that is not the recorded one, as the setup words it.
    """
    failure = setup.ssh_failure(stderr, name=target.name, destination=target.destination)
    if failure is None:
        return None
    if failure.where == "target" and failure.kind != "host key changed":
        return _target_message(failure.kind, target)
    worded = setup.classify(
        stderr, name=target.name, destination=target.destination, known_hosts=(target.known_hosts,)
    )
    return worded if worded is not None else _key_refused(target)


def run_ssh(command: list[str], stdin: bytes | None) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(command, input=stdin, capture_output=True, check=False)
    except FileNotFoundError as error:
        raise Refusal(NOT_ASKED, f"{PREFIX}: asking a target needs ssh, and this machine has none on its PATH.") from error


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
        line += f"\n  The dispatcher there may be older than this client; run the setup again:\n{setup_line(name, None)}"
    return line


def copy_tree(target: Target) -> int | None:
    """Send the copy; ``None`` once the target took it, else the status this call ends with."""
    try:
        stream = build_copy(TREE, setup.python_floor())
    except OSError as error:
        raise Refusal(NOT_ASKED, f"{PREFIX}: the copy could not be built here: {error}. Nothing was contacted.") from error
    copied = run_ssh(target.ssh(["copy"]), stream)
    stderr = copied.stderr.decode("utf-8", "replace")
    if copied.returncode == 0:
        pass_on(copied.stderr)
        return None
    if copied.returncode == setup.SSH_FAILED:
        message = not_reached(stderr, target)
        if message is not None:
            raise Refusal(NOT_ASKED, f"{message}\n  Nothing was done on {target.name}.")
    pass_on(copied.stderr)
    if copied.returncode in _DISPATCHER_STATUSES:
        say(dispatcher_status(copied.returncode, target.name) + "\n  Nothing was asked.")
        return copied.returncode
    say(
        f"{PREFIX}: the copy to {target.name} ended with {copied.returncode} without a word from its dispatcher —"
        " the connection broke, or the dispatcher there did not run; what ssh said is above.\n"
        f"  {target.name}'s current tree is this copy whole, or the one before it. Nothing was asked."
    )
    return BROKE_OFF


def ask_there(target: Target, request: Request) -> int:
    """Send the request after the copy: its stdout straight through, its stderr passed on as it comes and kept."""
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        process = subprocess.Popen(target.ssh(remote_request(request)), stdin=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except FileNotFoundError as error:
        raise Refusal(NOT_ASKED, f"{PREFIX}: asking a target needs ssh, and this machine has none on its PATH.") from error
    pipe = process.stderr
    assert pipe is not None
    said = bytearray()
    for chunk in iter(lambda: os.read(pipe.fileno(), 65536), b""):
        pass_on(chunk)
        said += chunk
    pipe.close()
    status = status_of(process.wait())
    return judge(status, bytes(said).decode("utf-8", "replace"), target, request)


def judge(status: int, stderr: str, target: Target, request: Request) -> int:
    """This call's status for a request that ended with *status*: the run's own, or the dispatcher's or this client's."""
    what = "the question" if request.mode == "ask" else f"the probe {request.words[0]}"
    if status in _DISPATCHER_STATUSES and status != dev_dispatch.COPY_FAILED:
        say(dispatcher_status(status, target.name))
        return status
    if status != setup.SSH_FAILED:
        return status
    message = not_reached(stderr, target)
    if message is not None:
        say(f"{message}\n  {what.capitalize()} was not asked; the copy made just before is current on {target.name}.")
        return NOT_ASKED
    say(
        f"{PREFIX}: ssh ended with {setup.SSH_FAILED} while {what} ran on {target.name}: the connection broke, or"
        f" the run itself ended with {setup.SSH_FAILED}; what it printed is above."
    )
    return BROKE_OFF


def run_there(request: Request) -> int:
    """Copy this tree to the target, then ask there; the run's own status, or the dispatcher's or this client's."""
    assert request.target is not None
    target = find_target(request.target)
    ended = copy_tree(target)
    if ended is not None:
        return ended
    return ask_there(target, request)


def main(mode: Mode, argv: list[str]) -> int:
    try:
        request = parse(mode, argv)
        return run_here(request) if request.target is None else run_there(request)
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


if __name__ == "__main__":
    if sys.argv[1:2] not in (["ask"], ["probe"]):
        say(f"{PREFIX}: run through `mise run ask` or `mise run probe`.\n{USAGE_LINES}")
        sys.exit(USAGE)
    sys.exit(main("ask" if sys.argv[1] == "ask" else "probe", sys.argv[2:]))
