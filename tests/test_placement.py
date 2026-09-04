"""Tests for atlas.placement — the placement type, its invariants, and layout math."""

from __future__ import annotations

import pytest

from atlas.placement import (
    CAVEAT_CORE_MODE_UNESTABLISHED,
    CAVEAT_FILENAMES_CONTENT_CONDITIONAL,
    CAVEAT_INVALID_SAVE_DIRECTORY,
    CAVEAT_SAVE_ROOT_REDIRECTED,
    CORE_MODE_UNESTABLISHED_REASONS,
    ENUMERATED_DATA,
    REASON_ACTIVE_USER_UNRECORDED,
    UNRESOLVED_EMULATOR_CONFIG_UNREADABLE,
    UNRESOLVED_STANDALONE,
    Caveat,
    Unresolved,
    ROOT_CONTENT_DIRECTORY,
    ROOT_SAVEFILE_DIRECTORY,
    ROOT_SAVESTATE_DIRECTORY,
    STATE_ROOT_CONTENT_DIRECTORY,
    UNKNOWN_FILE_SET,
    FileGroup,
    FileSet,
    SavefilePlacement,
    SavestatePlacement,
    TexturePlacement,
    build_savefile_placement,
    build_savestate_placement,
    file_set_holes,
    needs_with_file_set,
)
from atlas.retroarch_cfg import SAVEFILE_KEYS, SAVESTATE_KEYS, resolve_layout
from tests.shipped_layouts import RETRODECK_SHIPPED



class TestInvariants:
    """M10: invalid states are constructor errors, and values are deeply immutable."""

    def test_unknown_file_set_carries_no_files(self):
        with pytest.raises(ValueError):
            FileSet("unknown", ("a.srm",), "contradiction")

    def test_unknown_file_set_carries_no_completeness_claim(self):
        with pytest.raises(ValueError):
            FileSet("unknown", (), "contradiction", complete=True)

    def test_file_set_state_vocabulary_is_closed(self):
        with pytest.raises(ValueError):
            FileSet("guessed", (), "no such state")  # type: ignore[arg-type]

    def test_a_group_with_an_empty_file_list_is_refused(self):
        """``()`` and ``None`` are different claims, and only one of them is a group.

        An empty list would say *this directory holds nothing*, which is the one
        thing a group never says — a group exists because save data is there.
        The directory whose names are not established says so with ``None``.
        """
        with pytest.raises(ValueError):
            FileGroup(dir="/saves/mame/diff", files=(), granularity="per-game-files", role="disk-diff")

    def test_a_group_may_state_a_directory_without_its_names(self):
        group = FileGroup(
            dir="/saves/mame/diff", files=None, granularity="per-game-files", role="disk-diff"
        )
        assert group.files is None

    def test_a_group_without_names_contributes_nothing_to_the_flat_list(self):
        """The flat list stays the names a caller can actually look for.

        Both groups sit in the answer's own directory, but only one of them has
        names — so ``files`` is that one's, and the other is reachable through
        ``groups`` rather than being folded in or dropped.
        """
        named = FileGroup(
            dir="/saves/mame", files=("dkong",), granularity="per-game-file", role="battery"
        )
        unnamed = FileGroup(
            dir="/saves/mame", files=None, granularity="per-game-files", role="disk-diff"
        )
        file_set = FileSet("declared", ("dkong",), "card", groups=(named, unnamed))
        assert [g.files for g in file_set.groups] == [("dkong",), None]

    def test_the_flat_list_must_still_match_the_named_groups_beside_it(self):
        unnamed = FileGroup(
            dir="/saves/mame", files=None, granularity="per-game-files", role="disk-diff"
        )
        named = FileGroup(
            dir="/saves/mame", files=("dkong",), granularity="per-game-file", role="battery"
        )
        with pytest.raises(ValueError):
            FileSet("declared", ("something-else",), "card", groups=(unnamed, named))

    def test_root_kind_vocabulary_is_closed(self):
        with pytest.raises(ValueError):
            SavefilePlacement(
                dir="/saves",
                root_kind="wherever",  # type: ignore[arg-type]
                needs=(),
                file_set=UNKNOWN_FILE_SET,
                sources=(),
                caveats=(),
            )

    def test_placement_dir_must_be_non_empty(self):
        with pytest.raises(ValueError):
            SavefilePlacement(
                dir="",
                root_kind="savefile_directory",
                needs=(),
                file_set=UNKNOWN_FILE_SET,
                sources=(),
                caveats=(),
            )

    def test_texture_placement_dir_must_be_non_empty(self):
        # A texture question with no directory to name is Unresolved, the same
        # way an unanswerable save placement is.
        with pytest.raises(ValueError):
            TexturePlacement(dir="", needs=(), enabled=None, keying=None, sources=(), caveats=())

    def test_keying_vocabulary_is_closed(self):
        with pytest.raises(ValueError):
            TexturePlacement(
                dir="/mnt/sd/retrodeck/bios/dc/textures",
                needs=(),
                enabled=None,
                keying="per-game",  # type: ignore[arg-type]
                sources=(),
                caveats=(),
            )

    def test_caveat_data_is_read_only(self):
        caveat = Caveat("health", "msg", {"issue": "root-missing"})
        with pytest.raises(TypeError):
            caveat.data["issue"] = "tampered"  # type: ignore[index]
        assert caveat.data == {"issue": "root-missing"}

    def test_caveat_code_must_be_non_empty(self):
        with pytest.raises(ValueError):
            Caveat("", "msg")

