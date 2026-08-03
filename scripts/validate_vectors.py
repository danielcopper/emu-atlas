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
}
QUERY_FIELDS = {"content_path", "core_so", "installation"}
FIRMWARE_QUERY_FIELDS = {"installation", "platform", "verify"}
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
FIRMWARE_FIELDS = {"root", "hash_checked", "files", "caveats"}
FIRMWARE_FILE_FIELDS = {"path", "state", "required", "hash_known", "cores"}
KNOWN_FIRMWARE_STATES = {"verified", "mismatch", "present", "missing", "undeclared"}
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
    "card-mode-unconfirmed",
    "card-generation-mismatch",
    "no-firmware-declaration",
    "info-path-unresolved",
    "core-dir-unresolved",
    "firmware-root-missing",
}
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


def _validate_firmware_query(name: str, query: Any) -> None:
    if not isinstance(query, dict) or not set(query) <= FIRMWARE_QUERY_FIELDS:
        fail(f"{name}: input.firmware_query keys must be a subset of {sorted(FIRMWARE_QUERY_FIELDS)}")
    for key in ("installation", "platform"):
        if key in query and (not isinstance(query[key], str) or not query[key]):
            fail(f"{name}: input.firmware_query.{key} must be a non-empty string")
    if "verify" in query and not isinstance(query["verify"], bool):
        fail(f"{name}: input.firmware_query.verify must be a boolean")
    if "installation" in query and query["installation"] not in KNOWN_KINDS:
        fail(f"{name}: input.firmware_query.installation must be one of {sorted(KNOWN_KINDS)}")


def _validate_entry_query(name: str, query: Any) -> None:
    if not isinstance(query, dict):
        fail(f"{name}: input.entry_query must be an object")
    keys = set(query)
    if "system" not in keys or not keys <= {"system", "label", "content_path"}:
        fail(f"{name}: input.entry_query must carry 'system' plus optional 'label'/'content_path'")
    for key in keys:
        if not isinstance(query[key], str) or not query[key]:
            fail(f"{name}: input.entry_query.{key} must be a non-empty string")


def _validate_input(name: str, inp: Any) -> None:
    if not isinstance(inp, dict):
        fail(f"{name}: input must be an object")
    keys = set(inp)
    if not INPUT_FIELDS_REQUIRED <= keys or not keys <= (INPUT_FIELDS_REQUIRED | INPUT_FIELDS_OPTIONAL):
        fail(f"{name}: input keys must be {sorted(INPUT_FIELDS_REQUIRED)} plus optional {sorted(INPUT_FIELDS_OPTIONAL)}")
    if not isinstance(inp["home"], str) or not inp["home"]:
        fail(f"{name}: input.home must be a non-empty string")
    files = inp["files"]
    if not isinstance(files, dict):
        fail(f"{name}: input.files must be an object")
    for path, spec in files.items():
        if not isinstance(path, str):
            fail(f"{name}: input.files keys must be strings")
        if isinstance(spec, str):
            continue
        if not isinstance(spec, dict):
            fail(f"{name}: input.files[{path!r}] must be string content or an object spec")
        if "status" in spec:
            if set(spec) != {"status"} or spec["status"] not in KNOWN_FILE_STATUSES:
                fail(
                    f"{name}: input.files[{path!r}] status spec must be exactly "
                    f"{{'status': one of {sorted(KNOWN_FILE_STATUSES)}}}"
                )
            continue
        # A binary blob: it exists, reads as invalid-text, and answers the
        # declared identity for file_size / file_digest.
        if not set(spec) or not set(spec) <= BLOB_FIELDS:
            fail(f"{name}: input.files[{path!r}] blob spec keys must be a non-empty subset of {sorted(BLOB_FIELDS)}")
        for key in ("md5", "sha1"):
            if key in spec and (not isinstance(spec[key], str) or not spec[key]):
                fail(f"{name}: input.files[{path!r}].{key} must be a non-empty string")
        if "size" in spec and (not isinstance(spec["size"], int) or spec["size"] < 0):
            fail(f"{name}: input.files[{path!r}].size must be a non-negative integer")
    for list_key in ("dirs", "inaccessible"):
        entries = inp.get(list_key, [])
        if not isinstance(entries, list) or not all(isinstance(e, str) and e for e in entries):
            fail(f"{name}: input.{list_key} must be a list of non-empty path strings")
    symlinks = inp.get("symlinks", {})
    if not isinstance(symlinks, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in symlinks.items()
    ):
        fail(f"{name}: input.symlinks must be an object of link paths to target strings")
    cores = inp.get("cores", {})
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
    file_set = placement["file_set"]
    _require_exact(name, file_set, FILE_SET_FIELDS, "save_location.file_set")
    if file_set["state"] not in KNOWN_FILE_SET_STATES:
        fail(f"{name}: file_set.state must be one of {sorted(KNOWN_FILE_SET_STATES)}")
    if not isinstance(file_set["files"], list) or not all(isinstance(f, str) for f in file_set["files"]):
        fail(f"{name}: file_set.files must be a list of strings")
    if not isinstance(file_set["complete"], bool):
        fail(f"{name}: file_set.complete must be a boolean")
    if file_set["state"] == "unknown" and (file_set["files"] or file_set["complete"]):
        fail(f"{name}: an unknown file_set carries no files and no completeness claim")
    granularity = placement["granularity"]
    if granularity is not None:
        _require_exact(name, granularity, GRANULARITY_FIELDS, "granularity")
        if not isinstance(granularity["value"], str) or not granularity["value"]:
            fail(f"{name}: granularity.value must be a non-empty string")
        alternatives = granularity["alternatives"]
        if not isinstance(alternatives, list) or not all(
            isinstance(a, list) and len(a) == 2 and all(isinstance(x, str) for x in a) for a in alternatives
        ):
            fail(f"{name}: granularity.alternatives must be a list of [value, granularity] string pairs")
    _validate_caveats(name, placement["caveats"])


