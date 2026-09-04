"""Generate docs/contract-reference.md from atlas's own types and the vectors.

The reference answers one question per row: a consumer holding a serialized
answer wants to know which fields come back, what they are called, which can be
``null``, and which caveat codes carry which data keys. Nothing here is
hand-written — every cell is derived from one of five readings, and the page
says which reading spoke:

- **the annotations** — :func:`typing.get_type_hints`, :func:`dataclasses.fields`
  and the properties of the answer types. Authoritative on what *can* appear: a
  field is nullable when its annotation admits ``None``, and a closed vocabulary
  is a ``Literal`` or a membership check a ``__post_init__`` makes against a
  named tuple, directly or through a helper it hands the attribute to.
- **the vectors** — ``vectors/machines/*.json``. Authoritative on what *did*
  appear: which keys a real answer carried, where ``null`` was really produced,
  and which caveat code rode which question with which data keys.
- **the construction sites** — an AST scan of ``atlas/`` for ``Caveat(...)`` and
  ``Unresolved(...)``. A site whose ``data`` is a literal dict states its keys in
  the source; a site that builds ``data`` from a variable states nothing, and
  only a vector can witness those keys. The two are different strengths of
  evidence, so the page marks which one it holds.
- **the serializers** — an AST scan of ``atlas/contract.py`` for what each
  function returns. It names the shapes: which function produces which set of
  keys, which functions return more than one shape and so dispatch, and which
  key a serializer fills from a differently named attribute.
- **the data registry** — ``atlas.ENUMERATED_DATA``, read as an
  object rather than as source, because it is a mapping assembled from tuples
  and reading it any other way would re-derive what the package already states.
  It is the strongest reading about a value: ``Caveat.__post_init__`` and
  ``Unresolved.__post_init__`` refuse anything outside the tuple it binds to a
  ``(code, key)``, so the guarantee holds whether or not a vector exercises it.
  Where the vectors say what a value *was*, this one says what it *can be*, and
  the page states both — a pair the corpus has not reached keeps its guarantee
  and says the corpus has not reached it.

Joining the first two means matching a JSON key to the attribute it serializes.
That match is by name, with two mechanical exceptions: a key filled by a list
comprehension takes the name of the attribute the comprehension iterates, which
the serializer scan reads off ``contract.py`` (the alternatives group serializes
``options`` under ``alternatives``); and a single-key object is a discriminated
wrapper, which is inferred at generation time from a corpus shape whose one key
names no attribute of the answer type, so the walk starts inside it. A key that
still does not match is reported as stating nothing rather than guessed at.

Where the readings disagree in the direction that would publish a false claim —
the annotations say a field cannot be ``null`` and a vector produced ``null``
there — this script exits non-zero and names the field. The other direction
(annotated nullable, no vector produced ``null``) is not an error: it is a true
and useful sentence, and the field table states it.

Usage: ``python scripts/generate_contract_reference.py`` (then ``deno fmt``).
Stdlib only, like every script here; it imports ``atlas`` and reads ``vectors/``
and never touches a real machine.
"""

from __future__ import annotations

import ast
import collections.abc
import dataclasses
import functools
import json
import re
import sys
import types
import typing
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

import atlas

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "atlas"
CONTRACT_PATH = PACKAGE_DIR / "contract.py"
VECTOR_DIR = REPO_ROOT / "vectors" / "machines"
OUTPUT_PATH = REPO_ROOT / "docs" / "contract-reference.md"

LINE_WIDTH = 120
CODE_PREFIXES = ("CAVEAT_", "UNRESOLVED_", "HEALTH_ISSUE_")
CAVEAT_TYPES = ("Caveat", "Unresolved")
# How a caveat serializes, everywhere it appears. The walk recognises the shape
# rather than the type, because a caveat reached through an attribute that
# declares no members — installation health, behind a method — is still one.
CAVEAT_SHAPE = frozenset({"code", "data"})
# The one expected block that is not a question: every vector carries it, and it
# is what ``detect()`` must find rather than something a caller asks for.
DETECTION_BLOCK = "installations"
EMPTY_ARRAY = "empty array"
# How many distinct values a caveat data key may show before the page states the
# count instead of the list. A reader branching on a key needs the values; a
# reader looking at forty paths needs to be told there are forty.
VALUE_LIST_LIMIT = 8
VALUE_LENGTH_LIMIT = 60
# What the line filler treats as one word: a whole markdown link, or a run of
# non-space. The formatter breaks a line at neither's inside.
WORD = re.compile(r"\[[^\]]*\]\([^)]*\)|\S+")


# --------------------------------------------------------------------------
# Markdown, in the shape `deno fmt` leaves alone
# --------------------------------------------------------------------------


def wrap(text: str) -> list[str]:
    """*text* greedily filled to the formatter's line width.

    A markdown link is one word: the formatter never breaks inside one, so a
    generator that did would produce a page it then reformats.
    """
    lines: list[str] = []
    current = ""
    for word in (match.group() for match in WORD.finditer(text)):
        candidate = f"{current} {word}" if current else word
        if current and len(candidate) > LINE_WIDTH:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def paragraph(text: str) -> list[str]:
    """*text* filled to the line width, with the blank line that closes a block."""
    return [*wrap(text), ""]


def cell(text: str) -> str:
    """One table cell: a pipe would split the row, so it is escaped."""
    return text.replace("|", "\\|")


def table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """A pipe table padded the way the formatter pads one, so a reformat is a no-op."""
    widths = [
        max([len(headers[i]), *(len(row[i]) for row in rows)]) for i in range(len(headers))
    ]
    out = ["| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |"]
    out.append("| " + " | ".join("-" * width for width in widths) + " |")
    for row in rows:
        out.append("| " + " | ".join(row[i].ljust(widths[i]) for i in range(len(headers))) + " |")
    return out


def anchor(heading: str) -> str:
    """The in-page link target a markdown heading gets."""
    kept = [c for c in heading.lower() if c.isalnum() or c in " -_"]
    return "#" + "".join(kept).replace(" ", "-")


def backticked(values: Sequence[str]) -> str:
    return ", ".join(f"`{value}`" for value in values)


def counted(count: int, noun: str) -> str:
    """*count* with its noun — a page that says "1 site(s)" is one nobody read."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


# --------------------------------------------------------------------------
# Small AST helpers, shared by every scan
# --------------------------------------------------------------------------


@functools.cache
def package_modules() -> tuple[tuple[Path, ast.Module], ...]:
    """Every module under ``atlas/``, parsed once and shared by the scans below."""
    return tuple(
        (path, ast.parse(path.read_text(encoding="utf-8"))) for path in sorted(PACKAGE_DIR.rglob("*.py"))
    )


def _assignment(node: ast.stmt) -> tuple[str | None, ast.expr | None]:
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id, node.value
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id, node.value
    return None, None


def _called_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _string_keys(node: ast.Dict) -> set[str]:
    return {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "value"`` strings, aliases of one another included."""
    values: dict[str, str] = {}
    for node in tree.body:
        target, value = _assignment(node)
        if target is None or value is None:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            values[target] = value.value
        elif isinstance(value, ast.Name) and value.id in values:
            values[target] = values[value.id]
    return values


def module_string_tuples(tree: ast.Module) -> dict[str, tuple[str, ...]]:
    """Module-level ``NAME = ("a", "b")`` tuples of plain strings."""
    values = module_string_constants(tree)
    tuples: dict[str, tuple[str, ...]] = {}
    for node in tree.body:
        target, value = _assignment(node)
        if target is None or not isinstance(value, ast.Tuple):
            continue
        resolved: list[str] = []
        for element in value.elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                resolved.append(element.value)
            elif isinstance(element, ast.Name) and element.id in values:
                resolved.append(values[element.id])
            else:
                resolved = []
                break
        if resolved:
            tuples[target] = tuple(resolved)
    return tuples


# --------------------------------------------------------------------------
# Reading 1 — the annotations
# --------------------------------------------------------------------------

FIELD = "field"
PROPERTY = "property"
METHOD = "method"
SEQUENCE_ORIGINS = (tuple, list, set, frozenset, collections.abc.Sequence, collections.abc.Iterable)


@dataclasses.dataclass(frozen=True)
class Vocabulary:
    """One closed value list, and the name the source holds it under."""

    name: str
    values: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class FieldRef:
    """One attribute an answer type declares, and what it admits."""

    owner: str
    name: str
    kind: str
    annotation: str
    admits: object
    nullable: bool | None
    vocabularies: tuple[Vocabulary, ...]
    descends_into: tuple[type[Any], ...]


def _is_none(annotation: object) -> bool:
    return annotation is type(None)


def _union_args(annotation: object) -> tuple[object, ...]:
    origin = typing.get_origin(annotation)
    if origin is types.UnionType or origin is typing.Union:
        return typing.get_args(annotation)
    return ()


def render_annotation(annotation: object) -> str:
    """An annotation as a short string — module paths dropped, unions spelled out."""
    if _is_none(annotation):
        return "None"
    origin = typing.get_origin(annotation)
    if origin is None:
        return getattr(annotation, "__name__", str(annotation))
    args = typing.get_args(annotation)
    if origin is types.UnionType or origin is typing.Union:
        return " | ".join(render_annotation(a) for a in args)
    if origin is typing.Literal:
        return "Literal[" + ", ".join(repr(a) for a in args) + "]"
    name = getattr(origin, "__name__", str(origin))
    inner = ", ".join("..." if a is Ellipsis else render_annotation(a) for a in args)
    return f"{name}[{inner}]" if inner else name


