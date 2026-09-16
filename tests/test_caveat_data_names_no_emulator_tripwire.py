"""The tripwire that keeps caveat data's identity keys each in one vocabulary.

Four words in a serialized answer name the thing that runs, and each means a
different one. ``emulator`` is the identity the launch command spells, which is
the frontend's own word for it and differs between frontends — RetroDECK writes
``CEMU`` where EmuDeck writes ``cemu``. ``token`` is atlas's own name for a
carded emulator. ``core`` is a libretro core's short name, the spelling
``core_audit.json`` and ``core_oddities.json`` are keyed by. ``core_so`` is that
core as a file, which is the same core under a name that ends in ``.so``. A
client joining a caveat to the entry it is about compares two of them and gets
away with it only where they happen to agree.

What this module holds is one rule per word:

* **no caveat data key is spelled** ``emulator``, so a key of that name anywhere
  on the wire is the launch identity and never the card;
* **no** ``core`` **key carries a card token**, nor a core file's name;
* **every** ``token`` **key carries a packaged card token**, except under
  ``platform-unknown``, whose ``token`` is an ES-DE ``<platform>`` tag rather
  than a card and says so in the guide;
* **every** ``core_so`` **key carries a core file's name**, the one of these
  vocabularies a shape can state positively, because a file name is what it ends
  in.

Only the first of these used to be asserted. A card token rode under
``core`` as well as under ``token``, beside the libretro core names that key
also carried, which made "a card token is spelled ``token``" a false universal.
It no longer rides there, and what keeps it from coming back is below rather
than in a sentence.

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

The reach is measured, not assumed: of the 292 construction sites this walk's
own definitions find, 273 are read whole, 7 are read in part, and 12 cannot be
read at all. The 292 and the 273 were counted when this was written and grow
with the package, held by nothing here; the 19 behind the other two numbers are
the ones that matter, and :data:`BLIND_SITES` pins them. Seven shapes account
for those 19, in two groups. Written at the call: the argument is a conditional
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

The same walk reads the **value** each entry under one of the three identity
keys is written with, wherever that entry is a written-out dictionary entry, and
places it by the expression's spelling: an attribute ``.token`` and the name
``token`` state the card; an attribute ``.key`` and the names ``core``,
``core_key`` and ``short_name`` state the core; an attribute ``.core_so`` and
the names ``core_so`` and ``so_basename`` state the core's file; and a string
literal is placed by looking it up in the packaged card tokens and the packaged
core tables, so ``'DOLPHIN'`` is a card and ``'mame'`` is a core. A value placed
in one vocabulary and written under another's key is the offence, whichever pair
of the three it is. Anything the spelling cannot place is unplaced, and
:data:`UNPLACED_VALUES` pins that census the way :data:`BLIND_SITES` pins the
other. Reading a spelling is not reading a value: a parameter is placed by the
word it is named, and a caller that hands it the other vocabulary is something
only the corpus end can see — which is the reason the one parameter that did
carry a card token under ``core`` was also named ``core``.

A hollow pass is made visible by pinning: :data:`BLIND_SITES` is the reviewed
census of those 19, keyed by the function that holds them, and
:func:`test_the_blind_sites_are_the_reviewed_ones` fails when the walk's census
differs. A new construction site the walk cannot read therefore fails loudly and
gets reviewed, rather than quietly shrinking what the rule covers. The census is
keyed by function rather than by line so that editing a module does not churn
it.

**The corpus end** reads what answers actually carry, whatever built them, which
is what covers the 19 sites the source end does not read whole: the keys, and
for the identity keys the values as well. What separates ``core`` from ``token``
there is the packaged card set itself — a ``core`` value is never one of those
tokens, and a ``token`` value is always one — and neither of those two is a rule
about shape, because shape cannot carry them: a libretro short name can carry
capitals (``DoubleCherryGB`` is one), so case separates nothing, and a core in
none of the packaged tables still answers, reaching ``core-unaudited``, so a
``core`` value cannot be required to be a table entry either. ``core_so`` is the
one a shape can state, because the suffix belongs to the file rather than to a
convention about how cores are named.

The packaged tokens are not in :data:`atlas.ENUMERATED_DATA`, which is where a
closed ``(code, key)`` vocabulary is refused at construction. That table holds
module constants, and this union needs all six card loaders — four of which
reach :mod:`atlas.placement`, where the table lives: :mod:`atlas.mods` and
:mod:`atlas.textures` import it directly, :mod:`atlas.standalone_savestates` and
:mod:`atlas.emulator_settings` through the second of those. So the table's own
module cannot import them back to compute the union, and the one code whose
``token`` is not a card would need excepting there as well. The rule is held
here instead.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from typing import Iterator, Mapping, Sequence, TypeAlias

import pytest

from atlas._data import packaged_text
from atlas.emulator_settings import load_emulator_settings
from atlas.mods import load_standalone_mod_cards
from atlas.standalone_firmware import load_standalone_firmware
from atlas.standalone_saves import load_standalone_saves
from atlas.standalone_savestates import load_standalone_savestates
from atlas.textures import load_standalone_texture_packs
from scripts import generate_contract_reference as reference
from tests.corpus import caveat_blocks, vector_files


IDENTITY_KEY = "emulator"

# One caveat as the corpus end reads it: which file it came out of, its code,
# and its data. A mapping rather than a dict so a probe can hand these
# predicates a caveat of its own making.
CaveatBlock: TypeAlias = tuple[str, str, Mapping[str, object]]

# The keys that name the thing that runs, and what each is the vocabulary of: a
# libretro core's short name, a packaged card's own token, and that same core as
# the file a loader opens. All three are read at a construction site, and each
# is an offence under either of the other two.
CORE_KEY = "core"
TOKEN_KEY = "token"
CORE_SO_KEY = "core_so"
VOCABULARY_KEYS = (CORE_KEY, TOKEN_KEY, CORE_SO_KEY)

# What makes a name a core file's rather than a core's: the suffix a shared
# object carries. The corpus spells every one of them `<name>_libretro.so`, but
# the infix is a convention of the cores RetroArch ships, and the package's own
# reader takes it off optionally (`removesuffix(".so").removesuffix("_libretro")`),
# so the suffix is the part that holds.
CORE_FILE_SUFFIX = ".so"

# The one code whose ``token`` is not a card: an ES-DE ``<platform>`` tag
# outside the platform vocabulary, which is what that caveat is about. The
# guide documents the exception at the same place it documents the key.
PLATFORM_TAG_CODE = "platform-unknown"

# The tables a libretro core's short name is a key of. Membership is not
# something a value can be held to: the name is taken off a deployed ``.so``,
# and a core none of these tables knows still answers — it reaches
# ``core-unaudited``, which fires on a missing ``core_audit`` entry alone. So
# this set places a literal written into the source, never what a value must be.
CORE_TABLES = ("core_audit", "core_oddities", "core_firmware", "save_memory")

# Value expressions the walk places without reading the packaged data: an
# attribute at the end of the expression, or a bare name.
_TOKEN_NAMES = frozenset({TOKEN_KEY})
_CORE_NAMES = frozenset({CORE_KEY, "core_key", "short_name"})
_TOKEN_ATTRIBUTES = (".token",)
_CORE_ATTRIBUTES = (".key",)
_CORE_FILE_NAMES = frozenset({CORE_SO_KEY, "so_basename"})
_CORE_FILE_ATTRIBUTES = (".core_so",)

# The reviewed census of identity values this walk sees and cannot place from
# the spelling alone, as {(module, function, key): the expression}. A new one
# fails rather than passing as placed, and the corpus end is what covers
# whatever it hides. Each was read: ``tag`` is the ES-DE ``<platform>`` token
# this module's one documented exception is about; the three basename calls are
# handed a name this walk does place (``core_so``) and return the file part of
# it; and the f-string builds a file name by appending the suffix itself. None
# of them is a name a rule about names could place without pretending to
# evaluate an expression.
UNPLACED_VALUES: dict[tuple[str, str, str], str] = {
    ("firmware.py", "firmware_for_core", CORE_SO_KEY): "f'{stem}.so'",
    ("installations.py", "_core_info_unreadable_caveat", CORE_SO_KEY): (
        "os.path.basename(core_so)"
    ),
    ("installations.py", "_core_info_unreadable_for_patching", CORE_SO_KEY): (
        "os.path.basename(core_so)"
    ),
    ("installations.py", "_savestate_support_caveats", CORE_SO_KEY): (
        "os.path.basename(core_so)"
    ),
    ("installations.py", "platform_ids", TOKEN_KEY): "tag",
}

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

    ``values`` is ``(key, the value expression's spelling)`` for each written-out
    entry under one of :data:`VOCABULARY_KEYS`, which is what the identity rules
    read. A key with no pair here is one whose value the walk did not see; a pair
    is a spelling, never a value.
    """

    where: str
    function: str
    keys: tuple[str, ...]
    unread: bool
    values: tuple[tuple[str, str], ...] = ()


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


