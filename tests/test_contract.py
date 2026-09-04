"""Tests for atlas.contract — the serializers the vectors assert answers with.

The vectors exercise every serializer through whole machines; what belongs here
is the shape rules a single vector cannot state, starting with health: a finding
serializes as the caveat it is, and the installation form composes that rather
than restating it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import atlas
from atlas.machine import FixtureMachine
from atlas.contract import health_contract, installation_contract
from tests.answers import placed, state_placed
from tests.corpus import caveat_blocks, expected_blocks, vector_files

_VECTOR_DIR = Path(__file__).resolve().parents[1] / "vectors" / "machines"

HOME = "/home/deck"
RETRODECK_JSON = f"{HOME}/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
GONE = "/run/media/gone/retrodeck"


def _broken() -> atlas.RetroDeck:
    machine = FixtureMachine({RETRODECK_JSON: '{"paths": {"rd_home_path": "%s"}}' % GONE})
    return atlas.RetroDeck(HOME, machine)


def _healthy() -> atlas.RetroDeck:
    machine = FixtureMachine(
        {
            RETRODECK_JSON: '{"paths": {"rd_home_path": "/mnt/sd/retrodeck"}}',
            "/mnt/sd/retrodeck/roms/systeminfo.txt": "",
        },
        dirs=["/mnt/sd/retrodeck/saves"],
    )
    return atlas.RetroDeck(HOME, machine)


class TestHealthContract:
    """The health answer is an object like every other answer in the grammar."""

    def test_a_healthy_installation_answers_ok_with_no_issues(self):
        assert health_contract(_healthy().health()) == {"ok": True, "issues": []}

    def test_a_broken_one_is_not_ok(self):
        assert health_contract(_broken().health())["ok"] is False

    def test_a_finding_serializes_as_a_caveat(self):
        assert health_contract(_broken().health())["issues"][0] == {
            "code": atlas.HEALTH_ISSUE_ROOT_MISSING,
            "data": {"path": GONE},
        }

    def test_every_finding_is_carried(self):
        health = _broken().health()
        assert [f["code"] for f in health_contract(health)["issues"]] == list(health.codes)

    def test_ok_says_what_the_issues_say(self):
        # The summary is derived, never a second fact: it can only ever agree.
        answer = health_contract(_broken().health())
        assert answer["ok"] == (not answer["issues"])

    def test_the_form_is_json(self):
        # A read-only mapping inside would serialize fine here and fail in a
        # caller's json.dumps, so the check is the round trip, not the type.
        serialized = health_contract(_broken().health())
        assert json.loads(json.dumps(serialized)) == serialized


class TestInstallationContractCarriesTheFindings:
    """Inside an installation, health is a field — the findings, unwrapped."""

    def test_the_health_field_is_the_findings(self):
        handle = _broken()
        assert installation_contract(handle)["health"] == health_contract(handle.health())["issues"]

    def test_a_healthy_installation_states_an_empty_list(self):
        assert installation_contract(_healthy())["health"] == []

    def test_identity_is_unchanged(self):
        assert set(installation_contract(_healthy())) == {"kind", "label", "kinds", "root", "health"}

    def test_the_label_is_the_packaged_spelling_of_the_kind(self):
        # Presentation beside the key: the identifier is 'retrodeck' and the
        # project writes 'RetroDECK', which is a spelling a consumer could only
        # guess at from the identifier — and would guess wrong.
        handle = _healthy()
        assert installation_contract(handle)["label"] == atlas.distribution_label(handle.kind)


class TestTheSavestateFormOmitsGranularity:
    """The one field the two placement serializers do not share.

    A ``"granularity": null`` here would be a key no answer can ever fill, and
    a client would read it as "not established yet" rather than "cannot exist".
    Its absence is therefore contractual, and so is the rest of the form being
    identical — the two answers come off one upstream rule.
    """

    def _placements(self):
        handle = _healthy()
        return (
            atlas.savefile_placement_contract(placed(handle.savefile_location())),
            atlas.savestate_placement_contract(state_placed(handle.savestate_location())),
        )

    def test_granularity_is_absent_rather_than_null(self):
        _, state = self._placements()
        assert "granularity" not in state

    def test_every_other_key_is_the_same(self):
        save, state = self._placements()
        assert set(save) - set(state) == {"granularity"}
        assert set(state) - set(save) == set()

    def test_the_root_kind_names_the_savestate_anchor(self):
        _, state = self._placements()
        assert state["root_kind"] == atlas.ROOT_SAVESTATE_DIRECTORY

    def test_the_form_is_json(self):
        _, state = self._placements()
        assert json.loads(json.dumps(state)) == state


class TestTheStatedNoIsItsOwnShape:
    """#284: ``no_savestates`` is an answer — not a placement, not a refusal."""

    def _absence(self, **overrides):
        stated = {
            "emulator": "DEMO",
            "citation": "the whole tree at v1: no state serializer",
            "sources": ("standalone savestate card 'DEMO': prose",),
        }
        stated.update(overrides)
        return atlas.SavestateAbsence(**stated)

    def test_the_answer_serializer_branches_to_it(self):
        block = atlas.savestate_answer_contract(self._absence())
        assert set(block) == {"no_savestates"}
        assert block["no_savestates"]["emulator"] == "DEMO"
        assert block["no_savestates"]["citation"].startswith("the whole tree")
        assert block["no_savestates"]["caveats"] == []

    def test_the_cards_own_caveat_rides_contractually(self):
        caveat = atlas.Caveat(
            atlas.CAVEAT_UNVERIFIED_VERSION,
            "nothing ships this emulator",
            {"emulator": "DEMO", "verification": "build-unestablished"},
        )
        block = atlas.savestate_absence_contract(self._absence(caveats=(caveat,)))
        assert block["no_savestates"]["caveats"] == [
            {
                "code": "unverified-version",
                "data": {"emulator": "DEMO", "verification": "build-unestablished"},
            }
        ]

    def test_sources_stay_out_like_every_provenance(self):
        block = atlas.savestate_absence_contract(self._absence())
        assert "sources" not in block["no_savestates"]

    def test_a_citation_free_no_cannot_exist(self):
        with pytest.raises(ValueError, match="citation"):
            self._absence(citation="")

    def test_the_form_is_json(self):
        block = atlas.savestate_absence_contract(self._absence())
        assert json.loads(json.dumps(block)) == block


