"""Validate the shape of every vector file under vectors/ (schema 3). Stdlib only.

Catches malformed vectors independently of the runner: a vector file must
parse, carry the family header and schema matching its directory, and every
vector must have the family's declared input/expected shape with no stray
keys. Expected blocks are the canonical contract serializations
(atlas/contract.py) in full — the runner asserts them with exact equality, so
the validator requires every stable field to be present. Canonical inputs must
be unique across ALL files of a family: a duplicated input would let one
guarantee silently shadow another.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, NoReturn

REPO_ROOT = Path(__file__).resolve().parents[1]

# Schema 3 asks more of a port than 2 did: `glob` answers how much of the walk
# it could read, and a fixture can state a directory that exists and cannot be
# listed. A port built to 2 would answer a bare list and model no such
# directory, so the corpus is not the same contract — the number says so
# instead of leaving it to be discovered one failing vector at a time.
SCHEMA = 3

INPUT_FIELDS_REQUIRED = {"home", "files"}
INPUT_FIELDS_OPTIONAL = {
    "symlinks",
    "cores",
    "dirs",
    "inaccessible",
    "unlistable",
    "appimages",
    "savefile_query",
    "aggregate_query",
    "catalogue_query",
    "systems_query",
    "launchable_query",
    "rom_location_query",
    "entry_savefile_query",
    "savestate_query",
    "entry_savestate_query",
    "screenshot_query",
    "texture_query",
    "entry_texture_query",
    "soft_patch_query",
    "mod_query",
    "entry_mod_query",
    "firmware_query",
    "identify_query",
    "platform_systems_query",
    "platform_ids_query",
}
# The savefile, savestate and texture-pack questions take the same three
# arguments, because they are one question asked about three families — one
# vocabulary, not three that have to be kept in step. Content plays a part in
# the texture answer too, and a narrower part than in the other two: it never
# moves the directory a core builds its tree in, but it does decide which
# per-game options file states whether replacement is on.
QUERY_FIELDS = {"content_path", "core_so", "installation"}
CATALOGUE_QUERY_FIELDS = {"installation", "system", "content_path"}
# The systems question takes no arguments — only which handle answers it.
SYSTEMS_QUERY_FIELDS = {"installation"}
LAUNCHABLE_QUERY_FIELDS = {"installation", "system", "content_path"}
# The launchability verdicts — the vocabulary expected.launchable.verdict draws
# from, mirrored from atlas.installations.LAUNCH_VERDICTS.
KNOWN_LAUNCH_VERDICTS = {
    "launchable",
    "not-accepted",
    "entry-not-accepted",
    "needs-installation",
    "unknown",
}
# ROM placement is asked of one system; content plays no part in where a
# system's ROMs live, so unlike the catalogue query this one takes no path.
ROM_LOCATION_QUERY_FIELDS = {"installation", "system"}
# The platform questions (issue #68): forward takes a public vocabulary and a
# value in it, reverse takes one system. The vocabularies, statuses and tag
# sources mirror atlas.platforms / atlas.installations — closed sets.
PLATFORM_SYSTEMS_QUERY_FIELDS = {"installation", "vocabulary", "value"}
PLATFORM_IDS_QUERY_FIELDS = {"installation", "system"}
KNOWN_PLATFORM_VOCABULARIES = {"igdb", "libretro", "screenscraper", "thegamesdb"}
KNOWN_PLATFORM_STATUSES = {"declared", "disabled", "absent"}
KNOWN_PLATFORM_TAG_SOURCES = {"catalogue", "vocabulary"}
ENTRY_QUERY_FIELDS = {"installation", "system", "label", "content_path"}
# The aggregate asks EVERY detected handle one question, so it names the
# question instead of a handle. The fan-out is the same for every question it
# mirrors, so the vocabulary is the questions that make the LABEL provable —
# one with a full payload per arrangement, one whose arrangements refuse for
# different reasons — rather than every question the surface offers; widening
# it means teaching the runner to ask, and giving the answer a shape below.
#
# Per question: (the keys it is asked by, the keys it may also carry). The map
# is the single source of both rules, because a key the runner never passes on
# — a core_so on a catalogue question — makes the vector state something no
# answer can reflect, and half a rule refuses only half of those.
AGGREGATE_QUESTION_FIELDS = {
    "savefile_location": ({"question"}, {"content_path", "core_so"}),
    "savestate_location": ({"question"}, {"content_path", "core_so"}),
    "texture_pack_location": ({"question"}, {"content_path", "core_so"}),
    "emulators_for": ({"question", "system"}, {"content_path"}),
}
AGGREGATE_ANSWER_FIELDS = {"installation", "answer"}
FIRMWARE_QUERY_FIELDS = {"installation", "kind", "core_so", "system", "verify"}
KNOWN_FIRMWARE_QUERY_KINDS = {"core", "system", "inventory"}
IDENTIFY_QUERY_FIELDS = {"installation", "md5", "sha1", "size"}
INSTALLATION_FIELDS = {"kind", "kinds", "root", "health"}
PLACEMENT_FIELDS = {
    "dir",
    "root_kind",
    "needs",
    "fallback_dir",
    "physical_dir",
    "file_set",
    "granularity",
    "caveats",
}
# A savestate placement is a save placement without `granularity`, and the
# omission is the contract: no core writes a savestate, so no rule card can
# ever state how one groups them (atlas/placement.py). Derived from the save
# set rather than spelled out, so the two can never drift in the fields they
# do share.
SAVESTATE_PLACEMENT_FIELDS = PLACEMENT_FIELDS - {"granularity"}
# A screenshot placement is the save shape minus the three fields whose
# domain is empty there (no file set of dated names, no fallback, no
# granularity) — see atlas/placement.py.
SCREENSHOT_PLACEMENT_FIELDS = {"dir", "root_kind", "needs", "physical_dir", "caveats"}
KNOWN_SCREENSHOT_ROOT_KINDS = {"screenshot_directory", "content_directory"}
# A texture placement shares only the fields its own question has. Written out
# rather than derived from the save set, because here the difference is not a
# subtraction: three save fields are gone (`root_kind` nobody asked for,
# `fallback_dir` is RetroArch's path math for files it writes itself, and a
# `file_set` below a texture root would report the caller's own downloads back
# to it) and two fields are new.
TEXTURE_PLACEMENT_FIELDS = {"dir", "needs", "physical_dir", "enabled", "keying", "caveats"}
# The soft-patching answer has no directory at all: its subject is the content,
# and what it answers with is the candidate files beside it. `applies` is the one
# live per-core reading (does this core load its content into memory), and it is
# nullable for the reason every nullable field here is — nothing established it.
# A mod placement is the texture placement with its directory trio moved inside
# `trees`: this family's answer is plural because an emulator may read mods from
# several directories that are different mechanisms rather than alternatives.
MOD_PLACEMENT_FIELDS = {"trees", "needs", "enabled", "caveats"}
MOD_TREE_FIELDS = {"role", "dir", "physical_dir", "keying"}
SOFT_PATCH_FIELDS = {"candidates", "applies", "caveats"}
SOFT_PATCH_CANDIDATE_FIELDS = {"format", "path", "continuations", "attempted"}
# The formats RetroArch tries beside the content, in the order it tries them.
# The ORDER is contractual, not just the set: a port that answers the four in
# another order answers a different question.
PATCH_FORMAT_ORDER = ("ips", "bps", "ups", "xdelta")
# One digit, 1 through 9 — upstream's own bound (task_patch.c:1121-1147).
PATCH_CONTINUATIONS = 9
# How the tree below a texture root is divided per game. World knowledge, so it
# is stated only where a citation backs it and is null otherwise — never a claim
# that the tree is undivided.
KNOWN_KEYINGS = {"game-id", "serial", "title-id", "rom-name", "pack", "title"}
FILE_SET_FIELDS = {"state", "files", "complete", "groups"}
FILE_GROUP_FIELDS = {"dir", "files", "granularity", "role"}
FIRMWARE_FIELDS = {"root", "hash_checked", "cores", "unclaimed", "caveats"}
FIRMWARE_CORE_FIELDS = {
    "core_so",
    "label",
    "declaration",
    "requirements_met",
    "requirements",
    "refused",
    "caveats",
}
REFUSED_FIELDS = {"declared", "need", "reason"}
KNOWN_REFUSAL_REASONS = {
    "firmware-path-escapes-root",
    "firmware-path-unresolvable",
    "firmware-path-names-no-file",
    "firmware-root-unusable",
}
KNOWN_PATH_KINDS = {"file", "directory", "missing", "inaccessible"}
FIRMWARE_REQUIREMENT_FIELDS = {
    "core_so",
    "system",
    "system_source",
    "need",
    "file_name",
    "path",
    "declared",
    "identity",
    "found",
    "present",
    "checked",
    "satisfied",
}
KNOWN_DECLARATION_STATES = {"read", "unreadable", "absent", "unsupported", "packaged"}
KNOWN_SYSTEM_SOURCES = {"override", "systemname", "slug", "none", "card"}
UNCLAIMED_FIELDS = {"path", "identity", "known_as"}
IDENTIFICATION_FIELDS = {"identity", "known_as", "requirements", "caveats"}
IDENTITY_FIELDS = {"md5", "sha1", "size"}
KNOWN_FIRMWARE_NEEDS = {"required", "optional"}
KNOWN_FIRMWARE_CHECKED = {"verified", "mismatch", "unchecked", "unknown"}
GRANULARITY_FIELDS = {"value", "mode", "readings", "alternatives"}
READING_FIELDS = {"key", "value", "options_file"}
ALTERNATIVE_FIELDS = {"mode", "options", "values"}
# The one granularity value no file group may carry: it says no save data is
# kept at all (write protection discarding the writes), and a group is a place
# save data lives. Valid for granularity.value and an alternative's value only.
GRANULARITY_VALUE_NONE = "none"
CAVEAT_FIELDS = {"code", "data"}
EMULATOR_FIELDS = {"system", "label", "kind", "core_so", "selection", "caveats"}
# The three ways a catalogue answer carries no entries, each a different claim:
# the arrangement has none, its one could not be read, or atlas has not
# established where it keeps one. None of them can accompany actual entries.
# The fourth family member, "emulator-catalogue-sealed", is deliberately NOT
# here: it states that part of the catalogue could not be opened while the
# readable part answered, so it accompanies real entries as legitimately as an
# empty list. The fifth, "emulator-catalogue-exclusive", is no refusal either:
# the custom layer declared itself the whole catalogue (<loadExclusive/>), so
# the enumeration it accompanies — entries or legitimately empty — is complete.
NO_CATALOGUE_CODES = {
    "emulator-catalogue-unavailable",
    "emulator-catalogue-unreadable",
    "emulator-catalogue-unestablished",
}
KNOWN_KINDS = {"retrodeck", "emudeck", "bare_retroarch_flatpak", "bare_retroarch_native"}
KNOWN_FILE_STATUSES = {"unreadable", "invalid-text"}
BLOB_FIELDS = {"md5", "sha1", "size"}
KNOWN_HEALTH_ISSUES = {
    "marker-missing",
    "marker-unreadable",
    "marker-invalid",
    "root-missing",
    "saves-root-missing",
    "companion-config-missing",
    "config-unreadable",
    "catalogue-invalid",
    "content-tree-unwired",
}
KNOWN_ROOT_KINDS = {
    "savefile_directory",
    "content_directory",
    "system_directory",
    "working_directory",
    "emulator_directory",
}
# Closed around its own question: a savestate is never anchored at the saves
# root, and never at a core's system directory — no card can move it there.
# emulator_directory and working_directory joined with the standalone savestate
# cards (#225): the tree a standalone emulator owns, and the launch's cwd a
# relative melonDS SavestatePath anchors at — the savefile family's own words.
KNOWN_STATE_ROOT_KINDS = {
    "savestate_directory",
    "content_directory",
    "emulator_directory",
    "working_directory",
}
# The holes a placement may hand back — each one a value the CALLER fills from
# the content at hand. Closed like every other vocabulary here, and for the
# sharpest reason of them all: a hole nobody can fill is worse than a stated
# unknown, because a client reads it as work it is supposed to do. Nothing a
# config states belongs here either; that is atlas's own to resolve.
KNOWN_HOLES = {"content_dir", "content_dir_name", "cwd", "library_name", "region", "rom_stem", "save_id"}
KNOWN_GRANULARITIES = {
    "shared-card",
    "shared-file",
    "per-game-file",
    "per-game-files",
    "per-game-directory",
}
KNOWN_ROLES = {"battery", "memory-card", "disk-diff", "high-score", "settings", "notes"}
KNOWN_FILE_SET_STATES = {"observed", "declared", "unknown"}
KNOWN_EMULATOR_KINDS = {"libretro", "standalone"}
KNOWN_CAVEAT_CODES = {
    "no-core",
    "core-unqueryable",
    "sorted-dir-missing",
    "sorted-dir-uncreatable",
    "dead-symlink",
    "symlink-loop",
    "filenames-unverified",
    "filenames-content-conditional",
    "file-names-unestablished",
    "file-set-across-systems",
    "file-set-spans-roots",
    "unknown-option-value",
    "system-directory-cleared",
    "per-game-overrides-present",
    "per-game-override",
    "per-game-layer-unread",
    "unverified-version",
    "invalid-save-directory",
    "invalid-screenshot-directory",
    "core-suspect",
    "core-unaudited",
    "core-multi-option",
    "core-generation-mismatch",
    "core-generation-unestablished",
    "core-option-value-unestablished",
    "option-entry-retired",
    "core-own-writes-unestablished",
    "arrangement-unverified",
    "arrangement-version-drifted",
    "sandbox-path-untranslated",
    "app-relative-path-unexpanded",
    "cfg-line-dropped",
    "cfg-value-rejected",
    "content-dir-observation",
    "content-path-unnamed",
    "core-savestates-unsupported",
    "no-firmware-declaration",
    "no-firmware-requirement",
    "firmware-declaration-unknown",
    "firmware-packaged-declaration",
    "firmware-builtin-replacement",
    # The save route's refusal word, doubling as the firmware route's caveat
    # for the same fact — one fact, one code on both routes.
    "emulator-config-unreadable",
    "info-path-unresolved",
    "core-dir-unresolved",
    "firmware-root-missing",
    "core-not-installed",
    "standalone-unsupported",
    "emulator-catalogue-unavailable",
    "firmware-unreadable",
    "firmware-content-unidentified",
    "system-unknown",
    "system-not-in-catalogue",
    "rom-path-undeclared",
    "rom-path-unresolved",
    "frontend-settings-unreadable",
    "config-home-relocated",
    "system-assignment-derived",
    "core-without-systemname",
    "system-assignment-may-hide-cores",
    "core-info-unreadable",
    "emulator-catalogue-unreadable",
    "emulator-catalogue-unestablished",
    "emulator-catalogue-sealed",
    "emulator-catalogue-exclusive",
    "emulator-list-derived",
    "frontend-marker-mismatch",
    "firmware-path-obstructed",
    "firmware-path-inaccessible",
    "firmware-path-escapes-root",
    "firmware-path-unresolvable",
    "firmware-path-names-no-file",
    "firmware-root-unusable",
    "firmware-declaration-unread",
    "firmware-content-contradictory",
    "firmware-content-unstated",
    "firmware-image-identified",
    "firmware-image-ambiguous",
    "firmware-search-unverified",
    "firmware-scan-incomplete",
    "core-enumeration-incomplete",
    "save-dir-launch-dependent",
    "save-inside-content",
    "save-inside-image",
    "save-writes-discarded",
    "core-mode-unestablished",
    "save-root-redirected",
    "save-root-revoked",
    "save-root-unresolvable",
    "save-dir-unlistable",
    "emulator-read-unestablished",
    "emulator-config-unread",
    "feature-switch-absent",
    "patch-formats-unestablished",
    "soft-patching-applies",
    "entry-format-unclaimed",
    "archive-contents-unread",
    "entry-format-unestablished",
    "platform-unmapped",
    "platform-unknown",
    "platform-scraping-ignored",
    # An installation's health findings are caveats with their own stable
    # codes, and an answer computed on a broken installation carries them
    # directly — so the caveat vocabulary contains the health vocabulary by
    # construction rather than by a second list that can drift from it. The
    # envelope code "health" is deliberately absent: it retired with schema 3's
    # grammar, and a vector still spelling it names a code no resolver emits.
    *KNOWN_HEALTH_ISSUES,
}
# The codes that may stand in for "nothing could be read here". Each says a
# different thing to a client — nothing declares firmware, nothing declared
# became a requirement, what is declared could not be established, the
# identifier is unknown here, the named core is absent — and none of them may
# be read as "nothing needed".
NOTHING_READ_CODES = {
    "no-firmware-declaration",
    "firmware-declaration-unknown",
    "system-unknown",
    "core-not-installed",
}
# An identification without an identity says which kind of nothing it is: the
# table does not know this content, the request contradicts itself, or it named
# no content at all.
UNIDENTIFIED_CODES = {
    "firmware-content-unidentified",
    "firmware-content-contradictory",
    "firmware-content-unstated",
}
# The refusals a placement question may answer with instead of a location.
# "core-not-installed" is the firmware route's word for the same fact, and
# deliberately the same string: one fact, one code on both routes.
KNOWN_UNRESOLVED_CODES = {
    "standalone-unsupported",
    "standalone-variant-unestablished",
    "core-not-installed",
    "texture-wiring-unestablished",
    "mod-wiring-unestablished",
    "emulator-config-unreadable",
}


class VectorError(Exception):
    pass


def fail(message: str) -> NoReturn:
    raise VectorError(message)


def _require_exact(name: str, obj: Any, fields: set[str], what: str) -> None:
    if not isinstance(obj, dict) or set(obj) != fields:
        fail(f"{name}: {what} must be exactly the fields {sorted(fields)}, got {obj!r}")


def _validate_handle_selector(name: str, family: str, query: Any) -> None:
    """The optional 'which handle answers this' key every query family shares.

    One spelling of the rule: the runner selects by ``kind``, so a selector
    naming something that is not a kind can never match a detected handle.
    """
    if "installation" in query and query["installation"] not in KNOWN_KINDS:
        fail(f"{name}: input.{family}.installation must be one of {sorted(KNOWN_KINDS)}")


def _validate_query(name: str, query: Any, family: str = "savefile_query") -> None:
    """A placement question — *family* says which of the two it is.

    Both take the same three keys, so both are held to one rule; only the
    message names the key the vector actually wrote.
    """
    if not isinstance(query, dict):
        fail(f"{name}: input.{family} must be an object")
    keys = set(query)
    if not keys or not keys <= QUERY_FIELDS:
        fail(f"{name}: input.{family} keys must be a non-empty subset of {sorted(QUERY_FIELDS)}")
    for key in keys:
        if not isinstance(query[key], str) or not query[key]:
            fail(f"{name}: input.{family}.{key} must be a non-empty string")
    _validate_handle_selector(name, family, query)


def _validate_firmware_query_fields(name: str, query: Any) -> None:
    for key in ("installation", "kind", "core_so", "system"):
        if key in query and (not isinstance(query[key], str) or not query[key]):
            fail(f"{name}: input.firmware_query.{key} must be a non-empty string")
    if "verify" in query and not isinstance(query["verify"], bool):
        fail(f"{name}: input.firmware_query.verify must be a boolean")
    _validate_handle_selector(name, "firmware_query", query)


def _validate_firmware_query_kind(name: str, query: Any) -> None:
    """Each kind of firmware query carries exactly what that kind answers by."""
    kind = query.get("kind")
    if kind not in KNOWN_FIRMWARE_QUERY_KINDS:
        fail(f"{name}: input.firmware_query.kind must be one of {sorted(KNOWN_FIRMWARE_QUERY_KINDS)}")
    if kind == "core" and "core_so" not in query:
        fail(f"{name}: a 'core' firmware query needs input.firmware_query.core_so")
    if kind == "system" and "system" not in query:
        fail(f"{name}: a 'system' firmware query needs input.firmware_query.system")
    if kind == "inventory" and ({"core_so", "system"} & set(query)):
        fail(f"{name}: an 'inventory' firmware query takes neither core_so nor system")


def _validate_firmware_query(name: str, query: Any) -> None:
    if not isinstance(query, dict) or not set(query) <= FIRMWARE_QUERY_FIELDS:
        fail(f"{name}: input.firmware_query keys must be a subset of {sorted(FIRMWARE_QUERY_FIELDS)}")
    _validate_firmware_query_fields(name, query)
    _validate_firmware_query_kind(name, query)


def _validate_identify_query(name: str, query: Any) -> None:
    if not isinstance(query, dict) or not set(query) <= IDENTIFY_QUERY_FIELDS:
        fail(f"{name}: input.identify_query keys must be a subset of {sorted(IDENTIFY_QUERY_FIELDS)}")
    for key in ("installation", "md5", "sha1"):
        if key in query and (not isinstance(query[key], str) or not query[key]):
            fail(f"{name}: input.identify_query.{key} must be a non-empty string")
    if "size" in query and not isinstance(query["size"], int):
        fail(f"{name}: input.identify_query.size must be an integer")
    _validate_handle_selector(name, "identify_query", query)
    if not ({"md5", "sha1", "size"} & set(query)):
        fail(f"{name}: input.identify_query must state some content — md5, sha1, or size")


def _validate_entry_query(name: str, query: Any, family: str = "entry_savefile_query") -> None:
    if not isinstance(query, dict):
        fail(f"{name}: input.{family} must be an object")
    keys = set(query)
    if "system" not in keys or not keys <= ENTRY_QUERY_FIELDS:
        fail(
            f"{name}: input.{family} must carry 'system' plus optional "
            "'label'/'content_path'/'installation'"
        )
    for key in keys:
        if not isinstance(query[key], str) or not query[key]:
            fail(f"{name}: input.{family}.{key} must be a non-empty string")
    _validate_handle_selector(name, family, query)


def _validate_catalogue_query(name: str, query: Any) -> None:
    if not isinstance(query, dict):
        fail(f"{name}: input.catalogue_query must be an object")
    keys = set(query)
    if "system" not in keys or not keys <= CATALOGUE_QUERY_FIELDS:
        fail(
            f"{name}: input.catalogue_query must carry 'system' plus optional "
            "'content_path'/'installation'"
        )
    for key in keys:
        if not isinstance(query[key], str) or not query[key]:
            fail(f"{name}: input.catalogue_query.{key} must be a non-empty string")
    _validate_handle_selector(name, "catalogue_query", query)


def _validate_rom_location_query(name: str, query: Any) -> None:
    if not isinstance(query, dict):
        fail(f"{name}: input.rom_location_query must be an object")
    if not set(query) <= ROM_LOCATION_QUERY_FIELDS or "system" not in query:
        fail(
            f"{name}: input.rom_location_query must carry 'system' plus optional "
            f"{sorted(ROM_LOCATION_QUERY_FIELDS - {'system'})}"
        )
    if not isinstance(query["system"], str) or not query["system"]:
        fail(f"{name}: input.rom_location_query.system must be a non-empty string")
    _validate_handle_selector(name, "rom_location_query", query)


def _validate_systems_query(name: str, query: Any) -> None:
    if not isinstance(query, dict) or not set(query) <= SYSTEMS_QUERY_FIELDS:
        fail(f"{name}: input.systems_query keys must be a subset of {sorted(SYSTEMS_QUERY_FIELDS)}")
    _validate_handle_selector(name, "systems_query", query)


def _validate_launchable_query(name: str, query: Any) -> None:
    # Both halves are the question's subject: a verdict without a file has
    # nothing to derive an extension from, and one without a system has no
    # accept-list to judge it by.
    if not isinstance(query, dict):
        fail(f"{name}: input.launchable_query must be an object")
    keys = set(query)
    if not {"system", "content_path"} <= keys or not keys <= LAUNCHABLE_QUERY_FIELDS:
        fail(
            f"{name}: input.launchable_query must carry 'system' and 'content_path' plus "
            "optional 'installation'"
        )
    for key in keys:
        if not isinstance(query[key], str) or not query[key]:
            fail(f"{name}: input.launchable_query.{key} must be a non-empty string")
    _validate_handle_selector(name, "launchable_query", query)


def _validate_platform_systems_query(name: str, query: Any) -> None:
    if not isinstance(query, dict):
        fail(f"{name}: input.platform_systems_query must be an object")
    keys = set(query)
    if not {"vocabulary", "value"} <= keys or not keys <= PLATFORM_SYSTEMS_QUERY_FIELDS:
        fail(
            f"{name}: input.platform_systems_query must carry 'vocabulary' and 'value' plus "
            "optional 'installation'"
        )
    if query["vocabulary"] not in KNOWN_PLATFORM_VOCABULARIES:
        fail(
            f"{name}: input.platform_systems_query.vocabulary must be one of "
            f"{sorted(KNOWN_PLATFORM_VOCABULARIES)}"
        )
    if not isinstance(query["value"], str) or not query["value"]:
        fail(f"{name}: input.platform_systems_query.value must be a non-empty string")
    _validate_handle_selector(name, "platform_systems_query", query)


def _validate_platform_ids_query(name: str, query: Any) -> None:
    if not isinstance(query, dict) or not {"system"} <= set(query) <= PLATFORM_IDS_QUERY_FIELDS:
        fail(
            f"{name}: input.platform_ids_query must carry 'system' plus optional 'installation'"
        )
    if not isinstance(query["system"], str) or not query["system"]:
        fail(f"{name}: input.platform_ids_query.system must be a non-empty string")
    _validate_handle_selector(name, "platform_ids_query", query)


def _validate_aggregate_question(name: str, query: dict[str, Any]) -> None:
    """Each question carries what that question is asked by, and nothing else.

    Both halves come off :data:`AGGREGATE_QUESTION_FIELDS`, so a question
    cannot end up with its missing keys refused and its stray ones accepted:
    a key the runner does not pass on for this question is a vector stating
    something no answer below can reflect.
    """
    question = query.get("question")
    if question not in AGGREGATE_QUESTION_FIELDS:
        fail(f"{name}: input.aggregate_query.question must be one of {sorted(AGGREGATE_QUESTION_FIELDS)}")
    required, optional = AGGREGATE_QUESTION_FIELDS[question]
    keys = set(query)
    missing = sorted(required - keys)
    if missing:
        fail(f"{name}: a {question!r} aggregate query needs {missing}")
    stray = sorted(keys - required - optional)
    if stray:
        fail(f"{name}: a {question!r} aggregate query is not asked by {stray} — the runner never passes it on")


def _validate_aggregate_query(name: str, query: Any) -> None:
    if not isinstance(query, dict):
        fail(f"{name}: input.aggregate_query must be an object")
    # Naming a handle here would ask the aggregate to choose one, which is the
    # one thing it does not do: it asks every detected installation. Its own
    # message, because "not asked by" would read as a missing feature.
    if "installation" in query:
        fail(
            f"{name}: input.aggregate_query takes no 'installation' — the aggregate asks every "
            "detected handle; use the query family of a single question to name one"
        )
    for key in set(query):
        if not isinstance(query[key], str) or not query[key]:
            fail(f"{name}: input.aggregate_query.{key} must be a non-empty string")
    _validate_aggregate_question(name, query)


def _validate_blob_spec(name: str, path: str, spec: Any) -> None:
    # A binary blob: it exists, reads as invalid-text, and answers the
    # declared identity for file_size / file_digest.
    if not set(spec) or not set(spec) <= BLOB_FIELDS:
        fail(f"{name}: input.files[{path!r}] blob spec keys must be a non-empty subset of {sorted(BLOB_FIELDS)}")
    for key in ("md5", "sha1"):
        if key in spec and (not isinstance(spec[key], str) or not spec[key]):
            fail(f"{name}: input.files[{path!r}].{key} must be a non-empty string")
    if "size" in spec and (not isinstance(spec["size"], int) or spec["size"] < 0):
        fail(f"{name}: input.files[{path!r}].size must be a non-negative integer")


def _validate_status_spec(name: str, path: str, spec: dict[str, Any]) -> None:
    # A read failure, optionally with the size the stat still answers: a
    # chmod-000 file is a file of a known size whose bytes cannot be read.
    # No digest may join it — that is the read this file does not survive.
    #
    # Stricter than FixtureMachine on purpose, and this is the one place the
    # two are allowed to differ: the machine's grammar is convenience for
    # hand-written unit fixtures, while the corpus is the normative artifact a
    # port is checked against, so each state gets exactly ONE spelling here. A
    # blob and {"status": "invalid-text", ...} describe the same file; a port
    # author comparing two vectors must not have to work out that they agree.
    if spec["status"] not in KNOWN_FILE_STATUSES or not set(spec) <= {"status", "size"}:
        fail(
            f"{name}: input.files[{path!r}] status spec must be "
            f"{{'status': one of {sorted(KNOWN_FILE_STATUSES)}}}, optionally with 'size'"
        )
    if "size" in spec and (not isinstance(spec["size"], int) or spec["size"] < 0):
        fail(f"{name}: input.files[{path!r}].size must be a non-negative integer")


def _validate_file_spec(name: str, path: str, spec: Any) -> None:
    """One input.files entry: string content, a read failure, or a blob."""
    if isinstance(spec, str):
        return
    if not isinstance(spec, dict):
        fail(f"{name}: input.files[{path!r}] must be string content or an object spec")
    if "status" in spec:
        _validate_status_spec(name, path, spec)
        return
    _validate_blob_spec(name, path, spec)


def _validate_input_files(name: str, files: Any) -> None:
    if not isinstance(files, dict):
        fail(f"{name}: input.files must be an object")
    for path, spec in files.items():
        if not isinstance(path, str):
            fail(f"{name}: input.files keys must be strings")
        _validate_file_spec(name, path, spec)


def _validate_input_paths(name: str, inp: Any) -> None:
    """The directories, unreadable paths, and links a fixture machine declares."""
    for list_key in ("dirs", "inaccessible", "unlistable"):
        entries = inp.get(list_key, [])
        if not isinstance(entries, list) or not all(isinstance(e, str) and e for e in entries):
            fail(f"{name}: input.{list_key} must be a list of non-empty path strings")
    # The two unreadable lists answer opposite things about the same question —
    # does the stat succeed? — so a path in both describes no machine. It is
    # worth refusing rather than resolving, because the tempting reading is
    # that both together spell a mode-000 directory: they do not. Such a
    # directory answers *directory* for itself and belongs in 'unlistable'
    # alone; 'inaccessible' would make the resolver refuse it a step earlier
    # than the real machine does, and the vector would prove the wrong path.
    contradictory = sorted(set(inp.get("inaccessible", [])) & set(inp.get("unlistable", [])))
    if contradictory:
        fail(
            f"{name}: {contradictory} are in both input.inaccessible and input.unlistable — a path "
            "whose stat fails cannot also be a directory whose stat succeeds. A mode-000 directory "
            "is 'unlistable'; name its children in 'inaccessible' if they matter."
        )
    symlinks = inp.get("symlinks", {})
    if not isinstance(symlinks, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in symlinks.items()
    ):
        fail(f"{name}: input.symlinks must be an object of link paths to target strings")


def _validate_core_options(name: str, so_path: str, options: Any) -> None:
    if not isinstance(options, dict):
        fail(f"{name}: input.cores[{so_path!r}].options must be an object of option definitions")
    for opt_key, opt_spec in options.items():
        if (
            not isinstance(opt_key, str)
            or not isinstance(opt_spec, dict)
            or not set(opt_spec) <= {"default", "values"}
            or not isinstance(opt_spec.get("values", []), list)
        ):
            fail(
                f"{name}: input.cores[{so_path!r}].options[{opt_key!r}] must be "
                "{'default': str|null, 'values': [str, ...]}"
            )


def _validate_input_cores(name: str, cores: Any) -> None:
    if not isinstance(cores, dict):
        fail(f"{name}: input.cores must be an object of .so paths to core answers")
    for so_path, spec in cores.items():
        if not isinstance(so_path, str):
            fail(f"{name}: input.cores keys must be strings")
        if spec is None:
            continue  # present but unloadable
        if not isinstance(spec, dict) or not isinstance(spec.get("library_name"), str):
            fail(f"{name}: input.cores[{so_path!r}] must be null or an object with a string library_name")
        options = spec.get("options")
        if options is not None:
            _validate_core_options(name, so_path, options)


# Which validator each optional query key goes through. Three groups, because
# there are three shapes of question and not fifteen: a placement question asked
# of a core, the same asked through a catalogue entry, and the ones that carry
# their own vocabulary. The key doubles as the label a failure reports under, so
# the table states the pairing once instead of repeating it per family — and a
# family added tomorrow is a line here rather than another branch.
_PLAIN_QUERY_KEYS = (
    "savefile_query",
    "savestate_query",
    "screenshot_query",
    "texture_query",
    "mod_query",
    "soft_patch_query",
)
_ENTRY_QUERY_KEYS = (
    "entry_savefile_query",
    "entry_savestate_query",
    "entry_texture_query",
    "entry_mod_query",
)
_OWN_QUERY_VALIDATORS = {
    "aggregate_query": _validate_aggregate_query,
    "catalogue_query": _validate_catalogue_query,
    "systems_query": _validate_systems_query,
    "launchable_query": _validate_launchable_query,
    "rom_location_query": _validate_rom_location_query,
    "firmware_query": _validate_firmware_query,
    "identify_query": _validate_identify_query,
    "platform_systems_query": _validate_platform_systems_query,
    "platform_ids_query": _validate_platform_ids_query,
}


def _validate_soft_patch_subject(name: str, inp: Any) -> None:
    """The one query whose content is required rather than optional.

    The content is that question's subject, not a modifier: the patch files are
    named after it, so a query without one asks about no file at all. Its own
    rule rather than a branch inside the dispatch, because it is a statement
    about that question and not about how queries are shaped.
    """
    query = inp.get("soft_patch_query")
    if query is not None and not query.get("content_path"):
        fail(f"{name}: soft_patch_query must name the content_path it asks about")


def _validate_input_queries(name: str, inp: Any) -> None:
    """The optional question an input asks, at most one shape per family."""
    for key in _PLAIN_QUERY_KEYS:
        if key in inp:
            _validate_query(name, inp[key], key)
    for key in _ENTRY_QUERY_KEYS:
        if key in inp:
            _validate_entry_query(name, inp[key], key)
    for key, validate in _OWN_QUERY_VALIDATORS.items():
        if key in inp:
            validate(name, inp[key])
    _validate_soft_patch_subject(name, inp)


def _validate_input(name: str, inp: Any) -> None:
    if not isinstance(inp, dict):
        fail(f"{name}: input must be an object")
    keys = set(inp)
    if not INPUT_FIELDS_REQUIRED <= keys or not keys <= (INPUT_FIELDS_REQUIRED | INPUT_FIELDS_OPTIONAL):
        fail(f"{name}: input keys must be {sorted(INPUT_FIELDS_REQUIRED)} plus optional {sorted(INPUT_FIELDS_OPTIONAL)}")
    if not isinstance(inp["home"], str) or not inp["home"]:
        fail(f"{name}: input.home must be a non-empty string")
    _validate_input_files(name, inp["files"])
    _validate_input_paths(name, inp)
    _validate_input_cores(name, inp.get("cores", {}))
    _validate_input_appimages(name, inp.get("appimages", {}))
    _validate_input_queries(name, inp)


# The AppImage modeling vocabulary — mirrored from FixtureMachine
# (atlas/machine.py): whole-archive states RealMachine can report short of
# opening the image, and per-entry states short of the entry's text. An absent
# AppImage is an undeclared path, an absent entry an undeclared entry.
APPIMAGE_STATES = {"unreadable", "not-appimage", "capability-missing"}
APPIMAGE_ENTRY_STATES = {"unreadable", "invalid-text"}


def _validate_input_appimages(name: str, appimages: Any) -> None:
    if not isinstance(appimages, dict):
        fail(f"{name}: input.appimages must be an object")
    for path, spec in appimages.items():
        if not isinstance(path, str) or not path.startswith("/"):
            fail(f"{name}: appimage paths must be absolute, got {path!r}")
        _validate_appimage_spec(name, path, spec)


def _validate_appimage_spec(name: str, path: str, spec: Any) -> None:
    if isinstance(spec, str):
        if spec not in APPIMAGE_STATES:
            fail(
                f"{name}: appimage {path!r} whole-archive state must be one of "
                f"{sorted(APPIMAGE_STATES)}, got {spec!r}"
            )
        return
    if not isinstance(spec, dict):
        fail(f"{name}: appimage {path!r} must map entries or name a whole-archive state")
    for inner, value in spec.items():
        _validate_appimage_entry(name, path, inner, value)


def _validate_appimage_entry(name: str, path: str, inner: Any, value: Any) -> None:
    if not isinstance(inner, str) or not inner or inner.startswith("/"):
        fail(f"{name}: appimage {path!r} entry names must be relative paths, got {inner!r}")
    if isinstance(value, str):
        return
    if (
        not isinstance(value, dict)
        or set(value) != {"status"}
        or value["status"] not in APPIMAGE_ENTRY_STATES
    ):
        fail(
            f"{name}: appimage {path!r} entry {inner!r} must be text or "
            f"{{'status': one of {sorted(APPIMAGE_ENTRY_STATES)}}}"
        )


def _validate_installations(name: str, installations: Any) -> None:
    if not isinstance(installations, list):
        fail(f"{name}: expected.installations must be a list")
    for inst in installations:
        _require_exact(name, inst, INSTALLATION_FIELDS, "each installation")
        if inst["kind"] not in KNOWN_KINDS:
            fail(f"{name}: installation kind must be one of {sorted(KNOWN_KINDS)}, got {inst['kind']!r}")
        kinds = inst["kinds"]
        if not isinstance(kinds, list) or not kinds or not all(k in KNOWN_KINDS for k in kinds):
            fail(f"{name}: installation kinds must be a non-empty list of known kinds, got {kinds!r}")
        if not isinstance(inst["root"], str) or not inst["root"]:
            fail(f"{name}: installation root must be a non-empty string")
        _validate_health(name, inst["health"])


def _validate_health(name: str, health: Any) -> None:
    """An installation's health: its findings, each a caveat like any other.

    Bare codes were the old shape. A finding carries the path it is about (and
    the read status or marker key behind it), so it serializes as ``{code,
    data}`` — and a vector that states only the code would pin an answer that
    has lost what a client acts on.
    """
    _validate_caveats(name, health)
    unknown = sorted({finding["code"] for finding in health} - KNOWN_HEALTH_ISSUES)
    if unknown:
        fail(
            f"{name}: installation health findings must be issue codes from "
            f"{sorted(KNOWN_HEALTH_ISSUES)}, got {unknown}"
        )


def _validate_caveats(name: str, caveats: Any) -> None:
    if not isinstance(caveats, list):
        fail(f"{name}: caveats must be a list")
    for caveat in caveats:
        _require_exact(name, caveat, CAVEAT_FIELDS, "each caveat")
        if caveat["code"] not in KNOWN_CAVEAT_CODES:
            fail(f"{name}: caveat code must be one of {sorted(KNOWN_CAVEAT_CODES)}, got {caveat['code']!r}")
        data = caveat["data"]
        if not isinstance(data, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in data.items()
        ):
            fail(f"{name}: caveat data must be an object of strings, got {data!r}")


def _validate_file_set(name: str, file_set: Any) -> None:
    _require_exact(name, file_set, FILE_SET_FIELDS, "savefile_location.file_set")
    if file_set["state"] not in KNOWN_FILE_SET_STATES:
        fail(f"{name}: file_set.state must be one of {sorted(KNOWN_FILE_SET_STATES)}")
    if not isinstance(file_set["files"], list) or not all(isinstance(f, str) for f in file_set["files"]):
        fail(f"{name}: file_set.files must be a list of strings")
    if not isinstance(file_set["complete"], bool):
        fail(f"{name}: file_set.complete must be a boolean")
    if file_set["state"] == "unknown" and (file_set["files"] or file_set["complete"]):
        fail(f"{name}: an unknown file_set carries no files and no completeness claim")
    _validate_file_groups(name, file_set)


def _validate_one_file_group(name: str, group: Any) -> None:
    """One group's own shape — its directory, its names, and its two words."""
    _require_exact(name, group, FILE_GROUP_FIELDS, "file_set.groups[]")
    if not isinstance(group["dir"], str) or not group["dir"]:
        fail(f"{name}: every file group states a directory")
    if group["files"] is not None:
        if not isinstance(group["files"], list) or not all(
            isinstance(f, str) for f in group["files"]
        ):
            fail(f"{name}: file group files must be a list of strings, or null")
        if not group["files"]:
            # null is the directory whose names are not established; an empty
            # list would claim the directory holds nothing, which is the one
            # thing such a group does not say.
            fail(f"{name}: a file group with no files is not a group — use null")
    if group["granularity"] not in KNOWN_GRANULARITIES:
        fail(f"{name}: file group granularity must be one of {sorted(KNOWN_GRANULARITIES)}")
    if group["role"] not in KNOWN_ROLES:
        fail(f"{name}: file group role must be one of {sorted(KNOWN_ROLES)}")


