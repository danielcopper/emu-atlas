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
emulator states its build in a string constant of its own, and each pinned
build states that constant as its ``stamp``. What the constant says is the
emulator's choice — Vita3K's version string names the commit, DuckStation's
legacy build carries no commit hash and states its commit's date instead — so
the test reads nothing out of it: it searches the binary for exactly the
stamp, as raw bytes with nothing run at all, bounded by a NUL on each side.
Raw bytes rather than ``strings`` tokens, because the two NULs are what make a
hit the whole constant rather than the head or the tail of a longer one, which
a token pass cannot promise.

**What this cannot see.** Only an arrangement that deploys an extracted build
can be read. EmuDeck downloads the ``continuous`` AppImage and moves the
downloaded file itself to ``~/Applications/Vita3K/Vita3K``, extracting nothing
(emuDeckVita3K.sh:18-24 with functions/installEmuBI.sh:27 at acc45fc, the
folder being ``$HOME/Applications`` per vars.sh:4-5), so what sits there is
the AppImage: its tag moves with every upstream commit, which no card can pin,
and the build itself sits inside that archive, which this test does not open.
EmuDeck installs DuckStation as an AppImage too (emuDeckDuckStation.sh:5-6
at acc45fc). The app-of-its-own road ``test_anchor_tripwire.py`` walks does
not apply either, because the settings table names no flatpak for either
emulator. What the stamp cannot do is go stale while the revision
moves, since both stamps pinned today are read from HEAD — Vita3K's count and
hash as the build is configured, DuckStation's committer date as its version
file is written — so the stamp names the commit the build was made from. But
the reading is still a floor rather than a proof: it establishes which build
is deployed, not that any line the card cites still says what the card says.

Skipped where the components tree is not deployed, like the tripwires beside
it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from atlas.emulator_settings import load_emulator_settings
from atlas.standalone_saves import (
    SAVES_SCHEMA,
    StandaloneBuild,
    StandaloneSaveCard,
    load_standalone_saves,
)

COMPONENTS = Path(
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
)

# The cards that pin no build today. A new card either pins its build — and
# states the stamp its binary carries — or is added here on purpose; pinning
# the cards below is open work, not a finding that they cannot be pinned.
UNPINNED = frozenset(
    {
        "DOLPHIN",
        "PRIMEHACK",
        "PPSSPP",
        "XEMU",
        "CEMU",
        "AZAHAR",
        "PCSX2",
        "MELONDS",
        "RPCS3",
    }
)

NO_STAMP = "carries none of them as a whole NUL-delimited constant"

DEMO = "DEMO"
DEMO_RELEASE = "1234"
DEMO_REVISION = "abc1234"
DEMO_STAMP = "Demo v1.0.0 1234-abc1234"
DEMO_CITATION = "[V] a citation"
DEMO_PROVENANCE = "Demo build 1234, commit abc1234"
BLOCK_SHAPE = "exactly release/revision/stamp/citation/provenance"
BLANK = "non-blank string"


def _demo_build(**changed: object) -> dict[str, object]:
    """One well-formed build block, with *changed* keys replaced or, as None, dropped."""
    block: dict[str, object] = {
        "release": DEMO_RELEASE,
        "revision": DEMO_REVISION,
        "stamp": DEMO_STAMP,
        "citation": DEMO_CITATION,
        "provenance": DEMO_PROVENANCE,
    }
    for key, value in changed.items():
        if value is None:
            del block[key]
        else:
            block[key] = value
    return block


def pinned_cards() -> tuple[StandaloneSaveCard, ...]:
    """The packaged cards that state a build, in the file's own order."""
    return tuple(card for card in load_standalone_saves() if card.builds)


def pinned_builds() -> tuple[tuple[StandaloneSaveCard, StandaloneBuild], ...]:
    """Every build a packaged card pins, beside its card."""
    return tuple((card, build) for card in pinned_cards() for build in card.builds)


def deployed_build(card: StandaloneSaveCard, binary: bytes) -> StandaloneBuild | None:
    """The pinned build whose stamp *binary* holds as one whole constant, or ``None``.

    The stamp is searched for with a NUL on each side, so a hit is the entire
    string constant: a stamp that is only the head of a longer constant (a
    fork's ``-owner/branch`` suffix after a hash) or only its tail is a
    different string, and the build that carries it is not the pinned one.
    """
    return next(
        (build for build in card.builds if b"\x00" + build.stamp.encode() + b"\x00" in binary),
        None,
    )


