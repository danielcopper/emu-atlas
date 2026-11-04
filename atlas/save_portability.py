"""Whether one entry's save can be read by another — the save-portability question (#577).

A client that switches a game from one emulator to another needs to know
whether the save carries over. The two entries' save answers already say where
each keeps the game's save and which files make it up; this answer adds what
those cannot: whether the target reads what the source wrote.

The question is directed — ``target`` reads what ``source`` wrote — and is
answered **per part** of the save (#577 D2): a memory card in each slot, the
console's backup RAM, a backup cartridge. Each part names its
memory with a word of its own (:data:`~atlas.save_formats.SAVE_MEMORIES`) and,
where the system has several, its slot, beside the file group each entry's
save answer states for it (D6); the summary is the most severe part (D9).

Nothing here re-derives a placement. Both save answers are asked as they
stand, with the live mode each one read, and the packaged format records
(:mod:`atlas.save_formats`) label their file groups — so a live mode (Kronos's
Beetle-saves switch, LRPS2's card mode, SwanStation's twenty slot
combinations) arrives through the save answer, never through a reading of
this module's own. A verdict built on a source holds only where the machine names the build
the source was read at (D5); elsewhere the part answers ``unestablished`` and
says why.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Callable, Literal, Mapping, Protocol

from .placement import (
    CAVEAT_SAVE_FILES_COMPRESSED,
    GRANULARITY_SHARED_CARD,
    GRANULARITY_SHARED_FILE,
    ROLE_UNKNOWN,
    Caveat,
    DataValue,
    FileGroup,
    SavefilePlacement,
    Unresolved,
)
from .portability_codes import (
    CAVEAT_ENTRY_LABEL_UNKNOWN,
    CAVEAT_SAVE_ANSWER_UNRESOLVED,
    CAVEAT_SAVE_FORMAT_BUILD_UNCONFIRMED,
    CAVEAT_SAVE_FORMAT_UNRECORDED,
    CAVEAT_SAVE_PART_UNESTABLISHED,
    REASON_BUILD_UNREAD,
    REASON_NO_CONVERSION_STATED,
    REASON_OTHER_BUILD,
    REASON_TARGET_FILE_UNNAMED,
    SIDE_SOURCE,
    SIDE_TARGET,
)
from .save_formats import (
    CONVERSION_RULES,
    SAVE_MEMORIES,
    EmulatorFormats,
    PartRule,
    SaveFormats,
    SystemFormats,
    save_formats,
)



class PortabilityEntry(Protocol):
    """What the computation reads off a catalogue entry — :class:`atlas.installations.EmulatorEntry` is one."""

    @property
    def label(self) -> str: ...

    @property
    def core_so(self) -> str | None: ...

    @property
    def emulator(self) -> str | None: ...

PortabilityVerdict = Literal["same-files", "same-data", "convertible", "unestablished", "not-readable"]
PORTABILITY_SAME_FILES: PortabilityVerdict = "same-files"
"""The target reads the source's files where they lie, under their names: nothing has to move."""
PORTABILITY_SAME_DATA: PortabilityVerdict = "same-data"
"""The target reads the same data once it is placed and named as the target's own save answer names it; ``moves``
says what differs."""
PORTABILITY_CONVERTIBLE: PortabilityVerdict = "convertible"
"""The target reads the data only after a conversion a source states; ``conversion`` carries the rule where it is
plain, and is ``null`` where a program performs it without one."""
PORTABILITY_UNESTABLISHED: PortabilityVerdict = "unestablished"
"""Nothing atlas read at a build it confirmed here settles whether the target reads it; the caveats say why."""
PORTABILITY_NOT_READABLE: PortabilityVerdict = "not-readable"
"""The target keeps no such part in its live mode, so this part of the save does not carry over."""
# The verdicts a part and a pair take, from the mildest to the most severe —
# the order the summary is taken in (#577 D9).
PORTABILITY_VERDICTS = (
    PORTABILITY_SAME_FILES,
    PORTABILITY_SAME_DATA,
    PORTABILITY_CONVERTIBLE,
    PORTABILITY_UNESTABLISHED,
    PORTABILITY_NOT_READABLE,
)

