"""The standalone build tripwire: a card must describe the build deployed here.

A standalone save card cites its emulator's source at one revision, and every
sentence it states is worth something only while the deployed binary *is* that
revision. Vita3K is the case this was written for: its card's readings turn on
a compiled-in default for ``user-id``, on which of two ``path::stem`` overloads
the build compiles, and on the yaml-cpp commit the revision pins — all of them
things an upgrade moves, and none of them things a green suite would notice
moving.

So where the emulator is deployed, this asks the binary what it is. The
difference from ``tests/test_core_firmware_tripwire.py`` is the seam: a
libretro core answers through ``retro_get_system_info``, while a standalone
emulator answers through the string its ``--version`` prints. Where that
string is a constant of the build, as Vita3K's is, it can be read off the
binary as raw bytes with nothing run at all — and raw bytes rather than
``strings`` tokens because the NUL the pattern ends on is what makes a hit the
whole constant, which a token pass cannot promise.

**What this cannot see.** Only an arrangement that deploys an extracted build
can be read. EmuDeck downloads the ``continuous`` AppImage and moves the
downloaded file itself to ``~/Applications/Vita3K/Vita3K``, extracting nothing
(emuDeckVita3K.sh:18-24 with functions/installEmuBI.sh:27 at acc45fc, the
folder being ``$HOME/Applications`` per vars.sh:4-5), so what sits there is
the AppImage: its tag moves with every upstream commit, which no card can pin,
and the build itself sits inside that archive, which this test does not open.
The app-of-its-own road ``test_anchor_tripwire.py`` walks does not apply
either, because the settings table names no flatpak for this emulator. What
the stamp cannot do is go stale while the revision moves, since the count and
the hash in it both come from HEAD — read as the build was configured, so a
rebuild without a reconfigure keeps the stamp it was given and the stamp names
the commit that configure saw. But the reading is still a floor rather than a
proof: it establishes which build is deployed, not that any line the card
cites still says what the card says.

Skipped where the components tree is not deployed, like the tripwires beside
it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

import pytest

from atlas.emulator_settings import load_emulator_settings
from atlas.standalone_saves import SAVES_SCHEMA, StandaloneSaveCard, load_standalone_saves

COMPONENTS = Path(
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
)

# The shape of the version string Vita3K's build carries, matched as bytes up
# to its NUL so a hit is the whole constant rather than a prefix of something
# longer: `<app name> <version> <commit count>-<revision>`, the name and the
# version being set outright (vita3k/CMakeLists.txt:1-6) and the revision
# taking one of three shapes (vita3k/CMakeLists.txt:52-63) — the bare short
# hash where the owner is Vita3K and the branch is master or a detached HEAD,
# `<hash>-<owner>/<branch>` for any other git tree, which the optional suffix
# admits, and `Development version` with a count of 0 where git answered
# nothing, which is the one shape this pattern does not match and which the
# comparison then names as a build carrying no stamp.
_VITA3K_STAMP = rb"Vita3K v\d+\.\d+\.\d+ \d+-[0-9a-f]+(?:-[^\x00]*)?\x00"

# One pattern per pinned card. The table is the mechanism, so a card that pins
# a build and has no pattern here is named by the test below rather than
# skipped: a pin nobody can read is the silence this file exists to break.
VERSION_STAMPS: Mapping[str, bytes] = {"VITA3K": _VITA3K_STAMP}

# The cards that pin no build today. A new card either pins its build — and
# gets a pattern above — or is added here on purpose; pinning the cards below
# is open work, not a finding that they cannot be pinned.
UNPINNED = frozenset(
    {
        "DOLPHIN",
        "PRIMEHACK",
        "PPSSPP",
        "XEMU",
        "CEMU",
        "AZAHAR",
        "DUCKSTATION",
        "PCSX2",
        "MELONDS",
        "RPCS3",
    }
)

NO_STAMP = "carries no version stamp of the shape this test reads"

DEMO = "DEMO"
DEMO_REVISION = "abc1234"
DEMO_CITATION = "[V] a citation"
BLOCK_SHAPE = "exactly revision/citation"
BLANK = "non-blank string"


def pinned_cards() -> tuple[StandaloneSaveCard, ...]:
    """The packaged cards that state a build, in the file's own order."""
    return tuple(card for card in load_standalone_saves() if card.build is not None)