def _validate_file_groups(name: str, file_set: Any) -> None:
    """A decomposed set, and the two ways it must agree with the flat one."""
    groups = file_set["groups"]
    if not isinstance(groups, list):
        fail(f"{name}: file_set.groups must be a list")
    if groups and file_set["state"] != "declared":
        fail(f"{name}: only a declared file_set is decomposed into groups")
    for group in groups:
        _validate_one_file_group(name, group)
    if groups:
        here = [
            f
            for g in groups
            if g["dir"] == groups[0]["dir"] and g["files"] is not None
            for f in g["files"]
        ]
        if here != file_set["files"]:
            fail(
                f"{name}: file_set.files must be every group in the answer's own directory whose "
                f"names are established, in order — got {file_set['files']}, groups say {here}"
            )


def _validate_reading(name: str, reading: Any) -> None:
    """One granularity reading: a switch, its live value, where it changes."""
    _require_exact(name, reading, READING_FIELDS, "granularity reading")
    if not isinstance(reading["key"], str) or not reading["key"]:
        fail(f"{name}: a granularity reading's key must be a non-empty string")
    if reading["value"] is not None and not isinstance(reading["value"], str):
        fail(f"{name}: a granularity reading's value must be null or a string")
    if reading["options_file"] is not None and not isinstance(reading["options_file"], str):
        fail(f"{name}: a granularity reading's options_file must be null or a string")