SaveMove = Literal["directory", "name", "granularity"]
MOVE_DIRECTORY: SaveMove = "directory"
"""The target keeps the part in another directory."""
MOVE_NAME: SaveMove = "name"
"""The target keeps the part under other file names."""
MOVE_GRANULARITY: SaveMove = "granularity"
"""One side keeps the part shared by every game and the other per game, so copying one game's file over a shared one
overwrites every other game's saves in it (#577 D11)."""
# What differs between two parts of the same data, the vocabulary of ``moves``.
SAVE_MOVES = (MOVE_DIRECTORY, MOVE_NAME, MOVE_GRANULARITY)


@dataclass(frozen=True, slots=True)
class Conversion:
    """A conversion a source pins as a plain rule (#577 D4); atlas states it and converts nothing."""

    rule: str
    """The rule, one of :data:`~atlas.save_formats.CONVERSION_RULES`."""
    length: int
    """The byte count the rule names."""

    def __post_init__(self) -> None:
        if self.rule not in CONVERSION_RULES:
            raise ValueError(f"Conversion: rule must be one of {CONVERSION_RULES}, got {self.rule!r}")


@dataclass(frozen=True, slots=True)
class SavePart:
    """One part of the source's save, and whether the target reads it."""

    memory: str
    """What this part holds, one of :data:`~atlas.save_formats.SAVE_MEMORIES`."""
    slot: str | None
    """The port the emulators name this part's memory by (``"1"``, ``"2"``), and ``None`` where the system has one."""
    verdict: str
    """Whether the target reads this part, one of :data:`PORTABILITY_VERDICTS`."""
    source_group: FileGroup
    """The source save answer's file group for this part, narrowed to the files that are this part."""
    target_group: FileGroup | None
    """The target save answer's file group for the same memory and slot, narrowed the same way, and ``None`` where
    that answer states none."""
    moves: tuple[SaveMove, ...] = ()
    """On a same-data verdict, what differs between the two groups, each one of :data:`SAVE_MOVES`; empty on every
    other verdict."""
    conversion: Conversion | None = None
    """On a convertible verdict whose conversion a source pins as a plain rule, that rule; ``None`` everywhere else."""

    def __post_init__(self) -> None:
        if self.memory not in SAVE_MEMORIES:
            raise ValueError(f"SavePart: memory must be one of {SAVE_MEMORIES}, got {self.memory!r}")
        if self.verdict not in PORTABILITY_VERDICTS:
            raise ValueError(f"SavePart: verdict must be one of {PORTABILITY_VERDICTS}, got {self.verdict!r}")
        if any(move not in SAVE_MOVES for move in self.moves):
            raise ValueError(f"SavePart: moves must be drawn from {SAVE_MOVES}, got {self.moves!r}")
        if bool(self.moves) != (self.verdict == PORTABILITY_SAME_DATA):
            raise ValueError("SavePart: moves stand exactly where the verdict is same-data")
        if self.conversion is not None and self.verdict != PORTABILITY_CONVERTIBLE:
            raise ValueError("SavePart: a conversion stands only on a convertible verdict")


# --- the computation ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PortabilityOutcome[EntryT: PortabilityEntry]:
    """What :func:`portability_answer` settles, before the installation states it as an answer.

    The answer type itself lives beside the catalogue entry it names
    (:class:`atlas.installations.SavePortabilityAnswer`), as every answer that
    names an entry does; this carries the same fields over the entry type the
    computation was handed.
    """

    verdict: str
    source: EntryT | None
    target: EntryT | None
    parts: tuple[SavePart, ...] = ()
    sources: tuple[str, ...] = ()
    caveats: tuple[Caveat, ...] = ()


@dataclass(frozen=True, slots=True)
class _Labelled:
    """One part of one side's save: what holds it and the narrowed group the answer states for it."""

    rule: PartRule
    group: FileGroup


@dataclass(frozen=True, slots=True)
class _Side:
    """Everything one side contributes: its answer's parts, its record, and whether its build is confirmed."""

    name: str
    placement: SavefilePlacement
    record: SystemFormats | None
    parts: tuple[_Labelled, ...]
    confirmed: bool
    caveats: tuple[Caveat, ...]
    sources: tuple[str, ...]
    identity: _Identity

    @property
    def mode(self) -> str | None:
        return _mode(self.placement)


def _core_key(core_so: str) -> str:
    """A core's short name, the key its records and cards are filed under."""
    return core_so.rsplit("/", 1)[-1].removesuffix(".so").removesuffix("_libretro")