def literal_values(annotation: object) -> tuple[str, ...]:
    """The strings a ``Literal`` annotation admits, through an optional union."""
    if typing.get_origin(annotation) is typing.Literal:
        return tuple(a for a in typing.get_args(annotation) if isinstance(a, str))
    for arg in _union_args(annotation):
        found = literal_values(arg)
        if found:
            return found
    return ()


def is_structured(candidate: object) -> typing.TypeGuard[type[Any]]:
    """Whether a type is one of atlas's own answer types, so a walk may descend."""
    return isinstance(candidate, type) and candidate.__module__.startswith("atlas")


def types_below(annotation: object) -> tuple[type[Any], ...]:
    """The answer types a field's value can carry, through unions and sequences.

    A mapping is opaque on purpose: its keys are data rather than declared
    attributes, so the walk stops there and the caveat tables carry them instead.
    """
    origin = typing.get_origin(annotation)
    if origin is None:
        return (annotation,) if is_structured(annotation) else ()
    if origin is types.UnionType or origin is typing.Union:
        return tuple(t for a in typing.get_args(annotation) if not _is_none(a) for t in types_below(a))
    if origin in SEQUENCE_ORIGINS:
        return tuple(t for a in typing.get_args(annotation) if a is not Ellipsis for t in types_below(a))
    # A parameterised answer type (``InstallationAnswer[AnswerT]``) is still that
    # type; what fills its variable is the caller's and stays unresolved.
    return (origin,) if is_structured(origin) else ()


def sequence_element(annotation: object) -> object | None:
    """What one step into a sequence annotation admits, ``None`` if it is not one.

    The outer ``| None`` belongs to the container: ``tuple[str, ...] | None``
    says the *list* may be absent, and says nothing about a member of it, so
    both the sequence layer and that ``None`` come off. Where several sequences
    share a union, the element admits what any of them admits.
    """
    candidates = [a for a in _union_args(annotation) if not _is_none(a)] or [annotation]
    elements = [
        argument
        for candidate in candidates
        if typing.get_origin(candidate) in SEQUENCE_ORIGINS
        for argument in typing.get_args(candidate)
        if argument is not Ellipsis
    ]
    if not elements:
        return None
    unique = list(dict.fromkeys(elements))
    return unique[0] if len(unique) == 1 else typing.Union[tuple(unique)]


def post_init_vocabularies() -> dict[str, dict[str, Vocabulary]]:
    """``{class: {field: Vocabulary}}`` for the tuple a ``__post_init__`` holds an attribute to.

    A dataclass that validates a field against a module-level tuple has stated
    that field's closed vocabulary in code, which is the same claim a ``Literal``
    makes and the only one available where the annotation is a bare ``str``.

    Only ``__post_init__`` is read. A membership test elsewhere in the class is
    a decision some method takes, not a statement about what the attribute
    admits, and the page says ``__post_init__`` — so the scan is what the page
    says rather than something wider that happens to agree today.
    """
    found: dict[str, dict[str, Vocabulary]] = {}
    for _, tree in package_modules():
        for owner, checks in module_post_init_checks(tree).items():
            found.setdefault(owner, {}).update(checks)
    return found


def _not_in_comparisons(function: ast.FunctionDef) -> Iterator[tuple[ast.expr, str]]:
    """Every ``<left> not in NAME`` comparison in *function*, with the name."""
    for compare in ast.walk(function):
        if not isinstance(compare, ast.Compare) or len(compare.ops) != 1:
            continue
        if not isinstance(compare.ops[0], ast.NotIn):
            continue
        right = compare.comparators[0]
        if isinstance(right, ast.Name):
            yield compare.left, right.id


def _self_checks(function: ast.FunctionDef) -> dict[str, set[str]]:
    """``{attribute: tuples ``self.<attr> not in NAME`` names}``.

    Only ``self.<attr>`` counts. A bare name here is a local or a loop
    variable — ``for option in self.options`` — and reading one as an attribute
    would put a field on the page that the type does not declare.
    """
    found: dict[str, set[str]] = {}
    for left, named in _not_in_comparisons(function):
        if isinstance(left, ast.Attribute) and isinstance(left.value, ast.Name):
            if left.value.id == "self":
                found.setdefault(left.attr, set()).add(named)
    return found


