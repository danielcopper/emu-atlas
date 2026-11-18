"""Installs this machine as a remote target of one build host for emu-atlas.

setup:remote-target sends this script through the setup login, or prints it
with --print, with VALUES and DISPATCHER_SOURCE filled in where their lines
stand. What it leaves: docs/remote-target.md, "What the setup does".

    /usr/bin/python3 <this file> [address]

Restricts the build host's key to [address], the address this machine sees
the build host at; without one, to the address shown before anything is
written. Running it again changes what changed.

Before it writes anything it checks the two things every probe needs here: the
interpreter at VALUES["python"] at or above the floor, and VALUES["bwrap"]
able to start the sandbox the dispatcher starts every probe in. The file keeps
to Python 3.8's grammar and to standard-library names that exist there, so
that an older interpreter reaches that check instead of dying on a
``SyntaxError``.

What the build host reads off the run: STARTED on stdout before anything else,
PYTHON_FOUND with the interpreter's version once it passed the floor,
INSTALLED with the address once the install is done; on stderr, LACKS with
what this machine lacks (nothing was changed), STOPPED with why the install
refused to go on (nothing was moved into place), or FAILED with an error on
the way and what was and was not moved into place.
"""

from __future__ import annotations

import base64
import binascii
import errno
import glob
import hashlib
import os
import re
import shlex
import stat
import subprocess
import sys
from typing import Callable, cast

VALUES: dict[str, str] = {}  # @VALUES@
DISPATCHER_SOURCE = ""  # @DISPATCHER@

_ADDRESS = re.compile(r"[0-9A-Fa-f.:]+")
_UNSAFE_IN_OPTION = re.compile(r'["\\\x00-\x1f\x7f]')
_VERSION = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)")
_VERSION_PROBE = "import sys; sys.stdout.write('%d.%d.%d' % sys.version_info[:3])"

DISPATCHER_NAME = "dispatch.py"
KEYS_SUFFIX = ".emu-atlas-dev.new"


class Stop(Exception):
    """The install ends here: *line* goes to stderr, after its marker."""

    def __init__(self, marker: str, line: str) -> None:
        super().__init__(line)
        self.marker = marker
        self.line = line


def value(name: str) -> str:
    return VALUES[name]


def stopped(line: str) -> Stop:
    return Stop(value("stopped"), line)


def failed(line: str) -> Stop:
    return Stop(value("failed"), line)


def lacks(line: str) -> Stop:
    return Stop(value("lacks"), line)


def python_version(python: str) -> tuple[tuple[int, int, int] | None, str]:
    """The version of the interpreter at *python*, or ``None`` and what came back instead."""
    try:
        result = subprocess.run(
            [python, "-I", "-S", "-c", _VERSION_PROBE], stdin=subprocess.DEVNULL, capture_output=True, check=False
        )
    except FileNotFoundError:
        return None, "there is none"
    except OSError as error:
        return None, "it did not start: {}".format(error.strerror or error)
    match = _VERSION.fullmatch(result.stdout.decode("ascii", "replace").strip())
    if result.returncode != 0 or match is None:
        said = result.stderr.decode("utf-8", "replace").strip() or "status {}".format(result.returncode)
        return None, "it did not say its version: {}".format(said)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3))), ""


def check_python(python: str, floor_text: str) -> str:
    """The version of the interpreter at *python*, refused below *floor_text* with both versions named."""
    floor = tuple(int(part) for part in floor_text.split("."))
    version, why = python_version(python)
    if version is None:
        raise lacks("Python {} or newer at {} — {}".format(floor_text, python, why))
    text = ".".join(str(part) for part in version)
    if version[:2] < floor:
        raise lacks("Python {} or newer at {} — it has {}".format(floor_text, python, text))
    return text


def dispatcher_function(name: str) -> Callable[..., object]:
    """A function of the dispatcher this script installs, read from its source rather than written twice."""
    namespace: dict[str, object] = {"__name__": "emu_atlas_dev_dispatch"}
    exec(compile(DISPATCHER_SOURCE, DISPATCHER_NAME, "exec"), namespace)
    return cast("Callable[..., object]", namespace[name])


