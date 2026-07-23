# emu-atlas

The map of where emulators keep things — a resolver answering live, for any emulator installation on a machine: which
config files govern it, how they override each other, and where saves and BIOS actually live.

## Why

Every tool that touches emulator data re-learns the same facts: a save-sync client needs the save directory, a backup
tool needs it too, a BIOS manager needs the firmware folder and which files belong in it. Today that knowledge lives as
prose in wikis, as static path lists that go stale, and as private code inside each frontend and client.

Static lists are the trap: RetroArch's actual save layout depends on live config values (`savefiles_in_content_dir`,
`sort_savefiles_by_content_enable`, `sort_savefiles_enable`), on the active core, and on which of several install
flavors is present. A path list is wrong the moment a user flips a setting. The truth lives in the configs — so the
library reads them the way the emulator does: probe order, override chains, defaults.

## Design

Four principles, fixed before any code:

- **Installations are handles.** `detect(home)` finds what is present — RetroDECK, EmuDeck, standalone installs, any of
  them side by side — and every question is asked _of an installation_, never of a global "the system":
  `installation.save_placement(system, core=...)`, `installation.bios_dir(system)`.
- **All machine access goes through an injected seam.** The library never touches the machine directly; it asks a narrow
  machine protocol (`read_text`, `glob`, `exists`, `readlink`, `query_core`) — files, symlinks, and the answers only a
  core binary can give, which is the same read the emulator itself performs. In production the seam is the real machine.
  In tests and conformance vectors it is a fixture machine — files, symlinks, and core answers as plain data — so
  detection, config parsing, and override chains are all provable from data.
- **Placements are templates, not paths.** Where a concrete path cannot be known from configs alone, the answer carries
  named holes: `<rom_stem>` for RetroArch-style naming, `<save_id>` where the emulator keys saves off a serial or title
  id. Whoever can fill a hole fills it — [sigil](https://github.com/rommforge/argosy-sigil) is one supplier of
  `<save_id>`, not a dependency.
- **Every answer carries provenance.** Which config file said so, which default applied. Debugging a user's broken setup
  is the daily reality of every consumer; explainability is a feature, not a log line.

emu-atlas depends on nothing and nothing depends on it: sigil identifies, atlas locates,
[gavel](https://github.com/danielcopper/romm-gavel) decides — three independent libraries a client composes.

## What lives here

1. **RetroArch knowledge** — interpretation of `retroarch.cfg` (the three save-layout keys and their override
   semantics), the save-directory math (`sort_by_content` / `sort_by_core`), core `.info` parsing, and the probe
   locations per install flavor (flatpak, native, RetroDECK; EmuDeck is planned — no production knowledge exists for it
   yet) as data.
2. **BIOS registry** — which firmware files each platform and libretro core wants, with hashes and sizes (548 entries
   across 54 platforms and 122 cores at extraction time), plus the classification rules (required / optional / unknown
   relative to an active core); its generator and data provenance (`scripts/generate_bios_registry.py`,
   `atlas/data/README.md`) live with the data.
3. **ES-DE knowledge** — `es_systems.xml` / `es_find_rules.xml` parsing and launch-command classification, generalized
   across the frontends that ship ES-DE (RetroDECK, EmuDeck, standalone).
4. **Standalone emulators** — per-emulator config parsing and save/BIOS placement rules (Dolphin, PPSSPP, RPCS3, …), as
   multi-emulator support becomes concrete.
5. **A canonical system vocabulary** — atlas ids plus translation tables for the naming dialects (RomM slugs, ES-DE
   system names, RetroArch core/database names).

Conformance follows the gavel pattern: language-neutral vectors, each one a fixture machine in and the expected
installations + placements out.

## What does not live here

- ROM identification — which game a file is and what its save will be called: sigil's territory.
- Sync decisions — what to do when local and server disagree: gavel's territory.
- File transfer, UI, per-client policy: the client's territory.

## Integration opportunities

Places this knowledge could plug in — options, not commitments:

- **[decky-romm-sync](https://github.com/danielcopper/decky-romm-sync)** (first consumer): the PlatformEnvironment seam
  resolves paths and invocations per installation; the save-placement model composes atlas templates with sigil ids; the
  BIOS service already runs on the registry extracted here.
- **Other RomM clients**: [grout](https://github.com/rommapp/grout) (Go, retro handhelds — where path dialects diverge
  hardest), [argosy](https://github.com/rommapp/argosy-launcher) (Android RetroArch layouts), and whatever comes next —
  each currently carries its own path knowledge.
- **Backup tooling**: exporting detected installations in the
  [ludusavi manifest](https://github.com/mtkennerly/ludusavi-manifest) format would make atlas useful to an existing
  user base without anyone adopting a library.
- **The frontends themselves**: RetroDECK and EmuDeck maintain this knowledge as shell scripts today; a shared,
  conformance-tested base is the same offer gavel makes for sync decisions.

## Status

Phase 1 shipped — RetroArch knowledge and the BIOS registry, extracted from decky-romm-sync's production code. What
exists now:

- `atlas.detect(home, reader=...)` finds RetroDECK, the standalone `org.libretro.RetroArch` Flatpak, and a native
  `~/.config/retroarch` install by their config markers, in that order; coexisting installs each return their own
  handle.
- Every installation answers `save_placement(system, core=..., rom_dir_name=...)` with a `SavePlacement` — a template
  carrying named holes (`<content_dir>`, `<rom_stem>`, `<savefile_directory>`, `<core>`), the `needs` still to be
  filled, and a provenance trail. RetroDECK handles also expose `bios_dir()` and `roms_dir()`.
- `retroarch.cfg` interpretation of the three save-layout keys (`savefiles_in_content_dir`,
  `sort_savefiles_by_content_enable`, `sort_savefiles_enable`) plus `savefile_directory`, each with per-key provenance.
- The RetroArch `.info` parser and the BIOS registry (548 entries across 54 platforms and 122 cores) with entry lookup
  and a required-classification query that honors the per-core override over the top-level flag.
- All filesystem access flows through the injected `Reader`; a `machines` conformance-vector family (16 cases) drives
  real detection and placement from fixture machines, with save-placement expectations oracle-derived from
  decky-romm-sync's `resolve_save_dir` / `compute_local_save_target`.

EmuDeck detection is **not** in this phase — research now exists (`docs/research/retrodeck-save-placement.md`, §13) but
no implementation. ES-DE knowledge, standalone emulators, and the canonical system vocabulary remain future phases.

The current code predates the resolver redesign: the shipped `Reader` still lacks `readlink`/`query_core`, and
`save_placement` does not yet read the override chain. `DESIGN.md` specifies the target; the code follows it next.
