"""The machine seam — the one injected protocol every machine access flows through.

atlas is a resolver: it answers questions about the running machine by reading
the running machine. All of that reading goes through a :class:`Machine`, which
abstracts the machine itself — files, directories, symlinks, and the answers
only a core binary can give. In production the machine is the real one
(:class:`RealMachine`); in tests and conformance vectors it is a fixture
(:class:`FixtureMachine`): files, directories, symlinks, and core answers as
plain data describing a whole machine, including broken links, unreadable
files, and unloadable cores. One code path, two data sources, so everything
atlas concludes is provable from data — the failure states included.

Every operation reports an explicit outcome instead of collapsing failure
modes: ``read_text`` distinguishes *missing* from *unreadable* from
*invalid text*, and ``path_kind`` distinguishes a file from a directory from
an inaccessible path. The resolver needs those distinctions because the
emulators make them — RetroArch applies a configured directory only when
``path_is_directory()`` succeeds — and because health reporting must never
present a present-but-broken installation as absent or healthy.

``readlink`` exists because RetroDECK's standalone save architecture is symlinks
(``dir_prep``): the emulator-side path and the real path are two truthful
answers to different questions, and a dead link is a real state the resolver
must be able to see. ``query_core`` exists because ``library_name`` — the value
that names sort-by-core directories and the override directory — lives only in
the core binary; loading the core and asking it is the same read RetroArch
performs. A live read, never a shipped table.

``file_size`` and ``file_digest`` exist because firmware identity is checked by
content, not by name: a file present under the right name may still be the
wrong dump. ``file_size`` is the free pre-filter (one ``stat``) that settles
most mismatches before any bytes are hashed; ``file_digest`` is the paid
answer. Both return ``None`` for *cannot tell* — never a sentinel that a caller
could mistake for a real value.

Glob semantics (normative, for ports): patterns support ``*``, ``?`` and
``[seq]`` within one path segment; a wildcard never crosses a ``/``; a wildcard
segment never matches a name starting with ``.`` (only a segment that itself
starts with ``.`` does); matches are returned sorted, spelled the way the
pattern reached them (a file matched through a symlinked directory keeps the
link-side path, like a real filesystem's glob).
"""

from __future__ import annotations

import fnmatch
import glob as _glob
import hashlib
import json
import os
import stat as _stat
import subprocess
import sys
from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Literal, Mapping, Protocol

_CORE_PROBE_TIMEOUT_SECONDS = 15
SYMLINK_HOPS = 40
"""How many symlink hops a path may take before it counts as unresolvable.

Linux answers ``ELOOP`` after 40 (``SYMLOOP_MAX``), and both machines use this
one number so a chain that resolves on the real filesystem resolves in a
fixture and one that does not fails in both. A fixture that disagreed inside
some window would make every vector built on it prove nothing.
"""

# Digest algorithms the seam answers for. Closed on purpose: these are the two
# libretro-database's System.dat carries, and a port must implement exactly
# them (an open algorithm parameter would make conformance unprovable).
DIGEST_MD5 = "md5"
DIGEST_SHA1 = "sha1"
DIGEST_ALGORITHMS = (DIGEST_MD5, DIGEST_SHA1)

_DIGEST_CHUNK_BYTES = 1 << 20

ReadStatus = Literal["ok", "missing", "unreadable", "invalid-text"]
PathKind = Literal["file", "directory", "missing", "inaccessible"]

READ_OK: ReadStatus = "ok"
READ_MISSING: ReadStatus = "missing"
READ_UNREADABLE: ReadStatus = "unreadable"
READ_INVALID_TEXT: ReadStatus = "invalid-text"

KIND_FILE: PathKind = "file"
KIND_DIRECTORY: PathKind = "directory"
KIND_MISSING: PathKind = "missing"
KIND_INACCESSIBLE: PathKind = "inaccessible"