def _validate_alternative(name: str, alternative: Any, granularity_values: set[str]) -> None:
    """One reachable mode: its name, the option combination, its grouping."""
    _require_exact(name, alternative, ALTERNATIVE_FIELDS, "granularity alternative")
    if not isinstance(alternative["mode"], str) or not alternative["mode"]:
        fail(f"{name}: an alternative's mode must be a non-empty string")
    options = alternative["options"]
    if not isinstance(options, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in options.items()
    ):
        fail(f"{name}: an alternative's options must map option keys to values, both strings")
    values = alternative["values"]
    if not isinstance(values, list) or not values:
        fail(f"{name}: an alternative's values must be a non-empty list — its mode groups somehow")
    if any(value not in granularity_values for value in values):
        fail(f"{name}: every alternative's granularity must be one of {sorted(granularity_values)}")


def _validate_granularity(name: str, granularity: Any) -> None:
    _require_exact(name, granularity, GRANULARITY_FIELDS, "granularity")
    granularity_values = KNOWN_GRANULARITIES | {GRANULARITY_VALUE_NONE}
    if granularity["value"] not in granularity_values:
        fail(f"{name}: granularity.value must be one of {sorted(granularity_values)}")
    mode = granularity["mode"]
    if mode is not None and (not isinstance(mode, str) or not mode):
        fail(f"{name}: granularity.mode must be null or a non-empty string")
    readings = granularity["readings"]
    if not isinstance(readings, list):
        fail(f"{name}: granularity.readings must be a list")
    for reading in readings:
        _validate_reading(name, reading)
    alternatives = granularity["alternatives"]
    if not isinstance(alternatives, list):
        fail(f"{name}: granularity.alternatives must be a list")
    for alternative in alternatives:
        _validate_alternative(name, alternative, granularity_values)


