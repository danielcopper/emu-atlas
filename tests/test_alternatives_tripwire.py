"""The alternatives tripwire: an alternative selects the mode it names (issue #393).

A rule card's answer carries ``granularity.alternatives`` — for every other
mode the card can be in, the full option combination that reaches it. A client
acts on that: it writes those keys into that file and expects the next answer
to be about the new mode. Nothing held the claim, and the puae round produced
one that did not. The alternative offered beside a CD model that keeps no
non-volatile memory named ``puae_model = auto``, which reaches the NVRAM modes
only while ``puae_use_boot_hd`` is disabled, so applying it led to a refusal in
48 of 480 cells. It was found by re-invoking the rule with the edit applied,
which is what this module does for the whole corpus.

The walk is machine-free and runs end to end through the resolver. Every
``machines`` vector is a machine to start from, and the answer walked is what
the resolver says about it — the expected block only picks the question, so an
alternative published in a mode the corpus itself stands in is asked of the code
rather than read out of a frozen file. Each published alternative's edits are
then written into that fixture's own option files, line by line the way a person
would; the machine is rebuilt and the same question asked again; and the answer
must select exactly the mode the alternative named. The answers reached that way
publish alternatives of their own, so the walk continues from them until no
distinct ``(mode, edits)`` pair is left — the first hop is not the claim, the
closure is.

Four things are stated rather than assumed, each held by its own test below. An
edit the fixture cannot state has its name in :data:`UNAPPLIABLE_BY_FIXTURE` —
the set holds names, and the reason it was refused rides in the walk's own
message and in the comment beside the entry — so it is exempted rather than
dropped. The walk's reach is held above :data:`COVERAGE_FLOOR`, so a walk that
quietly stops applying anything fails instead of passing. An answer that
publishes alternatives in the corpus and none when the resolver is asked is
collected as :attr:`Walk.silent` — that is the one that keeps a whole case from
vanishing in silence, because a lost alternative makes the walk smaller rather
than redder. And the answers the walk starts from are counted a second time by
shape, so an answer shape the enumerator does not read cannot pass for one that
is not there.
"""

from __future__ import annotations

import copy
import json
import posixpath
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterator, cast

import atlas
from tests.corpus import caveat_blocks
from tests.test_machine_vectors import QUESTIONS, fixture_machine, load_vectors

# The refusal's own key in a serialized answer — an answer carrying it is not a
# placement at all, so nothing below it can be read for a mode.
REFUSAL = "unresolved"


@dataclass(frozen=True, slots=True)
class OptionFile:
    """One options-file grammar, as the three facts an edit of it has to know.

    ``separator`` is what must stand between the key and its ``=``: RetroArch
    takes the key as the whole graph run and then requires the ``=``, so
    ``key=value`` scans as one long key with nothing behind it and sets nothing
    at all (``config_file.c:596-623``, and :func:`atlas.retroarch_cfg.parse_cfg`
    after it) — a line written here keeps the space. ``ignore_case`` says
    whether the reader matches a key regardless of how it is spelled, which an
    emulator's own ini does and RetroArch does not: ``_parse_sectioned_ini``
    (``atlas/installations.py``) keeps the file's own spellings and
    :func:`atlas.qt_ini.simpleini_value` does the matching, ASCII
    case-insensitively on the section (``IniFile.cpp:130-146``, case-variant
    headers merged at ``:289``) and on the key (``IniFile.h:64``), at dolphin
    2603a. The vector
    ``dolphin-a-case-variant-spelling-of-the-slot-keys-governs`` is a fixture
    where ``slota`` governs. ``sectioned`` says whether the file divides its
    keys under ``[section]`` headers, which decides two things at once: a
    setting the file does not yet state can be appended to a flat cfg and not
    to a sectioned ini, where nothing in the answer says which section a new
    line belongs in, and a rewrite may touch every line of a flat cfg while it
    must stay inside one section of an ini.
    """

    separator: str
    ignore_case: bool
    sectioned: bool


RETROARCH_OPTIONS = OptionFile(separator="[ \t]+", ignore_case=False, sectioned=False)
EMULATOR_INI = OptionFile(separator="[ \t]*", ignore_case=True, sectioned=True)