def _dictionaries(scope: ast.AST, data: ast.expr | None) -> list[ast.Dict]:
    """The dictionary literals a site's ``data`` argument is written as, if any."""
    if isinstance(data, ast.Dict):
        return [data]
    if isinstance(data, ast.Name):
        return [value for value in _bound_values(scope, data.id) if isinstance(value, ast.Dict)]
    return []


def _vocabulary_values(scope: ast.AST, data: ast.expr | None) -> tuple[tuple[str, str], ...]:
    """``(key, spelling)`` for each written-out identity entry of one site.

    An entry stored into a mapping under a subscript is not read here, the same
    way its key is only read as a key: seeing fewer entries can cost coverage,
    never make a rule state something false.
    """
    seen = [
        (key.value, ast.unparse(value))
        for literal in _dictionaries(scope, data)
        for key, value in zip(literal.keys, literal.values)
        if isinstance(key, ast.Constant) and key.value in VOCABULARY_KEYS
    ]
    return tuple(sorted(seen))


def _site(where: str, scope: ast.AST, call: ast.Call) -> Site:
    """One construction site read as far as the AST allows."""
    function = _scope_name(scope)
    data = _argument(call, "data", 2)
    values = _vocabulary_values(scope, data)
    spelled = _site_data_keys(call)
    if spelled is not None:
        return Site(where, function, tuple(sorted(spelled)), False, values)
    if isinstance(data, ast.Dict):
        return Site(where, function, tuple(sorted(_string_keys(data))), True, values)
    if isinstance(data, ast.Name):
        keys, unread = _from_variable(scope, data.id)
        return Site(where, function, keys, unread, values)
    return Site(where, function, (), True, values)


