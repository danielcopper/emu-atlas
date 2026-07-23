"""The machine seam — the one injected protocol every machine access flows through.

atlas is a resolver: it answers questions about the running machine by reading
the running machine. All of that reading goes through a :class:`Machine`, which
abstracts the machine itself — files, symlinks, and the answers only a core
binary can give — not merely "text files". In production the machine is the real
one (:class:`RealMachine`); in tests and conformance vectors it is a fixture
(:class:`FixtureMachine`): files, symlinks, and core answers as plain data
describing a whole machine, including broken links and unloadable cores. One
code path, two data sources, so everything atlas concludes is provable from
data — the failure states included.

``readlink`` exists because RetroDECK's standalone save architecture is symlinks
(``dir_prep``): the emulator-side path and the real path are two truthful
answers to different questions, and a dead link is a real state the resolver
must be able to see. ``query_core`` exists because ``library_name`` — the value
that names sort-by-core directories and the override directory — lives only in
the core binary; loading the core and asking it is the same read RetroArch
performs. A live read, never a shipped table.
"""

from __future__ import annotations

import fnmatch
import glob as _glob
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Mapping, Protocol

_CORE_PROBE_TIMEOUT_SECONDS = 15
_MAX_SYMLINK_HOPS = 40


@dataclass(frozen=True, slots=True)
class CoreInfo:
    """What a libretro core reports about itself via ``retro_get_system_info``.

    ``library_name`` is the value RetroArch uses for sort-by-core directories and
    override directories — the display name, not the ``.so`` basename (they
    differ for 87% of RetroDECK's shipped cores).
    """

    library_name: str
    library_version: str | None
    valid_extensions: str | None


class Machine(Protocol):
    """Narrow machine port: read a file, glob, test existence, follow links, ask a core.

    ``read_text`` returns ``None`` when the path does not exist or cannot be read
    as text. ``glob`` returns matches sorted, so results are deterministic.
    ``readlink`` returns the link target when the path itself is a symlink, else
    ``None``. ``query_core`` returns the core's self-reported info, or ``None``
    when the core cannot be loaded — the caller treats that as *unknown*, never
    as a guess.
    """

    def read_text(self, path: str) -> str | None: ...

    def glob(self, pattern: str) -> list[str]: ...

    def exists(self, path: str) -> bool: ...

    def readlink(self, path: str) -> str | None: ...

    def query_core(self, so_path: str) -> CoreInfo | None: ...


class RealMachine:
    """The production machine: the real filesystem plus a real core prober.

    ``query_core`` runs the probe in a subprocess (``atlas._core_probe``) so a
    crashing core costs one answer, not the host process, and memoizes per
    ``(path, mtime, size)`` — a cached live read, not shipped data: the cache
    invalidates the moment the ``.so`` changes.
    """

    def __init__(self) -> None:
        self._core_cache: dict[tuple[str, int, int], CoreInfo | None] = {}

    def read_text(self, path: str) -> str | None:
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return None

    def glob(self, pattern: str) -> list[str]:
        return sorted(_glob.glob(pattern, recursive=True))

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def readlink(self, path: str) -> str | None:
        try:
            return os.readlink(path) if os.path.islink(path) else None
        except OSError:
            return None

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
        try:
            data = json.loads(proc.stdout.decode("utf-8", "replace"))
        except (json.JSONDecodeError, ValueError):
            return None
        name = data.get("library_name")
        if not isinstance(name, str) or not name:
            return None
        return CoreInfo(
            library_name=name,
            library_version=data.get("library_version"),
            valid_extensions=data.get("valid_extensions"),
        )


class FixtureMachine:
    """A machine backed by plain data: files, symlinks, and core answers.

    ``files`` maps absolute paths to contents. ``symlinks`` maps link paths to
    their targets (absolute, or relative to the link's directory); path lookups
    resolve symlink prefixes the way the kernel does, left to right, with a hop
    limit against cycles — so a link to a target that is not in ``files`` is a
    *dead* link: ``readlink`` shows it, ``exists`` says no, exactly like the
    real thing. ``cores`` maps ``.so`` paths to core-answer objects
    (``{"library_name": ...}``); a path mapped to ``None`` is a core that is
    present but unloadable.
    """

    def __init__(
        self,
        files: Mapping[str, str],
        symlinks: Mapping[str, str] | None = None,
        cores: Mapping[str, Mapping[str, str | None] | None] | None = None,
    ) -> None:
        self._files = dict(files)
        self._symlinks = dict(symlinks or {})
        self._cores = dict(cores or {})

    def _resolve(self, path: str) -> str:
        """Resolve symlink prefixes in *path*, shortest prefix first, cycle-guarded."""
        for _ in range(_MAX_SYMLINK_HOPS):
            parts = path.split("/")
            replaced = False
            for i in range(2, len(parts) + 1):
                prefix = "/".join(parts[:i])
                if prefix in self._symlinks:
                    target = self._symlinks[prefix]
                    if not target.startswith("/"):
                        target = os.path.normpath(os.path.join(os.path.dirname(prefix), target))
                    rest = "/".join(parts[i:])
                    path = target + ("/" + rest if rest else "")
                    replaced = True
                    break
            if not replaced:
                return path
        return path

    def _known_paths(self) -> list[str]:
        return [*self._files, *self._symlinks, *self._cores]

    def read_text(self, path: str) -> str | None:
        return self._files.get(self._resolve(path))

    def glob(self, pattern: str) -> list[str]:
        # Resolve symlink prefixes in the pattern too, so a glob through a
        # linked directory finds the real files — like the real filesystem.
        patterns = {pattern, self._resolve(pattern)}
        return sorted(
            p for p in set(self._known_paths()) if any(fnmatch.fnmatch(p, pt) for pt in patterns)
        )

    def exists(self, path: str) -> bool:
        resolved = self._resolve(path)
        if resolved in self._files or resolved in self._cores:
            return True
        prefix = resolved.rstrip("/") + "/"
        return any(known.startswith(prefix) for known in self._known_paths())

    def readlink(self, path: str) -> str | None:
        return self._symlinks.get(path)

    def query_core(self, so_path: str) -> CoreInfo | None:
        spec = self._cores.get(self._resolve(so_path))
        if not spec:
            return None
        name = spec.get("library_name")
        if not isinstance(name, str) or not name:
            return None
        return CoreInfo(
            library_name=name,
            library_version=spec.get("library_version"),
            valid_extensions=spec.get("valid_extensions"),
        )
