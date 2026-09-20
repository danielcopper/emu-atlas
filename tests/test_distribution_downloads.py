"""The distribution download list: packaged shape, and a loader that never coerces."""

import json

import pytest

from atlas.distribution_downloads import (
    DOWNLOAD_INVOCATIONS,
    DOWNLOAD_KINDS,
    INVOKED_RETROARCH_SETUP,
    INVOKED_UNESTABLISHED,
    load_distribution_downloads,
    lookup_distribution_downloads,
)

ENTRY = {
    "kind": "tree",
    "destination": "PPSSPP",
    "url": "https://buildbot.libretro.com/assets/system/PPSSPP.zip",
    "condition": "only where the directory is empty when the step runs",
    "invoked": "retroarch-setup",
    "citation": "[V-script] emuDeckRetroArch.sh:333-334 at acc45fc",
}


def _table(entries, **distribution):
    return json.dumps(
        {
            "schema": 1,
            "version": "1",
            "reviewed": "2026-09-20",
            "distributions": {
                "emudeck": {"version": "acc45fc", "entries": entries, **distribution},
            },
        }
    )


class TestThePackagedCard:
    def test_emudeck_is_pinned_and_populated(self):
        card = lookup_distribution_downloads("emudeck")
        assert card is not None
        assert card.version == "acc45fc"
        assert card.card_version == "1"
        assert card.entries

    def test_every_entry_states_a_known_kind_and_a_known_invocation(self):
        card = lookup_distribution_downloads("emudeck")
        assert card is not None
        assert {entry.kind for entry in card.entries} <= set(DOWNLOAD_KINDS)
        assert {entry.invoked for entry in card.entries} <= set(DOWNLOAD_INVOCATIONS)

    def test_the_declared_file_that_opened_the_issue_resolves(self):
        # ppsspp_libretro.info declares PPSSPP/ppge_atlas.zim, and EmuDeck's
        # installer fills PPSSPP by unpacking an archive it fetched.
        card = lookup_distribution_downloads("emudeck")
        assert card is not None
        entry = card.covering("PPSSPP/ppge_atlas.zim")
        assert entry is not None
        assert entry.url == "https://buildbot.libretro.com/assets/system/PPSSPP.zip"
        assert entry.invoked == INVOKED_RETROARCH_SETUP

    def test_the_rpg_maker_step_is_recorded_and_answers_nothing(self):
        # Two entries nothing in the read repository calls. They are in the
        # table so the next reading starts from the measurement rather than
        # from scratch, and they answer no path, because a statement about a
        # step no caller reaches would be a guess.
        card = lookup_distribution_downloads("emudeck")
        assert card is not None
        recorded = {
            entry.destination for entry in card.entries if entry.invoked == INVOKED_UNESTABLISHED
        }
        assert recorded == {"rtp/2000", "rtp/2003"}
        assert card.covering("rtp/2000/RPG2000/harmony.dll") is None
        assert card.covering("rtp/2003/RPG2003/ultima.wav") is None

    def test_a_directory_no_step_fills_is_not_covered(self):
        card = lookup_distribution_downloads("emudeck")
        assert card is not None
        assert card.covering("dc/dc_boot.bin") is None

    def test_a_tree_answers_below_itself_and_not_for_itself(self):
        # The destination IS the directory, so a declaration of the directory
        # is not a declaration of something the download put in it.
        card = lookup_distribution_downloads("emudeck")
        assert card is not None
        assert card.covering("PPSSPP") is None
        assert card.covering("PPSSPPX/ppge_atlas.zim") is None

    def test_an_unknown_distribution_is_none(self):
        assert lookup_distribution_downloads("retrodeck") is None
        assert lookup_distribution_downloads("emudek") is None
        assert lookup_distribution_downloads(None) is None