HOME = "/home/deck"


def _layout(text):
    return resolve_layout(
        text, keys=SAVEFILE_KEYS, home=HOME, cfg_label="retroarch.cfg", defaults=RETRODECK_SHIPPED
    )


def _build(text, *, content_dir_path=None, content_dir_name=None, library_name=None, **kwargs):
    return build_savefile_placement(
        layout=_layout(text),
        platform_default_dir="/platform/saves",
        content_dir_path=content_dir_path,
        content_dir_name=content_dir_name,
        library_name=library_name,
        **kwargs,
    )


class TestRoots:
    def test_sorted_by_content_concrete(self):
        p = _build(
            'savefile_directory = "/saves"\nsort_savefiles_by_content_enable = "true"\n',
            content_dir_path="/roms/gba",
            content_dir_name="gba",
        )
        assert p.dir == "/saves/gba"
        assert p.root_kind == ROOT_SAVEFILE_DIRECTORY
        assert p.needs == ()

    def test_in_content_dir_is_content_root(self):
        p = _build(
            'savefile_directory = "/saves"\nsavefiles_in_content_dir = "true"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n',
            content_dir_path="/roms/gba",
            content_dir_name="gba",
        )
        assert p.dir == "/roms/gba"
        assert p.root_kind == ROOT_CONTENT_DIRECTORY

    def test_unset_directory_is_platform_default_root(self):
        # platform_unix.c:2133-2134 — defaults are initialized before config load;
        # an unset key means 'saves' under the config tree, never the ROM dir.
        p = _build(
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n',
            content_dir_path="/roms/gba",
            content_dir_name="gba",
        )
        assert p.dir == "/platform/saves"
        assert p.root_kind == ROOT_SAVEFILE_DIRECTORY
        assert p.needs == ()
        assert any("platform default" in s for s in p.sources)

    def test_content_root_still_sorts(self):
        # runloop.c:8785-8841 — in_content_dir picks the root; enabled sorting
        # stages still append afterwards (REVIEW H6).
        p = _build(
            'savefile_directory = "/saves"\nsavefiles_in_content_dir = "true"\n'
            'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n',
            content_dir_path="/roms/gba",
            content_dir_name="gba",
        )
        assert p.dir == "/roms/gba/gba"
        assert p.root_kind == ROOT_CONTENT_DIRECTORY


