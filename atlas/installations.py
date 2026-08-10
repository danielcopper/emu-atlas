"""Installation handles — every question is asked *of an installation*.

Detection produces these; each answers ``savefile_location`` for its own flavor by
reading the configs that govern it — the same files, in the same order, with the
same fallbacks the emulator itself uses — through the injected machine seam.

- :class:`RetroDeck` — the RetroDECK Flatpak. ``retrodeck.json`` supplies the
  roots and the health check; the bundled RetroArch's cfg (plus its override
  chain) supplies the layout. The cfg is what RetroArch reads, so the cfg is the
  truth; ``retrodeck.json`` is context.
- :class:`EmuDeck` — an EmuDeck arrangement. Its truth is ``settings.sh``; its
  RetroArch is the bare ``org.libretro.RetroArch`` Flatpak, so the handle
  carries both descriptions (``kinds``) — the same RetroArch under two names.
- :class:`BareRetroArchFlatpak` / :class:`BareRetroArchNative` — bare
  RetroArch installs, differing in config location and default set.

Health is reported, not guessed around: a readable config whose root points into
an absent mount (unmounted SD card) yields ``root_missing``, never a
syntactically correct path handed out as if it were usable.
"""

from __future__ import annotations

import json
import os
from glob import escape as _glob_escape
from typing import Any, Callable, Mapping, Protocol, Sequence, cast, runtime_checkable

from dataclasses import dataclass, replace as _dc_replace