def check_sandbox(bwrap: str, python: str) -> None:
    """Refuse unless *bwrap* starts the dispatcher's sandbox here, with *python* in it: the dispatcher's own trial."""
    failure = dispatcher_function("sandbox_failure")(bwrap, python)
    if failure is not None:
        repair = "install bubblewrap"
        if failure not in ("there is none", "it is not executable"):
            repair += ", or allow unprivileged user namespaces"
        raise lacks("a {} that starts a sandbox — {}; {}".format(bwrap, failure, repair))


def from_address(argv: list[str]) -> str:
    """The address the key is restricted to: the argument, else the printed default, else ``SSH_CLIENT``'s.

    Checked to be nothing but an address — hex digits, ``.`` and ``:`` — since
    it is written into the key's ``from="…"`` option, where anything else
    could add options of its own.
    """
    address = argv[0] if argv else value("default_from")
    if not address:
        address = os.environ.get("SSH_CLIENT", "").split(" ")[0]
    if not _ADDRESS.fullmatch(address):
        raise stopped('no address to restrict the key to (got "{}").'.format(address))
    return address


def check_option_safe(path: str) -> None:
    if _UNSAFE_IN_OPTION.search(path):
        raise stopped("{!r} cannot be written into the key's command= option safely.".format(path))


def is_ours(line: bytes, blob: bytes, comment: bytes) -> bool:
    """Whether *line* of ``authorized_keys`` is this build host's: its key, its comment, or the comment line above."""
    fields = line.split()
    if not fields:
        return False
    return blob in line or fields[-1] == comment or fields[:2] == [b"#", comment + b":"]


def rebuilt_keys(old: bytes, comment_line: bytes, line: bytes) -> tuple[bytes, int]:
    """*old* less every line of this build host's key, plus *comment_line* and *line*; and how many lines went."""
    public_key = value("public_key").encode("utf-8")
    blob = public_key.split()[1]
    comment = public_key.split()[-1]
    kept = []
    ours = 0
    for each in old.splitlines(keepends=True):
        if is_ours(each, blob, comment):
            ours += 1
        else:
            kept.append(each if each.endswith(b"\n") else each + b"\n")
    return b"".join(kept) + comment_line + b"\n" + line + b"\n", ours


def write_new(path: str, data: bytes, mode: int) -> None:
    """*data* at *path*, on the disk before it is renamed into place: a rename that outlives a crash must not
    name a file whose contents did not."""
    with open(path, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, mode)


def sync_directory(path: str) -> None:
    """The renames into *path* on the disk, not only in the page cache.

    A file system that cannot sync a directory (some FUSE and network ones
    answer ``EINVAL`` or ``EBADF``) is no failure: there is nothing more to do.
    """
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    except OSError as error:
        if error.errno not in (errno.EINVAL, errno.EBADF):
            raise
    finally:
        os.close(descriptor)