def _validate_placement_core(name: str, placement: Any, *, root_kinds: set[str], what: str) -> None:
    """The fields both placement questions answer — one rule, applied twice.

    *root_kinds* is the vocabulary of the question being validated, which is
    the whole reason this takes a parameter: a savestate placement naming
    ``savefile_directory`` is exactly the confusion the split vocabularies
    exist to catch, and a shared set would wave it through.
    """
    if not isinstance(placement["dir"], str) or not placement["dir"]:
        fail(f"{name}: {what}.dir must be a non-empty string")
    if placement["root_kind"] not in root_kinds:
        fail(f"{name}: {what}.root_kind must be one of {sorted(root_kinds)}")
    needs = placement["needs"]
    if not isinstance(needs, list) or not all(isinstance(n, str) for n in needs):
        fail(f"{name}: {what}.needs must be a list of strings")
    unknown_holes = sorted(set(needs) - KNOWN_HOLES)
    if unknown_holes:
        fail(f"{name}: {what}.needs must be holes from {sorted(KNOWN_HOLES)}, got {unknown_holes}")
    for opt_dir in ("fallback_dir", "physical_dir"):
        value = placement[opt_dir]
        if value is not None and (not isinstance(value, str) or not value):
            fail(f"{name}: {what}.{opt_dir} must be null or a non-empty string")
    _validate_file_set(name, placement["file_set"])
    _validate_caveats(name, placement["caveats"])


def _validate_savefile_placement(name: str, placement: Any) -> None:
    _require_exact(name, placement, PLACEMENT_FIELDS, "savefile_location")
    _validate_placement_core(name, placement, root_kinds=KNOWN_ROOT_KINDS, what="savefile_location")
    if placement["granularity"] is not None:
        _validate_granularity(name, placement["granularity"])


def _validate_screenshot_placement(name: str, placement: Any) -> None:
    _require_exact(name, placement, SCREENSHOT_PLACEMENT_FIELDS, "screenshot_location")
    if not isinstance(placement["dir"], str) or not placement["dir"]:
        fail(f"{name}: screenshot_location.dir must be a non-empty string")
    if placement["root_kind"] not in KNOWN_SCREENSHOT_ROOT_KINDS:
        fail(
            f"{name}: screenshot_location.root_kind must be one of "
            f"{sorted(KNOWN_SCREENSHOT_ROOT_KINDS)}"
        )
    needs = placement["needs"]
    if not isinstance(needs, list) or not all(isinstance(n, str) for n in needs):
        fail(f"{name}: screenshot_location.needs must be a list of strings")
    unknown_holes = sorted(set(needs) - KNOWN_HOLES)
    if unknown_holes:
        fail(f"{name}: screenshot_location.needs must be holes from {sorted(KNOWN_HOLES)}, got {unknown_holes}")
    value = placement["physical_dir"]
    if value is not None and (not isinstance(value, str) or not value):
        fail(f"{name}: screenshot_location.physical_dir must be null or a non-empty string")
    _validate_caveats(name, placement["caveats"])


def _validate_savestate_placement(name: str, placement: Any) -> None:
    """A savestate placement — the save shape minus the field it cannot have.

    ``_require_exact`` is what enforces the omission: a vector carrying
    ``"granularity": null`` here is refused rather than quietly accepted, so
    the contract stays the one the serializer actually produces.
    """
    _require_exact(name, placement, SAVESTATE_PLACEMENT_FIELDS, "savestate_location")
    _validate_placement_core(
        name, placement, root_kinds=KNOWN_STATE_ROOT_KINDS, what="savestate_location"
    )
    if placement["file_set"]["complete"]:
        fail(
            f"{name}: savestate_location.file_set.complete must be false — which slots exist is a live "
            "setting away from changing, so no savestate observation is ever closed"
        )


