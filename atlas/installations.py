"""Installation handles — every question is asked *of an installation*.

Detection produces these; each answers ``save_location`` for its own flavor by
reading the configs that govern it — the same files, in the same order, with the
same fallbacks the emulator itself uses — through the injected machine seam.

- :class:`RetroDeck` — the RetroDECK Flatpak. ``retrodeck.json`` supplies the
  roots and the health check; the bundled RetroArch's cfg (plus its override
  chain) supplies the layout. The cfg is what RetroArch reads, so the cfg is the
  truth; ``retrodeck.json`` is context.
- :class:`EmuDeck` — an EmuDeck arrangement. Its truth is ``settings.sh``; its
  RetroArch is the standalone ``org.libretro.RetroArch`` Flatpak, so the handle
  carries both descriptions (``kinds``) — the same RetroArch under two names.
- :class:`StandaloneRetroArchFlatpak` / :class:`NativeRetroArch` — bare
  RetroArch installs, differing in config location and default set.

Health is reported, not guessed around: a readable config whose root points into
an absent mount (unmounted SD card) yields ``root_missing``, never a
syntactically correct path handed out as if it were usable.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

from atlas.machine import Machine
from atlas.placement import (
    UNKNOWN_FILE_SET,
    FileSet,
    SavePlacement,
    build_save_placement,
)
from atlas.retroarch_cfg import (
    EMUDECK_DEFAULTS,
    RETRODECK_DEFAULTS,
    UPSTREAM_DEFAULTS,
    LayoutDefaults,
    parse_cfg_text,
    resolve_save_layout,
)

# Health states.
HEALTH_OK = "ok"
HEALTH_ROOT_MISSING = "root_missing"
HEALTH_CONFIG_UNREADABLE = "config_unreadable"

# Config markers, as ``home``-relative suffixes.
RETRODECK_JSON_SUFFIX = os.path.join(
    ".var", "app", "net.retrodeck.retrodeck", "config", "retrodeck", "retrodeck.json"
)
RETRODECK_CFG_SUFFIX = os.path.join(
    ".var", "app", "net.retrodeck.retrodeck", "config", "retroarch", "retroarch.cfg"
)
EMUDECK_SETTINGS_SUFFIX = os.path.join(".config", "EmuDeck", "settings.sh")
STANDALONE_FLATPAK_CFG_SUFFIX = os.path.join(
    ".var", "app", "org.libretro.RetroArch", "config", "retroarch", "retroarch.cfg"
)
NATIVE_CFG_SUFFIX = os.path.join(".config", "retroarch", "retroarch.cfg")


def _split_content_path(content_path: str) -> tuple[str, str, str]:
    """Derive ``(content_dir_path, content_dir_name, rom_stem)`` from a content path.

    ``content_dir`` is the ROM's parent directory name
    (``fill_pathname_parent_dir_name``, ``runloop.c:8781``); ``rom_stem`` is the
    basename truncated at the last dot, but not when the name begins with one
    (``runloop.c:8710``).
    """
    content_dir_path = os.path.dirname(content_path)
    content_dir_name = os.path.basename(content_dir_path)
    base = os.path.basename(content_path)
    dot = base.rfind(".")
    rom_stem = base[:dot] if dot > 0 else base
    return content_dir_path, content_dir_name, rom_stem


def _flatpak_host_path(machine: Machine, home: str, app_id: str, path: str) -> str | None:
    """Translate a sandbox-internal ``/app/...`` path to its host location.

    A Flatpak app's ``/app`` is the deployment's ``files/`` directory, reachable
    from the host under the system or user installation. Returns the first
    candidate that exists, or ``None`` — an honest miss, never a guess.
    """
    if not path.startswith("/app/"):
        return path
    rest = path[len("/app/") :]
    for base in (
        f"/var/lib/flatpak/app/{app_id}/current/active/files",
        os.path.join(home, ".local", "share", "flatpak", "app", app_id, "current", "active", "files"),
    ):
        candidate = os.path.join(base, rest)
        if machine.exists(candidate):
            return candidate
    return None


def _retroarch_save_location(
    machine: Machine,
    *,
    home: str,
    global_cfg_path: str,
    cfg_label: str,
    override_config_dir: str,
    defaults: LayoutDefaults,
    content_path: str | None,
    core_so: str | None,
    core_path_resolver: Callable[[str], str | None],
    extra_sources: tuple[str, ...] = (),
    extra_caveats: tuple[str, ...] = (),
) -> SavePlacement:
    """The shared resolver: global cfg → override chain → placement, all live.

    Reads the same four layers RetroArch reads (``configuration.c:7095``),
    resolves ``library_name`` from the core binary when a core is named, and
    observes the file set for existing saves. Every degradation is a stated
    caveat, never a silent guess.
    """
    caveats = list(extra_caveats)
    sources_extra = list(extra_sources)

    content_dir_path = content_dir_name = rom_stem = None
    if content_path is not None:
        content_dir_path, content_dir_name, rom_stem = _split_content_path(content_path)

    library_name: str | None = None
    if core_so is not None:
        so_path = core_so if os.sep in core_so else core_path_resolver(core_so)
        info = machine.query_core(so_path) if so_path else None
        if info is not None:
            library_name = info.library_name
            sources_extra.append(
                f'core: {os.path.basename(so_path or core_so)} reports library_name "{library_name}"'
                " (retro_get_system_info)"
            )
        else:
            caveats.append(
                f"core {core_so!r} could not be queried — library_name unknown, per-core overrides not checked"
            )
    else:
        caveats.append("no core given — library_name unavailable, per-core overrides not checked")

    overrides: list[tuple[str, str]] = []
    if library_name is not None:
        candidates = [
            (
                f"core override config/{library_name}/{library_name}.cfg",
                os.path.join(override_config_dir, library_name, f"{library_name}.cfg"),
            )
        ]
        if content_dir_name:
            candidates.append(
                (
                    f"content-dir override config/{library_name}/{content_dir_name}.cfg",
                    os.path.join(override_config_dir, library_name, f"{content_dir_name}.cfg"),
                )
            )
        if rom_stem:
            candidates.append(
                (
                    f"game override config/{library_name}/{rom_stem}.cfg",
                    os.path.join(override_config_dir, library_name, f"{rom_stem}.cfg"),
                )
            )
        for label, path in candidates:
            text = machine.read_text(path)
            if text is not None:
                overrides.append((label, text))

    layout = resolve_save_layout(
        machine.read_text(global_cfg_path),
        home=home,
        cfg_label=cfg_label,
        defaults=defaults,
        overrides=overrides,
    )

    file_set = UNKNOWN_FILE_SET
    placement = build_save_placement(
        layout=layout,
        content_dir_path=content_dir_path,
        content_dir_name=content_dir_name,
        library_name=library_name,
        extra_sources=tuple(sources_extra),
    )

    if not placement.needs:
        directory = placement.dir
        if rom_stem is not None:
            content_basename = os.path.basename(content_path) if content_path else None
            matches = [
                m
                for m in machine.glob(os.path.join(directory, f"{rom_stem}.*"))
                # In content-dir mode the ROM shares the save's directory and
                # stem — the content file itself is never part of the save set.
                if os.path.basename(m) != content_basename
            ]
            if matches:
                file_set = FileSet(
                    state="observed",
                    files=tuple(sorted(os.path.basename(m) for m in matches)),
                    source=f"observed on the machine: {directory}",
                )
            else:
                file_set = FileSet(
                    state="unknown",
                    files=(),
                    source=f"no files present at {directory} — file set not stated (never guessed)",
                )
        if (
            placement.root_kind == "savefile_directory"
            and layout.savefile_directory is not None
            and directory != layout.savefile_directory
            and not machine.exists(directory)
        ):
            caveats.append(
                f"sorted directory {directory} does not exist yet — RetroArch creates it on first save, "
                f"and silently reverts to {layout.savefile_directory} if creation fails (runloop.c:8844)"
            )

    return SavePlacement(
        dir=placement.dir,
        root_kind=placement.root_kind,
        needs=placement.needs,
        file_set=file_set,
        sources=placement.sources,
        caveats=tuple(caveats),
    )


def _parse_retrodeck_config(text: str | None) -> dict[str, Any]:
    """Parse ``retrodeck.json`` best-effort — malformed or absent yields ``{}``."""
    if text is None:
        return {}
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class RetroDeck:
    """A RetroDECK installation — cfg is the truth, ``retrodeck.json`` is context."""

    kind = "retrodeck"
    kinds = ("retrodeck",)
    _APP_ID = "net.retrodeck.retrodeck"

    def __init__(self, home: str, machine: Machine, retrodeck_json: str | None) -> None:
        self._home = home
        self._machine = machine
        self._config = _parse_retrodeck_config(retrodeck_json)

    def _config_path(self, key: str, fallback_subdir: str) -> tuple[str, str]:
        """Resolve a RetroDECK path and its provenance from ``retrodeck.json``."""
        paths = self._config.get("paths")
        if isinstance(paths, dict):
            value = paths.get(key, "")
            if value:
                return value, f"retrodeck.json: paths.{key}"
        fallback = (
            os.path.join(self._home, "retrodeck", fallback_subdir)
            if fallback_subdir
            else os.path.join(self._home, "retrodeck")
        )
        return fallback, f"default: {key} unset → {fallback}"

    def root(self) -> str:
        """The RetroDECK home directory (``rd_home_path`` or the fallback)."""
        return self._config_path("rd_home_path", "")[0]

    def saves_root(self) -> str:
        """The RetroDECK saves root (``saves_path`` or the fallback)."""
        return self._config_path("saves_path", "saves")[0]

    def bios_dir(self) -> str:
        """The RetroDECK BIOS directory (``bios_path`` or the fallback)."""
        return self._config_path("bios_path", "bios")[0]

    def roms_dir(self) -> str:
        """The RetroDECK ROMs directory (``roms_path`` or the fallback)."""
        return self._config_path("roms_path", "roms")[0]

    def health(self) -> str:
        """Installation health — config readable, root present."""
        if not self._config:
            return HEALTH_CONFIG_UNREADABLE
        if not self._machine.exists(self.root()):
            return HEALTH_ROOT_MISSING
        return HEALTH_OK

    def _retroarch_config_dir(self) -> str:
        return os.path.join(self._home, ".var", "app", self._APP_ID, "config", "retroarch")

    def _core_path(self, core_so: str) -> str | None:
        """Resolve a core ``.so`` basename against the cfg's ``libretro_directory``.

        The configured value points into the sandbox (``/app/...``); translate it
        to the host deployment. ``None`` when nothing resolvable — never a guess.
        """
        text = self._machine.read_text(os.path.join(self._home, RETRODECK_CFG_SUFFIX))
        if text is None:
            return None
        raw = parse_cfg_text(text).get("libretro_directory", "")
        if not raw:
            return None
        cores_dir = _flatpak_host_path(self._machine, self._home, self._APP_ID, raw)
        if cores_dir is None:
            return None
        return os.path.join(cores_dir, core_so)

    def save_location(self, *, content_path: str | None = None, core_so: str | None = None) -> SavePlacement:
        """Where this RetroDECK's RetroArch keeps the save for *content_path* under *core_so*.

        ``core_so`` is the core's ``.so`` basename (e.g.
        ``"mupen64plus_next_libretro.so"``) or a full path; atlas resolves
        ``library_name`` from the binary. Both arguments are optional — missing
        ones leave holes and stated caveats, never guesses.
        """
        caveats: list[str] = []
        health = self.health()
        if health != HEALTH_OK:
            caveats.append(f"installation health: {health}")
        return _retroarch_save_location(
            self._machine,
            home=self._home,
            global_cfg_path=os.path.join(self._home, RETRODECK_CFG_SUFFIX),
            cfg_label="retroarch.cfg",
            override_config_dir=os.path.join(self._retroarch_config_dir(), "config"),
            defaults=RETRODECK_DEFAULTS,
            content_path=content_path,
            core_so=core_so,
            core_path_resolver=self._core_path,
            extra_caveats=tuple(caveats),
        )


def _parse_settings_sh(text: str, *, home: str) -> dict[str, str]:
    """Parse EmuDeck's ``settings.sh`` (``key=value`` shell lines) into a dict.

    Values are quote-stripped and literal ``$HOME`` / ``${HOME}`` is expanded
    against the machine home — the two forms EmuDeck actually writes. Anything
    fancier stays verbatim; atlas does not emulate a shell.
    """
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        value = value.replace("${HOME}", home).replace("$HOME", home)
        if key:
            result[key] = value
    return result


class EmuDeck:
    """An EmuDeck arrangement — ``settings.sh`` is its truth, the standalone
    ``org.libretro.RetroArch`` Flatpak is its RetroArch.

    The handle carries both descriptions (``kinds``): EmuDeck *is* a configured
    standalone RetroArch, so both statements are true of the same installation.
    """

    kind = "emudeck"
    kinds = ("emudeck", "standalone_retroarch_flatpak")
    _RA_APP_ID = "org.libretro.RetroArch"

    def __init__(self, home: str, machine: Machine, settings_text: str) -> None:
        self._home = home
        self._machine = machine
        self._settings = _parse_settings_sh(settings_text, home=home)

    def _setting_path(self, key: str, fallback_subdir: str) -> tuple[str, str]:
        value = self._settings.get(key, "")
        if value:
            return value, f"settings.sh: {key}"
        fallback = os.path.join(self._home, "Emulation", fallback_subdir)
        return fallback, f"default: {key} unset → {fallback} (EmuDeck default)"

    def root(self) -> str:
        """The EmuDeck ``Emulation`` tree root (parent of ``romsPath``)."""
        return os.path.dirname(self._setting_path("romsPath", "roms")[0])

    def saves_root(self) -> str:
        """EmuDeck's saves root (``savesPath`` or the default)."""
        return self._setting_path("savesPath", "saves")[0]

    def bios_dir(self) -> str:
        """EmuDeck's BIOS directory (``biosPath`` or the default)."""
        return self._setting_path("biosPath", "bios")[0]

    def health(self) -> str:
        """Installation health — the saves root must be present."""
        if not self._machine.exists(self.saves_root()):
            return HEALTH_ROOT_MISSING
        return HEALTH_OK

    def _retroarch_config_dir(self) -> str:
        return os.path.join(self._home, ".var", "app", self._RA_APP_ID, "config", "retroarch")

    def _core_path(self, core_so: str) -> str | None:
        text = self._machine.read_text(os.path.join(self._home, STANDALONE_FLATPAK_CFG_SUFFIX))
        if text is None:
            return None
        raw = parse_cfg_text(text).get("libretro_directory", "")
        if not raw:
            return None
        cores_dir = _flatpak_host_path(self._machine, self._home, self._RA_APP_ID, raw)
        if cores_dir is None:
            return None
        return os.path.join(cores_dir, core_so)

    def save_location(self, *, content_path: str | None = None, core_so: str | None = None) -> SavePlacement:
        """Where EmuDeck's RetroArch keeps the save — resolved from the standalone Flatpak cfg."""
        caveats: list[str] = []
        health = self.health()
        if health != HEALTH_OK:
            caveats.append(f"installation health: {health}")
        return _retroarch_save_location(
            self._machine,
            home=self._home,
            global_cfg_path=os.path.join(self._home, STANDALONE_FLATPAK_CFG_SUFFIX),
            cfg_label="retroarch.cfg",
            override_config_dir=os.path.join(self._retroarch_config_dir(), "config"),
            defaults=EMUDECK_DEFAULTS,
            content_path=content_path,
            core_so=core_so,
            core_path_resolver=self._core_path,
            extra_caveats=tuple(caveats),
        )


