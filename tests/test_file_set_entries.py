"""One entry per file in a save answer (#689): the type refusing a repeat, and the merge.

The resolvers' half — Dolphin, DuckStation, PCSX2 and xemu meeting one file
through two settings, MAME and Genesis Plus GX meeting one through the
content's name, Kronos keeping one name in two directories, Flycast's save
root on the system directory's ``dc`` — is held by the vectors in
``vectors/machines/standalone-saves.json``, ``save-file-sets.json`` and
``named-cases.json``. What is here is what a vector cannot reach: the type
refusing every shape of a repeat, the merge on shapes no fixture machine
builds, and the two card routes no shipped card collides on.
"""

from __future__ import annotations

import pytest

from atlas.installations import (
    _CrossParts,  # pyright: ignore[reportPrivateUsage] - the assembly steps are the units under test
    _card_file_set,  # pyright: ignore[reportPrivateUsage]
    _card_files,  # pyright: ignore[reportPrivateUsage]
    _Content,  # pyright: ignore[reportPrivateUsage]
    _content_groupings,  # pyright: ignore[reportPrivateUsage]
    _frontend_sizes,  # pyright: ignore[reportPrivateUsage]
    _grouped_file_set,  # pyright: ignore[reportPrivateUsage]
    _observed_groups,  # pyright: ignore[reportPrivateUsage]
    _one_entry_per_file,  # pyright: ignore[reportPrivateUsage]
    _with_cross_parts,  # pyright: ignore[reportPrivateUsage]
)
from atlas.machine import FixtureMachine
from atlas.oddities import CoreCard, SaveGroup, SaveMode, load_oddities
from atlas.placement import (
    CAVEAT_FILE_SET_CLAIMS_MERGED,
    FILE_SET_DECLARED,
    FILE_SET_OBSERVED,
    GRANULARITY_PER_GAME_FILE,
    GRANULARITY_SHARED_CARD,
    GRANULARITY_SHARED_FILE,
    ROLE_BATTERY,
    ROLE_MEMORY_CARD,
    ROLE_SETTINGS,
    ROLE_UNKNOWN,
    Caveat,
    FileGroup,
    FileSet,
    SizeFact,
    TEMPLATE_ROM_STEM,
)

CITATION = "[V] libretro.c:1-2 at example abc1234"


def _group(
    name: str,
    *,
    directory: str = "/saves",
    granularity: str = GRANULARITY_SHARED_FILE,
    role: str = ROLE_BATTERY,
    size_fixed: bool | None = None,
) -> FileGroup:
    return FileGroup(
        dir=directory, files=(name,), granularity=granularity, role=role, size_fixed=size_fixed
    )


def _unnamed(directory: str = "/saves") -> FileGroup:
    return FileGroup(dir=directory, files=None, granularity="per-game-files", role=ROLE_BATTERY)


class TestTheTypeRefusesARepeat:
    """``FileSet`` states each directory and name once, wherever it lies."""

    def test_two_groups_naming_one_file_are_refused_where_files_repeats_it_too(self):
        # The shape every resolver used to build: the flat list composed from
        # the groups repeats the name as well, so the agreement check passed it.
        groups = (_group("x.sav"), _group("x.sav", role=ROLE_MEMORY_CARD))
        with pytest.raises(ValueError, match="more than one entry"):
            FileSet(FILE_SET_DECLARED, ("x.sav", "x.sav"), "card", groups=groups)

    def test_one_group_naming_a_file_twice_is_refused(self):
        group = FileGroup(
            dir="/saves", files=("x.sav", "x.sav"), granularity="per-game-files", role=ROLE_BATTERY
        )
        with pytest.raises(ValueError, match="more than one entry"):
            FileSet(FILE_SET_DECLARED, ("x.sav", "x.sav"), "card", groups=(group,))

    def test_a_repeat_under_another_directory_is_refused(self):
        # The agreement check reads only the answer's own directory, so a
        # repeat elsewhere had nothing to hold it.
        groups = (_group("own.sav"), _group("x.sav", directory="/b"), _group("x.sav", directory="/b"))
        with pytest.raises(ValueError, match="more than one entry"):
            FileSet(FILE_SET_DECLARED, ("own.sav",), "card", groups=groups)

    def test_an_observed_set_is_held_too(self):
        groups = (_group("x.sav"), _group("x.sav", granularity=GRANULARITY_PER_GAME_FILE))
        with pytest.raises(ValueError, match="more than one entry"):
            FileSet(FILE_SET_OBSERVED, ("x.sav", "x.sav"), "seen", groups=groups)

    def test_one_name_in_two_directories_is_two_files(self):
        groups = (_group("x.sav", directory="/a"), _group("x.sav", directory="/b"))
        file_set = FileSet(FILE_SET_DECLARED, ("x.sav",), "card", groups=groups)
        assert [g.dir for g in file_set.groups] == ["/a", "/b"]

    def test_one_unnamed_directory_stated_twice_alike_is_refused(self):
        # Two folder slots on one directory are one claim made twice (D4): the
        # answer states it once, and the type refuses the second.
        groups = (_unnamed(), _unnamed())
        with pytest.raises(ValueError, match="stated twice alike"):
            FileSet(FILE_SET_DECLARED, (), "card", groups=groups)

    def test_unnamed_groups_of_one_directory_that_differ_are_both_stated(self):
        # Not decided, and built by no route — so not refused either.
        other = FileGroup(dir="/saves", files=None, granularity="per-game-files", role=ROLE_SETTINGS)
        file_set = FileSet(FILE_SET_DECLARED, (), "card", groups=(_unnamed(), other))
        assert [g.role for g in file_set.groups] == [ROLE_BATTERY, ROLE_SETTINGS]

    def test_an_unnamed_group_beside_a_named_one_in_its_directory_is_no_repeat(self):
        file_set = FileSet(FILE_SET_DECLARED, ("x.sav",), "card", groups=(_group("x.sav"), _unnamed()))
        assert [g.files for g in file_set.groups] == [("x.sav",), None]