def _validate_unresolved(name: str, outcome: Any, what: str) -> bool:
    """Is this the typed refusal? Refuses a malformed one either way."""
    if not (isinstance(outcome, dict) and set(outcome) == {"unresolved"}):
        return False
    unresolved = outcome["unresolved"]
    _require_exact(name, unresolved, {"code", "data"}, f"{what}.unresolved")
    if unresolved["code"] not in KNOWN_UNRESOLVED_CODES:
        fail(f"{name}: unresolved code must be one of {sorted(KNOWN_UNRESOLVED_CODES)}")
    return True


def _validate_savefile_outcome(name: str, outcome: Any, what: str = "savefile_location") -> None:
    """A savefile answer in either shape: the placement, or the typed refusal.

    Both routes refuse the same way and for the same reasons — a standalone
    emulator on the entry route, a core this installation does not have on
    either — so one validator holds the pair everywhere the answer appears.
    """
    if not _validate_unresolved(name, outcome, what):
        _validate_savefile_placement(name, outcome)


def _validate_screenshot_outcome(name: str, outcome: Any) -> None:
    if isinstance(outcome, dict) and "unresolved" in outcome:
        _validate_unresolved(name, outcome, what="screenshot_location")
        return
    _validate_screenshot_placement(name, outcome)


def _validate_savestate_absence(name: str, outcome: Any, what: str) -> bool:
    """Is this the stated no? Refuses a malformed one either way.

    The savestate question's third shape (#284): ``no_savestates`` states,
    with its citation, that the emulator has no such feature — an answer, not
    a refusal — and the only caveats that may ride it are the card's own.
    """
    if not (isinstance(outcome, dict) and set(outcome) == {"no_savestates"}):
        return False
    absence = outcome["no_savestates"]
    _require_exact(name, absence, {"emulator", "citation", "caveats"}, f"{what}.no_savestates")
    for field in ("emulator", "citation"):
        if not isinstance(absence[field], str) or not absence[field]:
            fail(f"{name}: {what}.no_savestates.{field} must be a non-empty string")
    _validate_caveats(name, absence["caveats"])
    return True


def _validate_savestate_outcome(name: str, outcome: Any, what: str = "savestate_location") -> None:
    """A savestate answer in any of its three shapes — placement, stated no, refusal."""
    if _validate_unresolved(name, outcome, what):
        return
    if _validate_savestate_absence(name, outcome, what):
        return
    _validate_savestate_placement(name, outcome)


def _validate_texture_placement(name: str, placement: Any, what: str) -> None:
    """A texture placement: the directory, the switch, and how the tree is keyed.

    ``enabled`` and ``keying`` are both nullable and null means something
    different in each — nothing established whether replacement is on, and
    nothing cited says how the tree is divided — so both are required to be
    present rather than omittable. A field a vector may leave out is a field a
    port may forget to answer.
    """
    _require_exact(name, placement, TEXTURE_PLACEMENT_FIELDS, what)
    if not isinstance(placement["dir"], str) or not placement["dir"]:
        fail(f"{name}: {what}.dir must be a non-empty string")
    needs = placement["needs"]
    if not isinstance(needs, list) or not all(isinstance(n, str) for n in needs):
        fail(f"{name}: {what}.needs must be a list of strings")
    unknown_holes = sorted(set(needs) - KNOWN_HOLES)
    if unknown_holes:
        fail(f"{name}: {what}.needs must be holes from {sorted(KNOWN_HOLES)}, got {unknown_holes}")
    physical = placement["physical_dir"]
    if physical is not None and (not isinstance(physical, str) or not physical):
        fail(f"{name}: {what}.physical_dir must be null or a non-empty string")
    if physical is not None and needs:
        # A template is not a path: nothing can be link-resolved through a hole,
        # so a vector stating both locks in an answer the resolver cannot give.
        fail(f"{name}: {what} states a physical_dir for a directory that is still a template")
    enabled = placement["enabled"]
    if enabled is not None and not isinstance(enabled, bool):
        fail(f"{name}: {what}.enabled must be true, false, or null")
    keying = placement["keying"]
    if keying is not None and keying not in KNOWN_KEYINGS:
        fail(f"{name}: {what}.keying must be null or one of {sorted(KNOWN_KEYINGS)}")
    _validate_caveats(name, placement["caveats"])


def _validate_texture_outcome(name: str, outcome: Any, what: str = "texture_pack_location") -> None:
    """A texture answer in either shape: the placement, or the typed refusal."""
    if not _validate_unresolved(name, outcome, what):
        _validate_texture_placement(name, outcome, what)


def _validate_mod_tree(name: str, tree: Any, what: str) -> None:
    _require_exact(name, tree, MOD_TREE_FIELDS, what)
    if not isinstance(tree["dir"], str) or not tree["dir"]:
        fail(f"{name}: {what}.dir must be a non-empty string")
    physical = tree["physical_dir"]
    if physical is not None and (not isinstance(physical, str) or not physical):
        fail(f"{name}: {what}.physical_dir must be null or a non-empty string")
    role = tree["role"]
    if role is not None and (not isinstance(role, str) or not role):
        fail(f"{name}: {what}.role must be null or a non-empty string")
    keying = tree["keying"]
    if keying is not None and keying not in KNOWN_KEYINGS:
        fail(f"{name}: {what}.keying must be null or one of {sorted(KNOWN_KEYINGS)}")


def _validate_mod_placement(name: str, placement: Any, what: str) -> None:
    """A mod placement: the trees, the switch, and the holes they all share."""
    _require_exact(name, placement, MOD_PLACEMENT_FIELDS, what)
    trees = placement["trees"]
    if not isinstance(trees, list) or not trees:
        # An emulator with no directory is a refusal, not an empty list.
        fail(f"{name}: {what}.trees must be a non-empty list")
    for index, tree in enumerate(trees):
        _validate_mod_tree(name, tree, f"{what}.trees[{index}]")
    roles = [tree["role"] for tree in trees if isinstance(tree, dict)]
    if len(trees) == 1:
        if roles != [None]:
            fail(f"{name}: {what} states one tree, which names no role — there is nothing to tell apart")
    elif None in roles or len(set(roles)) != len(roles):
        fail(f"{name}: {what} states several trees, so each names its own distinct role")
    needs = placement["needs"]
    if not isinstance(needs, list) or not all(isinstance(n, str) for n in needs):
        fail(f"{name}: {what}.needs must be a list of strings")
    unknown_holes = sorted(set(needs) - KNOWN_HOLES)
    if unknown_holes:
        fail(f"{name}: {what}.needs must be holes from {sorted(KNOWN_HOLES)}, got {unknown_holes}")
    if needs and any(tree.get("physical_dir") is not None for tree in trees if isinstance(tree, dict)):
        # A template is not a path: nothing can be link-resolved through a hole.
        fail(f"{name}: {what} states a physical_dir for a directory that is still a template")
    enabled = placement["enabled"]
    if enabled is not None and not isinstance(enabled, bool):
        fail(f"{name}: {what}.enabled must be true, false, or null")
    _validate_caveats(name, placement["caveats"])


def _validate_mod_outcome(name: str, outcome: Any, what: str = "mod_location") -> None:
    """A mod answer in either shape: the placement, or the typed refusal."""
    if not _validate_unresolved(name, outcome, what):
        _validate_mod_placement(name, outcome, what)


def _validate_soft_patch_candidate(name: str, candidate: Any, what: str) -> None:
    _require_exact(name, candidate, SOFT_PATCH_CANDIDATE_FIELDS, what)
    for key in ("format", "path"):
        if not isinstance(candidate[key], str) or not candidate[key]:
            fail(f"{name}: {what}.{key} must be a non-empty string")
    if not candidate["path"].endswith("." + candidate["format"]):
        # The name IS the format: RetroArch composes it by appending the
        # extension to the content's basename, so a path that ends elsewhere
        # states a file the frontend never looks for.
        fail(f"{name}: {what}.path must end in the format's own extension")
    continuations = candidate["continuations"]
    if not isinstance(continuations, list) or len(continuations) != PATCH_CONTINUATIONS:
        fail(f"{name}: {what}.continuations must list {PATCH_CONTINUATIONS} indexed follow-ups")
    for index, path in enumerate(continuations, start=1):
        if path != f"{candidate['path']}{index}":
            fail(f"{name}: {what}.continuations[{index - 1}] must be the path with '{index}' appended")
    if candidate["attempted"] is not None and not isinstance(candidate["attempted"], bool):
        fail(f"{name}: {what}.attempted must be true, false, or null")


def _validate_soft_patch_outcome(name: str, outcome: Any, what: str = "soft_patch_candidates") -> None:
    """A soft-patching answer in either shape: the candidates, or the typed refusal."""
    if _validate_unresolved(name, outcome, what):
        return
    _require_exact(name, outcome, SOFT_PATCH_FIELDS, what)
    candidates = outcome["candidates"]
    if not isinstance(candidates, list):
        fail(f"{name}: {what}.candidates must be a list")
    # Each candidate first, then the list as a whole: a malformed one would
    # otherwise trip the order rule and name the wrong defect.
    for candidate in candidates:
        _validate_soft_patch_candidate(name, candidate, f"{what} candidate")
    if candidates and [c["format"] for c in candidates] != list(PATCH_FORMAT_ORDER):
        fail(f"{name}: {what}.candidates must be the four formats in attempt order {PATCH_FORMAT_ORDER}")
    if outcome["applies"] is not None and not isinstance(outcome["applies"], bool):
        fail(f"{name}: {what}.applies must be true, false, or null")
    _validate_caveats(name, outcome["caveats"])


def _validate_identity(name: str, identity: Any, what: str) -> None:
    _require_exact(name, identity, IDENTITY_FIELDS, what)
    for key in ("md5", "sha1"):
        if not isinstance(identity[key], str) or not identity[key]:
            fail(f"{name}: {what} {key} must be a non-empty string")
    if not isinstance(identity["size"], int) or identity["size"] < 0:
        fail(f"{name}: {what} size must be a non-negative integer")


def _validate_requirement_fields(name: str, entry: Any) -> None:
    _require_exact(name, entry, FIRMWARE_REQUIREMENT_FIELDS, "each firmware requirement")
    for key in ("system", "file_name", "path", "declared"):
        if not isinstance(entry[key], str) or not entry[key]:
            fail(f"{name}: firmware requirement {key} must be a non-empty string")
    # core_so is null exactly for a card-declared requirement: a standalone
    # emulator has no .so, and inventing one would collide with the real
    # namespace.
    if entry["core_so"] is not None and (not isinstance(entry["core_so"], str) or not entry["core_so"]):
        fail(f"{name}: firmware requirement core_so must be null or a non-empty string")
    if (entry["core_so"] is None) != (entry["system_source"] == "card"):
        fail(f"{name}: a requirement without a core is exactly a card-declared one")
    if entry["need"] not in KNOWN_FIRMWARE_NEEDS:
        fail(f"{name}: firmware requirement need must be one of {sorted(KNOWN_FIRMWARE_NEEDS)}")
    if entry["system_source"] not in KNOWN_SYSTEM_SOURCES:
        fail(f"{name}: firmware requirement system_source must be one of {sorted(KNOWN_SYSTEM_SOURCES)}")
    if entry["system_source"] == "none" and entry["system"] != "_unknown":
        fail(f"{name}: with no source for the system the slug must be '_unknown'")


def _validate_requirement_path(name: str, entry: Any, root: str) -> None:
    # The root itself is a legal destination: LRPS2 declares the FOLDER
    # "pcsx2/bios", and RetroDECK links it back to the firmware root, so that
    # declaration resolves to the root exactly.
    #
    # A card-declared requirement is the exception to containment: its
    # destination is the standalone emulator's own tree (Cemu probes its keys
    # below its user data path), which no firmware root contains.
    if (
        entry["system_source"] != "card"
        and entry["path"] != root
        and not entry["path"].startswith(f"{root}/")
    ):
        fail(f"{name}: a requirement's path must be the absolute destination under the root {root!r}")
    if os.path.normpath(entry["path"]) != entry["path"]:
        fail(f"{name}: a requirement's path must be normalized — no '..' segment may survive into an answer")
    if os.path.basename(entry["declared"]) != entry["file_name"]:
        fail(f"{name}: a requirement's file_name must be the name the core spelled at the end of 'declared'")
    # 'declared' is not required to be relative. RetroArch composes it with the
    # system directory whatever it looks like (fill_pathname_join,
    # file_path.c:983-993), so an absolute declaration lands under the root like
    # any other one. The containment rule is the path check above, not the
    # spelling of 'declared'.