def construction_sites() -> list[Site]:
    """Every ``Caveat(...)`` / ``Unresolved(...)`` site under ``atlas/``."""
    return [
        _site(f"{path.name}:{call.lineno}", scope, call)
        for path, tree in reference.package_modules()
        for scope, call in _calls_by_scope(tree)
    ]


def packaged_tokens() -> frozenset[str]:
    """Every token a packaged standalone card is addressed by."""
    loaders = (
        load_standalone_saves,
        load_standalone_savestates,
        load_standalone_firmware,
        load_standalone_mod_cards,
        load_standalone_texture_packs,
    )
    tokens = {card.token for load in loaders for card in load()}
    return frozenset(tokens | set(load_emulator_settings()))


def packaged_core_names() -> frozenset[str]:
    """Every libretro short name the packaged core tables are keyed by."""
    return frozenset(
        name
        for table in CORE_TABLES
        for name in json.loads(packaged_text(f"{table}.json"))["cores"]
    )


def _placed_literal(spelling: str) -> str | None:
    """Which vocabulary a string literal belongs to, by looking the value up."""
    if not (spelling.startswith("'") and spelling.endswith("'")):
        return None
    value = spelling[1:-1]
    if value in packaged_tokens():
        return TOKEN_KEY
    return CORE_KEY if value in packaged_core_names() else None