class TestTheMergedClaimsVocabulary:
    """The words a merged collision lists are the words a group's own fields are held to."""

    def test_a_role_outside_the_vocabulary_is_refused(self):
        with pytest.raises(ValueError, match="file-set-claims-merged.roles"):
            Caveat(CAVEAT_FILE_SET_CLAIMS_MERGED, "a message", {"roles": ("battery", "save")})

    def test_a_granularity_outside_the_vocabulary_is_refused(self):
        with pytest.raises(ValueError, match="file-set-claims-merged.granularities"):
            Caveat(CAVEAT_FILE_SET_CLAIMS_MERGED, "a message", {"granularities": ("per-game",)})


class TestTheMerge:
    """``_one_entry_per_file``: D1 for what disagrees, D2 for naming it, D3 for what agrees."""

    def test_claims_that_disagree_are_one_entry_keeping_only_what_they_share(self):
        card = _group("x.sav", role=ROLE_MEMORY_CARD, size_fixed=True)
        cartridge = _group("x.sav", role=ROLE_BATTERY)
        groups, caveats = _one_entry_per_file([card, cartridge])
        assert groups == (
            FileGroup(
                dir="/saves",
                files=("x.sav",),
                granularity=GRANULARITY_SHARED_FILE,
                role=ROLE_UNKNOWN,
                size_fixed=None,
            ),
        )
        assert [(c.code, dict(c.data)) for c in caveats] == [
            (
                CAVEAT_FILE_SET_CLAIMS_MERGED,
                {
                    "dir": "/saves",
                    "file": "x.sav",
                    "roles": ("memory-card", "battery"),
                    "granularities": ("shared-file",),
                    "size_fixed": ("true", "null"),
                },
            )
        ]

    def test_granularities_that_differ_state_shared_file(self):
        per_game = _group("default.cfg", granularity=GRANULARITY_PER_GAME_FILE, role=ROLE_SETTINGS)
        shared = _group("default.cfg", granularity=GRANULARITY_SHARED_FILE, role=ROLE_SETTINGS)
        groups, caveats = _one_entry_per_file([per_game, shared])
        assert [(g.granularity, g.role) for g in groups] == [(GRANULARITY_SHARED_FILE, ROLE_SETTINGS)]
        assert [c.data["granularities"] for c in caveats] == [("per-game-file", "shared-file")]

    def test_granularities_that_agree_are_kept_even_when_not_shared_file(self):
        # Two shared cards meeting is no reason to call the file anything else.
        first = _group("card.mcd", granularity=GRANULARITY_SHARED_CARD, size_fixed=True)
        second = _group("card.mcd", granularity=GRANULARITY_SHARED_CARD, size_fixed=False)
        groups, _ = _one_entry_per_file([first, second])
        assert [(g.granularity, g.size_fixed) for g in groups] == [(GRANULARITY_SHARED_CARD, None)]

    def test_claims_that_agree_in_every_field_are_one_entry_and_no_caveat(self):
        slot = _group("card.mcd", granularity=GRANULARITY_SHARED_CARD, role=ROLE_MEMORY_CARD, size_fixed=True)
        groups, caveats = _one_entry_per_file([slot, slot])
        assert (groups, caveats) == ((slot,), ())

    def test_the_entry_keeps_the_first_claims_position(self):
        groups, _ = _one_entry_per_file(
            [_group("x.sav"), _group("y.sav"), _group("x.sav", role=ROLE_SETTINGS)]
        )
        assert [g.files for g in groups] == [("x.sav",), ("y.sav",)]

    def test_each_list_names_a_value_once_in_the_order_the_claims_came(self):
        groups = [
            _group("x.sav", role=ROLE_BATTERY, size_fixed=False),
            _group("x.sav", role=ROLE_SETTINGS, size_fixed=None),
            _group("x.sav", role=ROLE_BATTERY, size_fixed=True),
        ]
        _, caveats = _one_entry_per_file(groups)
        assert [(c.data["roles"], c.data["size_fixed"]) for c in caveats] == [
            (("battery", "settings"), ("false", "null", "true"))
        ]

    def test_unnamed_groups_stating_one_directory_alike_are_one_entry_and_no_caveat(self):
        # D4: two slots on one folder card.
        groups, caveats = _one_entry_per_file(
            [_unnamed(), _group("x.sav"), _unnamed(), _group("x.sav")]
        )
        assert [g.files for g in groups] == [None, ("x.sav",)]
        assert caveats == ()

    def test_unnamed_groups_that_differ_or_lie_apart_pass_through_where_they_stood(self):
        settings = FileGroup(dir="/saves", files=None, granularity="per-game-files", role=ROLE_SETTINGS)
        groups, caveats = _one_entry_per_file([_unnamed(), _unnamed("/other"), settings])
        assert [(g.dir, g.role) for g in groups] == [
            ("/saves", ROLE_BATTERY),
            ("/other", ROLE_BATTERY),
            ("/saves", ROLE_SETTINGS),
        ]
        assert caveats == ()

    def test_a_partial_overlap_is_left_for_the_type_to_refuse(self):
        # No route builds one: every group that can meet another names a
        # single file. What it should state is undecided, so the merge does
        # not guess and the set refuses it.
        both = FileGroup(
            dir="/saves", files=("x.sav", "y.sav"), granularity="per-game-files", role=ROLE_BATTERY
        )
        groups, caveats = _one_entry_per_file([both, _group("y.sav")])
        assert (groups, caveats) == ((both, _group("y.sav")), ())
        with pytest.raises(ValueError, match="more than one entry"):
            _grouped_file_set(
                FILE_SET_DECLARED, ("x.sav", "y.sav", "y.sav"), "card", complete=False, groups=groups
            )

    def test_the_flat_list_states_each_name_once_too(self):
        file_set, _ = _grouped_file_set(
            FILE_SET_DECLARED,
            ("x.sav", "y.sav", "x.sav"),
            "card",
            complete=False,
            groups=(_group("x.sav"), _group("y.sav"), _group("x.sav", role=ROLE_SETTINGS)),
        )
        assert file_set.files == ("x.sav", "y.sav")


