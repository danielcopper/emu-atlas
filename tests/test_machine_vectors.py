"""Run every 'machines' vector through the real detect() + resolver routes.

The vectors are the artifact; this is atlas's conformance run for the machines
family (schema 4). Each vector is a whole fixture machine — files, dirs,
symlinks, core answers — and detect() must find exactly the expected
installations. Expectations are the canonical contract serializations
(atlas.contract) asserted with EXACT equality: every stable field, including
caveat codes and data, granularity identity, fallback/physical directories,
and health issue codes. Prose (sources, messages) is deliberately outside the
contract. A port that passes these reads the machine the way the reference
does.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterator, Mapping, Sequence, cast
from pathlib import Path

import pytest

import atlas
import atlas.machine
from atlas import whdload
from atlas import placement, retroarch_cfg
from atlas.firmware import CORE_DECLARATION_STATES
from atlas.machine import FixtureMachine
from tests.corpus import caveat_blocks, expected_blocks
from scripts import validate_vectors
from atlas.contract import (
    catalogue_contract,
    savefile_answer_contract,
    savestate_answer_contract,
    screenshot_answer_contract,
    texture_answer_contract,
    mod_answer_contract,
    soft_patch_answer_contract,
    systems_contract,
    launchable_contract,
    firmware_contract,
    identification_contract,
    installation_answers_contract,
    installation_contract,
    platform_systems_contract,
    rom_placement_contract,
    system_platforms_contract,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_VECTOR_DIR = _REPO_ROOT / "vectors" / "machines"


def load_vectors():
    files = sorted(_VECTOR_DIR.glob("*.json"))
    assert files, f"no vector files found in {_VECTOR_DIR}"
    for path in files:
        data = json.loads(path.read_text())
        assert data["family"] == "machines"
        assert data["schema"] == 4, f"{path}: runner speaks vector schema 4"
        for vector in data["vectors"]:
            yield pytest.param(vector, id=f"{path.stem}:{vector['name']}")


def fixture_machine(inp) -> FixtureMachine:
    return FixtureMachine(
        inp["files"],
        symlinks=inp.get("symlinks"),
        cores=inp.get("cores"),
        dirs=inp.get("dirs"),
        inaccessible=inp.get("inaccessible"),
        unlistable=inp.get("unlistable"),
        appimages=inp.get("appimages"),
        ps2_bios_headers=inp.get("ps2_bios_headers"),
        archives=inp.get("archives"),
        whdload_slaves=inp.get("whdload_slaves"),
    )


def _select(installs, selector: str | None, context: str):
    if selector is None:
        assert installs, f"{context}: no installation detected to answer the query"
        return installs[0]
    for install in installs:
        if install.kind == selector:
            return install
    raise AssertionError(f"{context}: no detected installation of kind {selector!r}")


def _aggregate(installs, query, name):
    """The vector's question put to every detected installation, serialized.

    No handle is selected — that is what this family proves. The question names
    itself, and its answers serialize through the contract function that
    question already has.
    """
    every = atlas.EveryInstallation(installs)
    question = query["question"]
    if question == "savefile_location":
        return installation_answers_contract(
            every.savefile_location(
                content_path=query.get("content_path"), core_so=query.get("core_so")
            ),
            savefile_answer_contract,
        )
    if question == "savestate_location":
        return installation_answers_contract(
            every.savestate_location(
                content_path=query.get("content_path"), core_so=query.get("core_so")
            ),
            savestate_answer_contract,
        )
    if question == "texture_pack_location":
        return installation_answers_contract(
            every.texture_pack_location(
                content_path=query.get("content_path"), core_so=query.get("core_so")
            ),
            texture_answer_contract,
        )
    if question == "emulators_for":
        return installation_answers_contract(
            every.emulators_for(query["system"], content_path=query.get("content_path")),
            catalogue_contract,
        )
    raise AssertionError(f"{name}: the runner cannot ask {question!r} of every installation")


def _catalogue(installs, query, name):
    install = _select(installs, query.get("installation"), name)
    return catalogue_contract(
        install.emulators_for(query["system"], content_path=query.get("content_path"))
    )


def _rom_location(installs, query, name):
    install = _select(installs, query.get("installation"), name)
    return rom_placement_contract(install.rom_location(query["system"]))


def _systems(installs, query, name):
    return systems_contract(_select(installs, query.get("installation"), name).systems())


def _systems_for_platform(installs, query, name):
    install = _select(installs, query.get("installation"), name)
    return platform_systems_contract(
        install.systems_for_platform(query["vocabulary"], query["value"])
    )


def _platform_ids(installs, query, name):
    install = _select(installs, query.get("installation"), name)
    return system_platforms_contract(install.platform_ids(query["system"]))


def _launchable(installs, query, name):
    install = _select(installs, query.get("installation"), name)
    return launchable_contract(install.launchable(query["system"], query["content_path"]))


def _savefile_location(installs, query, name):
    install = _select(installs, query.get("installation"), name)
    return savefile_answer_contract(
        install.savefile_location(content_path=query.get("content_path"), core_so=query.get("core_so"))
    )


def _savestate_location(installs, query, name):
    install = _select(installs, query.get("installation"), name)
    return savestate_answer_contract(
        install.savestate_location(content_path=query.get("content_path"), core_so=query.get("core_so"))
    )


def _screenshot_location(installs, query, name):
    install = _select(installs, query.get("installation"), name)
    return screenshot_answer_contract(
        install.screenshot_location(content_path=query.get("content_path"), core_so=query.get("core_so"))
    )


def _texture_pack_location(installs, query, name):
    install = _select(installs, query.get("installation"), name)
    return texture_answer_contract(
        install.texture_pack_location(
            content_path=query.get("content_path"), core_so=query.get("core_so")
        )
    )


def _mod_location(installs, query, name):
    install = _select(installs, query.get("installation"), name)
    return mod_answer_contract(
        install.mod_location(content_path=query.get("content_path"), core_so=query.get("core_so"))
    )


def _entry_mod_location(installs, query, name):
    entry = _entry_of(installs, query, name)
    return mod_answer_contract(entry.mod_location(content_path=query.get("content_path")))


def _soft_patch_candidates(installs, query, name):
    install = _select(installs, query.get("installation"), name)
    return soft_patch_answer_contract(
        install.soft_patch_candidates(query["content_path"], core_so=query.get("core_so"))
    )


def _firmware(installs, query, name):
    install = _select(installs, query.get("installation"), name)
    verify = query.get("verify", False)
    kind = query["kind"]
    if kind == "core":
        answer = install.firmware_for_core(core_so=query["core_so"], verify=verify)
    elif kind == "system":
        answer = install.firmware_for_system(system=query["system"], verify=verify)
    else:
        answer = install.firmware_inventory(verify=verify)
    return firmware_contract(answer)


def _identification(installs, query, name):
    install = _select(installs, query.get("installation"), name)
    return identification_contract(
        install.identify_firmware(
            md5=query.get("md5"), sha1=query.get("sha1"), size=query.get("size")
        )
    )


def _entry_of(installs, query, name):
    """The catalogue entry a vector's entry query names — first one by default."""
    install = _select(installs, query.get("installation"), name)
    entries = install.emulators_for(query["system"], content_path=query.get("content_path")).entries
    if "label" in query:
        return next(e for e in entries if e.label == query["label"])
    return entries[0]