def vocabulary_of(spelling: str) -> str | None:
    """Which identity a value expression states, or ``None`` where it cannot be placed.

    Named for the key that identity belongs under, so a value and the key it is
    written with are comparable. A spelling rather than a value, so this says
    what the source claims about itself: an attribute or a bare name is placed
    by the word it ends in, and a literal by the packaged table it is a member
    of.
    """
    if spelling.endswith(_CORE_FILE_ATTRIBUTES) or spelling in _CORE_FILE_NAMES:
        return CORE_SO_KEY
    if spelling.endswith(_TOKEN_ATTRIBUTES) or spelling in _TOKEN_NAMES:
        return TOKEN_KEY
    if spelling.endswith(_CORE_ATTRIBUTES) or spelling in _CORE_NAMES:
        return CORE_KEY
    return _placed_literal(spelling)


def miswritten_values(sites: list[Site], key: str) -> list[str]:
    """Where *key* is written with a value the walk places in another vocabulary."""
    return [
        f"{site.where}: {key} = {spelling}"
        for site in sites
        for written, spelling in site.values
        if written == key and vocabulary_of(spelling) not in (None, key)
    ]


def unplaced_values(sites: list[Site]) -> dict[tuple[str, str, str], str]:
    """The census of ``core`` / ``token`` values the walk sees and cannot place."""
    return {
        (site.where.split(":")[0], site.function, key): spelling
        for site in sites
        for key, spelling in site.values
        if vocabulary_of(spelling) is None
    }


def corpus_caveats() -> list[tuple[str, str, dict[str, object]]]:
    """``(file, code, data)`` for every caveat block the shipped corpus carries."""
    return [
        (path.name, code, data)
        for path in vector_files()
        for code, data in caveat_blocks(json.loads(path.read_text(encoding="utf-8")))
    ]


def _stated(caveats: "Sequence[CaveatBlock]", key: str) -> Iterator[tuple[str, str, object]]:
    """``(file, code, value)`` for every caveat that carries *key* at all.

    Whatever it carries: an identity is a string under each of these keys, so a
    value that is anything else is the rule's business rather than beneath it.
    """
    for file, code, data in caveats:
        if key in data:
            yield file, code, data[key]


def _is_core_name(value: object, tokens: "frozenset[str]") -> bool:
    """A libretro core's short name: no card's token, and no file's name."""
    if not isinstance(value, str):
        return False
    return value not in tokens and not value.endswith(CORE_FILE_SUFFIX)


def _is_card_token(value: object, tokens: "frozenset[str]") -> bool:
    """One of the tokens a packaged standalone card is addressed by."""
    return isinstance(value, str) and value in tokens


def _is_core_file(value: object) -> bool:
    """A core as a file: the name a loader opens, which is what ends in ``.so``."""
    return isinstance(value, str) and value.endswith(CORE_FILE_SUFFIX)


def corpus_core_offenders(caveats: "Sequence[CaveatBlock]") -> list[str]:
    """Every ``core`` value that is a card token, or a core file's name, or no string."""
    tokens = packaged_tokens()
    return [
        f"{file}: {code}.{CORE_KEY} = {value!r}"
        for file, code, value in _stated(caveats, CORE_KEY)
        if not _is_core_name(value, tokens)
    ]


def corpus_token_offenders(caveats: "Sequence[CaveatBlock]") -> list[str]:
    """Every ``token`` value that is no packaged card token, the documented code apart."""
    tokens = packaged_tokens()
    return [
        f"{file}: {code}.{TOKEN_KEY} = {value!r}"
        for file, code, value in _stated(caveats, TOKEN_KEY)
        if code != PLATFORM_TAG_CODE and not _is_card_token(value, tokens)
    ]