# A mode whose per-game name comes out equal to a shared one for content
# named ``shared`` — the shape of Genesis Plus GX's ``<rom_stem>.brm`` beside
# its ``4Mbit_cart.brm``, in a directory of the core's own choosing.
COLLIDING = SaveMode(
    root="system_directory",
    groups=(
        SaveGroup(subdir="core", files=("<rom_stem>.brm",), granularity="per-game-file", role="battery"),
        SaveGroup(subdir="core", files=("shared.brm",), granularity="shared-file", role="battery"),
    ),
)


class TestTheCardRoutes:
    """The routes no shipped card collides on, held with a mode that does."""

    def test_an_observed_name_two_groups_declare_reaches_the_merge_as_two_claims(self):
        groups = _observed_groups(
            ("shared.brm",), directory="/sys/core", rom_stem="shared", mode=COLLIDING
        )
        assert [g.granularity for g in groups] == ["per-game-file", "shared-file"]

    def test_a_declared_card_set_states_the_file_once(self):
        card = load_oddities()[0]
        file_set, caveats = _card_file_set(
            FixtureMachine({}),
            card=card,
            mode=COLLIDING,
            directory="/sys/core",
            rom_stem="shared",
            content_dir_name=None,
            observable=False,
        )
        assert (file_set.state, file_set.files) == (FILE_SET_DECLARED, ("shared.brm",))
        assert [(g.granularity, g.role) for g in file_set.groups] == [("shared-file", "battery")]
        assert [c.data["granularities"] for c in caveats] == [("per-game-file", "shared-file")]

    def test_an_observed_card_set_states_the_file_once(self):
        card = load_oddities()[0]
        file_set, caveats = _card_file_set(
            FixtureMachine({"/sys/core/shared.brm": "x"}),
            card=card,
            mode=COLLIDING,
            directory="/sys/core",
            rom_stem="shared",
            content_dir_name=None,
            observable=True,
        )
        assert (file_set.state, file_set.files) == (FILE_SET_OBSERVED, ("shared.brm",))
        assert [(g.granularity, g.role) for g in file_set.groups] == [("shared-file", "battery")]
        assert [c.code for c in caveats] == [CAVEAT_FILE_SET_CLAIMS_MERGED]


