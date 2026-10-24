"""Whether a save file keeps its size (#681): the field, the packaged claims, the guards.

The resolver's half — the standard rule's groups, ``save_file_compression``
read live, the trailing-fill caveat, a card's frontend-written file observed
and declared, the folder saves — is held by the vectors in
``vectors/machines/save-file-sets.json`` and ``standalone-saves.json``. What is
here is everything a vector cannot reach: the types refusing a claim that is
not one, the loaders refusing a table that half-states one, and the packaged
data stating the values the issue decided.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from atlas.installations import (
    _card_group_size,  # pyright: ignore[reportPrivateUsage] - the claim's derivation is the unit under test
    _card_sources,  # pyright: ignore[reportPrivateUsage]
    _declared_groups,  # pyright: ignore[reportPrivateUsage]
    _dolphin_size_evidence,  # pyright: ignore[reportPrivateUsage]
    _DolphinSlot,  # pyright: ignore[reportPrivateUsage]
    _fill_rule_sizes,  # pyright: ignore[reportPrivateUsage]
    _observed_groups,  # pyright: ignore[reportPrivateUsage]
    _one_entry_per_file,  # pyright: ignore[reportPrivateUsage]
)
from atlas.oddities import CoreCard, SaveGroup, SaveMode, load_oddities
from atlas.placement import (
    GRANULARITY_PER_GAME_FILE,
    ROLE_BATTERY,
    SIZE_FILL_FF,
    Caveat,
    CAVEAT_SIZE_FIXED_EXCEPT_TRAILING_FILL,
    FileGroup,
    SizeFact,
)
from atlas.save_memory import (
    MEMORY_RTC,
    MEMORY_SAVE_RAM,
    SAVE_MEMORY_SCHEMA,
    load_save_memory,
)
from atlas.standalone_saves import lookup_standalone_save_card
from scripts.validate_vectors import VectorError, validate_machines_vector
from tests.test_validate_vectors import (
    _base_placement,  # pyright: ignore[reportPrivateUsage] - the validator suite's own shapes
    _file_set,  # pyright: ignore[reportPrivateUsage]
    _group,  # pyright: ignore[reportPrivateUsage]
)

CITATION = "[V] libretro.c:1-2 at example abc1234"


def _doc(systems: dict[str, Any]) -> str:
    entry = {
        "identifiers": {"library_name": ["Example"]},
        "systems": systems,
        "provenance": {"source": "read at the revision the binary names"},
    }
    return json.dumps({"schema": SAVE_MEMORY_SCHEMA, "cores": {"example": entry}})


def _entry(memory_types: list[str], size: dict[str, Any] | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "memory_types": memory_types,
        "verified_core": "1.0 abc1234",
        "citation": CITATION,
    }
    if size is not None:
        entry["size"] = size
    return entry


def _fact(fixed: bool | None, **extra: Any) -> dict[str, Any]:
    return {"fixed": fixed, "citation": CITATION, **extra}


class TestTheField:
    def test_a_group_states_true_false_or_nothing(self):
        for value in (True, False, None):
            group = FileGroup(
                dir="/saves",
                files=("a.srm",),
                granularity=GRANULARITY_PER_GAME_FILE,
                role=ROLE_BATTERY,
                size_fixed=value,
            )
            assert group.size_fixed is value

    def test_a_group_left_unsaid_is_not_established(self):
        group = FileGroup(
            dir="/saves", files=("a.srm",), granularity=GRANULARITY_PER_GAME_FILE, role=ROLE_BATTERY
        )
        assert group.size_fixed is None

    @pytest.mark.parametrize("value", ["false", 0, 1])
    def test_a_group_refuses_a_claim_that_is_not_a_boolean(self, value):
        # bool("false") is True; a truthy string would be read as "fixed".
        with pytest.raises(ValueError, match="size_fixed must be true, false or None"):
            FileGroup(
                dir="/saves",
                files=("a.srm",),
                granularity=GRANULARITY_PER_GAME_FILE,
                role=ROLE_BATTERY,
                size_fixed=value,  # pyright: ignore[reportArgumentType] - the refusal under test
            )


class TestTheFact:
    def test_a_fill_rides_only_on_a_fixed_size(self):
        assert SizeFact(fixed=True, citation=CITATION, fill=SIZE_FILL_FF).fill == SIZE_FILL_FF
        for fixed in (False, None):
            with pytest.raises(ValueError, match="rides only on fixed true"):
                SizeFact(fixed=fixed, citation=CITATION, fill=SIZE_FILL_FF)

    def test_a_fill_outside_the_vocabulary_is_refused(self):
        with pytest.raises(ValueError, match="fill must be one of"):
            SizeFact(fixed=True, citation=CITATION, fill="0xaa")

    def test_a_claim_without_its_citation_is_refused(self):
        with pytest.raises(ValueError, match="carries the citation"):
            SizeFact(fixed=True, citation="")

    def test_the_fill_caveat_refuses_a_byte_outside_the_vocabulary(self):
        with pytest.raises(ValueError, match="fill must be one of"):
            Caveat(
                CAVEAT_SIZE_FIXED_EXCEPT_TRAILING_FILL,
                "message",
                {"file": "a.srm", "fill": "0xaa", "citation": CITATION},
            )


class TestTheRecord:
    def test_a_claim_per_memory_type_is_read(self):
        size = {"save_ram": _fact(True), "rtc": _fact(None)}
        (record,) = load_save_memory(_doc({"gb": _entry(["save_ram", "rtc"], size)}))
        assert record.size("gb", MEMORY_SAVE_RAM) == SizeFact(fixed=True, citation=CITATION)
        assert record.size("gb", MEMORY_RTC) == SizeFact(fixed=None, citation=CITATION)

    def test_an_entry_nobody_read_the_size_of_answers_nothing(self):
        (record,) = load_save_memory(_doc({"gb": _entry(["save_ram"])}))
        assert record.size("gb", MEMORY_SAVE_RAM) is None

    def test_half_a_size_block_is_refused(self):
        text = _doc({"gb": _entry(["save_ram", "rtc"], {"save_ram": _fact(True)})})
        with pytest.raises(ValueError, match="one claim per memory type filled"):
            load_save_memory(text)

    def test_a_claim_for_a_file_never_written_is_refused(self):
        text = _doc({"gb": _entry([], {"save_ram": _fact(True)})})
        with pytest.raises(ValueError, match="one claim per memory type filled"):
            load_save_memory(text)

    def test_a_string_is_not_read_as_a_boolean(self):
        text = _doc({"gb": _entry(["save_ram"], {"save_ram": _fact("false")})})  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="expected true, false or null"):
            load_save_memory(text)

    def test_a_fill_beside_a_size_that_is_not_fixed_is_refused(self):
        text = _doc({"gb": _entry(["save_ram"], {"save_ram": _fact(None, fill="0xff")})})
        with pytest.raises(ValueError, match="exception to a fixed size"):
            load_save_memory(text)

    def test_unnamed_the_systems_have_to_agree_on_the_claim(self):
        agreeing = _doc(
            {
                "gb": _entry(["save_ram"], {"save_ram": _fact(True)}),
                "gbc": _entry(["save_ram"], {"save_ram": _fact(True)}),
            }
        )
        (record,) = load_save_memory(agreeing)
        fact = record.size(None, MEMORY_SAVE_RAM)
        assert fact is not None
        assert fact.fixed is True

        differing = _doc(
            {
                "megadrive": _entry(["save_ram"], {"save_ram": _fact(True)}),
                "segacd": _entry(["save_ram"], {"save_ram": _fact(None)}),
            }
        )
        (record,) = load_save_memory(differing)
        assert record.size(None, MEMORY_SAVE_RAM) is None
        # The files still agree, so the answer keeps naming them.
        assert record.for_system(None) is not None


class TestThePackagedClaims:
    """D3 coverage and the values D6 decided, read off the shipped tables."""

    def test_every_recorded_file_states_whether_its_size_is_fixed(self):
        unstated = [
            (record.key, system)
            for record in load_save_memory()
            for system, entry in record.systems.items()
            if entry.memory_types and set(entry.sizes) != set(entry.memory_types)
        ]
        assert unstated == []

    @pytest.mark.parametrize(
        ("core", "system", "memory", "fixed", "fill"),
        [
            ("mgba", "gba", MEMORY_SAVE_RAM, True, SIZE_FILL_FF),
            ("mgba", "gb", MEMORY_SAVE_RAM, True, None),
            ("mgba", "gb", MEMORY_RTC, True, None),
            ("vbam", "gba", MEMORY_SAVE_RAM, None, None),
            ("melondsds", "nds", MEMORY_SAVE_RAM, None, None),
            ("picodrive", "megacd", MEMORY_SAVE_RAM, None, None),
            ("picodrive", "megadrive", MEMORY_SAVE_RAM, True, None),
            ("jollycv", "crvision", MEMORY_SAVE_RAM, None, None),
            ("gambatte", "gb", MEMORY_RTC, True, None),
        ],
    )
    def test_the_decided_values(self, core, system, memory, fixed, fill):
        (record,) = [record for record in load_save_memory() if record.key == core]
        fact = record.size(system, memory)
        assert fact is not None
        assert (fact.fixed, fact.fill) == (fixed, fill)

    def test_the_cards_state_the_file_they_hand_the_frontend(self):
        cards = {card.key: card for card in load_oddities()}
        stated: dict[str, set[tuple[bool | None, str | None]]] = {}
        for key, card in cards.items():
            for mode in card.modes.values():
                for group in mode.groups:
                    if group.frontend is not None:
                        assert group.files == ("<rom_stem>.srm",)
                        assert group.frontend.memory == MEMORY_SAVE_RAM
                        stated.setdefault(key, set()).add(
                            (group.frontend.size.fixed, group.frontend.size.fill)
                        )
        assert stated == {
            "pcsx_rearmed": {(True, None)},
            "mednafen_psx": {(True, None)},
            "mednafen_psx_hw": {(True, None)},
            "swanstation": {(True, None)},
            "genesis_plus_gx": {(True, SIZE_FILL_FF)},
            "bsnes-jg": {(True, None)},
        }


def _card_table(group: dict[str, Any]) -> str:
    return json.dumps(
        {
            "schema": 1,
            "cores": {
                "example": {
                    "identifiers": {"library_name": ["Example"]},
                    "saves": {"modes": {"always": {"root": "savefile_directory", "groups": [group]}}},
                    "provenance": {"source": "[V] a citation"},
                }
            },
        }
    )


def _frontend_group(files: list[str], memory: str = "save_ram") -> dict[str, Any]:
    return {
        "files": files,
        "granularity": "per-game-file",
        "role": "battery",
        "written_by_frontend": {"memory": memory, "size": _fact(True)},
    }


class TestTheCardBlock:
    def test_the_frontends_own_name_is_accepted(self):
        (card,) = load_oddities(_card_table(_frontend_group(["<rom_stem>.srm"])))
        (group,) = card.modes["always"].groups
        assert group.frontend is not None
        assert group.frontend.size.fixed is True

    @pytest.mark.parametrize(
        "files",
        [["<rom_stem>.rtc"], ["<rom_stem>.1.mcr"], ["<rom_stem>.srm", "<rom_stem>.rtc"]],
    )
    def test_a_file_the_frontend_does_not_name_is_refused(self, files):
        table = _card_table(_frontend_group(files))
        with pytest.raises(ValueError, match="holds exactly <rom_stem>.srm"):
            load_oddities(table)

    def test_a_memory_id_that_reaches_no_file_is_refused(self):
        table = _card_table(_frontend_group(["<rom_stem>.srm"], memory="system_ram"))
        with pytest.raises(ValueError, match="written_by_frontend.memory must be one of"):
            load_oddities(table)


class TestTheValidator:
    def test_a_group_whose_size_is_a_string_is_refused(self):
        group = {**_group(), "size_fixed": "true"}
        vector = _base_placement(
            file_set=_file_set(state="declared", files=["a.srm"], groups=[group])
        )
        with pytest.raises(VectorError, match="size_fixed must be true, false or null"):
            validate_machines_vector(vector)

    def test_a_fill_caveat_outside_the_vocabulary_is_refused(self):
        group = {**_group(), "size_fixed": True}
        vector = _base_placement(
            file_set=_file_set(state="declared", files=["a.srm"], groups=[group]),
            caveats=[
                {
                    "code": "size-fixed-except-trailing-fill",
                    "data": {"file": "a.srm", "fill": "0xaa", "citation": CITATION},
                }
            ],
        )
        with pytest.raises(VectorError, match="size-fixed-except-trailing-fill.fill must be one of"):
            validate_machines_vector(vector)


def _sized_card(groups: list[dict[str, Any]], sizes: Any) -> str:
    """A one-mode card with a ``sizes`` block — the core-written files' claims (D9)."""
    document = json.loads(_card_table(groups[0]))
    saves = document["cores"]["example"]["saves"]
    saves["modes"]["always"]["groups"] = groups
    saves["sizes"] = sizes
    return json.dumps(document)