def corpus_core_file_offenders(caveats: "Sequence[CaveatBlock]") -> list[str]:
    """Every ``core_so`` value that is not a core file's name."""
    return [
        f"{file}: {code}.{CORE_SO_KEY} = {value!r}"
        for file, code, value in _stated(caveats, CORE_SO_KEY)
        if not _is_core_file(value)
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


class TestNoConstructionSiteWritesOneVocabularyUnderTheOthersKey:
    def test_no_core_key_is_written_with_a_card_token(self, sites: list[Site]):
        offenders = miswritten_values(sites, CORE_KEY)
        assert not offenders, (
            f"{len(offenders)} construction site(s) write a card token under {CORE_KEY!r}: "
            f"{', '.join(offenders)} — {CORE_KEY!r} is a libretro core's short name, and a "
            f"carded emulator's own identity belongs under {TOKEN_KEY!r}"
        )

    def test_no_token_key_is_written_with_a_core_name(self, sites: list[Site]):
        offenders = miswritten_values(sites, TOKEN_KEY)
        assert not offenders, (
            f"{len(offenders)} construction site(s) write a libretro core's name under "
            f"{TOKEN_KEY!r}: {', '.join(offenders)} — that name belongs under {CORE_KEY!r}"
        )

    def test_no_core_so_key_is_written_with_a_core_or_a_card(self, sites: list[Site]):
        offenders = miswritten_values(sites, CORE_SO_KEY)
        assert not offenders, (
            f"{len(offenders)} construction site(s) write something other than a core file's "
            f"name under {CORE_SO_KEY!r}: {', '.join(offenders)} — a core is named by "
            f"{CORE_KEY!r} and a carded emulator by {TOKEN_KEY!r}"
        )

    def test_the_unplaced_values_are_the_reviewed_ones(self, sites: list[Site]):
        """A value this walk cannot place is reviewed, never absorbed silently."""
        census = unplaced_values(sites)
        assert census == UNPLACED_VALUES, (
            "the value reading moved: it now cannot place "
            f"{sorted(census.items())}, against the reviewed census "
            f"{sorted(UNPLACED_VALUES.items())}. Read the new ones, confirm each carries the "
            "vocabulary its key names, then update UNPLACED_VALUES"
        )

    def test_a_card_token_written_under_core_is_what_this_finds(self):
        """The probe: the rule fails on the shape it exists to keep out."""
        site = Site("probe.py:1", "_probe", (CORE_KEY,), False, ((CORE_KEY, "card.token"),))
        assert miswritten_values([site], CORE_KEY) == [f"probe.py:1: {CORE_KEY} = card.token"]

    def test_a_core_name_written_under_token_is_what_this_finds(self):
        site = Site("probe.py:1", "_probe", (TOKEN_KEY,), False, ((TOKEN_KEY, "card.key"),))
        assert miswritten_values([site], TOKEN_KEY) == [f"probe.py:1: {TOKEN_KEY} = card.key"]

    def test_a_core_file_written_under_core_is_what_this_finds(self):
        """The site this change moved: the core's file is not the core's name."""
        site = Site("probe.py:1", "_probe", (CORE_KEY,), False, ((CORE_KEY, "core.core_so"),))
        assert miswritten_values([site], CORE_KEY) == [f"probe.py:1: {CORE_KEY} = core.core_so"]

    def test_a_card_token_written_under_core_so_is_what_this_finds(self):
        site = Site(
            "probe.py:1", "_probe", (CORE_SO_KEY,), False, ((CORE_SO_KEY, "card.token"),)
        )
        assert miswritten_values([site], CORE_SO_KEY) == [
            f"probe.py:1: {CORE_SO_KEY} = card.token"
        ]

    def test_a_value_spelled_neither_way_lands_in_the_census(self):
        site = Site("probe.py:1", "_probe", (CORE_KEY,), False, ((CORE_KEY, "whatever"),))
        assert unplaced_values([site]) == {("probe.py", "_probe", CORE_KEY): "whatever"}


class TestTheCorpusKeepsTheIdentityVocabulariesApart:
    def test_the_walk_finds_every_identity_key_across_the_corpus(
        self, caveats: list[tuple[str, str, dict[str, object]]]
    ):
        """The same guard the other ends have: an empty walk must not read as clean."""
        assert len(list(_stated(caveats, CORE_KEY))) > 1
        assert len(list(_stated(caveats, TOKEN_KEY))) > 1
        assert len(list(_stated(caveats, CORE_SO_KEY))) > 1

    def test_no_core_value_is_a_card_token_or_a_basename(
        self, caveats: list[tuple[str, str, dict[str, object]]]
    ):
        offenders = sorted(set(corpus_core_offenders(caveats)))
        assert not offenders, (
            f"{len(offenders)} corpus caveat(s) carry something other than a libretro core's "
            f"short name under {CORE_KEY!r}: {', '.join(offenders)}"
        )

    def test_every_token_value_is_a_packaged_card_token(
        self, caveats: list[tuple[str, str, dict[str, object]]]
    ):
        offenders = sorted(set(corpus_token_offenders(caveats)))
        assert not offenders, (
            f"{len(offenders)} corpus caveat(s) carry something other than a packaged card "
            f"token under {TOKEN_KEY!r}: {', '.join(offenders)} — the one exception is "
            f"{PLATFORM_TAG_CODE!r}, whose token is an ES-DE <platform> tag"
        )

    def test_every_core_so_value_is_a_core_files_name(
        self, caveats: list[tuple[str, str, dict[str, object]]]
    ):
        offenders = sorted(set(corpus_core_file_offenders(caveats)))
        assert not offenders, (
            f"{len(offenders)} corpus caveat(s) carry something other than a core file's name "
            f"under {CORE_SO_KEY!r}: {', '.join(offenders)}"
        )

    def test_a_card_token_under_core_is_what_this_finds(self):
        """The probe: an answer carrying the old spelling fails."""
        token = sorted(packaged_tokens())[0]
        caveats = [("probe.json", "a-code", {CORE_KEY: token})]
        assert corpus_core_offenders(caveats) == [f"probe.json: a-code.{CORE_KEY} = {token!r}"]

    def test_a_full_basename_under_core_is_what_this_finds(self):
        caveats = [("probe.json", "a-code", {CORE_KEY: "swanstation_libretro.so"})]
        assert len(corpus_core_offenders(caveats)) == 1

    def test_a_token_no_card_is_addressed_by_is_what_this_finds(self):
        caveats = [("probe.json", "a-code", {TOKEN_KEY: "NOTACARD"})]
        assert corpus_token_offenders(caveats) == [f"probe.json: a-code.{TOKEN_KEY} = 'NOTACARD'"]

    def test_the_platform_tag_is_the_one_token_that_is_no_card(self):
        caveats = [("probe.json", PLATFORM_TAG_CODE, {TOKEN_KEY: "selfmade"})]
        assert not corpus_token_offenders(caveats)

    def test_a_short_name_under_core_so_is_what_this_finds(self):
        """The probe for the fourth key: the core, where the core's file belongs."""
        caveats = [("probe.json", "a-code", {CORE_SO_KEY: "swanstation"})]
        assert corpus_core_file_offenders(caveats) == [
            f"probe.json: a-code.{CORE_SO_KEY} = 'swanstation'"
        ]

    def test_an_identity_that_is_no_string_is_what_this_finds(self):
        """A value of another JSON type is caught rather than skipped."""
        caveats = [("probe.json", "a-code", {CORE_KEY: ["flycast"]})]
        assert len(corpus_core_offenders(caveats)) == 1