def _parameter_checks(function: ast.FunctionDef) -> dict[str, set[str]]:
    """``{parameter: tuples ``<parameter> not in NAME`` names}``, rebinding excluded.

    A parameter the body assigns to is no longer what the caller passed, so a
    check on it states nothing about the attribute that was handed over.
    """
    rebound = {
        node.id
        for node in ast.walk(function)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    parameters = {a.arg for a in (*function.args.posonlyargs, *function.args.args)} - rebound
    found: dict[str, set[str]] = {}
    for left, named in _not_in_comparisons(function):
        if isinstance(left, ast.Name) and left.id in parameters:
            found.setdefault(left.id, set()).add(named)
    return found


def module_post_init_checks(tree: ast.Module) -> dict[str, dict[str, Vocabulary]]:
    """One module's ``self.x not in NAME`` checks, ``__post_init__`` only.

    A ``__post_init__`` that hands its attributes to a module-level helper —
    ``_refuse_bad_kind("FirmwareIdentity", self.kind, ...)`` — states the same
    thing one call away, so the helper's own checks are read and bound back
    through the argument the attribute was passed as. One hop, and only into a
    function of the same module: further than that the scan would be guessing
    which call decides what.

    An attribute checked against two different tuples is checked per case, and
    which one holds is not something this scan can decide, so it states
    neither. One tuple, whether the check stands at the top of the body or
    inside the branch that reaches it, is the whole of what the attribute
    admits besides ``None``, which the nullability column states separately.
    """
    tuples = module_string_tuples(tree)
    helpers = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    found: dict[str, dict[str, Vocabulary]] = {}
    for owner, member in _post_init_methods(tree):
        checks = _self_checks(member)
        for attribute, named in _handed_to_a_helper(member, helpers).items():
            checks.setdefault(attribute, set()).update(named)
        stated = _one_tuple_vocabularies(checks, tuples)
        if stated:
            found.setdefault(owner, {}).update(stated)
    return found


def _post_init_methods(tree: ast.Module) -> Iterator[tuple[str, ast.FunctionDef]]:
    """Every ``__post_init__`` in *tree*, with the name of the class holding it."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for member in node.body:
            if isinstance(member, ast.FunctionDef) and member.name == "__post_init__":
                yield node.name, member


def _handed_to_a_helper(member: ast.FunctionDef, helpers: Mapping[str, ast.FunctionDef]) -> dict[str, set[str]]:
    """``{attribute: tuples}`` a ``__post_init__`` states through a helper it calls."""
    found: dict[str, set[str]] = {}
    for call in ast.walk(member):
        if not isinstance(call, ast.Call):
            continue
        helper = helpers.get(_called_name(call.func) or "")
        if helper is None:
            continue
        for attribute, named in _checked_arguments(call, helper):
            found.setdefault(attribute, set()).update(named)
    return found


def _checked_arguments(call: ast.Call, helper: ast.FunctionDef) -> Iterator[tuple[str, set[str]]]:
    """``(attribute, tuples)`` for each ``self.<attr>`` *call* hands to a checked parameter."""
    checks = _parameter_checks(helper)
    parameters = [argument.arg for argument in (*helper.args.posonlyargs, *helper.args.args)]
    passed = [(parameters[i], value) for i, value in enumerate(call.args) if i < len(parameters)]
    passed += [(keyword.arg, keyword.value) for keyword in call.keywords if keyword.arg]
    for parameter, value in passed:
        named = checks.get(parameter)
        if not named:
            continue
        if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name) and value.value.id == "self":
            yield value.attr, named


def _one_tuple_vocabularies(
    checks: Mapping[str, set[str]], tuples: Mapping[str, tuple[str, ...]]
) -> dict[str, Vocabulary]:
    """The attributes whose checks name exactly one tuple the module declares."""
    found: dict[str, Vocabulary] = {}
    for attribute, names in checks.items():
        if len(names) != 1:
            continue
        name = next(iter(names))
        if name in tuples:
            found[attribute] = Vocabulary(name, tuples[name])
    return found


@dataclasses.dataclass(frozen=True)
class AttributeProse:
    """How much per-attribute prose the answer types carry, and in what form."""

    attributes: int
    docstrings: int
    commented: int


def attribute_prose(classes: Iterable[type[Any]]) -> AttributeProse:
    """The annotated attributes of *classes*, and the prose written about them.

    A docstring is the one form of per-attribute prose a generator can quote:
    it is an expression with an end. A preceding comment is prose too, and
    quoting it would mean deciding where its sentence stops, so it is counted
    instead. Counted rather than assumed, because the page's silence about what
    a field means is only honest if it states what the source actually holds.
    """
    by_module: dict[str, set[str]] = {}
    for cls in classes:
        by_module.setdefault(cls.__module__, set()).add(cls.__name__)
    counted: list[AttributeProse] = []
    for path, tree in package_modules():
        names = by_module.get(f"atlas.{path.stem}", set())
        if not names:
            continue
        source = path.read_text(encoding="utf-8").splitlines()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in names:
                counted.append(_class_prose(node, source))
    return AttributeProse(
        sum(one.attributes for one in counted),
        sum(one.docstrings for one in counted),
        sum(one.commented for one in counted),
    )


def _class_prose(node: ast.ClassDef, source: Sequence[str]) -> AttributeProse:
    """One class's annotated attributes, and the prose written about them."""
    attributes = docstrings = commented = 0
    for index, statement in enumerate(node.body):
        if not isinstance(statement, ast.AnnAssign):
            continue
        attributes += 1
        following = node.body[index + 1] if index + 1 < len(node.body) else None
        if isinstance(following, ast.Expr) and isinstance(following.value, ast.Constant):
            docstrings += isinstance(following.value.value, str)
        above = source[statement.lineno - 2].strip() if statement.lineno >= 2 else ""
        commented += above.startswith("#")
    return AttributeProse(attributes, docstrings, commented)


def declared_vocabularies() -> dict[str, tuple[str, ...]]:
    """Every module-level string tuple in the package, by name."""
    found: dict[str, tuple[str, ...]] = {}
    for _, tree in package_modules():
        found.update(module_string_tuples(tree))
    return found


def registered_enumerations() -> dict[tuple[str, str], str]:
    """``(code, key)`` → the name of the tuple its value is refused against.

    The fifth reading, and the strongest one about a data value:
    ``atlas.ENUMERATED_DATA`` is what ``Caveat.__post_init__`` and
    ``Unresolved.__post_init__`` check, so a pair listed there cannot carry a
    value outside its tuple — no corpus coverage is needed to say so. The
    vectors state what a value *was*; this one states what it *can be*.

    The name is recovered by CONTENTS, which is why :func:`vocabulary_names`
    states its preference rather than taking whatever it saw last.
    """
    return {
        (code, key): (vocabulary_names().get(tuple(vocabulary)) or ["an unnamed tuple"])[0]
        for (code, key), vocabulary in atlas.ENUMERATED_DATA.items()
    }


def vocabulary_names() -> dict[tuple[str, ...], list[str]]:
    """Contents → every name this package gives that exact tuple, preference applied.

    Two sources: the module-level literal tuples the AST reading collects, and
    the package's exported names — the second is not redundant, because a tuple
    assembled from others (``EMULATOR_CONFIG_UNREADABLE_REASONS`` is built by
    splat) is invisible to an AST reading of literals.

    An **exported** name wins outright where one exists: the merge below keys
    both readings by contents and lets the exported list overwrite the private
    one, so the private names are not candidates at all — the merge is the
    preference, and nothing else is needed. Contents alone do not identify a
    name here:
    ``_BASES`` and ``_FILE_BASES`` are both ``('config', 'data')``, and
    ``FIRMWARE_NEEDS`` and ``_FILE_NEEDS`` are both ``('required', 'optional')``
    — so a reading that simply matched members would cite whichever it happened
    to see last. The preference resolves those two, and where it does not
    resolve, the list this returns has more than one entry and
    :func:`ambiguous_vocabulary_names` stops the generation rather than letting
    a cell name the wrong tuple.
    """
    exported: dict[tuple[str, ...], list[str]] = {}
    private: dict[tuple[str, ...], list[str]] = {}
    for name in sorted(atlas.__all__):
        value = getattr(atlas, name)
        if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
            exported.setdefault(tuple(value), []).append(name)
    for name, members in sorted(declared_vocabularies().items()):
        private.setdefault(tuple(members), []).append(name)
    return {**private, **exported}


def ambiguous_vocabulary_names(registered: Mapping[tuple[str, str], str]) -> list[str]:
    """Registry tuples whose contents more than one name still claims.

    Only the registry's own tuples are held to this: the page cites a name for
    those and for nothing else, so a collision elsewhere in the package is not
    this page's problem to refuse over.
    """
    names = vocabulary_names()
    ambiguous: list[str] = []
    for code, key in sorted(registered):
        candidates = names.get(tuple(atlas.ENUMERATED_DATA[(code, key)]), [])
        if len(candidates) > 1:
            ambiguous.append(
                f"{code}.{key}: {sorted(candidates)} all hold the same members, so the cell cannot name one"
            )
    return ambiguous


@functools.cache
def contract_module() -> ast.Module:
    """``atlas/contract.py``, parsed once — the whole input of the serializer reading."""
    return next(tree for path, tree in package_modules() if path == CONTRACT_PATH)


def contract_key_renames() -> dict[str, set[str]]:
    """JSON keys a serializer fills from a differently named attribute.

    Only one form is read, because only one is unambiguous: a key whose value is
    a list comprehension serializes the attribute that comprehension iterates. A
    key built any other way keeps its own name and is joined by it.
    """
    renames: dict[str, set[str]] = {}
    for node in ast.walk(contract_module()):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            rename = _comprehension_rename(key, value)
            if rename is not None:
                renames.setdefault(rename[0], set()).add(rename[1])
    return renames


def _comprehension_rename(key: ast.expr | None, value: ast.expr) -> tuple[str, str] | None:
    """``(key, attribute)`` where a comprehension fills *key* from a differently named attribute."""
    if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
        return None
    if not isinstance(value, ast.ListComp) or not value.generators:
        return None
    iterated = value.generators[0].iter
    if isinstance(iterated, ast.Attribute) and iterated.attr != key.value:
        return key.value, iterated.attr
    return None


class Annotations:
    """The answer types, resolved once: attributes, nullability, vocabularies."""

    def __init__(self) -> None:
        self._hints: dict[type[Any], dict[str, object]] = {}
        self._names: dict[type[Any], frozenset[str]] = {}
        self._post_init = post_init_vocabularies()
        self._renames = contract_key_renames()
        self.seen_types: set[type[Any]] = set()

    def hints(self, cls: type[Any]) -> dict[str, object]:
        cached = self._hints.get(cls)
        if cached is None:
            try:
                cached = dict(typing.get_type_hints(cls))
            except Exception:  # pragma: no cover - a type whose hints cannot be resolved states nothing
                cached = {}
            self._hints[cls] = cached
        return cached

    def attribute_names(self, cls: type[Any]) -> frozenset[str]:
        cached = self._names.get(cls)
        if cached is None:
            names = {f.name for f in dataclasses.fields(cls)} if dataclasses.is_dataclass(cls) else set()
            for base in cls.__mro__:
                names |= {n for n, v in vars(base).items() if isinstance(v, property)}
            cached = frozenset(names)
            self._names[cls] = cached
        return cached

    def _declared(self, cls: type[Any], name: str) -> object | None:
        for base in cls.__mro__:
            found = vars(base).get(name)
            if found is not None:
                return found
        return None

    def attribute(self, cls: type[Any], name: str) -> FieldRef | None:
        if not is_structured(cls):
            return None
        self.seen_types.add(cls)
        declared = self._declared(cls, name)
        is_field = dataclasses.is_dataclass(cls) and name in {f.name for f in dataclasses.fields(cls)}
        if is_field:
            annotation = self.hints(cls).get(name)
            kind = FIELD
        elif isinstance(declared, property) and declared.fget is not None:
            try:
                annotation = typing.get_type_hints(declared.fget).get("return")
            except Exception:  # pragma: no cover - an unresolvable property states nothing
                annotation = None
            kind = PROPERTY
        elif callable(declared):
            return FieldRef(
                owner=cls.__name__,
                name=name,
                kind=METHOD,
                annotation="method",
                admits=None,
                nullable=None,
                vocabularies=(),
                descends_into=(),
            )
        else:
            return None
        return FieldRef(
            owner=cls.__name__,
            name=name,
            kind=kind,
            annotation=render_annotation(annotation),
            admits=annotation,
            nullable=any(_is_none(a) for a in _union_args(annotation)),
            vocabularies=self._vocabularies(cls, name, annotation),
            descends_into=types_below(annotation),
        )

    def _vocabularies(self, cls: type[Any], name: str, annotation: object) -> tuple[Vocabulary, ...]:
        """Every closed value list declared for one attribute, under every name.

        A field can state its vocabulary twice — a ``Literal`` annotation and a
        ``__post_init__`` check against a module-level tuple — and both names
        are recorded, because a consumer branching on the values wants whichever
        one is exported to it.
        """
        found: list[Vocabulary] = []
        values = literal_values(annotation)
        if values:
            found.append(Vocabulary(f"Literal[{cls.__name__}.{name}]", values))
        checked = self._post_init.get(cls.__name__, {}).get(name)
        if checked is not None:
            found.append(checked)
        return tuple(found)

    def element_of(self, reference: FieldRef | None) -> FieldRef | None:
        """*reference* one step into its sequence: what a member of it admits.

        A container annotated ``tuple[str, ...] | None`` says the *list* may be
        absent; reusing its reference for the element would publish the
        container's nullability as the element's, so a null member could never
        contradict the annotations and a legitimately nullable member would be
        read as a failure. The vocabulary a ``__post_init__`` checks is the
        container attribute's own and does not descend. Where the annotation is
        not a sequence — a method, a generic the caller fills — there is nothing
        to strip and the container's reference stands.
        """
        if reference is None:
            return None
        element = sequence_element(reference.admits)
        if element is None:
            return reference
        values = literal_values(element)
        named = f"Literal[{reference.owner}.{reference.name}]"
        return FieldRef(
            owner=reference.owner,
            name=reference.name,
            kind=reference.kind,
            annotation=render_annotation(element),
            admits=element,
            nullable=any(_is_none(a) for a in _union_args(element)),
            vocabularies=(Vocabulary(named, values),) if values else (),
            descends_into=types_below(element),
        )

    def resolve(self, roots: Sequence[type[Any]], name: str) -> tuple[FieldRef | None, bool]:
        """The attribute *name* on any of *roots* — and whether the roots disagreed.

        A key that matches nothing is tried again under the attribute name the
        serializer's own list comprehension iterates; see
        :func:`contract_key_renames`.
        """
        found = [ref for cls in roots if (ref := self.attribute(cls, name)) is not None]
        if not found:
            for alias in sorted(self._renames.get(name, set())):
                found = [ref for cls in roots if (ref := self.attribute(cls, alias)) is not None]
                if found:
                    break
        if not found:
            return None, False
        first = found[0]
        ambiguous = any(f.nullable != first.nullable or f.annotation != first.annotation for f in found[1:])
        return first, ambiguous


# --------------------------------------------------------------------------
# Reading 2 — the construction sites
# --------------------------------------------------------------------------


@dataclasses.dataclass
class CodeSites:
    """What the source states about one code's ``data``."""

    literal_keys: set[str] = dataclasses.field(default_factory=set)
    literal_sites: int = 0
    dynamic_sites: int = 0


def caveat_construction_sites() -> tuple[dict[str, CodeSites], int]:
    """``{code: CodeSites}`` plus the number of sites whose code is not literal.

    A site that names its code through a variable — the firmware refusal helper
    takes the code as a parameter — states nothing this scan can attribute, so it
    is counted rather than guessed at.
    """
    exported = {name: value for name in atlas.__all__ if isinstance(value := getattr(atlas, name), str)}
    sites: dict[str, CodeSites] = {}
    unattributed = 0
    for _, tree in package_modules():
        local = module_string_constants(tree)
        for node in _caveat_calls(tree):
            code = _site_code(node, local, exported)
            if code is None:
                unattributed += 1
                continue
            entry = sites.setdefault(code, CodeSites())
            keys = _site_data_keys(node)
            if keys is None:
                entry.dynamic_sites += 1
            else:
                entry.literal_sites += 1
                entry.literal_keys.update(keys)
    return sites, unattributed


def _caveat_calls(tree: ast.Module) -> Iterator[ast.Call]:
    """Every ``Caveat(...)`` / ``Unresolved(...)`` call in *tree*."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _called_name(node.func) in CAVEAT_TYPES:
            yield node


def _argument(node: ast.Call, name: str, position: int) -> ast.expr | None:
    """The argument *name* of a construction site, given by keyword or by position."""
    keyword = next((kw.value for kw in node.keywords if kw.arg == name), None)
    if keyword is not None:
        return keyword
    return node.args[position] if len(node.args) > position else None


def _site_code(node: ast.Call, local: Mapping[str, str], exported: Mapping[str, str]) -> str | None:
    """The code a site names, where it names one with a constant this scan can resolve."""
    code_node = _argument(node, "code", 0)
    if isinstance(code_node, ast.Constant) and isinstance(code_node.value, str):
        return code_node.value
    if isinstance(code_node, ast.Name):
        return local.get(code_node.id) or exported.get(code_node.id)
    return None


def _site_data_keys(node: ast.Call) -> frozenset[str] | None:
    """The ``data`` keys a site spells out, ``None`` where the scan cannot read them.

    An argument that is absent is the constructor's empty default and states no
    keys. One built from a variable, or supplied by a starred call the scan
    cannot follow — ``Caveat(CODE, *_state(...))`` — states nothing at all, and
    reading it as "no data" would understate the code.
    """
    data_node = _argument(node, "data", 2)
    if data_node is None:
        unreadable = any(isinstance(argument, ast.Starred) for argument in node.args) or any(
            keyword.arg is None for keyword in node.keywords
        )
        return None if unreadable else frozenset()
    if not isinstance(data_node, ast.Dict):
        return None
    if all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in data_node.keys):
        return frozenset(_string_keys(data_node))
    return None


# --------------------------------------------------------------------------
# The serializers — which contract function produces which shape
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Shape:
    """One serialized answer shape: an object, or an array of them."""

    container: str
    keys: frozenset[str]

    def sort_key(self) -> tuple[str, str]:
        return self.container, ",".join(sorted(self.keys))


@dataclasses.dataclass(frozen=True)
class Serializer:
    """The contract function that produces one shape, and how it was matched."""

    name: str | None
    per_element: bool
    reached_through: tuple[str, ...]


def contract_shapes() -> dict[str, list[Shape]]:
    """``{function: the shapes its returns can take}`` for ``atlas/contract.py``.

    A function that returns several distinct shapes is a dispatcher — the family
    serializers that branch on a refusal — and the page names the single-shape
    function beside each shape instead.
    """
    reader = _ShapeReader(contract_module())
    return {name: reader.shapes_of(name, frozenset()) for name in reader.functions}


class _ShapeReader:
    """What each function in ``atlas/contract.py`` returns, resolved once per name."""

    def __init__(self, tree: ast.Module) -> None:
        self.functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        self.resolved: dict[str, list[Shape]] = {}

    def shapes_of(self, name: str, seen: frozenset[str]) -> list[Shape]:
        """The shapes the function *name* can return, ``seen`` breaking recursion."""
        if name in self.resolved:
            return self.resolved[name]
        node = self.functions.get(name)
        if node is None or name in seen:
            return []
        found: list[Shape] = []
        for statement in ast.walk(node):
            if isinstance(statement, ast.Return) and statement.value is not None:
                found.extend(self.expression_shapes(statement.value, seen | {name}))
        unique = list(dict.fromkeys(found))
        self.resolved[name] = unique
        return unique

    def expression_shapes(self, expression: ast.expr, seen: frozenset[str]) -> list[Shape]:
        """The shapes one returned expression can take."""
        if isinstance(expression, ast.Dict):
            return [Shape("object", frozenset(_string_keys(expression) | self._spread_keys(expression, seen)))]
        if isinstance(expression, ast.Call):
            called = _called_name(expression.func)
            return self.shapes_of(called, seen) if called else []
        if isinstance(expression, (ast.ListComp, ast.List)):
            return self._array_shapes(expression, seen)
        if isinstance(expression, ast.IfExp):
            return [
                *self.expression_shapes(expression.body, seen),
                *self.expression_shapes(expression.orelse, seen),
            ]
        return []

    def _spread_keys(self, expression: ast.Dict, seen: frozenset[str]) -> set[str]:
        """The keys a ``{**serializer(...)}`` spread adds to the object around it."""
        keys: set[str] = set()
        for key, value in zip(expression.keys, expression.values):
            if key is not None or not isinstance(value, ast.Call):
                continue
            called = _called_name(value.func)
            if called is None:
                continue
            for shape in self.shapes_of(called, seen):
                keys |= shape.keys
        return keys

    def _array_shapes(self, expression: ast.ListComp | ast.List, seen: frozenset[str]) -> list[Shape]:
        """A returned list, as the array of whatever its elements serialize to."""
        elements = [expression.elt] if isinstance(expression, ast.ListComp) else expression.elts
        return [
            Shape("array", shape.keys)
            for element in elements
            for shape in self.expression_shapes(element, seen)
            if shape.container == "object"
        ]


def serializer_for(shape: Shape, produced: Mapping[str, list[Shape]]) -> Serializer:
    """The function that produces *shape*, and the dispatchers that reach it.

    Several functions can produce one shape — a public serializer and the private
    helper it delegates to — so the public single-shape one is what the page
    names. An array the corpus states whose keys a function serializes one object
    at a time is that function applied per element: the list is the caller's, not
    the serializer's.
    """

    def matches(wanted: Shape) -> list[str]:
        exact = [name for name, shapes in produced.items() if shapes == [wanted]]
        public = [name for name in exact if not name.startswith("_")]
        return sorted(public or exact)

    reaching = tuple(sorted(name for name, shapes in produced.items() if len(shapes) > 1 and shape in shapes))
    chosen = matches(shape)
    if chosen:
        return Serializer(chosen[0], False, reaching)
    if shape.container == "array":
        per_element = matches(Shape("object", shape.keys))
        if per_element:
            return Serializer(per_element[0], True, reaching)
    return Serializer(None, False, reaching)


def serializer_root(
    serializer: Serializer, shape: Shape, annotations: Annotations
) -> tuple[type[Any], ...]:
    """The answer types a serializer's own parameter admits, narrowed to *shape*."""
    if serializer.name is None:
        return ()
    function = getattr(atlas.contract, serializer.name, None)
    if function is None:
        return ()
    try:
        hints = typing.get_type_hints(function)
    except Exception:  # pragma: no cover - an unresolvable signature states nothing
        return ()
    candidates: list[type[Any]] = []
    for parameter, annotation in hints.items():
        if parameter == "return":
            continue
        candidates.extend(types_below(annotation))
        break
    if len(candidates) <= 1:
        return tuple(candidates)
    narrowed = [cls for cls in candidates if shape.keys <= annotations.attribute_names(cls)]
    return tuple(narrowed or candidates)


# --------------------------------------------------------------------------
# Reading 3 — the vectors
# --------------------------------------------------------------------------


@dataclasses.dataclass
class PathObservation:
    """What the corpus showed at one path of one shape."""

    types: Counter[str] = dataclasses.field(default_factory=Counter)
    answers: int = 0
    nulls: int = 0


def json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def load_vectors() -> tuple[dict[str, list[Any]], dict[str, int], int]:
    """``{expected key: answers}``, the per-file vector counts, and the total."""
    answers: dict[str, list[Any]] = {}
    per_file: dict[str, int] = {}
    total = 0
    paths = sorted(VECTOR_DIR.glob("*.json"))
    if not paths:
        raise SystemExit(f"no vector files under {VECTOR_DIR}")
    for path in paths:
        raw = json.loads(path.read_text(encoding="utf-8"))
        vectors = raw["vectors"]
        per_file[path.name] = len(vectors)
        total += len(vectors)
        for vector in vectors:
            for key, expectation in vector["expected"].items():
                answers.setdefault(key, []).append(expectation)
    return answers, per_file, total


def shape_of(answer: object) -> Shape:
    if isinstance(answer, list):
        keys: set[str] = set()
        for element in answer:
            if isinstance(element, dict):
                keys |= set(element)
        return Shape("array", frozenset(keys))
    if isinstance(answer, dict):
        return Shape("object", frozenset(answer))
    return Shape(json_type(answer), frozenset())


class ShapeWalk:
    """One shape's answers, walked into paths with their observed types."""

    def __init__(self, roots: Sequence[type[Any]], annotations: Annotations, wrapper: str | None) -> None:
        self.roots = roots
        self.annotations = annotations
        self.wrapper = wrapper
        self.paths: dict[str, PathObservation] = {}
        self.fields: dict[str, FieldRef | None] = {}
        self.ambiguous: set[str] = set()
        self.stopped: dict[str, Counter[Shape]] = {}
        self.answers = 0

    def add(self, answer: object) -> None:
        self.answers += 1
        seen: set[str] = set()
        if isinstance(answer, list):
            for element in answer:
                self._record("[]", element, seen)
                if isinstance(element, dict):
                    self._descend(element, "[]", self.roots, seen)
        elif isinstance(answer, dict):
            self._descend(answer, "", self.roots, seen)

    def _record(self, path: str, value: object, seen: set[str]) -> None:
        observation = self.paths.setdefault(path, PathObservation())
        observation.types[json_type(value)] += 1
        if value is None:
            observation.nulls += 1
        if path not in seen:
            seen.add(path)
            observation.answers += 1

    def _descend(self, value: Mapping[str, Any], path: str, roots: Sequence[type[Any]], seen: set[str]) -> None:
        if frozenset(value) == CAVEAT_SHAPE:
            # The one serialization of a caveat. Its ``data`` keys are data, not
            # declared attributes, so the caveat tables carry them instead.
            for key in ("code", "data"):
                self._leaf(f"{path}.{key}" if path else key, value[key], key, roots, seen)
            return
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if path == "" and key == self.wrapper:
                self.fields.setdefault(child_path, None)
                self._record(child_path, child, seen)
                if isinstance(child, dict):
                    self._descend(child, child_path, roots, seen)
                continue
            reference = self._leaf(child_path, child, key, roots, seen)
            self._continue(child, child_path, reference, seen)

    def _leaf(
        self, path: str, value: object, key: str, roots: Sequence[type[Any]], seen: set[str]
    ) -> FieldRef | None:
        reference, ambiguous = self.annotations.resolve(roots, key)
        self.fields.setdefault(path, reference)
        if ambiguous:
            self.ambiguous.add(path)
        self._record(path, value, seen)
        return reference

    def _continue(self, value: object, path: str, reference: FieldRef | None, seen: set[str]) -> None:
        if isinstance(value, list):
            element_path = f"{path}[]"
            element_reference = self.annotations.element_of(reference)
            for element in value:
                self.fields.setdefault(element_path, element_reference)
                self._record(element_path, element, seen)
                self._continue(element, element_path, element_reference, seen)
        elif isinstance(value, dict):
            self._enter(value, path, reference, seen)

    def _enter(self, value: Mapping[str, Any], path: str, reference: FieldRef | None, seen: set[str]) -> None:
        below = reference.descends_into if reference is not None else ()
        if reference is not None and not below and frozenset(value) != CAVEAT_SHAPE:
            # An attribute whose type declares no members of its own — a mapping,
            # or a generic the serializer's caller fills in. What it carried is
            # recorded as a shape rather than walked as if it were fields.
            #
            # A caveat is the exception: `{code, data}` is its serialization
            # wherever it appears, so the page states those two rows even where
            # the attribute it hangs off declares nothing — installation health
            # is a tuple of caveats behind a method, and stopping here would
            # leave the page silent about the shape of a health finding.
            self.stopped.setdefault(path, Counter())[shape_of(value)] += 1
            return
        self._descend(value, path, below or self.roots, seen)


# --------------------------------------------------------------------------
# Caveats as the corpus shows them
# --------------------------------------------------------------------------


@dataclasses.dataclass
class WitnessedCode:
    """One caveat code as the vectors show it."""

    questions: Counter[str] = dataclasses.field(default_factory=Counter)
    keys: dict[str, list[Any]] = dataclasses.field(default_factory=dict)
    keys_per_question: dict[str, set[str]] = dataclasses.field(default_factory=dict)


def collect_caveats(answers: Mapping[str, Sequence[Any]]) -> dict[str, WitnessedCode]:
    """Every ``{code, data}`` object the corpus carries, by code."""
    found: dict[str, WitnessedCode] = {}
    for question, question_answers in answers.items():
        for answer in question_answers:
            _visit_caveats(answer, question, found)
    return found


def _visit_caveats(node: object, question: str, found: dict[str, WitnessedCode]) -> None:
    """Record every ``{code, data}`` object under *node* against *question*."""
    if isinstance(node, list):
        for child in node:
            _visit_caveats(child, question, found)
        return
    if not isinstance(node, dict):
        return
    code = node.get("code")
    data = node.get("data")
    if set(node) == {"code", "data"} and isinstance(code, str) and isinstance(data, dict):
        _record_caveat(found.setdefault(code, WitnessedCode()), question, data)
        return
    for child in node.values():
        _visit_caveats(child, question, found)


def _record_caveat(entry: WitnessedCode, question: str, data: Mapping[str, Any]) -> None:
    """Add one caveat's data keys to what the corpus shows for its code."""
    entry.questions[question] += 1
    per_question = entry.keys_per_question.setdefault(question, set())
    for key, value in data.items():
        entry.keys.setdefault(key, []).append(value)
        per_question.add(key)


def describe_values(values: Sequence[Any], vocabularies: Mapping[str, tuple[str, ...]]) -> str:
    """What a data key's observed values are — a path, a closed set, or a list.

    A closed set is claimed only where a declared tuple holds exactly the
    observed values. Containing a few of a tuple's members is not evidence that
    the tuple is the key's vocabulary, so those values are listed instead. A
    value that is an array or an object is described by its shape: a tuple is
    the key's own contents, never a vocabulary it draws from.
    """
    kinds = {json_type(v) for v in values}
    if kinds != {"string"}:
        return f"{', '.join(sorted(kinds))} ({len(values)} observed)"
    distinct = sorted({str(v) for v in values})
    equal = sorted(name for name, members in vocabularies.items() if sorted(set(members)) == distinct)
    if equal:
        return f"closed set {backticked(equal)}: {backticked(distinct)}"
    if all(value.startswith("/") for value in distinct):
        return f"absolute path ({len(distinct)} distinct)"
    if len(distinct) <= VALUE_LIST_LIMIT and all(len(v) <= VALUE_LENGTH_LIMIT for v in distinct):
        return backticked(distinct)
    return f"{len(distinct)} distinct values"


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def shape_title(shape: Shape, serializer: Serializer) -> str:
    if shape.container == "array" and not shape.keys:
        return EMPTY_ARRAY
    if serializer.name is not None:
        return serializer.name if shape.container == "object" else f"{serializer.name} (array)"
    return f"unattributed {shape.container} of {', '.join(sorted(shape.keys)) or 'no keys'}"


def source_keys_cell(
    entry: CodeSites | None, witnessed: frozenset[str] | None, variable_code_sites: int
) -> str:
    """What the sites this scan can attribute spell out about one code's ``data``.

    A code no site spells that a vector nonetheless carried was built somewhere,
    and this scan reads every ``Caveat(...)`` and ``Unresolved(...)`` call in the
    package — so that somewhere is one of the sites naming its code through a
    variable, and the cell says exactly that. Absence is stated only where the
    corpus has no such code either, which is the only case where nothing at all
    is known.

    Where the corpus carried keys none of the attributable sites spells, the
    cell names them. A reader deciding whether to branch on such a key would
    otherwise read this column as the whole list and conclude the key is not in
    the source at all.
    """
    if entry is None:
        if witnessed is not None and variable_code_sites:
            return "code named through a variable"
        return "no construction site names this code"
    if not entry.literal_keys:
        if entry.dynamic_sites:
            return f"built dynamically at {counted(entry.dynamic_sites, 'site')}"
        return "no data"
    qualifiers: list[str] = []
    if entry.dynamic_sites:
        qualifiers.append(f"+{counted(entry.dynamic_sites, 'site')} building data dynamically")
    beyond = sorted((witnessed or frozenset()) - entry.literal_keys)
    if beyond:
        qualifiers.append(f"+{backticked(beyond)} spelled at no attributable site")
    source = backticked(sorted(entry.literal_keys))
    return f"{source} ({'; '.join(qualifiers)})" if qualifiers else source


def nullable_cell(reference: FieldRef | None, ambiguous: bool) -> str:
    if reference is None or reference.nullable is None:
        return "not stated"
    if ambiguous:
        return "yes" if reference.nullable else "no (types differ)"
    return "yes" if reference.nullable else "no"


def declared_cell(reference: FieldRef | None) -> str:
    if reference is None:
        return "—"
    if reference.kind == METHOD:
        return "derived (method)"
    suffix = " (property)" if reference.kind == PROPERTY else ""
    return f"`{reference.annotation}`{suffix}"


def field_rows(walk: ShapeWalk) -> list[list[str]]:
    rows: list[list[str]] = []
    for path in sorted(walk.paths):
        observation = walk.paths[path]
        reference = walk.fields.get(path)
        rows.append(
            [
                cell(f"`{path}`"),
                cell(" or ".join(sorted(observation.types))),
                cell(declared_cell(reference)),
                cell(nullable_cell(reference, path in walk.ambiguous)),
                "yes" if observation.nulls else "no",
                f"{observation.answers}/{walk.answers}",
                cell(backticked([v.name for v in reference.vocabularies]) if reference else ""),
            ]
        )
    return rows


def contradictions(walks: Mapping[str, ShapeWalk]) -> list[str]:
    """Attributes the annotations forbid to be ``null`` and a vector made ``null``."""
    found: list[str] = []
    for title, walk in sorted(walks.items()):
        for path, observation in sorted(walk.paths.items()):
            reference = walk.fields.get(path)
            if reference is None or reference.nullable is not False or not observation.nulls:
                continue
            found.append(
                f"{title}: '{path}' is annotated '{reference.annotation}' on "
                f"{reference.owner}, which admits no None, and {observation.nulls} "
                "serialized value(s) in the corpus are null"
            )
    return found


def shape_disagreements(witnessed: Mapping[str, "WitnessedCode"]) -> list[str]:
    """``(code, key)`` pairs the corpus shows under two JSON types.

    A client switches on the code and reads the key; if one pair is a string in
    one answer and an array in another, the page cannot state a type and the
    contract has none. Caught here rather than described, because a page that
    printed "string, array" would be documenting the defect as a feature.
    """
    found: list[str] = []
    for code in sorted(witnessed):
        for key in sorted(witnessed[code].keys):
            kinds = sorted({json_type(value) for value in witnessed[code].keys[key]})
            if len(kinds) > 1:
                found.append(f"{code}.{key}: the corpus carries {' and '.join(kinds)}")
    return found


def registry_disagreements(
    witnessed: Mapping[str, "WitnessedCode"], registered: Mapping[tuple[str, str], str]
) -> list[str]:
    """Values the corpus shows that the construction-time registry would refuse.

    The two readings must agree, and only one direction is a contradiction: a
    registered pair the corpus has not reached is a coverage gap the page
    states, while a value outside the tuple means the page would publish a
    closed set that is not closed. Nothing should reach here — the constructor
    raises first — so this fires only if the registry and the corpus were built
    from different revisions.
    """
    found: list[str] = []
    for (code, key), name in sorted(registered.items()):
        allowed = set(atlas.ENUMERATED_DATA[(code, key)])
        for value in witnessed.get(code, WitnessedCode()).keys.get(key, []):
            if isinstance(value, str) and value not in allowed:
                found.append(f"{code}.{key}: the corpus carries {value!r}, which `{name}` does not hold")
    return found


@dataclasses.dataclass(frozen=True)
class Reference:
    """What the five readings established, gathered once for the sections to render.

    Assembled by :func:`read_everything` and read-only from there on: a section
    states what is already known rather than reading the repository again, so
    two sections cannot disagree about the same fact.
    """

    annotations: Annotations
    prose: AttributeProse
    produced: dict[str, list[Shape]]
    vocabularies: dict[str, tuple[str, ...]]
    enumerations: dict[tuple[str, str], str]
    sites: dict[str, CodeSites]
    unattributed_sites: int
    answers: dict[str, list[Any]]
    per_file: dict[str, int]
    total_vectors: int
    exported_codes: list[str]
    exported_names: list[str]
    witnessed: dict[str, WitnessedCode]
    questions: list[str]
    question_shapes: dict[str, Counter[Shape]]
    serializers: dict[Shape, Serializer]
    titles: dict[Shape, str]
    walks: dict[str, ShapeWalk]


def read_everything() -> Reference:
    """Run the five readings and walk every shape the corpus states."""
    annotations = Annotations()
    produced = contract_shapes()
    sites, unattributed_sites = caveat_construction_sites()
    answers, per_file, total_vectors = load_vectors()

    shape_answers, question_shapes = _shapes_by_question(answers)
    serializers = {shape: serializer_for(shape, produced) for shape in shape_answers}
    titles = {shape: shape_title(shape, serializers[shape]) for shape in shape_answers}
    walks = _walk_every_shape(shape_answers, serializers, titles, annotations)
    exported_codes = {
        value
        for name in atlas.__all__
        if name.startswith(CODE_PREFIXES) and isinstance(value := getattr(atlas, name), str)
    }

    return Reference(
        annotations=annotations,
        prose=attribute_prose(annotations.seen_types),
        produced=produced,
        vocabularies=declared_vocabularies(),
        enumerations=registered_enumerations(),
        sites=sites,
        unattributed_sites=unattributed_sites,
        answers=answers,
        per_file=per_file,
        total_vectors=total_vectors,
        exported_codes=sorted(exported_codes),
        exported_names=[name for name in atlas.__all__ if name.startswith(CODE_PREFIXES)],
        witnessed=collect_caveats(answers),
        questions=sorted(key for key in answers if key != DETECTION_BLOCK),
        question_shapes=question_shapes,
        serializers=serializers,
        titles=titles,
        walks=walks,
    )


def _shapes_by_question(
    answers: Mapping[str, Sequence[Any]],
) -> tuple[dict[Shape, list[Any]], dict[str, Counter[Shape]]]:
    """Every answer filed under the shape it takes, and each question's shape counts."""
    shape_answers: dict[Shape, list[Any]] = {}
    question_shapes: dict[str, Counter[Shape]] = {}
    for question, question_answers in answers.items():
        counter: Counter[Shape] = Counter()
        for answer in question_answers:
            shape = shape_of(answer)
            counter[shape] += 1
            shape_answers.setdefault(shape, []).append(answer)
        question_shapes[question] = counter
    return shape_answers, question_shapes


def _walk_every_shape(
    shape_answers: Mapping[Shape, Sequence[Any]],
    serializers: Mapping[Shape, Serializer],
    titles: Mapping[Shape, str],
    annotations: Annotations,
) -> dict[str, ShapeWalk]:
    """One walk per shape that has a section, over every answer taking it."""
    walks: dict[str, ShapeWalk] = {}
    for shape, answers in shape_answers.items():
        if titles[shape] == EMPTY_ARRAY:
            continue
        serializer = serializers[shape]
        element_shape = Shape("object", shape.keys) if serializer.per_element else shape
        roots = serializer_root(serializer, element_shape, annotations)
        walk = ShapeWalk(roots, annotations, _discriminating_key(shape, roots, annotations))
        for answer in answers:
            walk.add(answer)
        walks[titles[shape]] = walk
    return walks


def _discriminating_key(
    shape: Shape, roots: Sequence[type[Any]], annotations: Annotations
) -> str | None:
    """The single key that names a shape rather than belonging to the answer type."""
    if shape.container != "object" or len(shape.keys) != 1:
        return None
    only = next(iter(shape.keys))
    if any(annotations.attribute(cls, only) for cls in roots):
        return None
    return only


def corpus_header(reference: Reference) -> list[str]:
    """The page's title, what produced it, and how much corpus stands behind it."""
    everywhere = sorted(
        key for key, block in reference.answers.items() if len(block) == reference.total_vectors
    )
    only_block = "the only block" if everywhere == [DETECTION_BLOCK] else "one of the blocks"
    return [
        "# Contract reference — GENERATED, do not edit",
        "",
        *paragraph(
            "Regenerate with `python scripts/generate_contract_reference.py` (then `deno fmt`). Every cell below is "
            "derived from one of five readings of this repository, and each says which one spoke: the **annotations** "
            "on the answer types (what a field _can_ be), the **vectors** under `vectors/machines/` (what an answer "
            "_did_ carry), an AST scan of the **construction sites** in `atlas/` (which caveat data keys the source "
            "itself spells out), an AST scan of the **serializers** in `atlas/contract.py` (which function returns "
            "which shape, and which key it fills from a differently named attribute), and the **data registry** "
            "`atlas.ENUMERATED_DATA` (which `(code, key)` values are refused at construction, whether or "
            "not a vector exercises them). An attribute an answer type "
            "declares and no serialized answer carries is listed under "
            "[attributes no answer carries](#attributes-no-answer-carries) rather than described."
        ),
        *paragraph(
            f"**Corpus:** {reference.total_vectors} vectors in {len(reference.per_file)} files carry "
            f"{len(reference.questions)} question kinds plus `{DETECTION_BLOCK}`, which is {only_block} every vector "
            f"states. **Codes:** {len(reference.exported_codes)} distinct caveat and unresolved codes are exported "
            f"under {len(reference.exported_names)} names in `atlas.__all__`, and {len(reference.witnessed)} of them "
            "appear in the corpus."
        ),
        *table(
            ["vector file", "vectors"],
            [[f"`{name}`", str(count)] for name, count in sorted(reference.per_file.items())],
        ),
        "",
    ]


def how_to_read_a_field_table(reference: Reference) -> list[str]:
    """What each column of every field table below states, and what it does not."""
    types = reference.annotations.seen_types
    documented = sum(1 for cls in types if (cls.__doc__ or "").strip())
    prose = reference.prose
    return [
        "## How to read a field table",
        "",
        *paragraph(
            "One row per path of a serialized answer, dotted through objects and with `[]` for a step into an array. "
            "**JSON type** is what the corpus showed at that path. **Declared as** is the annotation of the attribute "
            "the path serializes — suffixed `(property)` where the attribute is one, `derived (method)` where the "
            "answer type computes the value in a method, and `—` where no attribute of the answer type carries that "
            "name. A step into an array is declared as what one member of it admits, not as the list. **Can be null** "
            "reads that annotation where there is one to read: `yes` where it admits `None`, `no` where it does not, "
            "and `not stated` for the two cells above that carry no annotation this scan reads — a name that matched "
            "no attribute, and a method, whose return type is annotated and deliberately not taken as a field's. "
            "**Null observed** is whether a vector produced `null` there, so a `no` against a `yes` in the previous "
            "column is a nullable field the corpus does not witness — true, and weaker than a witnessed one. "
            "**Answers** is how many answers of this shape carried the path at all, over how many take the shape. "
            "**Closed vocabulary** names every `Literal` and the tuple a `__post_init__` checks the attribute "
            "against, whether it compares there or hands the attribute to a helper beside it; an empty cell means no "
            "vocabulary is declared for it."
        ),
        *paragraph(
            "A caveat serializes as `{code, data}` everywhere it appears, and its `data` keys are data rather than "
            "declared attributes, so a field table stops at `data` and the caveat tables below carry the keys — and "
            "those two rows read `—` where the attribute the caveat hangs off declares no members of its own, as "
            "installation health does."
        ),
        *paragraph(
            f"There is no column saying what a field means. Of the {prose.attributes} attributes the {len(types)} "
            f"answer types below declare, {prose.docstrings} carry a docstring and {prose.commented} are preceded by "
            f"a comment, and {documented} of the types carry a class docstring. A docstring is an expression with an "
            "end and could be quoted; a comment is not, and quoting one would mean this generator deciding where its "
            "sentence stops — a per-field sentence the page did not derive. Each shape names its type and module "
            "instead, and the source is where the comments are read."
        ),
    ]


def questions_section(reference: Reference) -> list[str]:
    """One section per expected block: the shapes it takes and the codes that ride it."""
    lines = [
        "## Questions",
        "",
        *paragraph(
            f"One section per expected block the corpus carries, `{DETECTION_BLOCK}` last: it is what detection must "
            "find rather than something a caller asks for. **Dispatchers producing it** names every function in "
            "`atlas/contract.py` that returns more than one shape and can return this one. That is read off the "
            "module as a whole, not off this question: a dispatcher listed against a shape here need not be one this "
            "question ever routes through, and a refusal shape is reached by the dispatcher of every family that can "
            "refuse."
        ),
    ]
    for question in [*reference.questions, DETECTION_BLOCK]:
        lines.append(f"### {question}")
        lines.append("")
        counts = reference.question_shapes[question]
        lines.extend(paragraph(f"{sum(counts.values())} vectors state it."))
        rows: list[list[str]] = []
        for shape, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].sort_key())):
            title = reference.titles[shape]
            linked = f"[{title}]({anchor(title)})" if title in reference.walks else title
            reached = backticked(reference.serializers[shape].reached_through)
            rows.append([cell(linked), str(count), cell(shape.container), cell(reached)])
        lines.extend(table(["shape", "vectors", "container", "dispatchers producing it"], rows))
        lines.append("")

        codes = sorted(
            code for code, entry in reference.witnessed.items() if question in entry.questions
        )
        if not codes:
            lines.extend(paragraph("No caveat code appears on this question in the corpus."))
            continue
        code_rows = [
            [
                cell(f"`{code}`"),
                str(reference.witnessed[code].questions[question]),
                cell(
                    backticked(sorted(reference.witnessed[code].keys_per_question[question]))
                    or "no data keys"
                ),
            ]
            for code in codes
        ]
        lines.extend(table(["code", "occurrences", "data keys witnessed here"], code_rows))
        lines.append("")
    return lines