class Progress:
    """What one install has changed so far: what a failure undoes, and what it names where it cannot."""

    def __init__(self, keys: str) -> None:
        self.keys = keys
        """The ``authorized_keys`` this install rebuilds."""
        self.created: list[str] = []
        """Directories this install made, outermost first."""
        self.moved: list[str] = []
        """Files renamed into place, in order."""
        self.fresh: list[str] = []
        """Of those, the ones that were not there before."""
        self.unconfirmed: list[str] = []
        """Of those, the ones whose rename did not reach the disk for certain."""
        self.left_behind: list[str] = []
        """New files beside their places that could not be removed, each with why."""

    def make_directories(self, path: str) -> None:
        """*path* and every missing directory above it, each recorded as this install's."""
        missing = []
        while not os.path.isdir(path):
            missing.append(path)
            path = os.path.dirname(path)
        for directory in reversed(missing):
            os.mkdir(directory, 0o700)
            self.created.append(directory)

    def move_into_place(self, new: str, path: str) -> None:
        """Rename *new* over *path*, then sync the directory; a rename that went through is recorded first."""
        was_there = os.path.lexists(path)
        os.replace(new, path)
        self.moved.append(path)
        if not was_there:
            self.fresh.append(path)
        try:
            sync_directory(os.path.dirname(path))
        except OSError:
            self.unconfirmed.append(path)
            raise

    def remove_leftovers(self, paths: tuple[str, ...]) -> None:
        """Remove the new files *paths* where they are left; one that cannot be is recorded with why."""
        for path in paths:
            try:
                if os.path.lexists(path):
                    os.unlink(path)
            except OSError as error:
                self.left_behind.append("left {} behind ({})".format(path, error.strerror or error))

    def undo(self) -> None:
        """Take back what can be: a dispatcher this install put where none was, while ``authorized_keys`` is
        unchanged, and the directories it made, once empty."""
        if self.keys not in self.moved:
            for path in [path for path in self.moved if path in self.fresh]:
                try:
                    os.unlink(path)
                except OSError:
                    continue
                self.moved.remove(path)
        for directory in reversed(list(self.created)):
            try:
                os.rmdir(directory)
            except OSError:
                continue
            self.created.remove(directory)

    def changes(self) -> list[str]:
        """What this install changed besides the files it moved, as phrases after "it"."""
        changed = []
        if self.created:
            which = "which stays" if len(self.created) == 1 else "which stay"
            changed.append("created {}, {}".format(", ".join(self.created), which))
        return changed + self.left_behind

    def state(self) -> str:
        """What a failed install leaves on this machine, once :meth:`undo` took back what it could."""
        parts = [
            "{} was moved into place{}".format(path, ", not confirmed on disk" if path in self.unconfirmed else "")
            for path in self.moved
        ]
        if not parts:
            parts.append("nothing was moved into place")
        if self.keys not in self.moved:
            parts.append("{} {}is as it was".format(self.keys, "was not moved, and " if self.moved else ""))
        changed = self.changes()
        if changed:
            parts.append("it " + ", and ".join(changed))
        return "; ".join(parts) + "."


def check_whole(path: str, lines: int, line: bytes) -> None:
    """Refuse a rebuilt ``authorized_keys`` that came out short, or without the new line last."""
    with open(path, "rb") as handle:
        written = handle.read().splitlines()
    if len(written) != lines or not written or written[-1] != line:
        raise stopped("the new {} did not come out whole, so it was left as it was.".format(path[: -len(KEYS_SUFFIX)]))


def keys_path(home: str) -> str:
    """``~/.ssh/authorized_keys``, or the file it links to: a dotfile manager's link stays a link."""
    keys = os.path.join(home, ".ssh", "authorized_keys")
    return os.path.realpath(keys) if os.path.islink(keys) else keys


def close_ssh_directory(home: str) -> None:
    """``~/.ssh`` closed to others, as sshd wants it: changed only once the install went through."""
    ssh = os.path.join(home, ".ssh")
    if stat.S_IMODE(os.stat(ssh).st_mode) != 0o700:
        os.chmod(ssh, 0o700)


def build_and_move(home: str, dispatcher: str, keys: str, line: bytes, comment_line: bytes, progress: Progress) -> None:
    """Every step of the install, each new file built beside its place and moved there last."""
    progress.make_directories(os.path.dirname(dispatcher))
    progress.make_directories(os.path.dirname(keys))
    write_new(dispatcher + ".new", DISPATCHER_SOURCE.encode("utf-8"), 0o600)
    old = b""
    if os.path.exists(keys):
        with open(keys, "rb") as handle:
            old = handle.read()
    rebuilt, ours = rebuilt_keys(old, comment_line, line)
    write_new(keys + KEYS_SUFFIX, rebuilt, 0o600)
    check_whole(keys + KEYS_SUFFIX, len(old.splitlines()) - ours + 2, line)
    progress.move_into_place(dispatcher + ".new", dispatcher)
    progress.move_into_place(keys + KEYS_SUFFIX, keys)
    close_ssh_directory(home)


