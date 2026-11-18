#!/usr/bin/env python3
"""Set another machine up as a remote target for emu-atlas, or repair it.

    mise run setup:remote-target <name> <destination> [--print]

<destination> is anything your own ssh reaches: user@address, or an alias from
your SSH config, which is read with `ssh -G` and never written. With --print
the setup logs into nothing: it resolves the destination, asks `ip route get`
for this machine's address towards it, and prints a script to run on the
target instead.

What the setup does on both machines: docs/remote-target.md, "What the setup
does"; what each refusal means: "When the setup refuses".
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import os
import pathlib
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from typing import Final, Literal, assert_never

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPATCHER = REPO / "scripts" / "dev_dispatch.py"
INSTALL_SCRIPT = REPO / "scripts" / "dev_target_install.py"
PYPROJECT = REPO / "pyproject.toml"

PREFIX = "emu-atlas-dev"
CONFIG_DIR_NAME = "emu-atlas-dev"
KEY_NAME = "id_ed25519"
PUBLIC_KEY_NAME = f"{KEY_NAME}.pub"
SSH_CONFIG_NAME = "ssh_config"
KNOWN_HOSTS_NAME = "known_hosts"

# Where the install script puts the dispatcher and the copied trees on the
# target, relative to the target user's home.
TARGET_ROOT = ".local/lib/emu-atlas-dev"

# The interpreter the dispatcher, every probe and the install script run on
# there, and the sandbox every probe starts in: /usr/bin only, as a probe
# needs nothing a target does not already carry.
TARGET_PYTHON = "/usr/bin/python3"
TARGET_BWRAP = "/usr/bin/bwrap"

# Where the printed script finds the host keys whose fingerprints it shows:
# OpenSSH's default HostKey paths.
HOST_KEY_DIR = "/etc/ssh"

# The install script's markers, searched for rather than expected alone: the
# user's shell may print lines of its own first. Without STARTED on stdout the
# script never ran, and the setup can say that a failure changed nothing on the
# target. With it, only a line the script wrote says what it left: LACKS opens
# the line on stderr naming what the target lacks, found before anything was
# written; STOPPED the line with which the install refused to go on, which
# running the setup again would not change; FAILED an error on the way, with
# what the install left in place. PYTHON_FOUND gives the version of the
# target's interpreter once it passed the floor.
STARTED = "emu-atlas-dev-setup: started"
PYTHON_FOUND = "emu-atlas-dev-setup: python="
INSTALLED = "emu-atlas-dev-setup: installed from="
STOPPED = "emu-atlas-dev-setup: stopped: "
LACKS = "emu-atlas-dev-setup: lacks: "
FAILED = "emu-atlas-dev-setup: failed: "

# What ssh exits with when it fails itself, rather than handing on the remote
# command's status (ssh(1), EXIT STATUS); and what a POSIX shell exits with for
# a command it cannot find.
SSH_FAILED = 255
NOT_FOUND = 127

# The first Python whose standard library opens a zstd image (PEP 784).
ZSTD_IN_STDLIB = (3, 14)

# What ssh says on stderr for each failure that ends a login before it starts,
# in the order they are tested: a host key mismatch also prints "Host key
# verification failed", and a refused login can end in "Connection closed".
_HOST_KEY_CHANGED = re.compile(r"REMOTE HOST IDENTIFICATION HAS CHANGED|Host key for \S+ has changed")
_OFFENDING_FILE = re.compile(r"Offending \S+ key in (\S+):\d+")
_HOST_KEY_NOT_ACCEPTED = re.compile(r"Host key verification failed")
_KEY_REFUSED = re.compile(r"Permission denied \(")
_NO_LOGIN = re.compile(_KEY_REFUSED.pattern + r"|Too many authentication failures")
_UNRESOLVED = re.compile(r"Could not resolve hostname ([^:\s]+)")
# A connection closed or reset counts only during the key exchange, before any
# login: the same words end a session that logged in and dropped.
_NO_SERVER = re.compile(
    r"Connection refused|Connection timed out|Operation timed out|No route to host|Network is unreachable"
    r"|kex_exchange_identification"
)

# Where ssh names the host a failure is about: a refused login as user@host, a
# connection as "connect to host <host> port", a name and a changed key by it.
_NAMED_HOSTS = (
    re.compile(r"\S@([^\s@]+): Permission denied \("),
    re.compile(r"connect to host (\S+) port"),
    _UNRESOLVED,
    re.compile(r"Host key for (\S+) has changed"),
)

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_FLOOR = re.compile(r">=\s*(\d+\.\d+)", re.ASCII)

_CONFIG_HEADER = (
    "# emu-atlas's own SSH config for remote targets, which setup:remote-target writes:\n"
    "# one entry per target, to be read with this file alone (docs/remote-target.md).\n"
)

_USER = "user"
_HOSTNAME = "hostname"
_PORT = "port"
_PROXY_JUMP = "proxyjump"
_PROXY_COMMAND = "proxycommand"


Where = Literal["target", "hop", "either"]
"""Whose failure ssh reported: the target's, a hop's, or either where it names no host."""

HandOver = Literal["refused", "forced command", "hop"]
"""Why the key's login gives way to the user's own (:func:`hand_over`)."""

FORCED_COMMAND: Final = "forced command"
"""The key was taken, but its forced command ran no install."""

FailureKind = Literal["host key changed", "host key not accepted", "no login", "unresolved", "no server", "unknown"]
"""What ended an ``ssh`` before the login, as its stderr says (:func:`ssh_failure`); ``unknown`` where no pattern knows
what it said."""


@dataclass(frozen=True)
class SshFailure:
    """A failure of ``ssh``'s own: what it was, and whose — the target's, a hop's, or either where ssh names no host.

    Each payload belongs to one kind and is empty for every other, which
    construction checks.
    """

    kind: FailureKind
    where: Where
    host: str = ""
    """For ``unresolved``, the name ``ssh`` could not resolve."""
    offending: str = ""
    """For ``host key changed``, the known-hosts file ``ssh`` names as holding the other key."""
    permission_denied: bool = False
    """For ``no login``, whether the host refused the keys (``Permission denied``) rather than ending the login after
    too many of them."""

    def __post_init__(self) -> None:
        stray = (
            (self.host and self.kind != "unresolved")
            or (self.offending and self.kind != "host key changed")
            or (self.permission_denied and self.kind != "no login")
        )
        if stray:
            raise ValueError(f"an SshFailure of kind {self.kind!r} carries another kind's payload: {self!r}")


class SetupError(Exception):
    """A refusal: the message is what the user reads, and the run ends with it.

    *why* is the cause in a few words, for a message that quotes it inside
    another; empty where the message has nothing shorter to give.
    """

    def __init__(self, message: str, why: str = "") -> None:
        super().__init__(message)
        self.why = why


@dataclass(frozen=True)
class Destination:
    """Where a target is: what ``ssh -G`` resolved :attr:`text` to, or a target's entry read back (:func:`read_entry`).

    Read back, :attr:`text` is the target's name and :attr:`proxy_jump` the jump
    as the entry spells it out.
    """

    text: str
    user: str
    hostname: str
    port: str
    proxy_jump: str | None
    proxy_command: str | None

    @property
    def via(self) -> str | None:
        """The hop the target is reached through, if any: the target then sees that hop's address."""
        return self.proxy_jump or self.proxy_command