def _entry_savefile_location(installs, query, name):
    entry = _entry_of(installs, query, name)
    return savefile_answer_contract(entry.savefile_location(content_path=query.get("content_path")))


def _entry_savestate_location(installs, query, name):
    entry = _entry_of(installs, query, name)
    return savestate_answer_contract(entry.savestate_location(content_path=query.get("content_path")))


def _entry_texture_pack_location(installs, query, name):
    entry = _entry_of(installs, query, name)
    return texture_answer_contract(entry.texture_pack_location(content_path=query.get("content_path")))


# expected key → (the input key that asks it, how the runner asks it). The map
# is what makes an unknown expectation an error instead of a silent pass: a
# vector could once carry an `expected.savelocation` typo and prove nothing,
# because the runner only ever looked for the keys it knew.
QUESTIONS = {
    "catalogue": ("catalogue_query", _catalogue),
    "systems": ("systems_query", _systems),
    "systems_for_platform": ("platform_systems_query", _systems_for_platform),
    "platform_ids": ("platform_ids_query", _platform_ids),
    "launchable": ("launchable_query", _launchable),
    "rom_location": ("rom_location_query", _rom_location),
    "aggregate": ("aggregate_query", _aggregate),
    "savefile_location": ("savefile_query", _savefile_location),
    "savestate_location": ("savestate_query", _savestate_location),
    "screenshot_location": ("screenshot_query", _screenshot_location),
    "entry_savestate_location": ("entry_savestate_query", _entry_savestate_location),
    "firmware": ("firmware_query", _firmware),
    "identification": ("identify_query", _identification),
    "entry_savefile_location": ("entry_savefile_query", _entry_savefile_location),
    "texture_pack_location": ("texture_query", _texture_pack_location),
    "entry_texture_pack_location": ("entry_texture_query", _entry_texture_pack_location),
    "soft_patch_candidates": ("soft_patch_query", _soft_patch_candidates),
    "mod_location": ("mod_query", _mod_location),
    "entry_mod_location": ("entry_mod_query", _entry_mod_location),
}


@pytest.mark.parametrize("vector", list(load_vectors()))
def test_machine_vector(vector):
    inp = vector["input"]
    expected = vector["expected"]
    name = vector["name"]
    rationale = vector.get("rationale", name)
    machine = fixture_machine(inp)

    installs = atlas.detect(inp["home"], machine)
    assert [installation_contract(i) for i in installs] == expected["installations"], rationale

    for key, expectation in expected.items():
        if key == "installations":
            continue
        # An expectation the runner cannot ask is a vector that proves nothing
        # while passing — the failure mode a typo produces, and the one a port
        # would inherit silently.
        assert key in QUESTIONS, f"{name}: the runner cannot ask for expected.{key}"
        query_key, ask = QUESTIONS[key]
        assert query_key in inp, f"{name}: expected.{key} needs input.{query_key} to ask it"
        assert ask(installs, inp[query_key], name) == expectation, rationale