from atlas.content_path import content_file_name, content_system_dir, split_content_path
from atlas.core_info import parse_core_info
from atlas.esde import (
    KIND_LIBRETRO,
    EmulatorSpec,
    GamelistSelections,
    SystemDeclaration,
    expand_home_path,
    merge_layers,
    parse_es_settings,
    parse_es_systems,
    parse_gamelist,
    resolve_rom_path,
)
from atlas.evidence import arrangement_caveats
from atlas.firmware import (
    CAVEAT_CORE_DIR_UNRESOLVED,
    CAVEAT_CORE_ENUMERATION_INCOMPLETE,
    CAVEAT_CORE_INFO_UNREADABLE,
    CAVEAT_EMULATOR_CATALOGUE_SEALED,
    CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE,
    CAVEAT_EMULATOR_CATALOGUE_UNESTABLISHED,
    CAVEAT_EMULATOR_CATALOGUE_UNREADABLE,
    CAVEAT_FIRMWARE_ROOT_MISSING,
    CAVEAT_INFO_PATH_UNRESOLVED,
    Catalogue,
    CatalogueEntry,
    CoreDeclarations,
    FirmwareAnswer,
    FirmwareContext,
    FirmwareIdentification,
    SYSTEMS_WITHOUT_CATALOGUE_ID,
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
    ReadResult,
    ReadStatus,
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
    CAVEAT_CORE_SAVESTATES_UNSUPPORTED,
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
    CAVEAT_SYSTEM_DIRECTORY_CLEARED,
    CAVEAT_UNKNOWN_OPTION_VALUE,
    HOLE_CONTENT_DIR,
    ROOT_CONTENT_DIRECTORY,
    ROOT_SYSTEM_DIRECTORY,
    STATE_ROOT_CONTENT_DIRECTORY,
    TEMPLATE_ROM_STEM,
    FILE_SET_DECLARED,
    FILE_SET_OBSERVED,
    FILE_SET_UNKNOWN,
    UNKNOWN_FILE_SET,
    UNRESOLVED_STANDALONE,
    Caveat,
    FileSet,
    Granularity,
    RootKind,
    SavefilePlacement,
    SavestatePlacement,
    Unresolved,
    build_savefile_placement,
    build_savestate_placement,
    file_set_holes,
    needs_with_file_set,
)
from atlas.retroarch_cfg import (
    IGNORED_LINE_DROPPED,
    SAVEFILE_KEYS,
    SAVESTATE_KEYS,
    UPSTREAM_DEFAULTS,
    IgnoredSetting,
    LayoutDefaults,
    LayoutKeys,
    ParsedCfg,
    RejectedDirectory,
    RetroArchCfg,
    cfg_bool,
    chain_bool,
    chain_value,
    expand_home,
    is_app_relative,
    parse_cfg,
    parse_cfg_text,
    resolve_layout,
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


# What naming no core costs, per family. The code is one — a client branches on
# CAVEAT_NO_CORE either way — but the consequences genuinely differ, and a save
# route's sentence on a savestate answer would name a mechanism (rule cards)
# that cannot reach savestates at all.
NO_CORE_FOR_SAVES = (
    "no core given — per-core overrides and save-behaviour rule cards not checked: this answer "
    "assumes a standard core, and a card-carrying core (e.g. one rooted in system_directory, like "
    "Flycast) keeps its saves elsewhere entirely"
)
NO_CORE_FOR_STATES = (
    "no core given — per-core overrides not checked, sorting by core cannot be resolved, and "
    "whether this core declares savestate support at all was not read. No rule card can move a "
    "savestate, so unlike the save answer this one is not assuming a standard core"
)


def _identify_core(
    machine: Machine,
    *,
    core_so: str | None,
    core_path_resolver: Callable[[str], str | None],
    no_core_message: str = NO_CORE_FOR_SAVES,
) -> _CoreIdentity:
    """Load the named core and ask it its ``library_name`` — the same read RetroArch does."""
    if core_so is None:
        return _CoreIdentity(caveats=(Caveat(CAVEAT_NO_CORE, no_core_message),))
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
    """The configured root as the host sees it — and whether it can look.

    ``reachable`` is false for a sandbox path with no host location: RetroArch's
    own "is this an existing directory" test cannot be reproduced from here,
    so it is not performed rather than answered from a read that never applied.
    """

    layout: RetroArchCfg
    reachable: bool = True
    sources: tuple[str, ...] = ()
    caveats: tuple[Caveat, ...] = ()


def _host_save_dir(sandbox: _Sandbox, layout: RetroArchCfg) -> _SaveRoot:
    """The resolved root as the host reads it — the cfg may spell it sandbox-side.

    An untranslatable spelling stays as configured: it is where the emulator
    writes, in the only namespace that names it, and the caveat states that
    atlas cannot follow it there. Substituting a host directory would answer
    with a location this RetroArch never touches. An application-relative value
    is the same kind of answer — the value as configured, unreachable from here
    — except that not even the emulator-side spelling is a path yet.

    Which key is being translated comes off the layout itself, so the savefile
    and savestate roots take the same route and each names its own key.
    """
    key = layout.keys.directory
    configured = layout.directory
    if configured is None:
        return _SaveRoot(layout)
    if is_app_relative(configured):
        return _SaveRoot(layout, reachable=False, caveats=(_app_relative_caveat(key, configured),))
    resolved = sandbox.host(key, configured)
    if resolved.path == configured:
        return _SaveRoot(layout)
    if resolved.path is None:
        return _SaveRoot(layout, reachable=False, caveats=resolved.caveats)
    return _SaveRoot(
        _dc_replace(layout, directory=resolved.path),
        sources=(f'{key} = "{configured}"{resolved.note}',),
    )


def _save_dir_probe(machine: Machine, sandbox: _Sandbox, key: str) -> Callable[[str], bool]:
    """``path_is_directory`` for one root read of *key*, host-side.

    RetroArch runs this test on every value it reads (``configuration.c:6920``
    for the saves root, ``:6941`` for the savestate one) and keeps the value
    only when it passes. A value atlas cannot test — one that exists only
    inside the Flatpak sandbox, one relative to the running executable's
    directory — passes: the emulator's own test still decides it, and answering
    "not a directory" here would reject a root that is very likely there. That
    the answer rests on an unperformed read is stated by the translation's own
    caveat.
    """

    def is_directory(value: str) -> bool:
        if is_app_relative(value):
            return True
        host = sandbox.host(key, value).path
        return host is None or machine.path_kind(host) == KIND_DIRECTORY

    return is_directory


def _rejected_dir_caveats(
    machine: Machine,
    sandbox: _Sandbox,
    rejected: Sequence[RejectedDirectory],
    *,
    key: str,
    effective: str,
) -> tuple[Caveat, ...]:
    """The roots the configs state and RetroArch refuses, layer by layer.

    ``path_is_directory`` failed, so that read set nothing
    (``configuration.c:6920-6932``, and the savestate twin ``:6941-6959``) and
    some other read decides the root. Which one is not fixed: usually the
    refusal is the last word and what stands preceded it — after an override,
    the global cfg's root rather than the platform default — but a refused
    global cfg can be followed by an override that supplies a usable root, and
    then the standing root was set afterwards. The message says whichever it
    was; claiming the wrong one would teach a reader a causality RetroArch does
    not have. The layer is named either way, because that is what tells a caller
    which file to fix.

    ``configured`` stays the cfg's own spelling — that is the line to edit —
    but inside a Flatpak that spelling is not where atlas looked. Where the two
    differ, the message names the host path too, so "not an existing directory"
    can be checked against the place the check was actually made.

    The code is the same for both families and the data is unchanged, because
    the caveat rides on the answer whose root it is about: a client reading it
    off a savestate placement is not left guessing which directory was refused,
    and a second code would have split one fact in two. *key* names the setting
    in the message, which is the part that would otherwise be wrong.
    """
    caveats: list[Caveat] = []
    for entry in rejected:
        stands = (
            f"writes to {effective!r} instead, the root a later file in the chain set"
            if entry.superseded
            else f"keeps {effective!r}, the root that stood before this file"
        )
        host = sandbox.host(key, entry.value).path
        looked_at = (
            f" (atlas looked at its host spelling {host!r})"
            if host is not None and host != entry.value
            else ""
        )
        caveats.append(
            Caveat(
                CAVEAT_INVALID_SAVE_DIRECTORY,
                f"{entry.layer}: {key} {entry.value!r} is not an existing "
                f"directory{looked_at} — RetroArch refuses it and {stands} "
                "(configuration.c:6914-6960)",
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
                option_provenance=f"rule card '{card.key}': fixed behaviour (no governing option)",
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
            option_provenance=opt_source,
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


def _content_system_root(content: _Content, *, provenance: str) -> _SystemRoot:
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
        return _SystemRoot(content.system_dir, ROOT_CONTENT_DIRECTORY, sources=(provenance,))
    return _SystemRoot(
        "<content_dir>", ROOT_CONTENT_DIRECTORY, needs=(HOLE_CONTENT_DIR,), sources=(provenance,)
    )


# What an *absent* ``system_directory`` resolves to, for every route that asks.
# ``config_set_defaults`` seeds it before any config is read
# (``configuration.c:5746-5749``), and on desktop Linux the seed is ``system``
# under the RetroArch config tree (``platform_unix.c:2141-2143``). So the key
# being absent is not a gap: it names a directory, and both the card route and
# the firmware route resolve it to the same one — from here, once.
#
# One qualification, and it is a reachability one: the join is the *else*
# branch. ``LIBRETRO_SYSTEM_DIRECTORY`` in the environment wins when it is set
# (``platform_unix.c:2137-2140``), and atlas cannot read the environment the
# emulator will run with — it is not on disk, and the seam abstracts the
# machine, not the process. [V] unset on the reference machine, so the join is
# what applies there; an installation that exports it is [O] — the answer would
# name this directory while RetroArch used the exported one.
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
      ``system`` under the config tree (``platform_unix.c:2141-2143``, the same
      block that seeds the saves default this resolver answers with) unless
      ``LIBRETRO_SYSTEM_DIRECTORY`` is exported, which wins (``:2137-2140``) and
      which atlas cannot read off a disk — [V] unset on the reference machine,
      [O] anywhere it is set. So an unset key resolves; it is not a hole and
      never was one.
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
            provenance=f'{cfg_label} chain: systemfiles_in_content_dir = "true" — the core is handed the '
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
            provenance=f'{cfg_label} chain: system_directory = "{raw_system}" leaves the setting empty — '
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
) -> SavefilePlacement:
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
    return SavefilePlacement(
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
            state=FILE_SET_OBSERVED,
            files=observed,
            provenance=f"observed on the machine: {directory}",
            complete=complete,
        )
    if declared is not None and card is not None:
        return FileSet(
            state=FILE_SET_DECLARED,
            files=declared,
            provenance=f"declared by rule card '{card.key}' (none present yet)",
            complete=mode.complete if mode is not None else False,
        )
    return FileSet(
        state=FILE_SET_UNKNOWN,
        files=(),
        provenance=f"no files present at {directory} — file set not stated (never guessed)",
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
) -> SavefilePlacement:
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
    placement = build_savefile_placement(
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
                effective_root = layout.directory or platform_default_dir
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

    return SavefilePlacement(
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


@dataclass(frozen=True, slots=True)
class _Chain:
    """One family's governing sources, read once and resolved — what both routes share.

    Everything up to the point where the two questions part company: the
    content's coordinates, what the core said about itself, the override gates
    and the layers they admitted, the resolved layout, and the platform default
    its root falls back to. Frozen tuples rather than the caller's own lists,
    because a route that appended to a shared list would be editing the other
    route's evidence.
    """

    content: _Content
    core: _CoreIdentity
    gates: _OverrideGates
    layers: tuple[_CfgLayer, ...]
    layout: RetroArchCfg
    # The global cfg's settings from the one parse this chain made. Carried
    # rather than re-derived: the snapshot is the same either way, but a route
    # that parses the text again pays for a second walk of a file that runs to
    # thousands of lines on a real installation.
    global_values: Mapping[str, str]
    reachable: bool
    platform_default_dir: str
    retroarch_config_dir: str
    sources: tuple[str, ...]
    caveats: tuple[Caveat, ...]

    @property
    def effective_root(self) -> str:
        """The root that stands — the configured one, or the platform default."""
        return self.layout.directory or self.platform_default_dir


# The consequence of naming no core, per family — see NO_CORE_FOR_SAVES.
_NO_CORE_MESSAGES = {
    SAVEFILE_KEYS.label: NO_CORE_FOR_SAVES,
    SAVESTATE_KEYS.label: NO_CORE_FOR_STATES,
}


def _read_chain(machine: Machine, query: _SaveQuery, keys: LayoutKeys) -> _Chain:
    """Global cfg → override chain → resolved layout, for one family, all live.

    Reads the same four layers RetroArch reads (``configuration.c:7095``) and
    resolves ``library_name`` from the core binary when a core is named. Every
    degradation is a stated caveat, never a silent guess. The query carries the
    global cfg's content, read exactly once by the caller — one query derives
    every decision from one snapshot of each source (REVIEW M4).

    *keys* picks the family. Nothing below is written twice for savestates:
    RetroArch reads both quartets in one function and applies the same rule to
    each (``runloop.c:8752-8979``), so the port takes the same shape.
    """
    content = _content_coordinates(query.content_path)
    core = _identify_core(
        machine,
        core_so=query.core_so,
        core_path_resolver=query.core_path_resolver,
        no_core_message=_NO_CORE_MESSAGES[keys.label],
    )
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

    layout = resolve_layout(
        query.global_text,
        keys=keys,
        home=query.sandbox.home,
        cfg_label=query.cfg_label,
        defaults=query.defaults,
        overrides=overrides,
        is_directory=_save_dir_probe(machine, query.sandbox, keys.directory),
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

    # The RetroArch platform default root — 'saves' or 'states' under the config
    # tree (platform_unix.c:2133-2136) — is the effective root whenever no layer
    # left a usable value: the key unset everywhere, reset to "default", or every
    # value RetroArch read refused by its own directory test.
    platform_default_dir = os.path.join(retroarch_config_dir, keys.platform_default_subdir)
    caveats.extend(
        _rejected_dir_caveats(
            machine,
            query.sandbox,
            layout.rejected_directories,
            key=keys.directory,
            effective=layout.directory or platform_default_dir,
        )
    )

    return _Chain(
        content=content,
        core=core,
        gates=gates,
        layers=tuple(layers),
        layout=layout,
        global_values=parse_cfg_text(query.global_text) if query.global_text is not None else {},
        reachable=saves_root.reachable,
        platform_default_dir=platform_default_dir,
        retroarch_config_dir=retroarch_config_dir,
        sources=tuple(sources_extra),
        caveats=tuple(caveats),
    )


def _retroarch_savefile_location(machine: Machine, query: _SaveQuery) -> SavefilePlacement:
    """Where the savefile lands: the shared chain, then the per-core rule cards.

    The cards are what this route has and the savestate one does not — cores
    write their own save data and can put it elsewhere entirely, which is a
    thing no core can do to a savestate (:func:`_retroarch_savestate_location`).
    """
    chain = _read_chain(machine, query, SAVEFILE_KEYS)
    content, core = chain.content, chain.core
    layout, layers = chain.layout, list(chain.layers)
    retroarch_config_dir = chain.retroarch_config_dir
    platform_default_dir = chain.platform_default_dir
    sources_extra = list(chain.sources)
    caveats = list(chain.caveats)

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
            gates=chain.gates,
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
        reachable=chain.reachable,
        sources=tuple(sources_extra),
        caveats=tuple(caveats),
    )


# What RetroArch names a savestate, and therefore what atlas globs for. The
# base is the content's stem with ".state" appended (runloop.c:8942-8949,
# FILE_PATH_STATE_EXTENSION at file_path_special.h:44); slot N above zero
# appends the number and the auto slot appends ".auto" (runloop.c:8185-8207).
# With savestate_thumbnail_enable a ".png" is written beside each of them
# (task_save.c:1226-1230 -> task_screenshot.c:476-485), which is why the glob
# is a prefix match rather than the three exact names.
STATE_EXTENSION = ".state"


def _observed_state_set(
    machine: Machine, *, directory: str, rom_stem: str
) -> tuple[FileSet, tuple[Caveat, ...]]:
    """The savestates actually lying in *directory* for this ROM.

    The glob is ``<stem>.state*``, and the narrowness is the point. A savefile
    observation has to match ``<stem>.*`` because the extensions are the core's
    own and no source lists them; a savestate is written by RetroArch alone,
    under a name this module can state, so the pattern names exactly the
    artifacts of this question. What that buys is precision in the one place
    the save route has to hedge: the ROM's own directory. There ``<stem>.*``
    sweeps up further disc tracks, cover art and the archive itself and the
    answer says so (``content-dir-observation``), while ``<stem>.state*``
    matches none of them — so no such hedge is needed here.

    Neighbours that share the directory and are *not* savestates stay out by
    construction: the ``.srm`` of a machine whose two roots coincide, the
    ``.replay`` of a recorded input movie (``runloop.c:8923``, ``:8950`` put it
    in this very directory), the ``.cht`` cheat file. A replay is a different
    artifact answering a different question, and folding it in would make a
    state set that no state ever wrote.

    A directory that could not be listed yields *unknown* with a caveat naming
    it — never "no states present", which is the sentence an empty directory
    earns and would send a caller off to restore a state it already has.
    """
    pattern = os.path.join(_glob_escape(directory), _glob_escape(rom_stem) + STATE_EXTENSION + "*")
    listing = machine.glob(pattern)
    caveats = tuple(_unlistable_caveat(path) for path in listing.unreadable)
    if listing.unreadable:
        return UNKNOWN_FILE_SET, caveats
    if not listing.matches:
        return (
            FileSet(
                state=FILE_SET_UNKNOWN,
                files=(),
                provenance=(
                    f"no savestates present at {directory} — file set not stated (never guessed)"
                ),
            ),
            caveats,
        )
    return (
        FileSet(
            state=FILE_SET_OBSERVED,
            files=tuple(sorted(os.path.basename(match) for match in listing.matches)),
            provenance=f"observed on the machine: {directory}",
            # Never closed: which slots exist is a live setting away from
            # changing, and nothing on disk says how many were ever written.
            complete=False,
        ),
        caveats,
    )


def _savestate_support_caveats(
    machine: Machine,
    *,
    sandbox: _Sandbox,
    parsed: Mapping[str, str],
    core_so: str,
    layers: Sequence[_CfgLayer],
) -> tuple[Caveat, ...]:
    """What the core's own ``.info`` says about whether it can make savestates.

    ``savestate = "false"`` sets the support level to
    ``CORE_INFO_SAVESTATE_DISABLED`` (``core_info.c:1841-1860``), and RetroArch
    checks that level before it offers a state at all
    (``core_info.c:2899-2937``). 68 of the 292 ``.info`` files a stock RetroDECK
    ships declare it, so a state placement that said only "here is the
    directory" would be technically true and read as a promise for a quarter of
    the matrix.

    It is stated as a caveat rather than as a refusal because the declaration
    does not bind absolutely, and both escapes are visible from here: the cfg
    key ``core_info_savestate_bypass`` waves the check through entirely
    (``:2904-2905``), and for the BASIC level a running core reporting a nonzero
    ``retro_serialize_size()`` overrides stale metadata (``:2926-2929``) — which
    atlas cannot evaluate, because it is a fact about a running core and not
    about anything on disk. So the message names them and the directory answer
    stands.

    Silence here means the declaration says states work, or the bypass is on.
    A ``.info`` that could not be read is not silence: it goes out as
    ``core-info-unreadable``, the same code the firmware route states for the
    same file, because "atlas could not look" and "the core is fine" are the
    two things this grammar may never collapse.
    """
    bypass, ignored = chain_bool(layers, "core_info_savestate_bypass", default=False)
    caveats = _ignored_caveats(ignored)
    if bypass:
        # The check the declaration feeds is skipped wholesale, so the
        # declaration says nothing about this machine's behaviour.
        return caveats
    info_dir, dir_caveats = _cfg_directory(sandbox, parsed, "libretro_info_path")
    if info_dir is None:
        return (*caveats, *dir_caveats, _info_path_unresolved_caveat())
    info_path = os.path.join(info_dir, os.path.basename(core_so).removesuffix(".so") + ".info")
    read = machine.read_text(info_path)
    if read.text is None:
        return (*caveats, _core_info_unreadable_caveat(core_so, read.status))
    declared = parse_core_info(read.text).get("savestate")
    # Only an explicit, well-formed false lowers the level. An absent key and a
    # spelling config_get_bool refuses (one shipped .info says
    # savestate = "serialized") both leave the block unentered and the
    # DETERMINISTIC default standing (core_info.c:1836-1861).
    if declared is None or cfg_bool(declared) is not False:
        return caveats
    return (
        *caveats,
        Caveat(
            CAVEAT_CORE_SAVESTATES_UNSUPPORTED,
            f"{os.path.basename(core_so)} declares savestate = \"{declared}\" in its .info, so RetroArch "
            "treats it as having no savestate support (core_info.c:1841-1860, checked at :2899-2937) — "
            "the directory below is where a state would go, not a promise that one can be made. Two "
            "things still override the declaration: core_info_savestate_bypass = \"true\" in the cfg, and "
            "a running core reporting a nonzero retro_serialize_size(), which atlas cannot ask from here",
            {"core_so": os.path.basename(core_so), "declared": declared},
        ),
    )


def _info_path_unresolved_caveat() -> Caveat:
    """``libretro_info_path`` names no readable directory, so no ``.info`` was read."""
    return Caveat(
        CAVEAT_INFO_PATH_UNRESOLVED,
        "libretro_info_path does not resolve to a readable directory on this machine — whether this "
        "core declares savestate support could not be established, so the absence of a warning below "
        "is not a statement that states work",
    )


def _core_info_unreadable_caveat(core_so: str, status: ReadStatus) -> Caveat:
    """The core's ``.info`` is there in name only — the same gap the firmware route states."""
    return Caveat(
        CAVEAT_CORE_INFO_UNREADABLE,
        f"{os.path.basename(core_so)}'s .info could not be read ({status}) — whether this core declares "
        "savestate support is unknown, so no warning below is not 'states work'",
        {"core_so": os.path.basename(core_so), "status": status},
    )


def _retroarch_savestate_location(machine: Machine, query: _SaveQuery) -> SavestatePlacement:
    """Where the savestate lands: the shared chain, and nothing per-core after it.

    The savefile route continues into rule cards here, because a core writes
    its own save data and may put it anywhere. A savestate has no such branch:
    the libretro API hands a core no savestate directory (``libretro.h``), so
    RetroArch serializes and writes the file itself and the four cfg keys are
    the whole story. What the core does get to say is whether it can be
    serialized at all, which is a caveat and not a placement
    (:func:`_savestate_support_caveats`).
    """
    chain = _read_chain(machine, query, SAVESTATE_KEYS)
    content = chain.content
    caveats = list(chain.caveats)

    if query.core_so is not None:
        # Asked of the .info whether or not the binary itself loaded: the two
        # are separate reads of separate files, and a core that would not load
        # is exactly one whose declaration is still worth reporting.
        caveats.extend(
            _savestate_support_caveats(
                machine,
                sandbox=query.sandbox,
                parsed=chain.global_values,
                core_so=query.core_so,
                layers=chain.layers,
            )
        )

    placement = build_savestate_placement(
        layout=chain.layout,
        platform_default_dir=chain.platform_default_dir,
        content_dir_path=content.dir_path,
        content_dir_name=content.dir_name,
        library_name=chain.core.library_name,
        extra_sources=chain.sources,
    )

    final_dir = placement.dir
    fallback_dir: str | None = None
    physical_dir: str | None = None
    file_set = UNKNOWN_FILE_SET
    if not placement.needs and chain.reachable:
        effective_root = (
            content.dir_path
            if placement.root_kind == STATE_ROOT_CONTENT_DIRECTORY
            else chain.effective_root
        )
        final_dir, fallback_dir, sorted_caveats = _sorted_dir_fallback(
            machine, intended_dir=final_dir, effective_root=effective_root
        )
        caveats.extend(sorted_caveats)
        if content.rom_stem is not None:
            file_set, set_caveats = _observed_state_set(
                machine, directory=final_dir, rom_stem=content.rom_stem
            )
            caveats.extend(set_caveats)
        physical_dir, link_caveats = _link_view(machine, final_dir)
        caveats.extend(link_caveats)

    return SavestatePlacement(
        dir=final_dir,
        root_kind=placement.root_kind,
        needs=placement.needs,
        file_set=file_set,
        sources=placement.sources,
        caveats=tuple(caveats),
        fallback_dir=fallback_dir,
        physical_dir=physical_dir,
    )


def _cfg_directory(
    sandbox: _Sandbox, parsed: Mapping[str, str], key: str
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
    arrangement_version: str | None,
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
    # The dropped lines are read here, not only the values: once an absent key
    # resolves silently to the platform default, a line the parser refused
    # looks exactly like a key nobody wrote — and the user did write it. The
    # card route states the same fact for the same reason.
    read = parse_cfg(global_text) if global_text is not None else ParsedCfg({})
    parsed = read.values
    # The installation's own health leads: whether the arrangement is broken is
    # the most general thing about any answer, so it stands before what this
    # particular read could not resolve. The caller derives the findings from
    # the same reads it passed *global_text* out of.
    caveats: list[Caveat] = list(findings)
    sources: list[str] = []

    raw_system = parsed.get("system_directory")
    configured_system = sandbox.cfg_path("system_directory", raw_system) if raw_system is not None else None
    root = configured_system.path if configured_system is not None else None
    caveats.extend(
        _ignored_caveats(
            tuple(
                IgnoredSetting(IGNORED_LINE_DROPPED, cfg_label, line.key, line.line)
                for line in read.dropped
                if line.key == "system_directory"
            )
        )
    )
    if raw_system is None:
        # Absent is not unset-and-unknown: RetroArch seeded the platform default
        # before it read a line of config, so this route resolves it exactly as
        # the card route does and scans there. A line the parser refused lands
        # here too, and is stated above rather than resolving in silence.
        root = _platform_system_dir(retroarch_config_dir)
        sources.append(PLATFORM_SYSTEM_DIR_SOURCE)
        if machine.path_kind(root) != KIND_DIRECTORY:
            caveats.append(_firmware_root_missing(root))
    elif configured_system is None:
        # Set to blank or the literal "default": the setting is empty, and what
        # the core is handed then depends on the run, not on the config.
        caveats.append(
            Caveat(
                CAVEAT_SYSTEM_DIRECTORY_CLEARED,
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
        arrangement_version=arrangement_version,
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

        The version the evidence is weighed against comes off the context for
        the same reason — it is what the handle's own read saw, not a fresh
        one.
        """
        return _dc_replace(
            context,
            caveats=(
                *context.caveats,
                *arrangement_caveats(self.kind, observed_version=context.arrangement_version),
            ),
        )

    def _firmware_context(self) -> FirmwareContext:
        """The handle's live read, stated — the context all four questions answer from."""
        return self._stated(self._read_firmware_context())

    def firmware_for_core(self, core_so: str, *, verify: bool = False) -> FirmwareAnswer:
        """Does *core_so* need firmware, where does each file go, and is it there?"""
        return _resolve_for_core(self._machine, self._firmware_context(), core_so=core_so, verify=verify)

    def firmware_for_system(self, system: str, *, verify: bool = False) -> FirmwareAnswer:
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
#
# ``roms_path`` left when the ROM directory moved to ES-DE's own ``ROMDirectory``:
# nothing here reads it any more, so checking it would be atlas reporting on a
# key it does not use — the same thing the scoping rule below refuses to do for
# every other key RetroDECK writes.
_MARKER_PATH_KEYS = ("rd_home_path", "saves_path", "bios_path")


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


def _marker_version(config: Mapping[str, Any]) -> str | None:
    """The version a ``retrodeck.json`` states about itself — ``None`` for none.

    One definition of "this marker names a version", used by both checks that
    ask: the per-card version comparison and the arrangement-level one. Two
    readings of the same key could disagree about whether a machine stated
    anything, and then one caveat would report drift where the other reported
    silence.

    Empty is *unstated*, because that is RetroDECK's own spelling for it: the
    shipped default config carries ``"version": ""`` and the first run fills it
    in (``libexec/global.sh``, ``conf_write`` in ``libexec/other_functions.sh``,
    RetroDECK 0.10.9b). A non-string is neither — :meth:`RetroDeck._read_marker`
    states that as a health finding, and nothing is compared against it.
    """
    value: object = config.get("version")
    return value if isinstance(value, str) and value else None


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

    Narrow on purpose: an entry asks its installation for placements and
    nothing else, so the type it is bound to is those methods rather than a
    concrete handle. It used to be ``RetroDeck``, which made every entry — and
    so the catalogue answer itself — RetroDECK's to hand out.
    """

    def entry_savefile_location(
        self,
        spec: EmulatorSpec,
        entry_caveats: tuple[Caveat, ...] = (),
        *,
        content_path: str | None = None,
    ) -> SavefilePlacement | Unresolved: ...

    def entry_savestate_location(
        self,
        spec: EmulatorSpec,
        entry_caveats: tuple[Caveat, ...] = (),
        *,
        content_path: str | None = None,
    ) -> SavestatePlacement | Unresolved: ...


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


# Flatpak's per-app overrides are plain INI: [Section] headers and KEY=VALUE
# lines. Only the [Environment] section is read here, and only to find out
# whether the app's config home was moved out from under the path atlas
# resolved — a full override parser is not the job.
def _environment_overrides(text: str) -> dict[str, str]:
    """The ``[Environment]`` section of a Flatpak overrides file, as key -> value."""
    settings: dict[str, str] = {}
    in_environment = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_environment = line[1:-1].strip() == "Environment"
            continue
        if in_environment and "=" in line:
            key, _, value = line.partition("=")
            settings[key.strip()] = value.strip()
    return settings


# The environment variables that decide where a Flatpak app's config home is,
# and therefore where the frontend's --home points. Flatpak sets
# XDG_CONFIG_HOME to the per-app config directory; HOME is what the rest is
# expressed relative to. Either one redefined in an overrides file moves the
# tree atlas resolved against, so atlas stops resolving rather than answering
# about a directory the frontend is not using. Documented Flatpak semantics
# rather than a reading of this machine: [D].
_CONFIG_HOME_ENV_KEYS = ("XDG_CONFIG_HOME", "HOME")


# The ways a READ catalogue still yields no ROM directory. The first two are
# facts about the machine — the catalogue declares nothing, or the frontend's
# own setting is not a path anything can be resolved against — and a client acts
# on them by fixing the machine. The last two are statements about atlas: the
# file that decides the directory could not be read, or the environment that
# decides where that file's defaults land was moved somewhere atlas cannot
# follow. Four facts, four codes: a client branches on the code, and prose is
# the one thing it cannot branch on.
CAVEAT_ROM_PATH_UNDECLARED = "rom-path-undeclared"
CAVEAT_ROM_PATH_UNRESOLVED = "rom-path-unresolved"
CAVEAT_FRONTEND_SETTINGS_UNREADABLE = "frontend-settings-unreadable"
CAVEAT_CONFIG_HOME_RELOCATED = "config-home-relocated"

# Why the settings could not be read, where the read itself succeeded and the
# parse did not. Alongside the seam's own read statuses in the caveat's data,
# because to a client they answer the same question — and it is not one of
# them: the bytes arrived.
_SETTINGS_UNPARSEABLE = "unparseable"


# The catalogue file's own name, spelled once: ES-DE uses it for the bundled
# layer, the custom_systems overlay and the resource-override shadow alike,
# and the three probes below must never drift apart on it.
_ES_SYSTEMS_XML = "es_systems.xml"


def _catalogue_unread_caveat(system: str | None = None) -> tuple[Caveat, ...]:
    """The caveat for a catalogue that could not be read — the same fact as the firmware route's.

    One fact, one code: ``firmware_for_system`` already states this exact
    thing when its catalogue comes back unread, and an answer that is empty
    because nobody could look is the same answer whichever door it left by.
    Module-level because it is the same fact on every catalogued arrangement —
    RetroDECK's bundled layer and EmuDeck's on-disk one degrade through the
    one builder, so the two can never spell the claim apart.
    """
    return (
        Caveat(
            CAVEAT_EMULATOR_CATALOGUE_UNREADABLE,
            "the frontend's emulator catalogue could not be read, so which emulators this "
            "installation knows is unknown — this answer is empty because atlas could not look, "
            "not because nothing is there",
            {"system": system} if system is not None else {},
        ),
    )


def _rom_path_undeclared_caveat(
    system: str, declaration: SystemDeclaration | None
) -> tuple[Caveat, ...]:
    """A read catalogue that names no directory for this system.

    The two ways it happens — no such system, or a system declared without
    a ``<path>`` — are one code and one thing to do about it, so they
    differ in the message and not in the data. What a client acts on is
    that this catalogue states no directory.
    """
    subject = (
        f"declares no system {system!r}"
        if declaration is None
        else f"declares {system!r} without a <path>"
    )
    return (
        Caveat(
            CAVEAT_ROM_PATH_UNDECLARED,
            f"the frontend's catalogue was read and {subject}, so where its ROMs live is not "
            "something this machine states — the answer is empty because the declaration is, "
            "not because atlas could not look",
            {"system": system},
        ),
    )


def _rom_path_unresolved_caveat(system: str, declared: str, rom_directory: str) -> tuple[Caveat, ...]:
    """A configured ROM directory that is not a path a ``%ROMPATH%`` can be resolved against.

    One fact and one code, now that the other reasons have theirs: the
    frontend's setting holds something that is not absolute even after the
    frontend's own ``~`` expansion (``atlas.esde.expand_home_path``) — a
    relative path, whose base is the ES-DE process's working directory, or a
    ``%ESPATH%`` spelling, whose base is the frontend's binary directory
    (``FileData.cpp:300-302``, v3.4.1) — and neither base is something atlas
    has established. A fact about the machine, and the thing to do about it
    is to fix the setting.

    The declaration travels in the data: it is the fact atlas *did*
    establish, and a caller who knows their own setup can finish the
    substitution atlas refused to guess at. ``configured`` is the setting's
    own text, unexpanded — the value whose remedy is an edit.
    """
    return (
        Caveat(
            CAVEAT_ROM_PATH_UNRESOLVED,
            f"the catalogue declares {system!r} at {declared!r}, and ES-DE's ROMDirectory is "
            f"{rom_directory!r}, which is not an absolute path — atlas states no directory rather "
            "than guessing one, because a ROM directory guessed wrong is a real directory the "
            "caller would go looking in",
            {"system": system, "declared": declared, "configured": rom_directory},
        ),
    )


def _settings_unreadable_caveat(system: str, path: str, status: str) -> tuple[Caveat, ...]:
    """The frontend's settings are there and atlas could not read them.

    A statement about atlas, not about the machine, and the reason it is
    not the unset case: the frontend reads this file, so the ROM directory
    it names is the one in force — and falling back on the default here
    would state a directory belonging to a configuration nobody established.
    """
    return (
        Caveat(
            CAVEAT_FRONTEND_SETTINGS_UNREADABLE,
            f"the frontend's settings at {path} exist and could not be read ({status}), so what "
            f"they say about {system!r}'s ROM directory is unknown — atlas states none rather "
            "than the default, which only applies to a file that sets nothing",
            {"system": system, "path": path, "status": status},
        ),
    )


def _per_game_override_caveat(override_label: str, spec: EmulatorSpec) -> Caveat:
    """This game's gamelist entry selects a different emulator than *spec*.

    One statement on both arrangements' entry routes: which emulator ES-DE
    would actually launch decides the placement, and an entry that would not
    launch this game says so instead of answering as if it would.
    """
    return Caveat(
        CAVEAT_PER_GAME_OVERRIDE,
        f"this game carries a per-game altemulator override selecting "
        f"{override_label!r} — ES-DE would launch that emulator, not "
        f"{spec.label!r}; ask emulators_for with content_path",
        {"label": override_label},
    )


def _entries_from(
    host: "_CatalogueHost",
    specs: tuple[EmulatorSpec, ...],
    selections: GamelistSelections,
    *,
    system_roms_dir: str | None,
    content_path: str | None,
) -> tuple[EmulatorEntry, ...]:
    """Apply ES-DE's selection hierarchy to one already-read catalogue snapshot.

    Module-level and host-parameterized: the hierarchy is ES-DE's, not one
    arrangement's, so the catalogue answer and the firmware route of every
    ES-DE-driven handle assemble their entries here instead of re-reading the
    sources — or worse, growing a second copy of the promotion rule.

    ``system_roms_dir`` is ``None`` where there is no anchor to match
    against — either nothing named content, or the directory could not be
    resolved. Per-game matching is skipped either way; the caller that
    asked for it is the one holding the caveat that says why.
    """
    chosen_label: str | None = None
    chosen_source: str | None = None
    if content_path is not None and system_roms_dir is not None:
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
    return tuple(EmulatorEntry(host, spec, entry_caveats) for spec in specs)


def _firmware_catalogue_entries(
    host: "_CatalogueHost",
    by_system: Mapping[str, SystemDeclaration],
    system: str,
    selections: GamelistSelections,
) -> tuple[CatalogueEntry, ...]:
    """The catalogue's emulator list for *system*, shaped for the firmware seam.

    Module-level for the same reason :func:`_entries_from` is: both ES-DE-driven
    handles hand their firmware route the same projection of the same assembly,
    and a second copy is how the two would drift apart. No content is named on
    the firmware route, so no per-game entry can match and the anchor is never
    consulted — the enumeration and its gamelist promotion are all that cross
    the seam.
    """
    entries = _entries_from(
        host,
        _declared_entries(by_system, system),
        selections,
        system_roms_dir=None,
        content_path=None,
    )
    return tuple(
        CatalogueEntry(label=entry.label, kind=entry.kind, core_so=entry.core_so)
        for entry in entries
    )


@dataclass(frozen=True, slots=True)
class _RomRoot:
    """What ES-DE substitutes for ``%ROMPATH%``, or which way it could not be established.

    ``directory`` is set exactly when the three refusal fields are not: an
    absolute root the frontend would really use, whether that is the configured
    value or ES-DE's own default for a file that sets nothing.

    The refusals stay apart rather than collapsing into one "no root", because
    each becomes a different caveat code — and they carry only what those
    messages need, not the messages themselves: two of the three name the
    system's declared ``<path>``, which the root does not know and has no
    business knowing.
    """

    directory: str | None = None
    unreadable: str | None = None
    relocated: tuple[str, str] | None = None
    not_absolute: str | None = None
    sources: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _RomDirectory:
    """One system's ROM directory as ES-DE resolves it, and what was read to say so.

    ``sources`` is not the same question as whether a directory came out:
    settings that were read and set nothing still resolve — through the
    frontend's own default — while settings nobody could read resolve nothing
    and are no source at all.
    """

    directory: str | None = None
    sources: tuple[str, ...] = ()
    caveats: tuple[Caveat, ...] = ()


# The resolution a query skips because it has nothing to anchor: no content
# path was named, so no per-game entry can match and the directory is never
# consulted. Distinct from a resolution that refused — that one carries the
# caveat saying so.
_NO_ANCHOR_NEEDED = _RomDirectory()


@dataclass(frozen=True, slots=True)
class RomPlacement:
    """Where one system's ROMs live, and which files the frontend launches.

    Both come from the catalogue's own ``<system>`` declaration, so both are
    facts about this machine rather than a table: ``dir`` is the ``<path>``
    with ES-DE's ``%ROMPATH%`` substituted from the setting ES-DE substitutes
    it from, and ``extensions`` is the ``<extension>`` list as declared.

    ``dir`` is ``None`` wherever atlas could not resolve one, and never a
    partial path: a caller acts on this by looking in a directory, so a
    half-resolved string would send it somewhere real and wrong. Which kind of
    ``None`` it is — an arrangement with no catalogue, one whose catalogue atlas
    has not located, one whose catalogue could not be read, one whose readable
    layers declare nothing while the rest is sealed away, a system the
    catalogue declares no path for, a setting that is not a path, settings
    nobody could read, or a relocated config home — is a caveat, exactly as an
    empty :class:`CatalogueAnswer` is.

    ``extensions`` is the frontend's declaration, not a filter atlas applies:
    the tokens are verbatim, both cases are listed where the file lists both,
    and what to do with them is the caller's business.

    ``physical_dir`` is the fully link-resolved backing directory when ``dir``
    reaches its files through symlinks, and ``None`` otherwise — the same pair
    and the same convention :class:`~atlas.placement.SavefilePlacement` answers
    with, because it is the same question: a distribution that wires a tree
    into place with symlinks leaves the frontend-side path and the physical
    path as two truthful answers, and a client copying files may need either.
    A traversal that ends nowhere resolves to ``None`` and says so as a
    ``dead-symlink`` or ``symlink-loop`` caveat.
    """

    dir: str | None = None
    physical_dir: str | None = None
    extensions: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    caveats: tuple[Caveat, ...] = ()


def _declared_entries(
    by_system: Mapping[str, SystemDeclaration], system: str
) -> tuple[EmulatorSpec, ...]:
    """The launch entries a read catalogue declares for *system* — none if it declares no such system."""
    declaration = by_system.get(system)
    return () if declaration is None else declaration.entries


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
    def provenance(self) -> str:
        """Which catalogue layer declared this entry — prose, for debugging."""
        return self._spec.provenance

    @property
    def selection(self) -> str | None:
        """Provenance of a user promotion, or ``None`` for declared order."""
        return self._spec.selection

    @property
    def caveats(self) -> tuple[Caveat, ...]:
        """Stated catalogue-level degradations (e.g. unchecked per-game overrides)."""
        return self._caveats

    def savefile_location(self, *, content_path: str | None = None) -> SavefilePlacement | Unresolved:
        """Where this emulator keeps the save — core filled in from the catalogue.

        Catalogue-level degradations stay attached to the derived answer
        (REVIEW M9). Standalone entries are outside the resolver's coverage
        until the standalone block lands — that is a domain outcome
        (:class:`~atlas.placement.Unresolved`), never a guess and never an
        exception (REVIEW M8).
        """
        if self._spec.kind != KIND_LIBRETRO:
            return self._standalone()
        return self._installation.entry_savefile_location(
            self._spec, self._caveats, content_path=content_path
        )

    def savestate_location(self, *, content_path: str | None = None) -> SavestatePlacement | Unresolved:
        """Where this emulator keeps the savestates — core filled in from the catalogue.

        The savefile route's twin, and it refuses on the same entries: a
        standalone emulator's states are outside the resolver's coverage for
        exactly the reason its saves are — nothing here reads that emulator's
        own config.
        """
        if self._spec.kind != KIND_LIBRETRO:
            return self._standalone()
        return self._installation.entry_savestate_location(
            self._spec, self._caveats, content_path=content_path
        )

    def _standalone(self) -> Unresolved:
        """The outcome both placement questions give for a non-libretro entry."""
        return Unresolved(
            UNRESOLVED_STANDALONE,
            f"standalone emulator {self._spec.label!r} ({self._spec.system}) is not resolvable yet — "
            "standalone emulators are the next big roadmap block (ROADMAP.md)",
            {"label": self._spec.label, "system": self._spec.system},
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"EmulatorEntry(system={self._spec.system!r}, label={self._spec.label!r}, "
            f"kind={self._spec.kind!r}, core_so={self._spec.core_so!r})"
        )


# One caveat-replacement helper per answer type, monomorphic on purpose: a
# generic one would hand Sonar's type engine the very TypeVar it cannot carry
# (it types dataclasses.replace as bare DataclassInstance), and the concrete
# signatures are what keep the declared type standing at every call site. Each
# helper holds the one cast: Sonar's engine does not carry replace's generic;
# basedpyright resolves it — the cast is for the weaker engine, made true by
# the signature's contract with its callers.


def _systems_with_caveats(answer: SystemsAnswer, caveats: tuple[Caveat, ...]) -> SystemsAnswer:
    """*answer* with *caveats* as its caveat list — ``dataclasses.replace`` behind a concrete signature."""
    return cast(SystemsAnswer, _dc_replace(answer, caveats=caveats))


def _rom_placement_with_caveats(answer: RomPlacement, caveats: tuple[Caveat, ...]) -> RomPlacement:
    """*answer* with *caveats* as its caveat list — ``dataclasses.replace`` behind a concrete signature."""
    return cast(RomPlacement, _dc_replace(answer, caveats=caveats))


def _catalogue_with_caveats(answer: CatalogueAnswer, caveats: tuple[Caveat, ...]) -> CatalogueAnswer:
    """*answer* with *caveats* as its caveat list — ``dataclasses.replace`` behind a concrete signature."""
    return cast(CatalogueAnswer, _dc_replace(answer, caveats=caveats))


def _firmware_with_caveats(answer: FirmwareAnswer, caveats: tuple[Caveat, ...]) -> FirmwareAnswer:
    """*answer* with *caveats* as its caveat list — ``dataclasses.replace`` behind a concrete signature."""
    return cast(FirmwareAnswer, _dc_replace(answer, caveats=caveats))


class _CatalogueQueries:
    """The catalogue entry points, answered by every handle — including with a refusal.

    "Which emulator would launch this?" is a question about an arrangement, not
    a RetroDECK feature, so every handle answers it. RetroDECK answers it from
    its ES-DE's full catalogue; EmuDeck answers it from its ES-DE's on-disk
    layers where an ES-DE is present — stated as incomplete, because the
    bundled layer is sealed inside the AppImage. The rest state why they
    cannot, and those reasons are different claims that must not collapse into
    one empty tuple:

    - a bare RetroArch has no frontend catalogue at all — a fact about the
      arrangement, and a settled one;
    - an EmuDeck arrangement with no ES-DE on disk may still have a catalogue
      (Pegasus, Steam ROM Manager), and atlas has not established where those
      keep one. That is a statement about atlas, not about the machine, and a
      client that read it as "no emulators" would be told something nobody
      checked.
    """

    kind: str

    def _catalogue_absence(self) -> Caveat:
        raise NotImplementedError  # pragma: no cover - every handle supplies one

    def systems(self) -> SystemsAnswer:
        """Every system the frontend catalogue declares, sorted."""
        answer, version = self._systems_answer()
        return _systems_with_caveats(
            answer, (*answer.caveats, *arrangement_caveats(self.kind, observed_version=version))
        )

    def rom_location(self, system: str) -> RomPlacement:
        """Where *system*'s ROMs live, and which extensions the frontend launches.

        Both facts are declared per system in the frontend's own catalogue and
        are read from it live — a client that recomputes either from a table of
        its own is answering from somewhere the machine cannot contradict.

        No directory is five different facts, and the codes tell them apart the
        way the catalogue question's do: the arrangement ships no catalogue, it
        has one atlas has not established, its catalogue could not be read, the
        readable part of it declares nothing while the rest is sealed away, or
        the catalogue was read and this is what it says — no such system, or a
        ``%ROMPATH%`` nothing here resolves.
        """
        answer, version = self._rom_location_answer(system)
        return _rom_placement_with_caveats(
            answer, (*answer.caveats, *arrangement_caveats(self.kind, observed_version=version))
        )

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

        No entries is five different facts, and the four
        ``emulator-catalogue-*`` codes tell them apart. None of the four means
        the catalogue was read and the frontend knows no emulator for this
        system — the only one of the five that is a statement about the
        machine. Three say why nobody could answer from a catalogue at all:
        the arrangement ships none, atlas has not established where it keeps
        one, or the one it has could not be read. The fourth (``sealed``) says
        the answer came from the readable part of a catalogue whose rest atlas
        cannot open — it is the one that may also accompany real entries, and
        an empty list under it means "nothing readable declares this", never
        "the frontend knows none".

        The test is those codes, not an empty ``caveats``: a broken
        installation states its health findings on this answer as on every
        other, so an empty caveat list is not what "read, and it declares
        nothing" looks like there.
        """
        answer, version = self._catalogue_answer(system, content_path=content_path)
        return _catalogue_with_caveats(
            answer, (*answer.caveats, *arrangement_caveats(self.kind, observed_version=version))
        )

    # The two public questions above are the whole catalogue surface, and a
    # handle overrides the two below instead: what it *answers* is its own,
    # how the answer states its arrangement's evidence is not. Splitting them
    # is what makes "every answer says what atlas has established about this
    # arrangement" a property of the surface rather than of remembering.
    #
    # Which is why the version the arrangement states about itself travels back
    # with the answer: the evidence above is weighed against it, and the handle
    # already read it — asking for it here would read the marker a second time
    # inside one query, and the two reads could disagree.

    def health(self) -> Health:
        raise NotImplementedError  # pragma: no cover - every handle answers it

    def _systems_answer(self) -> tuple[SystemsAnswer, str | None]:
        # A handle with no catalogue reads nothing to refuse, so its health is
        # this query's own single read of the sources — no snapshot to reuse
        # and none read twice. It states no version either: an arrangement
        # nobody verified has no pin to drift from.
        return SystemsAnswer(caveats=(*self.health().issues, self._catalogue_absence())), None

    def _catalogue_answer(
        self, system: str, *, content_path: str | None = None
    ) -> tuple[CatalogueAnswer, str | None]:
        return CatalogueAnswer(caveats=(*self.health().issues, self._catalogue_absence())), None

    def _rom_location_answer(self, system: str) -> tuple[RomPlacement, str | None]:
        # Same refusal as the two above, for the same reason: where a system's
        # ROMs live is declared in the catalogue this arrangement does not have,
        # so the honest answer names the absence rather than a directory.
        return RomPlacement(caveats=(*self.health().issues, self._catalogue_absence())), None


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

        A defect is scoped to what it actually costs. A ``paths`` value atlas
        cannot read as a path takes the whole snapshot down, because every root
        below is resolved from that section; a ``version`` that is not a string
        costs the version comparison and nothing else, so the config survives
        it and only the comparison falls away. Both are ``marker-invalid``: the
        finding names the key, and the marker is editable either way.
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
        if "version" in data and not isinstance(data["version"], str):
            return data, (
                Caveat(
                    HEALTH_ISSUE_MARKER_INVALID,
                    f"marker {path}: version is {_json_type_name(data['version'])}, not a version "
                    "string — atlas reads it to compare this installation against the version its "
                    "knowledge was verified on, so that comparison is not made",
                    {"path": path, "key": "version"},
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

    def roms_dir(self) -> str | None:
        """The ROM root the frontend substitutes for ``%ROMPATH%`` — or ``None``.

        The **root**, not a system's directory: a system's own directory is
        ``rom_location(system).dir``, which is this plus whatever ``<path>`` the
        catalogue declares for it — usually the system name, and not reliably
        so, which is why the two are different questions.

        Read from ES-DE's ``ROMDirectory`` rather than ``retrodeck.json``'s
        ``roms_path``. The two agree on a stock installation and are wired one
        way — RetroDECK seds its own value into ES-DE's setting and never reads
        it back — so where a user has moved them apart, only this one is what
        the frontend launches from.

        ``None`` is a refusal, never "no ROMs here", and it is the same refusal
        :meth:`rom_location` states — for three of its reasons, the ones that
        belong to the root: ES-DE's settings exist and could not be read, a
        Flatpak override moved the config home out from under them, or the
        configured value is not an absolute path even after the frontend's own
        ``~`` expansion. A bare string cannot carry which, and raising is not
        this domain's grammar, so a caller who needs the reason asks
        ``rom_location(system)`` and reads its caveats.
        """
        return self._rom_root().directory

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

    def _read_catalogue(self, root: str) -> tuple[dict[str, SystemDeclaration], bool]:
        """The merged ES-DE catalogue, and whether the bundled layer could be read.

        The second value is not a detail: an empty catalogue because the shipped
        ``es_systems.xml`` was unreadable says nothing about which emulators
        exist, while an empty *lookup* in a catalogue that was read says the
        frontend knows none for that system. The custom overlay is genuinely
        optional, so only the bundled layer decides.
        """
        bundled: dict[str, SystemDeclaration] = {}
        read = False
        bundled_path = self._sandbox().bundled(self._ESDE_BUNDLED_SANDBOX)
        if bundled_path is not None:
            text = self._machine.read_text(bundled_path).text
            if text is not None:
                bundled = parse_es_systems(text, provenance="es_systems.xml (bundled)")
                # Read AND parsed: parse_es_systems answers {} for malformed
                # XML, and an enumeration that came back empty because the file
                # is broken is not an enumeration. RetroDECK ships the custom
                # overlay fully commented out, so it parses to zero systems by
                # design and can never stand in for this.
                read = bool(bundled)
        custom: dict[str, SystemDeclaration] = {}
        custom_path = os.path.join(root, "ES-DE", "custom_systems", _ES_SYSTEMS_XML)
        custom_text = self._machine.read_text(custom_path).text
        if custom_text is not None:
            custom = parse_es_systems(custom_text, provenance="es_systems.xml (custom_systems overlay)")
        return merge_layers(bundled, custom), read

    _CATALOGUE_SOURCE = "ES-DE catalogue read live (es_systems.xml, bundled + custom_systems overlay)"
    _ROM_DIRECTORY_SOURCE = "ES-DE ROMDirectory read live (es_settings.xml)"

    # ES-DE keeps its settings in its app-data tree under the Flatpak's config
    # directory, NOT under the RetroDECK home the catalogue overlay lives in —
    # the two diverge on an SD-card install, where <rd_home>/ES-DE/settings/
    # does not exist at all (observed 2026-08-08).
    _ESDE_SETTINGS_SUFFIX = os.path.join("ES-DE", "settings", "es_settings.xml")
    _ROM_DIRECTORY_SETTING = "ROMDirectory"

    def _esde_config_home(self) -> str:
        """The app's config home — what the frontend is launched with as its ``--home``.

        RetroDECK's only path to the frontend is
        ``components/es-de/component_launcher.sh:10``::

            exec "$component_path/bin/es-de" --home "${XDG_CONFIG_HOME}" "$@"

        and under Flatpak ``XDG_CONFIG_HOME`` is the per-app config directory,
        which is the tree this handle already reads the settings out of. So one
        path answers two questions: where the settings are, and what the
        frontend's home-relative defaults are relative to.
        """
        return os.path.join(self._home, ".var", "app", self._APP_ID, "config")

    def _esde_settings_path(self) -> str:
        return os.path.join(self._esde_config_home(), self._ESDE_SETTINGS_SUFFIX)

    def _rom_directory(self) -> tuple[str | None, str | None]:
        """The configured ``ROMDirectory``, and the status that stopped the reading.

        The value comes from ``es_settings.xml`` rather than from
        ``retrodeck.json``'s ``roms_path``, and the difference is the boundary
        rule, not pedantry: ``ROMDirectory`` is the setting ES-DE actually
        substitutes, while ``roms_path`` is RetroDECK's bookkeeping about the
        same tree. They agree on a stock installation and are two different
        files a user can move apart, and only one of them is what the frontend
        reads. Same shape as this handle's cfg-over-marker rule everywhere else.

        Three outcomes, and collapsing any two of them states a directory that
        is not in force. *Missing* is a reading — there is no file, so there is
        no configured value, and the frontend's own default is what applies
        (:meth:`_default_rom_directory`); so is a file that parses and sets the
        key empty or not at all. Both answer ``(None, None)``. *Unreadable*,
        *invalid-text* and *unparseable* are not readings: the file is there,
        ES-DE reads it fine, and what it says could be anything — so those
        answer the status, and no directory, rather than the default a file
        nobody could read has no claim to.

        The status rather than the caveat, because the two callers label it
        differently and only one of them has a system to name.
        """
        result = self._machine.read_text(self._esde_settings_path())
        if result.status == READ_MISSING:
            return None, None
        if result.text is None:
            return None, result.status
        settings = parse_es_settings(result.text)
        if settings is None:
            return None, _SETTINGS_UNPARSEABLE
        return settings.get(self._ROM_DIRECTORY_SETTING) or None, None

    def _rom_root(self) -> _RomRoot:
        """What ES-DE substitutes for ``%ROMPATH%`` — the ROM root, before any ``<path>``.

        The one chain every ROM-directory answer on this handle resolves
        through: :meth:`roms_dir` *is* this root, and :meth:`_esde_system_dir`
        is this root with the catalogue's declaration applied. Keeping them one
        chain is the point — two would be two rules about the same tree, and
        the day they disagreed atlas would be contradicting itself.

        A configured value carrying ``~`` is expanded the way the frontend
        expands it — every occurrence, as text
        (:func:`atlas.esde.expand_home_path`; the call is
        ``FileData.cpp:289``, ES-DE v3.4.1) — against ES-DE's own home, which
        on this arrangement is the launcher's ``--home "${XDG_CONFIG_HOME}"``
        (:meth:`_esde_config_home`), not the user's. That is the same home
        the unset default derives from, so the same Flatpak override that
        stops the default from resolving stops the expansion too: what ``~``
        becomes then is a tree atlas cannot follow. The absoluteness check
        runs on the *expanded* value — the raw one is what a refusal names,
        because the setting's own text is what a user edits.
        """
        configured, unreadable = self._rom_directory()
        if unreadable is not None:
            return _RomRoot(unreadable=unreadable)
        sources = (self._ROM_DIRECTORY_SOURCE,)
        if configured is None:
            moved = self._config_home_override()
            if moved is not None:
                return _RomRoot(relocated=moved, sources=sources)
            return _RomRoot(directory=self._default_rom_directory(), sources=sources)
        expanded = configured
        if "~" in configured:
            moved = self._config_home_override()
            if moved is not None:
                return _RomRoot(relocated=moved, sources=sources)
            expanded = expand_home_path(configured, self._esde_config_home())
        if not expanded.startswith("/"):
            return _RomRoot(not_absolute=configured, sources=sources)
        return _RomRoot(directory=expanded, sources=sources)

    def _esde_system_dir(
        self, by_system: Mapping[str, SystemDeclaration], system: str
    ) -> _RomDirectory:
        """Where ES-DE puts *system*'s ROMs: the root with the catalogue's ``<path>`` applied.

        Both the answer to "where do this system's ROMs live" and the anchor
        per-game gamelist entries are matched against, because they are the
        same directory — ES-DE reads a gamelist's ``./Name.ext`` relative to
        the very ``startPath`` it built here. Anchoring any other way means
        matching overrides against a directory the frontend does not launch
        from, which is how an override goes missing on a machine whose two ROM
        paths have drifted apart.
        """
        declaration = by_system.get(system)
        if declaration is None or declaration.rom_path is None:
            return _RomDirectory(caveats=_rom_path_undeclared_caveat(system, declaration))
        declared = declaration.rom_path
        root = self._rom_root()
        if root.unreadable is not None:
            return _RomDirectory(
                caveats=_settings_unreadable_caveat(
                    system, self._esde_settings_path(), root.unreadable
                )
            )
        if root.relocated is not None:
            return _RomDirectory(
                sources=root.sources,
                caveats=self._config_home_relocated_caveat(system, declared, *root.relocated),
            )
        if root.not_absolute is not None:
            return _RomDirectory(
                sources=root.sources,
                caveats=_rom_path_unresolved_caveat(system, declared, root.not_absolute),
            )
        resolved = resolve_rom_path(declared, root.directory)
        if resolved is None:
            # Unreachable as the branches above stand: the root is absolute
            # here and the declaration is non-empty, which is every way
            # resolve_rom_path refuses. Stated rather than dropped, because a
            # directory that silently became None is the one answer this
            # question must never give.
            return _RomDirectory(
                sources=root.sources,
                caveats=_rom_path_unresolved_caveat(system, declared, root.directory or ""),
            )
        return _RomDirectory(directory=resolved, sources=root.sources)

    def _default_rom_directory(self) -> str:
        """Where the frontend looks when ``ROMDirectory`` is unset — resolved, not asserted.

        ES-DE falls back on ``<home>/ROMs/`` when the setting is empty
        (``es-app/src/FileData.cpp::getROMDirectory()``, ES-DE 3.4.1,
        ``:271-305`` with the empty-setting branch at ``:283-284``; RetroDECK
        ships the ``RetroDECK/ES-DE`` fork with that function unmodified at the
        pinned build), and its home is not the user's: the launcher passes
        ``--home "${XDG_CONFIG_HOME}"`` unconditionally, which outranks both
        ``portable.txt`` and ``$HOME``. That makes the branch reachable and its
        value knowable, which is the whole difference between resolving and
        guessing — the empty setting is the state RetroDECK's own shipped
        template is in before its first ``sed``.

        Resolving is not asserting the directory exists. Nothing here stats it;
        an absent one is the ordinary missing-directory state every other root
        is in, and the caller's own treatment applies unchanged.
        """
        return os.path.join(self._esde_config_home(), "ROMs")

    # Flatpak's overrides directories, one per installation. Both hold a file
    # per app id and a file named "global" that applies to every app. Each of
    # the four spellings was observed live under `strace` (flatpak 1.16.6,
    # reference machine 2026-08-08): one `flatpak override --show` invocation
    # opens exactly one file, and the four flag combinations — plain,
    # `--user`, `<app id>`, `--user <app id>` — open these four in turn.
    _FLATPAK_OVERRIDES_USER = os.path.join(".local", "share", "flatpak", "overrides")
    _FLATPAK_OVERRIDES_SYSTEM = os.path.join("/var", "lib", "flatpak", "overrides")
    _FLATPAK_OVERRIDES_GLOBAL = "global"

    def _override_files(self) -> tuple[str, ...]:
        """Every Flatpak overrides file that can speak for this app, most specific first.

        App-specific before global, because that is Flatpak's own precedence —
        "if the application ID APP is not specified then the overrides affect
        all applications, but the per-application overrides can override the
        global overrides" (flatpak-override(1)). Both installations are read at
        each level: which one deploys this app is not something atlas has
        established, and the order only decides which key a message names —
        every one of these files means the same thing to the answer.
        """
        directories = (
            os.path.join(self._home, self._FLATPAK_OVERRIDES_USER),
            self._FLATPAK_OVERRIDES_SYSTEM,
        )
        return tuple(
            os.path.join(directory, name)
            for name in (self._APP_ID, self._FLATPAK_OVERRIDES_GLOBAL)
            for directory in directories
        )

    def _config_home_override(self) -> tuple[str, str] | None:
        """The overrides file and key that move the app's config home, if one does.

        The default above holds only while the frontend's ``--home`` expands to
        the tree atlas read the settings from. A Flatpak overrides file can
        redefine that environment, and then atlas resolved against a directory
        the frontend is not using — quite possibly having missed the settings
        file itself for the same reason, which is how the answer lands on the
        default branch in the first place. Either way the honest answer is to
        stop resolving and say why, not to state a plausible directory.

        Returns ``(path, key)``, or ``None`` when nothing moved it.
        """
        for path in self._override_files():
            text = self._machine.read_text(path).text
            if text is None:
                continue
            environment = _environment_overrides(text)
            for key in _CONFIG_HOME_ENV_KEYS:
                if key in environment:
                    return path, key
        return None

    def _systems_answer(self) -> tuple[SystemsAnswer, str | None]:
        """Every system the catalogue declares, sorted — and the version that read stated.

        The findings come from the marker snapshot this query already read, so
        the answer's health, its roots and the version its evidence is weighed
        against are one revision of the file.
        """
        config, marker_issues = self._read_marker()
        findings = self._health_from(config, marker_issues).issues
        by_system, read = self._read_catalogue(self._config_path(config, "rd_home_path", "")[0])
        version = _marker_version(config)
        if not read:
            return SystemsAnswer(caveats=(*findings, *_catalogue_unread_caveat())), version
        return SystemsAnswer(tuple(sorted(by_system)), (self._CATALOGUE_SOURCE,), findings), version

    def _gamelist_selections_at(self, root: str, system: str) -> GamelistSelections:
        gamelist_path = os.path.join(root, "ES-DE", "gamelists", system, "gamelist.xml")
        text = self._machine.read_text(gamelist_path).text
        if text is None:
            return GamelistSelections(system_label=None, per_game={})
        return parse_gamelist(text)

    def gamelist_selections(self, system: str) -> GamelistSelections:
        config, _ = self._read_marker()
        return self._gamelist_selections_at(self._config_path(config, "rd_home_path", "")[0], system)

    def _catalogue_answer(
        self, system: str, *, content_path: str | None = None
    ) -> tuple[CatalogueAnswer, str | None]:
        """RetroDECK's own catalogue answer — one snapshot of the ES-DE sources.

        The contract this fills in is on
        :meth:`_CatalogueQueries.emulators_for`; what is RetroDECK's alone is
        where the answer comes from: the marker, the bundled ``es_systems.xml``
        plus its ``custom_systems`` overlay, and the system's gamelist, each
        read once here and handed to the entry assembly together (REVIEW M4).
        The marker's version travels back with the answer for the same reason.
        """
        config, marker_issues = self._read_marker()
        findings = self._health_from(config, marker_issues).issues
        root = self._config_path(config, "rd_home_path", "")[0]
        by_system, read = self._read_catalogue(root)
        version = _marker_version(config)
        if not read:
            return CatalogueAnswer(caveats=(*findings, *_catalogue_unread_caveat(system))), version
        # The anchor is only consulted where a content path was named, so only
        # that query pays ES-DE's settings read — and only that query can be
        # told the anchor failed, which is the whole reason its caveats join.
        anchor = (
            self._esde_system_dir(by_system, system)
            if content_path is not None
            else _NO_ANCHOR_NEEDED
        )
        return (
            CatalogueAnswer(
                _entries_from(
                    self,
                    _declared_entries(by_system, system),
                    self._gamelist_selections_at(root, system),
                    system_roms_dir=anchor.directory,
                    content_path=content_path,
                ),
                (self._CATALOGUE_SOURCE,),
                (*findings, *anchor.caveats),
            ),
            version,
        )

    def _rom_location_answer(self, system: str) -> tuple[RomPlacement, str | None]:
        """RetroDECK's ROM placement — the catalogue's declaration, resolved ES-DE's way.

        One snapshot again: the marker, both catalogue layers and ES-DE's
        settings are each read once here. The extensions survive an unresolved
        directory on purpose — which files launch is declared in the same
        element and does not depend on where they sit, so refusing to state
        them would throw away a fact atlas holds.
        """
        config, marker_issues = self._read_marker()
        findings = self._health_from(config, marker_issues).issues
        by_system, read = self._read_catalogue(self._config_path(config, "rd_home_path", "")[0])
        version = _marker_version(config)
        if not read:
            return RomPlacement(caveats=(*findings, *_catalogue_unread_caveat(system))), version

        # A source names a reading this answer rests on, so the settings file
        # joins the list only where the resolution actually read it — which is
        # every outcome except the two that never opened it or opened it and
        # failed. The resolution reports that itself rather than being asked.
        declaration = by_system.get(system)
        resolved = self._esde_system_dir(by_system, system)
        placement = RomPlacement(
            extensions=() if declaration is None else declaration.extensions,
            sources=(self._CATALOGUE_SOURCE, *resolved.sources),
            caveats=(*findings, *resolved.caveats),
        )
        if resolved.directory is None:
            return placement, version
        # Dropping resolved.caveats here is safe only because every branch of
        # _esde_system_dir that carries one also answers directory=None, so a
        # resolved directory has nothing to say. The day one of them resolves
        # AND caveats, this line starts swallowing it.
        #
        # The same link view every placement answers with, from the one helper
        # all of them share — a fourth symlink walk is exactly what the seam's
        # three existing ones warn against.
        physical_dir, link_caveats = _link_view(self._machine, resolved.directory)
        return (
            _dc_replace(
                placement,
                dir=resolved.directory,
                physical_dir=physical_dir,
                caveats=(*findings, *link_caveats),
            ),
            version,
        )

    @staticmethod
    def _config_home_relocated_caveat(
        system: str, declared: str, path: str, key: str
    ) -> tuple[Caveat, ...]:
        """A Flatpak override moved the tree the frontend's home-relative answers derive from.

        Both home-derived resolutions land here — the unset default and a
        ``~`` in the configured value — because they expand against the same
        moved home. Its own code rather than the unresolved one, because it is
        a different claim with a different remedy: nothing about this machine
        is wrong, and atlas is the one that cannot follow. It is also wider
        than this answer — the settings file atlas read lives in that same
        tree, so where an override moved it, other readings from this handle
        may rest on a file that is not the one in force. A client cannot learn
        that from prose.
        """
        return (
            Caveat(
                CAVEAT_CONFIG_HOME_RELOCATED,
                f"the catalogue declares {system!r} at {declared!r}, and the Flatpak overrides at "
                f"{path} redefine {key} for this app — so where the frontend's home-relative "
                "resolution lands (its own default, or a ~ in its setting) is not something atlas "
                "read, and the settings it did read may not be the ones in force either",
                {"system": system, "declared": declared, "path": path, "key": key},
            ),
        )

    def _query_from(
        self,
        config: dict[str, Any],
        marker_issues: tuple[Caveat, ...],
        *,
        content_path: str | None,
        core_so: str | None,
        extra_caveats: tuple[Caveat, ...] = (),
    ) -> _SaveQuery:
        """The placement question over a marker snapshot this query already read.

        Which family it is asked about is the resolver's business, not the
        query's: savefiles and savestates are governed by the same cfg, the same
        override chain and the same core, so one question object serves both.
        """
        health = self._health_from(config, marker_issues)
        global_cfg_path = os.path.join(self._home, RETRODECK_CFG_SUFFIX)
        global_text = self._machine.read_text(global_cfg_path).text
        version = _marker_version(config)
        return _SaveQuery(
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
            arrangement_version=version,
            extra_caveats=(
                *extra_caveats,
                *health.issues,
                *arrangement_caveats(self.kind, observed_version=version),
            ),
        )

    def _savefile_location_from(
        self,
        config: dict[str, Any],
        marker_issues: tuple[Caveat, ...],
        *,
        content_path: str | None,
        core_so: str | None,
        extra_caveats: tuple[Caveat, ...] = (),
    ) -> SavefilePlacement:
        return _retroarch_savefile_location(
            self._machine,
            self._query_from(
                config,
                marker_issues,
                content_path=content_path,
                core_so=core_so,
                extra_caveats=extra_caveats,
            ),
        )

    def _savestate_location_from(
        self,
        config: dict[str, Any],
        marker_issues: tuple[Caveat, ...],
        *,
        content_path: str | None,
        core_so: str | None,
        extra_caveats: tuple[Caveat, ...] = (),
    ) -> SavestatePlacement:
        return _retroarch_savestate_location(
            self._machine,
            self._query_from(
                config,
                marker_issues,
                content_path=content_path,
                core_so=core_so,
                extra_caveats=extra_caveats,
            ),
        )

    def savefile_location(self, *, content_path: str | None = None, core_so: str | None = None) -> SavefilePlacement:
        """Where this RetroDECK's RetroArch keeps the save for *content_path* under *core_so*.

        ``core_so`` is the core's ``.so`` basename (e.g.
        ``"mupen64plus_next_libretro.so"``) or a full path; atlas resolves
        ``library_name`` from the binary. Both arguments are optional — missing
        ones leave holes and stated caveats, never guesses.
        """
        config, marker_issues = self._read_marker()
        return self._savefile_location_from(config, marker_issues, content_path=content_path, core_so=core_so)

    def savestate_location(
        self, *, content_path: str | None = None, core_so: str | None = None
    ) -> SavestatePlacement:
        """Where this RetroDECK's RetroArch keeps the savestates for *content_path*.

        The savefile question's twin, taking the same two optional arguments and
        answering off the same configs — through the savestate quartet of keys
        instead of the savefile one.
        """
        config, marker_issues = self._read_marker()
        return self._savestate_location_from(config, marker_issues, content_path=content_path, core_so=core_so)

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
            arrangement_version=_marker_version(config),
        )

    def _read_firmware_context(self) -> FirmwareContext:
        return self._firmware_context_from(*self._read_marker())

    def firmware_for_system(self, system: str, *, verify: bool = False) -> FirmwareAnswer:
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
        catalogue = Catalogue(
            entries=_firmware_catalogue_entries(
                self, by_system, system, self._gamelist_selections_at(root, system)
            ),
            read=read,
        )
        # The marker this query already read builds the context too — asking
        # for a fresh one would read retrodeck.json twice inside one answer.
        context = self._stated(self._firmware_context_from(config, marker_issues))
        return _resolve_for_system(
            self._machine, context, system=system, catalogue=catalogue, verify=verify
        )

    def _entry_caveats_for(
        self, config: dict[str, Any], spec: EmulatorSpec, content_path: str
    ) -> tuple[Caveat, ...]:
        """What the gamelist says about *this* game being launched by *this* entry.

        Costs the catalogue and ES-DE's settings, and only on the content
        branch: the anchor is the system's ``<path>`` resolved against
        ``ROMDirectory``, and the catalogue is the only thing that declares a
        ``<path>``. There is no cheaper faithful route — the previous one was
        cheaper by reading the wrong file.

        The same fact governs both placement questions, because it is about
        which emulator runs at all: an entry that would not launch this game
        keeps neither its saves nor its states.
        """
        root = self._config_path(config, "rd_home_path", "")[0]
        selections = self._gamelist_selections_at(root, spec.system)
        by_system, read = self._read_catalogue(root)
        # A catalogue nobody could read declares nothing, which is not the
        # same fact as a catalogue that was read and declares no <path> for
        # this system — and handing the empty snapshot straight to the
        # resolution would spell the first as the second.
        anchor = (
            self._esde_system_dir(by_system, spec.system)
            if read
            else _RomDirectory(caveats=_catalogue_unread_caveat(spec.system))
        )
        override_label = (
            None
            if anchor.directory is None
            else _match_per_game(selections, content_path, system_roms_dir=anchor.directory)
        )
        if override_label is None or override_label == spec.label:
            return anchor.caveats
        return (*anchor.caveats, _per_game_override_caveat(override_label, spec))

    def entry_savefile_location(
        self,
        spec: EmulatorSpec,
        entry_caveats: tuple[Caveat, ...] = (),
        *,
        content_path: str | None = None,
    ) -> SavefilePlacement:
        """The entry route behind :meth:`EmulatorEntry.savefile_location` — one marker read.

        Resolves the placement for a catalogue entry and, when *content_path*
        is given, checks the gamelist for a per-game override that would launch
        a different emulator — all from one snapshot of the governing sources.
        """
        config, marker_issues = self._read_marker()
        placement = self._savefile_location_from(
            config,
            marker_issues,
            content_path=content_path,
            core_so=spec.core_so,
            extra_caveats=entry_caveats,
        )
        if content_path is None:
            return placement
        extra = self._entry_caveats_for(config, spec, content_path)
        return _dc_replace(placement, caveats=(*placement.caveats, *extra)) if extra else placement

    def entry_savestate_location(
        self,
        spec: EmulatorSpec,
        entry_caveats: tuple[Caveat, ...] = (),
        *,
        content_path: str | None = None,
    ) -> SavestatePlacement:
        """The entry route behind :meth:`EmulatorEntry.savestate_location` — one marker read.

        The savefile route's twin, down to the per-game override check: which
        emulator ES-DE would actually launch decides both answers, so the one
        that would not launch says so on both.
        """
        config, marker_issues = self._read_marker()
        placement = self._savestate_location_from(
            config,
            marker_issues,
            content_path=content_path,
            core_so=spec.core_so,
            extra_caveats=entry_caveats,
        )
        if content_path is None:
            return placement
        extra = self._entry_caveats_for(config, spec, content_path)
        return _dc_replace(placement, caveats=(*placement.caveats, *extra)) if extra else placement


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


# EmuDeck's settings.sh records which frontends its installer was told to set
# up — ``jsonToBashVars.sh:71`` writes ``doInstallESDE`` as a plain key=value
# line (upstream ``dragoonDorise/EmuDeck`` @ ``863ab69``). The disk decides
# whether ES-DE is answered from — EmuDeck's own presence test is the AppImage
# stat (``ESDE_IsInstalled``, ``emuDeckESDE.sh:488-494``) — and when the
# record and the disk disagree, the answer states the disagreement instead of
# silently following either side.
CAVEAT_FRONTEND_MARKER_MISMATCH = "frontend-marker-mismatch"


@dataclass(frozen=True, slots=True)
class _EsdeSnapshot:
    """One catalogue query's reading of EmuDeck's ES-DE side.

    ``refusal`` set means the answer is over: it is the complete caveat list
    of an answer that enumerates nothing — no ES-DE on disk, or a broken
    resource-override shadow — findings, reason and cross-check already in
    answer order. ``refusal`` ``None`` means the layers were read:
    ``by_system`` holds the merge, ``bundled_read`` whether the shadow stood
    in for the bundled layer, ``relocated`` whether a ``portable.txt`` casts
    doubt on the reads, and ``tail`` the caveats every enumerating answer
    states after its findings (sealed, relocation, marker cross-check — the
    pinned order).
    """

    findings: tuple[Caveat, ...]
    refusal: tuple[Caveat, ...] | None
    by_system: Mapping[str, SystemDeclaration]
    bundled_read: bool
    relocated: bool
    tail: tuple[Caveat, ...]


class EmuDeck(_FirmwareQueries, _CatalogueQueries):
    """An EmuDeck arrangement — ``settings.sh`` is its truth, the bare
    ``org.libretro.RetroArch`` Flatpak is its RetroArch.

    The handle carries both descriptions (``kinds``): EmuDeck *is* a configured
    bare RetroArch, so both statements are true of the same installation.
    Like every handle it is live — one snapshot of each source per query
    (REVIEW M4) — and its health covers the claimed companion RetroArch config,
    so a stale ``settings.sh`` next to a vanished Flatpak is visible instead of
    silently suppressing the bare handle (REVIEW H10).

    Its frontend side: EmuDeck deploys **upstream ES-DE as an AppImage** at
    ``~/Applications/ES-DE.AppImage`` (``vars.sh:4-6``, ``emuDeckESDE.sh:9-10``,
    upstream ``dragoonDorise/EmuDeck`` @ ``863ab69``; the stable
    ``LinuxSteamDeckAppImage`` package from ES-DE's own ``latest_release.json``,
    ``emuDeckESDE.sh:15,91-98``) and wires it through plain files under
    ``~/ES-DE`` — the application data directory ES-DE resolves when launched
    with no ``--home`` and no ``ESDE_APPDATA_DIR``, which is how EmuDeck's
    launcher runs it (``tools/launchers/es-de/es-de.sh:5``; ES-DE v3.4.1
    ``FileSystemUtil.cpp:259-285``). The catalogue answers read those files;
    what stays out of reach is the **bundled** ``es_systems.xml``, which the
    AppImage embeds (ES-DE ``INSTALL.md`` v3.4.1:1470) — see
    :meth:`_read_esde_catalogue`.
    """

    kind = "emudeck"
    kinds = ("emudeck", "bare_retroarch_flatpak")
    _RA_APP_ID = RETROARCH_FLATPAK_APP_ID

    def _catalogue_absence(self) -> Caveat:
        # Not "there is none": this EmuDeck runs no ES-DE atlas can find, and
        # what its frontend is instead — Pegasus, Steam ROM Manager — is what
        # atlas has not established. The honest answer is about atlas, so the
        # code says unestablished and the message does not claim an absence.
        return Caveat(
            CAVEAT_EMULATOR_CATALOGUE_UNESTABLISHED,
            "no ES-DE is present on this EmuDeck arrangement (no ~/Applications/ES-DE.AppImage, no "
            "~/ES-DE tree) — EmuDeck can also be driven by Pegasus or Steam ROM Manager, and atlas "
            "has not established where those keep a catalogue, so which emulators run a system is "
            "unknown here; name the core yourself with savefile_location(core_so=...)",
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

    def roms_dir(self) -> str | None:
        """The ROM root the frontend substitutes for ``%ROMPATH%`` — or ``None``.

        The **root**, not a system's directory: a system's own directory is
        ``rom_location(system).dir``, which is this plus whatever ``<path>`` the
        catalogue declares for it — usually the system name, and not reliably
        so, which is why the two are different questions.

        Read from ES-DE's ``ROMDirectory`` rather than ``settings.sh``'s
        ``romsPath``. The two agree on a stock installation and are wired one
        way — EmuDeck seds its own value into ES-DE's setting
        (``ESDE_setDefaultSettings``, ``emuDeckESDE.sh:407-409``) and never
        reads it back — so where a user has moved them apart, only this one is
        what the frontend launches from. The same cfg-over-marker rule as
        RetroDECK's :meth:`RetroDeck.roms_dir`, over this arrangement's files.

        ``None`` is a refusal, never "no ROMs here", and it is the same refusal
        :meth:`rom_location` states — for the reasons that belong to the root:
        no ES-DE is on this disk at all (so there is no frontend whose
        substitution this could be), ES-DE's settings exist and could not be
        read, a ``portable.txt`` may have moved the tree the frontend's own
        home-derived answers come from — the unset default and a ``~`` in the
        setting alike — or the configured value is not an absolute path even
        after the frontend's own ``~`` expansion. A bare string cannot carry
        which, and raising is not this domain's grammar, so a caller who needs
        the reason asks ``rom_location(system)`` and reads its caveats.
        """
        if not self._esde_present():
            return None
        return self._esde_rom_root(relocated=self._relocation_caveat() is not None).directory

    def _companion_cfg_path(self) -> str:
        return os.path.join(self._home, STANDALONE_FLATPAK_CFG_SUFFIX)

    def _health_from(
        self,
        settings: dict[str, str],
        marker_issues: tuple[Caveat, ...],
        companion_status: ReadStatus,
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

    # ── EmuDeck's ES-DE ─────────────────────────────────────────────────
    # Every path below is EmuDeck's shipped wiring, read from the installer at
    # the pinned revision (dragoonDorise/EmuDeck @ 863ab69) and from ES-DE's
    # own source at v3.4.1 — docs/research/retrodeck-save-placement.md §13
    # holds the evidence.

    _ESDE_APPIMAGE_SUFFIX = os.path.join("Applications", "ES-DE.AppImage")
    _ESDE_PORTABLE_SUFFIX = os.path.join("Applications", "portable.txt")
    _ESDE_APPDATA_DIRNAME = "ES-DE"
    _ESDE_SHADOW_SUFFIX = os.path.join("resources", "systems", "linux", _ES_SYSTEMS_XML)
    _ESDE_OVERLAY_SUFFIX = os.path.join("custom_systems", _ES_SYSTEMS_XML)
    _ESDE_SETTINGS_SUFFIX = os.path.join("settings", "es_settings.xml")
    _ROM_DIRECTORY_SETTING = "ROMDirectory"
    _FRONTEND_MARKER_KEY = "doInstallESDE"
    _CATALOGUE_SOURCE = "ES-DE catalogue read live (es_systems.xml, on-disk layers under ~/ES-DE)"
    _ROM_DIRECTORY_SOURCE = "ES-DE ROMDirectory read live (es_settings.xml)"

    def _esde_appdata_dir(self) -> str:
        """ES-DE's application data directory — plain ``~/ES-DE``.

        EmuDeck's launcher runs the AppImage with no ``--home`` and no
        ``ESDE_APPDATA_DIR`` (``tools/launchers/es-de/es-de.sh:5``), so the
        upstream resolution lands on ``<home>/ES-DE`` (ES-DE v3.4.1
        ``FileSystemUtil.cpp:259-285``). A user-set ``ESDE_APPDATA_DIR`` in the
        launch environment would move it and is written nowhere on disk — that
        residual is documented, not probed; the on-disk relocation
        (``portable.txt``) is (:meth:`_relocation_caveat`).
        """
        return os.path.join(self._home, self._ESDE_APPDATA_DIRNAME)

    def _esde_appimage_path(self) -> str:
        return os.path.join(self._home, self._ESDE_APPIMAGE_SUFFIX)

    def _esde_present(self) -> bool:
        """Whether ES-DE is on this disk — the AppImage, or its appdata tree.

        The AppImage stat is EmuDeck's own installed-test
        (``ESDE_IsInstalled``, ``emuDeckESDE.sh:488-494``: ``[ -e
        "$ESDE_toolPath" ]``); the ``~/ES-DE`` tree covers an AppImage moved
        or renamed out from under a configuration that still runs. Disk
        decides — ``settings.sh``'s own record is a cross-check
        (:meth:`_frontend_marker_caveat`), never the decision.
        """
        if self._machine.path_kind(self._esde_appimage_path()) != KIND_MISSING:
            return True
        return self._machine.path_kind(self._esde_appdata_dir()) == KIND_DIRECTORY

    def _relocation_caveat(self) -> Caveat | None:
        """The ``portable.txt`` statement, when one sits next to the AppImage — else ``None``.

        ES-DE reads ``portable.txt`` from its executable directory and moves
        its home by it — but only after validating the target: a path that
        does not exist or is a regular file is rejected and the default home
        stays (ES-DE v3.4.1 ``main.cpp:149-192``; the validation block is
        ``:174-192``). EmuDeck writes none; a user can. Atlas stats only the
        file's presence, so the honest claim is *may*: when one is there,
        every ``~/ES-DE`` read this handle makes may be reading files the
        frontend is not using — the answers still state what the on-disk tree
        says, and this caveat states the suspicion, never silently. Reading
        the file and validating its target the way ES-DE does is a possible
        refinement; presence alone is what is stated today.
        """
        path = os.path.join(self._home, self._ESDE_PORTABLE_SUFFIX)
        if self._machine.path_kind(path) == KIND_MISSING:
            return None
        return Caveat(
            CAVEAT_CONFIG_HOME_RELOCATED,
            f"a portable.txt sits next to the ES-DE AppImage at {path}, which may relocate "
            "ES-DE's application data directory away from ~/ES-DE (ES-DE ignores it when its "
            "target is missing or a file) — the on-disk files this answer was read from may "
            "not be the ones the frontend is using",
            {"path": path},
        )

    def _frontend_marker_caveat(self, settings: dict[str, str], present: bool) -> Caveat | None:
        """The cross-check: ``settings.sh``'s ES-DE record against the disk — ``None`` in agreement.

        ``doInstallESDE`` records the installer's choice
        (``jsonToBashVars.sh:71``); the disk records what is actually here.
        The disk decides the answer either way — this caveat exists so a stale
        record is stated rather than discovered. A marker that writes neither
        ``true`` nor ``false`` states nothing, and no disagreement can be
        manufactured from silence.
        """
        stated = settings.get(self._FRONTEND_MARKER_KEY)
        if stated not in ("true", "false"):
            return None
        if (stated == "true") == present:
            return None
        observed = "present" if present else "absent"
        consequence = (
            "the answer is read from the ES-DE on disk"
            if present
            else "no ES-DE answers here"
        )
        return Caveat(
            CAVEAT_FRONTEND_MARKER_MISMATCH,
            f"settings.sh states {self._FRONTEND_MARKER_KEY}={stated} while ES-DE is {observed} "
            f"on disk — the marker's record is stale or the frontend changed hands; {consequence}, "
            "because the disk is what runs",
            {"key": self._FRONTEND_MARKER_KEY, "stated": stated, "observed": observed},
        )

    def _riders(self, settings: dict[str, str], present: bool) -> tuple[Caveat, ...]:
        """The statements that ride beside the catalogue status, in the pinned order.

        The relocation suspicion first, then the marker cross-check — one
        builder, consumed by :meth:`_esde_snapshot`'s tail and by the firmware
        route, so the two can never spell the order apart. The relocation read
        happens only while an ES-DE is present: with none on disk there is
        nothing a ``portable.txt`` could move out from under.
        """
        cross_check = self._frontend_marker_caveat(settings, present)
        mismatch = () if cross_check is None else (cross_check,)
        if not present:
            return mismatch
        portable = self._relocation_caveat()
        relocation = () if portable is None else (portable,)
        return (*relocation, *mismatch)

    def _catalogue_sealed_caveat(self, system: str | None = None) -> Caveat:
        # Reading the sealed layer itself (extracting the AppImage's squashfs)
        # is tracked as issue #65; until then the on-disk layers are the whole
        # readable catalogue and every catalogue answer says so.
        return Caveat(
            CAVEAT_EMULATOR_CATALOGUE_SEALED,
            "ES-DE's bundled es_systems.xml is sealed inside the AppImage, which atlas does not "
            "open — only the on-disk layers under ~/ES-DE were read, so this enumeration is "
            "incomplete: a system or emulator this answer does not name may still be declared by "
            "the frontend",
            {"system": system} if system is not None else {},
        )

    def _read_esde_catalogue(self) -> tuple[dict[str, SystemDeclaration], bool, bool]:
        """The readable ES-DE layers, merged → ``(by_system, bundled_read, shadow_broken)``.

        The bundled ``es_systems.xml`` is embedded in the AppImage (ES-DE
        ``INSTALL.md`` v3.4.1:1470) and atlas does not open AppImages, so the
        bundled layer is ordinarily not readable — that is the ``sealed``
        state, and ``bundled_read`` is ``False``. The one exception is ES-DE's
        own per-file resource override (``INSTALL.md`` v3.4.1:1125): a file at
        ``~/ES-DE/resources/systems/linux/es_systems.xml`` shadows the
        embedded one for ES-DE itself, so where it exists and parses it *is*
        the bundled layer, on disk — ``bundled_read`` is ``True`` and nothing
        is sealed away. A shadow that exists and cannot be read or parsed is
        the third state (``shadow_broken``): ES-DE loads that file, atlas
        could not, and what the catalogue says is then unknown — the same
        claim RetroDECK's unreadable bundled layer makes.

        The overlay is EmuDeck's own write (``emuDeckESDE.sh:18,127``,
        deployed from ``configs/emulationstation/custom_systems/`` and
        path-rewritten at ``:144-145``): unlike RetroDECK's commented-out
        stub, it declares real systems, and per ES-DE's merge semantics a
        system it declares is *exactly* the one the frontend uses — the
        sealed layer cannot contradict a same-name overlay system.
        """
        appdata = self._esde_appdata_dir()
        bundled: dict[str, SystemDeclaration] = {}
        bundled_read = False
        shadow = self._machine.read_text(os.path.join(appdata, self._ESDE_SHADOW_SUFFIX))
        if shadow.status != READ_MISSING:
            if shadow.text is not None:
                bundled = parse_es_systems(
                    shadow.text, provenance="es_systems.xml (resource-override shadow)"
                )
                # Read AND parsed, the same rule RetroDECK's bundled layer
                # holds to: an enumeration that came back empty because the
                # file is broken is not an enumeration.
                bundled_read = bool(bundled)
            if not bundled_read:
                return {}, False, True
        custom: dict[str, SystemDeclaration] = {}
        custom_text = self._machine.read_text(os.path.join(appdata, self._ESDE_OVERLAY_SUFFIX)).text
        if custom_text is not None:
            custom = parse_es_systems(custom_text, provenance="es_systems.xml (custom_systems overlay)")
        return merge_layers(bundled, custom), bundled_read, False

    def _gamelist_selections(self, system: str) -> GamelistSelections:
        """The gamelist's emulator selections — EmuDeck seeds these itself.

        ``~/ES-DE/gamelists/<system>/gamelist.xml`` is upstream's location
        (ES-DE ``INSTALL.md`` v3.4.1:2145), and EmuDeck writes per-system
        ``<alternativeEmulator>`` defaults into it (``ESDE_setEmu``,
        ``emuDeckESDE.sh:427-480``) — the standalone-heavy selections are the
        arrangement's normal state, not a user customization.
        """
        path = os.path.join(self._esde_appdata_dir(), "gamelists", system, "gamelist.xml")
        text = self._machine.read_text(path).text
        if text is None:
            return GamelistSelections(system_label=None, per_game={})
        return parse_gamelist(text)

    def _esde_settings_path(self) -> str:
        return os.path.join(self._esde_appdata_dir(), self._ESDE_SETTINGS_SUFFIX)

    def _rom_directory(self) -> tuple[str | None, str | None]:
        """The configured ``ROMDirectory``, and the status that stopped the reading.

        The same three-outcome rule as RetroDECK's reading of the same file
        (one grammar, one settings format): missing or set-nothing answers
        ``(None, None)`` and the frontend's default applies; a file that is
        there and could not be read or parsed answers the status, and no
        directory. EmuDeck writes the value itself — ``ESDE_setDefaultSettings``
        seds ``${romsPath}`` into it (``emuDeckESDE.sh:407-409``) — so on a
        stock machine it is configured and absolute.
        """
        result = self._machine.read_text(self._esde_settings_path())
        if result.status == READ_MISSING:
            return None, None
        if result.text is None:
            return None, result.status
        settings = parse_es_settings(result.text)
        if settings is None:
            return None, _SETTINGS_UNPARSEABLE
        return settings.get(self._ROM_DIRECTORY_SETTING) or None, None

    def _esde_rom_root(self, *, relocated: bool) -> _RomRoot:
        """What this ES-DE substitutes for ``%ROMPATH%`` — or which way it could not be established.

        The unset default is ``~/ROMs``: ES-DE falls back on ``<home>/ROMs``
        (``FileData.cpp::getROMDirectory()``, ES-DE v3.4.1, ``:271-305``, the
        empty-setting branch at ``:283-284``), and this ES-DE's home is the
        user's own — the launcher passes no ``--home`` (``es-de.sh:5``). A
        configured value carrying ``~`` expands against that same home, as
        text (:func:`atlas.esde.expand_home_path`; the call is
        ``FileData.cpp:289``). Both derivations are exactly what a
        ``portable.txt`` may move, so with one present both home-derived
        branches stop resolving (*relocated*) — a configured absolute value
        is still answered, with the answer-level relocation caveat riding.
        The absoluteness check runs on the *expanded* value; the raw one is
        what a refusal names, because the setting's own text is what a user
        edits.
        """
        configured, unreadable = self._rom_directory()
        if unreadable is not None:
            return _RomRoot(unreadable=unreadable)
        sources = (self._ROM_DIRECTORY_SOURCE,)
        portable = (os.path.join(self._home, self._ESDE_PORTABLE_SUFFIX), "portable.txt")
        if configured is None:
            if relocated:
                return _RomRoot(relocated=portable, sources=sources)
            return _RomRoot(directory=os.path.join(self._home, "ROMs"), sources=sources)
        expanded = configured
        if "~" in configured:
            if relocated:
                return _RomRoot(relocated=portable, sources=sources)
            expanded = expand_home_path(configured, self._home)
        if not expanded.startswith("/"):
            return _RomRoot(not_absolute=configured, sources=sources)
        return _RomRoot(directory=expanded, sources=sources)

    def _esde_system_dir(
        self,
        by_system: Mapping[str, SystemDeclaration],
        system: str,
        *,
        bundled_read: bool,
        relocated: bool,
    ) -> _RomDirectory:
        """Where this ES-DE puts *system*'s ROMs — the root with the declared ``<path>`` applied.

        The same chain as RetroDECK's, with one branch that is EmuDeck's own:
        a system the readable layers do not declare is ``rom-path-undeclared``
        only when the bundled layer was read (the on-disk shadow) — in the
        sealed state the declaration may sit in the layer nobody could read,
        and the caller's answer-level sealed caveat is that statement, so this
        branch adds nothing on top of it. The relocated branch is silent here
        for the same reason: the answer-level ``config-home-relocated`` caveat
        is the stated reason.
        """
        declaration = by_system.get(system)
        if declaration is None or declaration.rom_path is None:
            if bundled_read:
                return _RomDirectory(caveats=_rom_path_undeclared_caveat(system, declaration))
            return _RomDirectory()
        declared = declaration.rom_path
        root = self._esde_rom_root(relocated=relocated)
        if root.unreadable is not None:
            return _RomDirectory(
                caveats=_settings_unreadable_caveat(system, self._esde_settings_path(), root.unreadable)
            )
        if root.relocated is not None:
            return _RomDirectory(sources=root.sources)
        if root.not_absolute is not None:
            return _RomDirectory(
                sources=root.sources,
                caveats=_rom_path_unresolved_caveat(system, declared, root.not_absolute),
            )
        resolved = resolve_rom_path(declared, root.directory)
        if resolved is None:
            # Unreachable as the branches above stand — same guard, same
            # reason as RetroDECK's: a directory that silently became None is
            # the one answer this question must never give.
            return _RomDirectory(
                sources=root.sources,
                caveats=_rom_path_unresolved_caveat(system, declared, root.directory or ""),
            )
        return _RomDirectory(directory=resolved, sources=root.sources)

    def _esde_snapshot(self, system: str | None = None) -> _EsdeSnapshot:
        """One read of every ES-DE-side source, in the pinned caveat order.

        The findings an answer states, the presence it decided and the layers
        it enumerates come from one revision of each file — a second read for
        any of them could see a different machine than the answer did (REVIEW
        M4). The caveat order every catalogue-shaped answer states is pinned
        here and only here: health findings lead (they qualify the
        installation), then the catalogue-status statement (sealed,
        unreadable, or the unestablished refusal), then the riding statements
        in :meth:`_riders`'s one order (the relocation suspicion, then the
        marker cross-check), then whatever the per-question resolution has to
        say — and the evidence caveat the template method appends closes the
        list.
        """
        settings, marker_issues = self._read_marker()
        companion_status = self._machine.read_text(self._companion_cfg_path()).status
        findings = self._health_from(settings, marker_issues, companion_status).issues
        present = self._esde_present()
        riders = self._riders(settings, present)
        if not present:
            return _EsdeSnapshot(
                findings, (*findings, self._catalogue_absence(), *riders), {}, False, False, ()
            )
        relocated = any(caveat.code == CAVEAT_CONFIG_HOME_RELOCATED for caveat in riders)
        by_system, bundled_read, shadow_broken = self._read_esde_catalogue()
        if shadow_broken:
            return _EsdeSnapshot(
                findings,
                (*findings, *_catalogue_unread_caveat(system), *riders),
                {},
                False,
                relocated,
                (),
            )
        sealed = () if bundled_read else (self._catalogue_sealed_caveat(system),)
        return _EsdeSnapshot(findings, None, by_system, bundled_read, relocated, (*sealed, *riders))

    def _systems_answer(self) -> tuple[SystemsAnswer, str | None]:
        """Every system the readable layers declare — stated as incomplete while sealed.

        No version travels back: ``settings.sh`` names none, and an
        arrangement nobody verified has no pin a version could drift from.
        """
        snapshot = self._esde_snapshot()
        if snapshot.refusal is not None:
            return SystemsAnswer(caveats=snapshot.refusal), None
        return (
            SystemsAnswer(
                tuple(sorted(snapshot.by_system)),
                (self._CATALOGUE_SOURCE,),
                (*snapshot.findings, *snapshot.tail),
            ),
            None,
        )

    def _catalogue_answer(
        self, system: str, *, content_path: str | None = None
    ) -> tuple[CatalogueAnswer, str | None]:
        """EmuDeck's own catalogue answer — one snapshot of the ES-DE sources.

        The contract this fills in is on
        :meth:`_CatalogueQueries.emulators_for`; what is EmuDeck's alone is
        where the answer comes from: the marker, the on-disk ES-DE layers and
        the system's gamelist, each read once here. An empty entry list in the
        sealed state is **not** "the frontend knows none" — the sealed caveat
        is the code that keeps those apart.
        """
        snapshot = self._esde_snapshot(system)
        if snapshot.refusal is not None:
            return CatalogueAnswer(caveats=snapshot.refusal), None
        anchor = (
            self._esde_system_dir(
                snapshot.by_system, system, bundled_read=snapshot.bundled_read, relocated=snapshot.relocated
            )
            if content_path is not None
            else _NO_ANCHOR_NEEDED
        )
        return (
            CatalogueAnswer(
                _entries_from(
                    self,
                    _declared_entries(snapshot.by_system, system),
                    self._gamelist_selections(system),
                    system_roms_dir=anchor.directory,
                    content_path=content_path,
                ),
                (self._CATALOGUE_SOURCE,),
                (*snapshot.findings, *snapshot.tail, *anchor.caveats),
            ),
            None,
        )

    def _rom_location_answer(self, system: str) -> tuple[RomPlacement, str | None]:
        """EmuDeck's ROM placement — the overlay's declaration, resolved ES-DE's way.

        An overlay-declared system answers fully: per ES-DE's merge semantics
        the overlay replaces a same-name bundled system entirely, so its
        ``<path>`` and ``<extension>`` are exactly what the frontend uses. A
        system the readable layers do not declare answers nothing *carrying
        the sealed caveat* — the declaration may sit in the sealed layer,
        which is a different claim from ``rom-path-undeclared``'s "the
        catalogue was read and declares none".
        """
        snapshot = self._esde_snapshot(system)
        if snapshot.refusal is not None:
            return RomPlacement(caveats=snapshot.refusal), None
        declaration = snapshot.by_system.get(system)
        resolved = self._esde_system_dir(
            snapshot.by_system, system, bundled_read=snapshot.bundled_read, relocated=snapshot.relocated
        )
        placement = RomPlacement(
            extensions=() if declaration is None else declaration.extensions,
            sources=(self._CATALOGUE_SOURCE, *resolved.sources),
            caveats=(*snapshot.findings, *snapshot.tail, *resolved.caveats),
        )
        if resolved.directory is None:
            return placement, None
        # Same rule as RetroDECK's: every resolution branch that carries a
        # caveat also answers directory=None, so a resolved directory has
        # nothing to drop here — and the same shared link view backs it.
        physical_dir, link_caveats = _link_view(self._machine, resolved.directory)
        return (
            _dc_replace(
                placement,
                dir=resolved.directory,
                physical_dir=physical_dir,
                caveats=(*snapshot.findings, *snapshot.tail, *link_caveats),
            ),
            None,
        )

    def _entry_caveats_for(self, spec: EmulatorSpec, content_path: str) -> tuple[Caveat, ...]:
        """What the ES-DE side says about *this* game being launched by *this* entry.

        The same question RetroDECK's entry route asks, over EmuDeck's
        sources, and it re-reads them — the handle is live, and the machine
        may have changed since the catalogue handed the entry out. The sealed
        caveat rides whenever the bundled layer stayed sealed: the entry came
        out of that partly-sealed catalogue, and the anchor the per-game check
        needs may be declared in the part nobody could read.

        Deliberately **not** :meth:`_esde_snapshot`: that would read the
        marker and the companion cfg a second time inside one placement query
        (:meth:`_query` already read both), and its refusal shapes carry the
        health findings this placement already states. Only the ES-DE files
        this check itself needs are read here.
        """
        if not self._esde_present():
            return (self._catalogue_absence(),)
        portable = self._relocation_caveat()
        relocation = () if portable is None else (portable,)
        by_system, bundled_read, shadow_broken = self._read_esde_catalogue()
        if shadow_broken:
            return (*_catalogue_unread_caveat(spec.system), *relocation)
        sealed = () if bundled_read else (self._catalogue_sealed_caveat(spec.system),)
        anchor = self._esde_system_dir(
            by_system, spec.system, bundled_read=bundled_read, relocated=bool(relocation)
        )
        override_label = (
            None
            if anchor.directory is None
            else _match_per_game(
                self._gamelist_selections(spec.system), content_path, system_roms_dir=anchor.directory
            )
        )
        if override_label is None or override_label == spec.label:
            return (*sealed, *relocation, *anchor.caveats)
        return (*sealed, *relocation, *anchor.caveats, _per_game_override_caveat(override_label, spec))

    def entry_savefile_location(
        self,
        spec: EmulatorSpec,
        entry_caveats: tuple[Caveat, ...] = (),
        *,
        content_path: str | None = None,
    ) -> SavefilePlacement:
        """The entry route behind :meth:`EmulatorEntry.savefile_location` — EmuDeck's wiring.

        The placement itself is the companion RetroArch's, exactly as the
        direct question answers it; what the entry adds is its own catalogue
        caveats and, when content is named, the per-game override check.
        """
        placement = _retroarch_savefile_location(
            self._machine,
            self._query(content_path=content_path, core_so=spec.core_so, extra_caveats=entry_caveats),
        )
        if content_path is None:
            return placement
        extra = self._entry_caveats_for(spec, content_path)
        return _dc_replace(placement, caveats=(*placement.caveats, *extra)) if extra else placement

    def entry_savestate_location(
        self,
        spec: EmulatorSpec,
        entry_caveats: tuple[Caveat, ...] = (),
        *,
        content_path: str | None = None,
    ) -> SavestatePlacement:
        """The savefile entry route's twin — same sources, the savestate keys."""
        placement = _retroarch_savestate_location(
            self._machine,
            self._query(content_path=content_path, core_so=spec.core_so, extra_caveats=entry_caveats),
        )
        if content_path is None:
            return placement
        extra = self._entry_caveats_for(spec, content_path)
        return _dc_replace(placement, caveats=(*placement.caveats, *extra)) if extra else placement

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

    def _query(
        self,
        *,
        content_path: str | None,
        core_so: str | None,
        extra_caveats: tuple[Caveat, ...] = (),
    ) -> _SaveQuery:
        """The placement question, over one read of the companion cfg."""
        settings, marker_issues = self._read_marker()
        global_cfg_path = self._companion_cfg_path()
        cfg = self._machine.read_text(global_cfg_path)
        health = self._health_from(settings, marker_issues, cfg.status)
        return _SaveQuery(
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
            extra_caveats=(*extra_caveats, *health.issues, *arrangement_caveats(self.kind)),
        )

    def savefile_location(self, *, content_path: str | None = None, core_so: str | None = None) -> SavefilePlacement:
        """Where EmuDeck's RetroArch keeps the save — resolved from the bare Flatpak cfg."""
        return _retroarch_savefile_location(
            self._machine, self._query(content_path=content_path, core_so=core_so)
        )

    def savestate_location(
        self, *, content_path: str | None = None, core_so: str | None = None
    ) -> SavestatePlacement:
        """Where EmuDeck's RetroArch keeps the savestates — the same cfg, the state keys.

        At the current pin both directions derive from ``savesPath``:
        ``RetroArch_setupSaves`` writes ``savestate_directory`` and
        ``savefile_directory`` as ``$savesPath/retroarch/{states,saves}`` into
        the global cfg (``emuDeckRetroArch.sh:222-230`` @ ``863ab69``), after
        symlinking those very paths into the Flatpak's own config tree — the
        cfg names the symlink, the bytes live behind it, and the placement's
        ``dir``/``physical_dir`` pair carries both truthfully. A prior
        generation (@ ``acc45fc``) hardcoded the states path into the Flatpak
        tree via ``RetroArch_maincfg.sh:3053`` — a writer that at the current
        pin has no caller in the Linux backend and targets an override file no
        launch path reads. The handle reads the live cfg either way; the
        installer history is context, not an input.
        """
        return _retroarch_savestate_location(
            self._machine, self._query(content_path=content_path, core_so=core_so)
        )

    def _firmware_context_from(
        self, settings: dict[str, str], marker_issues: tuple[Caveat, ...], cfg: ReadResult
    ) -> FirmwareContext:
        """The cfg is the truth here too: ``settings.sh`` names a ``biosPath``,
        but what RetroArch actually hands its cores is ``system_directory``.

        The companion cfg is read once and answers both questions asked of it —
        its text builds the context, its status decides the companion health
        finding — so the two can never describe different revisions of it. The
        marker snapshot arrives for the same reason: the caller read it once
        and every part of its answer describes that one revision.
        """
        return _retroarch_firmware_context(
            sandbox=self._sandbox(),
            global_text=cfg.text,
            cfg_label=RETROARCH_CFG,
            retroarch_config_dir=self._retroarch_config_dir(),
            findings=self._health_from(settings, marker_issues, cfg.status).issues,
            # ``settings.sh`` names no EmuDeck version, and an arrangement
            # nobody verified has no pin a version could drift from.
            arrangement_version=None,
        )

    def _read_firmware_context(self) -> FirmwareContext:
        settings, marker_issues = self._read_marker()
        cfg = self._machine.read_text(self._companion_cfg_path())
        return self._firmware_context_from(settings, marker_issues, cfg)

    def firmware_for_system(self, system: str, *, verify: bool = False) -> FirmwareAnswer:
        """Which emulators this EmuDeck's ES-DE offers for *system*, and what each wants.

        RetroDECK's route mirrored over this arrangement's sources: the ES-DE
        on disk is the enumeration, assembled from this query's own snapshot —
        marker, companion cfg and the on-disk ES-DE layers are each read once
        here and handed on. What is EmuDeck's alone is the catalogue's third
        state: the bundled layer is ordinarily sealed inside the AppImage, so
        the catalogue travels with its pinned sealed statement as the seam's
        ``hole`` — stated by the resolver on every answer the catalogue
        informs, while an id the readable layers do not declare answers empty
        as a look that failed, never as "no emulator covers this system".

        With no ES-DE on this disk the catalogue-less behavior stands
        unchanged: the derived enumeration, stated as derived. A broken
        resource shadow is the unreadable catalogue, exactly as RetroDECK's
        unreadable bundled layer is. And on every catalogue-informed answer
        the relocation suspicion and the marker cross-check ride adjacent to
        the catalogue-status statement, in the same order every catalogue
        answer pins (sealed, relocation, mismatch).
        """
        settings, marker_issues = self._read_marker()
        cfg = self._machine.read_text(self._companion_cfg_path())
        context = self._stated(self._firmware_context_from(settings, marker_issues, cfg))
        present = self._esde_present()
        if not present or context.root is None:
            # No ES-DE on this disk means the catalogue-less behavior stands;
            # and a root the context could not resolve refuses before any
            # catalogue could inform the answer — the resolver returns the
            # same empty answer either way, so the ES-DE sources are not read
            # for an answer that would discard them.
            return _resolve_for_system(self._machine, context, system=system, verify=verify)
        riders = self._riders(settings, present)
        by_system, bundled_read, shadow_broken = self._read_esde_catalogue()
        if shadow_broken:
            # The bundled layer is on disk (the resource shadow) and could not
            # be read or parsed: the resolver states the unreadable catalogue,
            # the same statement RetroDECK's unread bundled layer gets.
            catalogue = Catalogue(entries=(), read=False)
        else:
            catalogue = Catalogue(
                entries=_firmware_catalogue_entries(
                    self, by_system, system, self._gamelist_selections(system)
                ),
                hole=None if bundled_read else self._catalogue_sealed_caveat(system),
            )
        answer = _resolve_for_system(
            self._machine, context, system=system, catalogue=catalogue, verify=verify
        )
        # The riders ride only answers the catalogue informed: an own spelling
        # is answered from the cores on every arrangement, so the resolver
        # never looks at the catalogue for it.
        if not riders or system in SYSTEMS_WITHOUT_CATALOGUE_ID:
            return answer
        # Adjacent to the catalogue-status statement, which — when one exists
        # (the hole, or the unreadable statement of a broken shadow) — is the
        # first caveat the resolver appends after the context's own; the
        # resolver's answer is (*context.caveats, *its own), per its contract.
        index = len(context.caveats) + (1 if shadow_broken or catalogue.hole is not None else 0)
        return _firmware_with_caveats(
            answer, (*answer.caveats[:index], *riders, *answer.caveats[index:])
        )


class _RetroArchInstall(_FirmwareQueries, _CatalogueQueries):
    """Shared behavior for a bare RetroArch install (the Flatpak or a native one).

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
            CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE,
            "a bare RetroArch install ships no frontend catalogue, so there is no launch entry to "
            "answer with — name the core yourself with savefile_location(core_so=...)",
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

    def _health_from(self, cfg_status: ReadStatus) -> Health:
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

        ``_app_id`` is ``None`` for :class:`BareRetroArchNative`: it writes its cfg
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

    def _query(self, *, content_path: str | None, core_so: str | None) -> _SaveQuery:
        """The placement question, over one read of this install's cfg."""
        cfg = self._machine.read_text(self._cfg_path())
        health = self._health_from(cfg.status)
        return _SaveQuery(
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
        )

    def savefile_location(self, *, content_path: str | None = None, core_so: str | None = None) -> SavefilePlacement:
        """Where this RetroArch install keeps the save for *content_path* under *core_so*."""
        return _retroarch_savefile_location(
            self._machine, self._query(content_path=content_path, core_so=core_so)
        )

    def savestate_location(
        self, *, content_path: str | None = None, core_so: str | None = None
    ) -> SavestatePlacement:
        """Where this RetroArch install keeps the savestates for *content_path*.

        A bare install carries the upstream compile-time defaults, and there
        ``sort_savestates_enable`` is **true** (``config.def.h:983``) exactly as
        its savefile twin is — so an unconfigured install sorts states into
        per-``library_name`` subdirectories of ``states`` under the config tree.
        """
        return _retroarch_savestate_location(
            self._machine, self._query(content_path=content_path, core_so=core_so)
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
            # A bare install states no version of anything but RetroArch, and
            # this arrangement carries no verified pin to compare one against.
            arrangement_version=None,
        )


class BareRetroArchFlatpak(_RetroArchInstall):
    """The ``org.libretro.RetroArch`` Flatpak install."""

    kind = "bare_retroarch_flatpak"
    kinds = ("bare_retroarch_flatpak",)
    _app_id = RETROARCH_FLATPAK_APP_ID

    def __init__(self, home: str, machine: Machine) -> None:
        super().__init__(home, machine, STANDALONE_FLATPAK_CFG_SUFFIX)


class BareRetroArchNative(_RetroArchInstall):
    """A native ``~/.config/retroarch`` install."""

    kind = "bare_retroarch_native"
    kinds = ("bare_retroarch_native",)

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

    def savefile_location(
        self, *, content_path: str | None = None, core_so: str | None = None
    ) -> SavefilePlacement: ...

    def savestate_location(
        self, *, content_path: str | None = None, core_so: str | None = None
    ) -> SavestatePlacement: ...

    def systems(self) -> SystemsAnswer: ...

    def emulators_for(self, system: str, *, content_path: str | None = None) -> CatalogueAnswer: ...

    def rom_location(self, system: str) -> RomPlacement: ...

    def firmware_for_core(self, core_so: str, *, verify: bool = False) -> FirmwareAnswer: ...

    def firmware_for_system(self, system: str, *, verify: bool = False) -> FirmwareAnswer: ...

    def firmware_inventory(self, *, verify: bool = False) -> FirmwareAnswer: ...

    def identify_firmware(
        self, *, md5: str | None = None, sha1: str | None = None, size: int | None = None
    ) -> FirmwareIdentification: ...