def run(command: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """*command* run to its end, its output captured; a program that is not there is a refusal.

    UTF-8 both ways whatever this machine's locale, since the install script is
    UTF-8. What goes in is encoded strictly — a script that cannot be written
    as it is is not sent at all — and what comes back is read with replacement:
    a byte the target's login files print that is no UTF-8 does not end the run
    after the install ran.

    *command* is an argument list, never a shell line, and nothing from outside
    reaches it as an option: ``ssh``'s destination follows ``--``
    (:func:`ssh_resolve`, :func:`setup_login`), ``ssh-keygen``'s comment is
    built from the sanitized host name, and ``ip``'s address is the numeric one
    ``getaddrinfo`` returned.
    """
    try:
        data = None if stdin is None else stdin.encode("utf-8")
    except UnicodeEncodeError as error:
        raise SetupError(f"{PREFIX}: what the setup would send to {command[0]} is no UTF-8: {error}.") from error
    try:
        result = subprocess.run(command, input=data, capture_output=True, check=False)  # NOSONAR S8705: argument list, no shell; destinations follow "--"
    except FileNotFoundError as error:
        raise SetupError(f"{PREFIX}: the setup needs {command[0]}, and this machine has none on its PATH.") from error
    return subprocess.CompletedProcess(
        result.args,
        result.returncode,
        result.stdout.decode("utf-8", "replace"),
        result.stderr.decode("utf-8", "replace"),
    )


def read_text(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise SetupError(f"{PREFIX}: could not read {path}: {reason(error)}.") from error


def reason(error: OSError | UnicodeError) -> str:
    if isinstance(error, OSError) and error.strerror:
        return error.strerror
    return str(error)


def config_dir() -> pathlib.Path:
    """This machine's directory for remote targets: the key, the SSH config and the known-hosts file."""
    base = os.environ.get("XDG_CONFIG_HOME", "").strip() or os.path.join(os.path.expanduser("~"), ".config")
    return pathlib.Path(base) / CONFIG_DIR_NAME


def host_key_alias(name: str) -> str:
    """The name the target *name*'s host key is recorded under, whatever its address."""
    return f"emu-atlas-dev.{name}"


def is_recorded(known_hosts: str, alias: str) -> bool:
    """Whether a line of *known_hosts* names *alias* among its host names, its first field."""
    for line in known_hosts.splitlines():
        fields = line.split()
        if fields and alias in fields[0].split(","):
            return True
    return False


def build_host_name() -> str:
    """This machine's name, as the comments on both machines give it."""
    return re.sub(r"[^A-Za-z0-9.-]", "-", socket.gethostname()) or "build-host"


def stamp() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")


def python_floor() -> str:
    """The package's Python floor, ``X.Y`` out of ``pyproject.toml``'s ``requires-python``."""
    try:
        requires = tomllib.loads(read_text(PYPROJECT))["project"]["requires-python"]
    except (tomllib.TOMLDecodeError, KeyError) as error:
        raise SetupError(f"{PREFIX}: {PYPROJECT} states no requires-python: {error}.") from error
    match = _FLOOR.fullmatch(str(requires).strip())
    if match is None:
        raise SetupError(f"{PREFIX}: {PYPROJECT}'s requires-python {requires!r} is not of the form '>=X.Y'.")
    return match.group(1)


def setup_request() -> list[str]:
    """The command line both logins send: the install script on stdin, run by the target's interpreter."""
    return [TARGET_PYTHON, "-I", "-"]


def ssh_resolve(destination: str, port: str | None = None) -> dict[str, list[str]]:
    """``ssh -G``'s answer for *destination*: each key with every value it printed."""
    result = run(["ssh", "-G", *(["-p", port] if port else []), "--", destination])
    if result.returncode != 0:
        raise SetupError(f"{PREFIX}: ssh could not read its configuration for {destination}:\n{result.stderr.rstrip()}")
    answer: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        answer.setdefault(key.lower(), []).append(value.strip())
    return answer


def _single(answer: dict[str, list[str]], key: str) -> str | None:
    values = answer.get(key)
    if not values or values[0] in ("", "none"):
        return None
    return values[0]


def resolve(text: str, port: str | None = None) -> Destination:
    answer = ssh_resolve(text, port)
    hostname = _single(answer, _HOSTNAME)
    user = _single(answer, _USER)
    if hostname is None or user is None:
        raise SetupError(f"{PREFIX}: ssh resolved {text} to no host or no user.")
    return Destination(
        text=text,
        user=user,
        hostname=hostname,
        port=_single(answer, _PORT) or "22",
        proxy_jump=_single(answer, _PROXY_JUMP),
        proxy_command=_single(answer, _PROXY_COMMAND),
    )


def resolve_jump(proxy_jump: str) -> str:
    """*proxy_jump* with every hop spelled out as ``user@host:port``, through ``ssh -G``."""
    hops = []
    for hop in proxy_jump.split(","):
        user, host, port = split_hop(hop)
        resolved = resolve(f"{user}@{host}" if user else host, port=port)
        address = f"[{resolved.hostname}]" if ":" in resolved.hostname else resolved.hostname
        hops.append(f"{resolved.user}@{address}:{resolved.port}")
    return ",".join(hops)


def split_hop(hop: str) -> tuple[str | None, str, str | None]:
    """One ProxyJump hop, ``[ssh://][user@]host[:port]``, as its user, host and port.

    Split here rather than handed to ``ssh -G`` whole: ``ssh -G`` takes a
    ``host:port`` for a host name, and an IPv6 address in brackets for one too.
    """
    rest = hop.removeprefix("ssh://")
    user, _, rest = rest.rpartition("@")
    if rest.startswith("["):
        host, _, after = rest[1:].partition("]")
        port = after.removeprefix(":") or None
    elif rest.count(":") == 1:
        host, _, port_text = rest.partition(":")
        port = port_text or None
    else:
        host, port = rest, None
    return user or None, host, port


def existing_key(directory: pathlib.Path) -> pathlib.Path | None:
    """The build host's key for targets, if this machine holds one, checked for its public half."""
    key = directory / KEY_NAME
    if not key.exists():
        return None
    if not (directory / PUBLIC_KEY_NAME).exists():
        raise SetupError(
            f"{PREFIX}: {key} has no {PUBLIC_KEY_NAME} beside it.\n"
            f"  Put it back, or remove {key} so that the setup makes a new key."
        )
    return key


def generate_key(directory: pathlib.Path, comment: str) -> pathlib.Path:
    path = directory / KEY_NAME
    result = run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(path)])
    if result.returncode != 0:
        raise SetupError(f"{PREFIX}: ssh-keygen could not create a key:\n{result.stderr.rstrip()}")
    return path