def cards_without_a_pattern(cards: tuple[StandaloneSaveCard, ...]) -> list[str]:
    """The pinned cards :data:`VERSION_STAMPS` can read no stamp for."""
    return [
        card.token
        for card in cards
        if card.build is not None and card.token not in VERSION_STAMPS
    ]


def stamp_disagreements(
    cards: tuple[StandaloneSaveCard, ...], stamps: Mapping[str, str | None]
) -> list[str]:
    """The pinned cards whose revision the deployed stamp does not carry.

    *stamps* maps a card's token to the version string that emulator's deployed
    binary carries, ``None`` where the binary is there and carries no such
    string. An emulator that is not deployed at all is simply absent from the
    mapping and is not judged — this machine's emulator set is an accident of
    one installation, and the cards are not.

    Factored out of the assertion so the comparison can be exercised without a
    deployed emulator: a guard nobody has watched fail is a guard nobody knows
    fires.
    """
    wrong: list[str] = []
    for card in cards:
        if card.build is None or card.token not in stamps:
            continue
        reported = stamps[card.token]
        if reported is None:
            wrong.append(f"{card.token}: the deployed build {NO_STAMP}")
        elif card.build.revision not in reported:
            wrong.append(
                f"{card.token}: the card pins {card.build.revision!r} and the deployed "
                f"build states {reported!r}"
            )
    return wrong


def _stated_binary(token: str) -> str:
    """The path the settings table states for one emulator's own build.

    Read through that table, the way ``test_anchor_tripwire.py`` reads it, so
    the binary's address stays stated in one place. Every way a pinned card
    cannot be addressed raises by name rather than skipping, because a pin this
    file cannot read is exactly the silence it exists to break.
    """
    entries = load_emulator_settings()
    if token not in entries:
        raise ValueError(
            f"{token} pins a build and the settings table holds no entry for it, so nothing "
            "here knows which binary carries the stamp"
        )
    directory = entries[token].directory
    if directory is None:
        raise ValueError(
            f"{token} pins a build and the settings table states no directory for it, so "
            "nothing here knows which binary carries the stamp"
        )
    if directory.default.flatpak is not None:
        raise ValueError(
            f"{token} pins a build and the settings table installs it as an app of its own, "
            "whose binary sits below a flatpak root this probe does not walk"
        )
    return directory.default.binary


def _binary_of(token: str) -> Path:
    """Where the arrangement's own build of one emulator sits."""
    return COMPONENTS / _stated_binary(token)


def _component_version_of(token: str) -> Path:
    """The file naming the release the component tree around that binary was assembled from.

    Derived from the **first** segment of the stated path — the component's own
    directory — rather than by walking up from the binary, which would assume
    how deep inside its component a build sits and would quietly read some
    other tree for one that sits deeper or shallower.
    """
    return COMPONENTS / Path(_stated_binary(token)).parts[0] / "component_version"


def _pattern_of(token: str) -> bytes:
    """The stamp pattern for one pinned card, refused by name where there is none."""
    pattern = VERSION_STAMPS.get(token)
    if pattern is None:
        raise ValueError(
            f"{token} pins a build and VERSION_STAMPS holds no pattern for it, so nothing "
            "here knows which string to read its stamp out of"
        )
    return pattern


def deployed_stamps() -> dict[str, str | None]:
    """What each pinned emulator deployed here states as its own build."""
    stamps: dict[str, str | None] = {}
    for card in pinned_cards():
        binary = _binary_of(card.token)
        if not binary.is_file():
            continue
        found = re.search(_pattern_of(card.token), binary.read_bytes())
        stamps[card.token] = (
            None if found is None else found.group().rstrip(b"\x00").decode("ascii")
        )
    return stamps


def _stamps_or_skip() -> dict[str, str | None]:
    if not COMPONENTS.is_dir():
        pytest.skip(f"nothing is deployed at {COMPONENTS}")
    stamps = deployed_stamps()
    if not stamps:
        pytest.skip("no emulator a pinned card describes is deployed here")
    return stamps