def _mode(placement: SavefilePlacement) -> str | None:
    """The live mode the save answer read, or ``None`` where it states none."""
    return placement.granularity.mode if placement.granularity is not None else None


def _readings(placement: SavefilePlacement) -> dict[str, str]:
    """The answer's option readings that state a value, keyed by the option."""
    if placement.granularity is None:
        return {}
    return {r.key: r.value for r in placement.granularity.readings if r.value is not None}


def _claims(
    rules: tuple[PartRule, ...], name: str, readings: Mapping[str, str], *, folder: bool
) -> tuple[PartRule, ...]:
    """The rules that claim *name*: every closest match, so two slots on one card file are both its parts."""
    scored = [
        (score, rule)
        for rule in rules
        if rule.folder == folder and (score := rule.specificity(name, readings)) is not None
    ]
    if not scored:
        return ()
    best = max(score for score, _ in scored)
    return tuple(rule for score, rule in scored if score == best)


def _group_parts(
    group: FileGroup, rules: tuple[PartRule, ...], readings: Mapping[str, str], names: tuple[str, ...]
) -> tuple[tuple[_Labelled, ...], bool]:
    """The parts one group holds among *names*, and whether a name in it is one no rule claims."""
    if group.files is None:
        claimed = _claims(rules, group.dir.rstrip("/").rsplit("/", 1)[-1], readings, folder=True)
        return tuple(_Labelled(rule, group) for rule in claimed), not claimed
    by_rule: dict[PartRule, list[str]] = {}
    unmatched = False
    for name in names:
        claimed = _claims(rules, name, readings, folder=False)
        unmatched = unmatched or not claimed
        for rule in claimed:
            by_rule.setdefault(rule, []).append(name)
    return (
        tuple(_Labelled(rule, dataclasses.replace(group, files=tuple(names))) for rule, names in by_rule.items()),
        unmatched,
    )


@dataclass(frozen=True, slots=True)
class _Labelling:
    """One save answer's parts, whether some file stayed unlabelled, and the citations of what was set aside."""

    parts: tuple[_Labelled, ...]
    unmatched: bool
    set_aside: tuple[str, ...]


def _labelled(placement: SavefilePlacement, record: SystemFormats) -> _Labelling:
    """Every part one save answer states, labelled by its record, beside what the record sets aside and why."""
    mode = _mode(placement)
    rules = tuple(rule for rule in record.parts if rule.applies(mode))
    readings = _readings(placement)
    parts: list[_Labelled] = []
    unmatched = False
    set_aside: list[str] = []
    for group in placement.file_set.groups:
        if group.role == ROLE_UNKNOWN:
            continue
        names, cited = _kept_names(group, record)
        set_aside.extend(cited)
        if names is None:
            continue
        found, missed = _group_parts(group, rules, readings, names)
        parts.extend(found)
        unmatched = unmatched or missed
    return _Labelling(tuple(parts), unmatched, tuple(dict.fromkeys(set_aside)))


def _kept_names(group: FileGroup, record: SystemFormats) -> tuple[tuple[str, ...] | None, tuple[str, ...]]:
    """The names of *group* the record keeps — ``None`` where it sets the group aside whole — and the citations of what
    it set aside."""
    whole = next((ignored for ignored in record.ignore if ignored.covers(group.dir)), None)
    if whole is not None:
        return None, (whole.citation,)
    names: list[str] = []
    cited: list[str] = []
    for name in group.files or ():
        by_name = next((ignored for ignored in record.ignore if ignored.claims(name)), None)
        if by_name is None:
            names.append(name)
        else:
            cited.append(by_name.citation)
    if group.files is not None and not names:
        return None, tuple(cited)
    return tuple(names), tuple(cited)


@dataclass(frozen=True, slots=True)
class _Identity:
    """What a caveat names one side's emulator by: a core's short name, or a card's token.

    Neither is the launch identity (``emulator``), which the answer's entry
    carries itself. A standalone emulator no record covers is named by neither
    — its token is a card's only where a card states it — and the caveat's
    ``side`` points at the entry that says which it is.
    """

    core: str | None = None
    token: str | None = None

    @property
    def named(self) -> str:
        return self.core or self.token or "its emulator"


