"""Every input key and query field the vector validator accepts is described in vectors/README.md.

The README is where a port author learns the machine-vector format, and the
validator's key sets are what a vector's ``input`` may carry. A key the
validator accepts and the README never names is part of the format that can only
be learned by reading the validator. Counting rule: a key is described when its
name, and nothing else, stands in backticks somewhere in the file.

The fields inside a query are read off the validator's query registry —
``QUERY_FAMILY_FIELDS`` per query family, ``AGGREGATE_QUESTION_FIELDS`` per
question of the aggregate query — which every query validator takes its
accepted set from, so a field cannot be accepted without being counted here.
They are held to the same counting rule within the README's section on the
questions a vector asks, the one section that describes queries.

Blind spot: the README has no section per query family — the families share one
table and paragraphs, and one paragraph describes ``cwd`` or ``installation``
for many families at once — so a field is held to that section, not to the
paragraph of the family that takes it. A field described for one family counts
for every family that takes it. The section's table counts too: the fields its
question column names — ``kind``, ``md5``, ``sha1``, ``size`` — count as
described from the table alone, and an aggregate question named in its
expected-key column counts as named.
"""

from __future__ import annotations

from pathlib import Path

from scripts import validate_vectors

_README = Path(__file__).resolve().parents[1] / "vectors" / "README.md"
_QUERY_SECTION = "### The question a vector asks"


def _query_section() -> str:
    """The README from the query section's heading to the next level-2 heading."""
    text = _README.read_text(encoding="utf-8")
    assert _QUERY_SECTION in text, f"vectors/README.md has no {_QUERY_SECTION!r} heading to hold query fields to"
    start = text.index(_QUERY_SECTION)
    end = text.find("\n## ", start)
    return text[start:] if end == -1 else text[start:end]


def test_every_input_key_the_validator_accepts_is_named_in_the_readme():
    text = _README.read_text(encoding="utf-8")
    accepted = validate_vectors.INPUT_FIELDS_REQUIRED | validate_vectors.INPUT_FIELDS_OPTIONAL
    undescribed = sorted(key for key in accepted if f"`{key}`" not in text)
    assert undescribed == [], (
        f"scripts/validate_vectors.py accepts the input keys {undescribed}, and vectors/README.md "
        "names none of them in backticks — describe each where the README describes its neighbours"
    )


def test_every_field_a_query_family_accepts_is_named_in_the_query_section():
    section = _query_section()
    undescribed = sorted(
        f"{family}.{field}"
        for family, fields in validate_vectors.QUERY_FAMILY_FIELDS.items()
        for field in fields
        if f"`{field}`" not in section
    )
    assert undescribed == [], (
        f"scripts/validate_vectors.py accepts the query fields {undescribed}, and the README's "
        f"{_QUERY_SECTION!r} section names none of them in backticks — describe each beside the query that takes it"
    )


def test_every_aggregate_question_and_its_fields_are_named_in_the_query_section():
    section = _query_section()
    named = [
        (question, f"aggregate_query.question {question!r}")
        for question in validate_vectors.AGGREGATE_QUESTION_FIELDS
    ] + [
        (field, f"aggregate_query.{field} on {question!r}")
        for question, (required, optional) in validate_vectors.AGGREGATE_QUESTION_FIELDS.items()
        for field in required | optional
    ]
    undescribed = sorted(where for name, where in named if f"`{name}`" not in section)
    assert undescribed == [], (
        f"scripts/validate_vectors.py accepts {undescribed}, and the README's {_QUERY_SECTION!r} "
        "section names none of them in backticks — describe each beside the aggregate query"
    )
