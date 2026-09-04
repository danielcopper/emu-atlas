"""Tests for the generated contract reference.

The page is derived, so a stale one is a claim nobody re-read: the first test
regenerates it and demands the committed bytes. The rest hold the derivations
that make the page worth believing — the cross-check that refuses to publish a
false nullability, the rule that only calls a value list closed where the source
says it is, and the markdown the formatter has to leave alone for the exact
comparison above to be possible at all.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from atlas.firmware import FirmwareAlternatives, FirmwareRequirement
from atlas.placement import SavefilePlacement
from scripts import generate_contract_reference as reference


CODE_SECTION = "## Caveat codes"
VALUE_SECTION = "## Caveat data values"


@pytest.fixture(scope="module")
def attributes() -> reference.Annotations:
    return reference.Annotations()


@pytest.fixture(scope="module")
def generated() -> str:
    lines, failures = reference.build()
    assert not failures, failures
    return "\n".join(lines).rstrip("\n") + "\n"


def code_table_of(page: str) -> str:
    """The `Caveat codes` section alone, where one row per exported code lives."""
    start = page.index(CODE_SECTION)
    return page[start : page.index(VALUE_SECTION, start)]


class TestTheCommittedReferenceIsRegenerated:
    def test_it_equals_a_fresh_generation(self, generated: str) -> None:
        committed = reference.OUTPUT_PATH.read_text(encoding="utf-8")
        assert committed == generated, (
            "the committed docs/contract-reference.md is not what the generator produces — regenerate with "
            "`python scripts/generate_contract_reference.py` (then `deno fmt docs/contract-reference.md`)"
        )

    def test_every_exported_code_has_a_row(self, generated: str) -> None:
        # A code the page omits is a piece of the contract a consumer cannot
        # look up, and the omission would be silent. The search is the code
        # table alone: the per-question tables carry rows in the same spelling,
        # and searching the whole page would let a dropped row pass on any code
        # some question happens to ride.
        import atlas

        codes = {
            value
            for name in atlas.__all__
            if name.startswith(reference.CODE_PREFIXES) and isinstance(value := getattr(atlas, name), str)
        }
        table = code_table_of(generated)
        missing = sorted(code for code in codes if f"| `{code}`" not in table)
        assert not missing, f"exported codes with no row in the code table: {missing}"

    def test_every_internal_link_reaches_a_heading(self, generated: str) -> None:
        targets = {
            reference.anchor(line.lstrip("# ").strip())
            for line in generated.splitlines()
            if line.startswith("#")
        }
        links = {
            part.split(")", 1)[0]
            for chunk in generated.split("](#")[1:]
            for part in ["#" + chunk]
        }
        assert links, "the page states no internal links at all"
        assert links <= targets, f"links with no heading: {sorted(links - targets)}"


class TestTheCrossCheckBetweenTheAnnotationsAndTheVectors:
    """The one disagreement that must stop the page, and the one that must not."""

    def _walk(self, attributes: reference.Annotations, answer: object) -> reference.ShapeWalk:
        walk = reference.ShapeWalk((SavefilePlacement,), attributes, None)
        walk.add(answer)
        return walk

    def test_a_null_where_the_annotation_admits_none_is_a_failure(
        self, attributes: reference.Annotations
    ) -> None:
        walk = self._walk(attributes, {"dir": None})
        failures = reference.contradictions({"savefile_placement_contract": walk})
        assert len(failures) == 1
        assert "'dir'" in failures[0]
        assert "admits no None" in failures[0]

    def test_a_nullable_field_no_vector_nulls_is_not_a_failure(
        self, attributes: reference.Annotations
    ) -> None:
        walk = self._walk(attributes, {"physical_dir": "/saves"})
        assert reference.contradictions({"savefile_placement_contract": walk}) == []
        row = walk.fields["physical_dir"]
        assert row is not None
        assert row.nullable is True
        assert walk.paths["physical_dir"].nulls == 0

    def test_an_unannotated_path_states_nothing_either_way(
        self, attributes: reference.Annotations
    ) -> None:
        walk = self._walk(attributes, {"invented_by_nobody": None})
        assert reference.contradictions({"savefile_placement_contract": walk}) == []
        assert walk.fields["invented_by_nobody"] is None


class TestJoiningAJsonKeyToTheAttributeItSerializes:
    def test_the_only_rename_read_off_contract_py_is_the_alternatives_group(self) -> None:
        # The join is by name everywhere else. If contract.py grows a second
        # comprehension-filled key under a new name, this is where it shows up.
        assert reference.contract_key_renames() == {"alternatives": {"options"}}

    def test_the_rename_resolves_the_group_to_its_own_field(
        self, attributes: reference.Annotations
    ) -> None:
        found, _ = attributes.resolve((FirmwareRequirement, FirmwareAlternatives), "alternatives")
        assert found is not None
        assert found.owner == "FirmwareAlternatives"
        assert found.name == "options"

    def test_a_property_is_annotated_and_marked_as_one(
        self, attributes: reference.Annotations
    ) -> None:
        found = attributes.attribute(FirmwareRequirement, "satisfied")
        assert found is not None
        assert found.kind == reference.PROPERTY
        assert found.nullable is True

    def test_a_method_states_no_nullability(self, attributes: reference.Annotations) -> None:
        from atlas.installations import Installation

        found = attributes.attribute(Installation, "root")
        assert found is not None
        assert found.kind == reference.METHOD
        assert found.nullable is None

    def test_a_literal_field_carries_its_vocabulary(self, attributes: reference.Annotations) -> None:
        from atlas.placement import ROOT_KINDS

        found = attributes.attribute(SavefilePlacement, "root_kind")
        assert found is not None
        assert found.vocabularies[0] == reference.Vocabulary(
            "Literal[SavefilePlacement.root_kind]", ROOT_KINDS
        )

    def test_a_field_stating_its_vocabulary_twice_is_named_twice(
        self, attributes: reference.Annotations
    ) -> None:
        # `need` is annotated `Literal[...]` *and* checked against FIRMWARE_NEEDS
        # in __post_init__. Keeping only the annotation would drop the exported
        # name a consumer branches on from the closed-vocabulary table.
        from atlas.firmware import FIRMWARE_NEEDS

        found = attributes.attribute(FirmwareRequirement, "need")
        assert found is not None
        assert found.vocabularies == (
            reference.Vocabulary("Literal[FirmwareRequirement.need]", FIRMWARE_NEEDS),
            reference.Vocabulary("FIRMWARE_NEEDS", FIRMWARE_NEEDS),
        )

    def test_a_post_init_check_is_a_vocabulary_where_the_annotation_is_bare(
        self, attributes: reference.Annotations
    ) -> None:
        from atlas.placement import ROLES, FileGroup

        found = attributes.attribute(FileGroup, "role")
        assert found is not None
        assert found.vocabularies == (reference.Vocabulary("ROLES", ROLES),)

    def test_a_local_or_a_loop_variable_is_not_an_attribute(self) -> None:
        # `self.<attr>` is the whole of what a __post_init__ body states about
        # an attribute. Reading a bare name there would put `seen` and `option`
        # on the page as fields the type never declares.
        local = ast.parse(
            "T = ('a', 'b')\n"
            "class Thing:\n"
            "    def __post_init__(self):\n"
            "        seen = compute(self.x)\n"
            "        if seen not in T:\n"
            "            raise ValueError\n"
        )
        loop = ast.parse(
            "T = ('a', 'b')\n"
            "class Thing:\n"
            "    def __post_init__(self):\n"
            "        for option in self.options:\n"
            "            if option not in T:\n"
            "                raise ValueError\n"
        )
        assert reference.module_post_init_checks(local) == {}
        assert reference.module_post_init_checks(loop) == {}

    def test_a_helper_that_rebinds_its_parameter_states_nothing(self) -> None:
        # What the helper compares is no longer what the caller handed over.
        rebinding = ast.parse(
            "T = ('a', 'b')\n"
            "def helper(kind):\n"
            "    kind = normalise(kind)\n"
            "    if kind not in T:\n"
            "        raise ValueError\n"
            "class Thing:\n"
            "    def __post_init__(self):\n"
            "        helper(self.kind)\n"
        )
        assert reference.module_post_init_checks(rebinding) == {}

    def test_an_attribute_checked_against_two_tuples_states_neither(self) -> None:
        # Which tuple holds depends on the case, and the column has one cell.
        per_case = ast.parse(
            "T = ('a', 'b')\n"
            "U = ('c', 'd')\n"
            "class Thing:\n"
            "    def __post_init__(self):\n"
            "        if self.mode == 'x':\n"
            "            if self.k not in T:\n"
            "                raise ValueError\n"
            "        elif self.k not in U:\n"
            "            raise ValueError\n"
        )
        assert reference.module_post_init_checks(per_case) == {}

    def test_one_tuple_holds_wherever_the_check_stands(self) -> None:
        # `FirmwareRequirement.checked` is checked inside the branch that
        # reaches it, and `FIRMWARE_CHECKED` is still the whole of what it
        # admits besides None — a top-level-only rule would drop it.
        nested = ast.parse(
            "T = ('a', 'b')\n"
            "class Thing:\n"
            "    def __post_init__(self):\n"
            "        if self.present:\n"
            "            if self.k not in T:\n"
            "                raise ValueError\n"
        )
        assert reference.module_post_init_checks(nested) == {
            "Thing": {"k": reference.Vocabulary("T", ("a", "b"))}
        }

    def test_a_membership_check_outside_post_init_states_no_vocabulary(self) -> None:
        # A membership test in any other method is a decision that method takes,
        # not a statement about what the attribute admits — and the page says
        # `__post_init__`, so the scan reads `__post_init__`.
        module = ast.parse(
            "NEEDS = ('required', 'optional')\n"
            "class Thing:\n"
            "    def __post_init__(self):\n"
            "        if self.need not in NEEDS:\n"
            "            raise ValueError\n"
            "    def check(self):\n"
            "        if self.mood not in NEEDS:\n"
            "            raise ValueError\n"
        )
        assert reference.module_post_init_checks(module) == {
            "Thing": {"need": reference.Vocabulary("NEEDS", ("required", "optional"))}
        }


class TestWhatTheSourceStatesAboutACodesData:
    def test_literal_keys_are_named_and_dynamic_sites_counted(self) -> None:
        entry = reference.CodeSites(literal_keys={"path"}, literal_sites=1, dynamic_sites=2)
        cell = reference.source_keys_cell(entry, frozenset({"path"}), 0)
        assert cell == "`path` (+2 sites building data dynamically)"

    def test_one_dynamic_site_is_singular(self) -> None:
        entry = reference.CodeSites(dynamic_sites=1)
        assert reference.source_keys_cell(entry, frozenset(), 0) == "built dynamically at 1 site"

    def test_a_key_only_the_corpus_carried_is_named_on_the_row(self) -> None:
        # The column is what the attributable sites spell, and a reader
        # branching on `root` would otherwise read it as the whole list.
        entry = reference.CodeSites(literal_keys={"path"}, literal_sites=1)
        cell = reference.source_keys_cell(entry, frozenset({"path", "root"}), 0)
        assert cell == "`path` (+`root` spelled at no attributable site)"

    def test_a_witnessed_code_no_site_spells_was_built_through_a_variable(self) -> None:
        # The scan reads every Caveat(...) and Unresolved(...) call, so a code a
        # vector carried and no site spells came from a site naming its code
        # through a variable — which is what the cell must say. "No site" would
        # be a claim about the source the scan cannot make.
        assert reference.source_keys_cell(None, frozenset({"path"}), 1) == "code named through a variable"

    def test_a_code_neither_spelled_nor_witnessed_states_the_absence(self) -> None:
        assert reference.source_keys_cell(None, None, 1) == "no construction site names this code"

    def test_an_argument_the_scan_cannot_read_is_not_read_as_no_data(self) -> None:
        # `Caveat(CODE, *_state(...))` supplies data from a call this scan
        # cannot see. Counting it as the constructor's empty default would
        # publish a key list that is short without saying so.
        sites, _ = reference.caveat_construction_sites()
        assert sites["core-mode-unestablished"].dynamic_sites >= 1

    def test_the_universal_the_page_prints_holds_for_every_module(self) -> None:
        # The page says every Caveat(...) and Unresolved(...) call is read, and
        # the scan matches the called name. Any other name for the constructor
        # makes a site invisible, and the page would then call a code whose site
        # spells it unattributable — so an alias on the import, a star import
        # that hides one, and a rebinding to a second name are all defects.
        offenders: list[str] = []
        for path, tree in reference.package_modules():
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name in reference.CAVEAT_TYPES and alias.asname:
                            offenders.append(f"{path.name}:{node.lineno} {alias.name} as {alias.asname}")
                        if alias.name == "*" and (node.module or "").startswith("atlas"):
                            offenders.append(f"{path.name}:{node.lineno} star import")
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    value = node.value
                    if isinstance(value, ast.Name) and value.id in reference.CAVEAT_TYPES:
                        offenders.append(f"{path.name}:{node.lineno} rebinds {value.id}")
        assert not offenders, f"the construction-site scan cannot see these: {offenders}"


class TestEveryPathTheWalkStoppedAtIsNamed:
    """Nothing the walk declined to enter may leave the page without a trace."""

    def test_a_caveat_is_walked_even_where_the_attribute_declares_nothing(
        self, attributes: reference.Annotations
    ) -> None:
        # Installation health is a tuple of caveats behind a method, so there
        # are no members to descend into — and `{code, data}` is the caveat
        # serialization all the same. Stopping here left the page silent about
        # the shape of a health finding.
        from atlas.installations import Installation

        walk = reference.ShapeWalk((Installation,), attributes, None)
        walk.add({"health": [{"code": "root-missing", "data": {"path": "/gone"}}]})
        assert "health[].code" in walk.paths
        assert "health[].data" in walk.paths
        assert walk.stopped == {}

    def test_a_mapping_is_still_stopped_at(self, attributes: reference.Annotations) -> None:
        from atlas.placement import Caveat

        walk = reference.ShapeWalk((Caveat,), attributes, None)
        walk.add({"code": "x", "data": {"path": "/one"}})
        assert "data.path" not in walk.paths

    def test_the_nested_table_names_every_shape_a_walk_stopped_at(self) -> None:
        # A carried shape with no section of its own used to be filtered out of
        # the table, so the page stated nothing about that path at all.
        built = reference.read_everything()
        page = "\n".join(reference.answer_shapes_section(built))
        stopped = sum(len(shapes) for walk in built.walks.values() for shapes in walk.stopped.values())
        rows = sum(1 for line in page.splitlines() if line.startswith("| `") and line.count("|") == 4)
        assert stopped, f"{stopped} shapes stopped at, {rows} rows"
        assert rows == stopped, f"{stopped} shapes stopped at, {rows} rows"

    def test_no_section_reads_the_repository_a_second_time(self) -> None:
        # `Reference` promises a section states what is already known. A section
        # that re-read the source could disagree with the header's counts about
        # the same fact, and nothing would say which one was stale. Which
        # functions read is derived, not listed: whatever calls `ast.parse`,
        # `read_text`, `glob` or `rglob`, and whatever reaches one of those.
        module = ast.parse(pathlib.Path(reference.__file__).read_text(encoding="utf-8"))
        functions = {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}

        def calls(node: ast.AST) -> set[str]:
            return {c.func.id for c in ast.walk(node) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}

        def methods(node: ast.AST) -> set[str]:
            return {
                c.func.attr for c in ast.walk(node) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
            }

        readers = {n for n, f in functions.items() if methods(f) & {"parse", "read_text", "glob", "rglob"}}
        growing = True
        while growing:
            growing = False
            for name, node in functions.items():
                if name not in readers and calls(node) & readers:
                    readers.add(name)
                    growing = True
        # A positive control: the assembly does read, so an empty closure would
        # mean the derivation found nothing and the assertion below proved none.
        assert "read_everything" in readers
        offenders = [
            f"{section.__name__} reaches {sorted(calls(functions[section.__name__]) & readers)}"
            for section in (*reference.SECTIONS, reference.linked_title)
            # A section that reads inline is seeded into ``readers`` itself, which the
            # call test alone would wave through — the most direct violation of all.
            if section.__name__ in readers or calls(functions[section.__name__]) & readers
        ]
        assert not offenders, offenders

    def test_a_carried_shape_with_no_section_is_named_rather_than_linked(self) -> None:
        built = reference.read_everything()
        anonymous = reference.Shape("object", frozenset({"some_core_option"}))
        assert reference.linked_title(built, anonymous) == "unattributed object of some_core_option"


class TestTheProseTheAnswerTypesCarry:
    def test_it_counts_docstrings_and_comments_apart(self) -> None:
        # `Caveat` declares code, message and data, and only `data` is preceded
        # by a comment about that one field. A count that lumped the two forms
        # together would let the page claim prose it cannot quote.
        from atlas.placement import Caveat

        assert reference.attribute_prose([Caveat]) == reference.AttributeProse(
            attributes=3, docstrings=0, commented=1
        )


class TestDescribingACaveatDataKeysValues:
    VOCABULARIES = {"NEEDS": ("required", "optional"), "BIG": ("a", "b", "c", "d")}

    def test_a_closed_set_is_claimed_only_on_the_whole_tuple(self) -> None:
        assert reference.describe_values(
            ["required", "optional", "required"], self.VOCABULARIES
        ) == "closed set `NEEDS`: `optional`, `required`"

    def test_holding_part_of_a_tuple_is_not_a_closed_set(self) -> None:
        # "a" is a member of BIG, and that is no evidence that BIG is this
        # key's vocabulary — the values are listed instead.
        assert reference.describe_values(["a"], self.VOCABULARIES) == "`a`"

    def test_absolute_paths_are_named_rather_than_listed(self) -> None:
        assert reference.describe_values(["/one", "/two"], {}) == "absolute path (2 distinct)"

    def test_too_many_values_are_counted(self) -> None:
        values = [f"value-{n}" for n in range(reference.VALUE_LIST_LIMIT + 1)]
        assert reference.describe_values(values, {}) == f"{len(values)} distinct values"

    def test_a_long_value_is_counted_rather_than_printed(self) -> None:
        assert reference.describe_values(["x" * 200], {}) == "1 distinct values"

    def test_non_string_values_report_their_json_types(self) -> None:
        assert reference.describe_values([["a"], {"b": "c"}], {}) == "array, object (2 observed)"


class TestTheMarkdownTheFormatterLeavesAlone:
    def test_a_table_pads_every_cell_to_its_column(self) -> None:
        assert reference.table(["a", "bbbbb"], [["longer", "x"]]) == [
            "| a      | bbbbb |",
            "| ------ | ----- |",
            "| longer | x     |",
        ]

    def test_a_narrow_column_keeps_its_own_width(self) -> None:
        # dprint sizes the rule to the column, not to a three-dash minimum.
        assert reference.table(["a"], [["x"]]) == ["| a |", "| - |", "| x |"]

    def test_a_header_only_table_still_renders(self) -> None:
        assert reference.table(["only"], []) == ["| only |", "| ---- |"]

    def test_prose_fills_to_the_line_width(self) -> None:
        words = ["word"] * 60
        lines = reference.wrap(" ".join(words))
        assert all(len(line) <= reference.LINE_WIDTH for line in lines)
        assert " ".join(lines).split() == words

    def test_a_link_is_never_broken_across_lines(self) -> None:
        # dprint keeps a link whole, so a filler that split one would write a
        # page the formatter immediately rewrites.
        lines = reference.wrap(f"{'word ' * 22}[a link with spaces](#somewhere) and more words after it")
        assert any("[a link with spaces](#somewhere)" in line for line in lines)
        assert max(len(line) for line in lines) <= reference.LINE_WIDTH

    def test_a_pipe_in_a_cell_is_escaped(self) -> None:
        assert reference.cell("str | None") == "str \\| None"

    def test_an_anchor_matches_the_heading_it_links_to(self) -> None:
        assert reference.anchor("installation_contract (array)") == "#installation_contract-array"
        assert reference.anchor("savefile_placement_contract") == "#savefile_placement_contract"


class TestTheShapesTheCorpusStates:
    def test_a_list_answer_is_an_array_shape_over_its_element_keys(self) -> None:
        shape = reference.shape_of([{"a": 1}, {"b": 2}])
        assert shape == reference.Shape("array", frozenset({"a", "b"}))

    def test_an_empty_list_carries_no_keys(self) -> None:
        assert reference.shape_of([]) == reference.Shape("array", frozenset())

    def test_an_empty_array_is_named_rather_than_attributed(self) -> None:
        empty = reference.Shape("array", frozenset())
        title = reference.shape_title(empty, reference.Serializer(None, False, ()))
        assert title == reference.EMPTY_ARRAY

    def test_a_shape_is_attributed_to_the_public_single_shape_function(self) -> None:
        produced = reference.contract_shapes()
        placement = reference.Shape(
            "object",
            frozenset({"dir", "root_kind", "needs", "fallback_dir", "physical_dir", "file_set", "caveats"}),
        )
        serializer = reference.serializer_for(placement, produced)
        assert serializer.name == "savestate_placement_contract"
        assert not serializer.per_element
        assert "savestate_answer_contract" in serializer.reached_through

    def test_an_array_of_one_functions_objects_is_that_function_per_element(self) -> None:
        produced = reference.contract_shapes()
        installations = reference.Shape(
            "object", frozenset({"kind", "label", "kinds", "root", "health"})
        )
        as_array = reference.Shape("array", installations.keys)
        assert reference.serializer_for(installations, produced).name == "installation_contract"
        per_element = reference.serializer_for(as_array, produced)
        assert per_element.name == "installation_contract"
        assert per_element.per_element


class TestTheWalkStopsWhereTheTypeStopsSpeaking:
    def test_a_caveats_data_object_is_a_leaf(self, attributes: reference.Annotations) -> None:
        walk = reference.ShapeWalk((SavefilePlacement,), attributes, None)
        walk.add({"caveats": [{"code": "no-core", "data": {"core_so": "x.so"}}]})
        assert "caveats[].data" in walk.paths
        assert "caveats[].data.core_so" not in walk.paths

    def test_an_array_inside_an_array_is_walked_to_its_leaves(
        self, attributes: reference.Annotations
    ) -> None:
        # No answer in the corpus nests a list in a list; a future one would be
        # walked rather than shown as an array with no contents.
        walk = reference.ShapeWalk((SavefilePlacement,), attributes, None)
        walk.add({"needs": [["a"], ["b"]]})
        assert walk.paths["needs[][]"].types == {"string": 2}

    def test_a_step_into_a_list_states_what_a_member_admits(
        self, attributes: reference.Annotations
    ) -> None:
        # `regions` is `tuple[str, ...] | None`: the *list* may be absent, and a
        # member of it is a string. Carrying the container's nullability down
        # would publish "can be null: yes" for a member that never can be, and
        # a null member could then never contradict the annotations.
        walk = reference.ShapeWalk((FirmwareRequirement,), attributes, None)
        walk.add({"regions": ["ntsc-j"]})
        container = walk.fields["regions"]
        member = walk.fields["regions[]"]
        assert container is not None
        assert container.nullable is True
        assert member is not None
        assert member.nullable is False
        assert container.annotation == "tuple[str, ...] | None"
        assert member.annotation == "str"

    def test_a_null_member_of_a_nullable_list_is_a_contradiction(
        self, attributes: reference.Annotations
    ) -> None:
        walk = reference.ShapeWalk((FirmwareRequirement,), attributes, None)
        walk.add({"regions": [None]})
        failures = reference.contradictions({"firmware_contract": walk})
        assert len(failures) == 1
        assert "'regions[]'" in failures[0]

    def test_a_step_into_something_that_is_not_a_sequence_keeps_what_is_known(
        self, attributes: reference.Annotations
    ) -> None:
        # `health` is a method: there is no sequence layer to strip, and the
        # element keeps the one thing the scan does know about the path.
        from atlas.installations import Installation

        found = attributes.attribute(Installation, "health")
        assert attributes.element_of(found) is found

    def test_a_generic_payload_is_recorded_as_the_shape_it_carried(
        self, attributes: reference.Annotations
    ) -> None:
        from atlas.every_installation import InstallationAnswer

        walk = reference.ShapeWalk((InstallationAnswer,), attributes, None)
        walk.add([{"installation": {"kind": "retrodeck"}, "answer": {"systems": [], "caveats": []}}])
        assert "[].answer.systems" not in walk.paths
        assert walk.stopped["[].answer"] == {
            reference.Shape("object", frozenset({"systems", "caveats"})): 1
        }
