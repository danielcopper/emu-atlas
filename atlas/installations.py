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
from glob import escape as _glob_escape
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

from dataclasses import dataclass, replace as _dc_replace

from atlas.content_path import content_file_name, content_system_dir, split_content_path
from atlas.esde import (
    KIND_LIBRETRO,
    EmulatorSpec,
    GamelistSelections,
    merge_layers,
    parse_es_systems,
    parse_gamelist,
)
from atlas.evidence import arrangement_caveats
from atlas.firmware import (
    CAVEAT_CATALOGUE_UNAVAILABLE,
    CAVEAT_CATALOGUE_UNESTABLISHED,
    CAVEAT_CATALOGUE_UNREADABLE,
    CAVEAT_CORE_DIR_UNRESOLVED,
    CAVEAT_CORE_ENUMERATION_INCOMPLETE,
    CAVEAT_FIRMWARE_ROOT_MISSING,
    CAVEAT_INFO_PATH_UNRESOLVED,
    Catalogue,
    CatalogueEntry,
    CoreDeclarations,
    FirmwareAnswer,
    FirmwareContext,
    FirmwareIdentification,
    load_hashes,
    read_core_declarations,
)
from atlas.firmware import firmware_for_core as _resolve_for_core
from atlas.firmware import firmware_for_system as _resolve_for_system
from atlas.firmware import firmware_inventory as _resolve_inventory
from atlas.firmware import identify_firmware as _resolve_identification
from atlas.machine import (
    KIND_DIRECTORY,
    KIND_FILE,
    KIND_MISSING,
    READ_MISSING,
    READ_OK,
    SYMLINK_HOPS,
    CoreInfo,
    CoreOption,
    Machine,
)
from atlas.oddities import MODE_ALWAYS, CoreCard, SaveMode, VerifiedOn, lookup_audit, lookup_card
from atlas.placement import (
    CAVEAT_APP_RELATIVE_PATH_UNEXPANDED,
    CAVEAT_CARD_GENERATION_MISMATCH,
    CAVEAT_CARD_MODE_UNCONFIRMED,
    CAVEAT_CFG_LINE_DROPPED,
    CAVEAT_CFG_VALUE_REJECTED,
    CAVEAT_CONTENT_DIR_OBSERVATION,
    CAVEAT_CONTENT_PATH_UNNAMED,
    CAVEAT_DEAD_SYMLINK,
    CAVEAT_SORTED_DIR_UNCREATABLE,
    CAVEAT_CORE_MULTI_OPTION,
    CAVEAT_CORE_SUSPECT,
    CAVEAT_CORE_UNAUDITED,
    CAVEAT_CORE_UNQUERYABLE,
    CAVEAT_INVALID_SAVE_DIRECTORY,
    CAVEAT_UNVERIFIED_VERSION,
    CAVEAT_PER_GAME_OVERRIDE,
    CAVEAT_PER_GAME_OVERRIDES_PRESENT,
    CAVEAT_FILENAMES_CONTENT_CONDITIONAL,
    CAVEAT_FILENAMES_UNVERIFIED,
    CAVEAT_FILE_SET_SPANS_ROOTS,
    CAVEAT_NO_CORE,
    CAVEAT_SANDBOX_PATH_UNTRANSLATED,
    CAVEAT_SAVE_DIR_UNLISTABLE,
    CAVEAT_SORTED_DIR_MISSING,
    CAVEAT_SYMLINK_LOOP,
    CAVEAT_SYSTEM_DIR_CLEARED,
    CAVEAT_UNKNOWN_OPTION_VALUE,
    HOLE_CONTENT_DIR,
    ROOT_CONTENT_DIRECTORY,
    ROOT_SYSTEM_DIRECTORY,
    TEMPLATE_ROM_STEM,
    UNKNOWN_FILE_SET,
    UNRESOLVED_STANDALONE,
    Caveat,
    FileSet,
    Granularity,
    RootKind,
    SavePlacement,
    Unresolved,
    build_save_placement,
    file_set_holes,
    needs_with_file_set,
)
from atlas.retroarch_cfg import (
    IGNORED_LINE_DROPPED,
    UPSTREAM_DEFAULTS,
    IgnoredSetting,
    LayoutDefaults,
    RejectedDirectory,
    RetroArchCfg,
    chain_bool,
    chain_value,
    expand_home,
    is_app_relative,
    parse_cfg_text,
    resolve_save_layout,
)

# Health issue codes — stable identifiers clients and vectors branch on.
# An installation is healthy exactly when it has no issues; every issue keeps
# marker existence, read status, parse status, companion state, and root state
# apart instead of collapsing them into one lossy string (REVIEW H10).
HEALTH_ISSUE_MARKER_MISSING = "marker-missing"
HEALTH_ISSUE_MARKER_UNREADABLE = "marker-unreadable"
HEALTH_ISSUE_MARKER_INVALID = "marker-invalid"
HEALTH_ISSUE_ROOT_MISSING = "root-missing"
HEALTH_ISSUE_SAVES_ROOT_MISSING = "saves-root-missing"
HEALTH_ISSUE_COMPANION_CONFIG_MISSING = "companion-config-missing"
HEALTH_ISSUE_CONFIG_UNREADABLE = "config-unreadable"


@dataclass(frozen=True, slots=True)
class Health:
    """Structured installation health — *ok* is simply the absence of issues.

    Each issue is a :class:`~atlas.placement.Caveat` whose ``code`` is one of
    the ``HEALTH_ISSUE_*`` constants; ``data`` carries the affected path plus
    whatever else the finding established (the read ``status`` behind an
    unreadable marker, the ``key`` behind an invalid one). Handles never hide a
    present-but-broken installation — they report it with the issues attached.

    These issues *are* caveats, and they travel as themselves: every answer
    computed on a broken installation carries the findings directly in its own
    ``caveats``, under their own codes and ahead of what the query itself could
    not resolve. Nothing wraps them in a category code with the real condition
    nested in ``data`` — that shape hides a distinct, stable code behind a
    discriminator a client has to unpack, and the firmware route retired it for
    the same reason.

    Every answer, not only the ones a finding bears on: whether the arrangement
    is broken is true of the installation however it was asked, and a map of
    which finding affects which answer would have to be maintained and could
    rot into silence. What broke is in the ``data``; judging relevance is the
    caller's. Each route derives the findings from the reads it already made,
    never from a second :meth:`Installation.health` call, so an answer and the
    findings beside it were read from one revision of each source (REVIEW M4).
    """

    issues: tuple[Caveat, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)

# The file RetroArch reads, and the Flatpak apps that ship one.
RETROARCH_CFG = "retroarch.cfg"
RETRODECK_APP_ID = "net.retrodeck.retrodeck"
RETROARCH_FLATPAK_APP_ID = "org.libretro.RetroArch"

# Config markers, as ``home``-relative suffixes.
RETRODECK_JSON_SUFFIX = os.path.join(
    ".var", "app", RETRODECK_APP_ID, "config", "retrodeck", "retrodeck.json"
)
RETRODECK_CFG_SUFFIX = os.path.join(
    ".var", "app", RETRODECK_APP_ID, "config", "retroarch", RETROARCH_CFG
)
EMUDECK_SETTINGS_SUFFIX = os.path.join(".config", "EmuDeck", "settings.sh")
STANDALONE_FLATPAK_CFG_SUFFIX = os.path.join(
    ".var", "app", RETROARCH_FLATPAK_APP_ID, "config", "retroarch", RETROARCH_CFG
)
NATIVE_CFG_SUFFIX = os.path.join(".config", "retroarch", RETROARCH_CFG)


# Flatpak binds the app's private XDG directories into the sandbox under /var, so
# an emulator running inside one writes those spellings into its own config: the
# live RetroDECK 0.10.9b cfg names its override directory
# "/var/config/retroarch/config". Verified on this machine — /var/config and
# ~/.var/app/<app id>/config are the same inode, as are /var/data and /var/cache
# against .../data and .../cache (docs/research/retrodeck-save-placement.md,
# "Flatpak sandbox spellings").
_SANDBOX_XDG_BINDS: tuple[tuple[str, str], ...] = (
    ("/var/config", "config"),
    ("/var/data", "data"),
    ("/var/cache", "cache"),
)

# Prefixes that name something else inside the sandbox than on the host, once the
# binds above, /app, and the machine's own home are ruled out: the rest of /var is
# the runtime's own filesystem (a different device from the host's /var), and
# /run/user is the sandbox's private runtime directory. Everything else a cfg can
# name — /run/media, /home, /mnt — is shared with the host and passes untouched.
_SANDBOX_ONLY_PREFIXES: tuple[str, ...] = ("/var/", "/run/user/")

# On an ostree host (Fedora Silverblue, Bazzite — both ship RetroDECK) /home is a
# symlink to /var/home, so real home directories live *under* /var and are shared
# with the sandbox like any other home. The machine's own home is checked first,
# but the cfg may spell a home path either way (RetroArch resolves the symlink,
# the caller may not), so the literal prefix is host-side too.
_OSTREE_HOME = "/var/home/"


def _app_relative_caveat(key: str, configured: str) -> Caveat:
    """A path value RetroArch resolves against its own executable's directory.

    ``fill_pathname_expand_special`` expands a leading ``:`` against
    ``fill_pathname_application_dir`` (file_path.c:1066-1101), which on Linux
    is the directory of the running RetroArch binary, read from
    ``/proc/<pid>/exe`` (file_path.c:1421-1455). That is a property of the
    process that will run, not of anything on this disk, so atlas states the
    value as configured instead of inventing an expansion for it.
    """
    return Caveat(
        CAVEAT_APP_RELATIVE_PATH_UNEXPANDED,
        f'{key} = "{configured}" is relative to the RetroArch application directory (the ":" '
        "prefix, file_path.c:1066-1101) — that directory is the running executable's own, which "
        "atlas cannot read off this machine, so the value stays unexpanded and unverified",
        {"key": key, "path": configured},
    )


@dataclass(frozen=True, slots=True)
class _CfgPath:
    """A cfg path value as the host can read it, and what the translation did.

    ``path`` is ``None`` when the value names no location atlas can read: one
    that exists only inside the Flatpak sandbox, or one RetroArch resolves
    against its own application directory (``app_relative``). Either way atlas
    says so rather than resolving against a host path that means something else.
    """

    key: str
    configured: str
    path: str | None
    translated: bool = False
    app_relative: bool = False

    @property
    def caveats(self) -> tuple[Caveat, ...]:
        """The degradation, when the configured spelling has no host location."""
        if self.path is not None:
            return ()
        if self.app_relative:
            return (_app_relative_caveat(self.key, self.configured),)
        return (
            Caveat(
                CAVEAT_SANDBOX_PATH_UNTRANSLATED,
                f'{self.key} = "{self.configured}" names a location inside the Flatpak sandbox that '
                "has no equivalent on this host — atlas cannot read what the emulator reads there",
                {"key": self.key, "path": self.configured},
            ),
        )

    @property
    def note(self) -> str:
        """Provenance suffix naming the host spelling — empty when nothing moved."""
        if self.path is None or not self.translated:
            return ""
        return f" — Flatpak sandbox path, on the host {self.path}"


@dataclass(frozen=True, slots=True)
class _Sandbox:
    """Where a Flatpak app's own cfg spellings live when read from the host.

    Every cfg value that becomes a host read passes through here: an app writes
    its config from inside its sandbox, so the paths in it are sandbox paths.
    ``app_id`` is ``None`` for a native install — its cfg is host-native, a
    ``/var/...`` value there is a real (if unusual) host path, and translating
    it would invent a location the emulator never uses.
    """

    machine: Machine
    home: str
    app_id: str | None

    def host(self, key: str, path: str) -> _CfgPath:
        """*path* as this host reads it, with the provenance *key* names it by."""
        translated, was_sandbox = self._translate(path)
        return _CfgPath(key, path, translated, was_sandbox)

    def bundled(self, path: str) -> str | None:
        """A path the app ships inside its own tree, as the host reads it.

        No config key is involved, so no provenance travels with it: the caller
        knows which file it asked for and reports a miss in its own terms.
        """
        return self._translate(path)[0]

    def _translate(self, path: str) -> tuple[str | None, bool]:
        """``(host path or None, whether this was a sandbox spelling)``.

        The XDG binds are a deterministic per-app mapping, so they translate
        unconditionally and the caller's own existence check stays the one that
        decides usability. ``/app`` has two possible deployment roots (system
        and user install), so there the existing one is the answer.
        """
        app_id = self.app_id
        if app_id is None or not path.startswith("/") or self._is_host_home(path):
            return path, False
        for prefix, xdg_dir in _SANDBOX_XDG_BINDS:
            if path == prefix or path.startswith(prefix + "/"):
                rest = path[len(prefix) :].lstrip("/")
                app_dir = os.path.join(self.home, ".var", "app", app_id, xdg_dir)
                return (os.path.join(app_dir, rest) if rest else app_dir), True
        if path.startswith("/app/"):
            return self._deployment_path(app_id, path[len("/app/") :]), True
        if path.startswith(_SANDBOX_ONLY_PREFIXES):
            return None, True
        return path, False

    def _is_host_home(self, path: str) -> bool:
        """Does *path* lie in a home directory, which is shared with the sandbox?

        Checked before the sandbox-only prefixes because an ostree host puts
        real homes under ``/var/home`` — a machine whose home is
        ``/var/home/deck`` must not have its own configured directories read as
        sandbox-internal.
        """
        return path == self.home or path.startswith((self.home + "/", _OSTREE_HOME))

    def _deployment_path(self, app_id: str, rest: str) -> str | None:
        """The app's ``/app`` tree on the host: the deployment's ``files/``."""
        for base in (
            f"/var/lib/flatpak/app/{app_id}/current/active/files",
            os.path.join(self.home, ".local", "share", "flatpak", "app", app_id, "current", "active", "files"),
        ):
            candidate = os.path.join(base, rest)
            if self.machine.path_kind(candidate) != KIND_MISSING:
                return candidate
        return None

    def cfg_path(self, key: str, raw: str | None) -> _CfgPath | None:
        """One cfg key resolved to a host path — ``None`` when the key is unset.

        Blank and the literal ``"default"`` are RetroArch's "unset" spellings
        (:func:`expand_home`), and unset is the caller's own question. An
        application-relative value is set but unreadable from here, which is a
        third answer: the value comes back with no host path and the caveat
        that says why.
        """
        if raw is None:
            return None
        if is_app_relative(raw):
            return _CfgPath(key, raw, None, app_relative=True)
        expanded = expand_home(raw, home=self.home)
        return self.host(key, expanded) if expanded is not None else None


def _core_directory_in(sandbox: _Sandbox, global_text: str) -> str | None:
    """A cfg snapshot's ``libretro_directory`` as a host path, or ``None``.

    Where the core binaries live is a question every arrangement asks the same
    way; only which app's spellings the cfg is written in differs.
    """
    resolved = sandbox.cfg_path("libretro_directory", parse_cfg_text(global_text).get("libretro_directory"))
    return resolved.path if resolved is not None else None


# One file of the override chain as it is read: its provenance label and its
# text, in load order (global cfg first, game override last).
_CfgLayer = tuple[str, str]


def _resolve_symlink_chain(machine: Machine, path: str) -> tuple[str | None, list[tuple[str, str]]]:
    """Resolve symlink components in *path* through the seam, kernel-style.

    Walks components left to right via ``readlink``, splicing targets in
    (relative targets against the link's directory). Returns the fully resolved
    path and every ``(link, target)`` traversed — an empty list means no symlink
    was involved.

    ``None`` when the chain does not settle within
    :data:`~atlas.machine.SYMLINK_HOPS` hops. The kernel refuses the whole
    resolution there (``ELOOP``) rather than stopping partway, and so does this:
    the path the walk had reached after the last hop is still a link, and
    handing it back named a directory nothing can ever open as if it were the
    physical one — silently, because it looks like an ordinary answer. The links
    traversed are returned either way, so the caller can name the chain.

    One of three kernel walks in atlas, next to
    :func:`atlas.firmware.resolve_links` (which answers the path alone) and
    ``FixtureMachine._resolve`` (which additionally refuses to step through a
    non-directory, because it answers for paths rather than resolving them).
    This one exists for the links: a caveat has to name the chain it refused.
    A fidelity finding about symlinks, ``..`` or the hop limit belongs in all
    three; the limit itself is shared, never copied.
    """
    links: list[tuple[str, str]] = []
    current = path
    while True:
        step = _splice_first_link(machine, current)
        if step is None:
            return current, links
        if len(links) == SYMLINK_HOPS:
            return None, links
        current, link = step
        links.append(link)