def _validate_requirement_presence(name: str, entry: Any) -> None:
    found = entry["found"]
    if found not in KNOWN_PATH_KINDS:
        fail(f"{name}: firmware requirement found must be one of {sorted(KNOWN_PATH_KINDS)}")
    present = entry["present"]
    expected_present = None if found == "inaccessible" else found in ("file", "directory")
    if present is not expected_present:
        fail(f"{name}: with found={found!r} the requirement's present must be {expected_present!r}")
    identity = entry["identity"]
    if identity is not None:
        _validate_identity(name, identity, "a requirement's identity")


def _validate_absent_requirement(name: str, found: str, checked: Any, satisfied: Any) -> None:
    """Nothing is at the destination, so there is no verdict to carry."""
    if checked is not None:
        fail(f"{name}: nothing is there to check, so checked must be null")
    expected = False if found == "missing" else None
    if satisfied is not expected:
        fail(f"{name}: with found={found!r} the requirement's satisfied must be {expected!r}")


def _validate_file_requirement(
    name: str, identity: Any, checked: Any, satisfied: Any, *, hash_checked: bool
) -> None:
    """A file is at the destination: what was checked settles what is satisfied."""
    if identity is None and checked != "unknown":
        fail(f"{name}: with no known identity the bytes cannot be established — checked must be 'unknown'")
    if identity is not None and not hash_checked and checked != "unchecked":
        fail(f"{name}: without hash checking a known identity can only be 'unchecked', never a verdict")
    # The invariant a present-but-wrong file used to slip through.
    if checked == "mismatch" and satisfied is not False:
        fail(f"{name}: a file whose bytes are known to be wrong is never satisfied, present or not")
    if checked == "unknown" and satisfied is not (None if identity is not None else True):
        fail(f"{name}: 'unknown' is undetermined when an identity exists and settled when none can")
    if checked == "unchecked" and satisfied is not None:
        fail(f"{name}: an identity that exists and was not verified is not an all-clear")
    if checked == "verified" and satisfied is not True:
        fail(f"{name}: a verified file is satisfied")


def _validate_requirement_verdict(name: str, entry: Any, *, hash_checked: bool) -> None:
    found = entry["found"]
    checked = entry["checked"]
    satisfied = entry["satisfied"]
    if satisfied is not None and not isinstance(satisfied, bool):
        fail(f"{name}: firmware requirement satisfied must be true, false, or null")
    if found in ("missing", "inaccessible"):
        _validate_absent_requirement(name, found, checked, satisfied)
        return
    if checked not in KNOWN_FIRMWARE_CHECKED:
        fail(f"{name}: firmware requirement checked must be one of {sorted(KNOWN_FIRMWARE_CHECKED)}")
    if found == "directory":
        # Something is there and nothing about it was established — a core may
        # even have meant the folder (LRPS2 does).
        if checked != "unknown" or satisfied is not None:
            fail(f"{name}: a directory at the destination is checked='unknown' with satisfied null")
        return
    _validate_file_requirement(name, entry["identity"], checked, satisfied, hash_checked=hash_checked)


def _validate_requirement(name: str, entry: Any, *, root: str, hash_checked: bool) -> None:
    _validate_requirement_fields(name, entry)
    _validate_requirement_path(name, entry, root)
    _validate_requirement_presence(name, entry)
    _validate_requirement_verdict(name, entry, hash_checked=hash_checked)


def _validate_core_identity(name: str, core: Any) -> None:
    core_so = core["core_so"]
    if core_so is not None and (not isinstance(core_so, str) or not core_so):
        fail(f"{name}: firmware core core_so must be null or a non-empty string")
    label = core["label"]
    if label is not None and (not isinstance(label, str) or not label):
        fail(f"{name}: firmware core label must be null or a non-empty string")
    if core_so is None and label is None:
        fail(f"{name}: an emulator with neither a core nor a catalogue label cannot be identified")
    if core["declaration"] not in KNOWN_DECLARATION_STATES:
        fail(f"{name}: firmware core declaration must be one of {sorted(KNOWN_DECLARATION_STATES)}")


def _validate_core_requirements(name: str, core: Any, *, root: str, hash_checked: bool) -> None:
    requirements = core["requirements"]
    if not isinstance(requirements, list):
        fail(f"{name}: firmware core requirements must be a list")
    if core["declaration"] not in ("read", "packaged") and requirements:
        fail(f"{name}: a core atlas could not read declares nothing — its requirements must be empty")
    if core["declaration"] != "read" and not core["caveats"]:
        fail(
            f"{name}: a declaration that was not read off the machine must state why (or, "
            "packaged, its provenance) — an unexplained list reads as 'needs nothing'"
        )
    for entry in requirements:
        if entry["core_so"] != core["core_so"]:
            fail(f"{name}: a requirement must name the core it is listed under")
        _validate_requirement(name, entry, root=root, hash_checked=hash_checked)


def _validate_core_refusals(name: str, core: Any) -> None:
    refused = core["refused"]
    if not isinstance(refused, list):
        fail(f"{name}: firmware core refused must be a list")
    for item in refused:
        _require_exact(name, item, REFUSED_FIELDS, "each refused declaration")
        if not isinstance(item["declared"], str) or not item["declared"]:
            fail(f"{name}: a refused declaration must state what was declared")
        if item["need"] not in KNOWN_FIRMWARE_NEEDS:
            fail(f"{name}: a refused declaration's need must be one of {sorted(KNOWN_FIRMWARE_NEEDS)}")
        if item["reason"] not in KNOWN_REFUSAL_REASONS:
            fail(f"{name}: a refusal must say which fact it rests on: {sorted(KNOWN_REFUSAL_REASONS)}")
        # This is also what keeps a refused declaration from vanishing from the
        # answer: every refusal demands a caveat carrying its own reason, so a
        # core with refusals and no caveats is refused here, one item in.
        if not any(c["code"] == item["reason"] for c in core["caveats"]):
            fail(f"{name}: a refusal's reason must be stated as a caveat on the same core")


def _validate_core_verdict(name: str, core: Any, met: Any) -> None:
    """``requirements_met`` is derived, never asserted: recompute and compare."""
    requirements = core["requirements"]
    refused = core["refused"]
    required = [r for r in requirements if r["need"] == "required"]
    if core["declaration"] != "read":
        expected = None
    elif any(r["satisfied"] is False for r in required):
        expected = False
    elif any(r["satisfied"] is None for r in required):
        expected = None
    elif any(r["need"] == "required" for r in refused):
        # A required file atlas refused to look at is not an all-clear.
        expected = None
    else:
        expected = True
    if met is not expected:
        fail(
            f"{name}: requirements_met must be {expected!r} for this core — it is the one field a client "
            "renders, and deriving it wrongly is the whole failure mode"
        )


def _validate_firmware_core(name: str, core: Any, *, root: str, hash_checked: bool) -> None:
    _require_exact(name, core, FIRMWARE_CORE_FIELDS, "each firmware core")
    _validate_core_identity(name, core)
    _validate_core_requirements(name, core, root=root, hash_checked=hash_checked)
    # The one number a client renders: it may never be true out of
    # ignorance, and never with a required file that is not usable.
    met = core["requirements_met"]
    if met is not None and not isinstance(met, bool):
        fail(f"{name}: firmware core requirements_met must be true, false, or null")
    _validate_core_refusals(name, core)
    _validate_core_verdict(name, core, met)
    _validate_caveats(name, core["caveats"])


def _validate_unclaimed(name: str, entry: Any, *, root: str, hash_checked: bool) -> None:
    _require_exact(name, entry, UNCLAIMED_FIELDS, "each unclaimed file")
    if not isinstance(entry["path"], str) or not entry["path"].startswith(f"{root}/"):
        fail(f"{name}: an unclaimed file's path must be absolute under the root {root!r}")
    identity = entry["identity"]
    known_as = entry["known_as"]
    if not isinstance(known_as, list) or not all(isinstance(n, str) and n for n in known_as):
        fail(f"{name}: unclaimed known_as must be a list of non-empty names")
    if identity is None:
        if known_as:
            fail(f"{name}: an unrecognised file is known as nothing — known_as must be empty")
    else:
        _validate_identity(name, identity, "an unclaimed file's identity")
        if not known_as:
            fail(f"{name}: recognised content is known under at least the name it was matched by")
        if not hash_checked:
            fail(f"{name}: an unclaimed file is identified by content — impossible without hash checking")


def _validate_firmware_coverage(name: str, firmware: Any, cores: Any) -> None:
    """An answer that established nothing must say so, and never overclaim.

    A packaged declaration counts as an establishment: the card is the
    source, its provenance caveat rides the entry, and the destinations were
    read live — the answer is not empty-for-lack-of-looking.
    """
    if not any(core["declaration"] in ("read", "packaged") for core in cores) and not any(
        c["code"] in NOTHING_READ_CODES for c in firmware["caveats"]
    ):
        fail(
            f"{name}: with no core declaration read, the answer must carry one of {sorted(NOTHING_READ_CODES)} "
            "— empty must never read as complete"
        )
    if any(c["code"] == "system-unknown" for c in firmware["caveats"]):
        if cores:
            fail(f"{name}: 'system-unknown' means nothing covers the identifier — no emulator may be listed")
        # A read failure is not evidence about the machine. Claiming an
        # identifier is unknown requires that the enumeration actually ran.
        blind = {"emulator-catalogue-unreadable", "info-path-unresolved"}
        if any(c["code"] in blind for c in firmware["caveats"]):
            fail(
                f"{name}: 'system-unknown' claims the machine has no such emulator, which an answer that "
                "could not read the enumeration may never say"
            )


def _validate_firmware(name: str, firmware: Any) -> None:
    _require_exact(name, firmware, FIRMWARE_FIELDS, "firmware")
    root = firmware["root"]
    if root is not None and (not isinstance(root, str) or not root):
        fail(f"{name}: firmware.root must be null or a non-empty string")
    hash_checked = firmware["hash_checked"]
    if not isinstance(hash_checked, bool):
        fail(f"{name}: firmware.hash_checked must be a boolean")
    cores = firmware["cores"]
    unclaimed = firmware["unclaimed"]
    if not isinstance(cores, list) or not isinstance(unclaimed, list):
        fail(f"{name}: firmware.cores and firmware.unclaimed must be lists")
    if root is None:
        if cores or unclaimed:
            fail(
                f"{name}: without a root there is nothing to resolve against — cores and unclaimed must be empty"
            )
        _validate_caveats(name, firmware["caveats"])
        return
    for core in cores:
        _validate_firmware_core(name, core, root=root, hash_checked=hash_checked)
    for entry in unclaimed:
        _validate_unclaimed(name, entry, root=root, hash_checked=hash_checked)
    _validate_firmware_coverage(name, firmware, cores)
    _validate_caveats(name, firmware["caveats"])


def _validate_identified_content(name: str, identification: Any) -> None:
    """Content is either recognised — and then named — or explicitly not."""
    identity = identification["identity"]
    known_as = identification["known_as"]
    if not isinstance(known_as, list) or not all(isinstance(n, str) and n for n in known_as):
        fail(f"{name}: identification known_as must be a list of non-empty names")
    if identity is None:
        if known_as or identification["requirements"]:
            fail(f"{name}: unrecognised content has no names and satisfies nothing")
        if not any(c["code"] in UNIDENTIFIED_CODES for c in identification["caveats"]):
            fail(
                f"{name}: content with no identity must say which kind of nothing it is "
                f"({sorted(UNIDENTIFIED_CODES)}), or an empty answer reads as 'wanted nowhere'"
            )
        return
    _validate_identity(name, identity, "the identification's identity")
    if not known_as:
        fail(f"{name}: recognised content is known under at least one name")