class TestHoles:
    def test_missing_content_leaves_hole(self):
        p = _build('savefile_directory = "/saves"\nsort_savefiles_by_content_enable = "true"\n')
        assert p.dir == "/saves/<content_dir>"
        assert p.needs == ("content_dir",)

    def test_missing_library_name_leaves_hole(self):
        p = _build(
            'savefile_directory = "/saves"\n'
            'sort_savefiles_by_content_enable = "false"\n'
            'sort_savefiles_enable = "true"\n'
        )
        assert p.dir == "/saves/<library_name>"
        assert p.needs == ("library_name",)

    def test_content_then_core_order(self):
        # runloop.c:8827 then :8835 — content component first, then core.
        p = _build(
            'savefile_directory = "/saves"\n'
            'sort_savefiles_by_content_enable = "true"\n'
            'sort_savefiles_enable = "true"\n',
            content_dir_name="gba",
            library_name="mGBA",
        )
        assert p.dir == "/saves/gba/mGBA"
        assert p.needs == ()

    def test_unfilled_content_dir_root_is_hole(self):
        p = _build(
            'savefiles_in_content_dir = "true"\n'
            'sort_savefiles_by_content_enable = "false"\nsort_savefiles_enable = "false"\n'
        )
        assert p.dir == "<content_dir>"
        assert p.needs == ("content_dir",)

    def test_one_hole_named_twice_is_named_once(self):
        # L4: the content directory really is nested under itself
        # (runloop.c:8789 then :8827), but the caller fills one value.
        p = _build(
            'savefiles_in_content_dir = "true"\n'
            'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n'
        )
        assert p.dir == "<content_dir>/<content_dir>"
        assert p.needs == ("content_dir",)

    def test_deduping_keeps_the_order_the_holes_appear_in(self):
        p = _build(
            'savefiles_in_content_dir = "true"\n'
            'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "true"\n'
        )
        assert p.dir == "<content_dir>/<content_dir>/<library_name>"
        assert p.needs == ("content_dir", "library_name")


class TestFileSetHoles:
    """A file-set template leaves holes too, and they join the directory's."""

    def test_a_resolved_file_set_leaves_no_hole(self):
        assert file_set_holes(("vmu_save_A1.bin", "dc_nvmem.bin")) == ()

    def test_the_save_id_token_is_a_hole(self):
        assert file_set_holes(("<save_id>.A1.bin", "<save_id>.B1.bin")) == ("save_id",)

    def test_the_rom_stem_token_is_not_a_hole(self):
        # The resolver fills it from the content path — by the time a file set
        # exists it is either substituted or the set is unknown.
        assert file_set_holes(("<rom_stem>.ps2",)) == ()

    def test_directory_holes_come_first_and_repeat_once(self):
        assert needs_with_file_set(("content_dir",), ("<save_id>.A1.bin",)) == ("content_dir", "save_id")
        assert needs_with_file_set(("save_id",), ("<save_id>.A1.bin",)) == ("save_id",)


class TestFileSetAndProvenance:
    def test_default_file_set_is_unknown_never_guessed(self):
        p = _build('savefile_directory = "/saves"\n')
        assert p.file_set is UNKNOWN_FILE_SET
        assert p.file_set.state == "unknown"
        assert p.file_set.files == ()

    def test_observed_file_set_carried_through(self):
        fs = FileSet(state="observed", files=("a.srm",), provenance="observed on the machine: /saves")
        p = _build('savefile_directory = "/saves"\n', file_set=fs)
        assert p.file_set == fs

    def test_sources_carry_layout_provenance(self):
        p = _build('savefile_directory = "/saves"\nsort_savefiles_by_content_enable = "true"\n')
        joined = "\n".join(p.sources)
        assert 'retroarch.cfg: savefile_directory = "/saves"' in joined
        assert 'retroarch.cfg: sort_savefiles_by_content_enable = "true"' in joined

    def test_caveats_carried_through(self):
        caveat = Caveat("test-code", "something degraded")
        p = _build('savefile_directory = "/saves"\n', caveats=(caveat,))
        assert p.caveats == (caveat,)
        assert p.caveats[0].code == "test-code"