def _core_group(files: list[str], **extra: Any) -> dict[str, Any]:
    return {"files": files, "granularity": "per-game-files", "role": "battery", **extra}


class TestTheCardSizes:
    """D9: one ``sizes`` block per card, keyed by the template its groups spell."""

    def test_a_file_the_core_writes_states_its_size(self):
        table = _sized_card([_core_group(["<rom_stem>.bkr"])], {"<rom_stem>.bkr": _fact(True)})
        (card,) = load_oddities(table)
        assert card.sizes["<rom_stem>.bkr"] == SizeFact(fixed=True, citation=CITATION)

    def test_a_name_a_group_only_watches_for_may_carry_a_claim(self):
        group = _core_group(["<rom_stem>.ram"], observe=["<rom_stem>.ram", "<rom_stem>-ext1M.ram"])
        (card,) = load_oddities(_sized_card([group], {"<rom_stem>-ext1M.ram": _fact(True)}))
        assert card.sizes["<rom_stem>-ext1M.ram"].fixed is True

    def test_a_card_without_the_block_states_no_claim(self):
        (card,) = load_oddities(_card_table(_core_group(["<rom_stem>.bkr"])))
        assert dict(card.sizes) == {}

    def test_a_claim_for_a_file_no_group_names_is_refused(self):
        table = _sized_card([_core_group(["<rom_stem>.bkr"])], {"<rom_stem>.bcr": _fact(True)})
        with pytest.raises(ValueError, match="no group of this card declares or watches for this file"):
            load_oddities(table)

    def test_a_file_the_frontend_writes_takes_no_claim_from_the_core(self):
        groups = [_frontend_group(["<rom_stem>.srm"]), _core_group(["<rom_stem>.rtc"])]
        table = _sized_card(groups, {"<rom_stem>.srm": _fact(True)})
        with pytest.raises(ValueError, match="RetroArch writes this file"):
            load_oddities(table)

    def test_a_fill_is_refused_while_no_route_states_its_caveat(self):
        table = _sized_card(
            [_core_group(["<rom_stem>.sav"])], {"<rom_stem>.sav": _fact(True, fill=SIZE_FILL_FF)}
        )
        with pytest.raises(ValueError, match="a fill here would reach a caller without its caveat"):
            load_oddities(table)

    @pytest.mark.parametrize(
        "size",
        [
            {"fixed": True},
            {"fixed": True, "citation": CITATION, "bytes": 8192},
            {"fixed": "true", "citation": CITATION},
        ],
    )
    def test_a_claim_that_is_not_one_is_refused(self, size):
        table = _sized_card([_core_group(["<rom_stem>.bkr"])], {"<rom_stem>.bkr": size})
        with pytest.raises(ValueError, match=r"saves.sizes\['<rom_stem>.bkr'\]"):
            load_oddities(table)

    @pytest.mark.parametrize("sizes", [{}, [], "fixed"])
    def test_a_block_that_states_nothing_is_refused(self, sizes):
        table = _sized_card([_core_group(["<rom_stem>.bkr"])], sizes)
        with pytest.raises(ValueError, match="must be a non-empty object of file template -> size"):
            load_oddities(table)


