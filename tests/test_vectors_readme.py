"""Every input key the vector validator accepts is described in vectors/README.md.

The README is where a port author learns the machine-vector format, and the
validator's key sets are what a vector's ``input`` may carry. A key the
validator accepts and the README never names is part of the format that can only
be learned by reading the validator. Counting rule: a key is described when its
name, and nothing else, stands in backticks somewhere in the file.
"""

from __future__ import annotations

from pathlib import Path

from scripts import validate_vectors

_README = Path(__file__).resolve().parents[1] / "vectors" / "README.md"


def test_every_input_key_the_validator_accepts_is_named_in_the_readme():
    text = _README.read_text(encoding="utf-8")
    accepted = validate_vectors.INPUT_FIELDS_REQUIRED | validate_vectors.INPUT_FIELDS_OPTIONAL
    undescribed = sorted(key for key in accepted if f"`{key}`" not in text)
    assert undescribed == [], (
        f"scripts/validate_vectors.py accepts the input keys {undescribed}, and vectors/README.md "
        "names none of them in backticks — describe each where the README describes its neighbours"
    )
