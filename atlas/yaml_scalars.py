"""A deliberately small reader for the flat half of a YAML configuration.

Two of the standalone emulators RetroDECK ships keep the paths atlas answers
from in YAML — RPCS3's ``vfs.yml`` maps ``/dev_hdd0/`` to the directory its
save tree hangs off, Vita3K's ``config.yml`` carries ``pref-path`` — and atlas
ships zero runtime dependencies, so there is no YAML library to reach for.

This module reads the part of such a file that can be read *exactly*, in the
same spirit as the Cemu XML read and the melonDS legacy-INI scan: top-level
``key: value`` scalars, quoted or bare, with ``$(Name)`` substitution from
another key in the same file — which is how RPCS3 composes every device path
off ``$(EmulatorDir)``.

Everything else is refused rather than guessed, and the refusal is *named* so a
caller can tell "atlas did not read this" from "this is not set":

- a key whose value is a **nested block, a list or a multi-line scalar** is
  recorded in :attr:`YamlScalars.skipped` by name and not parsed. RPCS3's
  ``/dev_usb***/`` is one of those, and nothing atlas answers needs it, so the
  file still speaks while the reader stays honest about which key it passed
  over. Asking for a skipped key raises rather than answering a default.
- **anchors, aliases, tags and multiple documents** change meaning beyond the
  line they sit on — an alias can make a key elsewhere mean something this
  reader never saw — so they refuse the whole file, not one key.
- a **substitution cycle** refuses rather than looping.

What this is not: a YAML parser. It does not build a tree, it does not type
values (everything is the text as written), and it will refuse or skip a great
deal of legal YAML. That is the point — the alternative is a half-parser whose
mistakes look like answers.

Pure text in, value object out. No I/O — the machine seam supplies the text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

# The refusal codes this reader answers with. They name what was found, not
# what the caller wanted, so a message can say which construct stopped the read.
# A *second* document in the stream. The opening ``---`` is harmless and common
# — Vita3K's config.yml carries one — but a second document may redefine every
# key the first one set, and which of the two an emulator reads is not a
# question this reader can answer from the text.
REFUSAL_SECOND_DOCUMENT = "second-document"
REFUSAL_ANCHOR = "anchor-or-alias"
REFUSAL_TAG = "tag"
REFUSAL_SUBSTITUTION_CYCLE = "substitution-cycle"
REFUSAL_SUBSTITUTION_UNKNOWN = "substitution-unknown"
# An indented line under no key at all, or a top-level line that is not a
# ``key: value`` pair. Either way the file is not the flat mapping this reader
# reads, and no line in it can be attributed to a key with confidence.
REFUSAL_NOT_A_FLAT_MAPPING = "not-a-flat-mapping"

# How deep a chain of ``$(Name)`` substitutions may go before the reader calls
# it a cycle. RPCS3's file is one level deep; the bound exists so a malformed
# file refuses instead of spinning.
_MAX_SUBSTITUTION_DEPTH = 8


@dataclass(frozen=True, slots=True)
class YamlScalars:
    """What the reader established, and what it deliberately did not.

    ``values`` holds the top-level scalars it read, substitutions resolved.
    ``skipped`` names the top-level keys whose value was a construct this
    reader does not read — the caller decides whether that matters for the key
    it wants. ``refusal`` is set exactly when the whole file was refused, and
    then ``values`` is empty: a file carrying an alias may mean something
    different from what its plain lines say, so no line from it is reported.
    """

    values: Mapping[str, str] = field(default_factory=dict)
    skipped: tuple[str, ...] = ()
    refusal: str | None = None

    def get(self, key: str) -> str | None:
        """The value at *key*, or ``None`` where the file states none.

        Raises :class:`KeyError` for a key this reader skipped: the file *does*
        state something there and the reader did not read it, which is not the
        same fact as an absent key and must never collapse into one.
        """
        if key in self.skipped:
            raise KeyError(
                f"{key!r} is stated as a construct this reader does not read "
                f"(nested block, list or multi-line scalar) — its value is unread, not absent"
            )
        return self.values.get(key)


def _unquote(value: str) -> str:
    """A scalar as written: quotes come off, everything else stays verbatim.

    Only the two quote forms are handled, and only when they wrap the whole
    scalar. No escape processing: a double-quoted YAML scalar may carry
    backslash escapes, and pretending otherwise would silently change a path.
    A value that opens a quote and does not close it is left verbatim, the way
    the melonDS and marker readers leave an unterminated quote — visibly odd
    rather than invented.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _strip_comment(value: str) -> str:
    """A bare scalar's trailing comment, cut the way YAML cuts it.

    ``#`` begins a comment only where whitespace precedes it, so a ``#`` inside
    a word — a path segment, a colour — stays part of the value.
    """
    index = value.find(" #")
    return value if index == -1 else value[:index]