def stamp_disagreements(
    cards: tuple[StandaloneSaveCard, ...], binaries: Mapping[str, bytes]
) -> list[str]:
    """The pinned cards none of whose stamps the deployed binary carries.

    *binaries* maps a card's token to the bytes of that emulator's deployed
    binary. An emulator that is not deployed at all is simply absent from the
    mapping and is not judged — this machine's emulator set is an accident of
    one installation, and the cards are not.

    A card pinning more than one build passes where the binary carries any of
    their stamps: each is a build the card's resolver answers by its own rule,
    and the deployed one only has to be among them.

    Factored out of the assertion so the comparison can be exercised without a
    deployed emulator: a guard nobody has watched fail is a guard nobody knows
    fires.
    """
    wrong: list[str] = []
    for card in cards:
        if not card.builds or card.token not in binaries:
            continue
        if deployed_build(card, binaries[card.token]) is None:
            stamps = [build.stamp for build in card.builds]
            wrong.append(
                f"{card.token}: the card pins the stamps {stamps!r} and the deployed binary "
                f"{NO_STAMP}"
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


def deployed_binaries() -> dict[str, bytes]:
    """The bytes of each pinned emulator's binary deployed here."""
    return {
        card.token: binary.read_bytes()
        for card in pinned_cards()
        if (binary := _binary_of(card.token)).is_file()
    }


def _binaries_or_skip() -> dict[str, bytes]:
    if not COMPONENTS.is_dir():
        pytest.skip(f"nothing is deployed at {COMPONENTS}")
    binaries = deployed_binaries()
    if not binaries:
        pytest.skip("no emulator a pinned card describes is deployed here")
    return binaries


def _synthetic(builds: object, *, key: str = "builds") -> str:
    """One card's text, so the loader's own guards can be watched failing."""
    card: dict[str, object] = {
        "saves": {"settings": "demo.yml", "systems": ["psvita"]},
        "provenance": {"source": DEMO_CITATION},
    }
    if builds is not None:
        card[key] = builds
    return json.dumps({"schema": SAVES_SCHEMA, "emulators": {DEMO: card}})


class TestTheCheckItself:
    """The comparison, over binaries written here — no machine involved."""

    @pytest.mark.parametrize(
        ("card", "build"),
        pinned_builds(),
        ids=[f"{card.token}-{build.release}" for card, build in pinned_builds()],
    )
    def test_a_binary_carrying_any_pinned_stamp_passes(
        self, card: StandaloneSaveCard, build: StandaloneBuild
    ):
        binaries = {card.token: b"junk\x00" + build.stamp.encode() + b"\x00junk"}
        assert stamp_disagreements(pinned_cards(), binaries) == []

    def test_a_card_whose_stamp_is_absent_is_named(self):
        cards = pinned_cards()
        named = stamp_disagreements(
            cards, {card.token: b"\x00Demo v9.9.9 1-deadbeef\x00" for card in cards}
        )
        assert sorted(entry.split(":")[0] for entry in named) == sorted(
            card.token for card in cards
        )

    def test_a_build_that_states_no_stamp_is_named(self):
        one = pinned_cards()[0]
        named = stamp_disagreements(pinned_cards(), {one.token: b"\x00no version here\x00"})
        stamps = [build.stamp for build in one.builds]
        assert named == [
            f"{one.token}: the card pins the stamps {stamps!r} and the deployed binary {NO_STAMP}"
        ]

    def test_an_emulator_that_is_not_deployed_is_not_judged(self):
        assert stamp_disagreements(pinned_cards(), {}) == []


class TestTheStampSearch:
    """A hit is the whole constant: neither the head nor the tail of a longer one."""

    @staticmethod
    def _card() -> StandaloneSaveCard:
        return load_standalone_saves(_synthetic([_demo_build()]))[0]

    def test_the_whole_constant_is_found(self):
        binary = b"junk\x00" + DEMO_STAMP.encode() + b"\x00junk"
        assert deployed_build(self._card(), binary) == self._card().builds[0]

    def test_the_head_of_a_longer_constant_is_not_found(self):
        # A fork's build of the same commit appends `-owner/branch` to
        # Vita3K's revision: the same leading characters, another build.
        binary = b"\x00" + DEMO_STAMP.encode() + b"-someone/topic\x00"
        assert deployed_build(self._card(), binary) is None

    def test_the_tail_of_a_longer_constant_is_not_found(self):
        binary = b"\x00Not" + DEMO_STAMP.encode() + b"\x00"
        assert deployed_build(self._card(), binary) is None

    def test_a_stamp_without_its_closing_nul_is_not_found(self):
        # The closing NUL is what ends the constant: without it the bytes
        # could run on into a longer string the search never saw the end of.
        binary = b"\x00" + DEMO_STAMP.encode()
        assert deployed_build(self._card(), binary) is None


class TestTheCardsThatPinNothing:
    """The unpinned set, so an absent pin stays deliberate."""

    def test_the_cards_with_no_build_are_exactly_the_listed_ones(self):
        assert {card.token for card in load_standalone_saves() if not card.builds} == UNPINNED, (
            "which cards pin no build has changed — a new card either pins its build and states "
            "the stamp its binary carries, or joins UNPINNED on purpose, and a card that left "
            "the set stopped being held against anything"
        )


class TestTheLoaderReadsTheBlock:
    """What the packaged card states, and what the loader refuses."""

    @staticmethod
    def _pinned(token: str) -> StandaloneSaveCard:
        return next(card for card in pinned_cards() if card.token == token)

    def test_the_packaged_card_states_its_builds(self):
        card = self._pinned("VITA3K")
        assert [(build.release, build.revision, build.stamp) for build in card.builds] == [
            ("3996", "cb1f592c", "Vita3K v0.2.1 3996-cb1f592c"),
            ("4103", "e6ac4272", "Vita3K v0.2.1 4103-e6ac4272"),
        ]

    def test_the_duckstation_card_pins_the_deployed_legacy_build(self):
        # The legacy build carries no commit hash, so its stamp is the date
        # its version file was written with: the commit's committer date.
        card = self._pinned("DUCKSTATION")
        assert [(build.release, build.revision, build.stamp) for build in card.builds] == [
            ("preview", "1f6de046b", "2024-09-19T13:41:26+09:00"),
        ]

    @pytest.mark.parametrize(
        ("card", "build"),
        pinned_builds(),
        ids=[f"{card.token}-{build.release}" for card, build in pinned_builds()],
    )
    def test_every_statement_of_the_pin_names_one_commit(
        self, card: StandaloneSaveCard, build: StandaloneBuild
    ):
        # The commit stands three times in a build on purpose: `provenance` is
        # the prose an answer read under that build carries in its `sources`,
        # the citation is where the release and the stamp are tied to the
        # commit, and `revision` is the commit the card's citations were read
        # at. Three places is three chances to drift, so the agreement is a
        # test rather than a habit — a build whose prose still named the old
        # commit would tell a reader one build and the tripwire another.
        assert build.revision in build.provenance, (
            f"{card.token} reads its citations at {build.revision!r} and that build's "
            "provenance names some other commit — the sentence an answer carries and the "
            "commit the card's citations were read at must be the same commit"
        )
        assert build.revision in build.citation, (
            f"{card.token} reads its citations at {build.revision!r} and that build's citation "
            "names some other commit — the reading that ties the release and the stamp to a "
            "commit and the commit the card's citations were read at must be the same commit"
        )
        assert build.release in build.citation, (
            f"{card.token} pins release {build.release!r} and that build's citation ties some "
            "other release to the commit"
        )
        assert build.stamp in build.citation, (
            f"{card.token} pins the stamp {build.stamp!r} and that build's citation ties some "
            "other stamp to the commit — the string the tripwire searches for must be the one "
            "the reading established"
        )

    def test_a_card_with_no_list_pins_nothing(self):
        cards = load_standalone_saves(_synthetic(None))
        assert cards[0].builds == ()

    def test_a_card_finds_the_build_its_release_names(self):
        card = load_standalone_saves(_synthetic([_demo_build()]))[0]
        assert card.build_for(DEMO_RELEASE) == card.builds[0]
        assert card.build_for("9999") is None
        assert card.build_for(None) is None

    @pytest.mark.parametrize("key", ["release", "revision", "stamp", "citation", "provenance"])
    def test_a_block_missing_a_key_is_refused(self, key: str):
        table = _synthetic([_demo_build(**{key: None})])
        with pytest.raises(ValueError, match=BLOCK_SHAPE):
            load_standalone_saves(table)

    def test_a_block_with_an_extra_key_is_refused(self):
        table = _synthetic([_demo_build(mode="by-name")])
        with pytest.raises(ValueError, match=BLOCK_SHAPE):
            load_standalone_saves(table)

    def test_a_block_that_is_not_an_object_is_refused(self):
        table = _synthetic([DEMO_REVISION])
        with pytest.raises(ValueError, match=BLOCK_SHAPE):
            load_standalone_saves(table)

    def test_a_list_that_is_not_a_list_is_refused(self):
        table = _synthetic(_demo_build())
        with pytest.raises(ValueError, match="non-empty list"):
            load_standalone_saves(table)

    def test_an_empty_list_is_refused(self):
        # An empty list states a pin and pins nothing — the absent key is how
        # a card says it pins no build.
        table = _synthetic([])
        with pytest.raises(ValueError, match="non-empty list"):
            load_standalone_saves(table)

    def test_a_lone_build_block_is_refused(self):
        # Schema 2's shape: read under schema 3 it would be a card that pins
        # nothing, which is the silence this file exists to break.
        table = _synthetic(_demo_build(), key="build")
        with pytest.raises(ValueError, match="'builds' list"):
            load_standalone_saves(table)

    @pytest.mark.parametrize("key", ["release", "revision", "stamp"])
    def test_two_builds_under_one_name_are_refused(self, key: str):
        other = _demo_build(release="5678", revision="def5678", stamp="Demo v1.0.0 5678-def5678")
        twin = {**other, key: _demo_build()[key]}
        table = _synthetic([_demo_build(), twin])
        with pytest.raises(ValueError, match=f"one {key} twice"):
            load_standalone_saves(table)

    @pytest.mark.parametrize("key", ["release", "revision", "stamp", "citation", "provenance"])
    def test_an_empty_value_is_refused(self, key: str):
        table = _synthetic([_demo_build(**{key: ""})])
        with pytest.raises(ValueError, match=f"build.{key}"):
            load_standalone_saves(table)

    @pytest.mark.parametrize("key", ["release", "revision", "stamp", "citation", "provenance"])
    def test_a_blank_value_is_refused(self, key: str):
        # A stamp of spaces is a run any binary may hold between two NULs, so
        # it could pass the search against a build it never described; a
        # revision of spaces is a substring of every citation; a release of
        # spaces names no deploy, and a citation or provenance of spaces is
        # one nobody can follow.
        table = _synthetic([_demo_build(**{key: " "})])
        with pytest.raises(ValueError, match=BLANK):
            load_standalone_saves(table)


class TestEveryPinnedCardDescribesTheDeployedBuild:
    """Machine-bound: what the binaries here actually carry."""

    def test_every_deployed_card_carries_one_of_its_pinned_stamps(self):
        binaries = _binaries_or_skip()
        assert stamp_disagreements(pinned_cards(), binaries) == []

    def test_the_probe_really_read_something(self):
        # The search is only worth its hit where the bytes it searched are the
        # build itself: an ELF executable, not an empty file, a launcher
        # script or an AppImage whose build sits compressed inside it, where
        # no stamp could be found and the failure would name the wrong cause.
        binaries = _binaries_or_skip()
        not_elf = [token for token, data in binaries.items() if not data.startswith(b"\x7fELF")]
        assert not_elf == [], (
            "a deployed binary this probe read is not an ELF executable — the stamp search "
            "would be held against something other than the build"
        )

    def test_the_component_version_agrees_with_the_stamp(self):
        # Two readings of one fact: the component recipe writes the tag of the
        # release it downloaded, and the binary carries the stamp of one pinned
        # build, which names the release it is. A mismatch means the deployed
        # binary is not the release the tree beside it names.
        binaries = _binaries_or_skip()
        for card in pinned_cards():
            if card.token not in binaries:
                continue
            found = deployed_build(card, binaries[card.token])
            if found is None:
                continue  # named by the stamp check above
            version = _component_version_of(card.token)
            assert version.is_file(), (
                f"{card.token}: the deployed binary carries {found.stamp!r} and its component "
                f"tree names no release at all ({version.name} is absent) — the two readings "
                "of one release cannot be crossed, so the binary is held against nothing here"
            )
            named = version.read_text(encoding="utf-8").strip()
            assert named == found.release, (
                f"{card.token}: the deployed binary carries {found.stamp!r}, the stamp of "
                f"release {found.release!r}, and the component tree beside it names release "
                f"{named!r} — the binary is not the release that tree was assembled from"
            )