class TestTheLoaderRefusesWhatItCannotState:
    """Every table is built before the `with`, so the block holds only the call under test."""

    def test_an_unsupported_schema_fails(self):
        table = json.dumps({"schema": 2, "version": "1", "reviewed": "x", "distributions": {}})
        with pytest.raises(ValueError, match="unsupported schema"):
            load_distribution_downloads(table)

    def test_a_kind_outside_the_vocabulary_fails(self):
        table = _table([{**ENTRY, "kind": "archive"}])
        with pytest.raises(ValueError, match="kind must be one of"):
            load_distribution_downloads(table)

    def test_an_invocation_outside_the_vocabulary_fails(self):
        # 'runs' is the word the two in the tuple exist to avoid, and
        # 'retroarch-install' is the spelling this one deliberately is not:
        # the step runs from RetroArch_init and RetroArch_update, never from
        # RetroArch_install.
        for word in ("runs", "retroarch-install"):
            table = _table([{**ENTRY, "invoked": word}])
            with pytest.raises(ValueError, match="invoked must be one of"):
                load_distribution_downloads(table)

    def test_a_url_that_is_not_https_fails(self):
        table = _table([{**ENTRY, "url": "http://buildbot.libretro.com/assets/system/PPSSPP.zip"}])
        with pytest.raises(ValueError, match="expected an https:// url"):
            load_distribution_downloads(table)

    def test_an_absolute_destination_fails(self):
        table = _table([{**ENTRY, "destination": "/bios/PPSSPP"}])
        with pytest.raises(ValueError, match="clean relative path"):
            load_distribution_downloads(table)

    def test_a_parent_escape_in_the_destination_fails(self):
        table = _table([{**ENTRY, "destination": "../PPSSPP"}])
        with pytest.raises(ValueError, match="clean relative path"):
            load_distribution_downloads(table)

    def test_an_empty_citation_fails(self):
        table = _table([{**ENTRY, "citation": ""}])
        with pytest.raises(ValueError, match="citation: expected a non-empty string"):
            load_distribution_downloads(table)

    def test_an_entry_with_extra_keys_fails(self):
        table = _table([{**ENTRY, "checksum": "none"}])
        with pytest.raises(ValueError, match="an entry names exactly"):
            load_distribution_downloads(table)

    def test_an_entry_missing_a_key_fails(self):
        without = {key: value for key, value in ENTRY.items() if key != "condition"}
        table = _table([without])
        with pytest.raises(ValueError, match="an entry names exactly"):
            load_distribution_downloads(table)

    def test_a_repeated_destination_fails(self):
        # The statement is per directory, so two entries on one destination
        # would be stated once and the URL a reader saw would be whichever
        # entry the scan met first.
        table = _table([ENTRY, {**ENTRY, "url": "https://example.invalid/other.zip"}])
        with pytest.raises(ValueError, match="same destination"):
            load_distribution_downloads(table)

    @pytest.mark.parametrize("order", [("rtp", "rtp/2000"), ("rtp/2000", "rtp")])
    def test_a_destination_inside_another_one_fails(self, order: tuple[str, str]):
        # The second form of the same collision, and the one written order
        # alone would decide: with both entries loaded, a path below the
        # nested destination is covered by two of them, and `covering`
        # answers whichever came first. Both orders, because a rule that
        # caught one of them would be a rule about the writing, not the card.
        outer, inner = order
        table = _table(
            [
                {**ENTRY, "destination": outer},
                {**ENTRY, "destination": inner, "url": "https://example.invalid/other.zip"},
            ]
        )
        with pytest.raises(ValueError, match="contains the destination"):
            load_distribution_downloads(table)

    def test_a_distribution_with_no_entries_fails(self):
        table = _table([])
        with pytest.raises(ValueError, match="entries must be a non-empty list"):
            load_distribution_downloads(table)

    def test_a_distribution_missing_its_provenance_fails(self):
        table = json.dumps(
            {
                "schema": 1,
                "version": "1",
                "reviewed": "2026-09-20",
                "distributions": {"emudeck": {"entries": [ENTRY]}},
            }
        )
        with pytest.raises(ValueError, match="expected exactly"):
            load_distribution_downloads(table)

    def test_a_card_without_its_own_version_fails(self):
        table = json.dumps({"schema": 1, "reviewed": "2026-09-20", "distributions": {}})
        with pytest.raises(ValueError, match="distribution_downloads: version"):
            load_distribution_downloads(table)

    def test_distributions_that_are_not_an_object_fail(self):
        table = json.dumps(
            {"schema": 1, "version": "1", "reviewed": "2026-09-20", "distributions": ["emudeck"]}
        )
        with pytest.raises(ValueError, match="distributions must be an object"):
            load_distribution_downloads(table)