class TestEveryCodeTheCorpusCanShowIsInTheCorpus:
    """A code no vector produces is a promise no port is held to.

    The vectors are what a port is checked against, so a caveat code that
    appears in no expected block is a piece of the contract nobody has to
    implement — and nobody would notice, because the suite would stay green.
    The derivation is mechanical on both sides: the codes come off atlas's own
    export list, the coverage off the corpus, so a code added tomorrow is
    covered or named below, never silently neither.
    """

    # Two exceptions, each with the test that reaches the state behind it.
    # Detection triggers on the marker, so a machine without one has no
    # installation for a vector to ask at all; it is covered by a direct-handle
    # test instead
    # (tests/test_installations.py::TestAMarkerThatIsGoneIsStatedNotDetected).
    # 'filenames-unverified' rides on mode.files being None — the mode's
    # PRIMARY NAMED group carrying no 'files' — which is a different shape
    # from a group that states none at all: the nineteen shipped groups
    # without a list carry 'unnamed', which is never a mode's primary named
    # group, and their caveat is 'file-names-unestablished'. Since issue #387
    # gave the opera card one mode per (storage, version) pair, no shipped
    # card leaves a named group's list unverified, so no fixture machine can
    # reach the construction site; the loader still accepts the shape and the
    # resolver still says so. The test that holds it is
    # tests/test_oddities.py::TestAModeWhosePrimaryGroupDeclaresNoFiles. The
    # sibling test below asserts the moment a vector covers either one, and a
    # human then removes its entry.
    UNREACHABLE_BY_FIXTURE = {"marker-missing", "filenames-unverified"}

    def _exported_codes(self) -> set[str]:
        families = ("CAVEAT_", "HEALTH_ISSUE_", "UNRESOLVED_")
        return {
            value
            for name in atlas.__all__
            if name.startswith(families) and isinstance(value := getattr(atlas, name), str)
        }

    def _codes_in_corpus(self) -> set[str]:
        return {code for _, expected in expected_blocks() for code, _ in caveat_blocks(expected)}

    def test_every_code_appears_in_some_vector(self):
        uncovered = sorted(self._exported_codes() - self._codes_in_corpus() - self.UNREACHABLE_BY_FIXTURE)
        assert uncovered == []

    def test_the_exception_list_carries_nothing_a_vector_now_covers(self):
        # A code that gained a vector must lose its exemption, or the list
        # becomes a place where coverage quietly goes to die.
        assert sorted(self.UNREACHABLE_BY_FIXTURE & self._codes_in_corpus()) == []

    def test_the_corpus_shows_no_code_atlas_cannot_emit(self):
        assert sorted(self._codes_in_corpus() - self._exported_codes()) == []


def core_blocks(node: Any) -> Iterator[dict[str, Any]]:
    """Every serialized firmware core under *node*, recognised by its own shape.

    A core is the block that carries a declaration and the verdict over it, so
    the walk finds one wherever an answer puts it — the per-core route, the
    per-system one and the inventory all serialize the same shape.
    """
    if isinstance(node, dict):
        if "declaration" in node and "requirements_met" in node:
            yield node
        for value in node.values():
            yield from core_blocks(value)
    elif isinstance(node, list):
        for value in node:
            yield from core_blocks(value)


class TestEveryJudgedDeclarationReachesBothVerdicts:
    """A verdict no vector produces is a verdict no port is held to.

    ``requirements_met`` is the field a client renders as its traffic light,
    and for three weeks every ``packaged`` block in this corpus answered
    ``null`` — 36 of them, 25 with a requirement list behind them — because
    the property gated on ``!= read``. The suite stayed green throughout,
    because nothing asked whether a declaration that can be judged ever was.

    So the matrix is the check: group the corpus by declaration, and every
    declaration that reaches the field with something to weigh must reach both
    ``True`` and ``False`` somewhere. A declaration whose every block declares
    nothing is exempt — there is no verdict to demonstrate — and that is why
    the exemption is derived from the corpus rather than listed here.
    """

    def _matrix(self) -> dict[str, dict[str, int]]:
        matrix: dict[str, dict[str, int]] = {}
        for _, expected in expected_blocks():
            for core in core_blocks(expected):
                seen = matrix.setdefault(core["declaration"], {"true": 0, "false": 0, "null": 0, "weighed": 0})
                seen[str(core["requirements_met"]).lower().replace("none", "null")] += 1
                seen["weighed"] += 1 if core["requirements"] else 0
        return matrix

    def test_a_declaration_with_requirements_reaches_true_and_false(self):
        matrix = self._matrix()
        rendered = "\n".join(f"  {name}: {counts}" for name, counts in sorted(matrix.items()))
        missing = [
            f"{name} never reaches {verdict}"
            for name, counts in sorted(matrix.items())
            if counts["weighed"]
            for verdict in ("true", "false")
            if not counts[verdict]
        ]
        assert missing == [], f"{missing}\n{rendered}"

    def test_the_matrix_covers_every_declaration_the_vocabulary_has(self):
        # Without this the check above passes by never seeing a declaration at
        # all, which is the failure mode a corpus-derived exemption invites.
        assert set(self._matrix()) == set(CORE_DECLARATION_STATES)


class TestWhatTheContentReadFoundReachesTheRequirement:
    """The caveat named the image and the requirement said the table covers nothing.

    Two statements about one file, in one answer, disagreeing: the caveat says
    DuckStation's table knows these bytes, the requirement beside it said
    ``unknown`` with no identity — which is the value for a file no table
    covers. The path is what ties a caveat to the requirements it is about,
    and *requirements* is the plural it has to be: two routes can state one
    path in one answer, and only the one that hashed the file carries what the
    hashing found. So the check reads every option at the caveat's path, asks
    that one of them carries the reading, and that none of them carries a
    reading that contradicts it.
    """

    # Every reading that would contradict the caveat's own sentence about the
    # file at that path. `refused` and `unread` contradict both: a file the
    # emulator will not open, or whose bytes did not come back, is one nothing
    # hashed — so no caveat may say what its bytes are.
    CONTRADICTING = {
        "firmware-image-identified": {"unrecognised", "mismatch", "refused", "unread"},
        "firmware-content-unidentified": {"verified", "mismatch", "refused", "unread"},
    }

    def _paired(self, code: str) -> list[tuple[str, str, list[dict[str, Any]]]]:
        """Every ``(where, path, the options at that path)`` the corpus pairs with *code*."""
        pairs = []
        for where, expected in expected_blocks():
            for core in core_blocks(expected):
                options = [
                    option
                    for requirement in core["requirements"]
                    for option in requirement.get("alternatives", [requirement])
                ]
                for caveat_code, data in caveat_blocks(core["caveats"]):
                    at_path = [o for o in options if o["path"] == data.get("path")]
                    if caveat_code == code and at_path:
                        pairs.append((where, data["path"], at_path))
        return pairs

    def _wrong(self, code: str, checked: str, *, identity: bool) -> list[str]:
        """Where no option at the path carries the reading, or one contradicts it."""
        wrong = []
        for where, path, options in self._paired(code):
            readings = [f"{o['checked']}/{'identity' if o['identity'] else 'none'}" for o in options]
            carried = any(
                o["checked"] == checked and (o["identity"] is not None) == identity for o in options
            )
            contradicted = any(o["checked"] in self.CONTRADICTING[code] for o in options)
            if not carried or contradicted:
                wrong.append(f"{where}: {path} is {', '.join(readings)}")
        return wrong

    def test_an_identified_image_is_verified_where_it_is_the_requirement(self):
        assert self._paired("firmware-image-identified"), "no vector pairs one with a requirement"
        assert self._wrong("firmware-image-identified", "verified", identity=True) == []

    def test_an_unidentified_image_is_unrecognised_rather_than_unknown(self):
        assert self._paired("firmware-content-unidentified"), "no vector pairs one with a requirement"
        assert self._wrong("firmware-content-unidentified", "unrecognised", identity=False) == []