@dataclass(frozen=True, slots=True)
class ReadResult:
    """One text read's explicit outcome — ``text`` is set exactly when ``status`` is ok.

    ``missing`` means the path (or a parent component) does not exist;
    ``unreadable`` means it exists but cannot be read (permissions, a
    directory, I/O error); ``invalid-text`` means bytes exist but are not
    valid UTF-8 text. The distinctions are health signals, never collapsed.
    """

    status: ReadStatus
    text: str | None = None

    def __post_init__(self) -> None:
        if (self.text is None) == (self.status == READ_OK):
            raise ValueError(f"ReadResult: text must be set exactly when status is 'ok' (got {self.status!r})")


@dataclass(frozen=True, slots=True)
class CoreOption:
    """One option definition a core registers: its default and legal values.

    Captured from the registration call the core makes during
    ``retro_set_environment`` — the same declaration RetroArch's option
    manager validates persisted values against. A live read of the binary,
    never shipped data.
    """

    key: str
    default: str | None
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CoreInfo:
    """What a libretro core reports about itself.

    ``library_name`` (via ``retro_get_system_info``) is the value RetroArch
    uses for sort-by-core directories and override directories — the display
    name, not the ``.so`` basename: the two disagree for 183 of the 210 loadable
    cores RetroDECK ships (reference machine, recounted 2026-08-05). ``options``
    is the set of option definitions the core registered during
    ``retro_set_environment`` — the observable fact that identifies a
    core *generation* better than any version string. ``None`` means *not
    captured* (the probe saw no registration — some cores register later, in
    ``retro_init``): unknown, never "registers nothing".
    """

    library_name: str
    library_version: str | None
    valid_extensions: str | None
    options: Mapping[str, CoreOption] | None = None

    def __post_init__(self) -> None:
        if self.options is not None:
            object.__setattr__(self, "options", MappingProxyType(dict(self.options)))


class Machine(Protocol):
    """Narrow machine port: read a file, glob, classify a path, follow links, ask a core.

    Every operation reports an explicit outcome; the caller decides what a
    failure means — the seam never guesses. ``glob`` follows the normative
    semantics in the module docstring. ``readlink`` returns the link target
    when the path itself is a symlink, else ``None``. ``query_core`` returns
    the core's self-reported info, or ``None`` when the core cannot be loaded —
    the caller treats that as *unknown*, never as a guess. ``file_size`` and
    ``file_digest`` answer for regular files only and return ``None`` whenever
    the answer cannot be determined (missing, unreadable, not a regular file,
    or an algorithm outside :data:`DIGEST_ALGORITHMS`).
    """

    def read_text(self, path: str) -> ReadResult: ...

    def glob(self, pattern: str) -> list[str]: ...

    def path_kind(self, path: str) -> PathKind: ...

    def readlink(self, path: str) -> str | None: ...

    def query_core(self, so_path: str) -> CoreInfo | None: ...

    def file_size(self, path: str) -> int | None: ...

    def file_digest(self, path: str, algorithm: str) -> str | None: ...


