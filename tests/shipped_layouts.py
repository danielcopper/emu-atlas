"""The shipped layouts the cfg tests exercise — fixture knowledge, not atlas's.

Production reads every layout key off the machine and falls back to RetroArch's
own upstream defaults, so a table of what RetroDECK and EmuDeck put in their
configs is not knowledge atlas ships (M11). These two live here because two test
modules need the same fixture, and one definition cannot drift from itself.
"""

from __future__ import annotations

from atlas.retroarch_cfg import LayoutDefaults

RETRODECK_SHIPPED = LayoutDefaults(
    in_content_dir=False,
    sort_by_content=True,
    sort_by_core=False,
    label="RetroDECK shipped default",
)
EMUDECK_SHIPPED = LayoutDefaults(
    in_content_dir=False,
    sort_by_content=False,
    sort_by_core=False,
    label="EmuDeck shipped default",
)
