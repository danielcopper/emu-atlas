# emu-atlas

The map of where emulators keep things — a config-aware knowledge library answering, for any emulator installation on a
machine: which config files govern it, how they override each other, and where saves and BIOS actually live.

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
- **All I/O goes through an injected reader.** The library never touches the disk directly; it asks a narrow reader
  protocol (`read_text`, `glob`, `exists`). In production the default reader is the real filesystem. In tests and
  conformance vectors it is a fixture tree — a plain mapping of paths to file contents describing a whole machine — so
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
   locations per install flavor (flatpak, native, RetroDECK, EmuDeck) as data.
2. **BIOS registry** — which firmware files each platform and libretro core wants, with hashes and sizes (548 entries
   across 54 platforms and 122 cores at extraction time), plus the classification rules (required / optional / unknown
   relative to an active core).
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

Scoping. The extraction sources are in production in decky-romm-sync (~700 lines of pure, tested knowledge code plus the
BIOS registry); the phase issues track the work.