class TestThePartsUnderAnotherRoot:
    """``_with_cross_parts``: a part that resolves into the answer's own directory."""

    OWN = FileSet(
        FILE_SET_DECLARED,
        ("<save_id>.A1.bin",),
        "card",
        groups=(_group("<save_id>.A1.bin", directory="/bios/dc", role=ROLE_MEMORY_CARD),),
    )

    def test_its_names_join_the_answers_own_files(self):
        cross = _CrossParts(groups=(_group("vmu_save_B1.bin", directory="/bios/dc"),))
        file_set, caveats = _with_cross_parts(self.OWN, cross)
        assert file_set.files == ("<save_id>.A1.bin", "vmu_save_B1.bin")
        assert caveats == ()

    def test_a_part_elsewhere_leaves_files_alone(self):
        cross = _CrossParts(groups=(_group("vmu_save_B1.bin", directory="/elsewhere"),))
        file_set, _ = _with_cross_parts(self.OWN, cross)
        assert file_set.files == ("<save_id>.A1.bin",)
        assert [g.dir for g in file_set.groups] == ["/bios/dc", "/elsewhere"]

    def test_a_part_naming_a_file_already_there_is_stated_once(self):
        cross = _CrossParts(groups=(_group("<save_id>.A1.bin", directory="/bios/dc"),))
        file_set, caveats = _with_cross_parts(self.OWN, cross)
        assert file_set.files == ("<save_id>.A1.bin",)
        assert [g.role for g in file_set.groups] == [ROLE_UNKNOWN]
        assert [c.data["roles"] for c in caveats] == [("memory-card", "battery")]


class TestRetroArchsSizeClaimIsByDirectoryAndName:
    """``_frontend_sizes`` applies RetroArch's claim to the file RetroArch writes, and no other."""

    def test_a_group_elsewhere_with_the_same_name_keeps_its_own(self):
        file_set = FileSet(
            FILE_SET_DECLARED,
            ("game.srm",),
            "card",
            groups=(_group("game.srm"), _group("game.srm", directory="/elsewhere")),
        )
        sized, _ = _frontend_sizes(
            file_set, (("game.srm", SizeFact(True, CITATION)),), (), directory="/saves"
        )
        assert [(g.dir, g.size_fixed) for g in sized.groups] == [
            ("/saves", True),
            ("/elsewhere", None),
        ]


def _content(stem: str | None) -> _Content:
    return _Content(rom_stem=stem)


class TestTheGranularityBlockFollowsTheMerge:
    """``_content_groupings``: the words a granularity block states are the merged groups'."""

    def test_a_collision_makes_the_answers_word_shared_file(self):
        assert _content_groupings(COLLIDING, _content("shared")) == ("shared-file",)

    def test_without_a_collision_the_cards_own_list_stands(self):
        assert _content_groupings(COLLIDING, _content("Game")) == COLLIDING.granularities

    def test_without_a_content_nothing_can_meet(self):
        assert _content_groupings(COLLIDING, _content(None)) == COLLIDING.granularities

    def test_one_name_in_two_directories_does_not_meet(self):
        apart = SaveMode(
            root="savefile_directory",
            groups=(
                SaveGroup(subdir="a", files=("<rom_stem>.ram",), granularity="per-game-files", role="battery"),
                SaveGroup(subdir="b", files=("<rom_stem>.ram",), granularity="per-game-file", role="battery"),
            ),
        )
        assert _content_groupings(apart, _content("Game")) == ("per-game-files", "per-game-file")

    def test_every_shipped_mode_reads_the_cards_list_where_nothing_collides(self):
        # The helper replaces the card's own list on every route that states a
        # granularity, so for content whose name meets nothing it must be that
        # list exactly, order included — the corpus' alternatives depend on it.
        modes = [mode for card in load_oddities() for mode in card.modes.values()]
        assert modes
        changed = [
            mode
            for mode in modes
            if _content_groupings(mode, _content("No Collision (World)")) != mode.granularities
        ]
        assert changed == []


