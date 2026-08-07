<div align="center">

<img src="assets/compass-rose-animated.gif" alt="" width="180">

# emu-atlas

<h3>Where an emulator installation actually keeps its saves and BIOS — read live off the machine, not off a list</h3>

[![CI](https://github.com/danielcopper/emu-atlas/actions/workflows/ci.yml/badge.svg)](https://github.com/danielcopper/emu-atlas/actions/workflows/ci.yml)
[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=danielcopper_emu-atlas&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=danielcopper_emu-atlas)
[![Coverage](https://img.shields.io/sonar/coverage/danielcopper_emu-atlas?server=https%3A%2F%2Fsonarcloud.io)](https://sonarcloud.io/summary/new_code?id=danielcopper_emu-atlas)
[![Maintainability](https://sonarcloud.io/api/project_badges/measure?project=danielcopper_emu-atlas&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=danielcopper_emu-atlas)
[![Reliability](https://sonarcloud.io/api/project_badges/measure?project=danielcopper_emu-atlas&metric=reliability_rating)](https://sonarcloud.io/summary/new_code?id=danielcopper_emu-atlas)
[![Security](https://sonarcloud.io/api/project_badges/measure?project=danielcopper_emu-atlas&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=danielcopper_emu-atlas)

</div>

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
  `installation.save_location(content_path=..., core_so=...)` and `installation.emulators_for(system)` on every handle —
  the ones without a frontend catalogue answer with the reason rather than an empty list. Choosing one of them is
  optional: `every_installation(home)` asks them all and labels each answer with the handle it came from, because two
  arrangements on one machine give two true answers and picking a winner would be the guess.
- **All machine access goes through an injected seam.** The library never touches the machine directly; it asks a narrow
  machine protocol (`read_text`, `glob`, `path_kind`, `readlink`, `query_core`, `file_size`, `file_digest`) whose every
  operation reports an explicit outcome — missing is not unreadable is not invalid text — because the emulators make
  those distinctions and health reporting depends on them. In production the seam is the real machine. In tests and
  conformance vectors it is a fixture machine — files, directories, symlinks, and core answers as plain data — so
  detection, config parsing, and override chains are all provable from data, failure states included.
- **Placements are templates, not paths.** Where a concrete path cannot be known from configs alone, the answer carries
  named holes: `<content_dir>` when the layout keys on the ROM's own folder and no ROM was named, `<library_name>` when
  the core would not load, `<save_id>` where the emulator keys the save off a serial or title id it reads from the ROM
  itself (Flycast's per-game VMUs) — that one holds in the file names, not the directory, because a file set is a
  template too. Whoever can fill a hole fills it; [sigil](https://github.com/rommforge/argosy-sigil) is one supplier of
  `save_id`, not a dependency.
- **Every answer carries provenance.** Which config file said so, which default applied. Debugging a user's broken setup
  is the daily reality of every consumer; explainability is a feature, not a log line.

emu-atlas depends on nothing and nothing depends on it: sigil identifies, atlas locates,
[gavel](https://github.com/danielcopper/romm-gavel) decides — three independent libraries a client composes.

## What lives here

1. **RetroArch knowledge** — interpretation of `retroarch.cfg` (the three save-layout keys and their override
   semantics), the save-directory math (`sort_by_content` / `sort_by_core`), core `.info` parsing, and the probe
   locations per install flavor (flatpak, native, RetroDECK; EmuDeck is planned — no production knowledge exists for it
   yet) as data.
2. **Firmware** — split at the boundary rule. _Which_ files a core wants is read live off the machine, from the `.info`
   files RetroArch ships next to its cores, so it can never drift against the cores an installation actually has. _What
   a correct file's bytes are_ — the `md5` / `sha1` / `size` triple — is world knowledge and ships as a packaged,
   versioned, source-cited table (388 identities); its generator and data provenance
   (`scripts/generate_firmware_hashes.py`, `atlas/data/README.md`) live with the data.
3. **ES-DE knowledge** — `es_systems.xml` / `es_find_rules.xml` parsing and launch-command classification, generalized
   across the frontends that ship ES-DE (RetroDECK, EmuDeck, standalone).
4. **Standalone emulators** — per-emulator config parsing and save/BIOS placement rules (Dolphin, PPSSPP, RPCS3, …), as
   multi-emulator support becomes concrete.
5. **A canonical system vocabulary** — atlas ids plus translation tables for the naming dialects (RomM slugs, ES-DE
   system names, RetroArch core/database names); planned, so until it lands a query speaks the vocabulary of the source
   that enumerates it.

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
  BIOS service already runs on the firmware knowledge extracted here.
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
- `atlas.every_installation(home, machine=...)` puts the protocol's questions to every detected installation at once and
  answers each labelled with the handle that produced it, in detection order — fan-out only: it merges nothing, prefers
  nothing, and resolves nothing that a handle does not resolve. A machine with one installation answers once; a machine
  with none answers with nothing, which is a result and not an error.
- `installation.save_location(content_path=..., core_so=...)` resolves the save directory the way RetroArch does:
  platform-default roots, the four-layer override chain (gated by `auto_overrides_enable` / `game_specific_options` /
  `rgui_config_directory`), `library_name` read live from the core binary through the Flatpak-deployment translation,
  file sets observed literally (glob-escaped, RetroArch's `.ldci` bookkeeping filtered) or honestly unknown, granularity
  plus the option that switches it where a rule card exists (Flycast, LRPS2), and structured caveats for every stated
  degradation. Where the granularity is deliberately not stated — a core whose file set depends on options atlas does
  not interpret — the answer says so and names those options, so "unstated" never arrives looking like "nothing to
  report". A sorted directory that does not exist yet is a conditional answer with a structural `fallback_dir`; a
  placement reached through symlinks reports its `physical_dir`, and a dead `dir_prep` link is a stated caveat, not a
  silent path.
- `installation.emulators_for(system, content_path=...)` — on every handle — answers which emulators can launch a
  system. On RetroDECK it reads the ES-DE catalogue live (bundled + custom overlay) and resolves the effective default
  through the full hierarchy: per-game `altemulator` > per-system `alternativeEmulator` > declared order. Entries carry
  their core, so placement answers on that path need no core argument; a standalone entry answers with a typed
  `Unresolved` outcome instead of raising. Where there are no entries the answer says which kind of none: a bare
  RetroArch ships no catalogue at all, an EmuDeck arrangement may have one atlas has not established the location of,
  and a catalogue atlas could not read — missing, unreadable, or empty — is not an empty one; three codes, because a
  client must not read the last two as "nothing here".
- The audit trail: `docs/research/coverage-matrix.md` (generated, with full source identity) tracks every referenced
  emulator's verdict and per-arrangement verification; `atlas/data/core_audit.json` enforces card maintenance by test;
  verification fails closed — drifted **and** unverifiable live versions raise an `unverified-version` caveat at answer
  time. The arrangement itself is held to the same standard: every answer from one no live installation has confirmed
  carries `arrangement-unverified` (today EmuDeck and both bare-RetroArch handles; RetroDECK was verified against a
  running 0.10.9b installation). The claim is about atlas's evidence, not about the machine — the config chain is
  source-verified everywhere — and the status is packaged data, so verifying an arrangement retires the caveat without
  touching a resolver.
- Firmware, in four calls over one live read — `firmware_for_core(core_so=...)`, `firmware_for_system(system=...)`,
  `firmware_inventory()`, `identify_firmware(md5=...)`. Every installed core's declarations come from
  `libretro_info_path` (sandbox paths translated to the Flatpak deployment, limited to cores whose `.so` is actually
  there) and each requirement states its **absolute destination** under the live `system_directory` whether or not a
  file is sitting there. Two axes stay apart: `need` is `required` / `optional`, `checked` is `verified` / `mismatch` /
  `unchecked` (identity known, not asked about) / `unknown` (cannot be established) — "we did not look" is never the
  same answer as "we looked and cannot tell". `requirements_met` is `true` only when every required file is there and
  atlas _established_ that it is the right one: a present file with the wrong bytes makes it `false`, and one that was
  never verified — the default — makes it `null`, so a green light is asked for rather than assumed. A core that is
  installed and declares nothing answers "needs nothing"; one whose `.info` cannot be read answers
  `declaration="unreadable"`, and one that is not here answers `"absent"` — the same empty list never means three
  things. `identify_firmware` runs the download flow off content: one md5 comes back with every name it is known as and
  every destination on this machine that wants it. Files nobody declares are listed separately and identified by bytes;
  save data the rule cards claim (Flycast's VMUs, PCSX2's memory cards) is excluded outright. Where a file's system had
  to be derived from what its whole core is called — the per-file table is derived and deliberately incomplete — that
  emulator's entry says so and names the files, and a core shipping no `systemname` at all is its own stated case. An
  empty answer distinguishes "this identifier is unknown here" from "nothing declares firmware for it"; the two mean
  different things to a client.
- `atlas/contract.py` is the canonical JSON-shaped serialization of every answer — the same code the conformance run
  asserts with exact equality, available to consumers.
- The conformance vectors (`vectors/`, schema 2) are whole fixture machines — files, directories, symlinks, core
  answers, firmware blobs and read-failure states — each replayed against the canonical serialization and asserted with
  exact equality, alongside the unit suite on every push. Zero runtime dependencies, CI-verified wheel/sdist.

What is not covered yet, and in which order it comes: `ROADMAP.md`. The systematic core-by-core state:
`docs/research/coverage-matrix.md`.
