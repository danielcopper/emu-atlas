"""Whether a question put to every installation answers as it does put to one: the command line's two forms.

    mise run probe [--target <name>] answers_agree.py

For every installation ``detect`` finds, each question of :data:`QUESTIONS`
— the ones that take no content — is asked twice: put to every installation,
which answers with the labelled list, and with ``--installation <kind>``,
which answers with the bare answer of the first installation of that kind.
The bare answer is compared with that installation's entry of the list. A
second installation of a kind is named and left out: ``--installation`` never
answers for it.

One line per question and kind says whether the two agree; where they do not,
the line names the places in the answer where they part, as paths into its
JSON, never the values there. The probe ends with 0 when every pair agrees, 1
when one does not, and 2 when a question could not be put at all.

atlas is asked in this process, through ``atlas.cli.run``: under the
dispatcher's bootstrap ``atlas`` is importable from the copied tree in this
process only, not in a child it would start. Nothing here writes.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from typing import Any, Callable

QUESTIONS = ("health", "systems", "rom-locations", "firmware-inventory")
"""Every question both forms put that needs no content, no system and no core."""

SHOWN_PATHS = 5
"""How many places a disagreeing line names before it counts the rest."""

AGREE = 0
DISAGREE = 1
NOT_PUT = 2

Asker = Callable[[list[str]], tuple[int, Any]]
"""Ask atlas the command line it is given: its exit status, and its answer parsed where it gave one."""


def ask_atlas(argv: list[str]) -> tuple[int, Any]:
    """*argv* put to ``atlas.cli.run`` in this process; its status, and its JSON answer where the status is 0."""
    from atlas.cli import run

    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            status = run(argv)
        except SystemExit as stop:
            status = stop.code if isinstance(stop.code, int) else NOT_PUT
    return status, json.loads(out.getvalue()) if status == 0 else None


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
    for key in sorted(set(left) | set(right)):
        if key in left and key in right:
            found += differences(left[key], right[key], f"{path}.{key}")
        else:
            found.append(f"{path}.{key} (in one only)")
    return found


def verdict(question: str, kind: str, paths: list[str]) -> str:
    """The line for one question and one kind."""
    if not paths:
        return f"{question} {kind}: agree"
    shown = ", ".join(path or "(the whole answer)" for path in paths[:SHOWN_PATHS])
    more = f" and {len(paths) - SHOWN_PATHS} more" if len(paths) > SHOWN_PATHS else ""
    return f"{question} {kind}: differ at {shown}{more}"


def compare_question(ask: Asker, question: str, kinds: list[str]) -> tuple[int, list[str]]:
    """*question* in both forms for each of *kinds*: the worst status among them, and one line each."""
    status, labelled = ask([question])
    if status != 0:
        return NOT_PUT, [f"{question}: could not be put to every installation (atlas ended with {status})"]
    worst, lines = AGREE, []
    for kind in kinds:
        entry = next(item["answer"] for item in labelled if item["installation"]["kind"] == kind)
        status, bare = ask([question, "--installation", kind])
        if status != 0:
            worst = NOT_PUT
            lines.append(f"{question} {kind}: could not be put with --installation (atlas ended with {status})")
            continue
        paths = differences(entry, bare)
        worst = max(worst, DISAGREE if paths else AGREE)
        lines.append(verdict(question, kind, paths))
    return worst, lines


def compare(ask: Asker) -> tuple[int, list[str]]:
    """Every question of :data:`QUESTIONS` in both forms, for the first installation of each kind; status and lines."""
    status, installations = ask(["detect"])
    if status != 0:
        return NOT_PUT, [f"detect: could not be put (atlas ended with {status})"]
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
        status, found = compare_question(ask, question, kinds)
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