def linked_title(reference: Reference, shape: Shape) -> str:
    """What to call a shape a walk stopped at, linked where it has a section.

    A shape the corpus also states as a whole answer has one; a shape that only
    ever appears inside another answer does not, and is named rather than
    dropped, because a path whose carried shape went unlisted would read as a
    path that carries nothing.
    """
    title = reference.titles.get(shape) or shape_title(shape, serializer_for(shape, reference.produced))
    return f"[{title}]({anchor(title)})" if title in reference.walks else title


def answer_shapes_section(reference: Reference) -> list[str]:
    """One field table per distinct shape, plus the shapes each one carries inside it."""
    lines = [
        "## Answer shapes",
        "",
        *paragraph(
            "One section per distinct shape the corpus produced, named by the function in `atlas/contract.py` whose "
            "returned keys match it exactly. `(array)` marks a shape the corpus states as a list, which the named "
            "function either returns whole or serializes one element at a time — the two cases read alike on the "
            "wire, and the field tables below start at `[]` for both. An answer that is an empty list carries no keys "
            "and so takes no shape; it is counted in the question tables above and has no section here."
        ),
    ]
    for title in sorted(reference.walks):
        walk = reference.walks[title]
        lines.append(f"### {title}")
        lines.append("")
        roots = ", ".join(f"`{cls.__name__}` (`{cls.__module__}`)" for cls in walk.roots)
        if roots:
            lines.extend(
                paragraph(f"Serialized from {roots}. {walk.answers} answers in the corpus take this shape.")
            )
        else:
            lines.extend(
                paragraph(
                    f"{walk.answers} answers in the corpus take this shape. No answer type was resolved for it, so "
                    "every row below states the corpus alone."
                )
            )
        if walk.wrapper is not None:
            lines.extend(
                paragraph(
                    f"`{walk.wrapper}` is the single key that discriminates this shape. It is not an attribute of the "
                    "answer type; the object below it carries the answer."
                )
            )
        lines.extend(
            table(
                [
                    "field",
                    "JSON type",
                    "declared as",
                    "can be null",
                    "null observed",
                    "answers",
                    "closed vocabulary",
                ],
                field_rows(walk),
            )
        )
        lines.append("")
        nested = [
            [cell(f"`{path}`"), cell(linked_title(reference, shape)), str(count)]
            for path, shapes in sorted(walk.stopped.items())
            for shape, count in sorted(shapes.items(), key=lambda item: (-item[1], item[0].sort_key()))
        ]
        if nested:
            lines.extend(
                paragraph(
                    "The paths below carry a whole answer of another shape rather than attributes of this one, so the "
                    "walk stops there and the shape it carried is named instead. Every shape the corpus put there is "
                    "listed; one that is an answer in its own right links to its section, and one that is not is "
                    "named by its serializer where one matches its keys and by the keys themselves otherwise — "
                    "`unattributed` there means no serializer in `atlas/contract.py` returns exactly those keys. "
                    "**Occurrences** counts "
                    "the objects at that path, which for a path inside an array is more than one per answer."
                )
            )
            lines.extend(table(["field", "shape it carried", "occurrences"], nested))
            lines.append("")
    return lines


