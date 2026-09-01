"""Tests for atlas._xml — the ElementTree shape atlas parses XML with.

The module exists to keep behavior identical while dropping a wrapper package a
frozen runtime need not ship (issue #339), so the tests pin the semantics the
call sites read the tree with: where ``find`` stops, what ``text`` covers, and
which documents come back as a refusal rather than a half-read tree. Then the
shapes the real files have — the synthetic wrapper ES-DE's rootless files are
read through, and a document that opens with a declaration — and last the
property the module exists for: atlas answers on a runtime that never shipped
the wrapper package.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import atlas
from atlas._xml import ParseError, fromstring

# One tree with everything the navigation tests need: repeated children, a
# nested element whose name repeats a shallower one, and text beside markup.
TREE = """<catalogue>
  <system>
    <name>dreamcast</name>
    <inner><name>buried</name></inner>
  </system>
  <system>
    <name>n64</name>
  </system>
  <note>free text</note>
</catalogue>"""

# es_systems.xml as ES-DE writes it when <loadExclusive/> is set: two
# document-level elements, which only survive inside a synthetic root.
WRAPPED_CATALOGUE = (
    "<atlas-wrapper><loadExclusive/>"
    "<systemList><system><name>gc</name></system></systemList>"
    "</atlas-wrapper>"
)


class TestNavigation:
    def test_find_returns_the_first_direct_child_of_that_name(self):
        first = fromstring(TREE).find("system")
        assert first is not None
        assert first.findtext("name") == "dreamcast"

    def test_find_never_reaches_a_grandchild(self):
        # <name> exists two levels down and must stay unfound: the catalogue
        # read asks the wrapper root for <loadExclusive/>, which ES-DE honors
        # only as a document child (SystemData.cpp:884,898), so a nested one —
        # which the frontend ignores — must not answer either.
        assert fromstring(TREE).find("name") is None

    def test_findall_returns_every_direct_child_in_document_order(self):
        systems = fromstring(TREE).findall("system")
        assert [system.findtext("name") for system in systems] == ["dreamcast", "n64"]

    def test_findall_of_an_absent_name_is_empty(self):
        assert fromstring(TREE).findall("command") == []

    def test_iter_walks_the_whole_tree_in_document_order(self):
        buried = [element.text for element in fromstring(TREE).iter("name")]
        assert buried == ["dreamcast", "buried", "n64"]

    def test_iter_includes_the_element_it_is_called_on(self):
        root = fromstring("<system><system/></system>")
        assert len(list(root.iter("system"))) == 2

    def test_iterating_an_element_yields_its_direct_children(self):
        tags = [child.tag for child in fromstring(TREE)]
        assert tags == ["system", "system", "note"]

    def test_a_leaf_iterates_over_nothing(self):
        assert list(fromstring("<a>text</a>")) == []


class TestText:
    def test_an_element_with_no_character_data_has_no_text(self):
        # None, not "": nothing was written between the tags.
        assert fromstring("<a></a>").text is None

    def test_an_empty_element_has_no_text(self):
        assert fromstring("<a/>").text is None

    def test_text_is_the_run_before_the_first_child(self):
        assert fromstring("<a>before<b/>after</a>").text == "before"

    def test_character_data_after_a_child_is_not_the_parents_text(self):
        assert fromstring("<a><b/>after</a>").text is None

    def test_a_childs_text_is_its_own(self):
        child = fromstring("<a>before<b>inner</b>after</a>").find("b")
        assert child is not None
        assert child.text == "inner"

    def test_whitespace_is_kept_as_written(self):
        assert fromstring("<a>  padded\n</a>").text == "  padded\n"

    def test_entity_references_arrive_expanded(self):
        assert fromstring("<a>&amp;&lt;tag&gt;</a>").text == "&<tag>"

    def test_findtext_answers_the_empty_string_for_a_childless_element(self):
        # An element that is there and says nothing, versus one nobody wrote:
        # different facts, so a default must not stand in for the first.
        assert fromstring("<a><b/></a>").findtext("b", "DEFAULT") == ""

    def test_findtext_answers_the_default_when_no_child_matches(self):
        assert fromstring("<a><b/></a>").findtext("c", "DEFAULT") == "DEFAULT"

    def test_findtext_answers_none_by_default(self):
        assert fromstring("<a><b/></a>").findtext("c") is None


class TestAttributes:
    def test_get_answers_the_attribute_value(self):
        assert fromstring("<a label='Flycast'/>").get("label") == "Flycast"

    def test_get_answers_the_default_for_an_attribute_nobody_set(self):
        assert fromstring("<a/>").get("label", "") == ""

    def test_get_answers_none_by_default(self):
        assert fromstring("<a/>").get("label") is None

    def test_an_attribute_set_to_nothing_is_the_empty_string(self):
        assert fromstring("<a label=''/>").get("label", "MISSING") == ""


class TestRefusals:
    def test_unclosed_markup_is_refused(self):
        with pytest.raises(ParseError):
            fromstring("<a><b></a>")

    def test_text_that_is_not_xml_at_all_is_refused(self):
        with pytest.raises(ParseError):
            fromstring("not xml")

    def test_a_second_root_element_is_refused(self):
        # The reason the rootless ES-DE files are read through a wrapper.
        with pytest.raises(ParseError):
            fromstring("<a/><b/>")

    def test_an_empty_document_is_refused(self):
        # atlas hands unread files through as "" — this is the refusal that
        # keeps such a read from looking like a file that says nothing.
        with pytest.raises(ParseError):
            fromstring("")

    def test_a_whitespace_only_document_is_refused(self):
        with pytest.raises(ParseError):
            fromstring("  \n  ")

    def test_an_undefined_entity_is_refused(self):
        with pytest.raises(ParseError):
            fromstring("<a>&nowhere;</a>")

    def test_an_entity_a_dtd_never_declared_is_refused(self):
        # The document points at a DTD the parser does not fetch, so expat
        # reports the reference instead of failing on it; dropping it silently
        # would read the file as saying something it does not.
        with pytest.raises(ParseError):
            fromstring('<!DOCTYPE a SYSTEM "outside.dtd"><a>&nowhere;</a>')

    def test_a_refusal_is_a_syntax_error(self):
        # ElementTree's ParseError derives from SyntaxError; a caller catching
        # the broader name keeps catching this one.
        assert issubclass(ParseError, SyntaxError)


class TestTheShapesAtlasReads:
    def test_a_document_declaration_is_read_from_a_string(self):
        root = fromstring('<?xml version="1.0" encoding="UTF-8"?><systemList/>')
        assert root.tag == "systemList"

    def test_a_declared_encoding_does_not_govern_a_string_document(self):
        # The text arrives already decoded, and expat is told so — a file
        # declaring a legacy encoding still reads as the string atlas holds.
        root = fromstring('<?xml version="1.0" encoding="ISO-8859-1"?><a>ümläut</a>')
        assert root.text == "ümläut"

    def test_the_wrapper_reads_a_rootless_catalogue(self):
        assert fromstring(WRAPPED_CATALOGUE).find("loadExclusive") is not None

    def test_the_wrapper_keeps_the_catalogue_beside_the_flag(self):
        system_list = fromstring(WRAPPED_CATALOGUE).find("systemList")
        assert system_list is not None
        assert [system.findtext("name") for system in system_list.findall("system")] == ["gc"]

    def test_the_settings_fragments_are_read_as_direct_children(self):
        # es_settings.xml, wrapped: a flat run of <string> elements read by
        # attribute, which is how the ROM directory is found.
        root = fromstring(
            "<es-settings><string name='ROMDirectory' value='/roms'/>"
            "<string name='Empty'/></es-settings>"
        )
        settings = {element.get("name"): element.get("value") or "" for element in root}
        assert settings == {"ROMDirectory": "/roms", "Empty": ""}


def _wrapper_imports(source: str, filename: str) -> list[str]:
    """Every import of the ``xml.etree`` wrapper in *source*, with its file:line.

    Both import statements that bind it: the dotted ``import xml.etree…`` and
    the ``from``-forms, including ``from xml import etree``, which names the
    subpackage without ever spelling it dotted.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "xml.etree" or alias.name.startswith("xml.etree."):
                    found.append(f"{filename}:{node.lineno} import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "xml.etree" or module.startswith("xml.etree."):
                found.append(f"{filename}:{node.lineno} from {module} import ...")
            elif module == "xml" and any(alias.name == "etree" for alias in node.names):
                found.append(f"{filename}:{node.lineno} from xml import etree")
    return found


_PACKAGE_DIR = Path(atlas.__file__).resolve().parent

# Runs in a child interpreter, because the absence has to hold for the whole
# process: a meta-path finder refuses the packages a frozen runtime may lack,
# and the probe proves the refusal bites before it asks anything of atlas. It
# then asks twice — a question through the contract, which is what a consumer
# runs, and the two parses, because an empty home reads no XML at all.
_PROBE = """\
import io
import json
import sys
from contextlib import redirect_stdout

home, blocked = sys.argv[1], tuple(sys.argv[2:])


class Absent:
    def find_spec(self, name, path=None, target=None):
        if name.startswith(blocked):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None


sys.meta_path.insert(0, Absent())
for name in [module for module in sys.modules if module.startswith(blocked)]:
    del sys.modules[name]

for name in blocked:
    try:
        __import__(name)
    except ModuleNotFoundError:
        continue
    print(f"{name} is importable — the missing-package premise is broken", file=sys.stderr)
    sys.exit(3)

from atlas import cli
from atlas.esde import parse_es_settings, parse_es_systems

stdout = io.StringIO()
with redirect_stdout(stdout):
    rc = cli.run(["detect"], home=home)
layer = parse_es_systems(
    '<?xml version="1.0"?><systemList><system><name>n64</name></system></systemList>',
    provenance="probe",
)
print(json.dumps({
    "rc": rc,
    "detect": json.loads(stdout.getvalue()),
    "systems": sorted(layer.systems),
    "settings": parse_es_settings('<string name="ROMDirectory" value="/roms" />'),
}))
"""


class TestTheRuntimeItHasToRunOn:
    """The property the module exists for: no wrapper package, still an answer.

    A frozen consumer runtime ships only what its build analysis reached, and
    Decky Loader's bundle reached the expat extension but not ``xml.etree``
    (issue #339). Both halves of that are pinned — that no module reintroduces
    the import, and that a runtime without it answers a question through the
    contract and parses the files it would parse — the way
    ``tests/test_relocation.py`` pins the vendoring property.
    """

    def test_no_module_in_the_package_imports_the_wrapper(self):
        found: list[str] = []
        for path in sorted(_PACKAGE_DIR.rglob("*.py")):
            relative = str(path.relative_to(_PACKAGE_DIR))
            found.extend(_wrapper_imports(path.read_text(encoding="utf-8"), relative))
        assert found == [], "the package imports xml.etree:\n" + "\n".join(found)

    @pytest.mark.parametrize(
        "blocked",
        [
            pytest.param(("xml.etree",), id="without-the-etree-wrapper"),
            # ``xml.parsers`` is a wrapper package of the same kind, so the
            # fallback to the bare ``pyexpat`` extension is pinned too.
            pytest.param(("xml.etree", "xml.parsers"), id="without-either-wrapper"),
        ],
    )
    def test_the_package_answers_without_the_wrapper_packages(self, tmp_path, blocked):
        probe = tmp_path / "probe.py"
        probe.write_text(_PROBE, encoding="utf-8")
        home = tmp_path / "home"
        home.mkdir()

        result = subprocess.run(
            [sys.executable, str(probe), str(home), *blocked],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(_PACKAGE_DIR.parent)},
            timeout=120,
        )

        assert result.returncode == 0, result.stderr
        answer = json.loads(result.stdout)
        # `detect` over an empty home is the contract's answer for "nothing
        # installed here"; the two parses are the XML the shim actually reads.
        assert answer == {
            "rc": 0,
            "detect": [],
            "systems": ["n64"],
            "settings": {"ROMDirectory": "/roms"},
        }