class TestTheAlternativesEntryIsItsOwnShape:
    """A conjunction entry and a one-of-several entry must not be mistakable."""

    def _option(self, file_name: str, regions: tuple[str, ...]) -> atlas.FirmwareRequirement:
        return atlas.FirmwareRequirement(
            core_so=None, system="psx", system_source="card", need="required",
            file_name=file_name, path=f"/bios/{file_name}", declared=file_name,
            description="prose stays prose", identity=None, found="missing", checked=None,
            regions=regions,
        )

    def _answer(self, *entries: atlas.FirmwareRequirement | atlas.FirmwareAlternatives) -> dict[str, Any]:
        core = atlas.CoreFirmware(
            core_so=None, label="DuckStation (Legacy) (Standalone)", declaration="packaged",
            requirements=tuple(entries),
            caveats=(atlas.Caveat("firmware-packaged-declaration", "", {}),),
        )
        answer = atlas.FirmwareAnswer(
            root="/bios", cores=(core,), unclaimed=(), hash_checked=False, sources=(), caveats=(),
        )
        return atlas.firmware_contract(answer)

    def test_a_group_serializes_as_the_single_key_alternatives_entry(self):
        block = self._answer(
            atlas.FirmwareAlternatives(
                options=(
                    self._option("scph5501.bin", ("ntsc-u",)),
                    self._option("scph5502.bin", ("ntsc-j", "pal")),
                )
            )
        )
        [entry] = block["cores"][0]["requirements"]
        assert set(entry) == {"alternatives"}
        assert [option["regions"] for option in entry["alternatives"]] == [["ntsc-u"], ["ntsc-j", "pal"]]
        # Each option is the full requirement — a reader picks one and has
        # everything a plain requirement states.
        assert all("satisfied" in option and "path" in option for option in entry["alternatives"])

    def test_a_plain_requirement_carries_no_regions_key(self):
        plain = atlas.FirmwareRequirement(
            core_so=None, system="psx", system_source="card", need="required",
            file_name="bios.bin", path="/bios/bios.bin", declared="bios.bin",
            description="", identity=None, found="missing", checked=None,
        )
        block = self._answer(plain)
        [entry] = block["cores"][0]["requirements"]
        assert "regions" not in entry
        assert "alternatives" not in entry

    def test_the_description_prose_stays_out_of_an_option(self):
        block = self._answer(
            atlas.FirmwareAlternatives(options=(self._option("scph5501.bin", ("ntsc-u",)),))
        )
        [entry] = block["cores"][0]["requirements"]
        assert "description" not in entry["alternatives"][0]

    def test_the_form_is_json(self):
        block = self._answer(
            atlas.FirmwareAlternatives(options=(self._option("scph5501.bin", ("ntsc-u",)),))
        )
        assert json.loads(json.dumps(block)) == block