def _identity(entry: PortabilityEntry, formats: EmulatorFormats | None) -> _Identity:
    if entry.core_so is not None:
        return _Identity(core=_core_key(entry.core_so))
    return _Identity(token=formats.key if formats is not None else None)


def _unrecorded(side: str, identity: _Identity, mode: str | None) -> Caveat:
    # Each key is stored by name, so a reading of this site sees every key the
    # caveat can carry (tests/test_caveat_data_names_no_emulator_tripwire.py).
    data: dict[str, DataValue] = {"side": side}
    core, token = identity.core, identity.token
    if core is not None:
        data["core"] = core
    if token is not None:
        data["token"] = token
    if mode is not None:
        data["mode"] = mode
    return Caveat(
        CAVEAT_SAVE_FORMAT_UNRECORDED,
        f"no packaged save-format record tells apart the {side} entry's save as its answer states it "
        f"({identity.named}, mode {mode or 'none'}) — what its files hold is not established here",
        data,
    )


def _build_statement(
    formats: EmulatorFormats, running: str | None, side: str, identity: _Identity
) -> tuple[bool, Caveat | None, str]:
    """Whether the machine names the build the record was read at, the caveat where it does not, and the sentence."""
    build = formats.build
    if running is not None and running in build.pins:
        kind = {
            "commit": f"which names the commit {build.revision} the record was read at",
            "release": f"a release string only, which names no commit; the record was read at {build.revision}",
            "channel": f"a channel name only, which follows the newest build; the record was read at {build.revision}",
        }[build.names]
        return True, None, f"{formats.key}: this machine names the build {running!r}, {kind}"
    data: dict[str, DataValue] = {"side": side}
    core, token = identity.core, identity.token
    if core is not None:
        data["core"] = core
    if token is not None:
        data["token"] = token
    data["pinned"] = build.pins
    data["reason"] = REASON_BUILD_UNREAD if running is None else REASON_OTHER_BUILD
    if running is not None:
        data["running"] = running
    message = (
        f"nothing here names the build of {formats.key!r}, so the {side} side's save formats, read at "
        f"{', '.join(build.pins)}, are not confirmed on this machine"
        if running is None
        else f"this machine runs {formats.key!r} {running!r}, and its save formats were read at "
        f"{', '.join(build.pins)} — a verdict is not carried across the difference"
    )
    return False, Caveat(CAVEAT_SAVE_FORMAT_BUILD_UNCONFIRMED, message, data), ""


def _side(
    name: str,
    entry: PortabilityEntry,
    placement: SavefilePlacement,
    *,
    system: str,
    table: SaveFormats,
    running: Callable[[], str | None],
    token: Callable[[], str | None],
) -> _Side:
    """One side's answer, labelled and weighed against its record and its build.

    A core's record is filed under its short name, a standalone emulator's under
    the card token the arrangement's own reading of the command gives — the
    token its save answer found the card by.
    """
    key = _core_key(entry.core_so) if entry.core_so is not None else token()
    formats = table.emulators.get(key or "")
    record = formats.systems.get(system) if formats is not None else None
    identity = _identity(entry, formats)
    if formats is None or record is None:
        unrecorded = _unrecorded(name, identity, _mode(placement))
        return _Side(name, placement, None, (), False, (unrecorded,), (), identity)
    labelling = _labelled(placement, record)
    confirmed, unconfirmed, sentence = _build_statement(formats, running(), name, identity)
    caveats = tuple(
        caveat
        for caveat in (
            unconfirmed,
            _unrecorded(name, identity, _mode(placement)) if labelling.unmatched else None,
        )
        if caveat is not None
    )
    # What the record sets aside is said where the answer says what it rests
    # on: a part left out is never a silent drop.
    sources = (*((sentence,) if sentence else ()), *labelling.set_aside)
    return _Side(name, placement, record, labelling.parts, confirmed, caveats, sources, identity)


def _shared(group: FileGroup) -> bool:
    """Whether a group holds every game's data rather than one game's."""
    return group.granularity in (GRANULARITY_SHARED_CARD, GRANULARITY_SHARED_FILE)


def _moves(source: FileGroup, target: FileGroup) -> tuple[SaveMove, ...]:
    """What differs between two groups of the same data, in :data:`SAVE_MOVES` order."""
    differences: tuple[tuple[SaveMove, bool], ...] = (
        (MOVE_DIRECTORY, source.dir != target.dir),
        (MOVE_NAME, source.files != target.files),
        (MOVE_GRANULARITY, _shared(source) != _shared(target)),
    )
    return tuple(move for move, differs in differences if differs)