def _serialized_attributes(reference: Reference) -> tuple[dict[str, set[str]], set[str]]:
    """Which attributes the walks resolved a path to, and every key name on the wire."""
    serialized: dict[str, set[str]] = {}
    on_the_wire: set[str] = set()
    for walk in reference.walks.values():
        for path, field in walk.fields.items():
            on_the_wire.add(path.rstrip("[]").rpartition(".")[2])
            if field is not None:
                serialized.setdefault(field.owner, set()).add(field.name)
    return serialized, on_the_wire


def _unseen_rows(
    reference: Reference, serialized: Mapping[str, set[str]]
) -> tuple[list[list[str]], set[str]]:
    """One row per answer type that declares an attribute no path resolves to."""
    rows: list[list[str]] = []
    unseen_names: set[str] = set()
    for cls in sorted(reference.annotations.seen_types, key=lambda c: c.__name__):
        if not dataclasses.is_dataclass(cls):
            continue
        unseen = sorted({f.name for f in dataclasses.fields(cls)} - set(serialized.get(cls.__name__, set())))
        if unseen:
            unseen_names.update(unseen)
            rows.append([cell(f"`{cls.__name__}`"), cell(f"`{cls.__module__}`"), cell(backticked(unseen))])
    return rows, unseen_names


def _elsewhere_sentence(as_field: Sequence[str], as_data: Sequence[str]) -> str:
    """What to add where one of these names reaches the wire by another route."""
    routes: list[str] = []
    if as_field:
        carried = "an attribute" if len(as_field) == 1 else "attributes"
        routes.append(f"{backticked(as_field)} as {carried} another type carries")
    if as_data:
        keys = "a caveat `data` key" if len(as_data) == 1 else "caveat `data` keys"
        routes.append(f"{backticked(as_data)} as {keys}")
    if not routes:
        return ""
    found = len(as_field) + len(as_data)
    return (
        f" That is a claim about the attribute rather than about its name: {counted(found, 'name')} here "
        f"{'appears' if found == 1 else 'appear'} on the wire elsewhere — {' and '.join(routes)} — and whether "
        "the value behind one of those is this attribute's is the serializer's business, which this page reads "
        "per path and not per name."
    )