class TestUnknownNoLongerCarriesAReadFailure:
    """``unknown`` beside an identity used to mean two things, and now means one.

    A file whose bytes did not come back answered ``unknown`` with the
    identity its table pinned — the same pair a shape answers with, and
    indistinguishable from it by the value alone. The read failure is
    ``unread`` now, and what is left of the pair is the shape case: a
    directory standing where the core opens a file, where ``satisfied`` never
    consults ``checked`` at all.

    Measured over the corpus rather than asserted: the walk names every block
    that still carries the pair, and every one of them must be that shape.
    """

    def _pairs(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (where, option)
            for where, expected in expected_blocks()
            for core in core_blocks(expected)
            for requirement in core["requirements"]
            for option in requirement.get("alternatives", [requirement])
            if option["checked"] == "unknown" and option["identity"] is not None
        ]

    def test_no_file_carries_unknown_beside_an_identity(self):
        wrong = [
            f"{where}: {option['path']} is a {option['found']} at a {option['declared_kind']} declaration"
            for where, option in self._pairs()
            if option["found"] != "directory"
        ]
        assert wrong == []

    def test_the_pair_that_remains_is_the_obstructed_shape(self):
        # The corpus does carry it, so the rule above is holding something
        # rather than passing over an empty walk.
        remaining = self._pairs()
        assert remaining, "no block carries unknown beside an identity — the walk proves nothing"
        assert {option["found"] for _, option in remaining} == {"directory"}
        assert {option["declared_kind"] for _, option in remaining} == {"file"}


def _slugs(value: "str | Sequence[str] | Mapping[str, str]") -> list[str]:
    """The slugs one enumerated data value states.

    A string states itself, a list one per entry, and an object one per key —
    its values, since an object's keys are the subjects the slugs are said
    about. The same three readings ``atlas.placement`` makes at construction.
    """
    if isinstance(value, str):
        return [value]
    return list(value.values()) if isinstance(value, Mapping) else list(value)