def install_key(new_key: pathlib.Path, directory: pathlib.Path, written: list[str] | None = None) -> None:
    """The new key's two halves in *directory*, each written beside its place and renamed there.

    The public half goes first: a private key without it is refused
    (:func:`existing_key`), while a public half alone is no key and a new one
    is made. A copy, not a move: a move across file systems is itself a copy,
    which a full disk can leave cut short under the final name. Each half
    written goes into *written*.
    """
    for name, mode in ((PUBLIC_KEY_NAME, 0o644), (KEY_NAME, 0o600)):
        write_atomically(directory / name, new_key.with_name(name).read_bytes(), mode)
        if written is not None:
            written.append(str(directory / name))


def _fill(template: str, line: str, filled: str) -> str:
    if template.count(line) != 1:
        raise SetupError(f"{PREFIX}: {INSTALL_SCRIPT} does not hold the line {line!r} exactly once.")
    return template.replace(line, filled, 1)


def install_script(*, public_key: str, build_host: str, written: str, default_from: str, show_host_keys: bool) -> str:
    """``scripts/dev_target_install.py`` with its values and the dispatcher in place.

    *default_from* empty has the script take the address from ``SSH_CLIENT``,
    the setup login's.
    """
    values = {
        "started": STARTED,
        "python_found": PYTHON_FOUND,
        "installed": INSTALLED,
        "stopped": STOPPED,
        "lacks": LACKS,
        "failed": FAILED,
        "public_key": public_key,
        "build_host": build_host,
        "written": written,
        "target_root": TARGET_ROOT,
        "default_from": default_from,
        "show_host_keys": "yes" if show_host_keys else "no",
        "host_key_dir": HOST_KEY_DIR,
        "python": TARGET_PYTHON,
        "bwrap": TARGET_BWRAP,
        "requires_python": python_floor(),
    }
    script = _fill(read_text(INSTALL_SCRIPT), "VALUES: dict[str, str] = {}  # @VALUES@", f"VALUES = {values!r}")
    return _fill(script, 'DISPATCHER_SOURCE = ""  # @DISPATCHER@', f"DISPATCHER_SOURCE = {read_text(DISPATCHER)!r}")


def target_names(name: str, destination: Destination) -> set[str]:
    """Every name ssh may give the target *name* in a message: its address, its host key alias, its name."""
    return {destination.hostname, host_key_alias(name), name}


def failed_where(stderr: str, *, name: str, destination: Destination) -> Where:
    """Whose failure ssh reported: ``target``, ``hop``, or ``either`` where it names no host.

    A host ssh names is the target's where it is one of :func:`target_names`;
    without a hop, every failure is the target's.
    """
    if not destination.via:
        return "target"
    named = {match.group(1) for pattern in _NAMED_HOSTS for match in pattern.finditer(stderr)}
    if named & target_names(name, destination):
        return "target"
    return "hop" if named else "either"