def attributes_no_answer_carries(reference: Reference) -> list[str]:
    """The declared attributes no path in any field table above resolves to."""
    serialized, on_the_wire = _serialized_attributes(reference)
    data_keys = {key for code in reference.witnessed.values() for key in code.keys}
    rows, unseen_names = _unseen_rows(reference, serialized)
    as_field = sorted(unseen_names & on_the_wire)
    as_data = sorted(unseen_names & data_keys - on_the_wire)
    elsewhere = _elsewhere_sentence(as_field, as_data)
    return [
        "## Attributes no answer carries",
        "",
        *paragraph(
            "Declared on an answer type above and carried by no serialized answer in the corpus: no path in the field "
            "tables resolves to one of these attributes, so a port reproducing the contract never has to serialize "
            f"them.{elsewhere}"
        ),
        *table(["answer type", "module", "attributes"], rows),
        "",
    ]


def closed_vocabularies(reference: Reference) -> list[str]:
    """Every closed value list, under every name the source holds it under."""
    named: dict[tuple[str, ...], set[str]] = {}
    against: dict[tuple[str, ...], set[str]] = {}
    for title in sorted(reference.walks):
        for path, field in sorted(reference.walks[title].fields.items()):
            if field is None:
                continue
            for vocabulary in field.vocabularies:
                named.setdefault(vocabulary.values, set()).add(vocabulary.name)
                against.setdefault(vocabulary.values, set()).add(f"{title}.{path}")
    for name in atlas.__all__:
        exported = getattr(atlas, name)
        if isinstance(exported, tuple) and exported and all(isinstance(v, str) for v in exported):
            named.setdefault(exported, set()).add(name)
            against.setdefault(exported, set())
    rows = [
        [
            cell(backticked(sorted(named[values]))),
            str(len(values)),
            cell(backticked(values)),
            cell(backticked(sorted(against[values])) or "no serialized attribute is declared against it"),
        ]
        for values in sorted(named, key=lambda v: min(named[v]))
    ]
    return [
        "## Closed vocabularies",
        "",
        *paragraph(
            "One row per value list, with every name the readings attribute to it — an attribute that states its "
            "vocabulary twice is named twice. A `Literal[...]` name means an annotation states the values; a bare "
            "name is a module-level tuple — the one an answer type checks the attribute against in its "
            "`__post_init__` or in a helper that `__post_init__` hands it to, or one `atlas` exports. Exported "
            "tuples are listed even where no serialized attribute is declared against them, "
            "because the export is itself the offer to branch on the list."
        ),
        *table(["names", "values", "value list", "attributes declared against it"], rows),
        "",
    ]


