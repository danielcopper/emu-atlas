"""A Qt settings file, read as text — the format four emulators keep settings in.

Azahar, DuckStation, PCSX2 and melonDS's Qt frontend all write ``QSettings``
files, and two questions read them: where saves and texture packs live
(:mod:`atlas.installations`) and which firmware a launch expects
(:mod:`atlas.firmware`). The reading lives here so both reach it without one
importing the other, the way :mod:`atlas.melonds` and
:mod:`atlas.yaml_scalars` do for their formats.

Pure text in, mapping out. What a value *means* is the emulator's, and stays
with the caller: an empty string is a real value in one emulator and "unset"
in another, and a ``\\default`` companion key changes which of two values wins
(Azahar's ``ReadSetting``). Nothing here interprets.
"""

from __future__ import annotations


def unescape_section(name: str) -> str:
    """QSettings' ``%XX`` section-name escaping, undone byte-wise.

    ``[Data%20Storage]`` is the group ``Data Storage``. The undo is byte-wise
    because the escape encodes bytes, not characters, and a pair that is not
    valid hexadecimal is left as written rather than guessed at.
    """
    out = bytearray()
    index = 0
    encoded = name.encode("utf-8")
    while index < len(encoded):
        if encoded[index : index + 1] == b"%" and index + 2 < len(encoded) + 1:
            hex_pair = encoded[index + 1 : index + 3]
            try:
                out.append(int(hex_pair, 16))
                index += 3
                continue
            except ValueError:
                pass
        out.append(encoded[index])
        index += 1
    return out.decode("utf-8", errors="replace")


def values(text: str) -> dict[tuple[str, str], str]:
    """A Qt settings file as ``(section, key) -> raw value`` — read, not interpreted."""
    parsed: dict[tuple[str, str], str] = {}
    section = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = unescape_section(line[1:-1])
            continue
        key, separator, value = line.partition("=")
        if separator:
            parsed[(section, key.strip())] = value.strip()
    return parsed
