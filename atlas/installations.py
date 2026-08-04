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
from typing import Any, Callable, Protocol, runtime_checkable

from dataclasses import dataclass, replace as _dc_replace

from atlas.esde import (
    KIND_LIBRETRO,
    EmulatorSpec,
    GamelistSelections,
    merge_layers,
    parse_es_systems,
    parse_gamelist,
)
from atlas.firmware import (
    CAVEAT_CORE_DIR_UNRESOLVED,
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
from atlas.machine import KIND_DIRECTORY, KIND_FILE, KIND_MISSING, Machine
from atlas.oddities import lookup_audit, lookup_card
from atlas.placement import (
    CAVEAT_CARD_GENERATION_MISMATCH,
    CAVEAT_CARD_MODE_UNCONFIRMED,
    CAVEAT_DEAD_SYMLINK,
    CAVEAT_SORTED_DIR_UNCREATABLE,
    CAVEAT_CORE_SUSPECT,
    CAVEAT_CORE_UNAUDITED,
    CAVEAT_CORE_UNQUERYABLE,
    CAVEAT_INVALID_SAVE_DIRECTORY,
    CAVEAT_UNVERIFIED_VERSION,
    CAVEAT_PER_GAME_OVERRIDE,
    CAVEAT_PER_GAME_OVERRIDES_PRESENT,
    CAVEAT_FILENAMES_UNVERIFIED,
    CAVEAT_HEALTH,
    CAVEAT_NO_CORE,
    CAVEAT_SORTED_DIR_MISSING,
    CAVEAT_SYSTEM_DIR_UNSET,
    CAVEAT_UNKNOWN_OPTION_VALUE,
    ROOT_CONTENT_DIRECTORY,
    ROOT_SYSTEM_DIRECTORY,
    UNKNOWN_FILE_SET,
    UNRESOLVED_STANDALONE,
    Caveat,
    FileSet,
    Granularity,
    SavePlacement,
    Unresolved,
    build_save_placement,
)
from atlas.retroarch_cfg import (
    UPSTREAM_DEFAULTS,
    LayoutDefaults,
    expand_home,
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
    the ``HEALTH_ISSUE_*`` constants; ``data`` carries the affected path or
    read status. Handles never hide a present-but-broken installation — they
    report it with the issues attached.
    """

    issues: tuple[Caveat, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)


def _health_caveats(health: Health) -> tuple[Caveat, ...]:
    """Health issues restated as placement caveats — one per issue, structured."""
    return tuple(
        Caveat(
            CAVEAT_HEALTH,
            f"installation health: {issue.message}",
            {"issue": issue.code, **issue.data},
        )
        for issue in health.issues
    )

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
        if machine.path_kind(candidate) != "missing":
            return candidate
    return None


def _cfg_bool(raw: str | None, default: bool) -> bool:
    """Interpret a raw cfg value as RetroArch's config_get_bool does."""
    if raw is None:
        return default
    stripped = raw.strip()
    return stripped == "1" or stripped.lower() == "true"


_MAX_LINK_HOPS = 40


def _resolve_symlink_chain(machine: Machine, path: str) -> tuple[str, list[tuple[str, str]]]:
    """Resolve symlink components in *path* through the seam, kernel-style.

    Walks components left to right via ``readlink``, splicing targets in
    (relative targets against the link's directory), with a hop limit against
    cycles. Returns the fully resolved path and every ``(link, target)``
    traversed — an empty list means no symlink was involved.
    """
    links: list[tuple[str, str]] = []
    current = path
    for _ in range(_MAX_LINK_HOPS):
        parts = current.split("/")
        replaced = False
        for i in range(2, len(parts) + 1):
            prefix = "/".join(parts[:i])
            target = machine.readlink(prefix)
            if target is not None:
                links.append((prefix, target))
                if not target.startswith("/"):
                    target = os.path.normpath(os.path.join(os.path.dirname(prefix), target))
                rest = "/".join(parts[i:])
                current = target + ("/" + rest if rest else "")
                replaced = True
                break
        if not replaced:
            break
    return current, links


def _link_view(machine: Machine, directory: str) -> tuple[str | None, tuple[Caveat, ...]]:
    """The link-resolved view of a final directory (REVIEW M7).

    Returns ``(physical_dir, caveats)``: ``physical_dir`` is the fully
    resolved backing directory when *directory* traverses live symlinks —
    RetroDECK's ``dir_prep`` pattern makes the emulator-side path and the
    physical path two truthful answers to different questions. A traversal
    that ends nowhere yields a ``dead-symlink`` caveat instead: the
    emulator-side directory is dead, and writing there will fail.
    """
    resolved, links = _resolve_symlink_chain(machine, directory)
    if not links:
        return None, ()
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


def _resolve_chain_key(layer_texts: list[str], key: str) -> str | None:
    """Resolve one raw cfg key across the layer texts — later layers win."""
    value: str | None = None
    for text in layer_texts:
        parsed = parse_cfg_text(text)
        if key in parsed:
            value = parsed[key]
    return value


def _core_options_value(
    machine: Machine,
    layer_texts: list[str],
    *,
    home: str,
    retroarch_config_dir: str,
    override_config_dir: str,
    library_name: str | None,
    content_dir_name: str | None,
    rom_stem: str | None,
    option_key: str,
    option_default: str,
    game_specific_options: bool,
) -> tuple[str, str, str, bool]:
    """Read a core option the way RetroArch does — first existing file is THE source.

    Priority (``runloop.c`` ``validate_per_core_options``): game ``.opt``,
    folder ``.opt``, per-core ``.opt`` (when ``global_core_options`` is off),
    then the global options file (``core_options_path`` or
    ``retroarch-core-options.cfg``). A key absent from the governing file falls
    back to the core default — it does not fall through to another file.

    Returns ``(value, provenance, options_file, unconfirmed)``:
    ``options_file`` is the file a caller would edit to change the option, and
    ``unconfirmed`` is true when a governing file whose path needs
    ``library_name`` could exist but cannot be checked (the core was
    unqueryable) — the returned value may then not be the effective one.
    """
    candidates: list[str] = []
    if library_name and game_specific_options:
        if rom_stem:
            candidates.append(os.path.join(override_config_dir, library_name, f"{rom_stem}.opt"))
        if content_dir_name:
            candidates.append(os.path.join(override_config_dir, library_name, f"{content_dir_name}.opt"))
    global_flag = _resolve_chain_key(layer_texts, "global_core_options")
    # Upstream default is false (config.def.h DEFAULT_GLOBAL_CORE_OPTIONS).
    per_core_options = not _cfg_bool(global_flag, False)
    unconfirmed = library_name is None and (game_specific_options or per_core_options)
    if library_name and per_core_options:
        candidates.append(os.path.join(override_config_dir, library_name, f"{library_name}.opt"))
    custom_path = _resolve_chain_key(layer_texts, "core_options_path")
    global_file = expand_home(custom_path, home=home) if custom_path is not None else None
    if global_file is None:
        global_file = os.path.join(retroarch_config_dir, "retroarch-core-options.cfg")
    candidates.append(global_file)

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


def _retroarch_save_location(
    machine: Machine,
    *,
    home: str,
    global_cfg_path: str,
    global_text: str | None,
    cfg_label: str,
    override_config_dir: str,
    defaults: LayoutDefaults,
    content_path: str | None,
    core_so: str | None,
    core_path_resolver: Callable[[str], str | None],
    arrangement: str,
    arrangement_version: str | None,
    extra_sources: tuple[str, ...] = (),
    extra_caveats: tuple[Caveat, ...] = (),
) -> SavePlacement:
    """The shared resolver: global cfg → override chain → placement, all live.

    Reads the same four layers RetroArch reads (``configuration.c:7095``),
    resolves ``library_name`` from the core binary when a core is named, and
    observes the file set for existing saves. Every degradation is a stated
    caveat, never a silent guess. ``global_text`` is the global cfg's content,
    read exactly once by the caller — one query derives every decision from
    one snapshot of each source (REVIEW M4).
    """
    caveats = list(extra_caveats)
    sources_extra = list(extra_sources)

    content_dir_path = content_dir_name = rom_stem = None
    if content_path is not None:
        content_dir_path, content_dir_name, rom_stem = _split_content_path(content_path)

    library_name: str | None = None
    info = None
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
                Caveat(
                    CAVEAT_CORE_UNQUERYABLE,
                    f"core {core_so!r} could not be queried — library_name unknown, per-core overrides not checked",
                    {"core_so": core_so},
                )
            )
    else:
        caveats.append(
            Caveat(
                CAVEAT_NO_CORE,
                "no core given — per-core overrides and save-behaviour rule cards not checked: this answer "
                "assumes a standard core, and a card-carrying core (e.g. one rooted in system_directory, like "
                "Flycast) keeps its saves elsewhere entirely",
            )
        )

    global_layer = [global_text] if global_text is not None else []

    # Gates read from the global cfg (an override cannot enable itself):
    # auto_overrides_enable and game_specific_options both default true
    # (config.def.h); rgui_config_directory relocates the application config
    # dir used for override .cfg and option .opt files.
    auto_overrides = _cfg_bool(_resolve_chain_key(global_layer, "auto_overrides_enable"), True)
    game_specific_options = _cfg_bool(_resolve_chain_key(global_layer, "game_specific_options"), True)
    rgui_dir_raw = _resolve_chain_key(global_layer, "rgui_config_directory")
    rgui_dir = expand_home(rgui_dir_raw, home=home) if rgui_dir_raw is not None else None
    if rgui_dir is not None:
        override_config_dir = rgui_dir
        sources_extra.append(f'retroarch.cfg: rgui_config_directory = "{rgui_dir_raw}"')

    overrides: list[tuple[str, str]] = []
    if not auto_overrides:
        sources_extra.append('retroarch.cfg: auto_overrides_enable = "false" — override files not applied')
    elif library_name is not None:
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
            text = machine.read_text(path).text
            if text is not None:
                overrides.append((label, text))
    layout = resolve_save_layout(
        global_text,
        home=home,
        cfg_label=cfg_label,
        defaults=defaults,
        overrides=overrides,
    )
    layer_texts = [t for t in (global_text, *(text for _, text in overrides)) if t is not None]

    # The RetroArch platform default saves dir — 'saves' under the config tree
    # (platform_unix.c:1844) — is the effective root whenever the key is unset,
    # reset, or points at anything that is not an existing directory: RetroArch
    # only applies a configured path when path_is_directory() succeeds
    # (configuration.c:6916), otherwise the prior effective (default) stays.
    platform_default_dir = os.path.join(os.path.dirname(global_cfg_path), "saves")
    if layout.savefile_directory is not None and machine.path_kind(layout.savefile_directory) != KIND_DIRECTORY:
        caveats.append(
            Caveat(
                CAVEAT_INVALID_SAVE_DIRECTORY,
                f"configured savefile_directory {layout.savefile_directory!r} is not an existing "
                f"directory — RetroArch rejects it and keeps the platform default "
                f"{platform_default_dir!r} (configuration.c:6916)",
                {"configured": layout.savefile_directory, "effective": platform_default_dir},
            )
        )
        # When the rejection is a dead symlink, say why (REVIEW M7).
        caveats.extend(_link_view(machine, layout.savefile_directory)[1])
        layout = _dc_replace(layout, savefile_directory=None)

    # Rule cards: cores whose save behaviour deviates from the standard rule.
    # The card names the governing option; its current value is read live.
    so_basename = os.path.basename(core_so) if core_so is not None else None
    card = lookup_card(so_basename=so_basename, library_name=library_name)
    granularity: Granularity | None = None
    card_mode = None

    # Feature detection — the generation question made observable (the LRPS2
    # lesson): when the probe captured which options this core REGISTERS, the
    # card's governing option key decides applicability, not a version string.
    # Key registered → the card generation is confirmed by evidence. Key not
    # registered → the card describes a different generation and is NOT
    # applied; stale knowledge with a warning would still be a guess. Options
    # not captured (probe limitation, core registers later) → unknown, and the
    # version comparison below keeps doing its job.
    registered_options = info.options if info is not None else None
    live_option = None
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
            sources_extra.append(
                f"feature-detected: core registers {card.option_key!r} (default "
                f"{live_option.default!r}, values {list(live_option.values)}) — card generation "
                "confirmed by observation, not by version comparison"
            )
    if card is None and so_basename is not None:
        # A missing card is not evidence the standard rule is complete — the
        # audit verdict decides how loudly to say so (REVIEW H7).
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
    if card is not None:
        # Verification is explicit and fails closed (REVIEW M3): the states
        # are verified, drifted, runtime-version-unknown, never-verified —
        # missing live evidence is never treated as successful verification.
        audit = lookup_audit(card.key)
        verified = audit.verified.get(arrangement) if audit is not None else None
        live_core_version = info.library_version if core_so is not None and info is not None else None
        if verified is None:
            caveats.append(
                Caveat(
                    CAVEAT_UNVERIFIED_VERSION,
                    f"rule card '{card.key}' was never verified on a {arrangement} arrangement — "
                    "the behaviour it describes may not hold here",
                    {"card": card.key, "arrangement": arrangement, "verification": "never-verified"},
                )
            )
        else:
            drift: dict[str, str] = {}
            missing: list[str] = []
            if verified.version is not None:
                if arrangement_version is None:
                    missing.append("arrangement_version")
                elif verified.version != arrangement_version:
                    drift["arrangement_verified"] = verified.version
                    drift["arrangement_live"] = arrangement_version
            if verified.core_library_version is not None:
                if live_core_version is None:
                    missing.append("core_library_version")
                elif verified.core_library_version != live_core_version:
                    drift["core_verified"] = verified.core_library_version
                    drift["core_live"] = live_core_version
            if (drift or missing) and live_option is not None:
                # Feature detection outranks the version comparison: the
                # governing option is observably registered, so a differing or
                # unreadable version record is supplementary info, not an
                # alarm (the false-alarm class the version check produced).
                detail = str(drift) if drift else f"{', '.join(missing)} unavailable"
                sources_extra.append(
                    f"rule card '{card.key}': version records differ from this machine ({detail}), but "
                    f"the governing option is feature-confirmed — the decision falls on observed evidence"
                )
            elif drift:
                data = {"card": card.key, "arrangement": arrangement, "verification": "drifted", **drift}
                if missing:
                    data["missing"] = ", ".join(missing)
                caveats.append(
                    Caveat(
                        CAVEAT_UNVERIFIED_VERSION,
                        f"rule card '{card.key}' was verified against different versions than this "
                        f"machine runs ({drift}) — behaviour may have drifted",
                        data,
                    )
                )
            elif missing:
                caveats.append(
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
                    )
                )
            else:
                sources_extra.append(
                    f"rule card '{card.key}': verified on {arrangement} "
                    f"{verified.version or '?'} (core {verified.core_library_version or '?'}, "
                    f"{verified.date or 'undated'})"
                )
    if card is not None and card.option_key is None:
        card_mode = card.modes.get("always")
        if card_mode is not None:
            granularity = Granularity(
                value=card_mode.granularity,
                option_key=None,
                option_value=None,
                option_source=f"rule card '{card.key}': fixed behaviour (no governing option)",
                options_file=None,
                alternatives=(),
            )
    elif card is not None:
        # The registered default is a live read and outranks the card's
        # shipped-generation copy — feature detection makes option defaults
        # machine facts instead of world knowledge.
        effective_default = card.option_default
        if live_option is not None and live_option.default is not None:
            effective_default = live_option.default
        opt_value, opt_source, options_file, opt_unconfirmed = _core_options_value(
            machine,
            layer_texts,
            home=home,
            retroarch_config_dir=os.path.dirname(global_cfg_path),
            override_config_dir=override_config_dir,
            library_name=library_name,
            content_dir_name=content_dir_name,
            rom_stem=rom_stem,
            option_key=card.option_key or "",
            option_default=effective_default or "",
            game_specific_options=game_specific_options,
        )
        card_mode = card.modes.get(opt_value)
        if card_mode is None:
            live_registered_value = live_option is not None and opt_value in live_option.values
            fallback_mode = card.modes.get(effective_default or "")
            if live_registered_value or fallback_mode is None:
                # Either the live core legitimately offers a value the card
                # does not know, or even the effective default has no card
                # mode — value-level generation drift. Applying any other
                # mode would guess; the card steps aside.
                caveats.append(
                    Caveat(
                        CAVEAT_CARD_GENERATION_MISMATCH,
                        f'core option {card.option_key} = "{opt_value}" cannot be interpreted by rule '
                        f"card '{card.key}' — the card lags this core's generation; the configured save "
                        "behaviour is unknown until re-audited, and the standard answer below may miss "
                        "the real save stack",
                        {"card": card.key, "option_key": card.option_key or "", "value": opt_value},
                    )
                )
                card = None
                card_mode = None
            else:
                # RetroArch's option manager keeps the core-declared default
                # when a persisted value is invalid — it does not fall back to
                # the standard rule (REVIEW M1). With captured definitions the
                # invalidity itself is confirmed against the live value set.
                card_mode = fallback_mode
                caveats.append(
                    Caveat(
                        CAVEAT_UNKNOWN_OPTION_VALUE,
                        f'core option {card.option_key} = "{opt_value}" is not a value the rule card '
                        f"knows — applying the core default mode {effective_default!r} as RetroArch would",
                        {"card": card.key, "option_key": card.option_key or "", "value": opt_value},
                    )
                )
                opt_value = effective_default or opt_value
        if card is not None and opt_unconfirmed and card_mode is not None:
            caveats.append(
                Caveat(
                    CAVEAT_CARD_MODE_UNCONFIRMED,
                    f"a game/folder/per-core options file keyed by library_name could govern "
                    f"{card.option_key} but cannot be checked (core unqueryable) — the applied mode "
                    f"{opt_value!r} may not be the effective one",
                    {"card": card.key, "option_key": card.option_key or "", "applied": opt_value},
                )
            )
        if card is not None and card_mode is not None:
            granularity = Granularity(
                value=card_mode.granularity,
                option_key=card.option_key,
                option_value=opt_value,
                option_source=opt_source,
                options_file=options_file,
                alternatives=tuple(
                    (value, mode.granularity) for value, mode in card.modes.items() if value != opt_value
                ),
            )

    if card is not None and card_mode is not None and card_mode.root == ROOT_SYSTEM_DIRECTORY:
        card_sources = list(sources_extra)
        raw_system = _resolve_chain_key(layer_texts, "system_directory")
        system_dir = expand_home(raw_system, home=home) if raw_system is not None else None
        needs: tuple[str, ...] = ()
        if system_dir is None:
            base = "<system_directory>"
            needs = ("system_directory",)
            caveats.append(
                Caveat(
                    CAVEAT_SYSTEM_DIR_UNSET,
                    "system_directory is unset in the configs — its RetroArch default is not resolved yet",
                )
            )
        else:
            base = system_dir
            card_sources.append(f'{cfg_label} chain: system_directory = "{raw_system}"')
        directory = os.path.join(base, card_mode.subdir) if card_mode.subdir else base
        card_sources.append(f"rule card '{card.key}': core keeps saves under system_directory — {card.provenance}")
        declared = _card_files(card_mode.files, rom_stem) if card_mode.files is not None else None
        if declared is None:
            fs = UNKNOWN_FILE_SET
        elif needs:
            fs = FileSet("declared", declared, f"declared by rule card '{card.key}'", complete=card_mode.complete)
        else:
            # Observation candidates may be wider than the declared defaults —
            # e.g. Flycast's slot-2 VMUs exist only when configured (REVIEW M2).
            observe = _card_files(card_mode.observe, rom_stem) if card_mode.observe is not None else None
            candidates = observe if observe is not None else declared
            present = tuple(f for f in candidates if machine.path_kind(os.path.join(directory, f)) == KIND_FILE)
            if present:
                fs = FileSet(
                    "observed", present, f"observed on the machine: {directory}", complete=card_mode.complete
                )
            else:
                fs = FileSet(
                    "declared",
                    declared,
                    f"declared by rule card '{card.key}' (none present yet)",
                    complete=card_mode.complete,
                )
        physical_dir = None
        if not needs:
            physical_dir, link_caveats = _link_view(machine, directory)
            caveats.extend(link_caveats)
        return SavePlacement(
            dir=directory,
            root_kind=ROOT_SYSTEM_DIRECTORY,
            needs=needs,
            file_set=fs,
            sources=tuple(card_sources),
            caveats=tuple(caveats),
            granularity=granularity,
            physical_dir=physical_dir,
        )

    if card is not None and card_mode is not None and granularity is not None and card_mode.files is None:
        caveats.append(
            Caveat(
                CAVEAT_FILENAMES_UNVERIFIED,
                f"rule card '{card.key}': mode {granularity.option_value!r} places per-game files under the "
                "standard directory, but the filename scheme is unverified — file names not stated",
                {"card": card.key, "mode": granularity.option_value or ""},
            )
        )

    file_set = UNKNOWN_FILE_SET
    placement = build_save_placement(
        layout=layout,
        platform_default_dir=platform_default_dir,
        content_dir_path=content_dir_path,
        content_dir_name=content_dir_name,
        library_name=library_name,
        extra_sources=tuple(sources_extra),
    )

    final_dir = placement.dir
    fallback_dir: str | None = None
    physical_dir: str | None = None
    final_sources = list(placement.sources)
    if not placement.needs:
        # A sorted directory that does not exist yet is a CONDITIONAL result:
        # RetroArch creates it on first save and silently reverts to the
        # unsorted root when creation fails (runloop.c:8844). A file in the
        # way makes the failure certain — then the fallback IS the answer;
        # anything else keeps the intended dir with a structural fallback
        # (REVIEW H5).
        if placement.root_kind == ROOT_CONTENT_DIRECTORY:
            effective_root = content_dir_path
        else:
            effective_root = layout.savefile_directory or platform_default_dir
        if effective_root is not None and final_dir != effective_root:
            dir_kind = machine.path_kind(final_dir)
            if dir_kind == KIND_FILE:
                caveats.append(
                    Caveat(
                        CAVEAT_SORTED_DIR_UNCREATABLE,
                        f"sorted directory {final_dir} is blocked by an existing file — RetroArch "
                        f"cannot create it and reverts to {effective_root} (runloop.c:8844)",
                        {"intended": final_dir, "effective": effective_root},
                    )
                )
                final_dir = effective_root
            elif dir_kind != KIND_DIRECTORY:
                fallback_dir = effective_root
                caveats.append(
                    Caveat(
                        CAVEAT_SORTED_DIR_MISSING,
                        f"sorted directory {final_dir} does not exist yet — RetroArch creates it on first save, "
                        f"and silently reverts to {effective_root} if creation fails (runloop.c:8844)",
                        {"dir": final_dir, "fallback_dir": effective_root},
                    )
                )
        if card_mode is not None and card_mode.subdir and card_mode.root != ROOT_SYSTEM_DIRECTORY:
            # A card core may nest its own subtree under the effective save
            # directory: GET_SAVE_DIRECTORY hands the core the redirected
            # (sorted, fallback-resolved) dir (runloop.c:2001, set at
            # runloop.c:8977), and the core appends its subdir to whatever it
            # received — so the subdir follows the fallback too.
            final_dir = os.path.join(final_dir, card_mode.subdir)
            if fallback_dir is not None:
                fallback_dir = os.path.join(fallback_dir, card_mode.subdir)
            if card is not None:
                final_sources.append(
                    f"rule card '{card.key}': core nests its saves under '{card_mode.subdir}/' in the "
                    f"save directory — {card.provenance}"
                )
        if rom_stem is not None:
            content_basename = os.path.basename(content_path) if content_path else None
            # Literal observation: ROM names routinely carry glob
            # metacharacters ('[', ']') — escape them so '[' matches '['
            # (REVIEW M2). RetroArch's own bookkeeping next to saves is
            # filtered with a source citation: the disk-control index
            # '<stem>.ldci' (disk_index_file.c:201-249, file_path_special.h:83)
            # is not save data.
            pattern = os.path.join(_glob_escape(final_dir), _glob_escape(rom_stem) + ".*")
            companions = {f"{rom_stem}.ldci"}
            matches = [
                m
                for m in machine.glob(pattern)
                # In content-dir mode the ROM shares the save's directory and
                # stem — the content file itself is never part of the save set.
                if os.path.basename(m) != content_basename and os.path.basename(m) not in companions
            ]
            declared = None
            if card is not None and card_mode is not None and card_mode.files is not None:
                declared = _card_files(card_mode.files, rom_stem)
            if matches:
                observed = tuple(sorted(os.path.basename(m) for m in matches))
                complete = (
                    card_mode is not None
                    and card_mode.complete
                    and declared is not None
                    and set(observed) <= set(declared)
                )
                file_set = FileSet(
                    state="observed",
                    files=observed,
                    source=f"observed on the machine: {final_dir}",
                    complete=complete,
                )
            elif declared is not None and card is not None:
                file_set = FileSet(
                    state="declared",
                    files=declared,
                    source=f"declared by rule card '{card.key}' (none present yet)",
                    complete=card_mode.complete if card_mode is not None else False,
                )
            else:
                file_set = FileSet(
                    state="unknown",
                    files=(),
                    source=f"no files present at {final_dir} — file set not stated (never guessed)",
                )
        physical_dir, link_caveats = _link_view(machine, final_dir)
        caveats.extend(link_caveats)

    return SavePlacement(
        dir=final_dir,
        root_kind=placement.root_kind,
        needs=placement.needs,
        file_set=file_set,
        sources=tuple(final_sources),
        caveats=tuple(caveats),
        granularity=granularity,
        fallback_dir=fallback_dir,
        physical_dir=physical_dir,
    )


