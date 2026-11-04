"""The caveat codes of the save-portability question, and the closed vocabularies their data keys take.

They live apart from :mod:`atlas.save_portability`, which raises them, because
:data:`atlas.placement.ENUMERATED_DATA` registers the ``side`` and ``reason``
vocabularies under these codes, and :mod:`atlas.placement` cannot import the
module that imports its :class:`~atlas.placement.Caveat`.
"""

from __future__ import annotations

from typing import Final, Literal

# atlas could not confirm on this machine the build one entry's save formats
# were read at (#577 D5), so every part resting on that entry answers
# unestablished. ``data``: ``side``, the record's emulator as ``core`` (a
# libretro core's short name) or ``token`` (a standalone emulator's card
# token), ``pinned`` (the builds the record was read at), ``running`` (the
# build this machine names, absent where none was read) and ``reason``.
CAVEAT_SAVE_FORMAT_BUILD_UNCONFIRMED: Final = "save-format-build-unconfirmed"
# No packaged format record covers one side's save as its answer states it: an
# emulator or system the delivered records do not cover, a live mode no part
# rule names, or a file group no rule recognises. ``data``: ``side``, the
# side's ``core`` where it runs a libretro core or its record's ``token``
# where a record names its standalone emulator (neither for a standalone
# emulator no record covers: the answer's entry names it), and ``mode``
# (absent on an answer that states no mode).
CAVEAT_SAVE_FORMAT_UNRECORDED: Final = "save-format-unrecorded"
# One side's save question refused (an :class:`~atlas.placement.Unresolved`),
# so there are no files to compare. ``data``: ``side`` and ``code``, the
# refusal's own code.
CAVEAT_SAVE_ANSWER_UNRESOLVED: Final = "save-answer-unresolved"
# No catalogue entry of the system carries the label one side names. ``data``:
# ``system``, ``side`` and ``label``.
CAVEAT_ENTRY_LABEL_UNKNOWN: Final = "entry-label-unknown"
# Both builds are confirmed and both records cover the pair, and one part still
# answers unestablished: why is the ``reason``. ``data``: ``memory``, ``slot``
# (absent for a memory a system has one of) and ``reason``.
CAVEAT_SAVE_PART_UNESTABLISHED: Final = "save-part-unestablished"

PortabilitySide = Literal["source", "target"]
SIDE_SOURCE: PortabilitySide = "source"
"""The entry that wrote the save."""
SIDE_TARGET: PortabilitySide = "target"
"""The entry that would read it."""
# Which of the two entries a caveat is about, the vocabulary of ``side``.
PORTABILITY_SIDES = (SIDE_SOURCE, SIDE_TARGET)

BuildUnconfirmedReason = Literal["other-build", "build-unread"]
REASON_OTHER_BUILD: BuildUnconfirmedReason = "other-build"
"""The machine names a build, and it is none of the builds the record was read at."""
REASON_BUILD_UNREAD: BuildUnconfirmedReason = "build-unread"
"""Nothing atlas read here names the build: a core that did not answer, or a standalone emulator with no deploy
record of its build."""
# Why a build was not confirmed, the vocabulary of ``reason``.
BUILD_UNCONFIRMED_REASONS = (REASON_OTHER_BUILD, REASON_BUILD_UNREAD)

PartUnestablishedReason = Literal["target-file-unnamed", "no-conversion-stated"]
REASON_TARGET_FILE_UNNAMED: PartUnestablishedReason = "target-file-unnamed"
"""The target's live mode keeps such a part, and its save answer names no file for it: a card that lists the file only
as an observation candidate, or an observed directory the file is not written to yet."""
REASON_NO_CONVERSION_STATED: PartUnestablishedReason = "no-conversion-stated"
"""The two entries keep the part in two formats, and no source the records cite states a conversion between them."""
# Why a part with both builds confirmed is still unestablished, the vocabulary
# of ``save-part-unestablished``'s ``reason``.
PART_UNESTABLISHED_REASONS = (REASON_TARGET_FILE_UNNAMED, REASON_NO_CONVERSION_STATED)