def _splice_first_link(machine: Machine, path: str) -> tuple[str, tuple[str, str]] | None:
    """One kernel-style step: the leftmost symlink component replaced by its target.

    Returns the spliced path and the ``(link, target)`` traversed, or ``None``
    when no component is a link — the path is then fully resolved. A relative
    target is spliced against the directory holding the link.
    """
    parts = path.split("/")
    for i in range(2, len(parts) + 1):
        prefix = "/".join(parts[:i])
        target = machine.readlink(prefix)
        if target is None:
            continue
        link = (prefix, target)
        if not target.startswith("/"):
            target = os.path.normpath(os.path.join(os.path.dirname(prefix), target))
        rest = "/".join(parts[i:])
        return target + ("/" + rest if rest else ""), link
    return None


def _link_view(machine: Machine, directory: str) -> tuple[str | None, tuple[Caveat, ...]]:
    """The link-resolved view of a final directory (REVIEW M7).

    Returns ``(physical_dir, caveats)``: ``physical_dir`` is the fully
    resolved backing directory when *directory* traverses live symlinks —
    RetroDECK's ``dir_prep`` pattern makes the emulator-side path and the
    physical path two truthful answers to different questions. A traversal
    that ends nowhere yields a ``dead-symlink`` caveat instead: the
    emulator-side directory is dead, and writing there will fail.

    A chain that never settles is the other way to end nowhere, and it is its
    own code: a dead link points at a name that could be created, while a loop
    is a cycle somebody has to break, and the two are not one fact.
    """
    resolved, links = _resolve_symlink_chain(machine, directory)
    if not links:
        return None, ()
    if resolved is None:
        link, target = links[0]
        return None, (
            Caveat(
                CAVEAT_SYMLINK_LOOP,
                f"{directory} enters the symlink {link} -> {target} and never settles — the chain is "
                f"longer than the {SYMLINK_HOPS} hops the kernel follows, so it answers ELOOP and "
                "nothing can be read or written through this directory",
                {"path": directory, "link": link, "target": target},
            ),
        )
    if machine.path_kind(resolved) == KIND_MISSING:
        link, target = links[-1]
        return None, (
            Caveat(
                CAVEAT_DEAD_SYMLINK,
                f"{directory} traverses the symlink {link} -> {target}, which resolves to "
                f"{resolved} — a path that does not exist: the emulator-side directory is dead",
                {"link": link, "target": target, "resolved": resolved},
            ),
        )
    return (resolved if resolved != directory else None), ()


def _ignored_caveats(ignored: Sequence[IgnoredSetting]) -> tuple[Caveat, ...]:
    """State the settings the configs make and RetroArch does not apply."""
    return tuple(map(_ignored_caveat, ignored))


def _ignored_caveat(setting: IgnoredSetting) -> Caveat:
    """One ignored setting as a caveat — the file says it, the emulator does not do it."""
    if setting.kind == IGNORED_LINE_DROPPED:
        return Caveat(
            CAVEAT_CFG_LINE_DROPPED,
            f"{setting.layer}: the line {setting.text!r} sets nothing — a key must be followed by "
            "'=' after optional whitespace, and '=' is itself a key character, so RetroArch's "
            f"parser drops the line (config_file.c:596-623) and {setting.key} stays unset by it",
            {"key": setting.key, "line": setting.text},
        )
    return Caveat(
        CAVEAT_CFG_VALUE_REJECTED,
        f'{setting.layer}: {setting.key} = "{setting.text}" is not a value RetroArch accepts — a '
        "boolean is exactly 1, true, 0 or false, case-sensitively (config_file.c:1227-1262) — so "
        "the setting keeps the value it had before this file; it does not become false",
        {"key": setting.key, "value": setting.text},
    )


def _global_options_file(
    layers: Sequence[_CfgLayer], *, sandbox: _Sandbox, retroarch_config_dir: str
) -> tuple[str, tuple[Caveat, ...]]:
    """The global core-options file: ``core_options_path``, else the default name.

    Read through the chain like RetroArch does, and translated out of its
    sandbox spelling; an untranslatable one keeps the configured value, so the
    read misses and the caveat says why instead of the answer claiming the
    default file governed.
    """
    raw, ignored = chain_value(layers, "core_options_path")
    caveats = _ignored_caveats(ignored)
    configured = sandbox.cfg_path("core_options_path", raw)
    if configured is None:
        return os.path.join(retroarch_config_dir, "retroarch-core-options.cfg"), caveats
    return (
        configured.path if configured.path is not None else configured.configured,
        (*caveats, *configured.caveats),
    )


def _option_file_candidates(
    *,
    override_config_dir: str,
    global_file: str,
    library_name: str | None,
    content_dir_name: str | None,
    rom_stem: str | None,
    game_specific_options: bool,
    per_core_options: bool,
) -> tuple[list[str], bool]:
    """The options files that could govern an option, in RetroArch's priority order.

    Game ``.opt``, folder ``.opt``, per-core ``.opt`` (when
    ``global_core_options`` is off), then the global options file — the same
    order ``validate_per_core_options`` walks.

    Returns ``(candidates, unconfirmed)``, where ``unconfirmed`` is true when a
    path that needs ``library_name`` could exist but cannot be built because the
    core was unqueryable.
    """
    candidates: list[str] = []
    if library_name and game_specific_options:
        if rom_stem:
            candidates.append(os.path.join(override_config_dir, library_name, f"{rom_stem}.opt"))
        if content_dir_name:
            candidates.append(os.path.join(override_config_dir, library_name, f"{content_dir_name}.opt"))
    unconfirmed = library_name is None and (game_specific_options or per_core_options)
    if library_name and per_core_options:
        candidates.append(os.path.join(override_config_dir, library_name, f"{library_name}.opt"))
    candidates.append(global_file)
    return candidates, unconfirmed


def _core_options_value(
    machine: Machine,
    *,
    override_config_dir: str,
    global_file: str,
    library_name: str | None,
    content_dir_name: str | None,
    rom_stem: str | None,
    option_key: str,
    option_default: str,
    game_specific_options: bool,
    per_core_options: bool,
) -> tuple[str, str, str, bool]:
    """Read a core option the way RetroArch does — first existing file is THE source.

    Priority (``runloop.c`` ``validate_per_core_options``): game ``.opt``,
    folder ``.opt``, per-core ``.opt`` (when ``global_core_options`` is off),
    then *global_file*. A key absent from the governing file falls back to the
    core default — it does not fall through to another file.

    Returns ``(value, provenance, options_file, unconfirmed)``:
    ``options_file`` is the file a caller would edit to change the option, and
    ``unconfirmed`` is true when a governing file whose path needs
    ``library_name`` could exist but cannot be checked (the core was
    unqueryable) — the returned value may then not be the effective one.
    """
    candidates, unconfirmed = _option_file_candidates(
        override_config_dir=override_config_dir,
        global_file=global_file,
        library_name=library_name,
        content_dir_name=content_dir_name,
        rom_stem=rom_stem,
        game_specific_options=game_specific_options,
        per_core_options=per_core_options,
    )

    for path in candidates:
        text = machine.read_text(path).text
        if text is None:
            continue
        parsed = parse_cfg_text(text)
        if option_key in parsed:
            return (
                parsed[option_key],
                f'{os.path.basename(path)}: {option_key} = "{parsed[option_key]}"',
                path,
                unconfirmed,
            )
        return (
            option_default,
            f'core default: {option_key} = "{option_default}" ({os.path.basename(path)} has no entry)',
            path,
            unconfirmed,
        )
    return (
        option_default,
        f'core default: {option_key} = "{option_default}" (no options file present)',
        global_file,
        unconfirmed,
    )


@dataclass(frozen=True, slots=True)
class _SaveQuery:
    """One save-location question: the arrangement, its configs, and what is asked.

    The arrangements differ only in which cfg governs them and how their cores
    are found, so they hand the shared resolver the same question object —
    ``global_text`` is the global cfg's content, read exactly once by the
    caller (REVIEW M4), and ``sandbox`` says which app's spellings the cfg is
    written in (the machine home travels with it).
    """

    sandbox: _Sandbox
    global_cfg_path: str
    global_text: str | None
    cfg_label: str
    override_config_dir: str
    defaults: LayoutDefaults
    content_path: str | None
    core_so: str | None
    core_path_resolver: Callable[[str], str | None]
    arrangement: str
    arrangement_version: str | None
    extra_sources: tuple[str, ...] = ()
    extra_caveats: tuple[Caveat, ...] = ()


@dataclass(frozen=True, slots=True)
class _Content:
    """The ROM's coordinates — every value a placement fills in from the content.

    All of them stay ``None`` when no content was named: the holes then remain
    holes (``needs``), and nothing about the ROM is guessed. ``rom_stem`` is
    ``None`` for a named content path too when RetroArch's path math derives no
    name from it — the directory still resolves, but nothing is named after a
    name that does not exist.

    ``dir_path`` and ``system_dir`` are the content's directory computed by two
    different pieces of upstream path math, and they are not always the same
    string: the save side reads it off ``runtime_content_path_basename``, the
    system side off the raw content path (:func:`content_system_dir`). Each
    route answers with the one its own caller in RetroArch computes.
    """

    dir_path: str | None = None
    dir_name: str | None = None
    rom_stem: str | None = None
    system_dir: str | None = None


def _content_coordinates(content_path: str | None) -> _Content:
    """Split a content path into the coordinates the placement asks it for.

    An empty string names nothing at all — not even a directory — so it fills
    no coordinate and every hole stays a hole, exactly as if no content had been
    passed. The caller still states *that* it was asked with one
    (:func:`_unnamed_content_caveat`), and the empty answer stays out of the
    directory: a placement's ``dir`` may not be empty.
    """
    if not content_path:
        return _Content()
    dir_path, dir_name, rom_stem = split_content_path(content_path)
    return _Content(dir_path, dir_name, rom_stem or None, content_system_dir(content_path))


def _unnamed_content_caveat(content_path: str) -> Caveat:
    """A content path RetroArch's own path math derives no name from.

    Reached by a path whose last component is empty and carries no dot
    (``/roms/psx/Game/``) — the truncation at runloop.c:8710-8711 finds nothing
    to cut, ``fill_pathname`` then concatenates the extension onto an empty
    name (file_path.c:345-358) and the save is called ``.srm`` — and by the
    empty string, which names nothing whatsoever. Naming files after an empty
    stem would make every dotfile in the directory a save, so atlas states the
    fact and observes nothing; a domain answer with a caveat, never an
    exception.
    """
    return Caveat(
        CAVEAT_CONTENT_PATH_UNNAMED,
        f"content path {content_path!r} names no file — RetroArch's path math "
        "(runloop.c:8673-8713) derives an empty name from it, so save file names are not stated "
        "and no files are observed; name the content file itself, without a trailing slash",
        {"content_path": content_path},
    )


@dataclass(frozen=True, slots=True)
class _CoreIdentity:
    """What the core binary answered about itself — and how the asking went.

    ``library_name`` is the value that names sort-by-core directories *and* the
    override directory, and it lives only in the binary; ``None`` means the
    core could not be queried (or none was named), never a guessed name.
    """

    info: CoreInfo | None = None
    library_name: str | None = None
    sources: tuple[str, ...] = ()
    caveats: tuple[Caveat, ...] = ()


def _identify_core(
    machine: Machine, *, core_so: str | None, core_path_resolver: Callable[[str], str | None]
) -> _CoreIdentity:
    """Load the named core and ask it its ``library_name`` — the same read RetroArch does."""
    if core_so is None:
        return _CoreIdentity(
            caveats=(
                Caveat(
                    CAVEAT_NO_CORE,
                    "no core given — per-core overrides and save-behaviour rule cards not checked: this answer "
                    "assumes a standard core, and a card-carrying core (e.g. one rooted in system_directory, like "
                    "Flycast) keeps its saves elsewhere entirely",
                ),
            )
        )
    so_path = core_so if os.sep in core_so else core_path_resolver(core_so)
    info = machine.query_core(so_path) if so_path else None
    if info is None:
        return _CoreIdentity(
            caveats=(
                Caveat(
                    CAVEAT_CORE_UNQUERYABLE,
                    f"core {core_so!r} could not be queried — library_name unknown, per-core overrides not checked",
                    {"core_so": core_so},
                ),
            )
        )
    return _CoreIdentity(
        info=info,
        library_name=info.library_name,
        sources=(
            f'core: {os.path.basename(so_path or core_so)} reports library_name "{info.library_name}"'
            " (retro_get_system_info)",
        ),
    )


@dataclass(frozen=True, slots=True)
class _OverrideGates:
    """The global-cfg switches that decide whether overrides apply, and from where."""

    auto_overrides: bool
    override_config_dir: str
    sources: tuple[str, ...] = ()
    caveats: tuple[Caveat, ...] = ()


def _override_gates(
    global_text: str | None,
    *,
    sandbox: _Sandbox,
    cfg_label: str,
    override_config_dir: str,
    config_file_dir: str,
) -> _OverrideGates:
    """The gates, read from the global cfg — an override cannot enable itself.

    ``auto_overrides_enable`` defaults true (config.def.h) and takes only
    RetroArch's boolean vocabulary; anything else leaves the default in place
    and is stated. It is read from the global cfg alone because RetroArch
    copies it into a local *before* it merges the overrides
    (``runloop.c:4941``, used at ``:5002-5003``) — an override genuinely cannot
    switch itself on or off. ``game_specific_options`` reads the same way but
    is decided later, so it is *not* a gate here: see :func:`_apply_card`.
    Where the override files are read from is :func:`_override_directory`'s
    question.
    """
    layers: list[_CfgLayer] = [(cfg_label, global_text)] if global_text is not None else []
    auto_overrides, auto_ignored = chain_bool(layers, "auto_overrides_enable", default=True)
    directory, dir_sources, dir_caveats = _override_directory(
        layers,
        sandbox=sandbox,
        cfg_label=cfg_label,
        override_config_dir=override_config_dir,
        config_file_dir=config_file_dir,
    )
    return _OverrideGates(
        auto_overrides, directory, dir_sources, (*_ignored_caveats(auto_ignored), *dir_caveats)
    )


def _override_directory(
    layers: Sequence[_CfgLayer],
    *,
    sandbox: _Sandbox,
    cfg_label: str,
    override_config_dir: str,
    config_file_dir: str,
) -> tuple[str, tuple[str, ...], tuple[Caveat, ...]]:
    """Where RetroArch looks for override ``.cfg`` and option ``.opt`` files.

    ``fill_pathname_application_special`` takes ``rgui_config_directory`` when
    it holds a value and otherwise falls back to the directory of the loaded
    ``retroarch.cfg`` (file_path_special.c:203-206) — one level above the
    platform default ``config`` subdirectory. So an *absent* key means the
    platform default, while a key set to blank or the literal ``default``
    clears the setting and moves the whole override tree up into the config
    directory itself: this key is a *handled* path setting
    (``SETTING_PATH(..., handle_setting=true)``, configuration.c:1736), so the
    generic loop writes whatever the config holds without testing it
    (:6536-6537) — an empty value included — and ``default`` is cleared
    separately at :6825-6826.

    That is the exact opposite of what blank does to ``savefile_directory``,
    which passes ``handle_setting=false`` (:1709), is skipped by that loop
    (:6534-6535), and reaches only the block that demands ``path_is_directory``
    (:6914-6933) — so a blank value there is refused and changes nothing.
    Neither is a bug; :func:`atlas.retroarch_cfg._resolve_savefile_directory`
    carries the full pair.

    A Flatpak's cfg spells the directory sandbox-side, so it is translated to
    where the host reads it; an untranslatable spelling keeps the configured
    value, so the reads miss, which is the truth (atlas cannot see those
    files) — falling back to the default directory would apply overrides that
    do not govern.

    A line RetroArch's parser drops here moves the entire override tree back to
    the platform default while the file appears to say otherwise, so it is
    stated even though the key is not a save-layout key itself.
    """
    raw, ignored = chain_value(layers, "rgui_config_directory")
    caveats = _ignored_caveats(ignored)
    if raw is None:
        return override_config_dir, (), caveats
    configured = sandbox.cfg_path("rgui_config_directory", raw)
    if configured is None:
        return (
            config_file_dir,
            (
                f'{cfg_label}: rgui_config_directory = "{raw}" — the setting is cleared, so '
                f"overrides live beside retroarch.cfg in {config_file_dir} "
                "(configuration.c:6825, file_path_special.c:196-207)",
            ),
            caveats,
        )
    return (
        configured.path if configured.path is not None else configured.configured,
        (f'{cfg_label}: rgui_config_directory = "{raw}"{configured.note}',),
        (*caveats, *configured.caveats),
    )