def _synthetic(build: object) -> str:
    """One card's text, so the loader's own guards can be watched failing."""
    card: dict[str, object] = {
        "saves": {"settings": "demo.yml", "systems": ["psvita"]},
        "provenance": {"source": DEMO_CITATION},
    }
    if build is not None:
        card["build"] = build
    return json.dumps({"schema": SAVES_SCHEMA, "emulators": {DEMO: card}})


class TestTheCheckItself:
    """The comparison, over stamps written here — no machine involved."""

    def test_a_stamp_carrying_the_pinned_revision_passes(self):
        cards = pinned_cards()
        stamps = {
            card.token: f"Demo v9.9.9 1-{card.build.revision}"
            for card in cards
            if card.build is not None
        }
        assert stamp_disagreements(cards, stamps) == []

    def test_a_card_whose_revision_is_absent_is_named(self):
        cards = pinned_cards()
        named = stamp_disagreements(cards, {card.token: "Demo v9.9.9 1-deadbeef" for card in cards})
        assert sorted(entry.split(":")[0] for entry in named) == sorted(
            card.token for card in cards
        )

    def test_a_build_that_states_no_stamp_is_named(self):
        one = pinned_cards()[0]
        named = stamp_disagreements(pinned_cards(), {one.token: None})
        assert named == [f"{one.token}: the deployed build {NO_STAMP}"]

    def test_an_emulator_that_is_not_deployed_is_not_judged(self):
        assert stamp_disagreements(pinned_cards(), {}) == []


class TestThePatternTable:
    """Every pinned card is one this file can actually read a stamp for."""

    def test_every_pinned_card_has_a_pattern(self):
        assert cards_without_a_pattern(pinned_cards()) == [], (
            "a card pins a build and VERSION_STAMPS holds no pattern for it — the pin would "
            "then be held against nothing at all, which is what an absent pin already is"
        )

    def test_a_pinned_card_with_no_pattern_is_named(self):
        cards = load_standalone_saves(
            _synthetic({"revision": DEMO_REVISION, "citation": DEMO_CITATION})
        )
        assert cards_without_a_pattern(cards) == [DEMO]

    def test_the_pattern_matches_a_plain_stamp(self):
        found = re.search(_VITA3K_STAMP, b"junk\x00Vita3K v0.2.1 1234-abcdef12\x00junk")
        assert found is not None

    def test_the_pattern_matches_a_stamp_from_a_fork(self):
        found = re.search(_VITA3K_STAMP, b"\x00Vita3K v0.2.1 1234-abcdef12-someone/topic\x00")
        assert found is not None

    def test_a_stamp_without_its_nul_is_not_matched(self):
        # The NUL is what makes a hit the whole constant: without it the
        # pattern would accept a prefix of a longer string and read a
        # truncated revision out of it.
        assert re.search(_VITA3K_STAMP, b"Vita3K v0.2.1 1234-abcdef12") is None


class TestTheCardsThatPinNothing:
    """The unpinned set, so an absent pin stays deliberate."""

    def test_the_cards_with_no_build_are_exactly_the_listed_ones(self):
        assert {card.token for card in load_standalone_saves() if card.build is None} == UNPINNED, (
            "which cards pin no build has changed — a new card either pins its build and gets "
            "a pattern in VERSION_STAMPS, or joins UNPINNED on purpose, and a card that left "
            "the set stopped being held against anything"
        )