def _stems_that_collide(mode: SaveMode) -> list[str]:
    """Every content stem that makes a ``<rom_stem>`` name of *mode* equal a literal one."""
    names = [name for group in mode.groups for name in group.files or ()]
    names += [name for group in mode.groups for name in group.observe or ()]
    stems: list[str] = []
    for template in [name for name in names if name.count(TEMPLATE_ROM_STEM) == 1]:
        head, tail = template.split(TEMPLATE_ROM_STEM)
        for literal in [name for name in names if TEMPLATE_ROM_STEM not in name]:
            fits = literal.startswith(head) and literal.endswith(tail)
            if fits and len(literal) > len(head) + len(tail):
                stems.append(literal[len(head) : len(literal) - len(tail)])
    return stems


def _first_directory_names(groups: list[FileGroup]) -> tuple[str, ...]:
    return tuple(
        name for group in groups if group.dir == groups[0].dir for name in group.files or ()
    )


class TestNoShippedCardBuildsARepeatTheMergeLeaves:
    """Every shipped mode, under every content name that makes two of its files one.

    The merge joins entries naming the same files and leaves a partial overlap
    for :class:`~atlas.placement.FileSet` to refuse, because what one should
    state is undecided. That refusal is a raise, so this walk is what keeps it
    from reaching a user: a card that one day builds a partial overlap — or any
    other repeat the merge does not join — fails here, in CI, instead. Each
    mode is assembled the way both card routes assemble it: declared, every
    group in its own directory, and observed, every name the answer's own
    directory watches for found at once.
    """

    def _cases(self) -> list[tuple[CoreCard, str, SaveMode, str]]:
        return [
            (card, name, mode, stem)
            for card in load_oddities()
            for name, mode in card.modes.items()
            if mode.stated is None
            for stem in ["Game (World)", *_stems_that_collide(mode)]
        ]

    def test_the_walk_reaches_the_collisions_it_exists_for(self):
        collide = {(card.key, stem) for card, _, _, stem in self._cases() if stem != "Game (World)"}
        assert ("genesis_plus_gx", "4Mbit_cart") in collide
        assert ("mame", "default") in collide

    def test_every_declared_mode_is_a_legal_set(self):
        refused: list[str] = []
        for card, name, mode, stem in self._cases():
            groups = [
                FileGroup(
                    dir=f"/{group.root or mode.root}/{group.subdir or ''}",
                    files=_card_files(group.files, stem) if group.files is not None else None,
                    granularity=group.granularity,
                    role=group.role,
                )
                for group in mode.groups
            ]
            try:
                _grouped_file_set(
                    FILE_SET_DECLARED,
                    _first_directory_names(groups),
                    "card",
                    complete=False,
                    groups=groups,
                )
            except ValueError as error:
                refused.append(f"{card.key}/{name} for {stem!r}: {error}")
        assert refused == []

    def test_no_name_retroarch_writes_meets_another_groups_name(self):
        # RetroArch's size claim is applied after the merge, so a file it
        # writes that merged with another group's would take its claim over
        # the null the merge stated. That needs a frontend-written name to
        # come out equal to another group's in one directory, and no shipped
        # mode builds one under any content name this walk tries.
        met: list[str] = []
        for card, name, mode, stem in self._cases():
            for written in [group for group in mode.groups if group.frontend is not None]:
                mine = set(_card_files(written.files or (), stem) or ())
                for other in mode.groups:
                    same_dir = (other.root, other.subdir) == (written.root, written.subdir)
                    theirs = set(_card_files(other.files or (), stem) or ())
                    if other is not written and same_dir and mine & theirs:
                        met.append(f"{card.key}/{name} for {stem!r}: {sorted(mine & theirs)}")
        assert met == []

    def test_every_observed_mode_is_a_legal_set(self):
        refused: list[str] = []
        for card, name, mode, stem in self._cases():
            watched = [
                found
                for group in mode.here
                for found in _card_files(
                    group.observe if group.observe is not None else group.files or (), stem
                )
                or ()
            ]
            found = tuple(dict.fromkeys(watched))
            if not found:
                continue
            try:
                _grouped_file_set(
                    FILE_SET_OBSERVED,
                    found,
                    "seen",
                    complete=False,
                    groups=_observed_groups(found, directory="/saves", rom_stem=stem, mode=mode),
                )
            except ValueError as error:
                refused.append(f"{card.key}/{name} for {stem!r}: {error}")
        assert refused == []