# Which grammar an options file's suffix says it is written in — RetroArch's
# global cfg and its per-core, per-folder and per-game ``.opt`` layers on one
# side, an emulator's own sectioned ini on the other. Keyed on the suffix
# rather than on the names, because ``core_options_path`` lets a machine call
# the global file anything at all, and closed rather than defaulted: the
# corpus already carries readings in ``.yml``, ``.toml`` and ``.xml`` files,
# and an alternative that ever points at one has to arrive as a stated hole
# instead of a cfg line written into the middle of a YAML document.
GRAMMARS = {".cfg": RETROARCH_OPTIONS, ".opt": RETROARCH_OPTIONS, ".ini": EMULATOR_INI}

# A section header, matched against one line: everything an ini reader takes as
# the name is what stands between the brackets.
HEADER = re.compile(r"[ \t]*\[([^\]]*)\][ \t]*$")


class Unappliable(Exception):
    """An edit this fixture cannot state — the message is the exemption's reason."""


def grammar_of(path: str) -> OptionFile:
    """The grammar *path* is written in — refused where its suffix names none."""
    grammar = GRAMMARS.get(posixpath.splitext(path)[1].lower())
    if grammar is None:
        raise Unappliable(f"{path} is not written in a grammar this walk knows how to edit")
    return grammar


def _as_written(old: str, value: str) -> str:
    """*value* in the quoting the line's own value already had."""
    return f'"{value}"' if old.lstrip().startswith('"') else value


def _line_of(key: str, grammar: OptionFile) -> re.Pattern[str]:
    """The pattern a line stating *key* matches, spelled *grammar*'s way."""
    flags = re.IGNORECASE if grammar.ignore_case else 0
    return re.compile(rf"^([ \t]*{re.escape(key)}{grammar.separator}=)[ \t]*(.*)$", flags)


def _section_at(lines: list[str], index: int) -> str:
    """The section the line at *index* sits under, lowered — ``""`` above the first header."""
    for above in reversed(lines[: index + 1]):
        header = HEADER.match(above)
        if header is not None:
            return header[1].strip().lower()
    return ""


def _governed(lines: list[str], hits: list[int], grammar: OptionFile) -> list[int]:
    """Which of the matching lines the edit rewrites — all of them, or one section's."""
    if not grammar.sectioned:
        return hits
    first = _section_at(lines, hits[0])
    return [index for index in hits if _section_at(lines, index) == first]


def _set_line(text: str, key: str, value: str, grammar: OptionFile) -> str | None:
    """*text* with the lines stating *key* set to *value* — ``None`` where it states none.

    Which lines those are follows the grammar's own reader. A flat cfg has no
    sections, so a key stated twice is one setting written twice — RetroArch
    keeps the first (``config_file.c:496-507``) — and rewriting every matching
    line makes the edit say one thing however the file is read. A sectioned ini
    is a ``(section, key)`` map instead: ``_parse_sectioned_ini``
    (``atlas/installations.py``) builds it and :func:`atlas.qt_ini.simpleini_value`
    matches on the pair with the last occurrence winning, both ASCII
    case-insensitively, the way Dolphin's own reader does (``IniFile.h:64``,
    case-variant headers merged at ``IniFile.cpp:289``, at dolphin 2603a). Two
    sections stating one key are therefore two different settings — a
    constructed ``[Folders] Bios`` beside a ``[Filenames] Bios`` is the shape —
    so the rewrite stays inside the first section that states the key, and
    case-variant headers count as that one section.
    """
    line = _line_of(key, grammar)
    lines = text.split("\n")
    hits = [index for index, one in enumerate(lines) if line.match(one)]
    if not hits:
        return None
    for index in _governed(lines, hits, grammar):
        lines[index] = line.sub(
            lambda match: f"{match[1]} {_as_written(match[2], value)}", lines[index]
        )
    return "\n".join(lines)


def _stating(text: Any, path: str, key: str, value: str) -> str:
    """The text of *path* stating *key* = *value* — appended where no line states it.

    A path the fixture does not list arrives here as empty text, which is the
    truthful reading: a file that is not there states nothing, and setting an
    option in a flat cfg is what creates it. A path the fixture lists as a read
    failure is a different thing and cannot be edited at all.
    """
    if not isinstance(text, str):
        raise Unappliable(f"{path} holds no readable text in this fixture")
    grammar = grammar_of(path)
    replaced = _set_line(text, key, value, grammar)
    if replaced is not None:
        return replaced
    if grammar.sectioned:
        raise Unappliable(f"{path} states no line for {key!r} and names no section to write one in")
    padding = "" if not text or text.endswith("\n") else "\n"
    return f'{text}{padding}{key} = "{value}"\n'