def _host_key_changed(failure: SshFailure, *, name: str, destination: Destination, ours: tuple[str, ...]) -> str:
    """The message for a host key that is not the one a known-hosts file records.

    The ``ssh-keygen -R`` advice only where the file ssh names is this tool's
    own: a record in the user's files is the user's to judge.
    """
    if not failure.offending or failure.offending in ours:
        return (
            f"{PREFIX}: the host key at {destination.hostname} is not the one recorded for {name} — another machine"
            " may be answering at that address. No connection was made.\n"
            "  If the target was reinstalled, remove its record and run the setup again:\n"
            f"    ssh-keygen -R {host_key_alias(name)} -f {ours[0]}"
        )
    return (
        f"{PREFIX}: a hop on the way to {name} ({destination.via}) has a host key that is not the one"
        f" {failure.offending} records — another machine may be answering there. No connection was made.\n"
        "  Check that hop with your own ssh."
    )


def _unresolved(failure: SshFailure, *, name: str, destination: Destination) -> str:
    if failure.where == "hop":
        return (
            f"{PREFIX}: this machine cannot resolve {failure.host}, a hop on the way to {name}"
            f" ({destination.via}).\n"
            "  Check that hop in your SSH config, or give its address."
        )
    return (
        f"{PREFIX}: this machine cannot resolve {destination.hostname}.\n"
        "  Give the target's address instead, or a name the network resolves."
    )


def _no_server(destination: Destination, where: Where) -> str:
    host, hop = destination.hostname, f"the hop {destination.via}"
    if where == "hop":
        return f"{PREFIX}: no SSH server answers at {hop}.\n  Check that hop with your own ssh."
    if where == "either":
        return (
            f"{PREFIX}: no SSH server answers at {host} port {destination.port}, or at {hop}.\n"
            "  Check the address, that the target runs an SSH server, and that hop."
        )
    return (
        f"{PREFIX}: no SSH server answers at {host} port {destination.port}.\n"
        "  Check the address, and that the target runs an SSH server."
    )


def ssh_failure(stderr: str, *, name: str, destination: Destination) -> SshFailure:
    """What an ``ssh`` to the target *name* that ended with its own failure status said, and whose failure it was.

    The one reading of ssh's stderr: this setup's messages and its hand-over
    from the key's login, and every later ``--target`` call, act on what it
    returns and read the stderr no second time. The patterns are tested in the
    order their comment gives. A name ssh could not resolve is placed by the
    same rule :func:`failed_where` places a named host by, on that one name: the
    hop's unless it is one of :func:`target_names`.
    """
    where = failed_where(stderr, name=name, destination=destination)
    if _HOST_KEY_CHANGED.search(stderr):
        offending = _OFFENDING_FILE.search(stderr)
        return SshFailure("host key changed", where, offending="" if offending is None else offending.group(1))
    if _HOST_KEY_NOT_ACCEPTED.search(stderr):
        return SshFailure("host key not accepted", where)
    if _NO_LOGIN.search(stderr):
        return SshFailure("no login", where, permission_denied=bool(_KEY_REFUSED.search(stderr)))
    unresolved = _UNRESOLVED.search(stderr)
    if unresolved is not None:
        host = unresolved.group(1)
        on_hop = bool(destination.via) and host not in target_names(name, destination)
        return SshFailure("unresolved", "hop" if on_hop else "target", host=host)
    if _NO_SERVER.search(stderr):
        return SshFailure("no server", where)
    return SshFailure("unknown", where)


def failure_text(
    failure: SshFailure, *, name: str, destination: Destination, known_hosts: tuple[pathlib.Path, ...]
) -> str | None:
    """What to tell the user for the *failure* :func:`ssh_failure` read.

    ``None`` for the target's refusal of the login, which ``no_login_message``
    words, and for anything that is no failure of ssh's. A failure ssh names on
    a hop is worded as the hop's; one it names on no host, as the target's or
    the hop's. *known_hosts* are this tool's own files, the recorded one first.
    """
    hop = f"the hop {destination.via}"
    if failure.kind == "unknown":
        return None
    if failure.kind == "host key changed":
        ours = tuple(str(path) for path in known_hosts)
        return _host_key_changed(failure, name=name, destination=destination, ours=ours)
    if failure.kind == "host key not accepted":
        host = destination.hostname
        wording: dict[Where, str] = {"target": f"at {host}", "hop": f"of {hop}", "either": f"of {host} or of {hop}"}
        return (
            f"{PREFIX}: the host key {wording[failure.where]} was not accepted, or ssh could not ask about it — it"
            " asks on a terminal. No connection was made."
        )
    if failure.kind == "no login":
        if failure.where == "hop":
            return f"{PREFIX}: {hop} accepted no login from this machine.\n  Check that hop with your own ssh."
        return None
    if failure.kind == "unresolved":
        return _unresolved(failure, name=name, destination=destination)
    if failure.kind == "no server":
        return _no_server(destination, failure.where)
    assert_never(failure.kind)


