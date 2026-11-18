"""Whether a question put to every installation answers as it does put to one: the command line's two forms.

    mise run probe [--target <name>] answers_agree.py

For every installation ``detect`` finds, each question of :data:`QUESTIONS`
— the ones that take no content — is asked twice: put to every installation,
which answers with the labelled list, and with ``--installation <kind>``,
which answers with the bare answer of the first installation of that kind.
The bare answer is compared with that installation's entry of the list. A
second installation of a kind is named and left out: ``--installation`` never
answers for it. A kind ``detect`` reported that the labelled list leaves out
is a disagreement as well: atlas's own answers part there.

One line per question and kind says whether the two agree; where they do not,
the line names the places in the answer where they part, as paths into its
JSON, never the values there — a key that is no plain field name, which can be
a path or a name off the device, is shown as ``<key>``, and so is every key of
a ``data`` object. The probe ends with 0
when every pair agrees, 1 when one does not, and 2 when a question could not
be put: atlas refused it, answered with no JSON, or answered with a shape this
probe cannot read. A question whose comparison crashes is one not put, with
its traceback on stderr, and the questions after it are still asked; a crash
anywhere else ends the probe through the dispatcher's bootstrap, with 2 as
well.

atlas is asked in this process, through ``atlas.cli.run``: under the
dispatcher's bootstrap ``atlas`` is importable from the copied tree in this
process only, not in a child it would start. Nothing here writes.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import traceback
from typing import Any, Callable, NamedTuple

QUESTIONS = ("health", "systems", "rom-locations", "firmware-inventory")
"""Every question both forms put that needs no content, no system and no core."""

SHOWN_PATHS = 5
"""How many places a disagreeing line names before it counts the rest."""

# The probe's statuses, in rising order of what they say is wrong: a run's
# status is the highest any of its lines reached (max), so one question not
# put outweighs any number that agree or part.
AGREE = 0
DISAGREE = 1
NOT_PUT = 2

FIELD_NAME = re.compile(r"[a-z][a-z0-9_]*", re.ASCII)
"""A key printed as it is in a path: a plain field name of the contract. Any other key stands as ``<key>``, and so
does every key anywhere below a ``data`` object, whose keys a caveat can take from the device."""

UNREADABLE = "an answer put to every installation with a shape this probe cannot read"

MISSING = object()
"""What a labelled answer holds for a kind it has no entry of."""

NO_ANSWER = object()
"""What :attr:`Asked.answer` holds where atlas gave none: it refused the question, or what it wrote is no JSON."""


class Asked(NamedTuple):
    """What atlas made of one command line."""

    status: int
    """atlas's own exit status."""
    answer: Any
    """The answer parsed, or :data:`NO_ANSWER`."""
    said: str
    """The last line atlas wrote on stderr — where an error ends — or why there is no answer; empty for nothing."""

    @property
    def answered(self) -> bool:
        return self.status == 0 and self.answer is not NO_ANSWER


Asker = Callable[[list[str]], Asked]
"""Ask atlas the command line it is given."""


def exit_status(stop: SystemExit) -> int:
    """The status a ``SystemExit`` ends a process with, as Python reads its code: ``None`` is 0, text is 1."""
    if stop.code is None:
        return 0
    return stop.code if isinstance(stop.code, int) else 1


def ask_atlas(argv: list[str]) -> Asked:
    """*argv* put to ``atlas.cli.run`` in this process, its stdout and stderr kept."""
    from atlas.cli import run

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            status = run(argv)
        except SystemExit as stop:
            status = exit_status(stop)
    said = next(iter(reversed(err.getvalue().strip().splitlines())), "")
    if status != 0:
        return Asked(status, NO_ANSWER, said)
    try:
        return Asked(0, json.loads(out.getvalue()), said)
    except ValueError:
        return Asked(0, NO_ANSWER, "its answer is no JSON")


def shown_key(key: str, *, in_data: bool) -> str:
    return key if FIELD_NAME.fullmatch(key) and not in_data else "<key>"


def differences(left: Any, right: Any, path: str = "") -> list[str]:
    """Where *left* and *right* part: a path into the JSON for each place, ``""`` for the whole."""
    if type(left) is not type(right):
        return [path]
    if isinstance(left, dict):
        return _dict_differences(left, right, path)
    if isinstance(left, list):
        if len(left) != len(right):
            return [f"{path}[] (lengths)"]
        pairs = enumerate(zip(left, right))
        return [found for index, pair in pairs for found in differences(*pair, f"{path}[{index}]")]
    return [] if left == right else [path]


