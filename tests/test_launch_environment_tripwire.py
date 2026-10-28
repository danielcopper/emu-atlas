"""The launch-environment tripwire: the deployed RetroDECK still holds the files its launcher readings were made from (#574).

What a component launcher reads from RetroDECK's own start is read per
launcher and pinned (``atlas/data/retrodeck_launchers.json``): the launcher by
the git blob hash of its deployed copy, the start file that defines each
variable it reads by its own. An answer checks those hashes itself and states
``launch-environment-unpinned`` instead of the list where one differs. Where
RetroDECK is deployed this turns that silence red:

1. Every deployed component launcher is pinned, at its own path, and every
   pinned start file hashes to its pin.
2. Each pinned launcher's reads are exactly the variables the start provides
   that its text reads, in the order it first reads them — the provided ones
   being everything the start exports: every assignment at the start of a
   line of ``all_vars.sh`` and of each component's ``component_functions.sh``
   (both sourced under ``allexport``), every assignment ``logger.sh`` makes
   (sourced under ``allexport`` too, global.sh:16-18), every key of the
   shipped ``retrodeck.json``'s ``paths`` and ``options``, which ``conf_read``
   exports, the names ``conf_read`` and ``source_component_functions`` assign
   while they run (:data:`_START_LOCALS`), and ``GLOBAL_SOURCED``
   (global.sh:234) — and each variable's definition is where the table says.
3. The libraries a launcher that reads ``rd_shared_libs`` loads resolve with
   the library path the launcher composes: its executables' ``DT_NEEDED``
   entries are walked, transitively, over that path, their ``RUNPATH`` and the
   loader's default directories in the sandbox, read off the deploy and the
   runtime on the host. This is file presence only — no loader runs, so a
   library found by name but refused for its version or its architecture
   passes — so what it proves is derived [D], never verified. It holds the
   answer's ``provided``, which judges only that the folder is there (D5).
   Only an ELF executable the launcher runs from its own folder is walked,
   and every launcher that reads ``rd_shared_libs`` must have one, except
   the ones :data:`NOT_WALKED` names with the reason the walk cannot reach
   what they run.

Skipped where RetroDECK is not deployed. The message names what to re-read
before a pin moves.
"""

from __future__ import annotations

import json
import posixpath
import re
import struct
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path

import pytest

from atlas.launch_environment import SOURCE_LINE, SOURCE_PATHS, environment_rules, start_line
from atlas.machine import DIGEST_GIT_BLOB, RealMachine

FLATPAK = Path("/var/lib/flatpak")
DEPLOY = FLATPAK / "app" / "net.retrodeck.retrodeck" / "current" / "active"
FILES = DEPLOY / "files"
COMPONENTS = FILES / "retrodeck" / "components"

_REREAD = (
    "re-read the launcher and the start files it rests on at the deployed release — which variables of the start "
    "it reads, where each is defined — then move the pins in atlas/data/retrodeck_launchers.json"
)


def _deployed() -> bool:
    return COMPONENTS.is_dir()


def _host(sandbox: str) -> Path:
    """Where the host reads a path under the deploy's ``/app`` tree."""
    return FILES / sandbox.removeprefix("/app/")


def reads_in(text: str, provided: Iterable[str]) -> list[str]:
    """The *provided* variables *text* expands, in the order it first expands each."""
    first: dict[str, int] = {}
    for name in provided:
        match = re.search(r"\$\{?" + re.escape(name) + r"\b", text)
        if match is not None:
            first[name] = match.start()
    return sorted(first, key=first.__getitem__)


# An assignment at the start of a line, as a file sourced at the top level
# makes one; and any assignment at all, ``export`` or not, where a whole file's
# functions run during the start (logger.sh's ``log``).
_TOP_LEVEL = re.compile(r"^([A-Za-z_]\w*)=", re.MULTILINE)
_ANYWHERE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_]\w*)=", re.MULTILINE)


# The names the start's own routines assign while they run, beside the files
# and keys above (other_functions.sh at 0.10.10b):
# - ``version``: conf_read's jq builds ``{version: $ver}`` + paths + options
#   and exports every pair it prints (:160-166, the export at :157);
# - ``name`` and ``value``: conf_read's ``read -r name value`` (:155), which
#   runs under the allexport it sets at :152;
# - ``functions_file``: source_component_functions' loop variable (:1394,
#   :1417). Its ``read`` runs outside the allexport each pass sets around
#   ``source``, so it is counted though it may not be exported — counting one
#   name too many can only make this test demand a read be stated.
_START_LOCALS = ("version", "name", "value", "functions_file")