def _override_layers(
    machine: Machine,
    *,
    gates: _OverrideGates,
    library_name: str | None,
    content: _Content,
) -> tuple[list[tuple[str, str]], tuple[str, ...]]:
    """The override files that exist, in RetroArch's load order (configuration.c:7095).

    Core, then content-dir, then game — each read through the seam, each kept
    only if it is there. Returns the layers and any provenance the reading
    itself produced.
    """
    overrides: list[tuple[str, str]] = []
    if not gates.auto_overrides:
        return overrides, ('retroarch.cfg: auto_overrides_enable = "false" — override files not applied',)
    if library_name is None:
        return overrides, ()
    candidates = [
        (
            f"core override config/{library_name}/{library_name}.cfg",
            os.path.join(gates.override_config_dir, library_name, f"{library_name}.cfg"),
        )
    ]
    if content.dir_name:
        candidates.append(
            (
                f"content-dir override config/{library_name}/{content.dir_name}.cfg",
                os.path.join(gates.override_config_dir, library_name, f"{content.dir_name}.cfg"),
            )
        )
    if content.rom_stem:
        candidates.append(
            (
                f"game override config/{library_name}/{content.rom_stem}.cfg",
                os.path.join(gates.override_config_dir, library_name, f"{content.rom_stem}.cfg"),
            )
        )
    for label, path in candidates:
        text = machine.read_text(path).text
        if text is not None:
            overrides.append((label, text))
    return overrides, ()


@dataclass(frozen=True, slots=True)
class _SaveRoot:
    """The configured saves root as the host sees it — and whether it can look.

    ``reachable`` is false for a sandbox path with no host location: RetroArch's
    own "is this an existing directory" test cannot be reproduced from here,
    so it is not performed rather than answered from a read that never applied.
    """

    layout: RetroArchCfg
    reachable: bool = True
    sources: tuple[str, ...] = ()
    caveats: tuple[Caveat, ...] = ()


def _host_save_dir(sandbox: _Sandbox, layout: RetroArchCfg) -> _SaveRoot:
    """The resolved saves root as the host reads it — the cfg may spell it sandbox-side.

    An untranslatable spelling stays as configured: it is where the emulator
    writes, in the only namespace that names it, and the caveat states that
    atlas cannot follow it there. Substituting a host directory would answer
    with a location this RetroArch never touches. An application-relative value
    is the same kind of answer — the value as configured, unreachable from here
    — except that not even the emulator-side spelling is a path yet.
    """
    configured = layout.savefile_directory
    if configured is None:
        return _SaveRoot(layout)
    if is_app_relative(configured):
        return _SaveRoot(
            layout, reachable=False, caveats=(_app_relative_caveat("savefile_directory", configured),)
        )
    resolved = sandbox.host("savefile_directory", configured)
    if resolved.path == configured:
        return _SaveRoot(layout)
    if resolved.path is None:
        return _SaveRoot(layout, reachable=False, caveats=resolved.caveats)
    return _SaveRoot(
        _dc_replace(layout, savefile_directory=resolved.path),
        sources=(f'savefile_directory = "{configured}"{resolved.note}',),
    )


def _save_dir_probe(machine: Machine, sandbox: _Sandbox) -> Callable[[str], bool]:
    """``path_is_directory`` for one ``savefile_directory`` read, host-side.

    RetroArch runs this test on every value it reads (``configuration.c:6920``)
    and keeps the value only when it passes. A value atlas cannot test — one
    that exists only inside the Flatpak sandbox, one relative to the running
    executable's directory — passes: the emulator's own test still decides it,
    and answering "not a directory" here would reject a saves root that is very
    likely there. That the answer rests on an unperformed read is stated by the
    translation's own caveat.
    """

    def is_directory(value: str) -> bool:
        if is_app_relative(value):
            return True
        host = sandbox.host("savefile_directory", value).path
        return host is None or machine.path_kind(host) == KIND_DIRECTORY

    return is_directory


def _rejected_dir_caveats(
    machine: Machine,
    sandbox: _Sandbox,
    rejected: Sequence[RejectedDirectory],
    *,
    effective: str,
) -> tuple[Caveat, ...]:
    """The saves roots the configs state and RetroArch refuses, layer by layer.

    ``path_is_directory`` failed, so that read set nothing
    (``configuration.c:6920-6932``) and some other read decides the root. Which
    one is not fixed: usually the refusal is the last word and what stands
    preceded it — after an override, the global cfg's root rather than the
    platform default — but a refused global cfg can be followed by an override
    that supplies a usable root, and then the standing root was set afterwards.
    The message says whichever it was; claiming the wrong one would teach a
    reader a causality RetroArch does not have. The layer is named either way,
    because that is what tells a caller which file to fix.

    ``configured`` stays the cfg's own spelling — that is the line to edit —
    but inside a Flatpak that spelling is not where atlas looked. Where the two
    differ, the message names the host path too, so "not an existing directory"
    can be checked against the place the check was actually made.
    """
    caveats: list[Caveat] = []
    for entry in rejected:
        stands = (
            f"writes to {effective!r} instead, the root a later file in the chain set"
            if entry.superseded
            else f"keeps {effective!r}, the root that stood before this file"
        )
        host = sandbox.host("savefile_directory", entry.value).path
        looked_at = (
            f" (atlas looked at its host spelling {host!r})"
            if host is not None and host != entry.value
            else ""
        )
        caveats.append(
            Caveat(
                CAVEAT_INVALID_SAVE_DIRECTORY,
                f"{entry.layer}: savefile_directory {entry.value!r} is not an existing "
                f"directory{looked_at} — RetroArch refuses it and {stands} "
                "(configuration.c:6920-6932)",
                {"layer": entry.layer, "configured": entry.value, "effective": effective},
            )
        )
        # When the rejection is a dead symlink, say why (REVIEW M7).
        if host is not None:
            caveats.extend(_link_view(machine, host)[1])
    return tuple(caveats)


def _unaudited_caveats(so_basename: str) -> tuple[Caveat, ...]:
    """What the audit says about a core that carries no rule card (REVIEW H7).

    A missing card is not evidence the standard rule is complete — the verdict
    decides how loudly to say so.
    """
    caveats: list[Caveat] = []
    short_name = so_basename.removesuffix(".so").removesuffix("_libretro")
    verdict_entry = lookup_audit(short_name)
    if verdict_entry is None:
        caveats.append(
            Caveat(
                CAVEAT_CORE_UNAUDITED,
                f"core {short_name!r} has not been audited — the standard rule is assumed, "
                "not verified (docs/research/coverage-matrix.md)",
                {"core": short_name},
            )
        )
    elif verdict_entry.verdict == "suspect":
        caveats.append(
            Caveat(
                CAVEAT_CORE_SUSPECT,
                f"core {short_name!r} is a documented deviation suspect — this standard answer "
                "may miss an additional or different save stack (docs/research/core-audit.md)",
                {"core": short_name, "verdict": verdict_entry.verdict},
            )
        )
    elif verdict_entry.verdict == "multi-option":
        # The directory is established; the granularity is not, and an
        # empty `granularity` field alone reads as nothing-to-report
        # (issue #23). The audit knows which options decide it, so the
        # answer names them instead of leaving the caller with "unknown".
        options = ", ".join(verdict_entry.save_options)
        caveats.append(
            Caveat(
                CAVEAT_CORE_MULTI_OPTION,
                f"core {short_name!r} places its saves in this directory, but its file set and "
                f"granularity depend on core options atlas does not interpret ({options}) — the "
                "granularity here is unstated, not standard (docs/research/core-audit.md)",
                {"core": short_name, "verdict": verdict_entry.verdict, "options": options},
            )
        )
    return tuple(caveats)


@dataclass(frozen=True, slots=True)
class _CardChoice:
    """The rule card that applies here, once feature detection has had its say.

    ``live_option`` is the governing option as the core *registers* it — the
    observation that confirms the card's generation.
    """

    card: CoreCard | None = None
    live_option: CoreOption | None = None
    sources: tuple[str, ...] = ()
    caveats: tuple[Caveat, ...] = ()


def _select_card(
    *,
    so_basename: str | None,
    library_name: str | None,
    registered_options: Mapping[str, CoreOption] | None,
) -> _CardChoice:
    """Which rule card applies to this core — decided on evidence, not on a version.

    Feature detection is the generation question made observable (the LRPS2
    lesson): when the probe captured which options this core REGISTERS, the
    card's governing option key decides applicability. Key registered → the
    card generation is confirmed by evidence. Key not registered → the card
    describes a different generation and is NOT applied; stale knowledge with a
    warning would still be a guess. Options not captured (probe limitation,
    core registers later) → unknown, and the version comparison keeps doing its
    job.
    """
    card = lookup_card(so_basename=so_basename, library_name=library_name)
    live_option = None
    sources: tuple[str, ...] = ()
    caveats: list[Caveat] = []
    if card is not None and card.option_key is not None and registered_options is not None:
        live_option = registered_options.get(card.option_key)
        if live_option is None:
            caveats.append(
                Caveat(
                    CAVEAT_CARD_GENERATION_MISMATCH,
                    f"rule card '{card.key}' is governed by option {card.option_key!r}, which this core "
                    "does not register — the card describes a different core generation and is not "
                    "applied; this core's actual save behaviour is unknown until re-audited, so the "
                    "standard answer below may miss the real save stack",
                    {"card": card.key, "option_key": card.option_key},
                )
            )
            card = None
        else:
            sources = (
                f"feature-detected: core registers {card.option_key!r} (default "
                f"{live_option.default!r}, values {list(live_option.values)}) — card generation "
                "confirmed by observation, not by version comparison",
            )
    if card is None and so_basename is not None:
        caveats.extend(_unaudited_caveats(so_basename))
    return _CardChoice(card=card, live_option=live_option, sources=sources, caveats=tuple(caveats))


def _version_drift(
    verified: VerifiedOn, *, arrangement_version: str | None, core_version: str | None
) -> tuple[dict[str, str], list[str]]:
    """Which pinned versions differ here, and which this machine does not expose."""
    drift: dict[str, str] = {}
    missing: list[str] = []
    if verified.version is not None:
        if arrangement_version is None:
            missing.append("arrangement_version")
        elif verified.version != arrangement_version:
            drift["arrangement_verified"] = verified.version
            drift["arrangement_live"] = arrangement_version
    if verified.core_library_version is not None:
        if core_version is None:
            missing.append("core_library_version")
        elif verified.core_library_version != core_version:
            drift["core_verified"] = verified.core_library_version
            drift["core_live"] = core_version
    return drift, missing


def _verification_notes(
    card: CoreCard,
    *,
    arrangement: str,
    arrangement_version: str | None,
    core_version: str | None,
    feature_confirmed: bool,
) -> tuple[tuple[str, ...], tuple[Caveat, ...]]:
    """Was this card verified on this arrangement, and does the record still hold?

    Explicit and failing closed (REVIEW M3): the states are verified, drifted,
    runtime-version-unknown, never-verified — missing live evidence is never
    treated as successful verification.
    """
    audit = lookup_audit(card.key)
    verified = audit.verified.get(arrangement) if audit is not None else None
    if verified is None:
        return (), (
            Caveat(
                CAVEAT_UNVERIFIED_VERSION,
                f"rule card '{card.key}' was never verified on a {arrangement} arrangement — "
                "the behaviour it describes may not hold here",
                {"card": card.key, "arrangement": arrangement, "verification": "never-verified"},
            ),
        )
    drift, missing = _version_drift(
        verified, arrangement_version=arrangement_version, core_version=core_version
    )
    if (drift or missing) and feature_confirmed:
        # Feature detection outranks the version comparison: the governing
        # option is observably registered, so a differing or unreadable version
        # record is supplementary info, not an alarm (the false-alarm class the
        # version check produced).
        detail = str(drift) if drift else f"{', '.join(missing)} unavailable"
        return (
            f"rule card '{card.key}': version records differ from this machine ({detail}), but "
            f"the governing option is feature-confirmed — the decision falls on observed evidence",
        ), ()
    if drift:
        data = {"card": card.key, "arrangement": arrangement, "verification": "drifted", **drift}
        if missing:
            data["missing"] = ", ".join(missing)
        return (), (
            Caveat(
                CAVEAT_UNVERIFIED_VERSION,
                f"rule card '{card.key}' was verified against different versions than this "
                f"machine runs ({drift}) — behaviour may have drifted",
                data,
            ),
        )
    if missing:
        return (), (
            Caveat(
                CAVEAT_UNVERIFIED_VERSION,
                f"rule card '{card.key}' is pinned to {arrangement} versions this machine does "
                f"not expose ({', '.join(missing)} unavailable) — the verification cannot be "
                "confirmed live",
                {
                    "card": card.key,
                    "arrangement": arrangement,
                    "verification": "runtime-version-unknown",
                    "missing": ", ".join(missing),
                },
            ),
        )
    return (
        f"rule card '{card.key}': verified on {arrangement} "
        f"{verified.version or '?'} (core {verified.core_library_version or '?'}, "
        f"{verified.date or 'undated'})",
    ), ()


def _mode_for_unknown_value(
    card: CoreCard, *, opt_value: str, effective_default: str | None, live_option: CoreOption | None
) -> tuple[CoreCard | None, SaveMode | None, str, Caveat]:
    """What applies when the configured option value has no mode on the card.

    Either the live core legitimately offers a value the card does not know, or
    even the effective default has no card mode — value-level generation drift,
    and applying any other mode would guess, so the card steps aside. Otherwise
    RetroArch's option manager keeps the core-declared default when a persisted
    value is invalid; it does not fall back to the standard rule (REVIEW M1).
    """
    live_registered_value = live_option is not None and opt_value in live_option.values
    fallback_mode = card.modes.get(effective_default or "")
    if live_registered_value or fallback_mode is None:
        return (
            None,
            None,
            opt_value,
            Caveat(
                CAVEAT_CARD_GENERATION_MISMATCH,
                f'core option {card.option_key} = "{opt_value}" cannot be interpreted by rule '
                f"card '{card.key}' — the card lags this core's generation; the configured save "
                "behaviour is unknown until re-audited, and the standard answer below may miss "
                "the real save stack",
                {"card": card.key, "option_key": card.option_key or "", "value": opt_value},
            ),
        )
    return (
        card,
        fallback_mode,
        effective_default or opt_value,
        Caveat(
            CAVEAT_UNKNOWN_OPTION_VALUE,
            f'core option {card.option_key} = "{opt_value}" is not a value the rule card '
            f"knows — applying the core default mode {effective_default!r} as RetroArch would",
            {"card": card.key, "option_key": card.option_key or "", "value": opt_value},
        ),
    )


@dataclass(frozen=True, slots=True)
class _CardApplication:
    """The card as it applies here: which mode governs, and what that makes granular.

    ``card`` comes back ``None`` when the card stepped aside — the answer then
    falls through to the standard rule, with the mismatch stated.
    """

    card: CoreCard | None = None
    mode: SaveMode | None = None
    granularity: Granularity | None = None
    caveats: tuple[Caveat, ...] = ()


def _apply_card(
    machine: Machine,
    *,
    sandbox: _Sandbox,
    retroarch_config_dir: str,
    card: CoreCard,
    live_option: CoreOption | None,
    library_name: str | None,
    layers: Sequence[_CfgLayer],
    content: _Content,
    gates: _OverrideGates,
) -> _CardApplication:
    """Read the option that governs this card, live, and take the mode it selects.

    A card without a governing option states one fixed behaviour. Otherwise the
    registered default is a live read and outranks the card's shipped-generation
    copy — feature detection makes option defaults machine facts instead of
    world knowledge.

    The options files are resolved here rather than per query because this is
    where they are read: a card that governs nothing consults none of them, and
    an unreachable ``core_options_path`` is only a degradation for an answer
    that would have looked there.
    """
    if card.option_key is None:
        # The load refuses any other shape (_expect_selectable_modes), so the
        # mode is there — a card that governs nothing states exactly this one.
        mode = card.modes[MODE_ALWAYS]
        return _CardApplication(
            card=card,
            mode=mode,
            granularity=Granularity(
                value=mode.granularity,
                option_key=None,
                option_value=None,
                option_source=f"rule card '{card.key}': fixed behaviour (no governing option)",
                options_file=None,
                alternatives=(),
            ),
        )

    effective_default = card.option_default
    if live_option is not None and live_option.default is not None:
        effective_default = live_option.default
    global_file, options_file_caveats = _global_options_file(
        layers, sandbox=sandbox, retroarch_config_dir=retroarch_config_dir
    )
    # Both default false/true from config.def.h and both are read from the
    # MERGED config, after config_load_override has run: the core's own
    # retro_set_environment (runloop.c:5037) triggers runloop_init_core_options,
    # which reads settings->bools.game_specific_options and .global_core_options
    # (runloop.c:1529-1530, :1564-1565) — one step after the overrides were
    # merged at :5003. So an override that says game_specific_options = "false"
    # really does switch the game/folder .opt layer off, unlike
    # auto_overrides_enable, which is captured before the merge (:4941).
    global_core_options, global_ignored = chain_bool(layers, "global_core_options", default=False)
    game_specific_options, game_ignored = chain_bool(layers, "game_specific_options", default=True)
    opt_value, opt_source, options_file, opt_unconfirmed = _core_options_value(
        machine,
        override_config_dir=gates.override_config_dir,
        global_file=global_file,
        library_name=library_name,
        content_dir_name=content.dir_name,
        rom_stem=content.rom_stem,
        option_key=card.option_key or "",
        option_default=effective_default or "",
        game_specific_options=game_specific_options,
        per_core_options=not global_core_options,
    )
    caveats: list[Caveat] = [
        *options_file_caveats,
        *_ignored_caveats((*global_ignored, *game_ignored)),
    ]
    applied: CoreCard | None = card
    mode = card.modes.get(opt_value)
    if mode is None:
        applied, mode, opt_value, caveat = _mode_for_unknown_value(
            card, opt_value=opt_value, effective_default=effective_default, live_option=live_option
        )
        caveats.append(caveat)
    if applied is not None and opt_unconfirmed and mode is not None:
        caveats.append(
            Caveat(
                CAVEAT_CARD_MODE_UNCONFIRMED,
                f"a game/folder/per-core options file keyed by library_name could govern "
                f"{card.option_key} but cannot be checked (core unqueryable) — the applied mode "
                f"{opt_value!r} may not be the effective one",
                {"card": card.key, "option_key": card.option_key or "", "applied": opt_value},
            )
        )
    granularity = None
    if applied is not None and mode is not None:
        granularity = Granularity(
            value=mode.granularity,
            option_key=card.option_key,
            option_value=opt_value,
            option_source=opt_source,
            options_file=options_file,
            alternatives=tuple(
                (value, other.granularity) for value, other in card.modes.items() if value != opt_value
            ),
        )
    return _CardApplication(card=applied, mode=mode, granularity=granularity, caveats=tuple(caveats))