def _validate_entry_outcome(name: str, outcome: Any) -> None:
    if isinstance(outcome, dict) and set(outcome) == {"unresolved"}:
        unresolved = outcome["unresolved"]
        _require_exact(name, unresolved, {"code", "data"}, "entry_save_location.unresolved")
        if unresolved["code"] not in KNOWN_UNRESOLVED_CODES:
            fail(f"{name}: unresolved code must be one of {sorted(KNOWN_UNRESOLVED_CODES)}")
        return
    _validate_placement(name, outcome)


def _validate_firmware(name: str, firmware: Any) -> None:
    _require_exact(name, firmware, FIRMWARE_FIELDS, "firmware")
    root = firmware["root"]
    if root is not None and (not isinstance(root, str) or not root):
        fail(f"{name}: firmware.root must be null or a non-empty string")
    if not isinstance(firmware["hash_checked"], bool):
        fail(f"{name}: firmware.hash_checked must be a boolean")
    files = firmware["files"]
    if not isinstance(files, list):
        fail(f"{name}: firmware.files must be a list")
    if root is None and files:
        fail(f"{name}: without a root there is nothing to state a file's state against — files must be empty")
    for entry in files:
        _require_exact(name, entry, FIRMWARE_FILE_FIELDS, "each firmware file")
        if not isinstance(entry["path"], str) or not entry["path"]:
            fail(f"{name}: firmware file path must be a non-empty string")
        if entry["state"] not in KNOWN_FIRMWARE_STATES:
            fail(f"{name}: firmware file state must be one of {sorted(KNOWN_FIRMWARE_STATES)}")
        for flag in ("required", "hash_known"):
            if not isinstance(entry[flag], bool):
                fail(f"{name}: firmware file {flag} must be a boolean")
        cores = entry["cores"]
        if not isinstance(cores, list) or not all(isinstance(c, str) and c for c in cores):
            fail(f"{name}: firmware file cores must be a list of non-empty core names")
        if entry["state"] == "undeclared" and (cores or entry["required"]):
            fail(f"{name}: an undeclared firmware file has no declaring core and is never required")
        if entry["state"] != "undeclared" and not cores:
            fail(f"{name}: a declared firmware file must name the core(s) that declare it")
        if entry["state"] in ("verified", "mismatch") and not entry["hash_known"]:
            fail(f"{name}: {entry['state']!r} is only reachable for a file whose identity is known")
    if not firmware["hash_checked"] and any(f["state"] in ("verified", "mismatch") for f in files):
        fail(f"{name}: no file can be verified or mismatched when hash checking did not run")
    _validate_caveats(name, firmware["caveats"])


def _validate_emulators(name: str, emulators: Any) -> None:
    if not isinstance(emulators, list):
        fail(f"{name}: expected.emulators must be a list")
    for entry in emulators:
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


def _validate_expected(name: str, expected: Any, inp: dict[str, Any]) -> None:
    if not isinstance(expected, dict):
        fail(f"{name}: expected must be an object")
    keys = set(expected)
    allowed = {"installations", "save_location", "emulators", "entry_save_location", "firmware"}
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
    _validate_installations(name, expected["installations"])
    if (keys & {"save_location", "entry_save_location", "emulators", "firmware"}) and not expected["installations"]:
        fail(f"{name}: a resolver expectation needs a detected installation to answer it")
    if "save_location" in keys:
        _validate_placement(name, expected["save_location"])
    if "emulators" in keys:
        _validate_emulators(name, expected["emulators"])
    if "entry_save_location" in keys:
        _validate_entry_outcome(name, expected["entry_save_location"])
    if "firmware" in keys:
        _validate_firmware(name, expected["firmware"])


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
