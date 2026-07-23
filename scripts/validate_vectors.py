"""Validate the shape of every vector file under vectors/. Stdlib only.

Catches malformed vectors independently of the runner: a vector file must parse,
carry the family header matching its directory, and every vector must have the
family's declared input/expected shape with no stray keys. A query and a
save-location expectation come as a pair — a machine you ask a placement of must
carry both, a machine you don't must carry neither.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NoReturn

REPO_ROOT = Path(__file__).resolve().parents[1]

INPUT_FIELDS_REQUIRED = {"home", "files"}
INPUT_FIELDS_OPTIONAL = {"symlinks", "cores", "query", "catalogue_query"}
QUERY_FIELDS_OPTIONAL = {"content_path", "core_so"}
INSTALLATION_FIELDS = {"kind", "root", "health"}
PLACEMENT_FIELDS = {"dir", "root_kind", "needs", "file_set"}
FILE_SET_FIELDS = {"state", "files"}
KNOWN_KINDS = {"retrodeck", "emudeck", "standalone_retroarch_flatpak", "native_retroarch"}
KNOWN_HEALTH = {"ok", "root_missing", "config_unreadable"}
KNOWN_ROOT_KINDS = {"savefile_directory", "content_directory", "system_directory"}
KNOWN_FILE_SET_STATES = {"observed", "declared", "unknown"}
KNOWN_CAVEAT_CODES = {
    "no-core",
    "core-unqueryable",
    "sorted-dir-missing",
    "health",
    "filenames-unverified",
    "unknown-option-value",
    "system-directory-unset",
    "per-game-overrides-present",
    "per-game-override",
}


class VectorError(Exception):
    pass


def fail(message: str) -> NoReturn:
    raise VectorError(message)


def _validate_query(name: str, query: Any) -> None:
    if not isinstance(query, dict):
        fail(f"{name}: input.query must be an object")
    keys = set(query)
    if not keys or not keys <= QUERY_FIELDS_OPTIONAL:
        fail(f"{name}: input.query keys must be a non-empty subset of {sorted(QUERY_FIELDS_OPTIONAL)}")
    for key in keys:
        if not isinstance(query[key], str) or not query[key]:
            fail(f"{name}: input.query.{key} must be a non-empty string")


def _validate_input(name: str, inp: Any) -> None:
    if not isinstance(inp, dict):
        fail(f"{name}: input must be an object")
    keys = set(inp)
    if not INPUT_FIELDS_REQUIRED <= keys or not keys <= (INPUT_FIELDS_REQUIRED | INPUT_FIELDS_OPTIONAL):
        fail(f"{name}: input keys must be {sorted(INPUT_FIELDS_REQUIRED)} plus optional {sorted(INPUT_FIELDS_OPTIONAL)}")
    if not isinstance(inp["home"], str) or not inp["home"]:
        fail(f"{name}: input.home must be a non-empty string")
    files = inp["files"]
    if not isinstance(files, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in files.items()
    ):
        fail(f"{name}: input.files must be an object of string paths to string contents")
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
    if "query" in inp:
        _validate_query(name, inp["query"])
    if "catalogue_query" in inp:
        cq = inp["catalogue_query"]
        if not isinstance(cq, dict) or set(cq) != {"system"} or not isinstance(cq["system"], str):
            fail(f"{name}: input.catalogue_query must be exactly {{'system': str}}")


def _validate_installations(name: str, installations: Any) -> None:
    if not isinstance(installations, list):
        fail(f"{name}: expected.installations must be a list")
    for inst in installations:
        if not isinstance(inst, dict) or set(inst) != INSTALLATION_FIELDS:
            fail(f"{name}: each installation must be exactly {sorted(INSTALLATION_FIELDS)}")
        if inst["kind"] not in KNOWN_KINDS:
            fail(f"{name}: installation kind must be one of {sorted(KNOWN_KINDS)}, got {inst['kind']!r}")
        if not isinstance(inst["root"], str) or not inst["root"]:
            fail(f"{name}: installation root must be a non-empty string")
        if inst["health"] not in KNOWN_HEALTH:
            fail(f"{name}: installation health must be one of {sorted(KNOWN_HEALTH)}, got {inst['health']!r}")


def _validate_placement(name: str, placement: Any) -> None:
    optional = {"granularity", "caveats"}
    if not isinstance(placement, dict) or not (
        PLACEMENT_FIELDS <= set(placement) and set(placement) <= PLACEMENT_FIELDS | optional
    ):
        fail(f"{name}: save_location must be exactly {sorted(PLACEMENT_FIELDS)} plus optional {sorted(optional)}")
    if "caveats" in placement:
        codes = placement["caveats"]
        if not isinstance(codes, list) or not all(c in KNOWN_CAVEAT_CODES for c in codes):
            fail(f"{name}: caveats must be a list of known caveat codes {sorted(KNOWN_CAVEAT_CODES)}")
    if "granularity" in placement:
        gran = placement["granularity"]
        if not isinstance(gran, dict) or "value" not in gran or not (
            set(gran) <= {"value", "option_key", "option_value"}
        ):
            fail(f"{name}: granularity must be an object with 'value' plus optional 'option_key'/'option_value'")
    if not isinstance(placement["dir"], str) or not placement["dir"]:
        fail(f"{name}: save_location.dir must be a non-empty string")
    if placement["root_kind"] not in KNOWN_ROOT_KINDS:
        fail(f"{name}: save_location.root_kind must be one of {sorted(KNOWN_ROOT_KINDS)}")
    needs = placement["needs"]
    if not isinstance(needs, list) or not all(isinstance(n, str) for n in needs):
        fail(f"{name}: save_location.needs must be a list of strings")
    file_set = placement["file_set"]
    if not isinstance(file_set, dict) or set(file_set) != FILE_SET_FIELDS:
        fail(f"{name}: save_location.file_set must be exactly {sorted(FILE_SET_FIELDS)}")
    if file_set["state"] not in KNOWN_FILE_SET_STATES:
        fail(f"{name}: file_set.state must be one of {sorted(KNOWN_FILE_SET_STATES)}")
    if not isinstance(file_set["files"], list) or not all(isinstance(f, str) for f in file_set["files"]):
        fail(f"{name}: file_set.files must be a list of strings")
    if file_set["state"] == "unknown" and file_set["files"]:
        fail(f"{name}: an unknown file_set must carry no files (never guessed)")


def _validate_expected(name: str, expected: Any, has_query: bool, has_catalogue_query: bool) -> None:
    if not isinstance(expected, dict):
        fail(f"{name}: expected must be an object")
    keys = set(expected)
    if "installations" not in keys or not keys <= {"installations", "save_location", "emulators"}:
        fail(f"{name}: expected keys must be 'installations' plus optional 'save_location'/'emulators'")
    if ("emulators" in keys) != has_catalogue_query:
        fail(f"{name}: catalogue_query and emulators expectation must appear together")
    if "emulators" in keys:
        for entry in expected["emulators"]:
            if not isinstance(entry, dict) or not set(entry) <= {"label", "kind", "core_so", "selection"} or "label" not in entry:
                fail(f"{name}: each emulator must carry 'label' plus optional 'kind'/'core_so'")
    _validate_installations(name, expected["installations"])
    has_placement = "save_location" in expected
    if has_placement != has_query:
        fail(f"{name}: a query and a save_location expectation must appear together (query={has_query}, placement={has_placement})")
    if has_placement:
        if not expected["installations"]:
            fail(f"{name}: a save_location is expected but no installation was detected to answer it")
        _validate_placement(name, expected["save_location"])


def validate_machines_vector(vector: dict[str, Any]) -> None:
    name = vector["name"]
    if "rationale" in vector and not isinstance(vector["rationale"], str):
        fail(f"{name}: rationale must be a string when present")
    _validate_input(name, vector.get("input"))
    _validate_expected(
        name,
        vector.get("expected"),
        has_query="query" in vector.get("input", {}),
        has_catalogue_query="catalogue_query" in vector.get("input", {}),
    )


FAMILIES = {
    "machines": ("machines", validate_machines_vector),
}


def validate_file(path: Path, family_name: str, validate_vector) -> int:
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        fail(f"not valid JSON: {exc}")
    for key in ("family", "spec", "description", "vectors"):
        if key not in data:
            fail(f"missing top-level key {key!r}")
    if data["family"] != family_name:
        fail(f"family must be {family_name!r}, got {data['family']!r}")
    names: set[str] = set()
    for idx, vector in enumerate(data["vectors"]):
        name = vector.get("name")
        if not isinstance(name, str) or not name:
            fail(f"vector #{idx}: missing or empty name")
        if name in names:
            fail(f"vector #{idx}: duplicate name {name!r}")
        names.add(name)
        validate_vector(vector)
    return len(data["vectors"])


def main() -> None:
    total = 0
    file_count = 0
    for directory, (family_name, validate_vector) in sorted(FAMILIES.items()):
        files = sorted((REPO_ROOT / "vectors" / directory).glob("*.json"))
        if not files:
            print(f"no vector files found under vectors/{directory}/")
            raise SystemExit(1)
        for path in files:
            try:
                total += validate_file(path, family_name, validate_vector)
            except VectorError as exc:
                print(f"{path.relative_to(REPO_ROOT)}: {exc}")
                raise SystemExit(1) from None
            file_count += 1
    print(f"OK: {total} vectors across {file_count} files")


if __name__ == "__main__":
    main()
