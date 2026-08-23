"""Read the arrangement's own scripts back, the way the anchors read the binaries (#264).

The weekly canary deploys the newest RetroDECK and un-skips every machine-bound
test, which is how the core anchors and the emulator directory names get
re-read days before the reference machine updates. The packaged data's other
half pointed nowhere: **112 citations name a RetroDECK component script and a
line number**, every one of them read once, by hand, from one release. A
release that inserts a line, renames a variable or moves a ``dir_prep`` left
the suite green and the citation pointing at a line that no longer said what it
said.

Three passes, weakest last:

1. **The wired trees.** Every row of ``content_tree_wiring.json`` against the
   ``dir_prep`` at its cited line — both sides of the pair, with the
   component's own variables expanded, because half of them name the emulator
   side through a variable ``component_functions.sh`` defines.
2. **Settings addresses.** Every entry of ``emulator_settings.json`` against
   the path the component pins for a file of that name, with the one
   deliberate disagreement written down rather than dodged.
3. **Every other citation** is at least a script that is still deployed and a
   line that still exists and still says something. It catches a component
   that went away or a file that shrank, which is what the two content passes
   would otherwise report as a mismatch nobody can read. Most citations name
   the script without naming its component — ``component_functions.sh:3``,
   because the sentence around it already said which emulator — so the
   component is resolved from the same string where one is named there, and
   otherwise from the token the entry sits under. One citation resolves
   neither way and is listed as such rather than dropped quietly.

Skipped where RetroDECK is not deployed, by the same path check that silences
the rest of the machine-bound tier.
"""

import json
import re
from pathlib import Path

import pytest

from atlas.emulator_settings import load_emulator_settings

DATA = Path(__file__).resolve().parent.parent / "atlas" / "data"
COMPONENTS = Path(
    "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
)

CITATION = re.compile(r"components/([a-z0-9_-]+)/(component_[a-z_]+\.sh):(\d+)")
# The same reference with the component left to the sentence around it.
BARE_CITATION = re.compile(r"(?<!/)\b(component_[a-z_]+\.sh):(\d+)")
# Only the plain `name="value"` form, which is every assignment these scripts
# make. Anything else is left alone rather than half-understood.
ASSIGNMENT = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)="([^"]*)"\s*$')
REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
DIR_PREP = re.compile(r'dir_prep\s+"([^"]*)"\s+"([^"]*)"')

# What a wiring row's `base` is spelled as once the component's own variables
# are expanded. RetroDECK's roots stay symbolic — where they point is the
# installation's business and the row does not claim it.
BASE_VARIABLES = {
    "bios": "$bios_path",
    "storage": "$storage_path",
    "xdg-config": "$XDG_CONFIG_HOME",
    "xdg-data": "$XDG_DATA_HOME",
}

# The %EMULATOR_…% token each component directory belongs to.
COMPONENT_OF = {
    "DOLPHIN": "dolphin",
    "PRIMEHACK": "primehack",
    "PPSSPP": "ppsspp",
    "XEMU": "xemu",
    "CEMU": "cemu",
    "AZAHAR": "azahar",
    "DUCKSTATION": "duckstation",
    "PCSX2": "pcsx2",
    "MELONDS": "melonds",
    "RPCS3": "rpcs3",
    "VITA3K": "vita3k",
}

# The one address where atlas and the arrangement disagree on purpose, and why.
# An exception that names its reason is worth more than a check that avoids the
# question — this one cost a shipped release to find.
SETTINGS_DISAGREEMENTS = {
    ("XEMU", "xemu.toml"): (
        "RetroDECK's script writes the toml under the config home and xemu reads the one under "
        "the data home (#250, #251); the card states the door the emulator opens, not the one "
        "the installer writes"
    )
}


def _lines(component: str, script: str) -> list[str] | None:
    path = COMPONENTS / component / script
    return path.read_text(encoding="utf-8").splitlines() if path.is_file() else None


