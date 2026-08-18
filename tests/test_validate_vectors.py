"""Tests for scripts/validate_vectors.py — the gate the corpus is held to.

The validator is what stands between a hand-edited vector and a corpus that
lies: it refuses a vector whose expected block could not come out of any
machine, and it is the only check a port author can run without the reference
implementation. It had no tests of its own, which meant every rule in it was a
rule nobody had ever seen fire.

So each rule gets one deliberately invalid vector, in a table, asserted to raise
with the message that names the rule. Two things make the table honest rather
than decorative:

- **the base shapes are validated clean first** (:class:`TestTheBaseShapesAreValid`)
  — a case built on an already-invalid base would pass for the wrong reason, and
  every case here is one mutation away from a base;
- **the message is asserted**, not just the exception type. A rule that fires
  for the wrong reason is a rule that will fire on a good vector one day — and
  the sweep found one rule that could never fire at all, which is now deleted
  rather than left as belt-and-braces nobody could reach.

``tests/test_machine_vectors.py`` keeps its own grammar cases: those read as
documentation of the contradictions a vector can state, and they run through the
runner's file where a port author meets them. The sweep lives here.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
import re
from typing import Any

import pytest

import atlas
from atlas import installations
from scripts import validate_vectors
from scripts.validate_vectors import VectorError, validate_machines_vector

# A vector is plain JSON on the way in — the validator's whole job is to decide
# whether this shape is one the corpus may carry, so nothing here is narrower.
Vector = dict[str, Any]

HOME = "/home/deck"
ROM = "/mnt/sd/retrodeck/roms/gb/Game.gb"
SAVES = "/mnt/sd/retrodeck/saves"
BIOS = "/mnt/sd/retrodeck/bios"
CORE_SO = "mgba_libretro.so"
SYSTEM = "gb"

INSTALLATION = {
    "kind": "retrodeck",
    "kinds": ["retrodeck"],
    "root": "/mnt/sd/retrodeck",
    "health": [],
}
FINDING = {"code": "root-missing", "data": {"path": "/mnt/sd/retrodeck"}}


def _vector(expected=None, *, installed=False, **input_keys) -> Vector:
    """A shape-valid vector: a machine, and whatever question the case asks.

    The validator never runs the resolver, so an input only has to be a
    well-formed machine — the expected block is not required to be what that
    machine would actually answer. That is what keeps every case below one
    mutation wide.
    """
    detected = [dict(INSTALLATION)] if installed else []
    return {
        "name": "synthetic",
        "input": {"home": HOME, "files": {f"{HOME}/marker": ""}, **input_keys},
        "expected": {"installations": detected, **(expected or {})},
    }


def _file_set(**overrides) -> Vector:
    return {"state": "unknown", "files": [], "complete": False, "groups": [], **overrides}


def _placement(**overrides) -> Vector:
    return {
        "dir": SAVES,
        "root_kind": "savefile_directory",
        "needs": [],
        "fallback_dir": None,
        "physical_dir": None,
        "file_set": _file_set(),
        "granularity": None,
        "caveats": [],
        **overrides,
    }


def _granularity(**overrides) -> Vector:
    return {
        "value": "shared-card",
        "mode": "on",
        "readings": [{"key": "opt", "value": "on", "options_file": "/opts.cfg"}],
        "alternatives": [{"mode": "off", "options": {"opt": "off"}, "values": ["per-game-file"]}],
        **overrides,
    }


def _identity(**overrides) -> Vector:
    return {"md5": "0" * 32, "sha1": "1" * 40, "size": 256, **overrides}


def _requirement(**overrides) -> Vector:
    return {
        "core_so": CORE_SO,
        "system": SYSTEM,
        "system_source": "systemname",
        "need": "required",
        "file_name": "gb_bios.bin",
        "path": f"{BIOS}/gb_bios.bin",
        "declared": "gb_bios.bin",
        "identity": None,
        "found": "missing",
        "present": False,
        "checked": None,
        "satisfied": False,
        **overrides,
    }


def _core(**overrides) -> Vector:
    return {
        "core_so": CORE_SO,
        "label": None,
        "declaration": "read",
        "requirements_met": False,
        "requirements": [_requirement()],
        "refused": [],
        "caveats": [],
        **overrides,
    }


def _firmware(**overrides) -> Vector:
    return {
        "root": BIOS,
        "hash_checked": False,
        "cores": [_core()],
        "unclaimed": [],
        "caveats": [],
        **overrides,
    }


def _emulator(**overrides) -> Vector:
    return {
        "system": SYSTEM,
        "label": "mGBA",
        "kind": "libretro",
        "core_so": CORE_SO,
        "selection": None,
        "caveats": [],
        **overrides,
    }


def _identification(**overrides) -> Vector:
    return {
        "identity": None,
        "known_as": [],
        "requirements": [],
        "caveats": [{"code": "firmware-content-unidentified", "data": {}}],
        **overrides,
    }


# --- the base shapes, one per question the corpus can ask ---------------------


def _base_plain() -> Vector:
    return _vector()


def _base_placement(**overrides) -> Vector:
    return _vector(
        {"savefile_location": _placement(**overrides)}, installed=True, savefile_query={"content_path": ROM}
    )


def _base_firmware(**overrides) -> Vector:
    return _vector(
        {"firmware": _firmware(**overrides)},
        installed=True,
        firmware_query={"kind": "core", "core_so": CORE_SO},
    )


def _base_identification(**overrides) -> Vector:
    return _vector(
        {"identification": _identification(**overrides)},
        installed=True,
        identify_query={"md5": "0" * 32},
    )


def _base_catalogue(**overrides) -> Vector:
    block = {"entries": [_emulator()], "caveats": [], **overrides}
    return _vector({"catalogue": block}, installed=True, catalogue_query={"system": SYSTEM})


def _base_systems(**overrides) -> Vector:
    block = {"systems": [SYSTEM], "caveats": [], **overrides}
    return _vector({"systems": block}, installed=True, systems_query={})


def _texture(**overrides) -> Vector:
    return {
        "dir": f"{HOME}/bios/dc/textures",
        "needs": [],
        "physical_dir": None,
        "enabled": None,
        "keying": None,
        "caveats": [],
        **overrides,
    }


def _base_texture(**overrides) -> Vector:
    return _vector(
        {"texture_pack_location": _texture(**overrides)},
        installed=True,
        texture_query={"core_so": CORE_SO},
    )


def _base_entry_texture(outcome=None) -> Vector:
    return _vector(
        {"entry_texture_pack_location": outcome if outcome is not None else _texture()},
        installed=True,
        entry_texture_query={"system": SYSTEM},
    )


def _mod_tree(**overrides) -> Vector:
    return {
        "role": None,
        "dir": f"{HOME}/bios/fbneo/patched",
        "physical_dir": None,
        "keying": None,
        **overrides,
    }


def _mods(**overrides) -> Vector:
    return {"trees": [_mod_tree()], "needs": [], "enabled": None, "caveats": [], **overrides}


def _base_mods(**overrides) -> Vector:
    return _vector(
        {"mod_location": _mods(**overrides)}, installed=True, mod_query={"core_so": CORE_SO}
    )


def _base_entry_mods(outcome=None) -> Vector:
    return _vector(
        {"entry_mod_location": outcome if outcome is not None else _mods()},
        installed=True,
        entry_mod_query={"system": SYSTEM},
    )


CONTENT = f"{HOME}/roms/nes/Game.nes"
PATCH_STEM = f"{HOME}/roms/nes/Game"


def _candidate(fmt: str, **overrides) -> Vector:
    return {
        "format": fmt,
        "path": f"{PATCH_STEM}.{fmt}",
        "continuations": [f"{PATCH_STEM}.{fmt}{index}" for index in range(1, 10)],
        "attempted": None,
        **overrides,
    }


def _soft_patch(**overrides) -> Vector:
    return {
        "candidates": [_candidate(fmt) for fmt in ("ips", "bps", "ups", "xdelta")],
        "applies": None,
        "caveats": [],
        **overrides,
    }


def _base_soft_patch(**overrides) -> Vector:
    return _vector(
        {"soft_patch_candidates": _soft_patch(**overrides)},
        installed=True,
        soft_patch_query={"content_path": CONTENT},
    )


def _base_entry(outcome=None) -> Vector:
    return _vector(
        {"entry_savefile_location": outcome if outcome is not None else _placement()},
        installed=True,
        entry_savefile_query={"system": SYSTEM},
    )


def _base_aggregate(answers=None) -> Vector:
    aggregate = (
        answers
        if answers is not None
        else [{"installation": dict(INSTALLATION), "answer": _placement()}]
    )
    return _vector(
        {"aggregate": aggregate},
        installed=True,
        aggregate_query={"question": "savefile_location"},
    )


BASES = {
    "plain": _base_plain,
    "placement": _base_placement,
    "firmware": _base_firmware,
    "identification": _base_identification,
    "catalogue": _base_catalogue,
    "systems": _base_systems,
    "entry": _base_entry,
    "texture": _base_texture,
    "entry_texture": _base_entry_texture,
    "mods": _base_mods,
    "entry_mods": _base_entry_mods,
    "soft_patch": _base_soft_patch,
    "aggregate": _base_aggregate,
}


class TestTheBaseShapesAreValid:
    """Every case below is a base plus one mutation — so the bases must pass.

    Without this, a base that quietly stopped validating would turn the whole
    table green for the wrong reason: each case would still raise, just never
    for the rule it names.
    """

    @pytest.mark.parametrize("shape", sorted(BASES))
    def test_the_base_validates(self, shape):
        validate_machines_vector(BASES[shape]())


def case(vector: Vector, message: str, *, id: str) -> object:
    """One deliberately invalid vector and the rule it must trip.

    The parameter is called ``id`` because that is what it becomes — pytest's
    test id, which is how a failure names itself in the run output.
    """
    return pytest.param(vector, message, id=id)


def _with(vector: Vector, **input_keys) -> Vector:
    vector["input"].update(input_keys)
    return vector


VECTOR_SHAPE_CASES = [
    case(
        {**_vector(), "stray": 1},
        "vector keys must be name/rationale/input/expected",
        id="vector-stray-key",
    ),
    case({**_vector(), "rationale": 7}, "rationale must be a string", id="vector-rationale-type"),
    case({"name": "synthetic", "input": [], "expected": {}}, "input must be an object", id="input-not-object"),
    case({"name": "synthetic", "input": {"files": {}}, "expected": {}}, "input keys must be", id="input-missing-home"),
    case(_with(_vector(), stray_key="x"), "input keys must be", id="input-stray-key"),
    case({"name": "s", "input": {"home": "", "files": {}}, "expected": {"installations": []}},
         "input.home must be a non-empty string", id="input-home-empty"),
    case({"name": "s", "input": {"home": HOME, "files": []}, "expected": {"installations": []}},
         "input.files must be an object", id="input-files-not-object"),
    case({"name": "s", "input": {"home": HOME, "files": {}}, "expected": []},
         "expected must be an object", id="expected-not-object"),
    case({"name": "s", "input": {"home": HOME, "files": {}}, "expected": {}},
         "expected keys must be", id="expected-missing-installations"),
    case(_vector({"stray": {}}), "expected keys must be", id="expected-stray-key"),
]

INPUT_FILE_CASES = [
    case({"name": "s", "input": {"home": HOME, "files": {"/a": 7}}, "expected": {"installations": []}},
         "must be string content or an object spec", id="file-spec-type"),
    case({"name": "s", "input": {"home": HOME, "files": {"/a": {"status": "nope"}}},
          "expected": {"installations": []}}, "status spec must be", id="file-status-unknown"),
    case({"name": "s", "input": {"home": HOME, "files": {"/a": {"status": "unreadable", "md5": "x"}}},
          "expected": {"installations": []}}, "status spec must be", id="file-status-stray-key"),
    case({"name": "s", "input": {"home": HOME, "files": {"/a": {"status": "unreadable", "size": -1}}},
          "expected": {"installations": []}}, "size must be a non-negative integer", id="file-status-size"),
    case({"name": "s", "input": {"home": HOME, "files": {"/a": {}}}, "expected": {"installations": []}},
         "blob spec keys must be", id="blob-empty"),
    case({"name": "s", "input": {"home": HOME, "files": {"/a": {"nope": 1}}}, "expected": {"installations": []}},
         "blob spec keys must be", id="blob-stray-key"),
    case({"name": "s", "input": {"home": HOME, "files": {"/a": {"md5": ""}}}, "expected": {"installations": []}},
         "md5 must be a non-empty string", id="blob-md5-empty"),
    case({"name": "s", "input": {"home": HOME, "files": {"/a": {"size": -1}}}, "expected": {"installations": []}},
         "size must be a non-negative integer", id="blob-size-negative"),
    # Reachable only through the Python API — JSON object keys are strings by
    # construction. The rule earns its place anyway: the validator is importable,
    # and a generator building vectors in memory is exactly what would hit it.
    case({"name": "s", "input": {"home": HOME, "files": {7: ""}}, "expected": {"installations": []}},
         "input.files keys must be strings", id="file-key-not-a-string"),
]

INPUT_PATH_CASES = [
    case(_with(_vector(), dirs="/saves"), "input.dirs must be a list", id="dirs-not-list"),
    case(_with(_vector(), dirs=[""]), "input.dirs must be a list", id="dirs-empty-string"),
    case(_with(_vector(), unlistable=[7]), "input.unlistable must be a list", id="unlistable-type"),
    case(_with(_vector(), inaccessible=["/a"], unlistable=["/a"]),
         "are in both input.inaccessible and input.unlistable", id="both-unreadable-lists"),
    case(_with(_vector(), symlinks=["/a"]), "input.symlinks must be an object", id="symlinks-not-object"),
    case(_with(_vector(), symlinks={"/a": 7}), "input.symlinks must be an object", id="symlinks-target-type"),
]

INPUT_CORE_CASES = [
    case(_with(_vector(), cores=[]), "input.cores must be an object", id="cores-not-object"),
    case(_with(_vector(), cores={"/a.so": {}}), "must be null or an object with a string library_name",
         id="core-without-library-name"),
    case(_with(_vector(), cores={"/a.so": {"library_name": "X", "options": []}}),
         "options must be an object", id="core-options-not-object"),
    case(_with(_vector(), cores={"/a.so": {"library_name": "X", "options": {"k": {"stray": 1}}}}),
         "must be", id="core-option-spec"),
    case(_with(_vector(), cores={"/a.so": {"library_name": "X", "options": {"k": {"values": "no"}}}}),
         "must be", id="core-option-values-type"),
    case(_with(_vector(), cores={7: None}), "input.cores keys must be strings", id="core-key-not-a-string"),
]

QUERY_CASES = [
    case(_vector({"savefile_location": _placement()}, installed=True, savefile_query="nope"),
         "input.savefile_query must be an object", id="query-not-object"),
    case(_vector({"savefile_location": _placement()}, installed=True, savefile_query={}),
         "input.savefile_query keys must be a non-empty subset", id="query-empty"),
    case(_vector({"savefile_location": _placement()}, installed=True, savefile_query={"nope": "x"}),
         "input.savefile_query keys must be a non-empty subset", id="query-stray-key"),
    case(_vector({"savefile_location": _placement()}, installed=True, savefile_query={"content_path": ""}),
         "must be a non-empty string", id="query-empty-value"),
    case(_vector({"savefile_location": _placement()}, installed=True,
                 savefile_query={"content_path": ROM, "installation": "nope"}),
         "installation must be one of", id="query-unknown-handle"),
    case(_vector({"catalogue": {"entries": [], "caveats": []}}, installed=True, catalogue_query="x"),
         "input.catalogue_query must be an object", id="catalogue-query-not-object"),
    case(_vector({"catalogue": {"entries": [], "caveats": []}}, installed=True, catalogue_query={}),
         "must carry 'system'", id="catalogue-query-no-system"),
    case(_vector({"catalogue": {"entries": [], "caveats": []}}, installed=True,
                 catalogue_query={"system": SYSTEM, "nope": "x"}),
         "must carry 'system'", id="catalogue-query-stray"),
    case(_vector({"catalogue": {"entries": [], "caveats": []}}, installed=True,
                 catalogue_query={"system": ""}), "must be a non-empty string", id="catalogue-query-empty-value"),
    case(_vector({"systems": {"systems": [], "caveats": []}}, installed=True, systems_query="x"),
         "input.systems_query keys must be a subset", id="systems-query-not-object"),
    case(_vector({"systems": {"systems": [], "caveats": []}}, installed=True,
                 systems_query={"nope": "x"}), "input.systems_query keys must be a subset", id="systems-query-stray"),
    case(_vector({"entry_savefile_location": _placement()}, installed=True, entry_savefile_query="x"),
         "input.entry_savefile_query must be an object", id="entry-query-not-object"),
    case(_vector({"entry_savefile_location": _placement()}, installed=True, entry_savefile_query={}),
         "must carry 'system'", id="entry-query-no-system"),
    case(_vector({"entry_savefile_location": _placement()}, installed=True,
                 entry_savefile_query={"system": SYSTEM, "nope": "x"}), "must carry 'system'", id="entry-query-stray"),
    case(_vector({"entry_savefile_location": _placement()}, installed=True, entry_savefile_query={"system": 7}),
         "must be a non-empty string", id="entry-query-value-type"),
]

FIRMWARE_QUERY_CASES = [
    case(_vector({"firmware": _firmware()}, installed=True, firmware_query={"kind": "core", "nope": 1}),
         "input.firmware_query keys must be a subset", id="firmware-query-stray"),
    case(_vector({"firmware": _firmware()}, installed=True, firmware_query={"kind": "nope"}),
         "input.firmware_query.kind must be one of", id="firmware-query-unknown-kind"),
    case(_vector({"firmware": _firmware()}, installed=True, firmware_query={"kind": "core"}),
         "a 'core' firmware query needs", id="firmware-query-core-without-so"),
    case(_vector({"firmware": _firmware()}, installed=True, firmware_query={"kind": "system"}),
         "a 'system' firmware query needs", id="firmware-query-system-without-system"),
    case(_vector({"firmware": _firmware()}, installed=True,
                 firmware_query={"kind": "inventory", "core_so": CORE_SO}),
         "takes neither core_so nor system", id="firmware-query-inventory-with-core"),
    case(_vector({"firmware": _firmware()}, installed=True,
                 firmware_query={"kind": "core", "core_so": ""}), "must be a non-empty string",
         id="firmware-query-empty-value"),
    case(_vector({"firmware": _firmware()}, installed=True,
                 firmware_query={"kind": "core", "core_so": CORE_SO, "verify": "yes"}),
         "verify must be a boolean", id="firmware-query-verify-type"),
]

IDENTIFY_QUERY_CASES = [
    case(_vector({"identification": _identification()}, installed=True, identify_query={"nope": 1}),
         "input.identify_query keys must be a subset", id="identify-query-stray"),
    case(_vector({"identification": _identification()}, installed=True, identify_query={"md5": ""}),
         "must be a non-empty string", id="identify-query-empty-md5"),
    case(_vector({"identification": _identification()}, installed=True, identify_query={"size": "big"}),
         "size must be an integer", id="identify-query-size-type"),
    case(_vector({"identification": _identification()}, installed=True, identify_query={}),
         "must state some content", id="identify-query-states-nothing"),
]

AGGREGATE_QUERY_CASES = [
    case(_vector({"aggregate": []}, aggregate_query="x"),
         "input.aggregate_query must be an object", id="aggregate-query-not-object"),
    case(_vector({"aggregate": []}, aggregate_query={"question": "savefile_location", "installation": "retrodeck"}),
         "takes no 'installation'", id="aggregate-query-names-handle"),
    case(_vector({"aggregate": []}, aggregate_query={"question": "nope"}),
         "aggregate_query.question must be one of", id="aggregate-query-unknown-question"),
    case(_vector({"aggregate": []}, aggregate_query={"question": "emulators_for"}),
         "aggregate query needs", id="aggregate-query-missing-key"),
    case(_vector({"aggregate": []}, aggregate_query={"question": "savefile_location", "system": SYSTEM}),
         "is not asked by", id="aggregate-query-stray-key"),
    case(_vector({"aggregate": []}, aggregate_query={"question": ""}),
         "must be a non-empty string", id="aggregate-query-empty-value"),
]

PAIRING_CASES = [
    case(_vector({"savefile_location": _placement()}, installed=True),
         "a savefile_query and a savefile_location expectation must appear together", id="pair-save-location"),
    case(_vector(installed=True, savefile_query={"content_path": ROM}),
         "a savefile_query and a savefile_location expectation must appear together", id="pair-query-alone"),
    case(_vector({"catalogue": {"entries": [], "caveats": []}}, installed=True),
         "catalogue_query and catalogue expectation", id="pair-catalogue"),
    case(_vector({"systems": {"systems": [], "caveats": []}}, installed=True),
         "systems_query and systems expectation", id="pair-systems"),
    case(_vector({"entry_savefile_location": _placement()}, installed=True),
         "entry_savefile_query and entry_savefile_location expectation", id="pair-entry"),
    case(_vector({"firmware": _firmware()}, installed=True),
         "firmware_query and firmware expectation", id="pair-firmware"),
    case(_vector({"identification": _identification()}, installed=True),
         "identify_query and identification expectation", id="pair-identification"),
    case(_vector({"aggregate": []}), "aggregate_query and aggregate expectation", id="pair-aggregate"),
    case(_vector({"texture_pack_location": _texture()}, installed=True),
         "texture_query and texture_pack_location expectation", id="pair-texture"),
    case(_vector(installed=True, texture_query={"core_so": CORE_SO}),
         "texture_query and texture_pack_location expectation", id="pair-texture-query-alone"),
    case(_vector({"entry_texture_pack_location": _texture()}, installed=True),
         "entry_texture_query and entry_texture_pack_location expectation", id="pair-entry-texture"),
    case(_vector({"mod_location": _mods()}, installed=True),
         "mod_query and mod_location expectation", id="pair-mods"),
    case(_vector(installed=True, mod_query={"core_so": CORE_SO}),
         "mod_query and mod_location expectation", id="pair-mods-query-alone"),
    case(_vector({"entry_mod_location": _mods()}, installed=True),
         "entry_mod_query and entry_mod_location expectation", id="pair-entry-mods"),
    case(_vector({"soft_patch_candidates": _soft_patch()}, installed=True),
         "soft_patch_query and soft_patch_candidates expectation", id="pair-soft-patch"),
    case(_vector(installed=True, soft_patch_query={"content_path": CONTENT}),
         "soft_patch_query and soft_patch_candidates expectation", id="pair-soft-patch-query-alone"),
    # The content is that question's subject, not a modifier: a query that names
    # other keys and not that one asks about no file at all.
    case(_vector({"soft_patch_candidates": _soft_patch()}, installed=True,
                 soft_patch_query={"core_so": CORE_SO}),
         "soft_patch_query must name the content_path", id="soft-patch-query-without-content"),
    # An empty path is refused a step earlier, by the rule every query shares —
    # asserted here so the two guarantees cannot collapse into one.
    case(_vector({"soft_patch_candidates": _soft_patch()}, installed=True,
                 soft_patch_query={"content_path": ""}),
         "soft_patch_query.content_path must be a non-empty string",
         id="soft-patch-query-empty-content"),
    case(_vector({"savefile_location": _placement()}, savefile_query={"content_path": ROM}),
         "needs a detected installation", id="expectation-without-installation"),
]

INSTALLATION_CASES = [
    case({"name": "s", "input": {"home": HOME, "files": {}}, "expected": {"installations": {}}},
         "expected.installations must be a list", id="installations-not-list"),
    case(_vector({"installations": [{**INSTALLATION, "stray": 1}]}),
         "each installation must be exactly the fields", id="installation-stray-field"),
    case(_vector({"installations": [{**INSTALLATION, "kind": "nope"}]}),
         "installation kind must be one of", id="installation-unknown-kind"),
    case(_vector({"installations": [{**INSTALLATION, "kinds": []}]}),
         "installation kinds must be a non-empty list", id="installation-empty-kinds"),
    case(_vector({"installations": [{**INSTALLATION, "kinds": ["nope"]}]}),
         "installation kinds must be a non-empty list", id="installation-unknown-kinds"),
    case(_vector({"installations": [{**INSTALLATION, "root": ""}]}),
         "installation root must be a non-empty string", id="installation-empty-root"),
    case(_vector({"installations": [{**INSTALLATION, "health": [{"code": "nope", "data": {}}]}]}),
         "caveat code must be one of", id="health-unknown-code"),
    case(_vector({"installations": [{**INSTALLATION, "health": [{"code": "no-core", "data": {}}]}]}),
         "installation health findings must be issue codes", id="health-non-health-code"),
    case(_vector({"installations": [{**INSTALLATION, "health": {}}]}),
         "caveats must be a list", id="health-not-list"),
    case(_vector({"installations": [{**INSTALLATION, "health": [{"code": "root-missing"}]}]}),
         "each caveat must be exactly the fields", id="caveat-missing-data"),
    case(_vector({"installations": [{**INSTALLATION, "health": [{"code": "root-missing", "data": {"n": 1}}]}]}),
         "caveat data must be an object of strings", id="caveat-data-not-strings"),
]

PLACEMENT_CASES = [
    case(_base_placement(dir=""), "savefile_location.dir must be a non-empty string", id="placement-empty-dir"),
    case(_base_placement(root_kind="nope"), "savefile_location.root_kind must be one of", id="placement-root-kind"),
    case(_base_placement(needs="content_dir"), "savefile_location.needs must be a list", id="placement-needs-not-list"),
    case(_base_placement(needs=["rom_step"]), "savefile_location.needs must be holes from", id="placement-unknown-hole"),
    case(_base_placement(fallback_dir=""), "must be null or a non-empty string", id="placement-empty-fallback"),
    case(_base_placement(physical_dir=7), "must be null or a non-empty string", id="placement-physical-type"),
    case(_vector({"savefile_location": {**_placement(), "stray": 1}}, installed=True, savefile_query={"content_path": ROM}),
         "savefile_location must be exactly the fields", id="placement-stray-field"),
    case(_base_placement(file_set={"state": "unknown", "files": []}),
         "savefile_location.file_set must be exactly the fields", id="file-set-missing-field"),
    case(_base_placement(file_set=_file_set(state="nope")), "file_set.state must be one of", id="file-set-state"),
    case(_base_placement(file_set=_file_set(state="observed", files="a.srm")),
         "file_set.files must be a list of strings", id="file-set-files-type"),
    case(_base_placement(file_set=_file_set(state="observed", complete="yes")),
         "file_set.complete must be a boolean", id="file-set-complete-type"),
    case(_base_placement(file_set=_file_set(files=["a.srm"])),
         "an unknown file_set carries no files", id="file-set-unknown-with-files"),
    case(_base_placement(file_set=_file_set(complete=True)),
         "an unknown file_set carries no files", id="file-set-unknown-complete"),
    case(_base_placement(granularity={**_granularity(), "stray": 1}),
         "granularity must be exactly the fields", id="granularity-stray-field"),
    case(_base_placement(granularity=_granularity(value="nope")),
         "granularity.value must be one of", id="granularity-value"),
    case(_base_placement(granularity=_granularity(alternatives=[{"mode": "off"}])),
         "granularity alternative must be exactly the fields", id="granularity-alternatives-shape"),
    case(_base_placement(
             granularity=_granularity(
                 alternatives=[{"mode": "off", "options": {"opt": "off"}, "values": ["nope"]}]
             )
         ),
         "every alternative's granularity must be one of", id="granularity-alternative-value"),
    case(_base_placement(
             granularity=_granularity(readings=[{"key": "", "value": None, "options_file": None}])
         ),
         "reading's key must be a non-empty string", id="granularity-reading-empty-key"),
    case(_base_placement(
             granularity=_granularity(
                 alternatives=[{"mode": "off", "options": [["opt", "off"]], "values": ["per-game-file"]}]
             )
         ),
         "options must map option keys to values", id="granularity-alternative-options-shape"),
]

TEXTURE_CASES = [
    case(_base_texture(dir=""), "texture_pack_location.dir must be a non-empty string", id="texture-empty-dir"),
    case(_base_texture(needs="content_dir"), "texture_pack_location.needs must be a list", id="texture-needs-not-list"),
    case(_base_texture(needs=["rom_step"]), "texture_pack_location.needs must be holes from", id="texture-unknown-hole"),
    case(_base_texture(physical_dir=""), "must be null or a non-empty string", id="texture-empty-physical"),
    # Nothing can be link-resolved through a hole, so a vector stating both
    # pins an answer the resolver cannot give.
    case(_base_texture(needs=["content_dir"], physical_dir="/real"),
         "physical_dir for a directory that is still a template", id="texture-physical-through-a-hole"),
    case(_base_texture(enabled="yes"), "enabled must be true, false, or null", id="texture-enabled-type"),
    case(_base_texture(keying="per-game"), "keying must be null or one of", id="texture-unknown-keying"),
    case(_vector({"texture_pack_location": {**_texture(), "stray": 1}}, installed=True,
                 texture_query={"core_so": CORE_SO}),
         "texture_pack_location must be exactly the fields", id="texture-stray-field"),
    # The three fields a save placement has and this question does not: naming
    # one would put a promise in the corpus no serializer produces.
    case(_vector({"texture_pack_location": {**_texture(), "root_kind": "system_directory"}}, installed=True,
                 texture_query={"core_so": CORE_SO}),
         "texture_pack_location must be exactly the fields", id="texture-root-kind"),
    case(_vector({"texture_pack_location": {**_texture(), "file_set": _file_set()}}, installed=True,
                 texture_query={"core_so": CORE_SO}),
         "texture_pack_location must be exactly the fields", id="texture-file-set"),
    case(_base_entry_texture({"unresolved": {"code": "nope", "data": {}}}),
         "unresolved code must be one of", id="entry-texture-unknown-unresolved-code"),
]

MOD_CASES = [
    case(_base_mods(trees=[]), "mod_location.trees must be a non-empty list", id="mods-no-tree"),
    case(_base_mods(trees=[_mod_tree(dir="")]), "dir must be a non-empty string", id="mods-empty-dir"),
    case(_base_mods(trees=[_mod_tree(physical_dir="")]), "must be null or a non-empty string",
         id="mods-empty-physical"),
    case(_base_mods(trees=[_mod_tree(role="")]), "role must be null or a non-empty string",
         id="mods-empty-role"),
    case(_base_mods(trees=[_mod_tree(keying="per-game")]), "keying must be null or one of",
         id="mods-unknown-keying"),
    case(_base_mods(trees=[{**_mod_tree(), "stray": 1}]), "must be exactly the fields",
         id="mods-tree-stray-field"),
    # The role tells several trees apart; on a lone tree it is vocabulary a
    # client has to learn to ignore, so the corpus may not carry one.
    case(_base_mods(trees=[_mod_tree(role="patched")]), "states one tree, which names no role",
         id="mods-lone-tree-with-a-role"),
    case(_base_mods(trees=[_mod_tree(dir="/a"), _mod_tree(dir="/b")]),
         "each names its own distinct role", id="mods-several-trees-without-roles"),
    case(_base_mods(trees=[_mod_tree(dir="/a", role="same"), _mod_tree(dir="/b", role="same")]),
         "each names its own distinct role", id="mods-repeated-roles"),
    case(_base_mods(needs="content_dir"), "mod_location.needs must be a list", id="mods-needs-not-list"),
    case(_base_mods(needs=["rom_step"]), "mod_location.needs must be holes from", id="mods-unknown-hole"),
    # Nothing can be link-resolved through a hole, whichever tree states it.
    case(_base_mods(needs=["content_dir"], trees=[_mod_tree(physical_dir="/real")]),
         "physical_dir for a directory that is still a template", id="mods-physical-through-a-hole"),
    case(_base_mods(enabled="yes"), "enabled must be true, false, or null", id="mods-enabled-type"),
    case(_vector({"mod_location": {**_mods(), "stray": 1}}, installed=True,
                 mod_query={"core_so": CORE_SO}),
         "mod_location must be exactly the fields", id="mods-stray-field"),
    # The texture question's own field: a mod answer keeps its directories
    # inside `trees`, so a top-level one would be a shape no serializer emits.
    case(_vector({"mod_location": {**_mods(), "dir": "/x"}}, installed=True,
                 mod_query={"core_so": CORE_SO}),
         "mod_location must be exactly the fields", id="mods-top-level-dir"),
    case(_base_entry_mods({"unresolved": {"code": "nope", "data": {}}}),
         "unresolved code must be one of", id="entry-mods-unknown-unresolved-code"),
]

SOFT_PATCH_CASES = [
    case(_base_soft_patch(candidates="ips"), "candidates must be a list", id="patch-candidates-not-list"),
    # The order is contractual, not just the set: a port answering the four in
    # another order answers a different question.
    case(_base_soft_patch(candidates=[_candidate(f) for f in ("bps", "ips", "ups", "xdelta")]),
         "must be the four formats in attempt order", id="patch-wrong-order"),
    case(_base_soft_patch(candidates=[_candidate(f) for f in ("ips", "bps", "ups")]),
         "must be the four formats in attempt order", id="patch-missing-format"),
    case(_base_soft_patch(applies="yes"), "applies must be true, false, or null", id="patch-applies-type"),
    case(_vector({"soft_patch_candidates": {**_soft_patch(), "stray": 1}}, installed=True,
                 soft_patch_query={"content_path": CONTENT}),
         "soft_patch_candidates must be exactly the fields", id="patch-stray-field"),
]


def _patched(**overrides) -> Vector:
    """A vector whose FIRST candidate carries *overrides* — the rest stay legal."""
    first = _candidate("ips", **overrides)
    rest = [_candidate(fmt) for fmt in ("bps", "ups", "xdelta")]
    return _base_soft_patch(candidates=[first, *rest])


SOFT_PATCH_CANDIDATE_CASES = [
    case(_patched(format=""), "must be a non-empty string", id="patch-empty-format"),
    case(_patched(path=""), "must be a non-empty string", id="patch-empty-path"),
    # The name IS the format: RetroArch composes it by appending the extension
    # to the content's basename, so a path ending elsewhere names a file the
    # frontend never looks for.
    case(_patched(path=f"{PATCH_STEM}.bps"), "must end in the format's own extension",
         id="patch-path-format-mismatch"),
    case(_patched(continuations=[]), "must list 9 indexed follow-ups", id="patch-no-continuations"),
    case(_patched(continuations=[f"{PATCH_STEM}.ips{i}" for i in range(1, 9)]),
         "must list 9 indexed follow-ups", id="patch-eight-continuations"),
    case(_patched(continuations=[f"{PATCH_STEM}.ips{i}" for i in range(2, 11)]),
         "must be the path with '1' appended", id="patch-continuations-misnumbered"),
    case(_patched(attempted="yes"), "attempted must be true, false, or null", id="patch-attempted-type"),
    case(_patched(stray=1), "must be exactly the fields", id="patch-candidate-stray-field"),
]

ENTRY_CASES = [
    case(_base_entry({"unresolved": {"code": "nope", "data": {}}}),
         "unresolved code must be one of", id="entry-unknown-unresolved-code"),
    case(_base_entry({"unresolved": {"code": "standalone-unsupported"}}),
         "entry_savefile_location.unresolved must be exactly the fields", id="entry-unresolved-missing-data"),
]

CATALOGUE_CASES = [
    case(_vector({"catalogue": []}, installed=True, catalogue_query={"system": SYSTEM}),
         "expected.catalogue must be", id="catalogue-not-object"),
    case(_base_catalogue(entries={}), "expected.catalogue.entries must be a list", id="catalogue-entries-not-list"),
    case(_base_catalogue(entries=[{**_emulator(), "stray": 1}]),
         "each emulator must be exactly the fields", id="emulator-stray-field"),
    case(_base_catalogue(entries=[_emulator(label="")]),
         "emulator label must be a non-empty string", id="emulator-empty-label"),
    case(_base_catalogue(entries=[_emulator(kind="nope")]), "emulator kind must be one of", id="emulator-kind"),
    case(_base_catalogue(entries=[_emulator(core_so="")]),
         "emulator core_so must be null or a non-empty string", id="emulator-empty-core-so"),
    case(_base_catalogue(entries=[_emulator(selection=7)]),
         "emulator selection must be null or a string", id="emulator-selection-type"),
    case(_base_catalogue(entries=[_emulator(system="")]),
         "emulator system must be a non-empty string", id="emulator-empty-system"),
    case(_base_catalogue(caveats=[{"code": "emulator-catalogue-unreadable", "data": {}}]),
         "states entries and", id="catalogue-entries-and-unread"),
]

SYSTEMS_CASES = [
    case(_vector({"systems": []}, installed=True, systems_query={}),
         "expected.systems must be", id="systems-not-object"),
    case(_base_systems(systems="gb"), "expected.systems.systems must be a list", id="systems-not-list"),
    case(_base_systems(systems=[""]), "expected.systems.systems must be a list", id="systems-empty-name"),
    case(_base_systems(caveats=[{"code": "emulator-catalogue-unavailable", "data": {}}]),
         "states systems and", id="systems-and-unread"),
]

AGGREGATE_CASES = [
    case(_base_aggregate(answers={}), "expected.aggregate must be a list", id="aggregate-not-list"),
    case(_base_aggregate(answers=[{"installation": dict(INSTALLATION)}]),
         "each aggregate answer must be exactly the fields", id="aggregate-answer-missing-field"),
    case(_base_aggregate(answers=[]),
         "must answer for exactly the detected installations", id="aggregate-drops-an-installation"),
    case(
        # Each answer is held to the shape its own question has: a placement
        # where the catalogue question answers is not a near-miss, it is a
        # different answer type riding under the wrong label.
        _vector(
            {"aggregate": [{"installation": dict(INSTALLATION), "answer": _placement()}]},
            installed=True,
            aggregate_query={"question": "emulators_for", "system": SYSTEM},
        ),
        "expected.catalogue must be",
        id="aggregate-answer-held-to-its-questions-shape",
    ),
]

FIRMWARE_SHAPE_CASES = [
    case(_vector({"firmware": {**_firmware(), "stray": 1}}, installed=True,
                 firmware_query={"kind": "core", "core_so": CORE_SO}),
         "firmware must be exactly the fields", id="firmware-stray-field"),
    case(_base_firmware(root=""), "firmware.root must be null or a non-empty string", id="firmware-empty-root"),
    case(_base_firmware(hash_checked="no"), "firmware.hash_checked must be a boolean", id="firmware-hash-checked-type"),
    case(_base_firmware(cores={}), "firmware.cores and firmware.unclaimed must be lists", id="firmware-cores-not-list"),
    case(_base_firmware(unclaimed={}), "firmware.cores and firmware.unclaimed must be lists",
         id="firmware-unclaimed-not-list"),
    case(_base_firmware(root=None), "without a root there is nothing to resolve against",
         id="firmware-rootless-with-cores"),
    case(_base_firmware(cores=[_core(declaration="absent", requirements=[], requirements_met=None,
                                     caveats=[{"code": "firmware-declaration-unknown", "data": {}}])],
                        caveats=[]),
         "with no core declaration read, the answer must carry one of", id="firmware-nothing-read-unstated"),
    case(_base_firmware(caveats=[{"code": "system-unknown", "data": {}}]),
         "'system-unknown' means nothing covers the identifier", id="firmware-system-unknown-with-cores"),
    case(_base_firmware(cores=[], caveats=[{"code": "system-unknown", "data": {}},
                                           {"code": "info-path-unresolved", "data": {}}]),
         "which an answer that could not read the enumeration may never say",
         id="firmware-system-unknown-while-blind"),
]

FIRMWARE_CORE_CASES = [
    case(_base_firmware(cores=[{**_core(), "stray": 1}]),
         "each firmware core must be exactly the fields", id="core-stray-field"),
    case(_base_firmware(cores=[_core(core_so="")]),
         "firmware core core_so must be null or a non-empty string", id="core-empty-so"),
    case(_base_firmware(cores=[_core(label="")]),
         "firmware core label must be null or a non-empty string", id="core-empty-label"),
    case(_base_firmware(cores=[_core(core_so=None, label=None, requirements=[])]),
         "an emulator with neither a core nor a catalogue label", id="core-unidentifiable"),
    case(_base_firmware(cores=[_core(declaration="nope")]),
         "firmware core declaration must be one of", id="core-declaration-unknown"),
    case(_base_firmware(cores=[_core(requirements={})]),
         "firmware core requirements must be a list", id="core-requirements-not-list"),
    case(_base_firmware(cores=[_core(declaration="absent", requirements_met=None)]),
         "a core atlas could not read declares nothing", id="core-unread-with-requirements"),
    case(_base_firmware(cores=[_core(declaration="absent", requirements=[], requirements_met=None)],
                        caveats=[{"code": "no-firmware-declaration", "data": {}}]),
         "must state why, or it reads as", id="core-unread-without-caveat"),
    case(_base_firmware(cores=[_core(requirements=[_requirement(core_so="other.so")])]),
         "a requirement must name the core it is listed under", id="requirement-names-other-core"),
    case(_base_firmware(cores=[_core(requirements_met="no")]),
         "firmware core requirements_met must be true, false, or null", id="core-met-type"),
    case(_base_firmware(cores=[_core(requirements_met=True)]),
         "requirements_met must be", id="core-met-overclaims"),
    case(_base_firmware(cores=[_core(refused={})]), "firmware core refused must be a list", id="core-refused-not-list"),
    case(_base_firmware(cores=[_core(refused=[{"declared": "x", "need": "required"}])]),
         "each refused declaration must be exactly the fields", id="refused-missing-field"),
    case(_base_firmware(cores=[_core(refused=[{"declared": "", "need": "required",
                                               "reason": "firmware-root-unusable"}],
                                     caveats=[{"code": "firmware-root-unusable", "data": {}}])]),
         "a refused declaration must state what was declared", id="refused-empty-declared"),
    case(_base_firmware(cores=[_core(refused=[{"declared": "x", "need": "nope",
                                               "reason": "firmware-root-unusable"}],
                                     caveats=[{"code": "firmware-root-unusable", "data": {}}])]),
         "a refused declaration's need must be one of", id="refused-need-unknown"),
    case(_base_firmware(cores=[_core(refused=[{"declared": "x", "need": "required", "reason": "nope"}],
                                     caveats=[{"code": "firmware-root-unusable", "data": {}}])]),
         "a refusal must say which fact it rests on", id="refused-reason-unknown"),
    case(_base_firmware(cores=[_core(refused=[{"declared": "x", "need": "required",
                                               "reason": "firmware-root-unusable"}],
                                     caveats=[{"code": "no-core", "data": {}}])]),
         "a refusal's reason must be stated as a caveat on the same core", id="refused-reason-unstated"),
    # The same rule, reached with no caveats at all — and this is why a separate
    # emptiness rule ("a refused declaration must be stated, or the file
    # vanishes from the answer") cannot fire and was deleted: a non-empty
    # `refused` runs that loop at least once, and the loop demands a caveat
    # whose code IS the refusal's reason, so an empty caveat list is refused
    # here, one item in, every time.
    case(_base_firmware(cores=[_core(refused=[{"declared": "x", "need": "required",
                                               "reason": "firmware-root-unusable"}])]),
         "a refusal's reason must be stated as a caveat on the same core", id="refused-with-no-caveats-at-all"),
]

REQUIREMENT_CASES = [
    case(_base_firmware(cores=[_core(requirements=[{**_requirement(), "stray": 1}])]),
         "each firmware requirement must be exactly the fields", id="requirement-stray-field"),
    case(_base_firmware(cores=[_core(requirements=[_requirement(system="")])]),
         "firmware requirement system must be a non-empty string", id="requirement-empty-system"),
    case(_base_firmware(cores=[_core(requirements=[_requirement(need="nope")], requirements_met=None)]),
         "firmware requirement need must be one of", id="requirement-need-unknown"),
    case(_base_firmware(cores=[_core(requirements=[_requirement(system_source="nope")])]),
         "firmware requirement system_source must be one of", id="requirement-system-source-unknown"),
    case(_base_firmware(cores=[_core(requirements=[_requirement(system_source="none")])]),
         "with no source for the system the slug must be", id="requirement-none-source-named-system"),
    case(_base_firmware(cores=[_core(requirements=[_requirement(path="/elsewhere/gb_bios.bin")])]),
         "must be the absolute destination under the root", id="requirement-path-outside-root"),
    case(_base_firmware(cores=[_core(requirements=[_requirement(path=f"{BIOS}/../bios/gb_bios.bin")])]),
         "must be normalized", id="requirement-path-unnormalized"),
    case(_base_firmware(cores=[_core(requirements=[_requirement(declared="sub/other.bin")])]),
         "file_name must be the name the core spelled", id="requirement-file-name-mismatch"),
    case(_base_firmware(cores=[_core(requirements=[_requirement(found="nope")])]),
         "firmware requirement found must be one of", id="requirement-found-unknown"),
    case(_base_firmware(cores=[_core(requirements=[_requirement(present=True)])]),
         "the requirement's present must be", id="requirement-present-contradicts-found"),
    case(_base_firmware(cores=[_core(requirements=[_requirement(identity={"md5": "x"})])]),
         "must be exactly the fields", id="requirement-identity-shape"),
    case(_base_firmware(cores=[_core(requirements=[_requirement(identity=_identity(md5=""))])]),
         "md5 must be a non-empty string", id="requirement-identity-empty-md5"),
    case(_base_firmware(cores=[_core(requirements=[_requirement(identity=_identity(size=-1))])]),
         "size must be a non-negative integer", id="requirement-identity-negative-size"),
    case(_base_firmware(cores=[_core(requirements=[_requirement(satisfied="no")], requirements_met=None)]),
         "satisfied must be true, false, or null", id="requirement-satisfied-type"),
    case(_base_firmware(cores=[_core(requirements=[_requirement(checked="verified")], requirements_met=None)]),
         "nothing is there to check, so checked must be null", id="requirement-absent-but-checked"),
    case(_base_firmware(cores=[_core(requirements=[_requirement(satisfied=None)], requirements_met=None)]),
         "the requirement's satisfied must be", id="requirement-missing-satisfied-null"),
    case(_base_firmware(cores=[_core(requirements=[_requirement(found="inaccessible", present=None,
                                                                satisfied=False)], requirements_met=False)]),
         "the requirement's satisfied must be", id="requirement-inaccessible-satisfied"),
    case(_base_firmware(cores=[_core(requirements=[_requirement(found="file", present=True, checked="nope",
                                                                satisfied=None)], requirements_met=None)]),
         "firmware requirement checked must be one of", id="requirement-checked-unknown"),
    case(_base_firmware(cores=[_core(requirements=[_requirement(found="directory", present=True,
                                                                checked="verified", satisfied=True)],
                                     requirements_met=True)]),
         "a directory at the destination is checked='unknown'", id="requirement-directory-verdict"),
    case(_base_firmware(cores=[_core(requirements=[_requirement(found="file", present=True, checked="unchecked",
                                                                satisfied=None)], requirements_met=None)]),
         "with no known identity the bytes cannot be established", id="requirement-no-identity-not-unknown"),
    case(_base_firmware(cores=[_core(requirements=[_requirement(found="file", present=True, identity=_identity(),
                                                                checked="verified", satisfied=True)],
                                     requirements_met=True)]),
         "without hash checking a known identity can only be", id="requirement-verdict-without-hashing"),
    case(_base_firmware(hash_checked=True,
                        cores=[_core(requirements=[_requirement(found="file", present=True, identity=_identity(),
                                                                checked="mismatch", satisfied=True)],
                                     requirements_met=True)]),
         "a file whose bytes are known to be wrong is never satisfied", id="requirement-mismatch-satisfied"),
    case(_base_firmware(cores=[_core(requirements=[_requirement(found="file", present=True, checked="unknown",
                                                                satisfied=None)], requirements_met=None)]),
         "'unknown' is undetermined when an identity exists", id="requirement-unknown-verdict-wrong"),
    case(_base_firmware(hash_checked=True,
                        cores=[_core(requirements=[_requirement(found="file", present=True, identity=_identity(),
                                                                checked="unchecked", satisfied=True)],
                                     requirements_met=True)]),
         "an identity that exists and was not verified is not an all-clear", id="requirement-unchecked-satisfied"),
    case(_base_firmware(hash_checked=True,
                        cores=[_core(requirements=[_requirement(found="file", present=True, identity=_identity(),
                                                                checked="verified", satisfied=None)],
                                     requirements_met=None)]),
         "a verified file is satisfied", id="requirement-verified-not-satisfied"),
]

UNCLAIMED_CASES = [
    case(_base_firmware(unclaimed=[{"path": f"{BIOS}/x.bin", "identity": None}]),
         "each unclaimed file must be exactly the fields", id="unclaimed-missing-field"),
    case(_base_firmware(unclaimed=[{"path": "/elsewhere/x.bin", "identity": None, "known_as": []}]),
         "an unclaimed file's path must be absolute under the root", id="unclaimed-outside-root"),
    case(_base_firmware(unclaimed=[{"path": f"{BIOS}/x.bin", "identity": None, "known_as": ""}]),
         "unclaimed known_as must be a list", id="unclaimed-known-as-type"),
    case(_base_firmware(unclaimed=[{"path": f"{BIOS}/x.bin", "identity": None, "known_as": ["gb_bios.bin"]}]),
         "an unrecognised file is known as nothing", id="unclaimed-unidentified-but-named"),
    case(_base_firmware(hash_checked=True,
                        unclaimed=[{"path": f"{BIOS}/x.bin", "identity": _identity(), "known_as": []}]),
         "recognised content is known under at least the name", id="unclaimed-identified-unnamed"),
    case(_base_firmware(unclaimed=[{"path": f"{BIOS}/x.bin", "identity": _identity(),
                                    "known_as": ["gb_bios.bin"]}]),
         "impossible without hash checking", id="unclaimed-identified-without-hashing"),
]

IDENTIFICATION_CASES = [
    case(_vector({"identification": {**_identification(), "stray": 1}}, installed=True,
                 identify_query={"md5": "0" * 32}),
         "identification must be exactly the fields", id="identification-stray-field"),
    case(_base_identification(known_as="mgba"), "identification known_as must be a list", id="identification-known-as-type"),
    case(_base_identification(known_as=["gb_bios.bin"]),
         "unrecognised content has no names and satisfies nothing", id="identification-unidentified-but-named"),
    case(_base_identification(caveats=[]), "must say which kind of nothing it is", id="identification-silent-nothing"),
    case(_base_identification(identity=_identity(), known_as=[], caveats=[]),
         "recognised content is known under at least one name", id="identification-identified-unnamed"),
    case(_base_identification(identity={"md5": "x"}, known_as=["a"], caveats=[]),
         "must be exactly the fields", id="identification-identity-shape"),
    case(_base_identification(requirements=[{"nope": 1}], identity=_identity(), known_as=["a"], caveats=[]),
         "each identified requirement must carry its absolute destination path", id="identification-requirement-shape"),
    case(_base_identification(identity=_identity(), known_as=["a"], caveats=[],
                              requirements=[_requirement(path=f"{BIOS}/gb_bios.bin")]),
         "returns only requirements that expect exactly this content", id="identification-foreign-requirement"),
    case(_vector({"identification": _identification()}, installed=True, identify_query={"size": 256}),
         "names no content and must answer that it does not", id="identification-size-only-unstated"),
]


ALL_CASES = [
    *VECTOR_SHAPE_CASES,
    *INPUT_FILE_CASES,
    *INPUT_PATH_CASES,
    *INPUT_CORE_CASES,
    *QUERY_CASES,
    *FIRMWARE_QUERY_CASES,
    *IDENTIFY_QUERY_CASES,
    *AGGREGATE_QUERY_CASES,
    *PAIRING_CASES,
    *INSTALLATION_CASES,
    *PLACEMENT_CASES,
    *TEXTURE_CASES,
    *MOD_CASES,
    *SOFT_PATCH_CASES,
    *SOFT_PATCH_CANDIDATE_CASES,
    *ENTRY_CASES,
    *CATALOGUE_CASES,
    *SYSTEMS_CASES,
    *AGGREGATE_CASES,
    *FIRMWARE_SHAPE_CASES,
    *FIRMWARE_CORE_CASES,
    *REQUIREMENT_CASES,
    *UNCLAIMED_CASES,
    *IDENTIFICATION_CASES,
]


@pytest.mark.parametrize("vector,message", ALL_CASES)
def test_the_validator_refuses(vector, message):
    """One invalid vector, and the rule that must be the one to catch it."""
    with pytest.raises(VectorError, match=re.escape(message)):
        validate_machines_vector(vector)


class TestTheFileLevelRules:
    """The rules that are about a vector FILE, not about one vector.

    They run through :func:`validate_file`, which is what the gate itself calls
    — the family header, the schema number, and the two uniqueness rules that
    keep one guarantee from silently shadowing another.
    """

    def _file(self, tmp_path, **overrides):
        document = {
            "family": "machines",
            "schema": validate_vectors.SCHEMA,
            "spec": "spec",
            "description": "description",
            "vectors": [_vector()],
            **overrides,
        }
        path = tmp_path / "cases.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def _validate(self, path, seen=None):
        # `seen` is the across-files input registry, and an empty one is
        # falsy — it has to be passed on as itself, or each file would be
        # checked against a fresh registry and the cross-file rule below
        # could never fire.
        return validate_vectors.validate_file(
            path, "machines", validate_machines_vector, {} if seen is None else seen
        )

    def test_a_wellformed_file_validates(self, tmp_path):
        assert self._validate(self._file(tmp_path)) == 1

    def test_a_file_that_is_not_json_is_refused(self, tmp_path):
        path = tmp_path / "cases.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(VectorError, match="not valid JSON"):
            self._validate(path)

    @pytest.mark.parametrize("key", ["family", "schema", "spec", "description", "vectors"])
    def test_a_missing_top_level_key_is_refused(self, tmp_path, key):
        document = json.loads(self._file(tmp_path).read_text())
        del document[key]
        path = tmp_path / "cases.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(VectorError, match=f"missing top-level key '{key}'"):
            self._validate(path)

    def test_a_foreign_family_is_refused(self, tmp_path):
        with pytest.raises(VectorError, match="family must be"):
            self._validate(self._file(tmp_path, family="other"))

    def test_another_schema_is_refused(self, tmp_path):
        # The number is the contract's generation: a port built to schema 2
        # models no unlistable directory, so the corpus is not the same
        # promise and says so instead of failing one vector at a time.
        with pytest.raises(VectorError, match="schema must be"):
            self._validate(self._file(tmp_path, schema=2))

    def test_a_nameless_vector_is_refused(self, tmp_path):
        nameless = {**_vector(), "name": ""}
        with pytest.raises(VectorError, match="missing or empty name"):
            self._validate(self._file(tmp_path, vectors=[nameless]))

    def test_a_duplicate_name_is_refused(self, tmp_path):
        twin = {**_vector(), "input": {"home": HOME, "files": {"/other": ""}}}
        with pytest.raises(VectorError, match="duplicate name"):
            self._validate(self._file(tmp_path, vectors=[_vector(), twin]))

    def test_a_duplicate_input_is_refused(self, tmp_path):
        # Same machine, same question, two expectations: one of them would
        # never be the answer, and nothing would say which.
        twin = {**_vector(), "name": "other"}
        with pytest.raises(VectorError, match="duplicate canonical input"):
            self._validate(self._file(tmp_path, vectors=[_vector(), twin]))

    def test_a_duplicate_input_across_files_is_refused(self, tmp_path):
        seen = {}
        self._validate(self._file(tmp_path), seen)
        other = tmp_path / "second.json"
        other.write_text(
            json.dumps(
                {
                    "family": "machines",
                    "schema": validate_vectors.SCHEMA,
                    "spec": "spec",
                    "description": "description",
                    "vectors": [{**_vector(), "name": "elsewhere"}],
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(VectorError, match="duplicate canonical input"):
            self._validate(other, seen)


# The other half of a gate: what it must ACCEPT. A validator that refused every
# vector would pass the whole table above, and each of these is a state the
# corpus is allowed to reach — several of them only reachable on real machines.
ACCEPTED_CASES = [
    pytest.param(_with(_vector(), files={"/a": {"status": "unreadable", "size": 12}}),
                 id="a-file-that-stats-but-cannot-be-read"),
    pytest.param(_with(_vector(), files={"/a": {"md5": "0" * 32, "size": 4}}),
                 id="a-binary-blob-with-a-declared-identity"),
    pytest.param(_with(_vector(), cores={"/a.so": None}), id="a-core-that-is-present-but-unloadable"),
    pytest.param(_with(_vector(), cores={"/a.so": {"library_name": "X", "options": {"k": {"default": None,
                                                                                          "values": ["a"]}}}}),
                 id="a-core-that-registers-options"),
    pytest.param(_with(_vector(), inaccessible=["/mnt/card"], unlistable=["/saves"]),
                 id="the-two-unreadable-lists-apart"),
    pytest.param(_base_entry({"unresolved": {"code": "standalone-unsupported",
                                             "data": {"label": "PPSSPP", "system": "psp"}}}),
                 id="a-standalone-entry-answering-unresolved"),
    pytest.param(_base_placement(needs=["content_dir", "library_name", "save_id"],
                                 file_set=_file_set(state="declared", files=["<save_id>.bin"])),
                 id="a-template-placement-naming-every-hole"),
    pytest.param(_base_placement(granularity=_granularity(value="per-game-files")),
                 id="a-granularity-with-alternatives"),
    pytest.param(_base_firmware(root=None, cores=[], unclaimed=[],
                                caveats=[{"code": "firmware-root-missing", "data": {}}]),
                 id="a-firmware-answer-with-no-root-at-all"),
    pytest.param(_base_firmware(cores=[_core(requirements=[_requirement(found="directory", present=True,
                                                                        checked="unknown", satisfied=None)],
                                             requirements_met=None)]),
                 id="a-declaration-that-names-a-folder"),
    pytest.param(_base_firmware(hash_checked=True,
                                cores=[_core(requirements=[_requirement(found="file", present=True,
                                                                        identity=_identity(),
                                                                        checked="verified", satisfied=True)],
                                             requirements_met=True)]),
                 id="a-verified-requirement-that-is-met"),
    pytest.param(_base_firmware(cores=[_core(requirements=[_requirement(found="file", present=True,
                                                                        identity=_identity(),
                                                                        checked="unchecked", satisfied=None)],
                                             requirements_met=None)]),
                 id="a-present-file-nobody-hashed"),
    pytest.param(_base_firmware(cores=[_core(refused=[{"declared": "../escape.bin", "need": "required",
                                                       "reason": "firmware-path-escapes-root"}],
                                             requirements=[], requirements_met=None,
                                             caveats=[{"code": "firmware-path-escapes-root", "data": {}}])]),
                 id="a-refused-declaration-stated-as-a-caveat"),
    pytest.param(_base_firmware(hash_checked=True,
                                unclaimed=[{"path": f"{BIOS}/x.bin", "identity": _identity(),
                                            "known_as": ["gb_bios.bin"]}]),
                 id="an-unclaimed-file-identified-by-content"),
    pytest.param(_base_firmware(cores=[_core(declaration="unreadable", requirements=[], requirements_met=None,
                                             caveats=[{"code": "firmware-declaration-unread", "data": {}}])],
                                caveats=[{"code": "no-firmware-declaration", "data": {}}]),
                 id="a-core-whose-declaration-could-not-be-read"),
    pytest.param(_base_identification(identity=_identity(), known_as=["gb_bios.bin"], caveats=[],
                                      requirements=[_requirement(identity=_identity(), found="missing",
                                                                 present=False, checked=None, satisfied=False)]),
                 id="identified-content-with-the-requirement-that-wants-it"),
    pytest.param(_base_catalogue(entries=[_emulator(kind="standalone", core_so=None, label="PPSSPP",
                                                    selection="gamelist.xml: alternativeEmulator")]),
                 id="a-standalone-catalogue-entry"),
    pytest.param(_base_catalogue(entries=[], caveats=[{"code": "emulator-catalogue-unreadable", "data": {}}]),
                 id="an-empty-catalogue-that-says-why"),
    pytest.param(_vector({"installations": [{**INSTALLATION, "health": [FINDING]}]}),
                 id="an-installation-with-a-health-finding"),
]


@pytest.mark.parametrize("vector", ACCEPTED_CASES)
def test_the_validator_accepts(vector):
    validate_machines_vector(vector)


def test_a_widened_aggregate_vocabulary_without_an_answer_shape_is_refused(monkeypatch):
    """The guard between the two halves of the aggregate rule.

    The question vocabulary and the answer shapes are separate maps, so a
    question added to one and not the other would have its answers validated as
    some other question's shape — or as nothing. This is the only rule in the
    file a vector alone cannot reach: it takes an edit to the validator itself.
    """
    monkeypatch.setitem(validate_vectors.AGGREGATE_QUESTION_FIELDS, "systems", ({"question"}, set()))
    vector = _vector(
        {"aggregate": [{"installation": dict(INSTALLATION), "answer": _placement()}]},
        installed=True,
        aggregate_query={"question": "systems"},
    )
    with pytest.raises(VectorError, match=re.escape("no answer shape is defined")):
        validate_machines_vector(vector)


class TestTheVocabularyIsOneVocabulary:
    """The validator mirrors atlas's closed sets by hand — so the mirror is checked.

    Being stdlib-only and standalone is the point of this script: a port author
    runs it without importing atlas at all. The price is that every vocabulary
    in it is hand-copied, and a hand-copied list drifts silently — a code the
    resolver started emitting would make the gate refuse a truthful vector, and
    a code atlas retired would keep passing one nobody can produce. Nothing here
    judges the lists; it checks that the two are the same list.
    """

    # Every family a code can belong to. `UNRESOLVED_` is not a caveat
    # vocabulary — it is the Unresolved namespace — but it is a code the same
    # way, so the package-wide walk below covers all three.
    FAMILIES = ("CAVEAT_", "HEALTH_ISSUE_", "UNRESOLVED_")

    def _exported(self, prefix: str) -> set[str]:
        return {
            value
            for name in atlas.__all__
            if name.startswith(prefix) and isinstance(value := getattr(atlas, name), str)
        }

    def _caveat_codes(self) -> set[str]:
        # Health findings ride in answer caveats under their own codes, so the
        # caveat vocabulary contains the health one by construction.
        return self._exported("CAVEAT_") | self._exported("HEALTH_ISSUE_")

    def _caveat_and_unresolved_codes(self) -> set[str]:
        return self._caveat_codes() | self._exported("UNRESOLVED_")

    def _codes_defined_anywhere_in_the_package(self) -> set[str]:
        """Every code constant in any atlas submodule, exported or not.

        The other guards all derive their "what codes exist" side from
        ``atlas.__all__``, so a constant a submodule defines and the export list
        forgets is invisible to every one of them at once — it would be a code
        the resolver can emit that no vector must cover and no validator must
        know. This is the one derivation that does not start from ``__all__``.
        """
        found: set[str] = set()
        for info in pkgutil.iter_modules(atlas.__path__):
            module = importlib.import_module(f"{atlas.__name__}.{info.name}")
            found |= {
                value
                for name, value in vars(module).items()
                if name.startswith(self.FAMILIES) and isinstance(value, str)
            }
        return found

    def test_no_code_is_defined_in_a_submodule_and_left_unexported(self):
        assert sorted(self._codes_defined_anywhere_in_the_package() - self._caveat_and_unresolved_codes()) == []

    def test_no_exported_code_is_missing_from_the_package(self):
        # The other direction is a typo guard: a code in `__all__` that no
        # module defines could only come from the export list itself.
        assert sorted(self._caveat_and_unresolved_codes() - self._codes_defined_anywhere_in_the_package()) == []

    def test_the_validator_knows_every_code_atlas_can_emit(self):
        assert sorted(self._caveat_codes() - validate_vectors.KNOWN_CAVEAT_CODES) == []

    def test_the_validator_knows_no_code_atlas_cannot_emit(self):
        assert sorted(validate_vectors.KNOWN_CAVEAT_CODES - self._caveat_codes()) == []

    def test_the_health_vocabularies_match(self):
        assert validate_vectors.KNOWN_HEALTH_ISSUES == self._exported("HEALTH_ISSUE_")

    def test_the_unresolved_vocabularies_match(self):
        assert validate_vectors.KNOWN_UNRESOLVED_CODES == self._exported("UNRESOLVED_")

    def test_the_hole_vocabularies_match(self):
        holes = {
            value
            for name in atlas.__all__
            if name.startswith("HOLE_") and isinstance(value := getattr(atlas, name), str)
        }
        assert validate_vectors.KNOWN_HOLES == holes

    def test_the_granularity_vocabularies_match(self):
        assert validate_vectors.KNOWN_GRANULARITIES == set(atlas.GRANULARITIES)

    def test_the_role_vocabularies_match(self):
        assert validate_vectors.KNOWN_ROLES == set(atlas.ROLES)

    def test_the_role_vocabulary_itself_is_pinned(self):
        """A role is consumer surface, so growing the set is a deliberate edit.

        The tests around this one keep the three spellings of the vocabulary
        from drifting apart, which is a different guarantee: they would all
        agree just as happily about a fifth value nobody meant to add. A client
        branches on these strings, and one more of them is a thing it has never
        seen — so the list is written out here and a change to it has to be
        made twice, on purpose.
        """
        assert atlas.ROLES == ("battery", "memory-card", "disk-diff", "high-score", "settings")

    def test_the_role_constants_are_the_role_tuple(self):
        assert self._exported("ROLE_") == set(atlas.ROLES)

    def test_the_granularity_constants_are_the_granularity_tuple(self):
        # Every other closed set here ships per-value names beside the tuple;
        # this is what keeps the two from drifting once both exist. One name
        # stands outside the tuple on purpose: GRANULARITY_NONE says no save
        # data is kept at all, which no file group may ever claim — it is a
        # granularity.value, never a group vocabulary member.
        assert self._exported("GRANULARITY_") == set(atlas.GRANULARITIES) | {atlas.GRANULARITY_NONE}
        assert atlas.GRANULARITY_NONE not in atlas.GRANULARITIES
        assert validate_vectors.GRANULARITY_VALUE_NONE == atlas.GRANULARITY_NONE

    def test_the_root_kind_vocabularies_match(self):
        assert validate_vectors.KNOWN_ROOT_KINDS == set(atlas.ROOT_KINDS)

    def test_the_vocabularies_a_client_branches_on_are_all_tier_one(self):
        # The sets above are read off `atlas` on purpose: a client branches on
        # `needs`, `granularity.value` and `root_kind`, so their vocabularies
        # are consumer surface. Reading them from a submodule instead would let
        # one drop out of the export list without a test noticing.
        promoted = {
            "HOLE_CONTENT_DIR",
            "HOLE_LIBRARY_NAME",
            "HOLE_SAVE_ID",
            "GRANULARITIES",
            "GRANULARITY_SHARED_CARD",
            "GRANULARITY_PER_GAME_FILE",
            "GRANULARITY_PER_GAME_FILES",
            "ROOT_KINDS",
        }
        assert sorted(promoted - set(atlas.__all__)) == []

    def test_the_emulator_kind_vocabularies_match(self):
        assert validate_vectors.KNOWN_EMULATOR_KINDS == {atlas.KIND_LIBRETRO, atlas.KIND_STANDALONE}

    def test_the_path_kind_vocabularies_match(self):
        path_kinds = {atlas.KIND_FILE, atlas.KIND_DIRECTORY, atlas.KIND_MISSING, atlas.KIND_INACCESSIBLE}
        assert validate_vectors.KNOWN_PATH_KINDS == path_kinds

    def test_the_two_kind_families_account_for_every_exported_kind(self):
        # `KIND_` is the one prefix two vocabularies share, so neither of the
        # tests above can be a prefix walk — and without this, a third KIND_
        # constant could appear and belong to neither set unnoticed.
        both = validate_vectors.KNOWN_PATH_KINDS | validate_vectors.KNOWN_EMULATOR_KINDS
        assert self._exported("KIND_") == both

    def test_the_file_set_state_vocabularies_match(self):
        assert validate_vectors.KNOWN_FILE_SET_STATES == self._exported("FILE_SET_")

    def test_the_system_source_vocabularies_match(self):
        assert validate_vectors.KNOWN_SYSTEM_SOURCES == self._exported("SOURCE_")

    def test_the_declaration_state_vocabularies_match(self):
        assert validate_vectors.KNOWN_DECLARATION_STATES == self._exported("DECLARATION_")

    def test_the_firmware_need_vocabularies_match(self):
        assert validate_vectors.KNOWN_FIRMWARE_NEEDS == self._exported("NEED_")

    def test_the_firmware_checked_vocabularies_match(self):
        assert validate_vectors.KNOWN_FIRMWARE_CHECKED == self._exported("CHECKED_")

    def test_the_installation_kind_vocabularies_match(self):
        kinds = {
            member.kind
            for name, member in vars(installations).items()
            if not name.startswith("_") and isinstance(member, type) and isinstance(vars(member).get("kind"), str)
        }
        assert validate_vectors.KNOWN_KINDS == kinds


class TestACodesNameIsItsString:
    """``CAVEAT_SORTED_DIR_MISSING`` is ``"sorted-dir-missing"`` — one rule, one exception.

    The name and the string are two spellings of the same code, and when they
    drift apart the name stops being a way to find the code: a client greps the
    string, a maintainer greps the constant, and one of them comes up empty.

    The exception is deliberate and stays. ``UNRESOLVED_STANDALONE`` is
    ``"standalone-unsupported"``: the prefix marks the *Unresolved* namespace
    rather than being part of the code, and the string matches the firmware
    caveat of the same name exactly — one outcome, one spelling, whichever
    surface answers it. A naive rule flags it, which is why the rule below names
    it instead of being loosened for it.
    """

    EXCEPTIONS = {"UNRESOLVED_STANDALONE": "standalone-unsupported"}
    FAMILIES = ("CAVEAT_", "HEALTH_ISSUE_", "UNRESOLVED_")

    def _codes(self) -> dict[str, str]:
        return {
            name: value
            for name in atlas.__all__
            if name.startswith(self.FAMILIES) and isinstance(value := getattr(atlas, name), str)
        }

    def _expected_code(self, name: str) -> str:
        """The string a constant's name says it holds — family prefix off, kebab-cased.

        The prefix is the whole family (``HEALTH_ISSUE_``, not ``HEALTH_``):
        splitting at the first underscore instead would demand
        ``issue-marker-missing``, which is a rule about nothing.
        """
        family = next(prefix for prefix in self.FAMILIES if name.startswith(prefix))
        return name[len(family) :].lower().replace("_", "-")

    def test_every_code_is_named_after_itself(self):
        drifted = sorted(
            f"{name} = {value!r} (expected {self._expected_code(name)!r})"
            for name, value in self._codes().items()
            if name not in self.EXCEPTIONS and value != self._expected_code(name)
        )
        assert drifted == []

    def test_the_documented_exception_is_still_the_only_one(self):
        assert {n: v for n, v in self._codes().items() if n in self.EXCEPTIONS} == self.EXCEPTIONS

    def test_the_exception_matches_the_caveat_of_the_same_name(self):
        # What licenses the exception: the Unresolved outcome and the firmware
        # caveat are one fact, so they are one string.
        assert atlas.UNRESOLVED_STANDALONE == atlas.CAVEAT_STANDALONE_UNSUPPORTED