class TestEveryEnumeratedValueComesFromItsClosedVocabulary:
    """A ``data`` value a client branches on is a slug from a named tuple.

    The codes have had this guarantee since the corpus existed; the values
    inside ``data`` did not, and three of them were English sentences. The
    check runs both ways for each: nothing the corpus shows may sit outside
    its vocabulary, and the vocabulary is what the guide documents — a slug
    the guide does not list is a branch a client cannot know it may take.

    Coverage is the separate question, and it is pinned rather than asserted:
    the corpus does not reach every slug yet, so what it does not reach is
    listed below and the list itself is checked both ways. A gap cannot open
    silently, and a gap that closes must lose its entry. Every entry names the
    test that does hold it, so "not in a fixture" never quietly becomes "not
    tested" — a claim this list carried once without earning it.
    """

    # Slugs no fixture machine produces yet, each with the test that reaches
    # the state behind it. All were equally unwitnessed while their value was a
    # sentence: this round changed the shape, not the reach.
    NOT_YET_IN_A_FIXTURE = {
        # tests/test_installations.py::TestMoreStandaloneSaves — xemu with no
        # hard-disk image, Cemu launched with --mlc, Azahar with the SD off.
        "hdd-path-unset",
        "mlc-launch-flag-outranks-config",
        "virtual-sd-disabled",
        # tests/test_oddities.py::TestScummvmSavepath and ::TestMameOwnPaths.
        "savepath-config-unreadable",
        "ini-search-path-unlistable",
        # tests/test_installations.py::TestTheUserAPerUserTreeWouldOpen —
        # one test per construct the scalar reader refuses a whole file on,
        # asserted as this code's reason rather than as the reader's own word.
        "second-document",
        "anchor-or-alias",
        "tag",
        "substitution-cycle",
        "substitution-unknown",
        "not-a-flat-mapping",
        # tests/test_oddities.py::TestTheUnpackedGameDirectoryScope and
        # ::TestGpgxContentClasses — the two card tokens whose cores have no
        # fixture machine.
        "unpacked-game-directory",
        "raw-cartridge-image",
        # tests/test_retroarch_cfg.py::TestSavefileDirectoryValidation drives
        # each of the four chain layers as the one refused, so it produces the
        # CfgSource kind this slug is; what no fixture machine builds is the
        # caveat carrying it.
        "content-dir-override",
        # No shipped system_firmware.json entry rests on a derived reading —
        # the one stated verdict is verified — so no fixture machine can
        # produce this word from the packaged table. It is reachable, and
        # tests/test_firmware.py::TestTheSystemBehindTheCoreReachesTheAnswer
        # ::test_a_derived_verdict_publishes_the_derived_word reaches it, by
        # answering over a table written in the test.
        "derived",
    }

    _GUIDE = (_REPO_ROOT / "docs" / "how-to-use.md").read_text(encoding="utf-8")

    # (code, data key) → the vocabulary its value must come from.
    VOCABULARIES = {
        ("core-mode-unestablished", "reason"): atlas.CORE_MODE_UNESTABLISHED_REASONS,
        ("emulator-config-unreadable", "reason"): atlas.EMULATOR_CONFIG_UNREADABLE_REASONS,
        ("filenames-content-conditional", "files_established_for"): (
            atlas.FILES_ESTABLISHED_FOR_TOKENS
        ),
        ("firmware-search-candidates", "readings"): atlas.FIRMWARE_SEARCH_READINGS,
        ("invalid-save-directory", "layer"): atlas.CFG_LAYER_KINDS,
        ("system-firmware-world-knowledge", "evidence"): atlas.STATED_EVIDENCE_WORDS,
    }

    def _values_in_corpus(self) -> dict[tuple[str, str], set[str]]:
        """Every value the corpus states at an enumerated pair, list-valued ones unpacked.

        A pair whose value is an object or a list states several slugs, so it
        is read word by word — the way the constructor checks it. Taking the
        shape itself would put an unhashable value in the set and, once past
        that, compare a whole object against the vocabulary.
        """
        found: dict[tuple[str, str], set[str]] = {pair: set() for pair in self.VOCABULARIES}
        for _, expected in expected_blocks():
            for code, data in caveat_blocks(expected):
                for pair in [p for p in self.VOCABULARIES if p[0] == code and p[1] in data]:
                    found[pair].update(_slugs(data[pair[1]]))
        return found

    def test_the_corpus_shows_no_value_outside_its_vocabulary(self):
        found = self._values_in_corpus()
        stray = {
            f"{code}.{key}": sorted(found[(code, key)] - set(vocabulary))
            for (code, key), vocabulary in self.VOCABULARIES.items()
            if found[(code, key)] - set(vocabulary)
        }
        assert stray == {}

    @pytest.mark.parametrize(
        ("where", "vocabulary"),
        [(f"{code}.{key}", vocabulary) for (code, key), vocabulary in VOCABULARIES.items()],
    )
    def test_the_guide_lists_every_slug(self, where, vocabulary):
        undocumented = sorted(slug for slug in vocabulary if f"`{slug}`" not in self._GUIDE)
        assert undocumented == [], where

    def test_the_corpus_reaches_every_slug_the_exemption_list_does_not_name(self):
        found: set[str] = set()
        for values in self._values_in_corpus().values():
            found |= values
        every = {slug for vocabulary in self.VOCABULARIES.values() for slug in vocabulary}
        assert sorted(every - found - self.NOT_YET_IN_A_FIXTURE) == []

    def test_the_exemption_list_names_nothing_the_corpus_now_reaches(self):
        found: set[str] = set()
        for values in self._values_in_corpus().values():
            found |= values
        assert sorted(self.NOT_YET_IN_A_FIXTURE & found) == []

    def test_every_slug_constant_is_in_its_tuple(self):
        # The tuple is what the guide, the validator and the corpus check
        # against, so a constant beside it and not in it is a value atlas can
        # emit that nothing documents. Read off the defining MODULE, not the
        # package's re-export list: a constant somebody forgot to export is
        # exactly the one that would slip past.
        for module, prefix, vocabulary in (
            (
                placement,
                "REASON_",
                (
                    *atlas.CORE_MODE_UNESTABLISHED_REASONS,
                    *atlas.EMULATOR_CONFIG_UNREADABLE_REASONS,
                ),
            ),
            (placement, "ESTABLISHED_FOR_", atlas.FILES_ESTABLISHED_FOR_TOKENS),
            (placement, "READING_", atlas.FIRMWARE_SEARCH_READINGS),
            (retroarch_cfg, "CFG_LAYER_", atlas.CFG_LAYER_KINDS),
        ):
            declared = {
                value
                for name, value in vars(module).items()
                if name.startswith(prefix) and isinstance(value, str)
            }
            assert sorted(declared - set(vocabulary)) == [], prefix

    def test_every_slug_constant_is_also_exported(self):
        # And the package exports each, since the guide tells clients to
        # compare against the names rather than against string literals.
        for module, prefix in (
            (placement, "REASON_"),
            (placement, "ESTABLISHED_FOR_"),
            (placement, "READING_"),
        ):
            for name, value in vars(module).items():
                if name.startswith(prefix) and isinstance(value, str):
                    assert name in atlas.__all__, name

    def test_no_slug_is_spelled_twice_or_reads_as_a_sentence(self):
        # Kebab-case, no spaces: the shape that separates a value from prose.
        for (code, key), vocabulary in self.VOCABULARIES.items():
            assert len(set(vocabulary)) == len(vocabulary), f"{code}.{key}"
            for slug in vocabulary:
                assert slug == slug.lower(), slug
                assert " " not in slug, slug
                assert "_" not in slug, slug