def _provided_by_the_start() -> list[str]:
    """Every name RetroDECK's start exports: the assignments of the files it sources under ``allexport``
    (``all_vars.sh``, ``logger.sh``, every ``component_functions.sh``), the shipped settings' keys, which
    ``conf_read`` exports, the names its routines assign (:data:`_START_LOCALS`), and ``GLOBAL_SOURCED``.
    """
    names = _TOP_LEVEL.findall((FILES / "libexec" / "all_vars.sh").read_text(encoding="utf-8"))
    names += _ANYWHERE.findall((FILES / "libexec" / "logger.sh").read_text(encoding="utf-8"))
    for functions in sorted(COMPONENTS.glob("*/component_functions.sh")):
        names += _TOP_LEVEL.findall(functions.read_text(encoding="utf-8"))
    defaults = json.loads((FILES / "retrodeck" / "config" / "retrodeck" / "retrodeck.json").read_text(encoding="utf-8"))
    settings = [*defaults.get("paths", {}), *defaults.get("options", {})]
    return list(dict.fromkeys([*names, *settings, *_START_LOCALS, "GLOBAL_SOURCED"]))


@pytest.mark.skipif(not _deployed(), reason="RetroDECK is not deployed on this machine")
class TestTheDeployedRetroDeckHoldsThePinnedFiles:
    def test_every_deployed_launcher_is_pinned_at_its_own_path(self):
        machine = RealMachine()
        by_blob = environment_rules().by_blob
        drift: list[str] = []
        for launcher in sorted(COMPONENTS.glob("*/component_launcher.sh")):
            sandbox = "/app/" + str(launcher.relative_to(FILES))
            blob = machine.file_digest(str(launcher), DIGEST_GIT_BLOB)
            rule = by_blob.get(blob or "")
            if rule is None or rule.launcher != sandbox:
                drift.append(f"{sandbox} hashes to {blob}, which no launcher at that path is pinned by: {_REREAD}")
        assert drift == []

    def test_every_pinned_start_file_hashes_to_its_pin(self):
        machine = RealMachine()
        start_files = environment_rules().start_files
        hashes = {path: machine.file_digest(str(_host(path)), DIGEST_GIT_BLOB) for path in start_files}
        assert {path: found for path, found in hashes.items() if found != start_files[path]} == {}, _REREAD

    def test_each_launcher_reads_what_the_table_says(self):
        provided = _provided_by_the_start()
        wrong: dict[str, tuple[list[str], list[str]]] = {}
        for rule in environment_rules().by_blob.values():
            host = _host(rule.launcher)
            if not host.is_file():
                continue
            found = reads_in(host.read_text(encoding="utf-8"), provided)
            stated = [read.name for read in rule.reads]
            if found != stated:
                wrong[rule.launcher] = (found, stated)
        assert wrong == {}, _REREAD

    @pytest.mark.parametrize("name", ["version", "name", "value", "functions_file", "GLOBAL_SOURCED"])
    def test_a_launcher_reading_what_the_start_s_routines_set_reads_a_provided_variable(self, name: str):
        assert reads_in(f'exec "${name}/x"\n', _provided_by_the_start()) == [name]

    def test_each_variable_is_defined_where_the_table_says(self):
        rules = environment_rules()
        for name, variable in rules.variables.items():
            lines = _host(variable.defined_in).read_text(encoding="utf-8").splitlines()
            if variable.source == SOURCE_LINE:
                assert start_line("\n".join(lines), name) is not None, f"{variable.defined_in} sets no {name}: {_REREAD}"
            else:
                assert variable.source == SOURCE_PATHS
                assert variable.line is not None
                # conf_read's assignment, which sets every key under its own name.
                assert lines[variable.line - 1].strip() == 'printf -v "$name" \'%s\' "$value"', _REREAD


# --- the libraries ---------------------------------------------------------------------------------------------

# ELF64 (the System V gABI): the section type of the dynamic section, and the
# tags of the entries this walk reads.
_SHT_DYNAMIC = 6
_DT_NULL = 0
_DT_NEEDED = 1
_DT_RPATH = 15
_DT_RUNPATH = 29
_ELF_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
_SECTION_HEADER = struct.Struct("<IIQQQQIIQQ")
_DYNAMIC = struct.Struct("<qQ")


