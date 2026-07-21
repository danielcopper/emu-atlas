# Interface design

Status: **draft** — fixed in scoping, refined during phase 1.

## The two entry points

```python
import atlas

# Detection: what is installed on this machine?
installations = atlas.detect(home="/home/deck")
# → [RetroDeck(root=...), StandaloneRetroArchFlatpak(root=...), NativeRetroArch(root=...)]

# Questions are asked of an installation:
inst = installations[0]
placement = inst.save_placement(system="gba", core="mgba")
bios = inst.bios_dir("psx")
needed = inst.required_bios("psx", core="swanstation")
```

A convenience helper wraps both steps for the simple case — `atlas.save_location("gba")` runs detection and asks every
installation — but the handle API is the foundation: coexisting installations each give their own answer, and the caller
(or their user) chooses.

## Placements

A placement is a template with named holes, not a resolved path:

```python
SavePlacement(
    dir="~/retrodeck/saves/<content_dir>",
    filename="<rom_stem>.srm",
    needs=["content_dir", "rom_stem"],
    sources=[
        "retroarch.cfg: sort_savefiles_by_content_enable = \"true\"",
        "default: savefile_directory (RetroDECK ships sort-by-content on)",
    ],
)
```

- Holes the caller can always fill from the ROM at hand: `<rom_stem>`, `<content_dir>` (the ROM's parent folder name —
  with sort-by-content the system slug appears only _as_ that folder, never as an extra path component).
- Holes that need identity knowledge: `<save_id>` (serial / title id) — sigil is one supplier, never a dependency.
- `sources` is the provenance trail: which config file produced each governing value, which default applied.
- "Saves live next to the ROMs" (`savefiles_in_content_dir`) is a valid placement, not an error.

## The reader seam

All filesystem access goes through one narrow protocol:

```python
class Reader(Protocol):
    def read_text(self, path: str) -> str | None: ...
    def glob(self, pattern: str) -> list[str]: ...
    def exists(self, path: str) -> bool: ...
```

`atlas.detect(home)` defaults to the real filesystem reader — in production the library reads the machine itself. Tests
and conformance vectors inject a fixture tree instead: a mapping of paths to file contents that describes a whole
machine. One code path, two data sources — which makes _everything_ vector-testable, detection and override chains
included:

```json
{
  "name": "retrodeck-content-sort-on",
  "input": {
    "files": {
      "~/.var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json": "{\"paths\": {\"rd_home_path\": \"~/retrodeck\"}}",
      "~/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch.cfg": "sort_savefiles_by_content_enable = \"true\"\n"
    }
  },
  "expected": {
    "installations": [{ "kind": "retrodeck", "root": "~/retrodeck" }],
    "save_placement": { "dir": "~/retrodeck/saves/<content_dir>", "filename": "<rom_stem>.srm" }
  }
}
```

## Vocabulary

Atlas defines canonical system ids and ships translation tables for the dialects it meets (RomM `slug` / `fs_slug`,
ES-DE `system`, RetroArch core and database names). Public functions accept canonical ids; translators are explicit
(`atlas.systems.from_romm_slug("gba")`), never guessed.

## Open questions (settled during phase 1)

- Exact canonical system-id set (lean toward ES-DE names — both RetroDECK and EmuDeck ship ES-DE).
- Override-chain representation in provenance when more than two layers stack (RetroArch global → core → content-dir →
  game overrides).
- Whether detection reports confidence/health (present-but-broken installs) alongside kind and root.

## Settled decisions

- **Python reference + data + vectors; no native core planned.** gavel's C core made sense because its kernel is the
  dream case for a C ABI: four strings in, an enum out, allocation-free. atlas is the opposite shape — config parsing,
  string assembly, variable-length results, and a reader seam that would become callback FFI across a C boundary:
  exactly the allocation-and-ownership hazards gavel's design ruled out. The portable core of atlas is the knowledge,
  not the code: the registry is language-neutral JSON, the probe locations are data, the rules are small, and the
  vectors are the contract — a client in another language implements the thin logic natively and proves it against the
  vectors, it does not link a library to read an ini file. Revisit only if a concrete consumer asks for a drop-in.

- **No cross-installation fall-through.** A RetroDECK install with no own `retroarch.cfg` gets RetroDECK's defaults — it
  never silently borrows a coexisting standalone install's cfg. Every question is asked of one installation; mixing
  their configs would make answers depend on unrelated installs being present.