def _cfg_directory(
    machine: Machine, parsed: dict[str, str], key: str, *, home: str, app_id: str | None
) -> str | None:
    """Resolve a cfg directory key to a host directory that exists, or ``None``.

    The configured value may point into a Flatpak sandbox (``/app/...``); a
    caller running outside it needs the deployment path instead. ``None`` means
    unset, reset, unresolvable, or not an existing directory — one honest miss,
    never a path handed out as if it were usable.
    """
    raw = parsed.get(key)
    if raw is None:
        return None
    expanded = expand_home(raw, home=home)
    if expanded is None:
        return None
    resolved = _flatpak_host_path(machine, home, app_id, expanded) if app_id is not None else expanded
    if resolved is None or machine.path_kind(resolved) != KIND_DIRECTORY:
        return None
    return resolved


def _retroarch_firmware_context(
    machine: Machine,
    *,
    home: str,
    app_id: str | None,
    global_text: str | None,
    cfg_label: str,
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
    parsed = parse_cfg_text(global_text) if global_text is not None else {}
    caveats: list[Caveat] = []
    sources: list[str] = []

    raw_system = parsed.get("system_directory")
    root = expand_home(raw_system, home=home) if raw_system is not None else None
    if root is None:
        caveats.append(
            Caveat(
                CAVEAT_SYSTEM_DIR_UNSET,
                "system_directory is unset in the configs — its RetroArch default is not resolved yet, so "
                "there is no root to check firmware against",
            )
        )
    else:
        sources.append(f'{cfg_label}: system_directory = "{raw_system}"')
        if machine.path_kind(root) != KIND_DIRECTORY:
            caveats.append(
                Caveat(
                    CAVEAT_FIRMWARE_ROOT_MISSING,
                    f"the configured system_directory {root} is not an existing directory — every declared "
                    "file below is missing because the whole firmware root is gone, not one file at a time",
                    {"path": root},
                )
            )

    info_dir = _cfg_directory(machine, parsed, "libretro_info_path", home=home, app_id=app_id)
    core_dir = _cfg_directory(machine, parsed, "libretro_directory", home=home, app_id=app_id)
    cores: tuple[CoreDeclarations, ...] = ()
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
        cores = read_core_declarations(machine, info_dir, core_dir=core_dir)

    return FirmwareContext(
        root=root,
        cores=cores,
        hashes=load_hashes(),
        # An empty core list means "this installation ships none" only if the
        # directory holding them was actually reached. Otherwise atlas never
        # looked, and must not turn that into a statement about the machine.
        cores_read=info_dir is not None,
        sources=tuple(sources),
        caveats=tuple(caveats),
    )


class _FirmwareQueries:
    """The four firmware entry points, over one live context per query.

    Every handle answers the same four questions; only the way its context is
    assembled differs. An installation whose frontend catalogue can enumerate a
    system's emulators overrides :meth:`firmware_for_system` to pass it.
    """

    _machine: Machine

    def _firmware_context(self) -> FirmwareContext:
        raise NotImplementedError  # pragma: no cover - every handle supplies one

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
    the file set is then honestly unknown rather than a template guess.
    """
    resolved: list[str] = []
    for name in files:
        if "<rom_stem>" in name:
            if rom_stem is None:
                return None
            name = name.replace("<rom_stem>", rom_stem)
        resolved.append(name)
    return tuple(resolved)


def _match_per_game(selections: GamelistSelections, content_path: str) -> str | None:
    """Match a content path against per-game ``altemulator`` entries, path-aware.

    Gamelist paths are relative to the system's ROM directory (``./Name.ext``,
    or ``./Folder`` for multi-disc directory entries) and may contain
    subdirectories — ``./USA/Game.iso`` and ``./Japan/Game.iso`` are distinct
    games. A gamelist path matches when the content path (or, for directory
    entries, the content's parent directory) ends with it as a whole path
    suffix.
    """
    content = content_path.replace("\\", "/")
    parent = os.path.dirname(content)
    for rel_path, label in selections.per_game.items():
        suffix = "/" + rel_path
        if content.endswith(suffix) or parent.endswith(suffix):
            return label
    return None


class EmulatorEntry:
    """One catalogue entry — an emulator that can launch one system, as configured.

    Wraps an :class:`~atlas.esde.EmulatorSpec` and the installation it belongs
    to, so the entry can answer placement questions with its core always known —
    the ``no-core`` caveat class does not exist on this path.
    """

    def __init__(
        self, installation: "RetroDeck", spec: EmulatorSpec, caveats: tuple[Caveat, ...] = ()
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


class RetroDeck(_FirmwareQueries):
    """A RetroDECK installation — cfg is the truth, ``retrodeck.json`` is context.

    The handle is *live*: it stores only its identity (home) and the machine
    seam. Every query re-reads the governing sources — each exactly once — and
    derives all decisions from that one snapshot, so a concurrent config edit
    can never mix two revisions inside one answer (REVIEW M4).
    """

    kind = "retrodeck"
    kinds = ("retrodeck",)
    _APP_ID = "net.retrodeck.retrodeck"

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
        if result.status == "missing":
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
        except (json.JSONDecodeError, ValueError):
            data = None
        if not isinstance(data, dict):
            return {}, (
                Caveat(HEALTH_ISSUE_MARKER_INVALID, f"marker {path} is not a JSON object", {"path": path}),
            )
        return data, ()

    def _config_path(self, config: dict[str, Any], key: str, fallback_subdir: str) -> tuple[str, str]:
        """Resolve a RetroDECK path and its provenance from a marker snapshot.

        A sub-path key that is unset falls back under the *resolved* root
        (``rd_home_path`` or its own ``~/retrodeck`` fallback) — RetroDECK
        lays its tree out under the home it was pointed at, so the honest
        default follows the configured root, not a hard-coded one.
        """
        paths = config.get("paths")
        if isinstance(paths, dict):
            value = paths.get(key, "")
            if value:
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

    def _core_path_in(self, global_text: str | None, core_so: str) -> str | None:
        """Resolve a core ``.so`` basename against a cfg snapshot's ``libretro_directory``.

        The configured value points into the sandbox (``/app/...``); translate it
        to the host deployment. ``None`` when nothing resolvable — never a guess.
        """
        if global_text is None:
            return None
        raw = parse_cfg_text(global_text).get("libretro_directory", "")
        if not raw:
            return None
        raw = expand_home(raw, home=self._home) or raw
        cores_dir = _flatpak_host_path(self._machine, self._home, self._APP_ID, raw)
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
        bundled_path = _flatpak_host_path(self._machine, self._home, self._APP_ID, self._ESDE_BUNDLED_SANDBOX)
        if bundled_path is not None:
            text = self._machine.read_text(bundled_path).text
            if text is not None:
                bundled = parse_es_systems(text, source="es_systems.xml (bundled)")
                read = True
        custom: dict[str, tuple[EmulatorSpec, ...]] = {}
        custom_path = os.path.join(root, "ES-DE", "custom_systems", "es_systems.xml")
        custom_text = self._machine.read_text(custom_path).text
        if custom_text is not None:
            custom = parse_es_systems(custom_text, source="es_systems.xml (custom_systems overlay)")
            read = True
        return merge_layers(bundled, custom), read

    def _catalogue(self, root: str) -> dict[str, tuple[EmulatorSpec, ...]]:
        return self._read_catalogue(root)[0]

    def systems(self) -> tuple[str, ...]:
        """Every system the catalogue declares, sorted."""
        config, _ = self._read_marker()
        return tuple(sorted(self._catalogue(self._config_path(config, "rd_home_path", "")[0])))

    def _gamelist_selections_at(self, root: str, system: str) -> GamelistSelections:
        gamelist_path = os.path.join(root, "ES-DE", "gamelists", system, "gamelist.xml")
        text = self._machine.read_text(gamelist_path).text
        if text is None:
            return GamelistSelections(system_label=None, per_game={})
        return parse_gamelist(text)

    def gamelist_selections(self, system: str) -> GamelistSelections:
        config, _ = self._read_marker()
        return self._gamelist_selections_at(self._config_path(config, "rd_home_path", "")[0], system)

    def emulators_for(self, system: str, *, content_path: str | None = None) -> tuple[EmulatorEntry, ...]:
        """The emulators that can launch *system*, in launch-priority order.

        First entry = the effective default, resolved live through ES-DE's
        hierarchy: per-game ``altemulator`` (when *content_path* is given and a
        gamelist entry matches) > per-system ``alternativeEmulator`` > declared
        first entry. A selection label matching no declared entry keeps the
        declared order — ES-DE itself falls back the same way.

        When *content_path* is omitted and the gamelist carries per-game
        overrides, every returned entry states that as a catalogue caveat: the
        system-level answer may be wrong for exactly those games.
        """
        config, _ = self._read_marker()
        root = self._config_path(config, "rd_home_path", "")[0]
        specs = self._catalogue(root).get(system, ())
        selections = self._gamelist_selections_at(root, system)
        chosen_label: str | None = None
        chosen_source: str | None = None
        if content_path is not None:
            per_game = _match_per_game(selections, content_path)
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
            home=self._home,
            global_cfg_path=global_cfg_path,
            global_text=global_text,
            cfg_label="retroarch.cfg",
            override_config_dir=os.path.join(self._retroarch_config_dir(), "config"),
            defaults=UPSTREAM_DEFAULTS,
            content_path=content_path,
            core_so=core_so,
            core_path_resolver=lambda so: self._core_path_in(global_text, so),
            arrangement="retrodeck",
            arrangement_version=version if isinstance(version, str) else None,
            extra_caveats=(*extra_caveats, *_health_caveats(health)),
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

    def _firmware_context(self) -> FirmwareContext:
        return _retroarch_firmware_context(
            self._machine,
            home=self._home,
            app_id=self._APP_ID,
            global_text=self._machine.read_text(os.path.join(self._home, RETRODECK_CFG_SUFFIX)).text,
            cfg_label="retroarch.cfg",
        )

    def firmware_for_system(self, *, system: str, verify: bool = False) -> FirmwareAnswer:
        """Which emulators RetroDECK offers for *system*, and what each of them wants.

        *system* is the ES-DE system name (``"gb"``, ``"dreamcast"``), the same
        vocabulary :meth:`emulators_for` speaks — the catalogue is the
        enumeration, so an emulator whose core is not installed and a
        standalone emulator both appear, stated as such instead of silently
        dropped. Whether that catalogue could be read travels with it: an
        unreadable ``es_systems.xml`` must never come out as "this machine has
        no emulator for that system".
        """
        config, _ = self._read_marker()
        root = self._config_path(config, "rd_home_path", "")[0]
        _, read = self._read_catalogue(root)
        catalogue = Catalogue(
            entries=tuple(
                CatalogueEntry(label=entry.label, kind=entry.kind, core_so=entry.core_so)
                for entry in self.emulators_for(system)
            ),
            read=read,
        )
        return _resolve_for_system(
            self._machine, self._firmware_context(), system=system, catalogue=catalogue, verify=verify
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
            override_label = _match_per_game(selections, content_path)
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


class EmuDeck(_FirmwareQueries):
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
    _RA_APP_ID = "org.libretro.RetroArch"

    def __init__(self, home: str, machine: Machine) -> None:
        self._home = home
        self._machine = machine

    def _marker_path(self) -> str:
        return os.path.join(self._home, EMUDECK_SETTINGS_SUFFIX)

    def _read_marker(self) -> tuple[dict[str, str], tuple[Caveat, ...]]:
        """One live read of ``settings.sh`` → (settings, marker issues)."""
        path = self._marker_path()
        result = self._machine.read_text(path)
        if result.status == "missing":
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

    def _core_path_in(self, global_text: str | None, core_so: str) -> str | None:
        if global_text is None:
            return None
        raw = parse_cfg_text(global_text).get("libretro_directory", "")
        if not raw:
            return None
        raw = expand_home(raw, home=self._home) or raw
        cores_dir = _flatpak_host_path(self._machine, self._home, self._RA_APP_ID, raw)
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
            home=self._home,
            global_cfg_path=global_cfg_path,
            global_text=cfg.text,
            cfg_label="retroarch.cfg",
            override_config_dir=os.path.join(self._retroarch_config_dir(), "config"),
            defaults=UPSTREAM_DEFAULTS,
            content_path=content_path,
            core_so=core_so,
            core_path_resolver=lambda so: self._core_path_in(cfg.text, so),
            arrangement="emudeck",
            arrangement_version=None,
            extra_caveats=_health_caveats(health),
        )

    def _firmware_context(self) -> FirmwareContext:
        """The cfg is the truth here too: ``settings.sh`` names a ``biosPath``,
        but what RetroArch actually hands its cores is ``system_directory``."""
        return _retroarch_firmware_context(
            self._machine,
            home=self._home,
            app_id=self._RA_APP_ID,
            global_text=self._machine.read_text(self._companion_cfg_path()).text,
            cfg_label="retroarch.cfg",
        )


class _RetroArchInstall(_FirmwareQueries):
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

    def _health_from(self, cfg_status: str) -> Health:
        path = self._cfg_path()
        if cfg_status == "ok":
            return Health()
        if cfg_status == "missing":
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

    def _core_path_in(self, global_text: str | None, core_so: str) -> str | None:
        if global_text is None:
            return None
        raw = parse_cfg_text(global_text).get("libretro_directory", "")
        if not raw:
            return None
        raw = expand_home(raw, home=self._home) or raw
        if self._app_id is not None:
            cores_dir = _flatpak_host_path(self._machine, self._home, self._app_id, raw)
        else:
            cores_dir = raw
        if cores_dir is None:
            return None
        return os.path.join(cores_dir, core_so)

    def save_location(self, *, content_path: str | None = None, core_so: str | None = None) -> SavePlacement:
        """Where this RetroArch install keeps the save for *content_path* under *core_so*."""
        cfg = self._machine.read_text(self._cfg_path())
        health = self._health_from(cfg.status)
        return _retroarch_save_location(
            self._machine,
            home=self._home,
            global_cfg_path=self._cfg_path(),
            global_text=cfg.text,
            cfg_label="retroarch.cfg",
            override_config_dir=os.path.join(self.root(), "config"),
            defaults=UPSTREAM_DEFAULTS,
            content_path=content_path,
            core_so=core_so,
            core_path_resolver=lambda so: self._core_path_in(cfg.text, so),
            arrangement="bare",
            arrangement_version=None,
            extra_caveats=_health_caveats(health),
        )

    def _firmware_context(self) -> FirmwareContext:
        return _retroarch_firmware_context(
            self._machine,
            home=self._home,
            app_id=self._app_id,
            global_text=self._machine.read_text(self._cfg_path()).text,
            cfg_label="retroarch.cfg",
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


@runtime_checkable
class Installation(Protocol):
    """The surface every installation handle offers — identity, health, placement.

    A common protocol instead of a closed union (REVIEW M8): detection returns
    these, and consumers program against the surface. Capabilities beyond it —
    the ES-DE catalogue (``systems``/``emulators_for``), RetroDECK's tree
    roots — live on the concrete handles; narrow with ``isinstance`` when a
    capability is needed.
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

    def firmware_for_core(self, *, core_so: str, verify: bool = False) -> FirmwareAnswer: ...

    def firmware_for_system(self, *, system: str, verify: bool = False) -> FirmwareAnswer: ...

    def firmware_inventory(self, *, verify: bool = False) -> FirmwareAnswer: ...

    def identify_firmware(
        self, *, md5: str | None = None, sha1: str | None = None, size: int | None = None
    ) -> FirmwareIdentification: ...