def _two_file_group() -> SaveGroup:
    return SaveGroup(
        subdir=None, files=("<rom_stem>.0", "<rom_stem>.1"), granularity="per-game-files", role="battery"
    )


def _claims(*facts: bool | None) -> dict[str, SizeFact]:
    return {
        f"<rom_stem>.{index}": SizeFact(fixed=fixed, citation=CITATION)
        for index, fixed in enumerate(facts)
        if fixed is not None
    }


class TestTheGroupClaim:
    """How a card group's ``size_fixed`` follows from the block, on every route."""

    def test_a_group_states_the_claim_its_files_share(self):
        group = _two_file_group()
        assert _card_group_size(group, _claims(True, True)) is True
        assert _card_group_size(group, _claims(False, False)) is False

    @pytest.mark.parametrize("facts", [(True, None), (True, False)])
    def test_a_group_whose_files_are_not_stated_alike_states_nothing(self, facts):
        group = _two_file_group()
        assert _card_group_size(group, _claims(*facts)) is None

    def test_a_folder_save_keeps_no_size_whatever_the_block_says(self):
        group = SaveGroup(
            subdir="saves/<rom_stem>",
            files=None,
            granularity="per-game-directory",
            role="battery",
            unnamed="the game names its own files",
        )
        assert _card_group_size(group, _claims(True)) is False

    def test_an_observed_group_answers_for_the_names_it_found(self):
        group = SaveGroup(
            subdir=None,
            files=("<rom_stem>.ram",),
            granularity="per-game-files",
            role="battery",
            observe=("<rom_stem>.ram", "<rom_stem>-ext1M.ram"),
        )
        mode = SaveMode(root="savefile_directory", groups=(group,))
        sizes = {"<rom_stem>.ram": SizeFact(fixed=True, citation=CITATION)}

        def observed(*found: str) -> bool | None:
            (answer,) = _observed_groups(found, directory="/saves", rom_stem="game", mode=mode, sizes=sizes)
            return answer.size_fixed

        assert observed("game.ram") is True
        assert observed("game.ram", "game-ext1M.ram") is None

    def test_two_claims_that_disagree_on_one_file_surface_through_the_merge(self):
        # #689's rule holds for a card's own claims: the entry states null and
        # the caveat lists both, rather than one claim overwriting the other.
        mode = SaveMode(
            root="savefile_directory",
            groups=(
                SaveGroup(
                    subdir=None, files=("<rom_stem>.brm",), granularity="per-game-file", role="battery"
                ),
                SaveGroup(subdir=None, files=("shared.brm",), granularity="shared-file", role="battery"),
            ),
        )
        sizes = {
            "<rom_stem>.brm": SizeFact(fixed=True, citation=CITATION),
            "shared.brm": SizeFact(fixed=False, citation=CITATION),
        }
        groups = _declared_groups(
            mode, directory="/saves", rom_stem="shared", content_dir_name=None, sizes=sizes
        )
        (entry,), (caveat,) = _one_entry_per_file(groups)
        assert entry.size_fixed is None
        assert caveat.data["size_fixed"] == ("true", "false")

    def test_a_claim_on_a_rule_filled_template_follows_the_names_the_rule_filled(self):
        card = CoreCard(
            key="example",
            library_names=("Example",),
            option_key=None,
            option_default=None,
            modes={},
            provenance="[V] a citation",
            sizes={"<archive_member_stem>_save.adf": SizeFact(fixed=True, citation=CITATION)},
        )
        filled = _fill_rule_sizes(card, {"<archive_member_stem>": ("Disk1", "Disk2")})
        assert {name: fact.fixed for name, fact in filled.sizes.items()} == {
            "<archive_member_stem>_save.adf": True,
            "Disk1_save.adf": True,
            "Disk2_save.adf": True,
        }
        assert _fill_rule_sizes(card, {}) is card


