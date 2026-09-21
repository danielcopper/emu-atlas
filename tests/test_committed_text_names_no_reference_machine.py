"""No ``.py``, ``.md``, ``.json``, ``.yml`` or ``.yaml`` file states a reading
taken off one maintainer's own machine.

atlas answers by reading the machine in front of it, so a count or a timing
read off one installation illustrates a mechanism and is never a fact this
package carries: the mechanism is what the sentence states, pointing at a
vector or a packaged table where one shows it. That is CLAUDE.md's boundary
rule — what is on the running machine is read, and the live verification round
is maintainer-side working state kept outside the tree, because it describes
one particular machine that no contributor has.

Two paths keep their readings, and for the same reason both times: there the
reading *is* the record. Under ``docs/research/`` a finding is what was
observed, carried with its evidence mark, which is what those documents exist
to hold. ``CHANGELOG.md`` is written by release-please out of merged pull
requests and never edited by hand, so a sentence in it is history rather than
a claim the package makes.

The two wordings such a sentence is written in are spelled once, in
:data:`WORDS`, and both the pattern and the planted readings below are built
out of them, so the two cannot drift apart. Nothing here spells either wording
in prose: this file is walked like every other, and a rule that exempted the
file stating it would be a hole in the rule.

**What stays the reader's job.** The walk sees the words rather than the
reading: a count read off one installation that names no machine at all passes
it. It reads the five suffixes :data:`SUFFIXES` names, so a reading in a shell
script, a ``.toml`` or any other file is outside it. And the pattern crosses
whitespace and one blanked line marker, nothing else: a wording split across
two JSON strings, or at an escaped newline inside one, passes it too.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

WORDS = ("reference", "machine", "installation")
"""The three words the two wordings are built from.

The first word of the phrase, then the two nouns either wording ends in.
"""

PHRASE = re.compile(rf"{WORDS[0]}\s+(?:{WORDS[1]}|{WORDS[2]})", re.IGNORECASE)
"""Both wordings as one pattern, and any whitespace between the two words.

The whitespace is what a wrapped line makes of a space, so a wording carried
across two lines of one text is matched where a line-by-line search misses it
— including inside a comment block, because :func:`_prose` blanks the marker
the second line starts with before any of this reads the text. What the
pattern does not cross is stated in the module docstring.
"""

COMMENT_MARKER = re.compile(r"^(?:[ \t]*)(?:#+|//+|\*|>)(?:[ \t]?)", re.MULTILINE)
"""What a wrapped line of a comment, a docstring bullet or a quote starts with."""

SUFFIXES = frozenset({".py", ".md", ".json", ".yml", ".yaml"})
"""What *committed text* means to this gate: code, prose, packaged data, workflows."""

SKIPPED_TREES = frozenset(
    {".git", ".claude", ".hypothesis", ".pytest_cache", "__pycache__", "build", "dist", "dist-bundle"}
)
"""Directories holding nothing this repository commits.

Git's own tree and the harness's, plus the build, packaging and cache trees
``.gitignore`` names. The local venvs are skipped by their shared prefix
instead, because the oldest-interpreter one carries a suffix of its own, and
a ``*.egg-info`` by its own.
"""

VENV_PREFIX = ".venv"
EGG_INFO_SUFFIX = ".egg-info"

KEPT_TREE = "docs/research/"
KEPT_FILE = "CHANGELOG.md"


def _keeps_its_readings(relative: str) -> bool:
    """Is this the record of a reading rather than a claim the package makes?"""
    return relative == KEPT_FILE or relative.startswith(KEPT_TREE)


def _is_skipped(directory: Path) -> bool:
    """Is this a directory the walk never enters?

    A symlinked one is not entered either — what it holds is committed under
    its real name or not at all.
    """
    name = directory.name
    return (
        directory.is_symlink()
        or name in SKIPPED_TREES
        or name.startswith(VENV_PREFIX)
        or name.endswith(EGG_INFO_SUFFIX)
    )


def _text_files(root: Path) -> Iterator[Path]:
    """Every file below *root* this walk reads, in a stable order."""
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            if not _is_skipped(entry):
                yield from _text_files(entry)
        elif entry.suffix in SUFFIXES:
            yield entry


def _prose(text: str) -> str:
    """*text* with each line's leading comment marker blanked out.

    The blanks are as long as what they replace, so every offset in the
    result is the offset it was in *text* and a line number counted over one
    is the line number in the other.
    """
    return COMMENT_MARKER.sub(lambda match: " " * len(match.group(0)), text)


def _named_sites(root: Path) -> list[str]:
    """Every ``file:line`` below *root* whose text spells one of the wordings."""
    sites: list[str] = []
    for path in _text_files(root):
        relative = path.relative_to(root).as_posix()
        if _keeps_its_readings(relative):
            continue
        text = _prose(path.read_text(encoding="utf-8", errors="surrogateescape"))
        for match in PHRASE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            sites.append(f"{relative}:{line}")
    return sites


def test_no_committed_text_states_a_reading_off_one_machine():
    """The rule, over the tree as it is committed."""
    sites = _named_sites(REPO_ROOT)
    assert sites == [], "state the mechanism instead, at: " + ", ".join(sites)


def test_the_walk_names_the_lines_a_reading_is_written_on(tmp_path: Path):
    """The gate watched failing, over a tree whose readings are planted.

    One of the two is wrapped across a newline, which is the shape a
    line-by-line search misses and :data:`PHRASE` is built to catch.
    """
    (tmp_path / "atlas").mkdir()
    (tmp_path / "atlas" / "mechanism.py").write_text(
        '"""The rule, stated from the mechanism."""\n', encoding="utf-8"
    )
    (tmp_path / "atlas" / "reading.md").write_text(
        f"A count\n\nread off the {WORDS[0]} {WORDS[1]}.\n", encoding="utf-8"
    )
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "wrapped.yml").write_text(
        f"# a timing read off one\n# {WORDS[0]}\n# {WORDS[2]}, wrapped\n", encoding="utf-8"
    )
    (tmp_path / ".venv-oldest").mkdir()
    (tmp_path / ".venv-oldest" / "vendored.py").write_text(
        f"# a {WORDS[0]} {WORDS[2]} of something else\n", encoding="utf-8"
    )
    assert _named_sites(tmp_path) == [".github/wrapped.yml:2", "atlas/reading.md:3"]