def dynamic_entries(read: Callable[[int, int], bytes]) -> tuple[list[str], list[str]] | None:
    """A little-endian ELF64's ``DT_NEEDED`` names and its ``RUNPATH``/``RPATH`` directories — ``None`` for a file
    that is no such ELF (a script). *read* hands out *size* bytes at an offset.
    """
    head = read(0, _ELF_HEADER.size)
    if len(head) < _ELF_HEADER.size or head[:4] != b"\x7fELF" or head[4] != 2 or head[5] != 1:
        return None
    header = _ELF_HEADER.unpack(head)
    shoff, shentsize, shnum = header[6], header[11], header[12]
    sections = [_SECTION_HEADER.unpack(read(shoff + i * shentsize, _SECTION_HEADER.size)) for i in range(shnum)]
    needed: list[str] = []
    paths: list[str] = []
    for section in sections:
        if section[1] == _SHT_DYNAMIC:
            strings = sections[section[6]]
            for tag, text in _dynamic_strings(read(section[4], section[5]), read(strings[4], strings[5])):
                if tag == _DT_NEEDED:
                    needed.append(text)
                else:
                    paths.extend(text.split(":"))
    return needed, paths


def _dynamic_strings(body: bytes, table: bytes) -> Iterator[tuple[int, str]]:
    """The ``DT_NEEDED``, ``DT_RPATH`` and ``DT_RUNPATH`` entries of a dynamic section, with their strings."""
    for at in range(0, len(body) - _DYNAMIC.size + 1, _DYNAMIC.size):
        tag, value = _DYNAMIC.unpack_from(body, at)
        if tag == _DT_NULL:
            return
        if tag in (_DT_NEEDED, _DT_RPATH, _DT_RUNPATH):
            yield tag, table[value : table.index(b"\x00", value)].decode("utf-8")


def unresolved(
    binary: str,
    search: list[str],
    entries: Callable[[str], tuple[list[str], list[str]] | None],
    exists: Callable[[str], bool],
) -> dict[str, str]:
    """Every library *binary* needs, itself or through what it loads, that no directory holds — each with the
    object that needs it. *search* is the library path and the default directories, in the sandbox's spelling;
    ``$ORIGIN`` in an object's own ``RUNPATH`` is that object's directory.
    """
    found: set[str] = set()
    missing: dict[str, str] = {}
    pending = [binary]
    while pending:
        current = pending.pop()
        read = entries(current)
        if read is None:
            continue
        needed, own = read
        origin = current.rsplit("/", 1)[0]
        directories = [*(d for d in search if d), *(d.replace("${ORIGIN}", origin).replace("$ORIGIN", origin) for d in own)]
        for name in needed:
            if name in found or name in missing:
                continue
            candidates = (posixpath.normpath(f"{directory}/{name}") for directory in directories)
            hit = next((candidate for candidate in candidates if exists(candidate)), None)
            if hit is None:
                missing[name] = current
            else:
                found.add(name)
                pending.append(hit)
    return missing


# The loader's own directories in a Flatpak sandbox: the app's, the GL
# extension's and the runtime's (the ld.so.conf flatpak writes for the app).
_DEFAULT_DIRECTORIES = ["/app/lib", "/usr/lib/x86_64-linux-gnu/GL/default/lib", "/usr/lib/x86_64-linux-gnu", "/usr/lib"]
_GL_DEFAULT = "/usr/lib/x86_64-linux-gnu/GL/default"


def _metadata(path: Path) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
        elif "=" in line:
            key, _, value = line.partition("=")
            current[key.strip()] = value.strip()
    return sections


def _sandbox_reader() -> Callable[[str], Path]:
    """The host path of a sandbox path under ``/app``, the GL extension or the runtime's ``/usr``."""
    runtime_ref = _metadata(DEPLOY / "metadata")["Application"]["runtime"]
    runtime = FLATPAK / "runtime" / runtime_ref / "active"
    gl_version = _metadata(runtime / "metadata")["Extension org.freedesktop.Platform.GL"]["versions"].split(";")[0]
    gl = FLATPAK / "runtime" / "org.freedesktop.Platform.GL.default" / runtime_ref.split("/")[1] / gl_version / "active"

    def host(path: str) -> Path:
        if path.startswith("/app/"):
            return FILES / path.removeprefix("/app/")
        if path.startswith(_GL_DEFAULT + "/"):
            return gl / "files" / path.removeprefix(_GL_DEFAULT + "/")
        if path.startswith("/usr/"):
            return runtime / "files" / path.removeprefix("/usr/")
        return Path(path)

    return host