def _card_file_set(
    machine: Machine,
    *,
    card: CoreCard,
    mode: SaveMode,
    directory: str,
    rom_stem: str | None,
    observable: bool,
) -> FileSet:
    """What the card says lies in its own directory — declared, or observed there.

    A template file that cannot be filled leaves the set honestly unknown; with
    a hole in the names there is nothing to look for, and where the directory
    itself is a hole or a path this host cannot reach there is nowhere to look
    (*observable* false), so the declaration stands unobserved.
    """
    declared = _card_files(mode.files, rom_stem) if mode.files is not None else None
    if declared is None:
        return UNKNOWN_FILE_SET
    if not observable or file_set_holes(declared):
        return FileSet("declared", declared, f"declared by rule card '{card.key}'", complete=mode.complete)
    # Observation candidates may be wider than the declared defaults —
    # e.g. Flycast's slot-2 VMUs exist only when configured (REVIEW M2).
    observe = _card_files(mode.observe, rom_stem) if mode.observe is not None else None
    candidates = observe if observe is not None else declared
    present = tuple(f for f in candidates if machine.path_kind(os.path.join(directory, f)) == KIND_FILE)
    if present:
        return FileSet("observed", present, f"observed on the machine: {directory}", complete=mode.complete)
    return FileSet(
        "declared",
        declared,
        f"declared by rule card '{card.key}' (none present yet)",
        complete=mode.complete,
    )


def _file_set_caveats(
    card: CoreCard, mode: SaveMode, *, mode_value: str, rom_stem: str | None, also_under: str | None
) -> tuple[Caveat, ...]:
    """What one declared file list cannot say about this mode's save.

    Three states the card keeps apart, each stated rather than left to an
    empty-looking answer: a mode whose save reaches beyond its own root (a
    list would offer a part as the whole), a mode whose names depend on a fact
    atlas does not read (both spellings are handed over, the caller picks),
    and a mode whose names are not established yet.

    *also_under* is the mode's second root **as this machine resolves it**, not
    the card's own spelling of it: the card names the root its core asks for,
    and where that is is the resolver's question — the same one
    :func:`_core_system_root` answers for the root the placement itself uses
    (:func:`_also_under_root`).
    """
    if also_under is not None:
        return (
            Caveat(
                CAVEAT_FILE_SET_SPANS_ROOTS,
                f"rule card '{card.key}': in mode {mode_value!r} only part of the save moves here — "
                f"the rest keeps using the {also_under} root. A card states one root per mode, so the "
                "file set is left unstated instead of presenting the visible part as the whole save",
                {"card": card.key, "mode": mode_value, "also_under": also_under},
            ),
        )
    if mode.files is None:
        return (
            Caveat(
                CAVEAT_FILENAMES_UNVERIFIED,
                f"rule card '{card.key}': mode {mode_value!r} places per-game files under the "
                "standard directory, but the filename scheme is unverified — file names not stated",
                {"card": card.key, "mode": mode_value},
            ),
        )
    if mode.files_without_save_id is not None or mode.files_established_for is not None:
        stated = _card_files(mode.files, rom_stem) or mode.files
        data = {"card": card.key, "mode": mode_value, "files": ", ".join(stated)}
        spelling = ""
        scope = ""
        if mode.files_without_save_id is not None:
            alternative = _card_files(mode.files_without_save_id, rom_stem) or mode.files_without_save_id
            data["files_without_save_id"] = ", ".join(alternative)
            spelling = (
                " The names hold for content that carries a platform-native id; content without one "
                "is named after the ROM instead, and that spelling is in this caveat's data — "
                "whoever fills 'save_id' knows which applies."
            )
        if mode.files_established_for is not None:
            data["files_established_for"] = mode.files_established_for
            scope = (
                f" Which files exist at all was established for {mode.files_established_for} content "
                "only: another content class connects a different set of devices, so a name stated "
                "here may never appear for it, and one that does may be missing."
            )
        if mode.files_citation is not None:
            data["citation"] = mode.files_citation
        return (
            Caveat(
                CAVEAT_FILENAMES_CONTENT_CONDITIONAL,
                f"rule card '{card.key}': in mode {mode_value!r} the file set depends on the content, "
                f"which atlas does not identify.{spelling}{scope}",
                data,
            ),
        )
    return ()


@dataclass(frozen=True, slots=True)
class _SystemRoot:
    """The directory RetroArch hands a core that asks for the system directory.

    ``base`` is that directory, or the ``<content_dir>`` template when the
    content's own directory is the root and the caller named no content.
    ``root_kind`` says which of the two anchors it is: the key's value is not
    always where the core is sent, so a card rooted in the system directory can
    legitimately answer ``content_directory``. ``reachable`` is false for a
    configured spelling with no host location — the emulator writes there, but
    nothing below it can be looked at from here.
    """

    base: str
    root_kind: RootKind
    needs: tuple[str, ...] = ()
    reachable: bool = True
    sources: tuple[str, ...] = ()
    caveats: tuple[Caveat, ...] = ()


def _content_system_root(content: _Content, *, source: str) -> _SystemRoot:
    """The content's own directory as the system root — resolved, or left as a hole.

    With no content loaded at all, upstream hands back the standing
    ``system_directory`` (``runloop.c:1986-1987``) — empty where an emptied key
    led into this branch, the configured directory where the flag alone did.
    Neither is an answer about a save, because a save presupposes content, so a
    caller who names none is answered with the template instead, exactly as
    ``savefiles_in_content_dir`` is answered on the standard route — and
    ``content_dir`` is a hole the caller can actually fill.
    """
    if content.system_dir is not None:
        return _SystemRoot(content.system_dir, ROOT_CONTENT_DIRECTORY, sources=(source,))
    return _SystemRoot(
        "<content_dir>", ROOT_CONTENT_DIRECTORY, needs=(HOLE_CONTENT_DIR,), sources=(source,)
    )


# What an *absent* ``system_directory`` resolves to, for every route that asks.
# ``config_set_defaults`` seeds it before any config is read
# (``configuration.c:5746-5749``), and on desktop Linux the seed is ``system``
# under the RetroArch config tree (``platform_unix.c:2137-2143``). So the key
# being absent is not a gap: it names a directory, and both the card route and
# the firmware route resolve it to the same one — from here, once.
PLATFORM_SYSTEM_DIR_SOURCE = (
    "default: system_directory unset — RetroArch platform default applies "
    "('system' under the config tree, platform_unix.c:2142-2143)"
)


def _platform_system_dir(retroarch_config_dir: str) -> str:
    """RetroArch's own default system directory for this config tree."""
    return os.path.join(retroarch_config_dir, "system")


def _core_system_root(
    *,
    sandbox: _Sandbox,
    cfg_label: str,
    layers: Sequence[_CfgLayer],
    content: _Content,
    retroarch_config_dir: str,
) -> _SystemRoot:
    """Resolve the system directory the way the core receives it (``runloop.c:1958-1999``).

    ``RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY`` is not a read of
    ``system_directory``: the core is handed the **content's** directory
    whenever ``systemfiles_in_content_dir`` is set, and whenever no value is
    left standing at all (``runloop.c:1963-1964``). Only otherwise does it get
    the configured directory (``:1992-1997``).

    What "left standing" means is decided before the environment call, and the
    two spellings that clear it are not the same as the key being absent:

    - **Absent** — ``config_set_defaults`` has already put the platform default
      there (``configuration.c:5746-5749``), which on desktop Linux is
      ``system`` under the config tree (``platform_unix.c:2137-2143``, the same
      block that seeds the saves default this resolver answers with). So an
      unset key resolves; it is not a hole and never was one.
    - **Blank or the literal ``default``** — ``system_directory`` passes
      ``handle_setting = true`` (``configuration.c:1691``), so the generic path
      loop writes whatever the merged config holds, with no directory test
      (``:6532-6538``); ``config_get_path`` copies an empty value through
      (``config_file.c:1202-1216``) and ``default`` is cleared outright
      (``:6834-6835``). Either way the setting is empty and the content
      directory wins — the opposite of ``savefile_directory``, where a blank
      value keeps the standing root.

    A dropped line is stated here for the first time: while an unset key
    surfaced as a hole, the fact spoke for itself; now it resolves silently to
    the platform default, so the line RetroArch refused has to be said out loud
    (the widened ``cfg-line-dropped`` scope of REVIEW M2/M4).
    """
    in_content_dir, flag_ignored = chain_bool(layers, "systemfiles_in_content_dir", default=False)
    raw_system, dropped = chain_value(layers, "system_directory")
    caveats = list(_ignored_caveats((*flag_ignored, *dropped)))
    configured = sandbox.cfg_path("system_directory", raw_system) if raw_system is not None else None

    if in_content_dir:
        root = _content_system_root(
            content,
            source=f'{cfg_label} chain: systemfiles_in_content_dir = "true" — the core is handed the '
            "content's own directory as its system directory (runloop.c:1963-1985)",
        )
    elif raw_system is None:
        root = _SystemRoot(
            _platform_system_dir(retroarch_config_dir),
            ROOT_SYSTEM_DIRECTORY,
            sources=(PLATFORM_SYSTEM_DIR_SOURCE,),
        )
    elif configured is None:
        root = _content_system_root(
            content,
            source=f'{cfg_label} chain: system_directory = "{raw_system}" leaves the setting empty — '
            "the core is handed the content's own directory instead (runloop.c:1963-1985)",
        )
    elif configured.path is None:
        # The value as configured: it is where the emulator writes, in the only
        # namespace that names it, and the caveat states that atlas cannot
        # follow it there — the same answer _host_save_dir gives for a saves
        # root it cannot reach.
        root = _SystemRoot(
            raw_system,
            ROOT_SYSTEM_DIRECTORY,
            reachable=False,
            sources=(f'{cfg_label} chain: system_directory = "{raw_system}"',),
            caveats=configured.caveats,
        )
    else:
        root = _SystemRoot(
            configured.path,
            ROOT_SYSTEM_DIRECTORY,
            sources=(f'{cfg_label} chain: system_directory = "{raw_system}"{configured.note}',),
        )
    return _dc_replace(root, caveats=(*caveats, *root.caveats))


def _also_under_root(
    mode: SaveMode | None,
    *,
    sandbox: _Sandbox,
    cfg_label: str,
    layers: Sequence[_CfgLayer],
    content: _Content,
    retroarch_config_dir: str,
) -> str | None:
    """A spanning mode's second root, resolved the way its own root is resolved.

    ``also_under`` is a root *kind*, and ``system_directory`` is the one kind
    that does not say where it is: the core is handed the content's directory
    instead wherever ``systemfiles_in_content_dir`` — or an emptied key — sends
    it (:func:`_core_system_root`). A consumer following ``also_under`` back to
    the cfg key would look in a directory the core never touches, and the
    answer's own ``root_kind`` stopped reading it that way, so this reads it the
    same. Only that kind needs resolving; the others name themselves.
    """
    if mode is None or mode.also_under is None:
        return None
    if mode.also_under != ROOT_SYSTEM_DIRECTORY:
        return mode.also_under
    return _core_system_root(
        sandbox=sandbox,
        cfg_label=cfg_label,
        layers=layers,
        content=content,
        retroarch_config_dir=retroarch_config_dir,
    ).root_kind


def _system_directory_placement(
    machine: Machine,
    *,
    sandbox: _Sandbox,
    cfg_label: str,
    card: CoreCard,
    mode: SaveMode,
    granularity: Granularity | None,
    layers: Sequence[_CfgLayer],
    content: _Content,
    retroarch_config_dir: str,
    sources: tuple[str, ...],
    caveats: tuple[Caveat, ...],
) -> SavePlacement:
    """The placement for a card whose core roots its saves in the system directory.

    The card states which directory the core *asks* for; which directory that
    is on this machine is RetroArch's answer, not the cfg key's value
    (:func:`_core_system_root`) — so this route's ``root_kind`` follows the root
    the core is actually handed.
    """
    root = _core_system_root(
        sandbox=sandbox,
        cfg_label=cfg_label,
        layers=layers,
        content=content,
        retroarch_config_dir=retroarch_config_dir,
    )
    directory = os.path.join(root.base, mode.subdir) if mode.subdir else root.base
    card_sources = [
        *sources,
        *root.sources,
        f"rule card '{card.key}': core keeps saves under the directory RetroArch hands it as the "
        f"system directory — {card.provenance}",
    ]
    all_caveats = [*caveats, *root.caveats]
    if granularity is not None:
        # A mode routed here is rooted in the system directory itself, and a
        # second root naming that same one is refused when the card is loaded —
        # so nothing here needs the resolution _also_under_root performs.
        all_caveats.extend(
            _file_set_caveats(
                card,
                mode,
                mode_value=granularity.option_value or "",
                rom_stem=content.rom_stem,
                also_under=mode.also_under,
            )
        )
    observable = root.reachable and not root.needs
    file_set = _card_file_set(
        machine, card=card, mode=mode, directory=directory, rom_stem=content.rom_stem, observable=observable
    )
    physical_dir = None
    if observable:
        physical_dir, link_caveats = _link_view(machine, directory)
        all_caveats.extend(link_caveats)
    return SavePlacement(
        dir=directory,
        root_kind=root.root_kind,
        needs=needs_with_file_set(root.needs, file_set.files),
        file_set=file_set,
        sources=tuple(card_sources),
        caveats=tuple(all_caveats),
        granularity=granularity,
        physical_dir=physical_dir,
    )


def _sorted_dir_fallback(
    machine: Machine, *, intended_dir: str, effective_root: str | None
) -> tuple[str, str | None, tuple[Caveat, ...]]:
    """Resolve a sorted directory that may not exist — a CONDITIONAL result.

    RetroArch creates it on first save and silently reverts to the unsorted
    root when creation fails (runloop.c:8844). A file in the way makes the
    failure certain — then the fallback IS the answer; anything else keeps the
    intended dir with a structural fallback (REVIEW H5).

    Returns ``(dir, fallback_dir, caveats)``.
    """
    if effective_root is None or intended_dir == effective_root:
        return intended_dir, None, ()
    dir_kind = machine.path_kind(intended_dir)
    if dir_kind == KIND_FILE:
        return (
            effective_root,
            None,
            (
                Caveat(
                    CAVEAT_SORTED_DIR_UNCREATABLE,
                    f"sorted directory {intended_dir} is blocked by an existing file — RetroArch "
                    f"cannot create it and reverts to {effective_root} (runloop.c:8844)",
                    {"intended": intended_dir, "effective": effective_root},
                ),
            ),
        )
    if dir_kind != KIND_DIRECTORY:
        return (
            intended_dir,
            effective_root,
            (
                Caveat(
                    CAVEAT_SORTED_DIR_MISSING,
                    f"sorted directory {intended_dir} does not exist yet — RetroArch creates it on first save, "
                    f"and silently reverts to {effective_root} if creation fails (runloop.c:8844)",
                    {"dir": intended_dir, "fallback_dir": effective_root},
                ),
            ),
        )
    return intended_dir, None, ()