class TestSavestatePlacementIsTheSaveShapeMinusOneField:
    """The fork the answer grammar makes for savestates, and why it holds.

    No core writes a savestate — the libretro API hands it no savestate
    directory and RetroArch serializes the file itself — so no rule card can
    ever state how one groups them. The field is absent rather than permanently
    ``None``, which is what these tests pin down.
    """

    def test_the_type_carries_no_granularity_at_all(self):
        assert not hasattr(_state('savestate_directory = "/states"\n'), "granularity")

    def test_it_carries_every_other_field_a_save_placement_does(self):
        save = set(SavefilePlacement.__dataclass_fields__)
        state = set(SavestatePlacement.__dataclass_fields__)
        assert save - state == {"granularity"}
        assert state - save == set()

    def test_root_kind_vocabulary_is_its_own(self):
        # The saves root is not a value a savestate placement can hold: a
        # client branching on it must never meet the other question's anchors.
        with pytest.raises(ValueError):
            SavestatePlacement(
                dir="/states",
                root_kind="savefile_directory",  # type: ignore[arg-type]
                needs=(),
                file_set=UNKNOWN_FILE_SET,
                sources=(),
                caveats=(),
            )

    def test_placement_dir_must_be_non_empty(self):
        with pytest.raises(ValueError):
            SavestatePlacement(
                dir="",
                root_kind=ROOT_SAVESTATE_DIRECTORY,
                needs=(),
                file_set=UNKNOWN_FILE_SET,
                sources=(),
                caveats=(),
            )


def _state(text, *, content_dir_path=None, content_dir_name=None, library_name=None, **kwargs):
    return build_savestate_placement(
        layout=resolve_layout(
            text, keys=SAVESTATE_KEYS, home=HOME, cfg_label="retroarch.cfg", defaults=RETRODECK_SHIPPED
        ),
        platform_default_dir="/platform/states",
        content_dir_path=content_dir_path,
        content_dir_name=content_dir_name,
        library_name=library_name,
        **kwargs,
    )


# RetroDECK ships sort-by-content ON for savestates too (its rd_config sets
# sort_savestates_by_content_enable = "true"), so a root test that left the flag
# to the defaults would be testing the sorting stage as well.
UNSORTED = 'sort_savestates_by_content_enable = "false"\n'


class TestSavestateRootsFollowTheSamePathMath:
    """One upstream function places both families (runloop.c:8752-8979), so one port does."""

    def test_the_configured_root_is_the_root(self):
        placement = _state(UNSORTED + 'savestate_directory = "/states"\n')
        assert placement.dir == "/states"
        assert placement.root_kind == ROOT_SAVESTATE_DIRECTORY

    def test_an_unset_root_is_the_platform_default_not_the_content_dir(self):
        placement = _state(UNSORTED, content_dir_path="/roms/gba")
        assert placement.dir == "/platform/states"
        assert placement.root_kind == ROOT_SAVESTATE_DIRECTORY

    def test_in_content_dir_roots_at_the_rom(self):
        placement = _state(
            UNSORTED + 'savestates_in_content_dir = "true"\n', content_dir_path="/roms/gba"
        )
        assert placement.dir == "/roms/gba"
        assert placement.root_kind == STATE_ROOT_CONTENT_DIRECTORY

    def test_sorting_stages_apply_in_upstream_order(self):
        placement = _state(
            'savestate_directory = "/states"\n'
            'sort_savestates_by_content_enable = "true"\n'
            'sort_savestates_enable = "true"\n',
            content_dir_name="gba",
            library_name="mGBA",
        )
        assert placement.dir == "/states/gba/mGBA"

    def test_unfilled_components_stay_holes(self):
        placement = _state(
            'savestate_directory = "/states"\n'
            'sort_savestates_by_content_enable = "true"\n'
            'sort_savestates_enable = "true"\n'
        )
        assert placement.dir == "/states/<content_dir>/<library_name>"
        assert placement.needs == ("content_dir", "library_name")