class TestTheRunnerAsksEverythingItIsGiven:
    """An expectation nobody asks is a guarantee nobody checks.

    The runner used to look for the keys it knew and step over the rest, so
    ``expected.savelocation`` — a typo away from the real key — rode in the
    corpus as a vector that passed while proving nothing. The validator refuses
    the same shape, but a port runs the runner, and each has to hold on its own.
    """

    _MACHINE = {"home": "/home/deck", "files": {"/home/deck/x": ""}}
    _EMPTY_SYSTEMS = {"systems": [], "caveats": []}

    def _vector(self, expected, **input_keys):
        return {
            "name": "synthetic",
            "input": {**self._MACHINE, **input_keys},
            "expected": {"installations": [], **expected},
        }

    def test_an_expectation_the_runner_cannot_ask_is_refused(self):
        vector = self._vector({"savelocation": {}})
        with pytest.raises(AssertionError, match="cannot ask for expected.savelocation"):
            test_machine_vector(vector)

    def test_an_expectation_without_its_query_is_refused(self):
        # The validator states this rule too; a port that only runs the runner
        # would otherwise meet a KeyError instead of the reason.
        vector = self._vector({"systems": self._EMPTY_SYSTEMS})
        with pytest.raises(AssertionError, match="needs input.systems_query"):
            test_machine_vector(vector)

    def test_a_machine_with_nothing_to_ask_still_runs(self):
        # The guard against a vacuous suite: the two refusals above must come
        # from the rules, not from every synthetic vector failing anyway.
        test_machine_vector(self._vector({}))


class TestTheGrammarRefusesContradictions:
    """A rule nothing else can catch: no vector contradicts itself today.

    The validator running clean over the corpus proves the corpus is clean, not
    that the rule is still there — delete the check and the gate stays green.
    So the refusal is asserted directly, through the same per-vector entry
    point the gate drives.
    """

    _INSTALLATION = {
        "kind": "retrodeck",
        "label": "RetroDECK",
        "kinds": ["retrodeck"],
        "root": "/mnt/sd/retrodeck",
        "health": [],
    }
    _UNREAD = [{"code": "emulator-catalogue-unreadable", "data": {}}]
    _SAVEFILE_LOCATION_EVERYWHERE = {"question": "savefile_location"}
    _NOBODY_ANSWERED = {"installations": [], "aggregate": []}

    def _vector(self, expected=None, **input_keys):
        return {
            "name": "synthetic",
            "input": {"home": "/home/deck", "files": {"/home/deck/x": ""}, **input_keys},
            "expected": {"installations": []} if expected is None else expected,
        }

    def test_a_path_in_both_unreadable_lists_is_refused(self):
        # The tempting wrong reading is that both together spell mode 000. They
        # do not: such a directory answers *directory* about itself, so it is
        # 'unlistable', and 'inaccessible' would have the resolver refuse it a
        # step earlier than the machine does.
        vector = self._vector(inaccessible=["/saves"], unlistable=["/saves"])
        with pytest.raises(validate_vectors.VectorError, match="both"):
            validate_vectors.validate_machines_vector(vector)

    def test_the_two_lists_are_fine_apart(self):
        vector = self._vector(inaccessible=["/mnt/card"], unlistable=["/saves"])
        validate_vectors.validate_machines_vector(vector)

    def test_a_systems_query_the_runner_cannot_read_is_refused(self):
        # Not an object is not a query: the runner asks it for a handle
        # selector, so a bare string reaches the resolver as an AttributeError
        # instead of a stated defect in the vector.
        vector = self._vector(systems_query="nonsense")
        with pytest.raises(validate_vectors.VectorError, match="systems_query keys"):
            validate_vectors.validate_machines_vector(vector)

    def test_a_systems_query_without_an_expectation_is_refused(self):
        # A question with nothing to compare against is never asked, and a
        # vector that silently asks nothing proves nothing.
        vector = self._vector(systems_query={})
        with pytest.raises(validate_vectors.VectorError, match="systems_query and systems"):
            validate_vectors.validate_machines_vector(vector)

    def test_a_systems_expectation_without_a_query_is_refused(self):
        vector = self._vector(
            expected={"installations": [], "systems": {"systems": [], "caveats": []}}
        )
        with pytest.raises(validate_vectors.VectorError, match="systems_query and systems"):
            validate_vectors.validate_machines_vector(vector)

    def test_systems_stated_beside_a_no_catalogue_code_are_refused(self):
        vector = self._vector(
            systems_query={},
            expected={
                "installations": [self._INSTALLATION],
                "systems": {"systems": ["n64"], "caveats": self._UNREAD},
            },
        )
        with pytest.raises(validate_vectors.VectorError, match="states systems"):
            validate_vectors.validate_machines_vector(vector)

    def test_an_aggregate_query_naming_a_handle_is_refused(self):
        # Naming one handle asks the aggregate to choose, which is the one
        # thing it does not do — the single-question families are where a
        # vector names a handle.
        vector = self._vector(
            aggregate_query={**self._SAVEFILE_LOCATION_EVERYWHERE, "installation": "retrodeck"},
            expected=self._NOBODY_ANSWERED,
        )
        with pytest.raises(validate_vectors.VectorError, match="takes no 'installation'"):
            validate_vectors.validate_machines_vector(vector)

    def test_an_aggregate_question_the_runner_cannot_ask_is_refused(self):
        vector = self._vector(
            aggregate_query={"question": "roms_dir"}, expected=self._NOBODY_ANSWERED
        )
        with pytest.raises(validate_vectors.VectorError, match="aggregate_query.question"):
            validate_vectors.validate_machines_vector(vector)

    def test_an_aggregate_query_carrying_a_key_its_question_ignores_is_refused(self):
        # The catalogue question is not asked by a core: the runner never
        # passes the key on, so the vector would state something no answer in
        # it can reflect — and it would read as if the core had governed.
        vector = self._vector(
            aggregate_query={
                "question": "emulators_for",
                "system": "n64",
                "core_so": "mgba_libretro.so",
            },
            expected=self._NOBODY_ANSWERED,
        )
        with pytest.raises(validate_vectors.VectorError, match="is not asked by"):
            validate_vectors.validate_machines_vector(vector)

    def test_a_savefile_location_aggregate_query_carrying_a_system_is_refused(self):
        vector = self._vector(
            aggregate_query={**self._SAVEFILE_LOCATION_EVERYWHERE, "system": "n64"},
            expected=self._NOBODY_ANSWERED,
        )
        with pytest.raises(validate_vectors.VectorError, match="is not asked by"):
            validate_vectors.validate_machines_vector(vector)

    def test_an_aggregate_query_without_the_key_its_question_needs_is_refused(self):
        vector = self._vector(
            aggregate_query={"question": "emulators_for"}, expected=self._NOBODY_ANSWERED
        )
        with pytest.raises(validate_vectors.VectorError, match="needs \\['system'\\]"):
            validate_vectors.validate_machines_vector(vector)

    def test_an_aggregate_expectation_without_a_query_is_refused(self):
        vector = self._vector(expected=self._NOBODY_ANSWERED)
        with pytest.raises(validate_vectors.VectorError, match="aggregate_query and aggregate"):
            validate_vectors.validate_machines_vector(vector)

    def test_an_empty_machine_may_still_be_asked_every_installation(self):
        # The one expectation that stands with no installations detected:
        # nothing installed is an answer, not a question nobody can answer.
        vector = self._vector(
            aggregate_query=self._SAVEFILE_LOCATION_EVERYWHERE, expected=self._NOBODY_ANSWERED
        )
        validate_vectors.validate_machines_vector(vector)

    def test_an_aggregate_that_skips_a_detected_installation_is_refused(self):
        # The fan-out answers for everything detection found. A vector that
        # asserts fewer answers than installations locks in exactly the
        # silently chosen winner the aggregate exists to avoid.
        vector = self._vector(
            aggregate_query=self._SAVEFILE_LOCATION_EVERYWHERE,
            expected={"installations": [self._INSTALLATION], "aggregate": []},
        )
        with pytest.raises(validate_vectors.VectorError, match="in detection order"):
            validate_vectors.validate_machines_vector(vector)

    def test_entries_stated_beside_a_no_catalogue_code_are_refused(self):
        # The same contradiction one level down: a catalogue nobody read
        # declares no entry, so naming one alongside the code that says it was
        # never read locks in an answer no arrangement can produce.
        vector = self._vector(
            catalogue_query={"system": "n64"},
            expected={
                "installations": [self._INSTALLATION],
                "catalogue": {
                    "entries": [
                        {
                            "system": "n64",
                            "label": "ParaLLEl N64",
                            "kind": "libretro",
                            "core_so": "parallel_n64_libretro.so",
                            "emulator": "parallel_n64_libretro.so",
                            "declared_index": 0,
                            "selection": None,
                            "caveats": [],
                        }
                    ],
                    "caveats": self._UNREAD,
                },
            },
        )
        with pytest.raises(validate_vectors.VectorError, match="states entries"):
            validate_vectors.validate_machines_vector(vector)