class TestTheLoaderReadsTheBlock:
    """What the packaged card states, and what the loader refuses."""

    def test_the_packaged_card_states_its_revision(self):
        card = pinned_cards()[0]
        assert card.build is not None
        assert card.build.revision == "cb1f592c"

    @pytest.mark.parametrize("card", pinned_cards(), ids=[c.token for c in pinned_cards()])
    def test_every_statement_of_the_pin_names_one_commit(self, card: StandaloneSaveCard):
        # The commit stands three times in a card on purpose: `provenance` is
        # the prose an answer carries in its `sources`, the build citation is
        # where the release is tied to the commit, and `build.revision` is the
        # pin this file checks. Three places is three chances to drift, so the
        # agreement is a test rather than a habit — a card whose prose still
        # named the old commit would tell a reader one build and the tripwire
        # another.
        assert card.build is not None
        assert card.build.revision in card.provenance, (
            f"{card.token} pins {card.build.revision!r} and its provenance names some other "
            "commit — the sentence an answer carries and the pin held against the deployed "
            "build must be the same commit"
        )
        assert card.build.revision in card.build.citation, (
            f"{card.token} pins {card.build.revision!r} and its build citation names some "
            "other commit — the reading that ties the release to a commit and the pin held "
            "against the deployed build must be the same commit"
        )

    def test_a_card_with_no_block_pins_nothing(self):
        cards = load_standalone_saves(_synthetic(None))
        assert cards[0].build is None

    def test_a_block_missing_a_key_is_refused(self):
        table = _synthetic({"revision": DEMO_REVISION})
        with pytest.raises(ValueError, match=BLOCK_SHAPE):
            load_standalone_saves(table)

    def test_a_block_with_an_extra_key_is_refused(self):
        table = _synthetic(
            {"revision": DEMO_REVISION, "citation": DEMO_CITATION, "mode": "by-name"}
        )
        with pytest.raises(ValueError, match=BLOCK_SHAPE):
            load_standalone_saves(table)

    def test_a_block_that_is_not_an_object_is_refused(self):
        table = _synthetic(DEMO_REVISION)
        with pytest.raises(ValueError, match=BLOCK_SHAPE):
            load_standalone_saves(table)

    def test_an_empty_revision_is_refused(self):
        table = _synthetic({"revision": "", "citation": DEMO_CITATION})
        with pytest.raises(ValueError, match="build.revision"):
            load_standalone_saves(table)

    def test_an_empty_citation_is_refused(self):
        table = _synthetic({"revision": DEMO_REVISION, "citation": ""})
        with pytest.raises(ValueError, match="build.citation"):
            load_standalone_saves(table)

    def test_a_blank_revision_is_refused(self):
        # A revision of spaces is a substring of every version string there is,
        # so it would pass the comparison against any build at all.
        table = _synthetic({"revision": " ", "citation": DEMO_CITATION})
        with pytest.raises(ValueError, match=BLANK):
            load_standalone_saves(table)

    def test_a_blank_citation_is_refused(self):
        table = _synthetic({"revision": DEMO_REVISION, "citation": " "})
        with pytest.raises(ValueError, match=BLANK):
            load_standalone_saves(table)


class TestEveryPinnedCardDescribesTheDeployedBuild:
    """Machine-bound: what the binaries here actually state."""

    def test_every_deployed_card_carries_its_pinned_revision(self):
        stamps = _stamps_or_skip()
        assert stamp_disagreements(pinned_cards(), stamps) == []

    def test_the_probe_really_read_something(self):
        # Without this, a probe that answered `None` for every emulator would
        # make the check above vacuous in the one direction a skip does not
        # cover.
        stamps = _stamps_or_skip()
        assert [token for token, stamp in stamps.items() if stamp], (
            "every deployed emulator with a pinned card stated no version string — the probe "
            "read nothing, so the check above proved nothing"
        )

    def test_the_component_version_agrees_with_the_stamp(self):
        # Two readings of one fact: the component recipe writes the tag of the
        # release it downloaded, and the binary carries the commit count that
        # release was built at. A mismatch means the deployed binary is not the
        # release the tree beside it names.
        stamps = _stamps_or_skip()
        for token, stamp in stamps.items():
            if stamp is None:
                continue
            version = _component_version_of(token)
            assert version.is_file(), (
                f"{token}: the deployed build states {stamp!r} and its component tree names no "
                f"release at all ({version.name} is absent) — the two readings of one release "
                "cannot be crossed, so the binary is held against nothing here"
            )
            named = version.read_text(encoding="utf-8").strip()
            counted = re.search(r" (\d+)-", stamp)
            assert counted is not None, f"{token}: {stamp!r} states no build count"
            assert counted.group(1) == named, (
                f"{token}: the deployed build states {stamp!r} and the component tree beside "
                f"it names release {named!r} — the binary is not the release that tree was "
                "assembled from"
            )
