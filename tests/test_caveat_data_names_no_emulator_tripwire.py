"""The tripwire that keeps ``emulator`` out of caveat data, on the wire and in the source.

``emulator`` names one thing in a serialized answer: the identity the launch
command spells, which is the frontend's own word for it and differs between
frontends — RetroDECK writes ``CEMU`` where EmuDeck writes ``cemu``. A card
token is a different vocabulary with a different value, and caveat data used to
spell one under the other's name, so a client joining a caveat to the entry it
is about compared two vocabularies and got away with it only where the two
happen to agree.

What this module holds is the negative rule, and only that: **no caveat data key
is spelled** ``emulator``. The positive converse is not true and is not
asserted. A card token rides caveat data under more than one name — as ``token``,
and as ``core`` where the caveat is about the core that runs, beside the
libretro core names that key also carries — so "a card token is spelled
``token``" would be a false universal.

**The source end** walks every ``Caveat(...)`` / ``Unresolved(...)`` call under
``atlas/`` and reads the ``data`` mapping each one passes. The predicate for a
construction site and the reading of a directly written mapping are the contract
reference generator's own primitives; the enumeration is this walk's own,
because it must pair each call with the scope that binds its names, and
:func:`test_the_walk_finds_every_call_the_generator_finds` holds it against the
generator's. On top of them this walk adds one thing the generator does not need:
a mapping the call passes by name is resolved **within the enclosing function
only** — a module-wide lookup binds a name to an unrelated function's dictionary
of the same name — and only where that function binds it exactly once, to a
dictionary literal whose every key is a string constant, stores into it under
string-literal keys alone, and uses it nowhere else. :func:`_from_variable` is
that rule.

The reach is measured, not assumed: of 273 construction sites, 254 are read
whole, 7 are read in part, and 12 cannot be read at all. The 273 and the 254
were counted when this was written and grow with the package, held by nothing
here; the 19 behind the other two numbers are the ones that matter, and
:data:`BLIND_SITES` pins them. Seven shapes account for those 19, in
two groups. Written at the call: the argument is a conditional
expression (5, ``{"core_so": x} if x is not None else {}``), a dictionary
literal holding a ``**`` entry (2, ``{"core": core, "reason": reason,
**facts}``), or a starred call (1, ``Caveat(CODE, *_per_user_state(...))``).
Passed in by name, where the function does not pin it to one fully literal
dictionary: bound to a dictionary that itself holds a ``**`` entry (4), bound
twice on two branches (1), bound to a call's result (3, ``stated =
_stated_content(...)``), or a ``data`` parameter the function was handed and
never binds (3).

Where a site is read in part, the keys the walk *did* see are still checked, and
seeing more keys can only find more offenders — so partial reach can cost
coverage, never make the rule state something false, which is what the census
and the corpus end are for.

A hollow pass is made visible by pinning: :data:`BLIND_SITES` is the reviewed
census of those 19, keyed by the function that holds them, and
:func:`test_the_blind_sites_are_the_reviewed_ones` fails when the walk's census
differs. A new construction site the walk cannot read therefore fails loudly and
gets reviewed, rather than quietly shrinking what the rule covers. The census is
keyed by function rather than by line so that editing a module does not churn
it.

**The corpus end** reads the keys answers actually carry, whatever built them,
which is what covers the 19 sites the source end does not read whole.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from typing import Iterator

import pytest

from scripts import generate_contract_reference as reference
from tests.corpus import caveat_blocks, vector_files


IDENTITY_KEY = "emulator"

# The generator's own primitives, bound once here rather than reimplemented:
# what counts as a construction site, and what a directly written `data` mapping
# spells, must mean the same thing in both scans or the two drift apart. Private
# on purpose over there, deliberately shared here — the alternative is a second
# copy of the definition.
_caveat_calls = reference._caveat_calls  # pyright: ignore[reportPrivateUsage]
_argument = reference._argument  # pyright: ignore[reportPrivateUsage]
_site_data_keys = reference._site_data_keys  # pyright: ignore[reportPrivateUsage]
_string_keys = reference._string_keys  # pyright: ignore[reportPrivateUsage]
_called_name = reference._called_name  # pyright: ignore[reportPrivateUsage]

# A scope of its own for name binding: a name bound inside one of these is not
# the name the enclosing function binds.
_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)

# The reviewed census of construction sites this walk cannot read whole, as
# {(module, function): how many}. Each entry was read and is one of the seven
# shapes the module docstring names; the corpus end is what covers whatever they
# hide. A mismatch here is not a failure of the rule — it means the source end's
# reach moved and wants re-reading.
BLIND_SITES: dict[tuple[str, str], int] = {
    ("firmware.py", "_declaration_unknown"): 1,
    ("firmware.py", "_image_by_header"): 3,
    ("firmware.py", "_no_declaration"): 1,
    ("firmware.py", "_no_requirement"): 1,
    ("firmware.py", "identify_firmware"): 3,
    ("installations.py", "_catalogue_exclusive_caveat"): 1,
    ("installations.py", "_catalogue_sealed_caveat"): 1,
    ("installations.py", "_catalogue_unread_caveat"): 1,
    ("installations.py", "_per_user_savedata_placement"): 1,
    ("installations.py", "_retroarch_mod_location"): 1,
    ("installations.py", "_retroarch_texture_pack_location"): 1,
    ("installations.py", "_verification_notes"): 1,
    ("installations.py", "revocation"): 1,
    ("mode_rules.py", "_mame_unresolvable"): 1,
    ("mode_rules.py", "_mode_unestablished"): 1,
}


@dataclass(frozen=True)
class Site:
    """One construction site: where it is, and what its ``data`` keys are.

    ``keys`` holds the string-constant keys the walk actually saw, which are
    keys whether or not the mapping was read whole. ``unread`` says the walk
    could not prove it saw every entry — the mapping was read in part (some
    keys, and an entry it could not resolve) or not at all (no keys). Reading a
    mapping whole is the only case ``unread`` is false.
    """

    where: str
    function: str
    keys: tuple[str, ...]
    unread: bool


def _scope_name(scope: ast.AST) -> str:
    name = getattr(scope, "name", None)
    if isinstance(name, str):
        return name
    return "<lambda>" if isinstance(scope, ast.Lambda) else "<module>"


def _own_nodes(scope: ast.AST) -> Iterator[ast.AST]:
    """Every node under *scope* that is not inside a nested scope of its own."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _NESTED_SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def _calls_by_scope(tree: ast.Module) -> Iterator[tuple[ast.AST, ast.Call]]:
    """Every construction site in *tree*, paired with the scope that binds its names."""
    for scope in (tree, *(n for n in ast.walk(tree) if isinstance(n, _NESTED_SCOPES))):
        for node in _own_nodes(scope):
            if isinstance(node, ast.Call) and _called_name(node.func) in reference.CAVEAT_TYPES:
                yield scope, node