class TestEveryDeclaredSlaveIsTheOneTheReaderWouldSelect:
    """A fixture may not declare a selection the reader would not make.

    ``whdload_slaves`` models the *bytes* a slave holds, which no fixture can
    carry — but which member those bytes belong to, and by which route, is not
    modelling at all: it follows from the container's own listing, which the
    fixture does declare. So it is computed here and held against what the
    vector says, and a fixture claiming the script found a slave it could not
    have found fails rather than teaching the corpus a selection nobody
    implements.

    The check lives here rather than in ``scripts/validate_vectors.py``
    because that script is stdlib-only on purpose — a port author runs it
    without importing atlas — and this claim is atlas's own reader.
    """

    def _members(self, container, inp):
        declared = inp.get("archives", {}).get(container)
        if isinstance(declared, list):
            return tuple(declared)
        prefix = container.rstrip("/") + "/"
        sources = (*inp.get("files", {}), *inp.get("cores", {}))
        return tuple(sorted(name[len(prefix) :] for name in sources if name.startswith(prefix)))

    def _selection(self, container, inp):
        members = self._members(container, inp)
        root = atlas.machine._mounted_root(container, members)  # pyright: ignore[reportPrivateUsage]
        if root is None:
            return None, whdload.Selection()
        inside = [name[len(root) :] for name in members if name.startswith(root)]
        return root, whdload.select_slave(inside)

    def _declarations(self):
        for parameters in load_vectors():
            vector = cast("dict[str, Any]", parameters.values[0])
            for container, spec in vector["input"].get("whdload_slaves", {}).items():
                yield vector["name"], container, spec, vector["input"]

    def test_a_declared_member_and_route_are_the_ones_the_reader_returns(self):
        wrong = []
        for name, container, spec, inp in self._declarations():
            if not isinstance(spec, dict):
                continue
            root, selection = self._selection(container, inp)
            expected = None if selection.slave is None else f"{root}{selection.slave}"
            if (spec["slave"], spec["selected_by"]) != (expected, selection.route):
                wrong.append(
                    f"{name}: declares {spec['slave']!r} by {spec['selected_by']!r}, "
                    f"the reader selects {expected!r} by {selection.route!r}"
                )
        assert wrong == []

    def test_a_declared_state_is_one_the_reader_would_reach(self):
        wrong = []
        for name, container, spec, inp in self._declarations():
            if not isinstance(spec, str):
                continue
            _, selection = self._selection(container, inp)
            reached = {
                "no-slave": selection.slave is None and not selection.ambiguous,
                "ambiguous": selection.ambiguous,
                "slave-unreadable": selection.slave is not None,
            }[spec]
            if not reached:
                wrong.append(f"{name}: declares {spec!r}, which this listing does not reach")
        assert wrong == []


