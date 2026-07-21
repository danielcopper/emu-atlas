"""Validate the shape of every vector file under vectors/. Stdlib only.

Catches malformed vectors independently of the runner: a vector file must parse,
carry the family header matching its directory, and every vector must have the
family's declared input/expected shape with no stray keys. A query and a
save-placement expectation come as a pair — a machine you ask a placement of must
carry both, a machine you don't must carry neither.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NoReturn

REPO_ROOT = Path(__file__).resolve().parents[1]

INPUT_FIELDS_REQUIRED = {"home", "files"}
INPUT_FIELDS_OPTIONAL = {"query"}
QUERY_FIELDS_REQUIRED = {"system"}
QUERY_FIELDS_OPTIONAL = {"core", "rom_dir_name"}
INSTALLATION_FIELDS = {"kind", "root"}
PLACEMENT_FIELDS = {"dir", "filename", "needs"}
KNOWN_KINDS = {"retrodeck", "standalone_retroarch_flatpak", "native_retroarch"}


class VectorError(Exception):
    pass


def fail(message: str) -> NoReturn:
    raise VectorError(message)


def _validate_query(name: str, query: Any) -> None:
    if not isinstance(query, dict):
        fail(f"{name}: input.query must be an object")
    keys = set(query)
    if not QUERY_FIELDS_REQUIRED <= keys or not keys <= (QUERY_FIELDS_REQUIRED | QUERY_FIELDS_OPTIONAL):
        fail(f"{name}: input.query keys must be {sorted(QUERY_FIELDS_REQUIRED)} plus optional {sorted(QUERY_FIELDS_OPTIONAL)}")
    if not isinstance(query["system"], str) or not query["system"]:
        fail(f"{name}: input.query.system must be a non-empty string")
    for opt in QUERY_FIELDS_OPTIONAL:
        if opt in query and not isinstance(query[opt], str):
            fail(f"{name}: input.query.{opt} must be a string when present")


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
    if "query" in inp:
        _validate_query(name, inp["query"])


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


def _validate_placement(name: str, placement: Any) -> None:
    if not isinstance(placement, dict) or set(placement) != PLACEMENT_FIELDS:
        fail(f"{name}: save_placement must be exactly {sorted(PLACEMENT_FIELDS)}")
    if not isinstance(placement["dir"], str) or not placement["dir"]:
        fail(f"{name}: save_placement.dir must be a non-empty string")
    if not isinstance(placement["filename"], str) or not placement["filename"]:
        fail(f"{name}: save_placement.filename must be a non-empty string")
    needs = placement["needs"]
    if not isinstance(needs, list) or not all(isinstance(n, str) for n in needs):
        fail(f"{name}: save_placement.needs must be a list of strings")


def _validate_expected(name: str, expected: Any, has_query: bool) -> None:
    if not isinstance(expected, dict):
        fail(f"{name}: expected must be an object")
    keys = set(expected)
    if "installations" not in keys or not keys <= {"installations", "save_placement"}:
        fail(f"{name}: expected keys must be 'installations' plus optional 'save_placement'")
    _validate_installations(name, expected["installations"])
    has_placement = "save_placement" in expected
    if has_placement != has_query:
        fail(f"{name}: a query and a save_placement expectation must appear together (query={has_query}, placement={has_placement})")
    if has_placement:
        if not expected["installations"]:
            fail(f"{name}: a save_placement is expected but no installation was detected to answer it")
        _validate_placement(name, expected["save_placement"])


def validate_machines_vector(vector: dict[str, Any]) -> None:
    name = vector["name"]
    if "rationale" in vector and not isinstance(vector["rationale"], str):
        fail(f"{name}: rationale must be a string when present")
    _validate_input(name, vector.get("input"))
    _validate_expected(name, vector.get("expected"), has_query="query" in vector.get("input", {}))


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