def _keeps(target: _Side, part: _Labelled) -> bool:
    """Whether the target's live mode keeps this memory and slot, whatever its answer happens to name."""
    if target.record is None:
        return False
    return any(
        rule.memory == part.rule.memory and rule.slot == part.rule.slot and rule.applies(target.mode)
        for rule in target.record.parts
    )


@dataclass(frozen=True, slots=True)
class _Judgement:
    """One part's verdict with what rides it: the moves, why it is unestablished, and the extra citation."""

    verdict: str
    moves: tuple[SaveMove, ...] = ()
    unestablished: str | None = None
    cited: str | None = None


def _compressed(side: _Side, part: _Labelled) -> bool:
    """Whether RetroArch wrote this part compressed: its file is RetroArch's and the save answer says it compresses."""
    return part.rule.frontend and any(c.code == CAVEAT_SAVE_FILES_COMPRESSED for c in side.placement.caveats)


def _judged(
    part: _Labelled, twin: _Labelled | None, *, source: _Side, target: _Side, table: SaveFormats
) -> _Judgement:
    """One part's verdict, both builds confirmed."""
    if twin is None:
        # The target's mode keeps the part but its answer names no file for it
        # (a declared set that lists it only as an observation candidate, an
        # observed one whose file is not written yet): where it would lie is
        # not established. Otherwise the target keeps none (#577 D8).
        if _keeps(target, part):
            return _Judgement(PORTABILITY_UNESTABLISHED, unestablished=REASON_TARGET_FILE_UNNAMED)
        return _Judgement(PORTABILITY_NOT_READABLE)
    if part.rule.format != twin.rule.format:
        conversion = table.conversion(part.rule.format, twin.rule.format)
        if conversion is None:
            return _Judgement(PORTABILITY_UNESTABLISHED, unestablished=REASON_NO_CONVERSION_STATED)
        return _Judgement(PORTABILITY_CONVERTIBLE, cited=conversion.citation)
    if _compressed(source, part) and not twin.rule.frontend:
        # RetroArch reads its own RZIP stream back and an uncompressed file
        # too, so a core that RetroArch loads the part for reads it either
        # way; an emulator that opens the file itself reads compressed bytes.
        return _Judgement(PORTABILITY_CONVERTIBLE, cited=table.compressed)
    moves = _moves(part.group, twin.group)
    return _Judgement(PORTABILITY_SAME_DATA, moves) if moves else _Judgement(PORTABILITY_SAME_FILES)


def _part_unestablished(part: _Labelled, reason: str) -> Caveat:
    data: dict[str, DataValue] = {"memory": part.rule.memory}
    slot = part.rule.slot
    if slot is not None:
        data["slot"] = slot
    data["reason"] = reason
    return Caveat(
        CAVEAT_SAVE_PART_UNESTABLISHED,
        f"whether the target reads the source's {part.rule.memory}{f' {slot}' if slot else ''} is not established "
        f"({reason})",
        data,
    )


def _part(
    part: _Labelled, source: _Side, target: _Side, table: SaveFormats
) -> tuple[SavePart, tuple[str, ...], Caveat | None]:
    """One source part against the target, with the citations its verdict rests on and the caveat it raises."""
    twin = next(
        (t for t in target.parts if (t.rule.memory, t.rule.slot) == (part.rule.memory, part.rule.slot)),
        None,
    )
    if source.confirmed and target.confirmed and target.record is not None:
        judgement = _judged(part, twin, source=source, target=target, table=table)
    else:
        # The build caveat or the unrecorded one already says why.
        judgement = _Judgement(PORTABILITY_UNESTABLISHED)
    cited = (part.rule.citation, *((twin.rule.citation,) if twin is not None else ()), judgement.cited or "")
    raised = None if judgement.unestablished is None else _part_unestablished(part, judgement.unestablished)
    return (
        SavePart(
            memory=part.rule.memory,
            slot=part.rule.slot,
            verdict=judgement.verdict,
            source_group=part.group,
            target_group=twin.group if twin is not None else None,
            moves=judgement.moves,
        ),
        tuple(c for c in cited if c),
        raised,
    )


