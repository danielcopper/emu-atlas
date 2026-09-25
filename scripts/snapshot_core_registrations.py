#!/usr/bin/env python3
"""Re-record each rule card's option registration from the deployed cores.

Two files take what this reads, one core at a time:

- ``atlas/data/core_audit.json`` — the card's ``registration``: the sorted keys
  the core registers, which is all the resolver compares an installed core
  with (``core-options-unaudited``, stated in :mod:`atlas.placement`);
- ``tests/data/core_registrations.json`` — the whole registration, every key
  with its registered default and values, under the ``build`` it was read from
  (the core's own ``library_version``). The tripwire in ``tests/test_oddities.py``
  holds it against the deployed core, and a test holds its keys equal to the
  audit record's.

Re-recording is this one command; it follows a card's re-audit against the
deployed build, never replaces it — where it sits in the audit method is stated
in ``docs/research/core-audit.md``::

    python scripts/snapshot_core_registrations.py

**Which build.** The cores RetroDECK deploys from its installed Flatpak
(:data:`DEPLOYED_CORES`, the ``current/active`` deployment), read through the
probe the resolver uses (:meth:`atlas.machine.RealMachine.read_core`). A card
whose ``retrodeck`` verification record pins a RetroDECK ``version`` or a
``core_library_version`` other than the deployed one is skipped and named: its
record describes another build, and re-recording from this one would pass that
build's registration off as the audited one's. A card with no such record is
recorded from the deployed build as it stands.

**What is written.** A core that answered with a captured registration gets its
keys and its whole registration. One that answered with none captured during
``retro_set_environment`` gets ``"not-captured"`` in both files, never an empty
list. A core that did not answer — absent, refused by the loader, crashed —
and a skipped card keep whatever both files already record.
"""
import json
import re
from pathlib import Path
from typing import Any

from atlas.machine import CORE_READ_ANSWERED, CoreInfo, RealMachine
from atlas.oddities import REGISTRATION_NOT_CAPTURED, load_oddities

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = REPO_ROOT / "atlas" / "data" / "core_audit.json"
SNAPSHOT_PATH = REPO_ROOT / "tests" / "data" / "core_registrations.json"

RETRODECK = Path("/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck")
DEPLOYED_CORES = RETRODECK / "components" / "retroarch" / "rd_extras" / "cores"

SNAPSHOT_SPEC = (
    "Test-side: each rule card's whole option registration — every key with its registered default and "
    "values — as the deployed core registered it, under the build it was read from (the core's own "
    "library_version), or 'not-captured'. Written by scripts/snapshot_core_registrations.py; the package "
    "carries only the keys (registration in atlas/data/core_audit.json), and tests/test_oddities.py holds "
    "this file against the deployed cores and its keys against the audit record's."
)

# Stands in for one option's definition while the snapshot is serialized, so
# the option can be written back on one line of its own. A NUL cannot occur in
# the file's own strings, so the serialized placeholder is unambiguous.
_PLACEHOLDER = "\x00option:{}"
_SERIALIZED_PLACEHOLDER = re.compile(r'"\\u0000option:(\d+)"')


def serialize_snapshot(snapshot: dict[str, Any]) -> str:
    """The snapshot as the file keeps it: two-space JSON, one line per registered option."""
    compact: list[str] = []
    for entry in snapshot["cores"].values():
        registration = entry["registration"]
        if not isinstance(registration, dict):
            continue
        for key, option in registration.items():
            compact.append(json.dumps(option, ensure_ascii=False))
            registration[key] = _PLACEHOLDER.format(len(compact) - 1)
    text = json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"
    return _SERIALIZED_PLACEHOLDER.sub(lambda match: compact[int(match.group(1))], text)


def pinned_elsewhere(entry: dict[str, Any], *, version: str, info: CoreInfo) -> str | None:
    """Why this card's retrodeck record describes another build than the deployed one, or ``None``."""
    record = entry.get("verified", {}).get("retrodeck")
    if record is None:
        return None
    if record["version"] != version:
        return f"record pins RetroDECK {record['version']!r}, deployed is {version!r}"
    pinned_core = record.get("core_library_version")
    if pinned_core is not None and pinned_core != info.library_version:
        return f"record pins core {pinned_core!r}, deployed is {info.library_version!r}"
    return None


def main() -> None:
    audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    snapshot: dict[str, Any] = (
        json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        if SNAPSHOT_PATH.is_file()
        else {"spec": SNAPSHOT_SPEC, "cores": {}}
    )
    snapshot["spec"] = SNAPSHOT_SPEC
    version = (RETRODECK / "version").read_text(encoding="utf-8").strip()
    print(f"cores: {DEPLOYED_CORES} (RetroDECK {version})")
    machine = RealMachine()
    captured, not_captured, kept = [], [], []
    for card in load_oddities():
        reading = machine.read_core(str(DEPLOYED_CORES / card.so_name))
        entry = audit["cores"][card.key]
        if reading.status != CORE_READ_ANSWERED or reading.info is None:
            kept.append(f"{card.key} ({reading.status}{': ' + reading.unloadable if reading.unloadable else ''})")
            continue
        info = reading.info
        elsewhere = pinned_elsewhere(entry, version=version, info=info)
        if elsewhere is not None:
            kept.append(f"{card.key} ({elsewhere})")
            continue
        if info.options is None:
            entry["registration"] = REGISTRATION_NOT_CAPTURED
            full: Any = REGISTRATION_NOT_CAPTURED
            not_captured.append(card.key)
        else:
            entry["registration"] = sorted(info.options)
            full = {
                key: {"default": info.options[key].default, "values": list(info.options[key].values)}
                for key in sorted(info.options)
            }
            captured.append(f"{card.key} ({len(info.options)})")
        snapshot["cores"][card.key] = {"build": info.library_version, "registration": full}
    snapshot["cores"] = dict(sorted(snapshot["cores"].items()))
    AUDIT_PATH.write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    SNAPSHOT_PATH.write_text(serialize_snapshot(snapshot), encoding="utf-8")
    print(f"captured {len(captured)}: {', '.join(captured)}")
    print(f"not captured {len(not_captured)}: {', '.join(not_captured)}")
    print(f"kept as recorded {len(kept)}: {', '.join(kept) or '-'}")


if __name__ == "__main__":
    main()