def _scalar(raw: str) -> str:
    """One value as written — quoted scalars keep their content, bare ones lose comments."""
    stripped = raw.strip()
    if stripped[:1] in ("\"", "'"):
        return _unquote(stripped)
    return _strip_comment(stripped).strip()


def _is_skipping_value(value: str) -> bool:
    """Does this value open a construct the reader records instead of reading?

    Four of them. A ``|`` or ``>`` opens a multi-line scalar; ``- `` opens a
    list; ``[`` and ``{`` open a flow collection — Vita3K writes ``[]`` for an
    empty module list, and reporting that as the two-character string ``"[]"``
    would be a value nothing configured. An empty value with indented lines
    under it is the fifth, and only the caller can see what follows.
    """
    return (
        value in ("|", ">", "|-", ">-", "|+", ">+")
        or value.startswith("- ")
        or value.startswith("[")
        or value.startswith("{")
    )


def _refusal_in(value: str) -> str | None:
    """The whole-file refusal this value triggers, if any."""
    if value.startswith("&") or value.startswith("*"):
        return REFUSAL_ANCHOR
    if value.startswith("!"):
        return REFUSAL_TAG
    return None


def _split_key(line: str) -> tuple[str, str] | None:
    """``key: value`` split at the first colon-space, or a bare ``key:``.

    A key may itself contain a colon — RPCS3's ``/dev_usb***/`` does not, but
    ``$(EmulatorDir)`` sits left of one — so the split is on ``": "`` first and
    on a trailing colon second, which is what YAML's own plain-key rule comes
    down to for these files.
    """
    if line.endswith(":"):
        return line[:-1].strip(), ""
    marker = line.find(": ")
    if marker == -1:
        return None
    return line[:marker].strip(), line[marker + 2 :]


def _substitute(
    values: dict[str, str],
) -> tuple[dict[str, str], str | None]:
    """Resolve ``$(Name)`` against the file's own keys, or refuse.

    RPCS3 writes ``$(EmulatorDir): /path/`` and then composes every device path
    off it — the defining key is spelled exactly like the reference, so the
    whole ``$(Name)`` token is what gets looked up, not the name inside it. A
    token the file does not define is a refusal rather than an empty string:
    the emulator would resolve it and atlas cannot, so answering the unexpanded
    text would state a path nothing uses.
    """
    resolved: dict[str, str] = {}
    for key, value in values.items():
        current = value
        for _ in range(_MAX_SUBSTITUTION_DEPTH):
            start = current.find("$(")
            if start == -1:
                break
            end = current.find(")", start)
            if end == -1:
                break
            token = current[start : end + 1]
            if token not in values:
                return {}, REFUSAL_SUBSTITUTION_UNKNOWN
            current = current[:start] + values[token] + current[end + 1 :]
        else:
            return {}, REFUSAL_SUBSTITUTION_CYCLE
        resolved[key] = current
    return resolved, None


def read_scalars(text: str) -> YamlScalars:
    """Read the flat top-level scalars of *text*, naming what was not read."""
    values: dict[str, str] = {}
    skipped: list[str] = []
    pending_block_key: str | None = None
    seen_content = False
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line.startswith("---"):
            # The opening marker is just "here begins the document"; a second
            # one begins a document that may redefine everything above it.
            if seen_content:
                return YamlScalars(refusal=REFUSAL_SECOND_DOCUMENT)
            continue
        if raw_line.startswith("..."):
            # The document ends here. Anything after it belongs to the next one.
            break
        if raw_line[:1].isspace():
            # An indented line belongs to whatever key opened above it, which
            # makes that key a nested block after all: it stops being a scalar
            # and becomes one this reader names as unread. Indentation under no
            # key at all is a file this reader cannot attribute lines from.
            if pending_block_key is None:
                return YamlScalars(refusal=REFUSAL_NOT_A_FLAT_MAPPING)
            if pending_block_key in values:
                del values[pending_block_key]
                skipped.append(pending_block_key)
            continue
        pending_block_key = None
        seen_content = True
        split = _split_key(raw_line.rstrip())
        if split is None:
            # A top-level line that is not a key at all — a list item, a bare
            # scalar. Nothing here can be attributed to a key.
            return YamlScalars(refusal=REFUSAL_NOT_A_FLAT_MAPPING)
        key, raw_value = split
        refusal = _refusal_in(raw_value.strip())
        if refusal is not None:
            return YamlScalars(refusal=refusal)
        if _is_skipping_value(raw_value.strip()):
            skipped.append(key)
            pending_block_key = key
            continue
        if raw_value.strip() == "":
            # Either an empty scalar or the head of a nested block — the lines
            # that follow decide, so the key is provisionally both: recorded as
            # empty, and re-recorded as skipped when an indented line arrives.
            values[key] = ""
            pending_block_key = key
            continue
        values[key] = _scalar(raw_value)
    resolved, refusal = _substitute(values)
    if refusal is not None:
        return YamlScalars(refusal=refusal)
    return YamlScalars(values=resolved, skipped=tuple(skipped))