def install(home: str, address: str) -> None:
    """The dispatcher and the key's line, each built beside its place and moved there last.

    An install that does not finish takes back what it can (:meth:`Progress.undo`)
    and ends with a STOPPED or FAILED line that names what it leaves, so the
    build host can pass that on.
    """
    root = os.path.join(home, value("target_root"))
    dispatcher = os.path.join(root, DISPATCHER_NAME)
    check_option_safe(dispatcher)
    check_option_safe(value("python"))
    public_key = value("public_key")
    command = "{} -I {}".format(shlex.quote(value("python")), shlex.quote(dispatcher))
    line = 'from="{}",command="{}",restrict {}'.format(address, command, public_key).encode("utf-8")
    comment_line = "# {}: written by setup:remote-target from {}, {}. Running it again changes it.".format(
        public_key.split()[-1], value("build_host"), value("written")
    ).encode("utf-8")
    keys = keys_path(home)
    leftovers = (dispatcher + ".new", keys + KEYS_SUFFIX)
    progress = Progress(keys)
    try:
        build_and_move(home, dispatcher, keys, line, comment_line, progress)
    except Stop as stop:
        progress.remove_leftovers(leftovers)
        progress.undo()
        changed = progress.changes()
        if progress.moved:
            suffix = " " + progress.state()
        else:
            suffix = " It " + ", and ".join(changed) + "." if changed else ""
        raise Stop(stop.marker, stop.line + suffix) from None
    except Exception as error:
        progress.remove_leftovers(leftovers)
        progress.undo()
        raise failed("{}; {}".format(error, progress.state())) from None


def fingerprint(public_key_line: str) -> str | None:
    """``SHA256:…`` of a public key line, as ``ssh`` shows a host key at first contact."""
    fields = public_key_line.split()
    try:
        blob = base64.b64decode(fields[1], validate=True)
    except (IndexError, binascii.Error):
        return None
    digest = base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii").rstrip("=")
    return "SHA256:{} ({})".format(digest, fields[0])


def show_host_keys(directory: str) -> None:
    print(
        "\nThis machine's host key fingerprints. The setup run again on {} shows one of them\n"
        "before it connects for the first time; go on only if it is one of these:".format(value("build_host"))
    )
    shown = 0
    for path in sorted(glob.glob(os.path.join(directory, "ssh_host_*_key.pub"))):
        try:
            with open(path, encoding="utf-8") as handle:
                shown_line = fingerprint(handle.read())
        except (OSError, UnicodeError) as error:
            print("  {}: could not be read ({})".format(path, error))
            continue
        if shown_line is None:
            print("  {}: no public key this script can read".format(path))
            continue
        print("  " + shown_line)
        shown += 1
    if not shown:
        print("  none found in {} — is an SSH server set up here?".format(directory))
    print()


def run(argv: list[str]) -> None:
    print(value("started"), flush=True)
    version = check_python(value("python"), value("requires_python"))
    print(value("python_found") + version, flush=True)
    check_sandbox(value("bwrap"), value("python"))
    address = from_address(argv)
    sys.stderr.write(
        "emu-atlas-dev-setup: the key from {} will be accepted from {} only.\n".format(value("build_host"), address)
    )
    install(os.path.expanduser("~"), address)
    try:
        print(value("installed") + address, flush=True)
        show = value("show_host_keys") == "yes"
    except Exception as error:
        raise failed("the install went through, but then: {}".format(error)) from None
    if show:
        try:
            show_host_keys(value("host_key_dir"))
        except Exception as error:
            print("The host key fingerprints could not be shown: {}".format(error))


def main(argv: list[str]) -> int:
    if not VALUES or not DISPATCHER_SOURCE:
        sys.stderr.write("emu-atlas-dev-setup: this is the template; setup:remote-target fills it in.\n")
        return 2
    os.umask(0o077)
    try:
        run(argv)
    except Stop as stop:
        sys.stderr.write(stop.marker + stop.line + "\n")
        if stop.marker == value("lacks"):
            sys.stderr.write("emu-atlas-dev-setup: nothing was changed.\n")
        return 1
    except Exception as error:
        # Raised before the install: the install and what follows it end
        # with a Stop of their own, so nothing was written.
        marker = VALUES.get("failed", "emu-atlas-dev-setup: failed: ")
        sys.stderr.write("{}{}; nothing was changed.\n".format(marker, error))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