def _observed_file_set(
    machine: Machine,
    *,
    directory: str,
    rom_stem: str,
    content_path: str | None,
    card: CoreCard | None,
    mode: SaveMode | None,
) -> tuple[FileSet, tuple[Caveat, ...]]:
    """The save files actually lying in *directory* for this ROM, or what is declared.

    Literal observation: ROM names routinely carry glob metacharacters
    (``[``, ``]``) — escaped so ``[`` matches ``[`` (REVIEW M2). RetroArch's own
    bookkeeping next to saves is filtered with a source citation: the
    disk-control index ``<stem>.ldci`` (disk_index_file.c:201-249,
    file_path_special.h:83) is not save data. The content file itself is
    filtered by the name it has on disk — for content inside an archive that is
    the archive, which shares the stem whenever it is named after its entry.

    A directory that could not be listed is the case this exists to keep out of
    the answer: the save data may be sitting right there, on a card that
    stopped answering mid-session. The set is *unknown* then, with a caveat
    naming the directory — never "no files present at …", which is the same
    sentence a genuinely empty directory earns and would send a caller off to
    restore a save it already has.
    """
    content_file = content_file_name(content_path) if content_path else None
    pattern = os.path.join(_glob_escape(directory), _glob_escape(rom_stem) + ".*")
    companions = {f"{rom_stem}.ldci"}
    listing = machine.glob(pattern)
    matches = [
        m
        for m in listing.matches
        # In content-dir mode the ROM shares the save's directory and
        # stem — the content file itself is never part of the save set.
        if os.path.basename(m) != content_file and os.path.basename(m) not in companions
    ]
    caveats = tuple(_unlistable_caveat(path) for path in listing.unreadable)
    if listing.unreadable:
        # Not "unless something matched": this pattern names one directory, so
        # a failed listing means zero matches anyway — and if that ever stopped
        # holding, the matches would be a partly-read directory presented as an
        # observed set, which a `complete` rule card could then close over.
        return UNKNOWN_FILE_SET, caveats
    return _file_set_of(matches, directory=directory, rom_stem=rom_stem, card=card, mode=mode), caveats


def _unlistable_caveat(path: str) -> Caveat:
    return Caveat(
        CAVEAT_SAVE_DIR_UNLISTABLE,
        f"{path} could not be listed (permissions or an I/O failure), so whether this content has "
        "saves there is unknown — an empty file set would be a claim about a directory atlas never read",
        {"path": path},
    )


def _file_set_of(
    matches: list[str],
    *,
    directory: str,
    rom_stem: str,
    card: CoreCard | None,
    mode: SaveMode | None,
) -> FileSet:
    """What was found, else what the card declares, else nothing stated."""
    declared = None
    if card is not None and mode is not None and mode.files is not None:
        declared = _card_files(mode.files, rom_stem)
    if matches:
        observed = tuple(sorted(os.path.basename(m) for m in matches))
        complete = (
            mode is not None and mode.complete and declared is not None and set(observed) <= set(declared)
        )
        return FileSet(
            state="observed",
            files=observed,
            source=f"observed on the machine: {directory}",
            complete=complete,
        )
    if declared is not None and card is not None:
        return FileSet(
            state="declared",
            files=declared,
            source=f"declared by rule card '{card.key}' (none present yet)",
            complete=mode.complete if mode is not None else False,
        )
    return FileSet(
        state="unknown",
        files=(),
        source=f"no files present at {directory} — file set not stated (never guessed)",
    )


def _nest_card_subdir(
    directory: str, fallback_dir: str | None, *, card: CoreCard | None, mode: SaveMode | None
) -> tuple[str, str | None, tuple[str, ...]]:
    """Nest a card core's own subtree under the effective save directory.

    GET_SAVE_DIRECTORY hands the core the redirected (sorted,
    fallback-resolved) dir (runloop.c:2001, set at runloop.c:8977), and the
    core appends its subdir to whatever it received — so the subdir follows the
    fallback too. A mode that nests nothing leaves both paths alone.
    """
    if mode is None or not mode.subdir or mode.root == ROOT_SYSTEM_DIRECTORY:
        return directory, fallback_dir, ()
    sources: tuple[str, ...] = ()
    if card is not None:
        sources = (
            f"rule card '{card.key}': core nests its saves under '{mode.subdir}/' in the save directory",
        )
    nested_fallback = os.path.join(fallback_dir, mode.subdir) if fallback_dir is not None else None
    return os.path.join(directory, mode.subdir), nested_fallback, sources


def _content_dir_caveat(directory: str) -> Caveat:
    """What an observation in the content's own directory can and cannot say.

    The observation is a glob for the name RetroArch saves under
    (runloop.c:8720), and in the ROM's own directory that name belongs to the
    *content* first: the remaining tracks of a ``.cue``, the box art and manual
    a frontend stores beside it, the archive the ROM was extracted from. Telling
    those from save data would take a source that says which extensions are
    content — atlas has none (rule cards state files per core, not per content
    format), and an invented list of ROM extensions is exactly the guess this
    resolver refuses. So the observation is stated for what it is: everything
    lying there under the content's name, never a completeness claim
    (REVIEW M10).
    """
    return Caveat(
        CAVEAT_CONTENT_DIR_OBSERVATION,
        f"the files observed lie in the content's own directory ({directory}) and are matched by the "
        "content's name — files belonging to the content itself (further disc tracks, cover art, "
        "manuals) cannot be told from save data here, so this set may be wider than the save",
        {"dir": directory},
    )


def _is_content_dir(directory: str, content: _Content) -> bool:
    """Is the directory just observed the content's own — the one atlas does not own?

    Normalized on both sides: the same directory can reach the placement as the
    content's own path and as a configured ``savefile_directory`` spelled with a
    trailing slash, and it is the same directory either way.
    """
    if content.dir_path is None:
        return False
    return os.path.normpath(directory) == os.path.normpath(content.dir_path)


def _observed_at(
    machine: Machine,
    *,
    directory: str,
    content: _Content,
    content_path: str | None,
    card: CoreCard | None,
    mode: SaveMode | None,
) -> tuple[FileSet, str | None, tuple[Caveat, ...]]:
    """What lies at the resolved directory: the file set and the link view."""
    file_set = UNKNOWN_FILE_SET
    caveats: tuple[Caveat, ...] = ()
    if content.rom_stem is not None:
        file_set, caveats = _observed_file_set(
            machine,
            directory=directory,
            rom_stem=content.rom_stem,
            content_path=content_path,
            card=card,
            mode=mode,
        )
        if file_set.state == "observed" and _is_content_dir(directory, content):
            caveats = (*caveats, _content_dir_caveat(directory))
    physical_dir, link_caveats = _link_view(machine, directory)
    return file_set, physical_dir, (*caveats, *link_caveats)


def _standard_placement(
    machine: Machine,
    query: _SaveQuery,
    *,
    layout: RetroArchCfg,
    platform_default_dir: str,
    content: _Content,
    library_name: str | None,
    card: CoreCard | None,
    mode: SaveMode | None,
    granularity: Granularity | None,
    also_under: str | None,
    reachable: bool,
    sources: tuple[str, ...],
    caveats: tuple[Caveat, ...],
) -> SavePlacement:
    """The standard rule: RetroArch's own path math, then what lies at the result.

    Whatever the configs resolved to is only the intended directory — the
    filesystem still decides, so a sorted directory that is not there yet, a
    card core's nested subtree and the files actually present are all resolved
    against the machine before the answer is stated.

    Unless the root cannot be reached from here (*reachable* false, a sandbox
    path with no host location): the path math still applies and ``dir`` still
    names where the emulator writes, but every observation of the result —
    whether the sorted directory exists, what it links to, which files lie in
    it — would come from a read that never applied, so none is performed and
    ``fallback_dir`` stays empty rather than claiming a fallback nobody takes.
    """
    all_caveats = list(caveats)
    if card is not None and mode is not None and granularity is not None:
        all_caveats.extend(
            _file_set_caveats(
                card,
                mode,
                mode_value=granularity.option_value or "",
                rom_stem=content.rom_stem,
                also_under=also_under,
            )
        )

    file_set = UNKNOWN_FILE_SET
    placement = build_save_placement(
        layout=layout,
        platform_default_dir=platform_default_dir,
        content_dir_path=content.dir_path,
        content_dir_name=content.dir_name,
        library_name=library_name,
        extra_sources=sources,
    )

    final_dir = placement.dir
    fallback_dir: str | None = None
    physical_dir: str | None = None
    final_sources = list(placement.sources)
    # Which world knowledge produced this answer — said once per answer, on
    # this route as much as on the system_directory one. It carries what the
    # placement's own fields cannot: that a mode *moves* the save rather than
    # adding to it, so the files the previous mode wrote are left behind stale.
    if card is not None and mode is not None:
        final_sources.append(f"rule card '{card.key}' governs this placement — {card.provenance}")
    if not placement.needs:
        if reachable:
            if placement.root_kind == ROOT_CONTENT_DIRECTORY:
                effective_root = content.dir_path
            else:
                effective_root = layout.savefile_directory or platform_default_dir
            final_dir, fallback_dir, sorted_dir_caveats = _sorted_dir_fallback(
                machine, intended_dir=final_dir, effective_root=effective_root
            )
            all_caveats.extend(sorted_dir_caveats)
        final_dir, fallback_dir, subdir_sources = _nest_card_subdir(
            final_dir, fallback_dir, card=card, mode=mode
        )
        final_sources.extend(subdir_sources)
        if reachable:
            file_set, physical_dir, link_caveats = _observed_at(
                machine,
                directory=final_dir,
                content=content,
                content_path=query.content_path,
                card=card,
                mode=mode,
            )
            all_caveats.extend(link_caveats)

    return SavePlacement(
        dir=final_dir,
        root_kind=placement.root_kind,
        needs=needs_with_file_set(placement.needs, file_set.files),
        file_set=file_set,
        sources=tuple(final_sources),
        caveats=tuple(all_caveats),
        granularity=granularity,
        fallback_dir=fallback_dir,
        physical_dir=physical_dir,
    )


def _retroarch_save_location(machine: Machine, query: _SaveQuery) -> SavePlacement:
    """The shared resolver: global cfg → override chain → placement, all live.

    Reads the same four layers RetroArch reads (``configuration.c:7095``),
    resolves ``library_name`` from the core binary when a core is named, and
    observes the file set for existing saves. Every degradation is a stated
    caveat, never a silent guess. The query carries the global cfg's content,
    read exactly once by the caller — one query derives every decision from
    one snapshot of each source (REVIEW M4).
    """
    content = _content_coordinates(query.content_path)
    core = _identify_core(machine, core_so=query.core_so, core_path_resolver=query.core_path_resolver)
    caveats = [*query.extra_caveats, *core.caveats]
    if query.content_path is not None and content.rom_stem is None:
        caveats.append(_unnamed_content_caveat(query.content_path))
    sources_extra = [*query.extra_sources, *core.sources]

    retroarch_config_dir = os.path.dirname(query.global_cfg_path)
    gates = _override_gates(
        query.global_text,
        sandbox=query.sandbox,
        cfg_label=query.cfg_label,
        override_config_dir=query.override_config_dir,
        config_file_dir=retroarch_config_dir,
    )
    sources_extra.extend(gates.sources)
    caveats.extend(gates.caveats)
    overrides, override_sources = _override_layers(
        machine, gates=gates, library_name=core.library_name, content=content
    )
    sources_extra.extend(override_sources)

    layout = resolve_save_layout(
        query.global_text,
        home=query.sandbox.home,
        cfg_label=query.cfg_label,
        defaults=query.defaults,
        overrides=overrides,
        is_directory=_save_dir_probe(machine, query.sandbox),
    )
    caveats.extend(_ignored_caveats(layout.ignored))
    layers: list[_CfgLayer] = [
        (label, text)
        for label, text in ((query.cfg_label, query.global_text), *overrides)
        if text is not None
    ]
    saves_root = _host_save_dir(query.sandbox, layout)
    layout = saves_root.layout
    sources_extra.extend(saves_root.sources)
    caveats.extend(saves_root.caveats)

    # The RetroArch platform default saves dir — 'saves' under the config tree
    # (platform_unix.c:2133-2134) — is the effective root whenever no layer left
    # a usable value: the key unset everywhere, reset to "default", or every
    # value RetroArch read refused by its own directory test.
    platform_default_dir = os.path.join(retroarch_config_dir, "saves")
    caveats.extend(
        _rejected_dir_caveats(
            machine,
            query.sandbox,
            layout.rejected_directories,
            effective=layout.savefile_directory or platform_default_dir,
        )
    )

    # Rule cards: cores whose save behaviour deviates from the standard rule.
    # The card names the governing option; its current value is read live.
    so_basename = os.path.basename(query.core_so) if query.core_so is not None else None
    choice = _select_card(
        so_basename=so_basename,
        library_name=core.library_name,
        registered_options=core.info.options if core.info is not None else None,
    )
    sources_extra.extend(choice.sources)
    caveats.extend(choice.caveats)

    card = choice.card
    card_mode: SaveMode | None = None
    granularity: Granularity | None = None
    if card is not None:
        notes, verification_caveats = _verification_notes(
            card,
            arrangement=query.arrangement,
            arrangement_version=query.arrangement_version,
            core_version=core.info.library_version if core.info is not None else None,
            feature_confirmed=choice.live_option is not None,
        )
        sources_extra.extend(notes)
        caveats.extend(verification_caveats)
        applied = _apply_card(
            machine,
            sandbox=query.sandbox,
            retroarch_config_dir=retroarch_config_dir,
            card=card,
            live_option=choice.live_option,
            library_name=core.library_name,
            layers=layers,
            content=content,
            gates=gates,
        )
        card, card_mode, granularity = applied.card, applied.mode, applied.granularity
        caveats.extend(applied.caveats)

    if card is not None and card_mode is not None and card_mode.root == ROOT_SYSTEM_DIRECTORY:
        return _system_directory_placement(
            machine,
            sandbox=query.sandbox,
            cfg_label=query.cfg_label,
            card=card,
            mode=card_mode,
            granularity=granularity,
            layers=layers,
            content=content,
            retroarch_config_dir=retroarch_config_dir,
            sources=tuple(sources_extra),
            caveats=tuple(caveats),
        )

    return _standard_placement(
        machine,
        query,
        layout=layout,
        platform_default_dir=platform_default_dir,
        content=content,
        library_name=core.library_name,
        card=card,
        mode=card_mode,
        granularity=granularity,
        also_under=_also_under_root(
            card_mode,
            sandbox=query.sandbox,
            cfg_label=query.cfg_label,
            layers=layers,
            content=content,
            retroarch_config_dir=retroarch_config_dir,
        ),
        reachable=saves_root.reachable,
        sources=tuple(sources_extra),
        caveats=tuple(caveats),
    )


def _cfg_directory(
    sandbox: _Sandbox, parsed: dict[str, str], key: str
) -> tuple[str | None, tuple[Caveat, ...]]:
    """Resolve a cfg directory key to a host directory that exists, or ``None``.

    The configured value is written in the emulator's own spelling, which inside
    a Flatpak means the sandbox's (``/app/...``, ``/var/config/...``); a caller
    running outside it needs the host location instead. ``None`` means unset,
    reset, unresolvable, or not an existing directory — one honest miss, never a
    path handed out as if it were usable — and a sandbox path with no host
    location comes with the caveat that says so.
    """
    resolved = sandbox.cfg_path(key, parsed.get(key))
    if resolved is None:
        return None, ()
    if resolved.path is None or sandbox.machine.path_kind(resolved.path) != KIND_DIRECTORY:
        return None, resolved.caveats
    return resolved.path, ()


def _firmware_root_missing(root: str) -> Caveat:
    """The whole firmware root is gone — one fact, not one per declared file."""
    return Caveat(
        CAVEAT_FIRMWARE_ROOT_MISSING,
        f"the system_directory {root} is not an existing directory — every declared file below is "
        "missing because the whole firmware root is gone, not one file at a time",
        {"path": root},
    )