def _files_stating(
    files: dict[str, Any], readings: dict[str, str | None], options: dict[str, str]
) -> dict[str, Any]:
    """*files*, with every ``(key, value)`` of *options* set where its reading says it lives."""
    edited = dict(files)
    for key, value in options.items():
        if key not in readings:
            raise Unappliable(f"the answer states no reading for {key!r}")
        path = readings[key]
        if path is None:
            raise Unappliable(f"the reading for {key!r} names no options file")
        edited[path] = _stating(edited.get(path, ""), path, key, value)
    return edited


@dataclass(frozen=True, slots=True)
class Hop:
    """One alternative of one answer, and what the resolver said once it was applied."""

    origin: str
    """The corpus answer this walk started from — ``vector id | question``."""
    standing: str | None
    """The mode in force where the alternative was published."""
    mode: str
    """The mode the alternative names."""
    edits: tuple[tuple[str, str, str | None], ...]
    """``(key, value, options file)`` per option the alternative names."""
    switches: tuple[str, ...]
    """The switches named by the alternatives of the answer this hop applies one of — the
    nearest thing to a card identity a serialized answer carries.
    """
    verdict: str
    """What the answer said instead; empty where it selected the mode named."""
    unappliable: str
    """Why the fixture could not state the edit; empty where it could."""

    @property
    def where(self) -> str:
        """The answer the alternative was published beside, named the way a reader finds it."""
        return f"{self.origin} | mode {self.standing!r}"

    @property
    def name(self) -> str:
        """This hop's place in the corpus, and an exemption's key.

        The edits are part of the name because the mode is not enough to tell
        two hops apart: one answer may publish two alternatives naming one mode
        through different option combinations, and an exemption keyed on the
        mode alone would silently cover both.
        """
        edits = " ".join(f"{key}={value}" for key, value, _ in self.edits)
        return f"{self.where} -> {self.mode} [{edits}]"

    def message(self) -> str:
        """The whole finding in one sentence — enough to fix the rule without the corpus."""
        edits = "; ".join(f"{key} = {value!r} in {path}" for key, value, path in self.edits)
        outcome = (
            f"this fixture cannot state that edit — {self.unappliable}"
            if self.unappliable
            else f"applied, the answer {self.verdict}"
        )
        return f"{self.where}: the alternative naming mode {self.mode!r} says to set {edits}; {outcome}"


def _undecided(answer: dict[str, Any]) -> set[str]:
    """Every ``core-mode-unestablished`` in *answer*, as the serialized data it carries."""
    return {
        json.dumps(data, sort_keys=True)
        for code, data in caveat_blocks(answer)
        if code == atlas.CAVEAT_CORE_MODE_UNESTABLISHED
    }


def _newly_undecided(answer: dict[str, Any], before: set[str]) -> str:
    """The mode-unestablished caveats *answer* added, as a clause — empty where it added none.

    A difference rather than an absence, because a fixture machine can stand in
    a state the rule already cannot decide: Dolphin's per-session card override
    is one, and it stays in force across an edit of the slot keys. An
    alternative is not answerable for a caveat the answer it was published
    beside already carried. It is answerable for adding one, and the caveat's
    own data is what says which rule gave up and why.
    """
    added = sorted(_undecided(answer) - before)
    return f", and newly could not establish the mode: {added}" if added else ""


def _verdict(answer: dict[str, Any], mode: str, before: set[str]) -> str:
    """What *answer* did instead of selecting *mode* — empty where it selected it."""
    if REFUSAL in answer:
        return f"refused with {answer[REFUSAL]['code']!r}"
    added = _newly_undecided(answer, before)
    granularity = answer.get("granularity")
    if granularity is None:
        return f"carried no granularity block at all{added}"
    if granularity["mode"] != mode:
        return f"selected mode {granularity['mode']!r}{added}"
    return f"selected the mode{added}" if added else ""


def _ask(state: dict[str, Any], question: str, where: str) -> dict[str, Any]:
    """The vector's own question, put to a machine rebuilt from *state* and serialized."""
    query_key, asker = QUESTIONS[question]
    installations = atlas.detect(state["home"], fixture_machine(state))
    return cast("dict[str, Any]", asker(installations, state[query_key], where))