class RealMachine:
    """The production machine: the real filesystem plus a real core prober.

    ``query_core`` runs the probe in a subprocess (``atlas._core_probe``) so a
    crashing core costs one answer, not the host process, and memoizes per
    ``(path, mtime, size)`` — a cached live read, not shipped data: the cache
    invalidates the moment the ``.so`` changes.
    """

    def __init__(self) -> None:
        self._core_cache: dict[tuple[str, int, int], CoreInfo | None] = {}

    def read_text(self, path: str) -> ReadResult:
        # Regular files only, checked BEFORE opening — for the same reason
        # file_digest checks: opening a FIFO with no writer blocks forever, and
        # these paths come out of config files. A ``.info`` that is a FIFO would
        # hang the whole firmware answer rather than degrade it.
        try:
            st = os.stat(path)
        except (FileNotFoundError, NotADirectoryError):
            return ReadResult(READ_MISSING)
        except OSError:
            return ReadResult(READ_UNREADABLE)
        if not _stat.S_ISREG(st.st_mode):
            # A directory, a FIFO, a device: present, and not readable as text.
            return ReadResult(READ_UNREADABLE)
        try:
            with open(path, encoding="utf-8") as f:
                return ReadResult(READ_OK, f.read())
        except (FileNotFoundError, NotADirectoryError):
            return ReadResult(READ_MISSING)
        except UnicodeDecodeError:
            return ReadResult(READ_INVALID_TEXT)
        except OSError:
            # Permissions, I/O failure: present but unreadable.
            return ReadResult(READ_UNREADABLE)

    def glob(self, pattern: str) -> list[str]:
        return sorted(_glob.glob(pattern))

    def path_kind(self, path: str) -> PathKind:
        try:
            st = os.stat(path)
        except (FileNotFoundError, NotADirectoryError):
            return KIND_MISSING
        except OSError:
            return KIND_INACCESSIBLE
        return KIND_DIRECTORY if _stat.S_ISDIR(st.st_mode) else KIND_FILE

    def readlink(self, path: str) -> str | None:
        try:
            return os.readlink(path) if os.path.islink(path) else None
        except OSError:
            return None

    def file_size(self, path: str) -> int | None:
        try:
            st = os.stat(path)
        except OSError:
            return None
        return st.st_size if _stat.S_ISREG(st.st_mode) else None

    def file_digest(self, path: str, algorithm: str) -> str | None:
        if algorithm not in DIGEST_ALGORITHMS:
            return None
        # Regular files only, checked BEFORE opening: reading a FIFO or a
        # character device blocks forever, and this runs inside a library entry
        # point that hashes whatever a config points at. A hang is not a
        # degraded answer, it is no answer at all.
        try:
            st = os.stat(path)
        except OSError:
            return None
        if not _stat.S_ISREG(st.st_mode):
            return None
        digest = hashlib.new(algorithm)
        try:
            with open(path, "rb") as f:
                while chunk := f.read(_DIGEST_CHUNK_BYTES):
                    digest.update(chunk)
        except OSError:
            # Unreadable, or an I/O failure mid-read: the identity cannot be
            # stated. (A path that stopped being a regular file between the
            # stat and the open lands here too.)
            return None
        return digest.hexdigest()

    def query_core(self, so_path: str) -> CoreInfo | None:
        try:
            st = os.stat(so_path)
        except OSError:
            return None
        key = (so_path, st.st_mtime_ns, st.st_size)
        if key in self._core_cache:
            return self._core_cache[key]
        info = self._probe(so_path)
        # Only successes are memoized: a failure can be transient (missing
        # host library installed later) even while the .so is unchanged.
        if info is not None:
            self._core_cache[key] = info
        return info

    @staticmethod
    def _probe(so_path: str) -> CoreInfo | None:
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "atlas._core_probe", so_path],
                capture_output=True,
                timeout=_CORE_PROBE_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        # The probe prints one JSON object per line and later lines enrich
        # earlier ones (two-phase design) — the last valid line wins, so a
        # crash in the option-capture phase still yields the base answer.
        data: dict[str, object] | None = None
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            try:
                candidate = json.loads(line)
            except ValueError:
                continue
            if isinstance(candidate, dict):
                data = candidate
        if data is None:
            return None
        name = data.get("library_name")
        if not isinstance(name, str) or not name:
            return None
        version = data.get("library_version")
        extensions = data.get("valid_extensions")
        return CoreInfo(
            library_name=name,
            library_version=version if isinstance(version, str) else None,
            valid_extensions=extensions if isinstance(extensions, str) else None,
            options=_parse_core_options(data.get("options")),
        )