def _retroarch_firmware_context(
    *,
    sandbox: _Sandbox,
    global_text: str | None,
    cfg_label: str,
    retroarch_config_dir: str,
    findings: tuple[Caveat, ...],
) -> FirmwareContext:
    """One live read of everything a firmware answer needs, for any arrangement.

    One path for every arrangement (D2): RetroDECK, EmuDeck and a bare
    RetroArch differ only in which cfg is read and whether its paths need
    sandbox translation. The three keys are read independently and may point
    anywhere: ``libretro_info_path`` names the ``.info`` files that declare
    what the installed cores want, ``libretro_directory`` says which of those
    cores are actually installed, and ``system_directory`` is the root the
    declared paths are relative to — the same directory RetroArch hands cores
    when they look their firmware up. RetroDECK happens to collapse the first
    two into one directory; nothing here assumes it.
    """
    machine = sandbox.machine
    parsed = parse_cfg_text(global_text) if global_text is not None else {}
    # The installation's own health leads: whether the arrangement is broken is
    # the most general thing about any answer, so it stands before what this
    # particular read could not resolve. The caller derives the findings from
    # the same reads it passed *global_text* out of.
    caveats: list[Caveat] = list(findings)
    sources: list[str] = []

    raw_system = parsed.get("system_directory")
    configured_system = sandbox.cfg_path("system_directory", raw_system) if raw_system is not None else None
    root = configured_system.path if configured_system is not None else None
    if raw_system is None:
        # Absent is not unset-and-unknown: RetroArch seeded the platform default
        # before it read a line of config, so this route resolves it exactly as
        # the card route does and scans there.
        root = _platform_system_dir(retroarch_config_dir)
        sources.append(PLATFORM_SYSTEM_DIR_SOURCE)
        if machine.path_kind(root) != KIND_DIRECTORY:
            caveats.append(_firmware_root_missing(root))
    elif configured_system is None:
        # Set to blank or the literal "default": the setting is empty, and what
        # the core is handed then depends on the run, not on the config.
        caveats.append(
            Caveat(
                CAVEAT_SYSTEM_DIR_CLEARED,
                f'system_directory = "{raw_system}" clears the setting, so what a core is handed as '
                "its system directory is decided per run: with content loaded RetroArch passes the "
                "content's own directory (runloop.c:1958-1997), and no content is named here — so "
                "there is no one root to check firmware against",
                {"value": raw_system},
            )
        )
    elif root is None:
        caveats.extend(configured_system.caveats)
    else:
        sources.append(f'{cfg_label}: system_directory = "{raw_system}"{configured_system.note}')
        if machine.path_kind(root) != KIND_DIRECTORY:
            caveats.append(_firmware_root_missing(root))

    info_dir, info_caveats = _cfg_directory(sandbox, parsed, "libretro_info_path")
    core_dir, core_dir_caveats = _cfg_directory(sandbox, parsed, "libretro_directory")
    caveats.extend((*info_caveats, *core_dir_caveats))
    cores: tuple[CoreDeclarations, ...] = ()
    cores_listed = False
    if info_dir is None:
        caveats.append(
            Caveat(
                CAVEAT_INFO_PATH_UNRESOLVED,
                "libretro_info_path does not resolve to a readable directory on this machine — the cores' "
                "own firmware declarations cannot be read, so nothing below is declared",
            )
        )
    else:
        sources.append(f"core declarations read live from {info_dir} (.info firmwareN_path)")
        if core_dir is None:
            caveats.append(
                Caveat(
                    CAVEAT_CORE_DIR_UNRESOLVED,
                    "libretro_directory does not resolve to a readable directory — which cores are actually "
                    "installed cannot be checked, so declarations by cores this installation does not ship "
                    "are included",
                )
            )
        else:
            sources.append(f"declarations limited to cores installed in {core_dir}")
        enumeration = read_core_declarations(machine, info_dir, core_dir=core_dir)
        cores = enumeration.cores
        cores_listed = enumeration.listed
        caveats.extend(
            Caveat(
                CAVEAT_CORE_ENUMERATION_INCOMPLETE,
                f"{unreadable} could not be listed (permissions or an I/O failure), so which cores are "
                "installed is unknown — the cores below are the ones atlas could see, not the set this "
                "installation ships",
                {"path": unreadable},
            )
            for unreadable in enumeration.unreadable
        )

    return FirmwareContext(
        root=root,
        cores=cores,
        hashes=load_hashes(),
        # Whether the enumeration happened is now the seam's answer, not a
        # guess from the list being empty. That guess was wrong in the safe
        # direction — a genuinely empty core directory read as "nobody looked",
        # so an installation that ships no cores could never say so — and the
        # case it protected against, a directory that resolves but cannot be
        # listed, is stated above by its own caveat.
        cores_read=info_dir is not None and cores_listed,
        sources=tuple(sources),
        caveats=tuple(caveats),
    )


class _FirmwareQueries:
    """The four firmware entry points, over one live context per query.

    Every handle answers the same four questions; only the way its context is
    assembled differs. An installation whose frontend catalogue can enumerate a
    system's emulators overrides :meth:`firmware_for_system` to pass it.
    """

    kind: str
    _machine: Machine

    def _read_firmware_context(self) -> FirmwareContext:
        raise NotImplementedError  # pragma: no cover - every handle supplies one

    def _stated(self, context: FirmwareContext) -> FirmwareContext:
        """A context with what atlas has established about this arrangement.

        Every firmware answer copies the context's caveats into itself, so
        stating the evidence once here states it on all four. The installation's
        health findings are already at the front of those caveats: each handle
        derives them from the very reads it built the context out of, because a
        second read for them could see a different revision of the same file
        than the answer did (REVIEW M4).
        """
        return _dc_replace(context, caveats=(*context.caveats, *arrangement_caveats(self.kind)))

    def _firmware_context(self) -> FirmwareContext:
        """The handle's live read, stated — the context all four questions answer from."""
        return self._stated(self._read_firmware_context())

    def firmware_for_core(self, *, core_so: str, verify: bool = False) -> FirmwareAnswer:
        """Does *core_so* need firmware, where does each file go, and is it there?"""
        return _resolve_for_core(self._machine, self._firmware_context(), core_so=core_so, verify=verify)

    def firmware_for_system(self, *, system: str, verify: bool = False) -> FirmwareAnswer:
        """Which emulators can run *system*, and what does each of them want?"""
        return _resolve_for_system(self._machine, self._firmware_context(), system=system, verify=verify)

    def firmware_inventory(self, *, verify: bool = False) -> FirmwareAnswer:
        """Every installed core's firmware, plus what is lying around unclaimed."""
        return _resolve_inventory(self._machine, self._firmware_context(), verify=verify)

    def identify_firmware(
        self, *, md5: str | None = None, sha1: str | None = None, size: int | None = None
    ) -> FirmwareIdentification:
        """What is this content, and which requirements here does it satisfy?"""
        return _resolve_identification(
            self._machine, self._firmware_context(), md5=md5, sha1=sha1, size=size
        )


def _card_files(files: tuple[str, ...], rom_stem: str | None) -> tuple[str, ...] | None:
    """Substitute the ``<rom_stem>`` hole in a card's declared file list.

    Returns ``None`` when a template file cannot be filled (no content given) —
    the file set is then honestly unknown rather than a template guess. Holes
    the resolver is not the one to fill (``<save_id>``) stay in the name and
    reach the caller through ``needs``.
    """
    resolved: list[str] = []
    for name in files:
        if TEMPLATE_ROM_STEM in name:
            if rom_stem is None:
                return None
            name = name.replace(TEMPLATE_ROM_STEM, rom_stem)
        resolved.append(name)
    return tuple(resolved)


_JSON_TYPE_NAMES: Mapping[type, str] = {
    bool: "a boolean",
    int: "a number",
    float: "a number",
    list: "a list",
    dict: "an object",
    type(None): "null",
}


def _json_type_name(value: object) -> str:
    """What a JSON value is, for a message that says why the marker is refused."""
    return _JSON_TYPE_NAMES.get(type(value), type(value).__name__)


# The ``retrodeck.json`` path keys atlas reads — the ones :meth:`RetroDeck._config_path`
# is asked for. A key read through it belongs here, because this is the list the
# type check below is scoped to.
_MARKER_PATH_KEYS = ("rd_home_path", "saves_path", "bios_path", "roms_path")


def _malformed_marker_paths(config: Mapping[str, Any]) -> tuple[str, str] | None:
    """A ``paths`` value atlas reads that is not a path string — ``(key, complaint)``.

    ``retrodeck.json`` is editable and its ``paths`` section drives every read
    that follows, so the type is checked here, at the one place the marker is
    read, rather than at each use: a non-string leaking out of
    :meth:`RetroDeck.root` reaches ``os.stat``, which reads an ``int`` as a
    *file descriptor* and so can answer for something that is not even a path.

    Scoped to :data:`_MARKER_PATH_KEYS`, because atlas reports on what it reads.
    A value under some other key says nothing about the reads atlas performs,
    and calling the marker broken over it would be a claim about who wrote the
    file rather than a reading of the machine — a future RetroDECK that nests
    something new under ``paths`` would take a healthy installation down to its
    fallback roots. A ``paths`` that is not an object at all is different: no
    key can be read from it, so every read is affected.
    """
    if "paths" not in config:
        return None
    # Absent and null are different states: a marker that omits the section
    # says nothing and the fallbacks answer, while one that writes ``null``
    # there has written a section no key can be read from.
    paths: object = config["paths"]
    if not isinstance(paths, dict):
        return "paths", f"is {_json_type_name(paths)}, not an object of path strings"
    for key in _MARKER_PATH_KEYS:
        if key not in paths:
            continue
        value: object = paths[key]
        if not isinstance(value, str):
            return f"paths.{key}", f"is {_json_type_name(value)}, not a path string"
    return None


def _normalized_path(path: str) -> str:
    """One spelling for two paths that name the same place, for comparison only.

    Lexical: separators, ``.``, ``..`` and repeated slashes are folded, links
    are not followed. Nothing here touches the machine.
    """
    return os.path.normpath(path.replace("\\", "/"))


def _match_per_game(
    selections: GamelistSelections, content_path: str, *, system_roms_dir: str
) -> str | None:
    """Match a content path against per-game ``altemulator`` entries, anchored.

    Gamelist paths are relative to the system's own ROM directory
    (``./Name.ext``, or ``./Folder`` for multi-disc directory entries) and may
    contain subdirectories — ``./USA/Game.iso`` and ``./Japan/Game.iso`` are
    distinct games. So each entry is resolved against *system_roms_dir* and
    compared as a whole path: an unanchored suffix match hands one game's
    selection to every same-named file at any depth below, and the live psx
    gamelist has exactly that shape — a root-level ``./<name>.m3u`` carrying the
    override next to a folder of the same name holding a second ``<name>.m3u``
    that carries none.

    A directory entry matches the files inside it, which is what makes the
    multi-disc convention work; an entry naming the content file itself is the
    more specific statement and wins over a directory entry that also covers it.

    **The comparison is lexical** (:func:`_normalized_path`): a content path
    spelled through a symlink — and RetroDECK's own tree uses symlinks liberally
    — names the same file for the kernel but not for this match, so the answer
    falls back to the per-system selection. Resolving would cost a read per
    entry per query, which is exactly what the one-read-per-source rule exists
    to prevent, so the limit is stated (here and in the developer guide) rather
    than paid for: name the content the way it lies under the ROM directory.
    """
    content = _normalized_path(content_path)
    parent = os.path.dirname(content)
    directory_label: str | None = None
    for rel_path, label in selections.per_game.items():
        entry = _normalized_path(os.path.join(system_roms_dir, rel_path))
        if content == entry:
            return label
        if directory_label is None and parent == entry:
            directory_label = label
    return directory_label


@runtime_checkable
class _CatalogueHost(Protocol):
    """What an entry needs back from the installation that produced it.

    Narrow on purpose: an entry asks its installation exactly one thing, so the
    type it is bound to is that one method rather than a concrete handle. It
    used to be ``RetroDeck``, which made every entry — and so the catalogue
    answer itself — RetroDECK's to hand out.
    """

    def entry_save_location(
        self,
        spec: EmulatorSpec,
        entry_caveats: tuple[Caveat, ...] = (),
        *,
        content_path: str | None = None,
    ) -> SavePlacement | Unresolved: ...


@dataclass(frozen=True, slots=True)
class CatalogueAnswer:
    """Which emulators a frontend catalogue declares for one system.

    An answer object rather than a bare tuple for the reason every other answer
    here is one: empty is not self-explaining. The catalogue may declare no
    emulator for this system, or the arrangement may have no catalogue, or its
    catalogue may not have been readable — three different facts that a tuple
    spells the same way. ``caveats`` carries which, ``sources`` says what was
    read to find out.
    """

    entries: tuple[EmulatorEntry, ...] = ()
    sources: tuple[str, ...] = ()
    caveats: tuple[Caveat, ...] = ()


@dataclass(frozen=True, slots=True)
class SystemsAnswer:
    """Every system a frontend catalogue declares, and what was read to say so.

    The same empty-is-ambiguous problem as :class:`CatalogueAnswer`, one level
    up: a catalogue that could not be read lists no systems, and so does one
    that genuinely declares none.
    """

    systems: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    caveats: tuple[Caveat, ...] = ()


class EmulatorEntry:
    """One catalogue entry — an emulator that can launch one system, as configured.

    Wraps an :class:`~atlas.esde.EmulatorSpec` and the installation it belongs
    to, so the entry can answer placement questions with its core always known —
    the ``no-core`` caveat class does not exist on this path.
    """

    def __init__(
        self, installation: "_CatalogueHost", spec: EmulatorSpec, caveats: tuple[Caveat, ...] = ()
    ) -> None:
        self._installation = installation
        self._spec = spec
        self._caveats = caveats

    @property
    def system(self) -> str:
        return self._spec.system

    @property
    def label(self) -> str:
        return self._spec.label

    @property
    def kind(self) -> str:
        return self._spec.kind

    @property
    def core_so(self) -> str | None:
        return self._spec.core_so

    @property
    def command(self) -> str:
        return self._spec.command

    @property
    def source(self) -> str:
        return self._spec.source

    @property
    def selection(self) -> str | None:
        """Provenance of a user promotion, or ``None`` for declared order."""
        return self._spec.selection

    @property
    def caveats(self) -> tuple[Caveat, ...]:
        """Stated catalogue-level degradations (e.g. unchecked per-game overrides)."""
        return self._caveats

    def save_location(self, *, content_path: str | None = None) -> SavePlacement | Unresolved:
        """Where this emulator keeps the save — core filled in from the catalogue.

        Catalogue-level degradations stay attached to the derived answer
        (REVIEW M9). Standalone entries are outside the resolver's coverage
        until the standalone block lands — that is a domain outcome
        (:class:`~atlas.placement.Unresolved`), never a guess and never an
        exception (REVIEW M8).
        """
        if self._spec.kind != KIND_LIBRETRO:
            return Unresolved(
                UNRESOLVED_STANDALONE,
                f"standalone emulator {self._spec.label!r} ({self._spec.system}) is not resolvable yet — "
                "standalone emulators are the next big roadmap block (ROADMAP.md)",
                {"label": self._spec.label, "system": self._spec.system},
            )
        return self._installation.entry_save_location(
            self._spec, self._caveats, content_path=content_path
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"EmulatorEntry(system={self._spec.system!r}, label={self._spec.label!r}, "
            f"kind={self._spec.kind!r}, core_so={self._spec.core_so!r})"
        )


class _CatalogueQueries:
    """The catalogue entry points, answered by every handle — including with a refusal.

    "Which emulator would launch this?" is a question about an arrangement, not
    a RetroDECK feature, so every handle answers it. Only RetroDECK answers it
    *from a catalogue*; the others state why they cannot, and those reasons are
    different claims that must not collapse into one empty tuple:

    - a bare RetroArch has no frontend catalogue at all — a fact about the
      arrangement, and a settled one;
    - an EmuDeck arrangement may well have one (it can be driven by ES-DE,
      Pegasus or Steam ROM Manager), and atlas has not established where. That
      is a statement about atlas, not about the machine, and a client that read
      it as "no emulators" would be told something nobody checked.
    """

    kind: str

    def _catalogue_absence(self) -> Caveat:
        raise NotImplementedError  # pragma: no cover - every handle supplies one

    def systems(self) -> SystemsAnswer:
        """Every system the frontend catalogue declares, sorted."""
        answer = self._systems_answer()
        return _dc_replace(answer, caveats=(*answer.caveats, *arrangement_caveats(self.kind)))

    def emulators_for(self, system: str, *, content_path: str | None = None) -> CatalogueAnswer:
        """The emulators that can launch *system*, in launch-priority order.

        The first entry is the effective default, resolved live through the
        frontend's own hierarchy: a per-game ``altemulator`` (when
        *content_path* is given and a gamelist entry matches it) outranks a
        per-system ``alternativeEmulator``, which outranks the declared order.
        A selection naming no declared entry keeps the declared order — ES-DE
        falls back the same way.

        When *content_path* is omitted and the gamelist carries per-game
        overrides, every returned entry states that as a catalogue caveat: the
        system-level answer may be wrong for exactly those games.

        No entries is four different facts, and the three
        ``emulator-catalogue-*`` codes tell them apart. None of the three means
        the catalogue was read and the frontend knows no emulator for this
        system — the only one of the four that is a statement about the
        machine. The other three say why nobody could answer from a catalogue
        at all: the arrangement ships none, atlas has not established where it
        keeps one, or the one it has could not be read.

        The test is those codes, not an empty ``caveats``: a broken
        installation states its health findings on this answer as on every
        other, so an empty caveat list is not what "read, and it declares
        nothing" looks like there.
        """
        answer = self._catalogue_answer(system, content_path=content_path)
        return _dc_replace(answer, caveats=(*answer.caveats, *arrangement_caveats(self.kind)))

    # The two public questions above are the whole catalogue surface, and a
    # handle overrides the two below instead: what it *answers* is its own,
    # how the answer states its arrangement's evidence is not. Splitting them
    # is what makes "every answer says what atlas has established about this
    # arrangement" a property of the surface rather than of remembering.

    def health(self) -> Health:
        raise NotImplementedError  # pragma: no cover - every handle answers it

    def _systems_answer(self) -> SystemsAnswer:
        # A handle with no catalogue reads nothing to refuse, so its health is
        # this query's own single read of the sources — no snapshot to reuse
        # and none read twice.
        return SystemsAnswer(caveats=(*self.health().issues, self._catalogue_absence()))

    def _catalogue_answer(self, system: str, *, content_path: str | None = None) -> CatalogueAnswer:
        return CatalogueAnswer(caveats=(*self.health().issues, self._catalogue_absence()))