def caveat_codes(reference: Reference) -> list[str]:
    """Every exported code: what the source spells out, and what the corpus carried."""
    lines = [
        "## Caveat codes",
        "",
        *paragraph(
            "Every code `atlas` exports, alphabetically. **Keys in source** is the union of the `data` keys read off "
            "literal dictionaries at the `Caveat(...)` / `Unresolved(...)` construction sites this scan can attribute "
            "to that code — not always the whole truth by itself, so the cell says where it stops. A site that builds "
            "`data` from a variable states no keys, and the count of such sites is given instead. A site that names "
            "its _code_ through a variable states neither, and the cell says so rather than reading absence into it. "
            "Where the corpus carried a key no attributable site spells, the cell names that key too, because a "
            "reader branching on it would otherwise read the list as complete. **Keys witnessed** is what the corpus "
            "actually carried. A code witnessed nowhere is a promise no vector holds a port to."
        ),
    ]
    sites_named_by_variable = reference.unattributed_sites
    if sites_named_by_variable:
        lines.extend(
            paragraph(
                f"Exactly {counted(sites_named_by_variable, 'construction site')} "
                f"{'names its' if sites_named_by_variable == 1 else 'name their'} code through a variable rather than "
                "a constant, so this scan attributes no keys there. A code below that no site spells and a vector "
                f"still carried was built {'there' if sites_named_by_variable == 1 else 'at one of them'}: every "
                "`Caveat(...)` and `Unresolved(...)` call in the package is read, so there is nowhere else it could "
                "have come from."
            )
        )
    rows: list[list[str]] = []
    for code in reference.exported_codes:
        seen = reference.witnessed.get(code)
        source = source_keys_cell(
            reference.sites.get(code),
            None if seen is None else frozenset(seen.keys),
            sites_named_by_variable,
        )
        if seen is None:
            witnessed_keys = "not witnessed"
            rides = "not witnessed"
        else:
            witnessed_keys = backticked(sorted(seen.keys)) or "no data keys"
            rides = ", ".join(f"`{q}` ({n})" for q, n in sorted(seen.questions.items()))
        rows.append([cell(f"`{code}`"), cell(source), cell(witnessed_keys), cell(rides)])
    lines.extend(table(["code", "keys in source", "keys witnessed", "questions it rides"], rows))
    lines.append("")
    return lines


