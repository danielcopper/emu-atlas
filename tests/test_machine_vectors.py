"""Run every 'machines' vector through the real detect() + resolver routes.

The vectors are the artifact; this is atlas's conformance run for the machines
family (schema 3). Each vector is a whole fixture machine — files, dirs,
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
from pathlib import Path

import pytest

import atlas
from atlas.machine import FixtureMachine
from scripts import validate_vectors
from atlas.contract import (
    catalogue_contract,
    savefile_answer_contract,
    savestate_answer_contract,
    systems_contract,
    firmware_contract,
    identification_contract,
    installation_answers_contract,
    installation_contract,
    rom_placement_contract,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_VECTOR_DIR = _REPO_ROOT / "vectors" / "machines"


def _load_vectors():
    files = sorted(_VECTOR_DIR.glob("*.json"))
    assert files, f"no vector files found in {_VECTOR_DIR}"
    for path in files:
        data = json.loads(path.read_text())
        assert data["family"] == "machines"
        assert data["schema"] == 3, f"{path}: runner speaks vector schema 3"
        for vector in data["vectors"]:
            yield pytest.param(vector, id=f"{path.stem}:{vector['name']}")


def _machine(inp) -> FixtureMachine:
    return FixtureMachine(
        inp["files"],
        symlinks=inp.get("symlinks"),
        cores=inp.get("cores"),
        dirs=inp.get("dirs"),
        inaccessible=inp.get("inaccessible"),
        unlistable=inp.get("unlistable"),
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


# expected key → (the input key that asks it, how the runner asks it). The map
# is what makes an unknown expectation an error instead of a silent pass: a
# vector could once carry an `expected.savelocation` typo and prove nothing,
# because the runner only ever looked for the keys it knew.
QUESTIONS = {
    "catalogue": ("catalogue_query", _catalogue),
    "systems": ("systems_query", _systems),
    "rom_location": ("rom_location_query", _rom_location),
    "aggregate": ("aggregate_query", _aggregate),
    "savefile_location": ("savefile_query", _savefile_location),
    "savestate_location": ("savestate_query", _savestate_location),
    "entry_savestate_location": ("entry_savestate_query", _entry_savestate_location),
    "firmware": ("firmware_query", _firmware),
    "identification": ("identify_query", _identification),
    "entry_savefile_location": ("entry_savefile_query", _entry_savefile_location),
}


@pytest.mark.parametrize("vector", list(_load_vectors()))
def test_machine_vector(vector):
    inp = vector["input"]
    expected = vector["expected"]
    name = vector["name"]
    rationale = vector.get("rationale", name)
    machine = _machine(inp)

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

    # The one exception, and why it is one: detection triggers on the marker,
    # so a machine without one has no installation for a vector to ask. It is
    # covered by a direct-handle test instead
    # (tests/test_installations.py::TestAMarkerThatIsGoneIsStatedNotDetected).
    UNREACHABLE_BY_FIXTURE = {"marker-missing"}

    def _exported_codes(self) -> set[str]:
        families = ("CAVEAT_", "HEALTH_ISSUE_", "UNRESOLVED_")
        return {
            value
            for name in atlas.__all__
            if name.startswith(families) and isinstance(value := getattr(atlas, name), str)
        }

    def _codes_in_corpus(self) -> set[str]:
        found: set[str] = set()

        def walk(node):
            if isinstance(node, dict):
                if isinstance(code := node.get("code"), str):
                    found.add(code)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        for path in sorted(_VECTOR_DIR.glob("*.json")):
            for vector in json.loads(path.read_text())["vectors"]:
                walk(vector["expected"])
        return found

    def test_every_code_appears_in_some_vector(self):
        uncovered = sorted(self._exported_codes() - self._codes_in_corpus() - self.UNREACHABLE_BY_FIXTURE)
        assert uncovered == []

    def test_the_exception_list_carries_nothing_a_vector_now_covers(self):
        # A code that gained a vector must lose its exemption, or the list
        # becomes a place where coverage quietly goes to die.
        assert sorted(self.UNREACHABLE_BY_FIXTURE & self._codes_in_corpus()) == []

    def test_the_corpus_shows_no_code_atlas_cannot_emit(self):
        assert sorted(self._codes_in_corpus() - self._exported_codes()) == []


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
        with pytest.raises(AssertionError, match="cannot ask for expected.savelocation"):
            test_machine_vector(self._vector({"savelocation": {}}))

    def test_an_expectation_without_its_query_is_refused(self):
        # The validator states this rule too; a port that only runs the runner
        # would otherwise meet a KeyError instead of the reason.
        with pytest.raises(AssertionError, match="needs input.systems_query"):
            test_machine_vector(self._vector({"systems": self._EMPTY_SYSTEMS}))

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