def _readings_of(answer: dict[str, Any]) -> dict[str, str | None]:
    """Which file each switch of *answer* lives in, by the answer's own readings."""
    return {r["key"]: r["options_file"] for r in answer["granularity"]["readings"]}


def _alternatives_of(answer: dict[str, Any]) -> list[dict[str, Any]]:
    """The other modes *answer* publishes, each with the option combination that selects it."""
    return answer["granularity"]["alternatives"]


def _switches_of(answer: dict[str, Any]) -> tuple[str, ...]:
    """Every switch the alternatives of *answer* name, sorted."""
    return tuple(sorted({key for other in _alternatives_of(answer) for key in other["options"]}))


def _apply(
    origin: str,
    question: str,
    state: dict[str, Any],
    standing: dict[str, Any],
    alternative: dict[str, Any],
) -> tuple[Hop, tuple[dict[str, Any], dict[str, Any]] | None]:
    """One alternative written into the fixture and asked again.

    Returns the hop, and the state-and-answer it reached — ``None`` where the
    edit could not be written, so the walk has nowhere to continue from.
    """
    readings = _readings_of(standing)
    switches = _switches_of(standing)
    in_force = standing["granularity"]["mode"]
    named = alternative["mode"]
    edits = tuple((key, value, readings.get(key)) for key, value in alternative["options"].items())
    try:
        files = _files_stating(state["files"], readings, alternative["options"])
    except Unappliable as why:
        return Hop(origin, in_force, named, edits, switches, "", str(why)), None
    reached = copy.deepcopy(state)
    reached["files"] = files
    answer = _ask(reached, question, origin)
    verdict = _verdict(answer, named, _undecided(standing))
    return Hop(origin, in_force, named, edits, switches, verdict, ""), (reached, answer)


def _hops(
    origin: str,
    question: str,
    state: dict[str, Any],
    standing: dict[str, Any],
    applied: set[tuple[str, tuple[tuple[str, str], ...]]],
) -> Iterator[tuple[Hop, tuple[dict[str, Any], dict[str, Any]] | None]]:
    """Each alternative *standing* publishes that no earlier hop of this walk applied."""
    for alternative in _alternatives_of(standing):
        pair = (alternative["mode"], tuple(sorted(alternative["options"].items())))
        if pair in applied:
            continue
        applied.add(pair)
        yield _apply(origin, question, state, standing, alternative)


def _closure(
    origin: str, question: str, state: dict[str, Any], answer: dict[str, Any]
) -> Iterator[Hop]:
    """Every distinct ``(mode, edits)`` reachable from *answer* along its own alternatives.

    Bounded by the pairs already applied rather than by the modes already
    reached, which are two different bounds: an alternative names the *full*
    combination that selects its mode, so applying one pair a second time from
    further along the walk would set the same values again, while two
    alternatives naming one mode through different edits are two separate
    claims and both get checked.
    """
    applied: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    pending = [(state, answer)]
    while pending:
        standing_state, standing = pending.pop()
        for hop, reached in _hops(origin, question, standing_state, standing, applied):
            yield hop
            if reached is not None and not hop.verdict:
                pending.append(reached)


def _publishes(answer: dict[str, Any]) -> bool:
    """Whether *answer* is a placement that lists other modes a caller could switch to."""
    granularity = cast("dict[str, Any] | None", answer.get("granularity"))
    return isinstance(granularity, dict) and bool(granularity["alternatives"])


def questions_publishing_alternatives(expected: dict[str, Any]) -> Iterator[str]:
    """Each question of *expected* whose answer publishes alternatives.

    The corpus block selects the question; it is not the answer the walk runs
    on, which the resolver gives fresh. Only the expectations that are one
    answer are read: the aggregate question's are a list of them, and no
    vector's aggregate publishes an alternative today —
    ``test_the_walk_starts_from_every_alternative_the_corpus_publishes`` is
    what keeps that from becoming a silent skip the day one does.
    """
    for question, answer in expected.items():
        if question in QUESTIONS and isinstance(answer, dict) and _publishes(answer):
            yield question