# The library path a launcher composes, and the executables it runs from its own folder.
_LIBRARY_PATH = re.compile(r'^export LD_LIBRARY_PATH="(?P<path>[^"]*)"$', re.MULTILINE)
_EXEC = re.compile(r'exec "\$component_path/(?P<binary>[^"]+)"')


def composed(text: str, component: str, rd_shared_libs: str) -> tuple[list[str], list[str]]:
    """The library path *text* exports for *component* and the executables it runs from the component's folder.

    ``$rd_shared_libs`` is its value, ``$component_path`` the component's
    folder; every other variable — ``DEFAULT_LD_LIBRARY_PATH``, the sandbox's
    own ``LD_LIBRARY_PATH`` and ``ffmpeg_path``, which nothing defines — is
    empty, as it is where the start sets it from an empty library path.
    """
    match = _LIBRARY_PATH.search(text)
    folder = f"/app/retrodeck/components/{component}"
    path = "" if match is None else match.group("path")
    path = path.replace("$rd_shared_libs", rd_shared_libs).replace("$component_path", folder)
    path = re.sub(r"\$\{?\w+\}?", "", path)
    return path.split(":"), [f"{folder}/{binary}" for binary in _EXEC.findall(text)]


def _entries_on(host: Callable[[str], Path]) -> Callable[[str], tuple[list[str], list[str]] | None]:
    def entries(path: str) -> tuple[list[str], list[str]] | None:
        with host(path).open("rb") as handle:

            def read(offset: int, size: int) -> bytes:
                handle.seek(offset)
                return handle.read(size)

            return dynamic_entries(read)

    return entries


# The launchers reading rd_shared_libs whose executables the walk cannot reach,
# and why. A launcher listed here that the walk does reach fails as well, so
# the list cannot outlive its reason.
NOT_WALKED = {
    "gzdoom": "it runs bin/gzdoom.sh, a script, whose own exec the walk does not follow",
    "portmaster": "it runs the port script it is handed, or /var/data/PortMaster/PortMaster.sh outside the deploy",
}


def walk_disagreements(walked: Mapping[str, int], exempt: Mapping[str, str]) -> list[str]:
    """One line per launcher the walk reached no executable of without an exemption, or reached despite one."""
    lines: list[str] = []
    for component, count in sorted(walked.items()):
        if not count and component not in exempt:
            lines.append(f"{component}: no ELF executable walked")
        elif count and component in exempt:
            lines.append(f"{component}: walked, so its exemption is stale")
    return lines


def _walk(text: str, component: str, rd_shared_libs: str, host: Callable[[str], Path]) -> tuple[int, dict[str, dict[str, str]]]:
    """How many ELF executables of one launcher the walk reached, and what each of them lacks."""
    entries = _entries_on(host)
    search, binaries = composed(text, component, rd_shared_libs)
    walked = 0
    missing: dict[str, dict[str, str]] = {}
    for binary in binaries:
        if not host(binary).is_file() or entries(binary) is None:
            continue
        walked += 1
        gone = unresolved(binary, [*search, *_DEFAULT_DIRECTORIES], entries, lambda p: host(p).is_file())
        if gone:
            missing[binary] = gone
    return walked, missing


@pytest.mark.skipif(not _deployed(), reason="RetroDECK is not deployed on this machine")
def test_the_libraries_resolve_for_every_launcher_that_reads_the_shared_libraries():
    host = _sandbox_reader()
    found = start_line(_host("/app/libexec/all_vars.sh").read_text(encoding="utf-8"), "rd_shared_libs")
    assert found is not None
    shared = found[1]
    assert shared is not None
    walked: dict[str, int] = {}
    missing: dict[str, dict[str, str]] = {}
    for rule in environment_rules().by_blob.values():
        if "rd_shared_libs" not in {read.name for read in rule.reads} or not _host(rule.launcher).is_file():
            continue
        component = rule.launcher.split("/")[-2]
        walked[component], lacking = _walk(_host(rule.launcher).read_text(encoding="utf-8"), component, shared, host)
        missing.update(lacking)
    assert walk_disagreements(walked, NOT_WALKED) == []
    assert missing == {}, "a library a launcher loads resolves nowhere on its library path: " + _REREAD