class TestEveryIdentityIsAWordTheCommandCarries:
    """The identity field, re-derived from the raw command text over the whole corpus.

    Two claims a single vector cannot make, both read off the fixture machines
    rather than off the expectations: an identity is a word the launch command
    itself carries, and a null one is a command that carries no word to take.
    The patterns here are the test's own — a second reading of the same text,
    so a resolver that started inventing names would not be checked against
    its own invention.
    """

    # The three shapes an identity can come from, spelled independently of
    # atlas.esde and atlas.installations: a libretro core file, an ES-DE
    # emulator token, an EmuDeck launcher script.
    _CORE = re.compile(r"[A-Za-z0-9_\-\[\]]+_libretro\.so")
    _TOKEN = re.compile(r"%EMULATOR_([A-Za-z0-9_-]+)%")
    _LAUNCHER = re.compile(r"/tools/launchers/([A-Za-z0-9_.-]+)\.sh")
    _RUNNER = "RETROARCH"

    def _catalogue_answers(self):
        """Every catalogue answer the corpus asks for, with the vector's name."""
        for param in load_vectors():
            vector = cast(dict[str, Any], param.values[0])
            query = vector["input"].get("catalogue_query")
            if query is None:
                continue
            inp = vector["input"]
            installs = atlas.detect(inp["home"], fixture_machine(inp))
            install = _select(installs, query.get("installation"), vector["name"])
            content_path = query.get("content_path")
            answer = install.emulators_for(query["system"], content_path=content_path)
            yield vector["name"], install, query["system"], answer, content_path

    def _identifiable(self, command: str) -> set[str]:
        """Every word this command offers as an identity, by the test's own reading."""
        words = set(self._CORE.findall(command))
        words |= {t for t in self._TOKEN.findall(command) if t != self._RUNNER}
        words |= set(self._LAUNCHER.findall(command))
        return words

    def test_a_stated_identity_is_a_word_the_command_carries(self):
        wrong = []
        for name, _, _, answer, _content in self._catalogue_answers():
            for entry in answer.entries:
                if entry.emulator is None:
                    continue
                if not entry.command:
                    # The derived enumeration has no command to quote; the core
                    # it was derived from is what it may name.
                    if entry.emulator != entry.core_so:
                        wrong.append(f"{name}/{entry.label}: {entry.emulator!r} for core {entry.core_so!r}")
                elif entry.emulator not in self._identifiable(entry.command):
                    wrong.append(f"{name}/{entry.label}: {entry.emulator!r} is not in {entry.command!r}")
        assert wrong == []

    def test_a_null_identity_is_a_command_with_no_word_to_take(self):
        # And the corpus must actually reach one, or the claim above is
        # vacuous for the case it exists to protect.
        reached = 0
        wrong = []
        for name, _, _, answer, _content in self._catalogue_answers():
            for entry in answer.entries:
                if entry.emulator is not None:
                    continue
                reached += 1
                offered = self._identifiable(entry.command)
                if offered:
                    wrong.append(f"{name}/{entry.label}: null, but the command offers {sorted(offered)}")
        assert wrong == []
        assert reached, "no fixture machine reaches an entry no command identifies"

    @staticmethod
    def _sorted(pairs):
        return sorted(pairs, key=lambda pair: (pair[0] is None, pair[0] or "", pair[1] is None, pair[1] or 0))

    @staticmethod
    def _pairs(rows):
        """What a client joins on: which emulator, and which row of the launch list."""
        return [(row.emulator, row.declared_index) for row in rows]

    def test_where_both_answers_name_emulators_they_name_the_same_ones(self):
        # The join, over every catalogue the corpus declares: the same emulator
        # under the same identity and the same declared position, whichever
        # question is asked about it — nulls included, so a route that silently
        # dropped either field on one shape is caught.
        #
        # "Where both name emulators": a catalogue answer can name entries
        # against a firmware answer that names no core at all. EmuDeck's
        # sealed catalogue is that case — the readable layers declare no such
        # system, so the catalogue route derives its entries from the installed
        # cores while the firmware route answers empty; both answers state
        # `emulator-catalogue-sealed`, the catalogue one with
        # `emulator-list-derived` beside it and the firmware one with
        # `firmware-declaration-unknown`. No corpus vector reaches that state
        # (this loop would fail if one did), so the claim is held where it
        # applies.
        #
        # The pair and not the label, because the label does not join: the
        # derived enumeration's firmware cores carry no label at all
        # (a-bare-retroarch-derives-its-emulator-list-from-the-cores answers
        # label None against the catalogue's "mGBA") while both sides name
        # mgba_libretro.so. That asymmetry is the argument for the fields.
        wrong = []
        for name, install, system, answer, _content in self._catalogue_answers():
            catalogue = self._sorted(self._pairs(answer.entries))
            firmware = self._sorted(self._pairs(install.firmware_for_system(system=system).cores))
            if firmware != catalogue:
                wrong.append(f"{name}: catalogue {catalogue} vs firmware {firmware}")
        assert wrong == []

    def test_the_two_answers_agree_on_the_order_no_content_reorders(self):
        # Sorted above because ONE thing legitimately reorders the catalogue
        # answer and not the firmware one: a per-game altemulator. The firmware
        # route names no content (installations._firmware_catalogue_entries
        # passes content_path=None), so no per-game entry can match there,
        # while the catalogue answer promotes the row the gamelist names. Asked
        # without a content path, the two orders are identical — and this is
        # what says the sort above hides nothing else.
        wrong = []
        for name, install, system, answer, content in self._catalogue_answers():
            if content is not None:
                continue
            catalogue = self._pairs(answer.entries)
            firmware = self._pairs(install.firmware_for_system(system=system).cores)
            if firmware != catalogue:
                wrong.append(f"{name}: catalogue {catalogue} vs firmware {firmware}")
        assert wrong == []
