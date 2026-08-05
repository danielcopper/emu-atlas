"""Validate the shape of every vector file under vectors/ (schema 2). Stdlib only.

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

SCHEMA = 2

INPUT_FIELDS_REQUIRED = {"home", "files"}
INPUT_FIELDS_OPTIONAL = {
    "symlinks",
    "cores",
    "dirs",
    "inaccessible",
    "query",
    "catalogue_query",
    "entry_query",
    "firmware_query",
    "identify_query",
}
QUERY_FIELDS = {"content_path", "core_so", "installation"}
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
FILE_SET_FIELDS = {"state", "files", "complete"}
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
KNOWN_DECLARATION_STATES = {"read", "unreadable", "absent"}
KNOWN_SYSTEM_SOURCES = {"override", "systemname", "slug", "none"}
UNCLAIMED_FIELDS = {"path", "identity", "known_as"}
IDENTIFICATION_FIELDS = {"identity", "known_as", "requirements", "caveats"}
IDENTITY_FIELDS = {"md5", "sha1", "size"}
KNOWN_FIRMWARE_NEEDS = {"required", "optional"}
KNOWN_FIRMWARE_CHECKED = {"verified", "mismatch", "unchecked", "unknown"}
GRANULARITY_FIELDS = {"value", "option_key", "option_value", "options_file", "alternatives"}
CAVEAT_FIELDS = {"code", "data"}
EMULATOR_FIELDS = {"label", "kind", "core_so", "selection", "caveats"}
KNOWN_KINDS = {"retrodeck", "emudeck", "standalone_retroarch_flatpak", "native_retroarch"}
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
}
KNOWN_ROOT_KINDS = {"savefile_directory", "content_directory", "system_directory"}
KNOWN_FILE_SET_STATES = {"observed", "declared", "unknown"}
KNOWN_EMULATOR_KINDS = {"libretro", "standalone"}
KNOWN_CAVEAT_CODES = {
    "no-core",
    "core-unqueryable",
    "sorted-dir-missing",
    "sorted-dir-uncreatable",
    "dead-symlink",
    "health",
    "filenames-unverified",
    "unknown-option-value",
    "system-directory-unset",
    "per-game-overrides-present",
    "per-game-override",
    "unverified-version",
    "invalid-save-directory",
    "core-suspect",
    "core-unaudited",
    "core-multi-option",
    "card-mode-unconfirmed",
    "card-generation-mismatch",
    "sandbox-path-untranslated",
    "app-relative-path-unexpanded",
    "cfg-line-dropped",
    "cfg-value-rejected",
    "no-firmware-declaration",
    "info-path-unresolved",
    "core-dir-unresolved",
    "firmware-root-missing",
    "core-not-installed",
    "standalone-emulator",
    "emulator-catalogue-unavailable",
    "firmware-unreadable",
    "firmware-content-unidentified",
    "system-unknown",
    "system-assignment-derived",
    "core-without-systemname",
    "system-assignment-may-hide-cores",
    "core-info-unreadable",
    "emulator-catalogue-unreadable",
    "firmware-path-obstructed",
    "firmware-path-inaccessible",
    "firmware-path-escapes-root",
    "firmware-path-unresolvable",
    "firmware-path-names-no-file",
    "firmware-content-contradictory",
}
# The codes that may stand in for "nothing could be read here". Each says a
# different thing to a client — nothing declares firmware, the identifier is
# unknown here, the named core is absent — and none of them may be read as
# "nothing needed".
NOTHING_READ_CODES = {"no-firmware-declaration", "system-unknown", "core-not-installed"}
KNOWN_UNRESOLVED_CODES = {"standalone-unsupported"}


class VectorError(Exception):
    pass


def fail(message: str) -> NoReturn:
    raise VectorError(message)


def _require_exact(name: str, obj: Any, fields: set[str], what: str) -> None:
    if not isinstance(obj, dict) or set(obj) != fields:
        fail(f"{name}: {what} must be exactly the fields {sorted(fields)}, got {obj!r}")


def _validate_query(name: str, query: Any) -> None:
    if not isinstance(query, dict):
        fail(f"{name}: input.query must be an object")
    keys = set(query)
    if not keys or not keys <= QUERY_FIELDS:
        fail(f"{name}: input.query keys must be a non-empty subset of {sorted(QUERY_FIELDS)}")
    for key in keys:
        if not isinstance(query[key], str) or not query[key]:
            fail(f"{name}: input.query.{key} must be a non-empty string")
    if "installation" in query and query["installation"] not in KNOWN_KINDS:
        fail(f"{name}: input.query.installation must be one of {sorted(KNOWN_KINDS)}")


def _validate_firmware_query_fields(name: str, query: Any) -> None:
    for key in ("installation", "kind", "core_so", "system"):
        if key in query and (not isinstance(query[key], str) or not query[key]):
            fail(f"{name}: input.firmware_query.{key} must be a non-empty string")
    if "verify" in query and not isinstance(query["verify"], bool):
        fail(f"{name}: input.firmware_query.verify must be a boolean")
    if "installation" in query and query["installation"] not in KNOWN_KINDS:
        fail(f"{name}: input.firmware_query.installation must be one of {sorted(KNOWN_KINDS)}")


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
    if "installation" in query and query["installation"] not in KNOWN_KINDS:
        fail(f"{name}: input.identify_query.installation must be one of {sorted(KNOWN_KINDS)}")
    if "md5" not in query and "sha1" not in query:
        fail(f"{name}: input.identify_query needs md5 or sha1 — size alone is not an identity")


def _validate_entry_query(name: str, query: Any) -> None:
    if not isinstance(query, dict):
        fail(f"{name}: input.entry_query must be an object")
    keys = set(query)
    if "system" not in keys or not keys <= {"system", "label", "content_path"}:
        fail(f"{name}: input.entry_query must carry 'system' plus optional 'label'/'content_path'")
    for key in keys:
        if not isinstance(query[key], str) or not query[key]:
            fail(f"{name}: input.entry_query.{key} must be a non-empty string")


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


def _validate_file_spec(name: str, path: str, spec: Any) -> None:
    """One input.files entry: string content, a read failure, or a blob."""
    if isinstance(spec, str):
        return
    if not isinstance(spec, dict):
        fail(f"{name}: input.files[{path!r}] must be string content or an object spec")
    if "status" in spec:
        if set(spec) != {"status"} or spec["status"] not in KNOWN_FILE_STATUSES:
            fail(
                f"{name}: input.files[{path!r}] status spec must be exactly "
                f"{{'status': one of {sorted(KNOWN_FILE_STATUSES)}}}"
            )
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
    for list_key in ("dirs", "inaccessible"):
        entries = inp.get(list_key, [])
        if not isinstance(entries, list) or not all(isinstance(e, str) and e for e in entries):
            fail(f"{name}: input.{list_key} must be a list of non-empty path strings")
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


def _validate_input_queries(name: str, inp: Any) -> None:
    """The optional question an input asks, at most one shape per family."""
    if "query" in inp:
        _validate_query(name, inp["query"])
    if "catalogue_query" in inp:
        cq = inp["catalogue_query"]
        if (
            not isinstance(cq, dict)
            or "system" not in cq
            or not set(cq) <= {"system", "content_path"}
            or not all(isinstance(v, str) and v for v in cq.values())
        ):
            fail(f"{name}: input.catalogue_query must carry 'system' plus optional 'content_path'")
    if "entry_query" in inp:
        _validate_entry_query(name, inp["entry_query"])
    if "firmware_query" in inp:
        _validate_firmware_query(name, inp["firmware_query"])
    if "identify_query" in inp:
        _validate_identify_query(name, inp["identify_query"])


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
    _validate_input_queries(name, inp)


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
        health = inst["health"]
        if not isinstance(health, list) or not all(h in KNOWN_HEALTH_ISSUES for h in health):
            fail(
                f"{name}: installation health must be a list of issue codes from "
                f"{sorted(KNOWN_HEALTH_ISSUES)}, got {health!r}"
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
    _require_exact(name, file_set, FILE_SET_FIELDS, "save_location.file_set")
    if file_set["state"] not in KNOWN_FILE_SET_STATES:
        fail(f"{name}: file_set.state must be one of {sorted(KNOWN_FILE_SET_STATES)}")
    if not isinstance(file_set["files"], list) or not all(isinstance(f, str) for f in file_set["files"]):
        fail(f"{name}: file_set.files must be a list of strings")
    if not isinstance(file_set["complete"], bool):
        fail(f"{name}: file_set.complete must be a boolean")
    if file_set["state"] == "unknown" and (file_set["files"] or file_set["complete"]):
        fail(f"{name}: an unknown file_set carries no files and no completeness claim")


def _validate_granularity(name: str, granularity: Any) -> None:
    _require_exact(name, granularity, GRANULARITY_FIELDS, "granularity")
    if not isinstance(granularity["value"], str) or not granularity["value"]:
        fail(f"{name}: granularity.value must be a non-empty string")
    alternatives = granularity["alternatives"]
    if not isinstance(alternatives, list) or not all(
        isinstance(a, list) and len(a) == 2 and all(isinstance(x, str) for x in a) for a in alternatives
    ):
        fail(f"{name}: granularity.alternatives must be a list of [value, granularity] string pairs")


def _validate_placement(name: str, placement: Any) -> None:
    _require_exact(name, placement, PLACEMENT_FIELDS, "save_location")
    if not isinstance(placement["dir"], str) or not placement["dir"]:
        fail(f"{name}: save_location.dir must be a non-empty string")
    if placement["root_kind"] not in KNOWN_ROOT_KINDS:
        fail(f"{name}: save_location.root_kind must be one of {sorted(KNOWN_ROOT_KINDS)}")
    needs = placement["needs"]
    if not isinstance(needs, list) or not all(isinstance(n, str) for n in needs):
        fail(f"{name}: save_location.needs must be a list of strings")
    for opt_dir in ("fallback_dir", "physical_dir"):
        value = placement[opt_dir]
        if value is not None and (not isinstance(value, str) or not value):
            fail(f"{name}: save_location.{opt_dir} must be null or a non-empty string")
    _validate_file_set(name, placement["file_set"])
    if placement["granularity"] is not None:
        _validate_granularity(name, placement["granularity"])
    _validate_caveats(name, placement["caveats"])


def _validate_entry_outcome(name: str, outcome: Any) -> None:
    if isinstance(outcome, dict) and set(outcome) == {"unresolved"}:
        unresolved = outcome["unresolved"]
        _require_exact(name, unresolved, {"code", "data"}, "entry_save_location.unresolved")
        if unresolved["code"] not in KNOWN_UNRESOLVED_CODES:
            fail(f"{name}: unresolved code must be one of {sorted(KNOWN_UNRESOLVED_CODES)}")
        return
    _validate_placement(name, outcome)


def _validate_identity(name: str, identity: Any, what: str) -> None:
    _require_exact(name, identity, IDENTITY_FIELDS, what)
    for key in ("md5", "sha1"):
        if not isinstance(identity[key], str) or not identity[key]:
            fail(f"{name}: {what} {key} must be a non-empty string")
    if not isinstance(identity["size"], int) or identity["size"] < 0:
        fail(f"{name}: {what} size must be a non-negative integer")


def _validate_requirement_fields(name: str, entry: Any) -> None:
    _require_exact(name, entry, FIRMWARE_REQUIREMENT_FIELDS, "each firmware requirement")
    for key in ("core_so", "system", "file_name", "path", "declared"):
        if not isinstance(entry[key], str) or not entry[key]:
            fail(f"{name}: firmware requirement {key} must be a non-empty string")
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
    if entry["path"] != root and not entry["path"].startswith(f"{root}/"):
        fail(f"{name}: a requirement's path must be the absolute destination under the root {root!r}")
    if os.path.normpath(entry["path"]) != entry["path"]:
        fail(f"{name}: a requirement's path must be normalized — no '..' segment may survive into an answer")
    if os.path.basename(entry["declared"]) != entry["file_name"]:
        fail(f"{name}: a requirement's file_name must be the name the core spelled at the end of 'declared'")
    if os.path.isabs(entry["declared"]):
        fail(f"{name}: a declared firmware path is relative to the root; an absolute one is never answered")


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
    if core["declaration"] != "read":
        if requirements:
            fail(f"{name}: a core atlas could not read declares nothing — its requirements must be empty")
        if not core["caveats"]:
            fail(
                f"{name}: an empty requirement list from an unread core must state why, or it reads as "
                "'needs nothing'"
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
        if not any(c["code"] == item["reason"] for c in core["caveats"]):
            fail(f"{name}: a refusal's reason must be stated as a caveat on the same core")
    if refused and not core["caveats"]:
        fail(f"{name}: a refused declaration must be stated, or the file vanishes from the answer")


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
    """An answer that established nothing must say so, and never overclaim."""
    if not any(core["declaration"] == "read" for core in cores) and not any(
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
        if not any(c["code"] == "firmware-content-unidentified" for c in identification["caveats"]):
            fail(f"{name}: unrecognised content must say so, or an empty answer reads as 'wanted nowhere'")
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


def _validate_identification(name: str, identification: Any) -> None:
    _require_exact(name, identification, IDENTIFICATION_FIELDS, "identification")
    _validate_identified_content(name, identification)
    _validate_identified_requirements(name, identification)
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
    if not isinstance(entry["caveats"], list) or not all(
        c in KNOWN_CAVEAT_CODES for c in entry["caveats"]
    ):
        fail(f"{name}: emulator caveats must be a list of known caveat codes")


def _validate_emulators(name: str, emulators: Any) -> None:
    if not isinstance(emulators, list):
        fail(f"{name}: expected.emulators must be a list")
    for entry in emulators:
        _validate_emulator(name, entry)


def _validate_expected(name: str, expected: Any, inp: dict[str, Any]) -> None:
    if not isinstance(expected, dict):
        fail(f"{name}: expected must be an object")
    keys = set(expected)
    allowed = {
        "installations",
        "save_location",
        "emulators",
        "entry_save_location",
        "firmware",
        "identification",
    }
    if "installations" not in keys or not keys <= allowed:
        fail(f"{name}: expected keys must be 'installations' plus optional {sorted(allowed - {'installations'})}")
    if ("emulators" in keys) != ("catalogue_query" in inp):
        fail(f"{name}: catalogue_query and emulators expectation must appear together")
    if ("save_location" in keys) != ("query" in inp):
        fail(f"{name}: a query and a save_location expectation must appear together")
    if ("entry_save_location" in keys) != ("entry_query" in inp):
        fail(f"{name}: entry_query and entry_save_location expectation must appear together")
    if ("firmware" in keys) != ("firmware_query" in inp):
        fail(f"{name}: firmware_query and firmware expectation must appear together")
    if ("identification" in keys) != ("identify_query" in inp):
        fail(f"{name}: identify_query and identification expectation must appear together")
    _validate_installations(name, expected["installations"])
    if (
        keys & {"save_location", "entry_save_location", "emulators", "firmware", "identification"}
    ) and not expected["installations"]:
        fail(f"{name}: a resolver expectation needs a detected installation to answer it")
    if "save_location" in keys:
        _validate_placement(name, expected["save_location"])
    if "emulators" in keys:
        _validate_emulators(name, expected["emulators"])
    if "entry_save_location" in keys:
        _validate_entry_outcome(name, expected["entry_save_location"])
    if "firmware" in keys:
        _validate_firmware(name, expected["firmware"])
    if "identification" in keys:
        _validate_identification(name, expected["identification"])


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