def _parse_core_options(raw: object) -> dict[str, CoreOption] | None:
    """Parse a probe's / fixture's option map — ``None`` (not captured) stays ``None``.

    A malformed entry is dropped rather than invented; a wholly malformed map
    counts as not captured.
    """
    if not isinstance(raw, dict):
        return None
    options: dict[str, CoreOption] = {}
    for key, spec in raw.items():
        if not isinstance(key, str) or not key or not isinstance(spec, dict):
            continue
        default = spec.get("default")
        values = spec.get("values")
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            values = []
        options[key] = CoreOption(
            key=key,
            default=default if isinstance(default, str) else None,
            values=tuple(values),
        )
    return options


_GLOB_MAGIC = frozenset("*?[")

# Fixture file specs: a plain string is readable content; an object states a
# non-ok read outcome ({"status": "unreadable"} or {"status": "invalid-text"})
# or a binary blob's identity ({"md5": ..., "sha1": ..., "size": ...}).
#
# A blob is how a fixture states a firmware file: firmware is not text, and its
# identity is exactly the size and digests a real one would answer with — so
# the fixture declares them rather than carrying bytes that would have to hash
# to a real dump's md5. A string-content file needs no declaration: its size
# and digests are computed from the content, so fixture and real machine agree
# by construction.
FixtureFileSpec = str | Mapping[str, str | int]

_BLOB_KEYS = ("size", *DIGEST_ALGORITHMS)


def _index_fixture_files(
    files: Mapping[str, FixtureFileSpec],
) -> tuple[dict[str, tuple[ReadStatus, str | None]], dict[str, dict[str, str | int]]]:
    """Split the declared file specs into read outcomes and blob identities.

    A malformed spec raises rather than being coerced: a fixture that cannot be
    read as written would otherwise prove something about a machine nobody
    described.
    """
    read: dict[str, tuple[ReadStatus, str | None]] = {}
    blobs: dict[str, dict[str, str | int]] = {}
    for path, spec in files.items():
        if isinstance(spec, str):
            read[path] = (READ_OK, spec)
            continue
        status = spec.get("status")
        if status is None:
            if not any(key in spec for key in _BLOB_KEYS):
                raise ValueError(
                    f"fixture file {path!r}: an object spec must carry a 'status' or at least one "
                    f"of {list(_BLOB_KEYS)}"
                )
            # A blob exists and is not text — the same answer a real
            # firmware file gives read_text.
            read[path] = (READ_INVALID_TEXT, None)
            blobs[path] = dict(spec)
            continue
        if status not in (READ_UNREADABLE, READ_INVALID_TEXT):
            raise ValueError(f"fixture file {path!r}: status must be 'unreadable' or 'invalid-text'")
        read[path] = (status, None)
    return read, blobs


def _ancestor_dirs(paths: Iterable[str]) -> set[str]:
    """Every ancestor of the given paths — a known path's parents are directories."""
    dirs: set[str] = set()
    for path in paths:
        parent = os.path.dirname(path)
        while parent and parent != "/":
            dirs.add(parent)
            parent = os.path.dirname(parent)
    return dirs