def _is_named(node: ast.expr | None, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _bound_values(scope: ast.AST, name: str) -> list[ast.expr]:
    """Every value assigned to *name* directly in *scope*."""
    values: list[ast.expr] = []
    for node in _own_nodes(scope):
        if isinstance(node, ast.Assign):
            values.extend(node.value for t in node.targets if _is_named(t, name))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if _is_named(node.target, name):
                values.append(node.value)
    return values


def _subscript_targets(scope: ast.AST, name: str) -> list[ast.Subscript]:
    """Every ``name[...] = ...`` store target in *scope*."""
    return [
        target
        for node in _own_nodes(scope)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Subscript) and _is_named(target.value, name)
    ]


def _stored_keys(scope: ast.AST, name: str) -> tuple[set[str], bool]:
    """The literal keys stored into *name*, and whether any store used a key this walk cannot read."""
    keys: set[str] = set()
    opaque = False
    for target in _subscript_targets(scope, name):
        index = target.slice
        if isinstance(index, ast.Constant) and isinstance(index.value, str):
            keys.add(index.value)
        else:
            opaque = True
    return keys, opaque


def _accounted_name_nodes(scope: ast.AST, name: str) -> set[int]:
    """The ``Name`` nodes for *name* this walk has already accounted for."""
    accounted = {id(target.value) for target in _subscript_targets(scope, name)}
    for node in _own_nodes(scope):
        if isinstance(node, ast.Assign):
            accounted |= {id(t) for t in node.targets if _is_named(t, name)}
        elif isinstance(node, ast.AnnAssign) and _is_named(node.target, name):
            accounted.add(id(node.target))
        elif isinstance(node, ast.Call) and _called_name(node.func) in reference.CAVEAT_TYPES:
            arguments = [*node.args, *(kw.value for kw in node.keywords)]
            accounted |= {id(a) for a in arguments if _is_named(a, name)}
    return accounted


def _escapes(scope: ast.AST, name: str) -> bool:
    """True where *name* is used in some way beyond binding it, storing into it, and passing it here."""
    accounted = _accounted_name_nodes(scope, name)
    return any(
        isinstance(node, ast.Name) and node.id == name and id(node) not in accounted
        for node in _own_nodes(scope)
    )


def _all_keys_are_strings(node: ast.Dict) -> bool:
    return all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in node.keys)


def _from_variable(scope: ast.AST, name: str) -> tuple[tuple[str, ...], bool]:
    """The keys a mapping assembled in *name* spells, and whether that reading is partial.

    Read whole only where the name is bound once in this scope, to a dictionary
    literal whose every key is a string constant, is otherwise only stored into
    under string-literal keys, and is used nowhere else in the scope. Anything
    weaker yields the constant keys that are visible and says the reading is
    partial.
    """
    bound = _bound_values(scope, name)
    literals = [value for value in bound if isinstance(value, ast.Dict)]
    keys: set[str] = set()
    for literal in literals:
        keys |= _string_keys(literal)
    stored, opaque = _stored_keys(scope, name)
    keys |= stored
    whole = (
        len(bound) == 1
        and len(literals) == 1
        and _all_keys_are_strings(literals[0])
        and not opaque
        and not _escapes(scope, name)
    )
    return tuple(sorted(keys)), not whole