class TestThePackagedCardSizes:
    """The claims cut 2 read at each card's pin (D9, D11), and the D13 splits."""

    @pytest.mark.parametrize(
        ("core", "template", "fixed"),
        [
            ("mednafen_psx", "<rom_stem>.1.mcr", True),
            ("mednafen_psx_hw", "mednafen_psx_libretro_shared.0.mcr", True),
            ("swanstation", "<save_id>_2.mcd", True),
            ("pcsx_rearmed", "pcsx-card2.mcd", True),
            ("pcsx2", "<rom_stem>.ps2", True),
            ("genesis_plus_gx", "scd_J.brm", True),
            ("genesis_plus_gx", "<rom_stem>_512Kbit_cart.brm", True),
            ("mednafen_saturn", "mednafen_saturn_libretro_shared.smpc", True),
            ("kronos", "<rom_stem>-ext4M.ram", True),
            ("kronos", "<rom_stem>.nv", True),
            ("flycast", "vmu_save_C2.bin", True),
            ("flycast", "dc_nvmem.bin", True),
            ("opera", "nvram.9.srm", True),
            ("bsnes-jg", "<rom_stem>.rtc", True),
            ("puae", "<rom_stem>_save.adf", True),
            ("puae", "<archive_member_stem>_save.adf.gz", False),
            ("puae2021", "<rom_stem>_save.adf.gz", False),
        ],
    )
    def test_the_read_values(self, core, template, fixed):
        cards = {card.key: card for card in load_oddities()}
        assert cards[core].sizes[template].fixed is fixed

    def test_every_claim_cites_what_it_was_read_at(self):
        unpinned = [
            (card.key, name)
            for card in load_oddities()
            for name, fact in card.sizes.items()
            if not fact.citation.startswith("[V] ") or " at " not in fact.citation
        ]
        assert unpinned == []

    def test_bsnes_jg_states_each_writers_file_as_its_own_group(self):
        cards = {card.key: card for card in load_oddities()}
        srm, rtc = cards["bsnes-jg"].modes["always"].groups
        assert (srm.files, srm.granularity, rtc.files, rtc.granularity) == (
            ("<rom_stem>.srm",),
            "per-game-file",
            ("<rom_stem>.rtc",),
            "per-game-file",
        )
        assert srm.frontend is not None
        assert srm.frontend.size.fixed is True
        assert rtc.frontend is None

    @pytest.mark.parametrize("core", ["puae", "puae2021"])
    @pytest.mark.parametrize(
        ("mode", "stem", "granularity"),
        [
            ("floppy-redirected", "<rom_stem>", "per-game-file"),
            # One write file per archive member: each group holds several names.
            ("archived-floppy-redirected", "<archive_member_stem>", "per-game-files"),
        ],
    )
    def test_puae_states_the_write_file_and_its_gzip_apart(self, core, mode, stem, granularity):
        cards = {card.key: card for card in load_oddities()}
        adf, gzip = cards[core].modes[mode].groups
        assert (adf.files, gzip.files) == ((f"{stem}_save.adf",), (f"{stem}_save.adf.gz",))
        assert {adf.granularity, gzip.granularity} == {granularity}
        assert gzip.files_established_for is None
        assert adf.files_established_for == cards[core].modes[mode].files_established_for