class _RetroArchInstall:
    """Shared behavior for a bare RetroArch install (standalone Flatpak or native).

    The saves root comes from the cfg's ``savefile_directory``; when unset,
    RetroArch resolves it to the ROM's own directory (``runloop.c:8786``) — a
    rule, not a hole. Bare installs get RetroArch's upstream compile-time
    defaults, under which ``sort_savefiles_enable`` is **true**.
    """

    kinds: tuple[str, ...] = ()
    _app_id: str | None = None

    def __init__(self, home: str, machine: Machine, cfg_suffix: str) -> None:
        self._home = home
        self._machine = machine
        self._cfg_suffix = cfg_suffix

    def _cfg_path(self) -> str:
        return os.path.join(self._home, self._cfg_suffix)

    def root(self) -> str:
        """The RetroArch config directory (the folder holding ``retroarch.cfg``)."""
        return os.path.dirname(self._cfg_path())

    def health(self) -> str:
        """Bare installs are healthy when their config marker exists (it is the marker)."""
        return HEALTH_OK

    def _core_path(self, core_so: str) -> str | None:
        text = self._machine.read_text(self._cfg_path())
        if text is None:
            return None
        raw = parse_cfg_text(text).get("libretro_directory", "")
        if not raw:
            return None
        if self._app_id is not None:
            cores_dir = _flatpak_host_path(self._machine, self._home, self._app_id, raw)
        else:
            cores_dir = raw
        if cores_dir is None:
            return None
        return os.path.join(cores_dir, core_so)

    def save_location(self, *, content_path: str | None = None, core_so: str | None = None) -> SavePlacement:
        """Where this RetroArch install keeps the save for *content_path* under *core_so*."""
        return _retroarch_save_location(
            self._machine,
            home=self._home,
            global_cfg_path=self._cfg_path(),
            cfg_label="retroarch.cfg",
            override_config_dir=os.path.join(self.root(), "config"),
            defaults=UPSTREAM_DEFAULTS,
            content_path=content_path,
            core_so=core_so,
            core_path_resolver=self._core_path,
        )


class StandaloneRetroArchFlatpak(_RetroArchInstall):
    """The ``org.libretro.RetroArch`` Flatpak install."""

    kind = "standalone_retroarch_flatpak"
    kinds = ("standalone_retroarch_flatpak",)
    _app_id = "org.libretro.RetroArch"

    def __init__(self, home: str, machine: Machine) -> None:
        super().__init__(home, machine, STANDALONE_FLATPAK_CFG_SUFFIX)


class NativeRetroArch(_RetroArchInstall):
    """A native ``~/.config/retroarch`` install."""

    kind = "native_retroarch"
    kinds = ("native_retroarch",)

    def __init__(self, home: str, machine: Machine) -> None:
        super().__init__(home, machine, NATIVE_CFG_SUFFIX)


Installation = RetroDeck | EmuDeck | StandaloneRetroArchFlatpak | NativeRetroArch