def _dict_differences(left: dict[str, Any], right: dict[str, Any], path: str) -> list[str]:
    found: list[str] = []
    in_data = any(part.split("[")[0] == "data" for part in path.split("."))
    for key in sorted(set(left) | set(right)):
        place = f"{path}.{shown_key(key, in_data=in_data)}"
        if key in left and key in right:
            found += differences(left[key], right[key], place)
        else:
            found.append(f"{place} (in one only)")
    return found


def verdict(question: str, kind: str, paths: list[str]) -> str:
    """The line for one question and one kind."""
    if not paths:
        return f"{question} {kind}: agree"
    shown = ", ".join(path or "(the whole answer)" for path in paths[:SHOWN_PATHS])
    more = f" and {len(paths) - SHOWN_PATHS} more" if len(paths) > SHOWN_PATHS else ""
    return f"{question} {kind}: differ at {shown}{more}"


def not_put(what: str, asked: Asked) -> str:
    """The line for a command line atlas did not answer."""
    return f"{what}: could not be put (atlas ended with {asked.status}{': ' + asked.said if asked.said else ''})"


def entries(labelled: Any) -> list[tuple[str, Any]] | None:
    """The labelled list as (kind, answer) pairs, in its order; ``None`` where its shape cannot be read."""
    if not isinstance(labelled, list):
        return None
    pairs = []
    for item in labelled:
        if not isinstance(item, dict) or not isinstance(item.get("installation"), dict) or "answer" not in item:
            return None
        if not isinstance(item["installation"].get("kind"), str):
            return None
        pairs.append((item["installation"]["kind"], item["answer"]))
    return pairs


def compare_question(ask: Asker, question: str, kinds: list[str]) -> tuple[int, list[str]]:
    """*question* in both forms for each of *kinds*: the highest status among them, and one line each."""
    asked = ask([question])
    if not asked.answered:
        return NOT_PUT, [not_put(f"{question} put to every installation", asked)]
    labelled = entries(asked.answer)
    if labelled is None:
        return NOT_PUT, [f"{question}: {UNREADABLE}"]
    worst, lines = AGREE, []
    for kind in kinds:
        status, line = compare_kind(ask, question, kind, labelled)
        worst = max(worst, status)
        lines.append(line)
    return worst, lines


def compare_kind(ask: Asker, question: str, kind: str, labelled: list[tuple[str, Any]]) -> tuple[int, str]:
    """*question* put with ``--installation`` *kind*, against that kind's first entry of the *labelled* list."""
    entry = next((answer for each, answer in labelled if each == kind), MISSING)
    if entry is MISSING:
        return DISAGREE, f"{question} {kind}: the answer put to every installation holds none of this kind"
    asked = ask([question, "--installation", kind])
    if not asked.answered:
        return NOT_PUT, not_put(f"{question} {kind} with --installation", asked)
    paths = differences(entry, asked.answer)
    return (DISAGREE if paths else AGREE), verdict(question, kind, paths)


def compare_or_crash(ask: Asker, question: str, kinds: list[str]) -> tuple[int, list[str]]:
    """:func:`compare_question`, a crash in it a question not put: its traceback on stderr, its type in the line."""
    try:
        return compare_question(ask, question, kinds)
    except Exception as error:
        traceback.print_exc()
        return NOT_PUT, [f"{question}: could not be put (the comparison crashed: {type(error).__name__})"]


def compare(ask: Asker) -> tuple[int, list[str]]:
    """Every question of :data:`QUESTIONS` in both forms, for the first installation of each kind; status and lines."""
    asked = ask(["detect"])
    if not asked.answered:
        return NOT_PUT, [not_put("detect", asked)]
    installations = asked.answer
    if not isinstance(installations, list) or not all(
        isinstance(item, dict) and isinstance(item.get("kind"), str) for item in installations
    ):
        return NOT_PUT, ["detect: an answer with a shape this probe cannot read"]
    kinds = list(dict.fromkeys(item["kind"] for item in installations))
    if not kinds:
        return AGREE, ["no installation detected; nothing to compare"]
    lines = [
        f"{kind}: {count - 1} more installation(s) of this kind left out; --installation answers for the first only"
        for kind, count in ((kind, sum(item["kind"] == kind for item in installations)) for kind in kinds)
        if count > 1
    ]
    worst = AGREE
    for question in QUESTIONS:
        status, found = compare_or_crash(ask, question, kinds)
        worst = max(worst, status)
        lines += found
    return worst, lines


def main(argv: list[str]) -> int:
    if argv:
        print(f"answers_agree.py takes no arguments, not {argv}", file=sys.stderr)
        return NOT_PUT
    status, lines = compare(ask_atlas)
    for line in lines:
        print(line)
    summary = {AGREE: "every answer agrees", DISAGREE: "answers differ", NOT_PUT: "a question could not be put"}
    print(f"answers_agree: {summary[status]}.")
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