def _site(where: str, scope: ast.AST, call: ast.Call) -> Site:
    """One construction site read as far as the AST allows."""
    function = _scope_name(scope)
    spelled = _site_data_keys(call)
    if spelled is not None:
        return Site(where, function, tuple(sorted(spelled)), False)
    data = _argument(call, "data", 2)
    if isinstance(data, ast.Dict):
        return Site(where, function, tuple(sorted(_string_keys(data))), True)
    if isinstance(data, ast.Name):
        keys, unread = _from_variable(scope, data.id)
        return Site(where, function, keys, unread)
    return Site(where, function, (), True)


def construction_sites() -> list[Site]:
    """Every ``Caveat(...)`` / ``Unresolved(...)`` site under ``atlas/``."""
    return [
        _site(f"{path.name}:{call.lineno}", scope, call)
        for path, tree in reference.package_modules()
        for scope, call in _calls_by_scope(tree)
    ]


def corpus_caveats() -> list[tuple[str, str, dict[str, object]]]:
    """``(file, code, data)`` for every caveat block the shipped corpus carries."""
    return [
        (path.name, code, data)
        for path in vector_files()
        for code, data in caveat_blocks(json.loads(path.read_text(encoding="utf-8")))
    ]


def _module_of(site: Site) -> str:
    return site.where.split(":")[0]


@pytest.fixture(scope="module")
def sites() -> list[Site]:
    return construction_sites()


@pytest.fixture(scope="module")
def caveats() -> list[tuple[str, str, dict[str, object]]]:
    return corpus_caveats()


class TestNoConstructionSiteSpellsACardTokenEmulator:
    def test_the_walk_finds_every_call_the_generator_finds(self, sites: list[Site]):
        """The scoping this walk adds must not cost it a single construction site.

        Compared by position rather than by count, so a site lost to one scope
        and gained in another cannot cancel out.
        """
        modules = reference.package_modules()
        scoped = {
            (path.name, call.lineno, call.col_offset)
            for path, tree in modules
            for _, call in _calls_by_scope(tree)
        }
        generator = {
            (path.name, call.lineno, call.col_offset)
            for path, tree in modules
            for call in _caveat_calls(tree)
        }
        assert scoped == generator
        assert len(sites) == len(scoped)

    def test_no_site_the_walk_can_read_passes_an_emulator_data_key(self, sites: list[Site]):
        offenders = [site.where for site in sites if IDENTITY_KEY in site.keys]
        partial = [site.where for site in sites if site.unread and site.keys]
        unreadable = [site.where for site in sites if site.unread and not site.keys]
        assert not offenders, (
            f"{len(offenders)} construction site(s) spell a caveat data key {IDENTITY_KEY!r}: "
            f"{', '.join(offenders)} — {IDENTITY_KEY!r} is the launch identity, which is a "
            f"different vocabulary from the card token that belongs here. Reach of this walk: "
            f"{len(sites)} sites, {len(sites) - len(partial) - len(unreadable)} read whole, "
            f"{len(partial)} read in part ({', '.join(partial) or 'none'}), "
            f"{len(unreadable)} unreadable ({', '.join(unreadable) or 'none'})"
        )

    def test_the_blind_sites_are_the_reviewed_ones(self, sites: list[Site]):
        """A construction site this walk cannot read whole is reviewed, never absorbed silently."""
        census: dict[tuple[str, str], int] = {}
        for site in sites:
            if site.unread:
                key = (_module_of(site), site.function)
                census[key] = census.get(key, 0) + 1
        assert census == BLIND_SITES, (
            "the source walk's reach moved: it now cannot read whole the sites "
            f"{sorted(census.items())}, against the reviewed census "
            f"{sorted(BLIND_SITES.items())}. Read the new ones, confirm none can hide an "
            f"{IDENTITY_KEY!r} key, then update BLIND_SITES"
        )


class TestNoCorpusCaveatCarriesAnEmulatorDataKey:
    def test_the_walk_finds_caveat_blocks_across_the_corpus(
        self, caveats: list[tuple[str, str, dict[str, object]]]
    ):
        """The same guard the source half has: an empty walk must not read as clean."""
        assert len({file for file, _, _ in caveats}) > 1
        assert any(data for _, _, data in caveats)

    def test_no_answer_in_the_corpus_carries_one(
        self, caveats: list[tuple[str, str, dict[str, object]]]
    ):
        offenders = sorted(
            {f"{file}: {code}" for file, code, data in caveats if IDENTITY_KEY in data}
        )
        assert not offenders, (
            f"{len(offenders)} corpus caveat code(s) carry a data key {IDENTITY_KEY!r}: "
            f"{', '.join(offenders)}"
        )