class TestAnEnumeratedValueIsRefusedAtConstruction:
    """A slug outside its vocabulary raises where it is built, not downstream.

    Closed by convention is not closed. Several of these slugs have no fixture
    machine, so a typo in one of those resolvers used to pass the type check,
    the suite and the vectors alike and reach a client as a value nothing
    documents. The registry is the same one the guide and the corpus tripwire
    read, so the three cannot drift apart.
    """

    def test_a_reason_outside_the_vocabulary_raises(self):
        with pytest.raises(ValueError, match="core-mode-unestablished.reason"):
            Caveat(CAVEAT_CORE_MODE_UNESTABLISHED, "a message", {"reason": "no-such-slug"})

    def test_the_sentence_it_replaced_is_refused_too(self):
        # The exact string this round retired, so a resolver that was missed
        # cannot quietly keep emitting it.
        with pytest.raises(ValueError, match="core-mode-unestablished.reason"):
            Caveat(
                CAVEAT_CORE_MODE_UNESTABLISHED,
                "a message",
                {"reason": "the active user account is not recorded on disk"},
            )

    def test_a_scope_token_outside_the_vocabulary_raises(self):
        with pytest.raises(ValueError, match="filenames-content-conditional"):
            Caveat(
                CAVEAT_FILENAMES_CONTENT_CONDITIONAL,
                "a message",
                {"files_established_for": "content loaded as a single disk image"},
            )

    def test_a_layer_kind_outside_the_vocabulary_raises(self):
        with pytest.raises(ValueError, match="invalid-save-directory.layer"):
            Caveat(
                CAVEAT_INVALID_SAVE_DIRECTORY,
                "a message",
                {"layer": "core override config/mGBA/mGBA.cfg"},
            )

    def test_a_refusal_reason_outside_the_vocabulary_raises(self):
        with pytest.raises(ValueError, match="emulator-config-unreadable.reason"):
            Unresolved(
                UNRESOLVED_EMULATOR_CONFIG_UNREADABLE,
                "a message",
                {"reason": "/dev_hdd0/ is unread"},
            )

    def test_every_member_of_a_vocabulary_is_accepted(self):
        for reason in CORE_MODE_UNESTABLISHED_REASONS:
            assert Caveat(
                CAVEAT_CORE_MODE_UNESTABLISHED, "a message", {"reason": reason}
            ).data["reason"] == reason

    def test_the_same_key_under_another_code_is_untouched(self):
        # ``reason`` is only an enumeration where the registry says so; a code
        # that has no vocabulary for the key keeps taking any value.
        assert Caveat(UNRESOLVED_STANDALONE, "a message", {"reason": "anything at all"}).data
        # And a key with no entry under an enumerated code is free too.
        assert Caveat(
            CAVEAT_CORE_MODE_UNESTABLISHED,
            "a message",
            {"reason": REASON_ACTIVE_USER_UNRECORDED, "core": "RPCS3"},
        ).data["core"] == "RPCS3"

    def test_the_registry_names_only_real_codes_and_closed_tuples(self):
        for (code, key), vocabulary in ENUMERATED_DATA.items():
            assert code, f"{vocabulary}: a registry entry with no code"
            assert key, f"{code}: a registry entry with no data key"
            assert vocabulary, f"{code}.{key} names an empty vocabulary"
            assert len(set(vocabulary)) == len(vocabulary), f"{code}.{key} repeats a value"
        # A code with no enumeration must not be listed by accident.
        assert (CAVEAT_SAVE_ROOT_REDIRECTED, "key") not in ENUMERATED_DATA