class RetroDeck(_FirmwareQueries, _CatalogueQueries):
    """A RetroDECK installation — cfg is the truth, ``retrodeck.json`` is context.

    The handle is *live*: it stores only its identity (home) and the machine
    seam. Every query re-reads the governing sources — each exactly once — and
    derives all decisions from that one snapshot, so a concurrent config edit
    can never mix two revisions inside one answer (REVIEW M4).
    """

    kind = "retrodeck"
    kinds = ("retrodeck",)
    _APP_ID = RETRODECK_APP_ID

    def __init__(self, home: str, machine: Machine) -> None:
        self._home = home
        self._machine = machine

    def _marker_path(self) -> str:
        return os.path.join(self._home, RETRODECK_JSON_SUFFIX)

    def _read_marker(self) -> tuple[dict[str, Any], tuple[Caveat, ...]]:
        """One live read of ``retrodeck.json`` → (config, marker issues).

        Missing, unreadable, and invalid are distinct states — a marker that
        exists but cannot be read or parsed is a *present, broken* RetroDECK,
        never an absent one (REVIEW H10).
        """
        path = self._marker_path()
        result = self._machine.read_text(path)
        if result.status == READ_MISSING:
            return {}, (Caveat(HEALTH_ISSUE_MARKER_MISSING, f"marker {path} does not exist", {"path": path}),)
        if result.text is None:
            return {}, (
                Caveat(
                    HEALTH_ISSUE_MARKER_UNREADABLE,
                    f"marker {path} cannot be read as text ({result.status})",
                    {"path": path, "status": result.status},
                ),
            )
        try:
            data = json.loads(result.text)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return {}, (
                Caveat(HEALTH_ISSUE_MARKER_INVALID, f"marker {path} is not a JSON object", {"path": path}),
            )
        malformed = _malformed_marker_paths(data)
        if malformed is not None:
            key, complaint = malformed
            return {}, (
                Caveat(
                    HEALTH_ISSUE_MARKER_INVALID,
                    f"marker {path}: {key} {complaint} — atlas reads it to resolve RetroDECK's "
                    "roots, so those roots fall back to their defaults instead",
                    {"path": path, "key": key},
                ),
            )
        return data, ()

    def _config_path(self, config: dict[str, Any], key: str, fallback_subdir: str) -> tuple[str, str]:
        """Resolve a RetroDECK path and its provenance from a marker snapshot.

        A sub-path key that is unset falls back under the *resolved* root
        (``rd_home_path`` or its own ``~/retrodeck`` fallback) — RetroDECK
        lays its tree out under the home it was pointed at, so the honest
        default follows the configured root, not a hard-coded one.

        Only a string is a path: :meth:`_read_marker` refuses a marker whose
        ``paths`` hold anything else, so the check here is what makes that
        guarantee local — nothing but a path ever leaves this method.
        """
        paths = config.get("paths")
        if isinstance(paths, dict):
            value: object = paths.get(key, "")
            if isinstance(value, str) and value:
                return value, f"retrodeck.json: paths.{key}"
        if not fallback_subdir:
            fallback = os.path.join(self._home, "retrodeck")
        else:
            root = self._config_path(config, "rd_home_path", "")[0]
            fallback = os.path.join(root, fallback_subdir)
        return fallback, f"default: {key} unset → {fallback}"

    def root(self) -> str:
        """The RetroDECK home directory (``rd_home_path`` or the fallback)."""
        return self._config_path(self._read_marker()[0], "rd_home_path", "")[0]

    def saves_root(self) -> str:
        """The RetroDECK saves root (``saves_path`` or the fallback)."""
        return self._config_path(self._read_marker()[0], "saves_path", "saves")[0]

    def bios_dir(self) -> str:
        """The RetroDECK BIOS directory (``bios_path`` or the fallback)."""
        return self._config_path(self._read_marker()[0], "bios_path", "bios")[0]

    def roms_dir(self) -> str:
        """The RetroDECK ROMs directory (``roms_path`` or the fallback)."""
        return self._config_path(self._read_marker()[0], "roms_path", "roms")[0]

    def _health_from(self, config: dict[str, Any], marker_issues: tuple[Caveat, ...]) -> Health:
        issues = list(marker_issues)
        root = self._config_path(config, "rd_home_path", "")[0]
        if self._machine.path_kind(root) != KIND_DIRECTORY:
            issues.append(
                Caveat(HEALTH_ISSUE_ROOT_MISSING, f"root {root} is not an existing directory", {"path": root})
            )
        saves = self._config_path(config, "saves_path", "saves")[0]
        if self._machine.path_kind(saves) != KIND_DIRECTORY:
            issues.append(
                Caveat(
                    HEALTH_ISSUE_SAVES_ROOT_MISSING,
                    f"saves root {saves} is not an existing directory",
                    {"path": saves},
                )
            )
        return Health(tuple(issues))

    def health(self) -> Health:
        """Installation health — marker readable and parseable, roots present."""
        config, marker_issues = self._read_marker()
        return self._health_from(config, marker_issues)

    def _retroarch_config_dir(self) -> str:
        return os.path.join(self._home, ".var", "app", self._APP_ID, "config", "retroarch")

    def _sandbox(self) -> _Sandbox:
        return _Sandbox(self._machine, self._home, self._APP_ID)

    def _core_path_in(self, global_text: str | None, core_so: str) -> str | None:
        """Resolve a core ``.so`` basename against a cfg snapshot's ``libretro_directory``.

        The configured value is written in the sandbox's spelling (live:
        ``/app/retrodeck/components/...``) and is translated to where the host
        reads it. ``None`` when nothing resolvable — never a guess.
        """
        if global_text is None:
            return None
        cores_dir = _core_directory_in(self._sandbox(), global_text)
        if cores_dir is None:
            return None
        return os.path.join(cores_dir, core_so)

    # ES-DE catalogue — read live: bundled file in the Flatpak deployment,
    # user overlay under <rd_home>/ES-DE/custom_systems (observed layout).
    _ESDE_BUNDLED_SANDBOX = "/app/retrodeck/components/es-de/share/es-de/resources/systems/linux/es_systems.xml"

    def _read_catalogue(self, root: str) -> tuple[dict[str, tuple[EmulatorSpec, ...]], bool]:
        """The merged ES-DE catalogue, and whether the bundled layer could be read.

        The second value is not a detail: an empty catalogue because the shipped
        ``es_systems.xml`` was unreadable says nothing about which emulators
        exist, while an empty *lookup* in a catalogue that was read says the
        frontend knows none for that system. The custom overlay is genuinely
        optional, so only the bundled layer decides.
        """
        bundled: dict[str, tuple[EmulatorSpec, ...]] = {}
        read = False
        bundled_path = self._sandbox().bundled(self._ESDE_BUNDLED_SANDBOX)
        if bundled_path is not None:
            text = self._machine.read_text(bundled_path).text
            if text is not None:
                bundled = parse_es_systems(text, source="es_systems.xml (bundled)")
                # Read AND parsed: parse_es_systems answers {} for malformed
                # XML, and an enumeration that came back empty because the file
                # is broken is not an enumeration. RetroDECK ships the custom
                # overlay fully commented out, so it parses to zero systems by
                # design and can never stand in for this.
                read = bool(bundled)
        custom: dict[str, tuple[EmulatorSpec, ...]] = {}
        custom_path = os.path.join(root, "ES-DE", "custom_systems", "es_systems.xml")
        custom_text = self._machine.read_text(custom_path).text
        if custom_text is not None:
            custom = parse_es_systems(custom_text, source="es_systems.xml (custom_systems overlay)")
        return merge_layers(bundled, custom), read

    def _catalogue_unread_caveat(self, system: str | None = None) -> tuple[Caveat, ...]:
        """The caveat for a catalogue that could not be read — the same fact as the firmware route's.

        One fact, one code: ``firmware_for_system`` already states this exact
        thing when its catalogue comes back unread, and an answer that is empty
        because nobody could look is the same answer whichever door it left by.
        """
        return (
            Caveat(
                CAVEAT_CATALOGUE_UNREADABLE,
                "the frontend's emulator catalogue could not be read, so which emulators this "
                "installation knows is unknown — this answer is empty because atlas could not look, "
                "not because nothing is there",
                {"system": system} if system is not None else {},
            ),
        )

    _CATALOGUE_SOURCE = "ES-DE catalogue read live (es_systems.xml, bundled + custom_systems overlay)"

    def _systems_answer(self) -> SystemsAnswer:
        """Every system the catalogue declares, sorted.

        The findings come from the marker snapshot this query already read, so
        the answer's health and its roots are one revision of the file.
        """
        config, marker_issues = self._read_marker()
        findings = self._health_from(config, marker_issues).issues
        by_system, read = self._read_catalogue(self._config_path(config, "rd_home_path", "")[0])
        if not read:
            return SystemsAnswer(caveats=(*findings, *self._catalogue_unread_caveat()))
        return SystemsAnswer(tuple(sorted(by_system)), (self._CATALOGUE_SOURCE,), findings)

    def _gamelist_selections_at(self, root: str, system: str) -> GamelistSelections:
        gamelist_path = os.path.join(root, "ES-DE", "gamelists", system, "gamelist.xml")
        text = self._machine.read_text(gamelist_path).text
        if text is None:
            return GamelistSelections(system_label=None, per_game={})
        return parse_gamelist(text)

    def gamelist_selections(self, system: str) -> GamelistSelections:
        config, _ = self._read_marker()
        return self._gamelist_selections_at(self._config_path(config, "rd_home_path", "")[0], system)

    def _system_roms_dir(self, config: dict[str, Any], system: str) -> str:
        """Where this system's ROMs live — the anchor gamelist paths are relative to."""
        return os.path.join(self._config_path(config, "roms_path", "roms")[0], system)

    def _catalogue_answer(self, system: str, *, content_path: str | None = None) -> CatalogueAnswer:
        """RetroDECK's own catalogue answer — one snapshot of the ES-DE sources.

        The contract this fills in is on
        :meth:`_CatalogueQueries.emulators_for`; what is RetroDECK's alone is
        where the answer comes from: the marker, the bundled ``es_systems.xml``
        plus its ``custom_systems`` overlay, and the system's gamelist, each
        read once here and handed to the entry assembly together (REVIEW M4).
        """
        config, marker_issues = self._read_marker()
        findings = self._health_from(config, marker_issues).issues
        root = self._config_path(config, "rd_home_path", "")[0]
        by_system, read = self._read_catalogue(root)
        if not read:
            return CatalogueAnswer(caveats=(*findings, *self._catalogue_unread_caveat(system)))
        return CatalogueAnswer(
            self._entries_from(
                by_system.get(system, ()),
                self._gamelist_selections_at(root, system),
                system_roms_dir=self._system_roms_dir(config, system),
                content_path=content_path,
            ),
            (self._CATALOGUE_SOURCE,),
            findings,
        )

    def _entries_from(
        self,
        specs: tuple[EmulatorSpec, ...],
        selections: GamelistSelections,
        *,
        system_roms_dir: str,
        content_path: str | None,
    ) -> tuple[EmulatorEntry, ...]:
        """Apply ES-DE's selection hierarchy to one already-read catalogue snapshot.

        Split out from :meth:`emulators_for` so the firmware route can ask the
        same question of the sources it has already read, instead of reading
        the marker and both catalogue layers a second time.
        """
        chosen_label: str | None = None
        chosen_source: str | None = None
        if content_path is not None:
            per_game = _match_per_game(selections, content_path, system_roms_dir=system_roms_dir)
            if per_game is not None:
                chosen_label = per_game
                chosen_source = f'gamelist.xml: altemulator = "{per_game}" (per-game)'
        if chosen_label is None and selections.system_label is not None:
            chosen_label = selections.system_label
            chosen_source = f'gamelist.xml: alternativeEmulator = "{selections.system_label}"'
        if specs and chosen_label is not None:
            for index, spec in enumerate(specs):
                if spec.label == chosen_label:
                    promoted = _dc_replace(spec, selection=chosen_source)
                    specs = (promoted, *specs[:index], *specs[index + 1 :])
                    break
        entry_caveats: tuple[Caveat, ...] = ()
        if content_path is None and selections.per_game:
            entry_caveats = (
                Caveat(
                    CAVEAT_PER_GAME_OVERRIDES_PRESENT,
                    f"{len(selections.per_game)} game(s) of this system carry per-game altemulator "
                    "overrides — this system-level order may be wrong for exactly those games; "
                    "ask emulators_for with content_path",
                    {"count": str(len(selections.per_game))},
                ),
            )
        return tuple(EmulatorEntry(self, spec, entry_caveats) for spec in specs)

    def _save_location_from(
        self,
        config: dict[str, Any],
        marker_issues: tuple[Caveat, ...],
        *,
        content_path: str | None,
        core_so: str | None,
        extra_caveats: tuple[Caveat, ...] = (),
    ) -> SavePlacement:
        health = self._health_from(config, marker_issues)
        global_cfg_path = os.path.join(self._home, RETRODECK_CFG_SUFFIX)
        global_text = self._machine.read_text(global_cfg_path).text
        version = config.get("version")
        return _retroarch_save_location(
            self._machine,
            _SaveQuery(
                sandbox=self._sandbox(),
                global_cfg_path=global_cfg_path,
                global_text=global_text,
                cfg_label=RETROARCH_CFG,
                override_config_dir=os.path.join(self._retroarch_config_dir(), "config"),
                defaults=UPSTREAM_DEFAULTS,
                content_path=content_path,
                core_so=core_so,
                core_path_resolver=lambda so: self._core_path_in(global_text, so),
                arrangement="retrodeck",
                arrangement_version=version if isinstance(version, str) else None,
                extra_caveats=(
                    *extra_caveats,
                    *health.issues,
                    *arrangement_caveats(self.kind),
                ),
            ),
        )

    def save_location(self, *, content_path: str | None = None, core_so: str | None = None) -> SavePlacement:
        """Where this RetroDECK's RetroArch keeps the save for *content_path* under *core_so*.

        ``core_so`` is the core's ``.so`` basename (e.g.
        ``"mupen64plus_next_libretro.so"``) or a full path; atlas resolves
        ``library_name`` from the binary. Both arguments are optional — missing
        ones leave holes and stated caveats, never guesses.
        """
        config, marker_issues = self._read_marker()
        return self._save_location_from(config, marker_issues, content_path=content_path, core_so=core_so)

    def _firmware_context_from(
        self, config: dict[str, Any], marker_issues: tuple[Caveat, ...]
    ) -> FirmwareContext:
        """The firmware context over a marker snapshot this query already read.

        Health comes from that same snapshot rather than from a fresh
        :meth:`health` call, so the findings an answer states and the roots it
        resolved were read from one revision of ``retrodeck.json``.
        """
        return _retroarch_firmware_context(
            sandbox=self._sandbox(),
            global_text=self._machine.read_text(os.path.join(self._home, RETRODECK_CFG_SUFFIX)).text,
            cfg_label=RETROARCH_CFG,
            retroarch_config_dir=self._retroarch_config_dir(),
            findings=self._health_from(config, marker_issues).issues,
        )

    def _read_firmware_context(self) -> FirmwareContext:
        return self._firmware_context_from(*self._read_marker())

    def firmware_for_system(self, *, system: str, verify: bool = False) -> FirmwareAnswer:
        """Which emulators RetroDECK offers for *system*, and what each of them wants.

        *system* is the ES-DE system name (``"gb"``, ``"dreamcast"``), the same
        vocabulary :meth:`emulators_for` speaks — the catalogue is the
        enumeration, so an emulator whose core is not installed and a
        standalone emulator both appear, stated as such instead of silently
        dropped. Whether that catalogue could be read travels with it: an
        unreadable ``es_systems.xml`` must never come out as "this machine has
        no emulator for that system".

        The catalogue is the one :meth:`emulators_for` answers from, assembled
        from this query's own snapshot: marker and both catalogue layers are
        read once here and handed on, never re-read.
        """
        config, marker_issues = self._read_marker()
        root = self._config_path(config, "rd_home_path", "")[0]
        by_system, read = self._read_catalogue(root)
        entries = self._entries_from(
            by_system.get(system, ()),
            self._gamelist_selections_at(root, system),
            system_roms_dir=self._system_roms_dir(config, system),
            content_path=None,
        )
        catalogue = Catalogue(
            entries=tuple(
                CatalogueEntry(label=entry.label, kind=entry.kind, core_so=entry.core_so)
                for entry in entries
            ),
            read=read,
        )
        # The marker this query already read builds the context too — asking
        # for a fresh one would read retrodeck.json twice inside one answer.
        context = self._stated(self._firmware_context_from(config, marker_issues))
        return _resolve_for_system(
            self._machine, context, system=system, catalogue=catalogue, verify=verify
        )

    def entry_save_location(
        self,
        spec: EmulatorSpec,
        entry_caveats: tuple[Caveat, ...] = (),
        *,
        content_path: str | None = None,
    ) -> SavePlacement:
        """The entry route behind :meth:`EmulatorEntry.save_location` — one marker read.

        Resolves the placement for a catalogue entry and, when *content_path*
        is given, checks the gamelist for a per-game override that would launch
        a different emulator — all from one snapshot of the governing sources.
        """
        config, marker_issues = self._read_marker()
        placement = self._save_location_from(
            config,
            marker_issues,
            content_path=content_path,
            core_so=spec.core_so,
            extra_caveats=entry_caveats,
        )
        if content_path is not None:
            root = self._config_path(config, "rd_home_path", "")[0]
            selections = self._gamelist_selections_at(root, spec.system)
            override_label = _match_per_game(
                selections, content_path, system_roms_dir=self._system_roms_dir(config, spec.system)
            )
            if override_label is not None and override_label != spec.label:
                placement = _dc_replace(
                    placement,
                    caveats=(
                        *placement.caveats,
                        Caveat(
                            CAVEAT_PER_GAME_OVERRIDE,
                            f"this game carries a per-game altemulator override selecting "
                            f"{override_label!r} — ES-DE would launch that emulator, not "
                            f"{spec.label!r}; ask emulators_for with content_path",
                            {"label": override_label},
                        ),
                    ),
                )
        return placement


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