def published_alternatives(node: Any) -> Iterator[dict[str, Any]]:
    """Every granularity block with alternatives anywhere under *node*.

    Recognised by its shape rather than by where it sits, the way
    :func:`tests.corpus.caveat_blocks` recognises a caveat: a counter keyed on
    position would agree with the enumerator above by construction and prove
    nothing about what the enumerator steps over.
    """
    if isinstance(node, dict):
        granularity = node.get("granularity")
        if isinstance(granularity, dict) and granularity["alternatives"]:
            yield cast("dict[str, Any]", granularity)
        for value in cast("dict[str, Any]", node).values():
            yield from published_alternatives(value)
    elif isinstance(node, list):
        for value in cast("list[Any]", node):
            yield from published_alternatives(value)


@dataclass(frozen=True, slots=True)
class Walk:
    """One pass over the corpus: the hops it made, and the answers that went quiet."""

    hops: tuple[Hop, ...]
    silent: tuple[str, ...]

    @property
    def applied(self) -> tuple[Hop, ...]:
        """The hops whose edit this fixture could state."""
        return tuple(hop for hop in self.hops if not hop.unappliable)

    @property
    def unappliable(self) -> tuple[Hop, ...]:
        """The hops whose edit it could not."""
        return tuple(hop for hop in self.hops if hop.unappliable)

    def coverage(self) -> dict[str, Any]:
        """What the walk reached — the numbers :data:`COVERAGE_FLOOR` pins."""
        modes: dict[tuple[str, ...], set[str]] = {}
        for hop in self.applied:
            modes.setdefault(hop.switches, set()).add(hop.mode)
        return {
            "answers": len({hop.origin for hop in self.hops} | set(self.silent)),
            "edits applied": len(self.applied),
            "switch sets": len(modes),
            "modes per switch set": {" ".join(s): sorted(reached) for s, reached in modes.items()},
        }


@lru_cache(maxsize=None)
def corpus() -> tuple[tuple[str, dict[str, Any]], ...]:
    """``(vector id, the vector)`` for the whole machines corpus, through the runner's loader."""
    return tuple(
        (str(param.id), cast("dict[str, Any]", param.values[0])) for param in load_vectors()
    )


@lru_cache(maxsize=None)
def walk() -> Walk:
    """The whole corpus walked once, on the first ask rather than at import.

    Cached rather than computed at module level so that an exception in the
    resolver is a failing test with a traceback, not a collection error that
    takes every check in this file with it.

    The state each walk starts from is the vector's machine, and the answer it
    starts from is what the resolver says about that machine right now, not
    what the expected block records. The two are the same thing while the
    runner is green, and the difference is the point: an alternative published
    only in a mode the corpus itself stands in would otherwise be read out of
    a frozen file and never asked of the code under test. Answers that go
    quiet are collected rather than dropped.
    """
    hops: list[Hop] = []
    silent: list[str] = []
    for vector_id, vector in corpus():
        for question in questions_publishing_alternatives(vector["expected"]):
            origin = f"{vector_id} | {question}"
            answer = _ask(vector["input"], question, origin)
            if _publishes(answer):
                hops.extend(_closure(origin, question, vector["input"], answer))
            else:
                silent.append(origin)
    return Walk(tuple(hops), tuple(silent))


# What the walk reached when it was written: 59 answers publishing alternatives,
# 454 edits applied, and 18 distinct switch sets among them — the switches named
# by the alternatives of the answer a hop applies one of, which is as close to a
# per-card count as a serialized answer allows. Floors rather than the
# measurements, because the corpus grows and a vector added tomorrow must not
# have to come with an edit here; what they catch is the opposite move, a walk
# that stops applying anything and passes green over an empty run.
COVERAGE_FLOOR = {"answers": 55, "edits applied": 400, "switch sets": 16}

# The names of edits no fixture machine can state. The reason each was refused
# is the walk's own message; the comment beside an entry is where a person says
# what would remove it.
#
# Empty, and measured so: every alternative the corpus publishes today names
# switches whose file the fixture either already states a line for or is a flat
# RetroArch cfg a line can simply be added to — including the nine Dolphin
# answers, whose fixtures all state the slot line the alternative edits. Five
# shapes would land here, one per refusal raised above: an options file whose
# suffix names no grammar; a path the fixture records as a read failure rather
# than as text; an emulator ini that states no line for the key, since nothing
# in the answer says which section a new one belongs in; an alternative naming
# an option the answer states no reading for; and a reading that names no
# options file at all. Each is a real hole in what the published alternative
# promises a client, so an entry added here names why and what would remove it.
UNAPPLIABLE_BY_FIXTURE: frozenset[str] = frozenset()