def route_source(destination: Destination) -> str:
    """The address this machine sends from towards the target (``ip route get``)."""
    try:
        infos = socket.getaddrinfo(destination.hostname, destination.port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as error:
        raise SetupError(
            f"{PREFIX}: this machine cannot resolve {destination.hostname}.\n"
            "  Give the target's address instead, or a name the network resolves.",
            why=f"this machine cannot resolve {destination.hostname}",
        ) from error
    address = str(infos[0][4][0])
    result = run(["ip", "route", "get", address])
    words = result.stdout.split()
    if result.returncode != 0 or "src" not in words or words.index("src") + 1 >= len(words):
        said = " ".join((result.stderr or result.stdout).split())
        raise SetupError(
            f"{PREFIX}: `ip route get {address}` named no address this machine sends from towards the target:\n"
            f"{(result.stderr or result.stdout).rstrip()}",
            why=f"`ip route get {address}` named no address this machine sends from; it said: {said}",
        )
    return words[words.index("src") + 1]


def _marked(stream: str, marker: str) -> str | None:
    """The rest of the first line of *stream* that starts with *marker*."""
    return next((line[len(marker) :] for line in stream.splitlines() if line.startswith(marker)), None)


def _target_said(result: subprocess.CompletedProcess[str], name: str) -> str | None:
    """The message for an install that started: lacking something, stopped, failed on the way, or part-way.

    The first three are lines the install wrote itself; a stopped or a failed
    install names what it left in place. An install that started and ended
    without such a line — killed, or its connection lost — stopped part-way.
    """
    lacking = _marked(result.stderr, LACKS)
    if lacking is not None:
        return f"{PREFIX}: {name} lacks {lacking}.\n  Nothing was changed, here or on the target."
    stopped = _marked(result.stderr, STOPPED)
    if stopped is not None:
        return f"{PREFIX}: the setup stopped on {name}: {stopped}\n  This machine is unchanged."
    failure = _marked(result.stderr, FAILED)
    if failure is not None:
        return (
            f"{PREFIX}: the setup failed on {name}: {failure}\n"
            "  This machine is unchanged; run the setup again once the cause above is fixed."
        )
    if STARTED in result.stdout.splitlines():
        return (
            f"{PREFIX}: the setup stopped part-way on {name} (ssh ended with {result.returncode}):\n"
            f"{result.stderr.rstrip()}\n"
            "  This machine is unchanged; run the setup again to finish it."
        )
    return None


def failure_message(
    result: subprocess.CompletedProcess[str],
    *,
    name: str,
    destination: Destination,
    key_refused: bool,
    known_hosts: tuple[pathlib.Path, ...],
) -> str:
    """What to tell the user for a setup login that did not end with the install done.

    Only a run whose install never started and whose ssh ended with its own
    failure status is a failure before the login, which changed nothing.
    """
    said = _target_said(result, name)
    if said is not None:
        return said
    nothing = "\n  Nothing was changed, here or on the target."
    if result.returncode == NOT_FOUND:
        return (
            f"{PREFIX}: {name} could not start {TARGET_PYTHON} (the shell there ended with {NOT_FOUND}, a command"
            f" it cannot find):\n{result.stderr.rstrip()}\n"
            f"  The setup needs Python {python_floor()} or newer at {TARGET_PYTHON} there." + nothing
        )
    if result.returncode == SSH_FAILED:
        reading = ssh_failure(result.stderr, name=name, destination=destination)
        failure = failure_text(reading, name=name, destination=destination, known_hosts=known_hosts)
        if failure is None and reading.kind == "no login":
            failure = no_login_message(destination, key_refused=key_refused)
        if failure is not None:
            return failure + nothing
    return (
        f"{PREFIX}: the setup did not start on {name} (ssh ended with {result.returncode}):\n"
        f"{result.stderr.rstrip()}" + nothing
    )


def no_login_message(destination: Destination, *, key_refused: bool) -> str:
    refused = f"{PREFIX}: {destination.text} accepted no login from this machine.\n"
    if destination.via:
        return (
            refused + f"  --print cannot set up a target reached through {destination.via}. Give the target a login"
            " of yours first — a key of yours in its ~/.ssh/authorized_keys, or a password — then run the setup"
            " here again."
        )
    message = (
        refused + "  Run the setup with --print, run the script it prints on the target, then run the setup here again."
    )
    if not key_refused:
        return message
    try:
        address = route_source(destination)
    except SetupError as error:
        return message + (
            "\n  The target refused this machine's key as well. (The address a script --print printed would"
            f" restrict the key to cannot be shown here: {error.why or error})"
        )
    return (
        message + "\n  The target refused this machine's key as well. If it was set up with the script --print"
        " printed, the likely cause is the address that script restricts the key to — by default"
        f" {address}, the one this machine sends from towards the target. If the target sees this machine at"
        " another, run that script there again with that address as its argument."
    )


def write_atomically(path: pathlib.Path, data: str | bytes, mode: int = 0o600) -> None:
    """*data* at *path*, written beside it, synced to disk and renamed over it; nothing half-written is left."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.new")
    try:
        with open(partial, "wb", opener=lambda p, flags: os.open(p, flags, mode)) as handle:
            handle.write(data.encode("utf-8") if isinstance(data, str) else data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(partial, mode)
        os.replace(partial, path)
    except BaseException:
        with contextlib.suppress(OSError):
            partial.unlink(missing_ok=True)
        raise


def config_entry(name: str, destination: Destination, *, key: pathlib.Path, known_hosts: pathlib.Path) -> list[str]:
    """The lines of a target's entry in this tool's SSH config, without its comment.

    A ProxyJump goes in resolved (:func:`resolve_jump`); a ProxyCommand as
    ``ssh -G`` printed it, a ``%h`` or ``%p`` left in it expanding against this
    entry's HostName and Port when it runs.
    """
    lines = [
        f"Host {name}",
        f"    HostName {destination.hostname}",
        f"    Port {destination.port}",
        f"    User {destination.user}",
    ]
    if destination.proxy_jump:
        lines.append(f"    ProxyJump {resolve_jump(destination.proxy_jump)}")
    elif destination.proxy_command:
        lines.append(f"    ProxyCommand {destination.proxy_command}")
    lines += [
        f"    HostKeyAlias {host_key_alias(name)}",
        f'    IdentityFile "{key}"',
        "    IdentitiesOnly yes",
        f'    UserKnownHostsFile "{known_hosts}"',
        "    GlobalKnownHostsFile /dev/null",
        "    StrictHostKeyChecking yes",
        "    BatchMode yes",
    ]
    return lines


def updated_config(current: str, name: str, entry: list[str], comment: str) -> str:
    """*current* with *name*'s entry set to *entry*, and only one entry of that name left.

    An entry that says the same keeps its comment; every other block stays as
    it was, and only the header's own lines are dropped, to be written afresh.
    """
    kept: list[str] = []
    seen = False
    for lines in config_blocks(current):
        body = [line for line in lines if not line.startswith("#")]
        if body[:1] != [f"Host {name}"]:
            kept.append("\n".join(lines))
        elif not seen:
            seen = True
            kept.append("\n".join(lines) if body == entry else "\n".join([comment, *entry]))
    if not seen:
        kept.append("\n".join([comment, *entry]))
    return _CONFIG_HEADER + "\n" + "\n\n".join(kept) + "\n"


def entry_comment(name: str, build_host: str, written: str) -> str:
    return f"# {name}: written by setup:remote-target on {build_host}, {written}. Running it again changes it."


def config_blocks(text: str) -> list[list[str]]:
    """The blocks of this tool's SSH config *text* as :func:`updated_config` writes them, each a list of lines.

    A blank line ends a block; the header's own lines are left out, and a block
    with nothing left is dropped.
    """
    header = set(_CONFIG_HEADER.splitlines())
    blocks = []
    for block in text.strip("\n").split("\n\n"):
        lines = [line for line in block.split("\n") if line not in header]
        if any(line.strip() for line in lines):
            blocks.append(lines)
    return blocks


def _unquoted(value: str) -> str:
    """*value* without the double quotes around it, where it is one quoted word as :func:`config_entry` writes a
    path; any other value, a proxy command that quotes words of its own among them, as it stands."""
    if len(value) >= 2 and value[0] == value[-1] == '"' and '"' not in value[1:-1]:
        return value[1:-1]
    return value


def _entry_settings(config: str, name: str) -> dict[str, str] | None:
    """The settings of *name*'s block in *config*, keys lowercased, the first value of a key kept as ssh keeps it;
    ``None`` where *config* has no block for *name*."""
    for lines in config_blocks(config):
        body = [line.split(maxsplit=1) for line in lines if line.strip() and not line.lstrip().startswith("#")]
        if body[:1] != [["Host", name]]:
            continue
        settings: dict[str, str] = {}
        for words in body[1:]:
            if len(words) == 2:
                settings.setdefault(words[0].lower(), _unquoted(words[1].strip()))
        return settings
    return None


def read_entry(config: str, name: str) -> Destination | None:
    """*name*'s entry in this tool's SSH config *config*, read back as :func:`config_entry` writes it.

    ``None`` where *config* holds no entry for *name*; one that names no
    address or no user is refused with a :class:`SetupError`.
    :attr:`Destination.text` is the target's name, the one every ``--target``
    call gives ``ssh``.
    """
    settings = _entry_settings(config, name)
    if settings is None:
        return None
    if _HOSTNAME not in settings or _USER not in settings:
        raise SetupError(f"{PREFIX}: the entry for {name} names no HostName or no User.")
    return Destination(
        text=name,
        user=settings[_USER],
        hostname=settings[_HOSTNAME],
        port=settings.get(_PORT, "22"),
        proxy_jump=settings.get(_PROXY_JUMP),
        proxy_command=settings.get(_PROXY_COMMAND),
    )


def check_name(name: str) -> None:
    """Refuse a target name ``ssh`` could take for something else: letters, digits, ``.``, ``_`` and ``-`` only."""
    if not _NAME.fullmatch(name):
        raise SetupError(f"{PREFIX}: a target's name is letters, digits, '.', '_' and '-' — not {name!r}.")


def check_arguments(name: str, destination: str) -> None:
    check_name(name)
    if not destination or destination.startswith("-"):
        raise SetupError(f"{PREFIX}: {destination!r} is no destination ssh could reach.")


def run_print(name: str, destination_text: str) -> int:
    destination = resolve(destination_text)
    if destination.via:
        raise SetupError(
            f"{PREFIX}: --print cannot set up a target reached through {destination.via}: the target sees that"
            " hop's address, which this machine cannot know.\n"
            "  Run the setup without --print, through a login the target accepts."
        )
    address = route_source(destination)
    directory = config_dir()
    build_host = build_host_name()
    floor = python_floor()
    if existing_key(directory) is None:
        with tempfile.TemporaryDirectory(prefix="emu-atlas-dev-setup-") as scratch:
            new_key = generate_key(pathlib.Path(scratch), f"emu-atlas-dev@{build_host}")
            try:
                install_key(new_key, directory)
            except OSError as error:
                raise SetupError(f"{PREFIX}: could not put the new key in {directory}: {reason(error)}.") from error
    public_key = read_text(directory / PUBLIC_KEY_NAME).strip()
    sys.stdout.write(
        install_script(
            public_key=public_key, build_host=build_host, written=stamp(), default_from=address, show_host_keys=True
        )
    )
    print(
        f"{PREFIX}: the script above sets {name} up; it restricts this machine's key to {address}.\n"
        f"  It needs Python {floor} or newer at {TARGET_PYTHON} there, and {TARGET_BWRAP}.\n"
        f"  Save it, copy it to the target and run it there with `{TARGET_PYTHON} <file>` — or\n"
        f"  `{TARGET_PYTHON} <file> <address>` if the target sees this machine at another address.\n"
        "  Then run here again, without --print:\n"
        f"    mise run setup:remote-target {name} {shlex.quote(destination_text)}",
        file=sys.stderr,
    )
    return 0


def setup_login(
    destination: Destination,
    *,
    name: str,
    script: str,
    known_hosts: pathlib.Path,
    key_config: pathlib.Path | None,
    host_key_recorded: bool,
) -> subprocess.CompletedProcess[str]:
    """One login with *script* on its stdin: with the build host's key alone, or with the user's own access.

    The key's login reads *key_config* alone, an entry :func:`config_entry`
    built for the key and *known_hosts*, so nothing of the user's SSH config —
    another IdentityFile among it — takes part: an IdentityFile given with
    ``-o`` would replace ssh's default identities rather than add to them, and
    a key offered after the user's agent keys could meet the server's
    MaxAuthTries before it is tried. The user's own login reads the user's
    config, *known_hosts* given beside it, with ``BatchMode=no`` whatever that
    config says, so a password can be asked.

    Every login asks about a host key it has not recorded, whatever a config
    says, and opens a connection of its own: one shared through a ControlMaster
    would skip the host key check that fills *known_hosts*. The key's login
    asks for nothing but that host key — no password, no passphrase — and once
    the host key is recorded runs in batch mode, so it asks for nothing at all.
    Before that it cannot: the first login after ``--print`` has to show the
    fingerprint for the user to compare.
    """
    options = ["StrictHostKeyChecking=ask", "HashKnownHosts=no", "ControlMaster=no", "ControlPath=none"]
    if key_config is None:
        command = ["ssh", "-T"]
        options += [
            f'UserKnownHostsFile="{known_hosts}"',
            "GlobalKnownHostsFile=/dev/null",
            f"HostKeyAlias={host_key_alias(name)}",
            "BatchMode=no",
        ]
        target = destination.text
    else:
        command = ["ssh", "-F", str(key_config), "-T"]
        options += [
            "IdentitiesOnly=yes",
            "PreferredAuthentications=publickey",
            f"BatchMode={'yes' if host_key_recorded else 'no'}",
        ]
        target = name
    for option in options:
        command += ["-o", option]
    command += ["--", target, *setup_request()]
    return run(command, stdin=script)


def installed_from(result: subprocess.CompletedProcess[str]) -> str | None:
    """The address the install restricted the key to, once it ran to its end."""
    return _marked(result.stdout, INSTALLED)


def hand_over(result: subprocess.CompletedProcess[str], *, name: str, destination: Destination) -> HandOver | None:
    """Why the key's login gives way to the user's own, or ``None`` when its result stands.

    ``refused`` for the target's refusal of the key; ``forced command`` for a
    key the target took whose forced command ran no install (a dispatcher gone,
    or one that refuses the setup request); ``hop`` for a failure on a hop, which
    the user's own config may pass. Any other failure, and an install that
    started, stand: the user's own login would meet them too. A
    ``Permission denied (`` counts only where ssh ended with its own failure
    status: the remote shell's login files write to the same stderr.
    """
    if installed_from(result) is not None or STARTED in result.stdout.splitlines():
        return None
    if result.returncode != SSH_FAILED:
        return FORCED_COMMAND
    failure = ssh_failure(result.stderr, name=name, destination=destination)
    if failure.where == "target" and failure.permission_denied:
        return "refused"
    if failure.where == "hop":
        return "hop"
    return None


@dataclass(frozen=True, kw_only=True)
class Plan:
    """What one setup run works with, gathered before any login."""

    name: str
    destination: Destination
    directory: pathlib.Path
    has_key: bool
    """Whether this machine already holds its key for targets, so the key's login comes first."""
    build_host: str
    written: str

    @property
    def key(self) -> pathlib.Path:
        return self.directory / KEY_NAME

    @property
    def known_hosts(self) -> pathlib.Path:
        return self.directory / KNOWN_HOSTS_NAME

    @property
    def ssh_config(self) -> pathlib.Path:
        return self.directory / SSH_CONFIG_NAME


_HANDING_OVER: dict[HandOver, str] = {
    "refused": "refused this machine's key",
    FORCED_COMMAND: "took this machine's key, but its forced command ran no install",
    "hop": "could not be reached with this machine's key: a hop on the way stopped the login",
}


def log_in_and_install(
    plan: Plan, scratch: pathlib.Path, public_key: str, recorded: str
) -> subprocess.CompletedProcess[str]:
    """Install through the key's login, else the user's own; the login that installed.

    Both logins record a host key into a trial copy of the known-hosts file in
    *scratch*, which only a finished install puts in place.
    """
    trial_known_hosts = scratch / KNOWN_HOSTS_NAME
    trial_known_hosts.write_text(recorded, encoding="utf-8")  # NOSONAR S2083: fixed name in this run's temp dir; text is content
    host_key_recorded = is_recorded(recorded, host_key_alias(plan.name))
    if not host_key_recorded:
        print(
            f"{PREFIX}: first contact with {plan.name}: ssh shows its host key fingerprint. If you ran the script"
            " --print printed, go on only if it is one that script showed.",
            file=sys.stderr,
        )
    script = install_script(
        public_key=public_key, build_host=plan.build_host, written=plan.written, default_from="", show_host_keys=False
    )

    def log_in(key_config: pathlib.Path | None) -> subprocess.CompletedProcess[str]:
        return setup_login(
            plan.destination,
            name=plan.name,
            script=script,
            known_hosts=trial_known_hosts,
            key_config=key_config,
            host_key_recorded=host_key_recorded,
        )

    handed_over = None
    key_result = None
    if plan.has_key:
        key_config = scratch / SSH_CONFIG_NAME
        entry = config_entry(plan.name, plan.destination, key=plan.key, known_hosts=trial_known_hosts)
        key_config.write_text("\n".join(entry) + "\n", encoding="utf-8")
        key_result = log_in(key_config)
        handed_over = hand_over(key_result, name=plan.name, destination=plan.destination)
        if handed_over is not None:
            print(
                f"{PREFIX}: {plan.name} {_HANDING_OVER[handed_over]}; logging in with your own access instead.",
                file=sys.stderr,
            )
    result = log_in(None) if key_result is None or handed_over is not None else key_result

    if installed_from(result) is None:
        message = failure_message(
            result,
            name=plan.name,
            destination=plan.destination,
            key_refused=handed_over == "refused",
            known_hosts=(plan.known_hosts, trial_known_hosts),
        )
        if handed_over == FORCED_COMMAND and key_result is not None:
            message += (
                f"\n  {plan.name} accepted this machine's key, but its forced command failed"
                f" (ssh ended with {key_result.returncode}):\n{key_result.stderr.rstrip()}"
            )
        raise SetupError(message)
    return result


def record_here(plan: Plan, final_entry: list[str], new_key: pathlib.Path | None, trial: str, recorded: str) -> None:
    """This machine's side, once the target is set up: the key, the known-hosts file and the config entry."""
    written: list[str] = []
    try:
        if new_key is not None:
            install_key(new_key, plan.directory, written)
        if trial != recorded:
            write_atomically(plan.known_hosts, trial)
            written.append(str(plan.known_hosts))
        current = plan.ssh_config.read_text(encoding="utf-8") if plan.ssh_config.exists() else ""
        config = updated_config(current, plan.name, final_entry, entry_comment(plan.name, plan.build_host, plan.written))
        if config != current:
            write_atomically(plan.ssh_config, config)
    except (OSError, UnicodeError) as error:
        where = getattr(error, "filename", None) or plan.directory
        already = f"\n  Already written here: {', '.join(written)}." if written else ""
        raise SetupError(
            f"{PREFIX}: {plan.name} is set up, but this machine's side could not be written — {where}:"
            f" {reason(error)}.{already}\n"
            "  Run the setup again to finish it."
        ) from error


def run_setup(name: str, destination_text: str) -> int:
    destination = resolve(destination_text)
    directory = config_dir()
    plan = Plan(
        name=name,
        destination=destination,
        directory=directory,
        has_key=existing_key(directory) is not None,
        build_host=build_host_name(),
        written=stamp(),
    )
    final_entry = config_entry(name, destination, key=plan.key, known_hosts=plan.known_hosts)

    with tempfile.TemporaryDirectory(prefix="emu-atlas-dev-setup-") as scratch_name:
        scratch = pathlib.Path(scratch_name)
        new_key = None if plan.has_key else generate_key(scratch, f"emu-atlas-dev@{plan.build_host}")
        public_key = read_text((new_key or plan.key).with_name(PUBLIC_KEY_NAME)).strip()
        recorded = read_text(plan.known_hosts) if plan.known_hosts.exists() else ""
        try:
            result = log_in_and_install(plan, scratch, public_key, recorded)
        except KeyboardInterrupt:
            raise SetupError(
                f"{PREFIX}: interrupted during the setup login to {name}. The target may be set up part-way;"
                " this machine is unchanged. Run the setup again to finish it."
            ) from None
        trial = (scratch / KNOWN_HOSTS_NAME).read_text(encoding="utf-8")
        try:
            record_here(plan, final_entry, new_key, trial, recorded)
        except KeyboardInterrupt:
            raise SetupError(
                f"{PREFIX}: interrupted while recording {name} here, after the target was set up. This machine's"
                f" files under {directory} may be part-way; run the setup again to finish it."
            ) from None

    print(set_up_message(name, result), file=sys.stderr)
    return 0


def set_up_message(name: str, result: subprocess.CompletedProcess[str]) -> str:
    """What the setup says once the target is set up: the address the key is bound to, and the target's Python.

    Below 3.14 the standard library has no zstd codec, which leaves the
    answers read out of a zstd AppImage sealed on that target.
    """
    version = _marked(result.stdout, PYTHON_FOUND)
    message = (
        f"{PREFIX}: {name} is set up. It accepts this machine's key from {installed_from(result)} only — the"
        " address it sees this machine at; if that changes, run the setup again.\n"
    )
    if version is None:
        return message + (
            f"  The install did not report the version of its {TARGET_PYTHON}, so whether zstd answers stay sealed"
            " there is not known."
        )
    message += f"  Its {TARGET_PYTHON} is Python {version}."
    numbers = tuple(int(part) for part in re.findall(r"\d+", version, re.ASCII)[:2])
    if numbers and numbers < ZSTD_IN_STDLIB:
        message += (
            " Below 3.14 its standard library has no zstd codec: the answers read out of a zstd AppImage"
            " (EmuDeck's ES-DE) stay sealed there (docs/remote-target.md)."
        )
    return message


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="the name to give the target: letters, digits, '.', '_' and '-'")
    parser.add_argument("destination", help="user@address, or an alias from your SSH config")
    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_script",
        help="print a script to run on the target instead of logging in",
    )
    arguments = parser.parse_args(argv)
    try:
        check_arguments(arguments.name, arguments.destination)
        if arguments.print_script:
            return run_print(arguments.name, arguments.destination)
        return run_setup(arguments.name, arguments.destination)
    except SetupError as refusal:
        print(refusal, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