def value_shape(values: Sequence[Any]) -> str:
    """The one JSON type a ``(code, key)`` carries — the contract is one, not two.

    :func:`shape_disagreements` fails the generation where the corpus shows two,
    so by the time this renders there is exactly one to name.
    """
    return ", ".join(sorted({json_type(value) for value in values}))


def refused_cell(vocabulary: str | None) -> str:
    """The allowed-values cell: the tuple the constructors check, or nothing to say."""
    return f"closed set `{vocabulary}`, refused at construction" if vocabulary else "—"


def caveat_data_values(reference: Reference) -> list[str]:
    """What each code's data keys actually held, and what they are allowed to hold."""
    registered = reference.enumerations
    rows: list[list[str]] = []
    for code in sorted(reference.witnessed):
        for key in sorted(reference.witnessed[code].keys):
            values = reference.witnessed[code].keys[key]
            vocabulary = registered.get((code, key))
            observed = describe_values(values, reference.vocabularies)
            allowed = refused_cell(vocabulary)
            rows.append(
                [cell(f"`{code}`"), cell(f"`{key}`"), cell(value_shape(values)), cell(allowed), cell(observed)]
            )
    # A registered pair the corpus never shows still belongs on the page: the
    # guarantee holds whether or not a fixture exercises it, and saying nothing
    # would read as "this key is free".
    for (code, key), vocabulary in sorted(registered.items()):
        if key in reference.witnessed.get(code, WitnessedCode()).keys:
            continue
        rows.append(
            [
                cell(f"`{code}`"),
                cell(f"`{key}`"),
                cell("string"),
                cell(refused_cell(vocabulary)),
                cell("not witnessed in the corpus"),
            ]
        )
    return [
        "## Caveat data values",
        "",
        *paragraph(
            "One row per data key, per code that carries it: a key that rides three codes is three rows, because what "
            "it holds is the code's business and not the key's — and so is its **type**, which is why the type is a "
            "column. `options` is an array under `core-multi-option` and an object under `core-mode-unestablished`; "
            "`key` is a string under a dozen codes and an array under the per-game-layer ones. One `(code, key)` "
            "carries one type across the whole corpus, and a second type stops this generation rather than being "
            "published as a choice."
        ),
        *paragraph(
            "**Refused at construction** names the tuple `atlas.ENUMERATED_DATA` binds to that pair: "
            "`Caveat.__post_init__` and `Unresolved.__post_init__` raise on anything outside it, so the guarantee "
            "holds whether or not a fixture exercises it — a pair the corpus has not reached says so in the last "
            "column and keeps its guarantee. Where no registry entry exists, a **closed set** in the last column is "
            "the weaker, corpus-only claim: a module-level tuple in `atlas/` holds exactly the observed values. "
            "Holding a few of a tuple's members is not evidence that the tuple is the key's vocabulary, so those "
            "values are listed instead, or counted where there are too many to list."
        ),
        *table(["code", "data key", "type", "allowed values", "values observed"], rows),
        "",
    ]


SECTIONS = (
    corpus_header,
    how_to_read_a_field_table,
    questions_section,
    answer_shapes_section,
    attributes_no_answer_carries,
    closed_vocabularies,
    caveat_codes,
    caveat_data_values,
)


def build() -> tuple[list[str], list[str]]:
    """The generated page's lines, and the contradictions that must stop it."""
    reference = read_everything()
    lines = [line for section in SECTIONS for line in section(reference)]
    failures = [
        *contradictions(reference.walks),
        *shape_disagreements(reference.witnessed),
        *registry_disagreements(reference.witnessed, reference.enumerations),
        *ambiguous_vocabulary_names(reference.enumerations),
    ]
    return lines, failures


def main() -> None:
    lines, failures = build()
    if failures:
        # Four gates print here and they do not share a pair of readings: the
        # null cross-check is annotations against vectors, the shape and
        # registry checks are the corpus against itself and against the
        # registry, and the naming check is the package against itself.
        print("contract reference: the readings disagree —", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        raise SystemExit(1)
    OUTPUT_PATH.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    print(f"written: {OUTPUT_PATH.relative_to(REPO_ROOT)} ({len(lines)} lines)")


if __name__ == "__main__":
    main()