class EmuDeck(_FirmwareQueries, _CatalogueQueries):
    """An EmuDeck arrangement — ``settings.sh`` is its truth, the standalone
    ``org.libretro.RetroArch`` Flatpak is its RetroArch.

    The handle carries both descriptions (``kinds``): EmuDeck *is* a configured
    standalone RetroArch, so both statements are true of the same installation.
    Like every handle it is live — one snapshot of each source per query
    (REVIEW M4) — and its health covers the claimed companion RetroArch config,
    so a stale ``settings.sh`` next to a vanished Flatpak is visible instead of
    silently suppressing the standalone handle (REVIEW H10).
    """

    kind = "emudeck"
    kinds = ("emudeck", "standalone_retroarch_flatpak")
    _RA_APP_ID = RETROARCH_FLATPAK_APP_ID

    def _catalogue_absence(self) -> Caveat:
        # Not "there is none": EmuDeck installs a frontend, and which one — and
        # where it keeps its catalogue — is what atlas has not established. The
        # honest answer is about atlas, so the code says unestablished and the
        # message does not describe the machine.
        return Caveat(
            CAVEAT_CATALOGUE_UNESTABLISHED,
            "atlas has not established where an EmuDeck arrangement keeps its frontend catalogue — "
            "EmuDeck can be driven by ES-DE, Pegasus or Steam ROM Manager — so which emulators run a "
            "system is unknown here; name the core yourself with save_location(core_so=...)",
            {"arrangement": "emudeck"},
        )

    def __init__(self, home: str, machine: Machine) -> None:
        self._home = home
        self._machine = machine

    def _marker_path(self) -> str:
        return os.path.join(self._home, EMUDECK_SETTINGS_SUFFIX)

    def _read_marker(self) -> tuple[dict[str, str], tuple[Caveat, ...]]:
        """One live read of ``settings.sh`` → (settings, marker issues)."""
        path = self._marker_path()
        result = self._machine.read_text(path)
        if result.status == READ_MISSING:
            return {}, (Caveat(HEALTH_ISSUE_MARKER_MISSING, f"marker {path} does not exist", {"path": path}),)
        if result.text is None:
            return {}, (
                Caveat(
                    HEALTH_ISSUE_MARKER_UNREADABLE,
                    f"marker {path} cannot be read as text ({result.status})",
                    {"path": path, "status": result.status},
                ),
            )
        return _parse_settings_sh(result.text, home=self._home), ()

    def _setting_path(self, settings: dict[str, str], key: str, fallback_subdir: str) -> tuple[str, str]:
        value = settings.get(key, "")
        if value:
            return value, f"settings.sh: {key}"
        fallback = os.path.join(self._home, "Emulation", fallback_subdir)
        return fallback, f"default: {key} unset → {fallback} (EmuDeck default)"

    def root(self) -> str:
        """The EmuDeck ``Emulation`` tree root (parent of ``romsPath``)."""
        return os.path.dirname(self._setting_path(self._read_marker()[0], "romsPath", "roms")[0])

    def saves_root(self) -> str:
        """EmuDeck's saves root (``savesPath`` or the default)."""
        return self._setting_path(self._read_marker()[0], "savesPath", "saves")[0]

    def bios_dir(self) -> str:
        """EmuDeck's BIOS directory (``biosPath`` or the default)."""
        return self._setting_path(self._read_marker()[0], "biosPath", "bios")[0]

    def _companion_cfg_path(self) -> str:
        return os.path.join(self._home, STANDALONE_FLATPAK_CFG_SUFFIX)

    def _health_from(
        self,
        settings: dict[str, str],
        marker_issues: tuple[Caveat, ...],
        companion_status: str,
    ) -> Health:
        issues = list(marker_issues)
        root = os.path.dirname(self._setting_path(settings, "romsPath", "roms")[0])
        if self._machine.path_kind(root) != KIND_DIRECTORY:
            issues.append(
                Caveat(HEALTH_ISSUE_ROOT_MISSING, f"root {root} is not an existing directory", {"path": root})
            )
        saves = self._setting_path(settings, "savesPath", "saves")[0]
        if self._machine.path_kind(saves) != KIND_DIRECTORY:
            issues.append(
                Caveat(
                    HEALTH_ISSUE_SAVES_ROOT_MISSING,
                    f"saves root {saves} is not an existing directory",
                    {"path": saves},
                )
            )
        if companion_status != "ok":
            path = self._companion_cfg_path()
            issues.append(
                Caveat(
                    HEALTH_ISSUE_COMPANION_CONFIG_MISSING,
                    f"the claimed org.libretro.RetroArch config {path} is not readable "
                    f"({companion_status}) — the arrangement's RetroArch side is broken or stale",
                    {"path": path, "status": companion_status},
                )
            )
        return Health(tuple(issues))

    def health(self) -> Health:
        """Installation health — marker, roots, and the claimed companion RetroArch config."""
        settings, marker_issues = self._read_marker()
        companion_status = self._machine.read_text(self._companion_cfg_path()).status
        return self._health_from(settings, marker_issues, companion_status)

    def _retroarch_config_dir(self) -> str:
        return os.path.join(self._home, ".var", "app", self._RA_APP_ID, "config", "retroarch")

    def _sandbox(self) -> _Sandbox:
        return _Sandbox(self._machine, self._home, self._RA_APP_ID)

    def _core_path_in(self, global_text: str | None, core_so: str) -> str | None:
        if global_text is None:
            return None
        cores_dir = _core_directory_in(self._sandbox(), global_text)
        if cores_dir is None:
            return None
        return os.path.join(cores_dir, core_so)

    def save_location(self, *, content_path: str | None = None, core_so: str | None = None) -> SavePlacement:
        """Where EmuDeck's RetroArch keeps the save — resolved from the standalone Flatpak cfg."""
        settings, marker_issues = self._read_marker()
        global_cfg_path = self._companion_cfg_path()
        cfg = self._machine.read_text(global_cfg_path)
        health = self._health_from(settings, marker_issues, cfg.status)
        return _retroarch_save_location(
            self._machine,
            _SaveQuery(
                sandbox=self._sandbox(),
                global_cfg_path=global_cfg_path,
                global_text=cfg.text,
                cfg_label=RETROARCH_CFG,
                override_config_dir=os.path.join(self._retroarch_config_dir(), "config"),
                defaults=UPSTREAM_DEFAULTS,
                content_path=content_path,
                core_so=core_so,
                core_path_resolver=lambda so: self._core_path_in(cfg.text, so),
                arrangement="emudeck",
                arrangement_version=None,
                extra_caveats=(*health.issues, *arrangement_caveats(self.kind)),
            ),
        )

    def _read_firmware_context(self) -> FirmwareContext:
        """The cfg is the truth here too: ``settings.sh`` names a ``biosPath``,
        but what RetroArch actually hands its cores is ``system_directory``.

        The companion cfg is read once and answers both questions asked of it —
        its text builds the context, its status decides the companion health
        finding — so the two can never describe different revisions of it.
        """
        settings, marker_issues = self._read_marker()
        cfg = self._machine.read_text(self._companion_cfg_path())
        return _retroarch_firmware_context(
            sandbox=self._sandbox(),
            global_text=cfg.text,
            cfg_label=RETROARCH_CFG,
            retroarch_config_dir=self._retroarch_config_dir(),
            findings=self._health_from(settings, marker_issues, cfg.status).issues,
        )


class _RetroArchInstall(_FirmwareQueries, _CatalogueQueries):
    """Shared behavior for a bare RetroArch install (standalone Flatpak or native).

    The saves root comes from the cfg's ``savefile_directory``; when unset, the
    RetroArch platform default applies — ``saves`` under the config tree that
    holds ``retroarch.cfg`` (``platform_unix.c:2133-2134``), never the ROM's own
    directory (the ``runloop.c:8786`` content fallback fires only when the
    effective dir is still empty, which the platform defaults prevent on
    desktop). Bare installs get RetroArch's upstream compile-time defaults,
    under which ``sort_savefiles_enable`` is **true**.
    """

    kinds: tuple[str, ...] = ()
    _app_id: str | None = None

    def _catalogue_absence(self) -> Caveat:
        # A settled fact about the arrangement, not a gap in atlas: RetroArch
        # has no frontend catalogue. The same code the firmware route already
        # uses to say so.
        return Caveat(
            CAVEAT_CATALOGUE_UNAVAILABLE,
            "a bare RetroArch install ships no frontend catalogue, so there is no launch entry to "
            "answer with — name the core yourself with save_location(core_so=...)",
            {"arrangement": "retroarch"},
        )

    def __init__(self, home: str, machine: Machine, cfg_suffix: str) -> None:
        self._home = home
        self._machine = machine
        self._cfg_suffix = cfg_suffix

    def _cfg_path(self) -> str:
        return os.path.join(self._home, self._cfg_suffix)

    def root(self) -> str:
        """The RetroArch config directory (the folder holding ``retroarch.cfg``)."""
        return os.path.dirname(self._cfg_path())

    def _health_from(self, cfg_status: str) -> Health:
        path = self._cfg_path()
        if cfg_status == READ_OK:
            return Health()
        if cfg_status == READ_MISSING:
            return Health((Caveat(HEALTH_ISSUE_MARKER_MISSING, f"marker {path} does not exist", {"path": path}),))
        return Health(
            (
                Caveat(
                    HEALTH_ISSUE_CONFIG_UNREADABLE,
                    f"config {path} cannot be read as text ({cfg_status})",
                    {"path": path, "status": cfg_status},
                ),
            )
        )

    def health(self) -> Health:
        """Bare installs: the cfg is the marker — health is its read status."""
        return self._health_from(self._machine.read_text(self._cfg_path()).status)

    def _sandbox(self) -> _Sandbox:
        """This install's cfg spellings — a native install's are the host's own.

        ``_app_id`` is ``None`` for :class:`NativeRetroArch`: it writes its cfg
        outside any sandbox, so a ``/var/...`` value there is a real host path
        (odd, but the user's), and the existence checks downstream judge it like
        any other. Translating it would move the answer to a directory this
        RetroArch never touches.
        """
        return _Sandbox(self._machine, self._home, self._app_id)

    def _core_path_in(self, global_text: str | None, core_so: str) -> str | None:
        if global_text is None:
            return None
        cores_dir = _core_directory_in(self._sandbox(), global_text)
        if cores_dir is None:
            return None
        return os.path.join(cores_dir, core_so)

    def save_location(self, *, content_path: str | None = None, core_so: str | None = None) -> SavePlacement:
        """Where this RetroArch install keeps the save for *content_path* under *core_so*."""
        cfg = self._machine.read_text(self._cfg_path())
        health = self._health_from(cfg.status)
        return _retroarch_save_location(
            self._machine,
            _SaveQuery(
                sandbox=self._sandbox(),
                global_cfg_path=self._cfg_path(),
                global_text=cfg.text,
                cfg_label=RETROARCH_CFG,
                override_config_dir=os.path.join(self.root(), "config"),
                defaults=UPSTREAM_DEFAULTS,
                content_path=content_path,
                core_so=core_so,
                core_path_resolver=lambda so: self._core_path_in(cfg.text, so),
                arrangement="bare",
                arrangement_version=None,
                extra_caveats=(*health.issues, *arrangement_caveats(self.kind)),
            ),
        )

    def _read_firmware_context(self) -> FirmwareContext:
        # One read of the cfg answers both: its text is the context, its status
        # is the health of an installation whose cfg *is* the marker.
        cfg = self._machine.read_text(self._cfg_path())
        return _retroarch_firmware_context(
            sandbox=self._sandbox(),
            global_text=cfg.text,
            cfg_label=RETROARCH_CFG,
            retroarch_config_dir=self.root(),
            findings=self._health_from(cfg.status).issues,
        )


class StandaloneRetroArchFlatpak(_RetroArchInstall):
    """The ``org.libretro.RetroArch`` Flatpak install."""

    kind = "standalone_retroarch_flatpak"
    kinds = ("standalone_retroarch_flatpak",)
    _app_id = RETROARCH_FLATPAK_APP_ID

    def __init__(self, home: str, machine: Machine) -> None:
        super().__init__(home, machine, STANDALONE_FLATPAK_CFG_SUFFIX)


class NativeRetroArch(_RetroArchInstall):
    """A native ``~/.config/retroarch`` install."""

    kind = "native_retroarch"
    kinds = ("native_retroarch",)

    def __init__(self, home: str, machine: Machine) -> None:
        super().__init__(home, machine, NATIVE_CFG_SUFFIX)


@runtime_checkable
class Installation(Protocol):
    """The surface every installation handle offers — identity, health, placement, catalogue.

    A common protocol instead of a closed union (REVIEW M8): detection returns
    these, and consumers program against the surface. The catalogue question is
    on it because it is a question about an arrangement, not a RetroDECK
    feature: a handle that cannot answer it from a catalogue says *why* in a
    caveat, which is an answer, where an ``isinstance`` narrow left the caller
    to guess. Capabilities that are genuinely one arrangement's — RetroDECK's
    tree roots, its gamelist selections — still live on the concrete handles.
    """

    @property
    def kind(self) -> str: ...

    @property
    def kinds(self) -> tuple[str, ...]: ...

    def root(self) -> str: ...

    def health(self) -> Health: ...

    def save_location(
        self, *, content_path: str | None = None, core_so: str | None = None
    ) -> SavePlacement: ...

    def systems(self) -> SystemsAnswer: ...

    def emulators_for(self, system: str, *, content_path: str | None = None) -> CatalogueAnswer: ...

    def firmware_for_core(self, *, core_so: str, verify: bool = False) -> FirmwareAnswer: ...

    def firmware_for_system(self, *, system: str, verify: bool = False) -> FirmwareAnswer: ...

    def firmware_inventory(self, *, verify: bool = False) -> FirmwareAnswer: ...

    def identify_firmware(
        self, *, md5: str | None = None, sha1: str | None = None, size: int | None = None
    ) -> FirmwareIdentification: ...