def _validate_identified_requirements(name: str, identification: Any) -> None:
    identity = identification["identity"]
    for entry in identification["requirements"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            fail(f"{name}: each identified requirement must carry its absolute destination path")
        if entry.get("identity") != identity:
            fail(f"{name}: identification returns only requirements that expect exactly this content")
        # The destination directory stands in for the root here: an
        # identification is about content, and carries no root of its own.
        _validate_requirement(
            name, entry, root=os.path.dirname(entry["path"]), hash_checked=False
        )


def _validate_identification(name: str, identification: Any, query: dict[str, Any]) -> None:
    _require_exact(name, identification, IDENTIFICATION_FIELDS, "identification")
    _validate_identified_content(name, identification)
    _validate_identified_requirements(name, identification)
    # A size is not an identity, so a request carrying only one identifies
    # nothing — and says that, instead of the caller meeting an exception.
    if not ({"md5", "sha1"} & set(query)) and not any(
        c["code"] == "firmware-content-unstated" for c in identification["caveats"]
    ):
        fail(f"{name}: a query with neither md5 nor sha1 names no content and must answer that it does not")
    _validate_caveats(name, identification["caveats"])


def _validate_emulator(name: str, entry: Any) -> None:
    _require_exact(name, entry, EMULATOR_FIELDS, "each emulator")
    if not isinstance(entry["label"], str) or not entry["label"]:
        fail(f"{name}: emulator label must be a non-empty string")
    if entry["kind"] not in KNOWN_EMULATOR_KINDS:
        fail(f"{name}: emulator kind must be one of {sorted(KNOWN_EMULATOR_KINDS)}")
    core_so = entry["core_so"]
    if core_so is not None and (not isinstance(core_so, str) or not core_so):
        fail(f"{name}: emulator core_so must be null or a non-empty string")
    selection = entry["selection"]
    if selection is not None and not isinstance(selection, str):
        fail(f"{name}: emulator selection must be null or a string")
    if not isinstance(entry["system"], str) or not entry["system"]:
        fail(f"{name}: emulator system must be a non-empty string")
    _validate_caveats(name, entry["caveats"])


def _no_catalogue_codes_in(caveats: list[Any]) -> list[str]:
    """The stated reasons, if any, that no catalogue was read.

    An answer that names anything has, by definition, read a catalogue; these
    codes say the opposite, and a vector asserting both would lock in an answer
    no arrangement can produce.
    """
    return sorted({c["code"] for c in caveats} & NO_CATALOGUE_CODES)


def _validate_rom_location_dirs(name: str, placement: dict[str, Any]) -> None:
    """The pair of directory views: each a non-empty string or null, and the
    physical view never stated without the resolved dir it backs."""
    for key in ("dir", "physical_dir"):
        if placement[key] is not None and (not isinstance(placement[key], str) or not placement[key]):
            fail(f"{name}: expected.rom_location.{key} must be a non-empty string or null")
    if placement["physical_dir"] is not None and placement["dir"] is None:
        fail(f"{name}: expected.rom_location.physical_dir without a dir — nothing was resolved to back")


def _validate_rom_location(name: str, placement: Any) -> None:
    """A ROM placement: the pair of directory views, the declaration, and why not."""
    fields = {"dir", "physical_dir", "extensions", "caveats"}
    if not isinstance(placement, dict) or set(placement) != fields:
        fail(f"{name}: expected.rom_location must carry exactly {sorted(fields)}")
    _validate_rom_location_dirs(name, placement)
    if not isinstance(placement["extensions"], list) or not all(
        isinstance(e, str) and e for e in placement["extensions"]
    ):
        fail(f"{name}: expected.rom_location.extensions must be a list of non-empty strings")
    _validate_caveats(name, placement["caveats"])
    # Both facts come out of the same <system> element, so a catalogue nobody
    # read yields neither — an expectation stating either alongside one of those
    # codes locks in an answer no resolver can produce.
    unread = _no_catalogue_codes_in(placement["caveats"])
    if unread and (placement["dir"] or placement["extensions"]):
        fail(f"{name}: expected.rom_location states an answer and {unread}")
    # No directory is never the whole answer: every branch that resolves none
    # says which kind of none it is, and a vector asserting silence would lock
    # in the one shape the caveats exist to prevent — a client reading null as
    # "look in the default place".
    if placement["dir"] is None and not placement["caveats"]:
        fail(f"{name}: expected.rom_location resolved no dir and states no caveat saying why")


def _validate_catalogue(name: str, catalogue: Any) -> None:
    """A catalogue answer: the entries, and why there are none when there are none."""
    if not isinstance(catalogue, dict) or set(catalogue) != {"entries", "caveats"}:
        fail(f"{name}: expected.catalogue must be {{'entries': [...], 'caveats': [...]}}")
    if not isinstance(catalogue["entries"], list):
        fail(f"{name}: expected.catalogue.entries must be a list")
    for entry in catalogue["entries"]:
        _validate_emulator(name, entry)
    _validate_caveats(name, catalogue["caveats"])
    unread = _no_catalogue_codes_in(catalogue["caveats"])
    # A derived answer is the one legal pairing (issue #133): the entries come
    # from the installed cores' own declarations, and the no-catalogue code
    # beside them says exactly why they had to.
    derived = any(c["code"] == "emulator-list-derived" for c in catalogue["caveats"])
    if catalogue["entries"] and unread and not derived:
        fail(f"{name}: expected.catalogue states entries and {unread} without emulator-list-derived")


def _validate_platform_systems(name: str, answer: Any) -> None:
    """A forward platform answer: resolved platforms, matches with status and provenance."""
    fields = {"vocabulary", "value", "platforms", "matches", "caveats"}
    if not isinstance(answer, dict) or set(answer) != fields:
        fail(f"{name}: expected.systems_for_platform must carry exactly {sorted(fields)}")
    if answer["vocabulary"] not in KNOWN_PLATFORM_VOCABULARIES:
        fail(
            f"{name}: expected.systems_for_platform.vocabulary must be one of "
            f"{sorted(KNOWN_PLATFORM_VOCABULARIES)}"
        )
    if not isinstance(answer["value"], str) or not answer["value"]:
        fail(f"{name}: expected.systems_for_platform.value must be a non-empty string")
    if not isinstance(answer["platforms"], list) or not all(
        isinstance(p, str) and p for p in answer["platforms"]
    ):
        fail(f"{name}: expected.systems_for_platform.platforms must be a list of non-empty strings")
    if not isinstance(answer["matches"], list):
        fail(f"{name}: expected.systems_for_platform.matches must be a list")
    for match in answer["matches"]:
        _validate_platform_match(name, match)
    _validate_caveats(name, answer["caveats"])
    unmapped = any(c["code"] == "platform-unmapped" for c in answer["caveats"])
    if bool(answer["platforms"]) == unmapped:
        fail(
            f"{name}: expected.systems_for_platform must resolve platforms or state "
            "platform-unmapped — exactly one of the two"
        )


def _validate_platform_match(name: str, match: Any) -> None:
    fields = {"system", "status", "platforms", "tags_source"}
    if not isinstance(match, dict) or set(match) != fields:
        fail(f"{name}: each systems_for_platform match must carry exactly {sorted(fields)}")
    if not isinstance(match["system"], str) or not match["system"]:
        fail(f"{name}: a match's system must be a non-empty string")
    if match["status"] not in KNOWN_PLATFORM_STATUSES:
        fail(f"{name}: a match's status must be one of {sorted(KNOWN_PLATFORM_STATUSES)}")
    if match["tags_source"] not in KNOWN_PLATFORM_TAG_SOURCES:
        fail(f"{name}: a match's tags_source must be one of {sorted(KNOWN_PLATFORM_TAG_SOURCES)}")
    if not isinstance(match["platforms"], list) or not all(
        isinstance(p, str) and p for p in match["platforms"]
    ):
        fail(f"{name}: a match's platforms must be a list of non-empty strings")


def _validate_platform_ids(name: str, answer: Any) -> None:
    """A reverse platform answer: the tags, their identities, and the qualifying status."""
    fields = {"system", "status", "tags_source", "platforms", "identities", "caveats"}
    if not isinstance(answer, dict) or set(answer) != fields:
        fail(f"{name}: expected.platform_ids must carry exactly {sorted(fields)}")
    if not isinstance(answer["system"], str) or not answer["system"]:
        fail(f"{name}: expected.platform_ids.system must be a non-empty string")
    if answer["status"] not in KNOWN_PLATFORM_STATUSES:
        fail(f"{name}: expected.platform_ids.status must be one of {sorted(KNOWN_PLATFORM_STATUSES)}")
    if answer["tags_source"] not in KNOWN_PLATFORM_TAG_SOURCES:
        fail(
            f"{name}: expected.platform_ids.tags_source must be one of "
            f"{sorted(KNOWN_PLATFORM_TAG_SOURCES)}"
        )
    if not isinstance(answer["platforms"], list) or not all(
        isinstance(p, str) and p for p in answer["platforms"]
    ):
        fail(f"{name}: expected.platform_ids.platforms must be a list of non-empty strings")
    if not isinstance(answer["identities"], list):
        fail(f"{name}: expected.platform_ids.identities must be a list")
    for identity in answer["identities"]:
        _validate_platform_identity(name, identity)
    _validate_caveats(name, answer["caveats"])


def _validate_igdb_identity(name: str, entry: Any) -> None:
    if (
        not isinstance(entry, dict)
        or set(entry) != {"id", "slug", "name"}
        or not isinstance(entry["id"], int)
        or not isinstance(entry["slug"], str)
        or not isinstance(entry["name"], str)
    ):
        fail(f"{name}: each igdb identity must be {{'id': int, 'slug': str, 'name': str}}")


def _validate_platform_identity(name: str, identity: Any) -> None:
    fields = {"platform", "igdb", "libretro", "screenscraper", "thegamesdb"}
    if not isinstance(identity, dict) or set(identity) != fields:
        fail(f"{name}: each platform identity must carry exactly {sorted(fields)}")
    if not isinstance(identity["platform"], str) or not identity["platform"]:
        fail(f"{name}: an identity's platform must be a non-empty string")
    if not isinstance(identity["igdb"], list):
        fail(f"{name}: an identity's igdb must be a list")
    for entry in identity["igdb"]:
        _validate_igdb_identity(name, entry)
    if not isinstance(identity["libretro"], list) or not all(
        isinstance(n, str) and n for n in identity["libretro"]
    ):
        fail(f"{name}: an identity's libretro must be a list of non-empty strings")
    for key in ("screenscraper", "thegamesdb"):
        if identity[key] is not None and not isinstance(identity[key], int):
            fail(f"{name}: an identity's {key} must be an integer or null")


def _validate_launchable(name: str, answer: Any) -> None:
    """A launchability answer: the verdict, and the couplings only it may state.

    ``entry`` travels with the two verdicts that have one — an emulator named
    for a file nothing launches would answer a different question, and a
    launchable verdict without the entry that would run has thrown half its
    answer away. ``alternatives`` is ``entry-not-accepted``'s remedy alone
    (and may be empty even there — none established is a legal answer).
    """
    fields = {"verdict", "extension", "accepted", "entry", "alternatives", "caveats"}
    if not isinstance(answer, dict) or set(answer) != fields:
        fail(f"{name}: expected.launchable must carry exactly {sorted(fields)}")
    if answer["verdict"] not in KNOWN_LAUNCH_VERDICTS:
        fail(f"{name}: expected.launchable.verdict must be one of {sorted(KNOWN_LAUNCH_VERDICTS)}")
    if not isinstance(answer["extension"], str) or not answer["extension"]:
        fail(f"{name}: expected.launchable.extension must be a non-empty string")
    for list_key in ("accepted", "alternatives"):
        if not isinstance(answer[list_key], list) or not all(
            isinstance(token, str) and token for token in answer[list_key]
        ):
            fail(f"{name}: expected.launchable.{list_key} must be a list of non-empty strings")
    entry_verdicts = {"launchable", "entry-not-accepted"}
    if (answer["entry"] is not None) != (answer["verdict"] in entry_verdicts):
        fail(f"{name}: expected.launchable.entry travels with {sorted(entry_verdicts)} alone")
    if answer["alternatives"] and answer["verdict"] != "entry-not-accepted":
        fail(f"{name}: expected.launchable.alternatives travels with 'entry-not-accepted' alone")
    if answer["entry"] is not None:
        _validate_emulator(name, answer["entry"])
    _validate_caveats(name, answer["caveats"])


def _validate_systems(name: str, answer: Any) -> None:
    """A systems answer: what the catalogue declares, and why nothing when nothing."""
    if not isinstance(answer, dict) or set(answer) != {"systems", "caveats"}:
        fail(f"{name}: expected.systems must be {{'systems': [...], 'caveats': [...]}}")
    if not isinstance(answer["systems"], list) or not all(
        isinstance(s, str) and s for s in answer["systems"]
    ):
        fail(f"{name}: expected.systems.systems must be a list of non-empty strings")
    _validate_caveats(name, answer["caveats"])
    unread = _no_catalogue_codes_in(answer["caveats"])
    derived = any(c["code"] == "emulator-list-derived" for c in answer["caveats"])
    if answer["systems"] and unread and not derived:
        fail(f"{name}: expected.systems states systems and {unread} without emulator-list-derived")


def _validate_aggregate_answer(name: str, answered: Any, question: str) -> None:
    """One labelled answer: the handle it came from, and that question's own form.

    The answer is held to the shape that question already has — the aggregate
    adds none of its own. A question whose vocabulary was widened without a
    shape to validate it by is refused here rather than checked as some other
    question's answer.
    """
    _require_exact(name, answered, AGGREGATE_ANSWER_FIELDS, "each aggregate answer")
    if question == "savefile_location":
        _validate_savefile_outcome(name, answered["answer"])
    elif question == "savestate_location":
        _validate_savestate_outcome(name, answered["answer"])
    elif question == "texture_pack_location":
        _validate_texture_outcome(name, answered["answer"])
    elif question == "emulators_for":
        _validate_catalogue(name, answered["answer"])
    else:
        fail(f"{name}: no answer shape is defined for the aggregate question {question!r}")


def _validate_aggregate(name: str, aggregate: Any, query: dict[str, Any], installations: Any) -> None:
    """The aggregate answer: every detected installation's answer, labelled, in order.

    The labels are held to ``expected.installations`` exactly — same handles,
    same order, none dropped and none added. That is the whole guarantee the
    aggregate makes: it fans out over what detection found, in detection order,
    and never picks a winner. It is also what makes the empty machine a
    truthful answer rather than a special case — no installations, no answers.
    """
    if not isinstance(aggregate, list):
        fail(f"{name}: expected.aggregate must be a list")
    for answered in aggregate:
        _validate_aggregate_answer(name, answered, query["question"])
    labels = [answered["installation"] for answered in aggregate]
    _validate_installations(name, labels)
    if labels != installations:
        fail(
            f"{name}: expected.aggregate must answer for exactly the detected installations, in "
            f"detection order — got {[label['kind'] for label in labels]}, "
            f"expected {[inst['kind'] for inst in installations]}"
        )


def _validate_expected(name: str, expected: Any, inp: dict[str, Any]) -> None:
    if not isinstance(expected, dict):
        fail(f"{name}: expected must be an object")
    keys = set(expected)
    allowed = {
        "installations",
        "savefile_location",
        "aggregate",
        "catalogue",
        "systems",
        "launchable",
        "rom_location",
        "entry_savefile_location",
        "savestate_location",
        "entry_savestate_location",
        "screenshot_location",
        "texture_pack_location",
        "entry_texture_pack_location",
        "soft_patch_candidates",
        "mod_location",
        "entry_mod_location",
        "firmware",
        "identification",
        "systems_for_platform",
        "platform_ids",
    }
    if "installations" not in keys or not keys <= allowed:
        fail(f"{name}: expected keys must be 'installations' plus optional {sorted(allowed - {'installations'})}")
    if ("aggregate" in keys) != ("aggregate_query" in inp):
        fail(f"{name}: aggregate_query and aggregate expectation must appear together")
    if ("catalogue" in keys) != ("catalogue_query" in inp):
        fail(f"{name}: catalogue_query and catalogue expectation must appear together")
    if ("systems" in keys) != ("systems_query" in inp):
        fail(f"{name}: systems_query and systems expectation must appear together")
    if ("systems_for_platform" in keys) != ("platform_systems_query" in inp):
        fail(f"{name}: platform_systems_query and systems_for_platform expectation must appear together")
    if ("platform_ids" in keys) != ("platform_ids_query" in inp):
        fail(f"{name}: platform_ids_query and platform_ids expectation must appear together")
    if ("launchable" in keys) != ("launchable_query" in inp):
        fail(f"{name}: launchable_query and launchable expectation must appear together")
    if ("rom_location" in keys) != ("rom_location_query" in inp):
        fail(f"{name}: rom_location_query and rom_location expectation must appear together")
    if ("savefile_location" in keys) != ("savefile_query" in inp):
        fail(f"{name}: a savefile_query and a savefile_location expectation must appear together")
    if ("entry_savefile_location" in keys) != ("entry_savefile_query" in inp):
        fail(f"{name}: entry_savefile_query and entry_savefile_location expectation must appear together")
    if ("savestate_location" in keys) != ("savestate_query" in inp):
        fail(f"{name}: savestate_query and savestate_location expectation must appear together")
    if ("screenshot_location" in keys) != ("screenshot_query" in inp):
        fail(f"{name}: screenshot_query and screenshot_location expectation must appear together")
    if ("entry_savestate_location" in keys) != ("entry_savestate_query" in inp):
        fail(f"{name}: entry_savestate_query and entry_savestate_location expectation must appear together")
    if ("texture_pack_location" in keys) != ("texture_query" in inp):
        fail(f"{name}: texture_query and texture_pack_location expectation must appear together")
    if ("entry_texture_pack_location" in keys) != ("entry_texture_query" in inp):
        fail(
            f"{name}: entry_texture_query and entry_texture_pack_location expectation must appear together"
        )
    if ("mod_location" in keys) != ("mod_query" in inp):
        fail(f"{name}: mod_query and mod_location expectation must appear together")
    if ("entry_mod_location" in keys) != ("entry_mod_query" in inp):
        fail(f"{name}: entry_mod_query and entry_mod_location expectation must appear together")
    if ("soft_patch_candidates" in keys) != ("soft_patch_query" in inp):
        fail(f"{name}: soft_patch_query and soft_patch_candidates expectation must appear together")
    if ("firmware" in keys) != ("firmware_query" in inp):
        fail(f"{name}: firmware_query and firmware expectation must appear together")
    if ("identification" in keys) != ("identify_query" in inp):
        fail(f"{name}: identify_query and identification expectation must appear together")
    _validate_installations(name, expected["installations"])
    # 'aggregate' is deliberately not in this set: asking every installation on
    # a machine that has none is a question with a truthful empty answer, not a
    # question nobody can answer.
    if (
        keys
        & {
            "savefile_location",
            "savestate_location",
            "entry_savefile_location",
            "entry_savestate_location",
            "screenshot_location",
            "texture_pack_location",
            "entry_texture_pack_location",
            "soft_patch_candidates",
            "mod_location",
            "entry_mod_location",
            "catalogue",
            "systems",
            "launchable",
            "firmware",
            "identification",
            "systems_for_platform",
            "platform_ids",
        }
    ) and not expected["installations"]:
        fail(f"{name}: a resolver expectation needs a detected installation to answer it")
    if "savefile_location" in keys:
        _validate_savefile_outcome(name, expected["savefile_location"])
    if "aggregate" in keys:
        _validate_aggregate(name, expected["aggregate"], inp["aggregate_query"], expected["installations"])
    if "catalogue" in keys:
        _validate_catalogue(name, expected["catalogue"])
    if "rom_location" in keys:
        _validate_rom_location(name, expected["rom_location"])
    if "systems" in keys:
        _validate_systems(name, expected["systems"])
    if "systems_for_platform" in keys:
        _validate_platform_systems(name, expected["systems_for_platform"])
    if "platform_ids" in keys:
        _validate_platform_ids(name, expected["platform_ids"])
    if "launchable" in keys:
        _validate_launchable(name, expected["launchable"])
    if "savestate_location" in keys:
        _validate_savestate_outcome(name, expected["savestate_location"])
    if "screenshot_location" in keys:
        _validate_screenshot_outcome(name, expected["screenshot_location"])
    if "entry_savefile_location" in keys:
        _validate_savefile_outcome(name, expected["entry_savefile_location"], "entry_savefile_location")
    if "entry_savestate_location" in keys:
        _validate_savestate_outcome(name, expected["entry_savestate_location"], "entry_savestate_location")
    if "texture_pack_location" in keys:
        _validate_texture_outcome(name, expected["texture_pack_location"])
    if "entry_texture_pack_location" in keys:
        _validate_texture_outcome(
            name, expected["entry_texture_pack_location"], "entry_texture_pack_location"
        )
    if "mod_location" in keys:
        _validate_mod_outcome(name, expected["mod_location"])
    if "entry_mod_location" in keys:
        _validate_mod_outcome(name, expected["entry_mod_location"], "entry_mod_location")
    if "soft_patch_candidates" in keys:
        _validate_soft_patch_outcome(name, expected["soft_patch_candidates"])
    if "firmware" in keys:
        _validate_firmware(name, expected["firmware"])
    if "identification" in keys:
        _validate_identification(name, expected["identification"], inp["identify_query"])


def validate_machines_vector(vector: dict[str, Any]) -> None:
    name = vector["name"]
    if not set(vector) <= {"name", "rationale", "input", "expected"}:
        fail(f"{name}: vector keys must be name/rationale/input/expected")
    if "rationale" in vector and not isinstance(vector["rationale"], str):
        fail(f"{name}: rationale must be a string when present")
    _validate_input(name, vector.get("input"))
    _validate_expected(name, vector.get("expected"), vector.get("input", {}))


FAMILIES = {
    "machines": ("machines", validate_machines_vector),
}


def validate_file(path: Path, family_name: str, validate_vector, seen_inputs: dict[str, str]) -> int:
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        fail(f"not valid JSON: {exc}")
    for key in ("family", "schema", "spec", "description", "vectors"):
        if key not in data:
            fail(f"missing top-level key {key!r}")
    if data["family"] != family_name:
        fail(f"family must be {family_name!r}, got {data['family']!r}")
    if data["schema"] != SCHEMA:
        fail(f"schema must be {SCHEMA}, got {data['schema']!r}")
    names: set[str] = set()
    for idx, vector in enumerate(data["vectors"]):
        name = vector.get("name")
        if not isinstance(name, str) or not name:
            fail(f"vector #{idx}: missing or empty name")
        if name in names:
            fail(f"vector #{idx}: duplicate name {name!r}")
        names.add(name)
        validate_vector(vector)
        # Canonical inputs are unique across the whole family — a duplicate
        # would let one guarantee silently shadow another (REVIEW M5).
        canonical = json.dumps(vector["input"], sort_keys=True)
        if canonical in seen_inputs:
            fail(f"vector {name!r}: duplicate canonical input (already used by {seen_inputs[canonical]!r})")
        seen_inputs[canonical] = name
    return len(data["vectors"])


def main() -> None:
    total = 0
    file_count = 0
    for directory, (family_name, validate_vector) in sorted(FAMILIES.items()):
        files = sorted((REPO_ROOT / "vectors" / directory).glob("*.json"))
        if not files:
            print(f"no vector files found under vectors/{directory}/")
            raise SystemExit(1)
        seen_inputs: dict[str, str] = {}
        for path in files:
            try:
                total += validate_file(path, family_name, validate_vector, seen_inputs)
            except VectorError as exc:
                print(f"{path.relative_to(REPO_ROOT)}: {exc}")
                raise SystemExit(1) from None
            file_count += 1
    print(f"OK: {total} vectors across {file_count} files")


if __name__ == "__main__":
    main()
