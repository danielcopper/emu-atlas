"""ES-DE's find rules as the frontend loads them (``SystemData.cpp:43-213`` @ v3.4.1)."""

from __future__ import annotations

from atlas.find_rules import (
    EmulatorRules,
    FindRules,
    merge_find_rules,
    parse_find_rules,
)

RULES = """<?xml version="1.0"?>
<!-- comment -->
<ruleList>
    <emulator name="DOLPHIN">
        <rule type="systempath">
            <entry>dolphin-emu</entry>
        </rule>
        <rule type="winregistrypath">
            <entry>Dolphin.exe</entry>
        </rule>
        <rule type="staticpath">
            <entry>~/Applications/Dolphin*.AppImage</entry>
            <entry>/app/retrodeck/components/dolphin/component_launcher.sh</entry>
        </rule>
        <rule>
            <entry>/typeless</entry>
        </rule>
        <rule type="systempath">
            <entry>dolphin-emu-nogui</entry>
        </rule>
    </emulator>
    <emulator name="DOLPHIN">
        <rule type="staticpath"><entry>/second/definition</entry></rule>
    </emulator>
    <emulator name="">
        <rule type="staticpath"><entry>/nameless</entry></rule>
    </emulator>
    <core name="RETROARCH">
        <rule type="corepath">
            <entry>/var/config/retroarch/cores</entry>
            <entry>
                ~/spread/over/lines</entry>
        </rule>
        <rule type="systempath"><entry>/not/a/corepath</entry></rule>
    </core>
</ruleList>
"""


class TestOneFileIsReadTheWayESDEReadsIt:
    def test_the_two_lists_keep_document_order_and_drop_every_other_rule_type(self):
        rules = parse_find_rules(RULES)
        assert rules is not None
        assert rules.emulators["DOLPHIN"] == EmulatorRules(
            system_paths=("dolphin-emu", "dolphin-emu-nogui"),
            static_paths=(
                "~/Applications/Dolphin*.AppImage",
                "/app/retrodeck/components/dolphin/component_launcher.sh",
            ),
        )

    def test_the_first_definition_of_a_name_wins_and_the_repeat_is_remembered(self):
        rules = parse_find_rules(RULES)
        assert rules is not None
        assert "/second/definition" not in rules.emulators["DOLPHIN"].static_paths
        assert rules.repeated_emulators == frozenset({"DOLPHIN"})

    def test_a_nameless_emulator_is_skipped(self):
        rules = parse_find_rules(RULES)
        assert rules is not None
        assert set(rules.emulators) == {"DOLPHIN"}

    def test_entry_text_is_kept_as_written_line_breaks_included(self):
        rules = parse_find_rules(RULES)
        assert rules is not None
        assert rules.cores["RETROARCH"] == (
            "/var/config/retroarch/cores",
            "\n                ~/spread/over/lines",
        )

    def test_a_file_that_does_not_parse_is_skipped(self):
        assert parse_find_rules("<ruleList><emulator></ruleList>") is None

    def test_a_file_without_a_rule_list_is_skipped(self):
        assert parse_find_rules('<?xml version="1.0"?>\n<rules><emulator name="X"/></rules>') is None

    def test_a_second_document_level_element_does_not_hide_the_rule_list(self):
        # pugixml reads document children, so a file XML calls malformed is
        # one ES-DE reads; the first <ruleList> is the one it gets.
        text = '﻿<ruleList><emulator name="A"><rule type="staticpath"><entry>/a</entry></rule></emulator></ruleList><ruleList/>'
        rules = parse_find_rules(text)
        assert rules is not None
        assert rules.emulators["A"].static_paths == ("/a",)

    def test_an_emulator_with_no_entries_is_held_empty(self):
        rules = parse_find_rules('<ruleList><emulator name="EMPTY"/></ruleList>')
        assert rules is not None
        assert rules.emulators["EMPTY"].empty


class TestTheLayersMergeWhole:
    def test_a_custom_definition_replaces_the_bundled_one_entirely(self):
        custom = FindRules(emulators={"X": EmulatorRules(static_paths=("/custom",))}, cores={})
        bundled = FindRules(
            emulators={"X": EmulatorRules(("x",), ("/bundled",)), "Y": EmulatorRules(("y",))},
            cores={"RETROARCH": ("/cores",)},
        )
        merged = merge_find_rules((custom, bundled))
        assert merged.emulators["X"] == EmulatorRules(static_paths=("/custom",))
        assert merged.emulators["Y"] == EmulatorRules(("y",))
        assert merged.cores["RETROARCH"] == ("/cores",)

    def test_an_empty_custom_definition_still_wins(self):
        custom = FindRules(emulators={"X": EmulatorRules()}, cores={})
        bundled = FindRules(emulators={"X": EmulatorRules(("x",))}, cores={})
        assert merge_find_rules((custom, bundled)).emulators["X"].empty