class TestAnswerShapeDiscipline:
    """The half of the usage guide's shape rule a corpus walk can actually hold.

    The guide tells clients an answer is either the question's own fields flat
    or a single-key object naming the shape, and that an unrecognized wrapper
    takes the branch a refusal takes. What is asserted here is the wrapper set
    being **closed**: no single-key shape outside the documented pair reaches
    the corpus, both documented ones are really witnessed, and no flat answer
    carries a wrapper key — so a client that checks those two names by hand
    cannot be surprised by a third.

    What it does not hold, stated so the sentence is not read as more than it
    is: an envelope with two or more keys is indistinguishable from a flat
    answer here, because "the question's own fields" is not knowable from the
    corpus without a per-question key set. That set is what a generated
    contract reference would build, and the stronger check belongs beside it.
    """

    WRAPPERS = frozenset({"unresolved", "no_savestates"})

    @staticmethod
    def _answers():
        """Every serialized answer in the corpus, unwrapped from the aggregate."""
        for path in sorted(_VECTOR_DIR.glob("*.json")):
            for vector in json.loads(path.read_text(encoding="utf-8"))["vectors"]:
                for question, block in vector.get("expected", {}).items():
                    if question == "installations":
                        continue
                    if isinstance(block, list):  # the aggregate: one entry per installation
                        for entry in block:
                            yield path.name, vector["name"], question, entry["answer"]
                    else:
                        yield path.name, vector["name"], question, block

    def test_every_answer_is_flat_or_a_named_single_key_wrapper(self):
        seen: set[str] = set()
        for file_name, vector, question, answer in self._answers():
            where = f"{file_name}:{vector}:{question}"
            assert isinstance(answer, dict), f"{where}: an answer is a JSON object"
            if len(answer) == 1:
                [key] = answer
                assert key in self.WRAPPERS, (
                    f"{where}: single-key answer {key!r} is a shape the usage guide's rule "
                    f"cannot name — either give it fields of its own or add it to the "
                    f"documented wrapper set"
                )
                seen.add(key)
        assert seen == self.WRAPPERS, f"corpus witnesses {sorted(seen)}, expected both wrappers"

    def test_no_flat_answer_carries_a_wrapper_key(self):
        for file_name, vector, question, answer in self._answers():
            if len(answer) > 1:
                overlap = self.WRAPPERS & set(answer)
                assert not overlap, (
                    f"{file_name}:{vector}:{question}: flat answer also carries {sorted(overlap)}, "
                    f"which makes the two shapes mistakable"
                )


class TestOneKeyHasOneShape:
    """A ``(code, key)`` pair carries ONE JSON type across the whole corpus.

    The vocabulary round made lists out of comma-joined strings, and a client
    that switched on ``code`` and read ``data["files"]`` now gets an array. It
    gets one everywhere or the promise is worthless: a key that is a list on
    one emitter and a string on another is worse than the joined string was,
    because the joined string was at least consistent.

    Nothing else can see this. The validator checks each value against the
    three allowed shapes one at a time, the vectors assert their own expected
    block, and both pass while two emitters of one code disagree — which is
    exactly what happened: five pairs shipped with two types because the sweep
    that converted them looked for ``", ".join(`` and nothing else. This walks
    the corpus instead of the source, so an emitter written in any style at all
    is held to what its siblings already state.
    """

    @staticmethod
    def _shapes(vectors_dir: Path) -> dict[tuple[str, str], dict[str, list[str]]]:
        """``(code, key)`` → JSON type name → where the corpus shows it."""
        shapes: dict[tuple[str, str], dict[str, list[str]]] = {}
        for where, expected in expected_blocks(vectors_dir):
            for code, data in caveat_blocks(expected):
                for key, value in data.items():
                    seen = shapes.setdefault((code, key), {})
                    seen.setdefault(_json_type(value), []).append(where)
        return shapes

    def test_no_pair_carries_two_shapes(self):
        mixed = {
            f"{code}.{key}": {name: sorted(set(where))[:2] for name, where in seen.items()}
            for (code, key), seen in self._shapes(_VECTOR_DIR).items()
            if len(seen) > 1
        }
        assert mixed == {}

    def test_it_catches_a_single_disagreeing_emitter(self, tmp_path):
        """The probe: one value flipped inside a COPY of the real corpus.

        Copied and mutated rather than synthesised, so the check is proven
        against the shapes that actually ship — a hand-built two-vector fixture
        would pass a check that only ever looked at its own invention.
        """
        copied = tmp_path / "machines"
        copied.mkdir()
        flipped = 0
        for path in vector_files():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if not flipped:
                flipped = _flip_one_list_to_a_string(doc)
            (copied / path.name).write_text(json.dumps(doc), encoding="utf-8")
        assert flipped == 1, "no list-valued caveat data left in the corpus to mutate"
        mixed = {
            f"{code}.{key}"
            for (code, key), seen in self._shapes(copied).items()
            if len(seen) > 1
        }
        assert mixed, "the injected string went unnoticed"


def _json_type(value: Any) -> str:
    """The JSON type name of a data value — the three the contract allows."""
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _flip_one_list_to_a_string(node: Any) -> int:
    """Join the first list-valued caveat datum found, in place. Returns 1 if one was.

    The walker yields the ``data`` mapping itself, so assigning through it
    breaks the copy the probe then reads.
    """
    for _, data in caveat_blocks(node):
        for key, value in data.items():
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                data[key] = ", ".join(value)
                return 1
    return 0