class TestTheWalkFiresWithoutADeploy:
    """The tripwire's readings, watched failing on data rather than on a deploy."""

    def test_the_reads_come_in_the_order_the_text_first_reads_them(self):
        text = 'export LD_LIBRARY_PATH="$rd_shared_libs:${DEFAULT_LD_LIBRARY_PATH}"\nexec "$bios_path/x"\n'
        assert reads_in(text, ["bios_path", "DEFAULT_LD_LIBRARY_PATH", "rd_shared_libs", "roms_path"]) == [
            "rd_shared_libs",
            "DEFAULT_LD_LIBRARY_PATH",
            "bios_path",
        ]

    def test_a_longer_name_is_not_the_shorter_one(self):
        assert reads_in("echo $rd_shared_libs_kde_path\n", ["rd_shared_libs"]) == []

    def test_the_composed_path_fills_the_two_variables_and_empties_the_rest(self):
        text = (
            'export LD_LIBRARY_PATH="$component_path/lib:$ffmpeg_path/25.08:$rd_shared_libs:${DEFAULT_LD_LIBRARY_PATH}"\n'
            'exec "$component_path/bin/x" "$@"\n'
        )
        assert composed(text, "x", "/app/shared") == (
            ["/app/retrodeck/components/x/lib", "/25.08", "/app/shared", ""],
            ["/app/retrodeck/components/x/bin/x"],
        )

    def test_a_library_no_directory_holds_is_named_with_what_needs_it(self):
        tree = {
            "/c/bin/x": (["liba.so", "libb.so"], ["$ORIGIN/../lib"]),
            "/c/lib/liba.so": (["libc.so"], []),
            "/shared/libb.so": ([], []),
        }
        entries: Callable[[str], tuple[list[str], list[str]] | None] = tree.get
        assert unresolved("/c/bin/x", ["/shared"], entries, tree.__contains__) == {"libc.so": "/c/lib/liba.so"}
        tree["/shared/libc.so"] = ([], [])
        assert unresolved("/c/bin/x", ["/shared"], entries, tree.__contains__) == {}

    def test_a_launcher_without_a_walked_executable_needs_its_exemption(self):
        assert walk_disagreements({"dolphin": 1, "gzdoom": 0}, {"gzdoom": "a script"}) == []
        assert walk_disagreements({"dolphin": 0}, {}) == ["dolphin: no ELF executable walked"]
        assert walk_disagreements({"gzdoom": 1}, {"gzdoom": "a script"}) == ["gzdoom: walked, so its exemption is stale"]

    def test_a_script_has_no_libraries(self):
        assert dynamic_entries(lambda offset, size: b"#!/bin/sh\n"[offset : offset + size]) is None

    def test_the_dynamic_section_is_read(self):
        blob = _synthetic_elf(["liba.so.1", "libb.so.2"], "$ORIGIN/../lib:/opt/x")

        def read(offset: int, size: int) -> bytes:
            return blob[offset : offset + size]

        assert dynamic_entries(read) == (["liba.so.1", "libb.so.2"], ["$ORIGIN/../lib", "/opt/x"])


def _synthetic_elf(needed: list[str], runpath: str) -> bytes:
    """A minimal ELF64: a null section, a ``.dynamic`` and the string table it links."""
    strings = b"\x00"
    entries = b""
    for name in needed:
        entries += _DYNAMIC.pack(_DT_NEEDED, len(strings))
        strings += name.encode() + b"\x00"
    entries += _DYNAMIC.pack(_DT_RUNPATH, len(strings)) + _DYNAMIC.pack(_DT_NULL, 0)
    strings += runpath.encode() + b"\x00"
    dynamic_at = _ELF_HEADER.size
    strings_at = dynamic_at + len(entries)
    sections_at = strings_at + len(strings)
    header = _ELF_HEADER.pack(
        b"\x7fELF\x02\x01\x01" + b"\x00" * 9, 3, 62, 1, 0, 0, sections_at, 0,
        _ELF_HEADER.size, 0, 0, _SECTION_HEADER.size, 3, 0,
    )
    sections = (
        _SECTION_HEADER.pack(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        + _SECTION_HEADER.pack(0, _SHT_DYNAMIC, 0, 0, dynamic_at, len(entries), 2, 0, 8, _DYNAMIC.size)
        + _SECTION_HEADER.pack(0, 3, 0, 0, strings_at, len(strings), 0, 0, 1, 0)
    )
    return header + entries + strings + sections