def _summary(parts: tuple[SavePart, ...], *, settled: bool) -> str:
    """The most severe part (#577 D9) — no milder than unestablished where something stayed unsettled."""
    floor = PORTABILITY_SAME_FILES if settled and parts else PORTABILITY_UNESTABLISHED
    return max((floor, *(p.verdict for p in parts)), key=PORTABILITY_VERDICTS.index)


def _label_unknown(system: str, side: str, label: str) -> Caveat:
    return Caveat(
        CAVEAT_ENTRY_LABEL_UNKNOWN,
        f"no catalogue entry of system {system!r} is labelled {label!r} — the {side} side names no entry",
        {"system": system, "side": side, "label": label},
    )


def _unresolved(side: str, outcome: Unresolved) -> Caveat:
    return Caveat(
        CAVEAT_SAVE_ANSWER_UNRESOLVED,
        f"the {side} entry's save question refused ({outcome.code}), so there are no files to compare",
        {"side": side, "code": outcome.code},
    )


def portability_answer[EntryT: PortabilityEntry](
    system: str,
    *,
    source: str,
    target: str,
    entries: tuple[EntryT, ...],
    ask: Callable[[EntryT], SavefilePlacement | Unresolved],
    running: Callable[[EntryT], str | None],
    token: Callable[[EntryT], str | None],
    caveats: tuple[Caveat, ...] = (),
    table: SaveFormats | None = None,
) -> PortabilityOutcome[EntryT]:
    """Whether the entry labelled *target* reads what the one labelled *source* wrote.

    *ask* is the entry's own save question with the caller's arguments, and
    *running* the build the machine names for an entry — a core's
    ``library_version``, a standalone emulator's deploy record — or ``None``
    where nothing names it. *token* is the card token a standalone entry's
    command identifies on this arrangement. *caveats* are the installation's own, which every
    answer on it carries.
    """
    table = save_formats() if table is None else table
    # The first entry a label names, as the launch command takes it.
    named: dict[str, EntryT] = {}
    for entry in entries:
        named.setdefault(entry.label, entry)
    source_entry, target_entry = named.get(source), named.get(target)
    if source_entry is None or target_entry is None:
        missing = tuple(
            _label_unknown(system, side, label)
            for side, label, entry in ((SIDE_SOURCE, source, source_entry), (SIDE_TARGET, target, target_entry))
            if entry is None
        )
        return PortabilityOutcome(PORTABILITY_UNESTABLISHED, source_entry, target_entry, caveats=(*caveats, *missing))
    source_placement, target_placement = ask(source_entry), ask(target_entry)
    if not isinstance(source_placement, SavefilePlacement) or not isinstance(target_placement, SavefilePlacement):
        refused = tuple(
            _unresolved(side, outcome)
            for side, outcome in ((SIDE_SOURCE, source_placement), (SIDE_TARGET, target_placement))
            if isinstance(outcome, Unresolved)
        )
        return PortabilityOutcome(PORTABILITY_UNESTABLISHED, source_entry, target_entry, caveats=(*caveats, *refused))
    sides = (
        _side(SIDE_SOURCE, source_entry, source_placement, system=system, table=table,
              running=lambda: running(source_entry), token=lambda: token(source_entry)),
        _side(SIDE_TARGET, target_entry, target_placement, system=system, table=table,
              running=lambda: running(target_entry), token=lambda: token(target_entry)),
    )
    judged = tuple(_part(part, sides[0], sides[1], table) for part in sides[0].parts)
    parts = tuple(part for part, _, _ in judged)
    side_caveats = (*sides[0].caveats, *sides[1].caveats, *(c for _, _, c in judged if c is not None))
    if not parts and not side_caveats:
        # The source's answer states nothing any rule of its record names — a
        # mode the save answer could not establish, or one that keeps no save
        # at all — so there is no part to judge, and the answer says so
        # rather than standing empty.
        side_caveats = (_unrecorded(SIDE_SOURCE, sides[0].identity, sides[0].mode),)
    cited = tuple(dict.fromkeys((*sides[0].sources, *sides[1].sources, *(c for _, cs, _ in judged for c in cs))))
    return PortabilityOutcome(
        verdict=_summary(parts, settled=not side_caveats),
        source=source_entry,
        target=target_entry,
        parts=parts,
        sources=cited,
        caveats=(*caveats, *side_caveats),
    )
