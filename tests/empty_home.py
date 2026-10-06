"""What ``detect`` answers for an empty home on the real machine this suite runs on.

An empty home holds nothing, so nothing it holds is detected. The machine is
not empty, though: a RetroDECK Flatpak deployed in the system installation runs
for every home, and in a home with no RetroDECK marker it is an installation
whose health is exactly ``not-set-up`` (issue #579). So the answer is ``[]`` on
a machine without that deploy and that one installation on a machine with it —
read off the machine here rather than assumed, so the tests that ask hold on
both kinds of machine and say it the same way.
"""

from __future__ import annotations

import os
from typing import Any

_SYSTEM_DEPLOY = "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active"
_MARKER = os.path.join(".var", "app", "net.retrodeck.retrodeck", "config", "retrodeck", "retrodeck.json")


def assert_nothing_the_empty_home_holds_is_detected(detected: Any, home: str) -> None:
    """*detected* is the serialized ``detect`` answer for the empty *home*.

    Nothing the empty home holds is detected; the only installation that can
    appear is a system deploy without its marker, whose health is exactly
    ``[not-set-up]``.
    """
    expected: list[dict[str, Any]] = []
    if os.path.isdir(_SYSTEM_DEPLOY):
        expected.append(
            {
                "kind": "retrodeck",
                "label": "RetroDECK",
                "kinds": ["retrodeck"],
                "root": os.path.join(home, "retrodeck"),
                "health": [
                    {
                        "code": "not-set-up",
                        "data": {"path": os.path.join(home, _MARKER), "app_id": "net.retrodeck.retrodeck"},
                    }
                ],
            }
        )
    assert detected == expected
