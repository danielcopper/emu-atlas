"""The packaged spellings: a loader that refuses a re-spelling, and a table with no gaps.

Two kinds of check. The loader ones are the usual packaged-data discipline —
a malformed build fails loudly rather than answering out of it. The completeness
ones are the point of the table: every identifier atlas can put in front of a
person needs a name, and the two places identifiers come from (the handle
classes, and the distributions the copy lists name) each derive their side here
rather than being listed by hand.
"""

from __future__ import annotations

import json

import pytest

import atlas
from atlas import installations
from atlas.distribution_labels import (
    DISTRIBUTION_LABELS_SCHEMA,
    distribution_label,
    load_distribution_labels,
)
from atlas.distribution_supplied import load_distribution_supplied

ENTRY = {
    "label": "RetroDECK",
    "spelling": "RetroDECK",
    "citation": "the project's own README title, '# RetroDECK'",
}


def _table(**entry) -> str:
    return json.dumps(
        {
            "schema": DISTRIBUTION_LABELS_SCHEMA,
            "version": "1",
            "reviewed": "2026-09-02",
            "distributions": {"retrodeck": {**ENTRY, **entry}},
        }
    )


def _handle_kinds() -> set[str]:
    """Every kind a detected installation can report, off the classes that declare them.

    Read from the code rather than listed, for the reason
    :mod:`tests.test_evidence` reads it the same way: a fifth handle must not be
    able to arrive with no name to show for itself. Both halves of a handle's
    identity count — ``kind`` is what an answer is keyed by, and every member of
    ``kinds`` is a description the same handle claims, so an EmuDeck saying it
    is also a bare RetroArch Flatpak needs that identifier spelled too.
    """
    return {
        kind
        for name, member in vars(installations).items()
        if not name.startswith("_")
        and isinstance(member, type)
        and isinstance(vars(member).get("kind"), str)
        for kind in (member.kind, *member.kinds)
    }


class TestThePackagedTable:
    def test_the_projects_spell_themselves(self):
        # The four spellings, written out: this is the file's whole content, and
        # a change to one is a change a person makes on purpose after reading
        # the project's own text again.
        labels = {kind: record.label for kind, record in load_distribution_labels().items()}
        assert labels == {
            "retrodeck": "RetroDECK",
            "emudeck": "EmuDeck",
            "bare_retroarch_flatpak": "RetroArch (Flatpak)",
            "bare_retroarch_native": "RetroArch (native)",
        }

    def test_every_entry_cites_where_its_spelling_was_read(self):
        assert all(record.citation for record in load_distribution_labels().values())

    def test_every_kind_a_handle_reports_has_a_name(self):
        # The gap this table may not have: an identifier atlas hands a consumer
        # with nothing to render beside it. `distribution_label` raises on one,
        # so the miss would surface as a crashed answer rather than a bad word.
        assert _handle_kinds() <= set(load_distribution_labels())

    def test_every_distribution_that_supplies_firmware_has_a_name(self):
        # The second door identifiers come through: a copy list names the
        # distribution a `supplied_by` statement carries, and that statement
        # serializes its label.
        assert set(load_distribution_supplied()) <= set(load_distribution_labels())


class TestTheLookup:
    def test_it_answers_the_name_the_project_writes(self):
        assert distribution_label("retrodeck") == "RetroDECK"

    def test_an_identifier_with_no_entry_is_an_error_and_not_a_guess(self):
        # Never `Retrodeck`, and never the bare identifier: both put a string
        # atlas never established in front of a user, silently.
        with pytest.raises(ValueError, match="no packaged spelling for 'retrodek'"):
            distribution_label("retrodek")

    def test_it_is_exported(self):
        assert atlas.distribution_label("emudeck") == "EmuDeck"


class TestTheLoaderRefuses:
    def test_a_schema_it_does_not_read(self):
        text = json.dumps({"schema": DISTRIBUTION_LABELS_SCHEMA + 1, "distributions": {}})
        with pytest.raises(ValueError, match="unsupported schema"):
            load_distribution_labels(text)

    def test_an_empty_table(self):
        text = json.dumps({"schema": DISTRIBUTION_LABELS_SCHEMA, "distributions": {}})
        with pytest.raises(ValueError, match="non-empty object"):
            load_distribution_labels(text)

    def test_a_table_that_is_not_an_object(self):
        text = json.dumps({"schema": DISTRIBUTION_LABELS_SCHEMA, "distributions": ["retrodeck"]})
        with pytest.raises(ValueError, match="non-empty object"):
            load_distribution_labels(text)

    def test_an_entry_missing_a_field(self):
        text = json.dumps(
            {
                "schema": DISTRIBUTION_LABELS_SCHEMA,
                "distributions": {"retrodeck": {"label": "RetroDECK"}},
            }
        )
        with pytest.raises(ValueError, match="names exactly"):
            load_distribution_labels(text)

    def test_an_entry_with_a_field_nobody_reads(self):
        text = json.dumps(
            {
                "schema": DISTRIBUTION_LABELS_SCHEMA,
                "distributions": {"retrodeck": {**ENTRY, "note": "why not"}},
            }
        )
        with pytest.raises(ValueError, match="names exactly"):
            load_distribution_labels(text)

    @pytest.mark.parametrize("field", sorted(ENTRY))
    def test_a_field_that_is_not_a_name(self, field):
        text = _table(**{field: ""})
        with pytest.raises(ValueError, match="non-empty string"):
            load_distribution_labels(text)

    def test_a_label_that_re_spells_its_source(self):
        # The mechanized half of the citation: a label may add a qualifier
        # around the cited name — `RetroArch (Flatpak)` — and may not quietly
        # become a different name than the one that was read.
        text = _table(label="Retrodeck")
        with pytest.raises(ValueError, match="does not contain the cited spelling"):
            load_distribution_labels(text)

    def test_a_label_that_qualifies_the_cited_spelling(self):
        # The other side of the same rule, so the check is not just strict:
        # this is the shape the two bare RetroArch kinds are in.
        table = load_distribution_labels(_table(label="RetroDECK (Flatpak)"))
        assert table["retrodeck"].label == "RetroDECK (Flatpak)"