class TestTheStandaloneSources:
    """Each fixed size an answer states rides its sources with the evidence for that kind of file."""

    def _dolphin(self) -> tuple[Any, Any]:
        card = lookup_standalone_save_card("DOLPHIN")
        assert card is not None
        return card, lambda slot: card.cite(slot, flatpak=None)

    def _slot(self, mode: str, size_fixed: bool | None) -> _DolphinSlot:
        group = FileGroup(
            dir="/gc", files=("a.sav",), granularity="shared-file", role=ROLE_BATTERY, size_fixed=size_fixed
        )
        return _DolphinSlot(mode=mode, groups=(group,))

    def test_a_cartridge_save_cites_the_adapter_not_the_card_image(self):
        card, cite = self._dolphin()
        slots = (self._slot("agp", True), _DolphinSlot(mode="none"))
        sources = _card_sources(card, _dolphin_size_evidence(slots, cite))
        assert sources[1:] == (
            f"a GBA cartridge's .sav keeps the size it had when the game started — "
            f"{cite('agp_save')} at {cite('build')}",
        )

    def test_a_card_and_a_cartridge_each_bring_their_own_sentence(self):
        card, cite = self._dolphin()
        slots = (self._slot("card", True), self._slot("agp", True))
        sources = _card_sources(card, _dolphin_size_evidence(slots, cite))
        assert [source.split(" — ")[0] for source in sources[1:]] == [
            "a memory-card image keeps the size it was created at",
            "a GBA cartridge's .sav keeps the size it had when the game started",
        ]

    def test_a_group_that_states_no_fixed_size_brings_no_sentence(self):
        card, cite = self._dolphin()
        slots = (self._slot("card", None), self._slot("folder", False))
        sources = _card_sources(card, _dolphin_size_evidence(slots, cite))
        assert len(sources) == 1