class FixtureMachine:
    """A machine backed by plain data: files, directories, symlinks, core answers.

    ``files`` maps absolute paths to contents — a string is readable content;
    ``{"status": "unreadable"}`` / ``{"status": "invalid-text"}`` is a file
    that exists but yields that read outcome; ``{"md5": ..., "sha1": ...,
    "size": ...}`` is a binary blob that exists, reads as ``invalid-text``, and
    answers those values for ``file_digest`` / ``file_size``. ``dirs`` lists directories that
    exist explicitly (parents of every known path are directories implicitly —
    the list is how *empty* directories are stated). ``symlinks`` maps link
    paths to their targets (absolute, or relative to the link's directory);
    path lookups resolve symlink components the way the kernel does, left to
    right, with a hop limit against cycles — so a link to a target that is not
    in the fixture is a *dead* link: ``readlink`` shows it, ``path_kind`` says
    missing, exactly like the real thing. ``cores`` maps ``.so`` paths to
    core-answer objects (``{"library_name": ...}``); a path mapped to ``None``
    is a core that is present but unloadable. ``inaccessible`` lists paths
    whose state cannot be determined at all (a failing ``stat``).
    """

    def __init__(
        self,
        files: Mapping[str, FixtureFileSpec],
        symlinks: Mapping[str, str] | None = None,
        cores: Mapping[str, Mapping[str, object] | None] | None = None,
        dirs: Iterable[str] | None = None,
        inaccessible: Iterable[str] | None = None,
    ) -> None:
        self._files, self._blobs = _index_fixture_files(files)
        self._symlinks = dict(symlinks or {})
        self._cores = dict(cores or {})
        self._inaccessible = set(inaccessible or ())
        self._dirs: set[str] = set(dirs or ())
        # Every ancestor of a known path is a directory.
        self._dirs |= _ancestor_dirs(
            (*self._files, *self._symlinks, *self._cores, *tuple(self._dirs), *self._inaccessible)
        )
        self._dirs.discard("")

    def _resolve(self, path: str) -> str | None:
        """Resolve symlink components in *path* (final one included).

        ``None`` when the chain does not settle within :data:`SYMLINK_HOPS`
        hops — a loop, or a chain longer than the kernel would follow. Returning
        the half-resolved path instead would make ``ELOOP`` unrepresentable in a
        fixture, and every caller below would then answer something the real
        machine never answers.
        """
        for _ in range(SYMLINK_HOPS):
            spliced = self._splice_first_symlink(path)
            if spliced is None:
                return path
            path = spliced
        return None

    def _splice_first_symlink(self, path: str) -> str | None:
        """*path* with its leftmost symlink component replaced by that link's target.

        ``None`` when no component is a link — the path is then fully resolved.
        A relative target is spliced against the directory holding the link, the
        way the kernel reads it.
        """
        parts = path.split("/")
        for i in range(2, len(parts) + 1):
            prefix = "/".join(parts[:i])
            if prefix not in self._symlinks:
                continue
            target = self._symlinks[prefix]
            if not target.startswith("/"):
                target = os.path.normpath(os.path.join(os.path.dirname(prefix), target))
            rest = "/".join(parts[i:])
            return target + ("/" + rest if rest else "")
        return None

    def _resolve_parent(self, path: str) -> str | None:
        """Resolve symlinks in the parent components only — the final one stays."""
        parent, name = os.path.dirname(path), os.path.basename(path)
        if not name or parent in ("", "/"):
            return path
        resolved_parent = self._resolve(parent)
        return None if resolved_parent is None else os.path.join(resolved_parent, name)

    def read_text(self, path: str) -> ReadResult:
        if self._is_inaccessible(path):
            return ReadResult(READ_UNREADABLE)
        resolved = self._resolve(path)
        if resolved is None:
            # ELOOP: os.stat fails, so the real machine cannot read it either.
            return ReadResult(READ_UNREADABLE)
        if resolved in self._files:
            status, text = self._files[resolved]
            return ReadResult(status, text)
        if resolved in self._cores:
            return ReadResult(READ_INVALID_TEXT)  # a binary is not text
        if resolved in self._dirs:
            return ReadResult(READ_UNREADABLE)
        return ReadResult(READ_MISSING)

    def path_kind(self, path: str) -> PathKind:
        if self._is_inaccessible(path):
            return KIND_INACCESSIBLE
        resolved = self._resolve(path)
        if resolved is None:
            # ELOOP: os.stat raises OSError, which the real machine reports as
            # inaccessible — never as an absent file.
            return KIND_INACCESSIBLE
        if resolved in self._files or resolved in self._cores:
            return KIND_FILE
        if resolved in self._dirs:
            return KIND_DIRECTORY
        return KIND_MISSING

    def _is_inaccessible(self, path: str) -> bool:
        if path in self._inaccessible:
            return True
        resolved = self._resolve(path)
        # A chain that never settles is ELOOP: the real machine fails to stat
        # it, which every operation here reports as inaccessible.
        return resolved is None or resolved in self._inaccessible

    def readlink(self, path: str) -> str | None:
        parent = self._resolve_parent(path)
        return None if parent is None else self._symlinks.get(parent)

    def file_size(self, path: str) -> int | None:
        if self._is_inaccessible(path):
            return None
        resolved = self._resolve(path)
        if resolved is None:
            return None
        declared = self._blobs.get(resolved, {}).get("size")
        if isinstance(declared, int):
            return declared
        text = self._files.get(resolved, (READ_MISSING, None))[1]
        return len(text.encode("utf-8")) if text is not None else None

    def file_digest(self, path: str, algorithm: str) -> str | None:
        if algorithm not in DIGEST_ALGORITHMS or self._is_inaccessible(path):
            return None
        resolved = self._resolve(path)
        if resolved is None:
            return None
        declared = self._blobs.get(resolved, {}).get(algorithm)
        if isinstance(declared, str):
            return declared
        text = self._files.get(resolved, (READ_MISSING, None))[1]
        if text is None:
            return None
        return hashlib.new(algorithm, text.encode("utf-8")).hexdigest()

    def query_core(self, so_path: str) -> CoreInfo | None:
        resolved = self._resolve(so_path)
        spec = self._cores.get(resolved) if resolved is not None else None
        if not spec:
            return None
        name = spec.get("library_name")
        if not isinstance(name, str) or not name:
            return None
        version = spec.get("library_version")
        extensions = spec.get("valid_extensions")
        return CoreInfo(
            library_name=name,
            library_version=version if isinstance(version, str) else None,
            valid_extensions=extensions if isinstance(extensions, str) else None,
            options=_parse_core_options(spec.get("options")),
        )

    def _children(self, spelled_dir: str) -> set[str]:
        """Entry names directly under *spelled_dir* (resolved like the kernel would)."""
        real = self._resolve(spelled_dir) if spelled_dir else ""
        if real is None:
            return set()
        prefix = real.rstrip("/") + "/"
        names: set[str] = set()
        for known in (*self._files, *self._symlinks, *self._cores, *self._dirs, *self._inaccessible):
            if known.startswith(prefix):
                names.add(known[len(prefix) :].split("/", 1)[0])
        names.discard("")
        return names

    def _present_for_glob(self, spelled: str) -> bool:
        # A symlink is listed by glob even when dead — like a real iterdir.
        if self._resolve_parent(spelled) in self._symlinks:
            return True
        return self.path_kind(spelled) != KIND_MISSING

    def _matching_children(self, base: str, segment: str) -> list[str]:
        """Entry names under *base* that the wildcard *segment* matches.

        A wildcard never matches a leading dot — the same hidden-file rule a
        real glob applies, so a dotfile is invisible to both.
        """
        hidden_ok = segment.startswith(".")
        return [
            child
            for child in self._children(base)
            if (hidden_ok or not child.startswith(".")) and fnmatch.fnmatchcase(child, segment)
        ]

    def _walk_glob(self, base: str, segments: list[str], results: set[str]) -> None:
        """Match *segments* against the tree below *base*, collecting hits in *results*."""
        if not segments:
            if base and self._present_for_glob(base):
                results.add(base)
            return
        segment, rest = segments[0], segments[1:]
        if not segment:
            self._walk_glob(base, rest, results)
        elif not _GLOB_MAGIC & set(segment):
            self._walk_glob(f"{base}/{segment}", rest, results)
        else:
            for child in self._matching_children(base, segment):
                self._walk_glob(f"{base}/{child}", rest, results)

    def glob(self, pattern: str) -> list[str]:
        if not pattern.startswith("/"):
            return []
        results: set[str] = set()
        self._walk_glob("", pattern.split("/")[1:], results)
        return sorted(results)