def _variables(component: str) -> dict[str, str]:
    """The component's own ``component_functions.sh`` assignments, unexpanded."""
    lines = _lines(component, "component_functions.sh") or []
    table: dict[str, str] = {}
    for line in lines:
        match = ASSIGNMENT.match(line)
        if match:
            table[match.group(1)] = match.group(2)
    return table


def _expand(value: str, table: dict[str, str]) -> str:
    """Substitute the component's own variables; leave the arrangement's roots alone."""
    for _ in range(8):
        grown = REFERENCE.sub(
            lambda m: table.get(m.group(1) or m.group(2), m.group(0)), value
        )
        if grown == value:
            break
        value = grown
    return value


# The one citation that names neither its component nor an emulator: the
# wiring table's own spec prose. Listed rather than dropped, so a second one
# cannot appear unnoticed.
UNRESOLVED_CITATIONS = {("content_tree_wiring.json", "spec", "component_prepare.sh", 46)}


def _strings(node: object, path: tuple[str, ...] = ()):
    """Every string in a packaged table, with the keys it sits under."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _strings(value, (*path, str(key)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _strings(value, (*path, str(index)))
    elif isinstance(node, str):
        yield path, node


def _component_in_scope(path: tuple[str, ...], text: str) -> str | None:
    """Which component a citation without one belongs to.

    The sentence usually names it outright somewhere else in the same string
    (``components/dolphin/component_prepare.sh:26`` beside a bare
    ``component_functions.sh:3``); failing that, the entry sits under the
    emulator token whose component it is. Neither is a guess — both are the
    reference the prose is already making.
    """
    named = CITATION.findall(text)
    if named:
        return named[0][0]
    if len(path) >= 2 and path[0] == "emulators":
        return COMPONENT_OF.get(path[1])
    return None


def _citations() -> list[tuple[str, str, int, str]]:
    """Every component-script citation the packaged data names, with its file."""
    found: list[tuple[str, str, int, str]] = []
    unresolved: set[tuple[str, str, str, int]] = set()
    for path in sorted(DATA.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        for keys, text in _strings(document):
            for component, script, line in CITATION.findall(text):
                found.append((component, script, int(line), path.name))
            scope = _component_in_scope(keys, text)
            for script, line in BARE_CITATION.findall(text):
                if scope is None:
                    unresolved.add((path.name, ".".join(keys), script, int(line)))
                    continue
                found.append((scope, script, int(line), path.name))
    assert unresolved == UNRESOLVED_CITATIONS, (
        "the set of citations naming neither a component nor an emulator has changed: "
        f"{sorted(unresolved ^ UNRESOLVED_CITATIONS)} — a citation nothing can resolve is one "
        "nothing re-reads, so name the component or record it here with the others"
    )
    return found


CITATIONS = _citations()
WIRING = json.loads((DATA / "content_tree_wiring.json").read_text(encoding="utf-8"))
WIRING_ROWS = [
    (arrangement, row, component, script, int(line))
    for arrangement, block in WIRING["arrangements"].items()
    for row in block["rows"]
    for component, script, line in CITATION.findall(row["source"])
]
SETTINGS_FILES = [
    (token, name, file)
    for token, entry in load_emulator_settings().items()
    for name, file in entry.files.items()
]


def _deployed() -> bool:
    return COMPONENTS.is_dir()


class TestTheWiredTreesStillSayWhatTheRowsState:
    """Pass one: the ``dir_prep`` at the cited line, both sides, expanded."""

    @pytest.mark.parametrize(
        ("row", "component", "script", "line"),
        [(row, *rest) for _, row, *rest in WIRING_ROWS],
        ids=[f"{r['family']}:{r['hub']}" for _, r, _, _, _ in WIRING_ROWS],
    )
    def test_the_cited_line_wires_the_pair_the_row_states(
        self, row, component, script, line
    ):
        lines = _lines(component, script)
        if lines is None:
            pytest.skip(f"{component}/{script} is not deployed")
        assert line <= len(lines), (
            f"{component}/{script} has {len(lines)} lines and the row cites :{line} — the "
            "script moved under the citation"
        )
        text = lines[line - 1]
        pair = DIR_PREP.search(text)
        assert pair is not None, (
            f"{component}/{script}:{line} is not a dir_prep any more: {text.strip()!r} — the "
            f"{row['family']} row for {row['hub']!r} cites a wiring that is no longer there"
        )
        table = _variables(component)
        hub, emulator = (_expand(side, table) for side in pair.groups())
        assert row["hub"] in hub, (
            f"{component}/{script}:{line} links {hub!r}, and the row states the hub side as "
            f"{row['hub']!r} — the distribution renamed its own tree"
        )
        expected = f"{BASE_VARIABLES[row['base']]}/{row['path']}"
        assert emulator.rstrip("/") == expected, (
            f"{component}/{script}:{line} links to {emulator!r} and the row states {expected!r} "
            "— the emulator side of the pair moved, so every answer that walks this link is "
            "pointing at the old place"
        )


class TestTheSettingsAddressesStillAgreeWithTheArrangement:
    """Pass two: what the component pins for a file the table addresses."""

    @pytest.mark.parametrize(
        ("token", "name", "file"),
        SETTINGS_FILES,
        ids=[f"{t}:{n}" for t, n, _ in SETTINGS_FILES],
    )
    def test_a_pinned_address_is_the_one_the_table_states(self, token, name, file):
        component = COMPONENT_OF[token]
        table = _variables(component)
        if not table:
            pytest.skip(f"{component}/component_functions.sh is not deployed")
        pinned = {
            _expand(value, table)
            for value in table.values()
            if _expand(value, table).rstrip("/").endswith("/" + name)
        }
        if not pinned:
            # RetroDECK names what it configures; a file it never writes — the
            # legacy ini melonDS migrates from — is nobody's disagreement.
            pytest.skip(f"{component} pins no {name}")
        stated = {
            f"{BASE_VARIABLES['xdg-' + base]}/{file.directory.default.name}/{file.path}"
            for base in file.bases
        }
        excuse = SETTINGS_DISAGREEMENTS.get((token, name))
        if excuse is not None:
            assert not (pinned & stated), (
                f"{token}/{name} is recorded as a deliberate disagreement ({excuse}) and the "
                "two now agree — the arrangement fixed it, so the exception should go"
            )
            return
        assert pinned & stated, (
            f"{component} pins {sorted(pinned)} for {name} and the settings table states "
            f"{sorted(stated)} — one of the two moved. Either re-read the address, or record "
            "the disagreement with its reason the way xemu's is"
        )


class TestEveryCitationStillPointsSomewhere:
    """Pass three: the weakest check, over all of them."""

    @pytest.mark.parametrize(
        ("component", "script", "line", "source"),
        CITATIONS,
        ids=[f"{s}:{c}/{n}:{ln}" for c, n, ln, s in CITATIONS],
    )
    def test_the_cited_script_and_line_exist(self, component, script, line, source):
        lines = _lines(component, script)
        if lines is None:
            if not _deployed():
                pytest.skip("RetroDECK is not deployed")
            pytest.fail(
                f"{source} cites components/{component}/{script} and the deployed tree has no "
                "such script — the component was renamed or dropped, so every citation into it "
                "is stale"
            )
        assert line <= len(lines), (
            f"{source} cites components/{component}/{script}:{line} and the script has "
            f"{len(lines)} lines"
        )
        assert lines[line - 1].strip(), (
            f"{source} cites components/{component}/{script}:{line} and that line is now blank "
            "— the evidence moved"
        )


def test_the_citations_are_really_read_where_retrodeck_is_deployed():
    # The all-skip guard: a run that checked nothing must not look clean.
    if not _deployed():
        pytest.skip(f"nothing is deployed at {COMPONENTS}")
    read = [c for c in CITATIONS if _lines(c[0], c[1]) is not None]
    assert read, (
        f"RetroDECK is deployed at {COMPONENTS} and not one cited script was read from it — "
        "either the components moved or this whole file is silently checking nothing"
    )