def test_every_alternative_selects_the_mode_it_names():
    # The claim itself. One test rather than one per hop, so that a resolver
    # that raises fails here with a traceback instead of aborting collection;
    # the assertion lists every miss at once, each message naming the vector,
    # the question, the mode in force, the edit, and what came back instead.
    # The messages ride as the assertion's own text, which pytest prints whole
    # where it elides a long list comparison.
    missed = sorted(hop.message() for hop in walk().applied if hop.verdict)
    assert missed == [], "\n".join(("", *missed))


def test_every_edit_the_corpus_cannot_state_is_a_named_one():
    # An alternative nothing can apply is an alternative nothing checks, so
    # its name stands in the exemption set or it fails right here; the reason
    # it was refused travels in the message below and in the comment beside
    # the entry.
    unnamed = [
        hop.message() for hop in walk().unappliable if hop.name not in UNAPPLIABLE_BY_FIXTURE
    ]
    assert unnamed == [], "\n".join(("", *unnamed))


def test_the_exemption_list_names_nothing_the_walk_now_applies():
    # An edit that became appliable must lose its exemption, or the list
    # becomes a place where coverage quietly goes to die.
    assert sorted(UNAPPLIABLE_BY_FIXTURE - {hop.name for hop in walk().unappliable}) == []


def test_the_walk_reaches_at_least_what_it_reached_when_it_was_written():
    measured = walk().coverage()
    short = {key: measured[key] for key, floor in COVERAGE_FLOOR.items() if measured[key] < floor}
    assert short == {}, f"the walk reached {measured}"


def test_no_answer_the_corpus_says_publishes_alternatives_goes_quiet():
    # The walk runs on what the resolver answers, not on the expected block,
    # so an answer that has lost its alternatives makes the walk smaller
    # rather than redder. Naming them keeps that from reading as green.
    assert list(walk().silent) == []


def test_the_walk_starts_from_every_alternative_the_corpus_publishes():
    # The enumerator reads the expected block key by key; this counts the same
    # blocks by shape, anywhere they sit. An answer shape it steps over — the
    # aggregate question's list of answers is the one that exists — is a claim
    # nobody would check while the suite stayed green.
    published = sum(len(list(published_alternatives(vector["expected"]))) for _, vector in corpus())
    assert walk().coverage()["answers"] == published


class TestAnEditStaysInsideTheSettingItMeans:
    """An ini states one key under many sections, and they are different settings.

    The flat-cfg rule is "rewrite every line", which is right where a repeated
    key is one setting written twice. Carried into a sectioned ini it would
    overwrite two unrelated settings at once — a directory and a filename that
    happen to share a key name — and the answer that came back would be about a
    machine nobody configured.
    """

    TWO_SECTIONS = "[Folders]\nBios = /old/folders\n\n[Filenames]\nBios = /old/filenames\n"

    def test_only_the_first_section_stating_the_key_is_rewritten(self):
        assert _set_line(self.TWO_SECTIONS, "Bios", "/new", EMULATOR_INI) == (
            "[Folders]\nBios = /new\n\n[Filenames]\nBios = /old/filenames\n"
        )

    def test_a_case_variant_header_is_the_same_section(self):
        # Dolphin merges case-variant headers (IniFile.cpp:289 at 2603a), so
        # the lines under both are one setting and both are rewritten.
        text = "[Folders]\nBios = /a\n[folders]\nBios = /b\n[Filenames]\nBios = /c\n"
        assert _set_line(text, "Bios", "/new", EMULATOR_INI) == (
            "[Folders]\nBios = /new\n[folders]\nBios = /new\n[Filenames]\nBios = /c\n"
        )

    def test_a_flat_cfg_still_rewrites_every_line(self):
        assert _set_line('k = "a"\nk = "b"\n', "k", "c", RETROARCH_OPTIONS) == 'k = "c"\nk = "c"\n'

    def test_a_file_stating_the_key_nowhere_is_no_edit(self):
        assert _set_line(self.TWO_SECTIONS, "Bezel", "/new", EMULATOR_INI) is None
