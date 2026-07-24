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
  `installation.save_location(content_path=..., core_so=...)`, `installation.emulators_for(system)`.
- **All machine access goes through an injected seam.** The library never touches the machine directly; it asks a narrow
  machine protocol (`read_text`, `glob`, `path_kind`, `readlink`, `query_core`) whose every operation reports an
  explicit outcome — missing is not unreadable is not invalid text — because the emulators make those distinctions and
  health reporting depends on them. In production the seam is the real machine. In tests and conformance vectors it is a
  fixture machine — files, directories, symlinks, and core answers as plain data — so detection, config parsing, and
  override chains are all provable from data, failure states included.
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

The resolver core is built and verified live against a real RetroDECK 0.10.9b installation. What exists now:

- `atlas.detect(home, machine=...)` finds RetroDECK, EmuDeck, the standalone `org.libretro.RetroArch` Flatpak, and a
  native install — handles implementing one `Installation` protocol, with structured health (a list of issue caveats
  with stable codes: unreadable or invalid markers, missing roots, a stale EmuDeck whose claimed RetroArch config is
  gone), ordered markers (EmuDeck claims the Flatpak it configures), and never a silently chosen winner. Handles are
  live: every query re-reads its governing sources, each exactly once.
- `installation.save_location(content_path=..., core_so=...)` resolves the save directory the way RetroArch does:
  platform-default roots, the four-layer override chain (gated by `auto_overrides_enable` / `game_specific_options` /
  `rgui_config_directory`), `library_name` read live from the core binary through the Flatpak-deployment translation,
  file sets observed literally (glob-escaped, RetroArch's `.ldci` bookkeeping filtered) or honestly unknown, granularity
  plus the option that switches it where a rule card exists (Flycast, LRPS2), and structured caveats for every stated
  degradation. A sorted directory that does not exist yet is a conditional answer with a structural `fallback_dir`; a
  placement reached through symlinks reports its `physical_dir`, and a dead `dir_prep` link is a stated caveat, not a
  silent path.
- `installation.emulators_for(system, content_path=...)` reads the ES-DE catalogue live (bundled + custom overlay) and
  resolves the effective default through the full hierarchy: per-game `altemulator` > per-system `alternativeEmulator` >
  declared order. Entries carry their core, so placement answers on that path need no core argument; a standalone entry
  answers with a typed `Unresolved` outcome instead of raising.
- The audit trail: `docs/research/coverage-matrix.md` (generated, with full source identity) tracks every referenced
  emulator's verdict and per-arrangement verification; `atlas/data/core_audit.json` enforces card maintenance by test;
  verification fails closed — drifted **and** unverifiable live versions raise an `unverified-version` caveat at answer
  time.
- `atlas/contract.py` is the canonical JSON-shaped serialization of every answer — the same code the conformance run
  asserts with exact equality, available to consumers.
- 246 tests, 33 conformance vectors (schema 2: whole fixture machines with files, dirs, symlinks, core answers, and
  read-failure states), zero runtime dependencies, CI-verified wheel/sdist.

What is not covered yet, and in which order it comes: `ROADMAP.md`. The systematic core-by-core state:
`docs/research/coverage-matrix.md`.
