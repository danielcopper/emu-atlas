<div align="center">

<img src="assets/compass-rose-animated.gif" alt="" width="180">

# emu-atlas

<h3>What an emulator installation actually is and where it keeps everything — read live off the machine, not off a list</h3>

[![CI](https://github.com/danielcopper/emu-atlas/actions/workflows/ci.yml/badge.svg)](https://github.com/danielcopper/emu-atlas/actions/workflows/ci.yml)
[![Canary](https://github.com/danielcopper/emu-atlas/actions/workflows/canary.yml/badge.svg)](https://github.com/danielcopper/emu-atlas/actions/workflows/canary.yml)
[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=danielcopper_emu-atlas&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=danielcopper_emu-atlas)
[![Coverage](https://img.shields.io/sonar/coverage/danielcopper_emu-atlas?server=https%3A%2F%2Fsonarcloud.io)](https://sonarcloud.io/summary/new_code?id=danielcopper_emu-atlas)
[![Maintainability](https://sonarcloud.io/api/project_badges/measure?project=danielcopper_emu-atlas&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=danielcopper_emu-atlas)
[![Reliability](https://sonarcloud.io/api/project_badges/measure?project=danielcopper_emu-atlas&metric=reliability_rating)](https://sonarcloud.io/summary/new_code?id=danielcopper_emu-atlas)
[![Security](https://sonarcloud.io/api/project_badges/measure?project=danielcopper_emu-atlas&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=danielcopper_emu-atlas)

</div>

The map of where emulators keep things — a resolver answering live, for any emulator installation on a machine: which
config files govern it, how they override each other, and where saves, savestates, firmware, texture packs, mods,
screenshots and ROMs actually live — plus what the installed frontend declares: its systems, the emulators that launch
them, whether a file would launch, and how its systems answer to the public platform ids (IGDB, libretro, ScreenScraper,
TheGamesDB).

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

- **Installations are handles.** `detect(home)` finds what is present — RetroDECK, EmuDeck, bare RetroArch installs, any
  of them side by side — and every question is asked _of an installation_, never of a global "the system": the placement
  family (`savefile_location`, `savestate_location`, `screenshot_location`, `texture_pack_location`, `mod_location`,
  `soft_patch_candidates`), the catalogue family (`systems`, `emulators_for`, `rom_location`, `launchable`,
  `systems_for_platform`, `platform_ids`) and the firmware family (`firmware_for_core`, `firmware_for_system`,
  `firmware_inventory`, `identify_firmware`) on every handle — the ones without a frontend catalogue answer with the
  reason rather than an empty list. Choosing one of them is optional: `every_installation(home)` asks them all and
  labels each answer with the handle it came from, because two arrangements on one machine give two true answers and
  picking a winner would be the guess.
- **All machine access goes through an injected seam.** The library never touches the machine directly; it asks a narrow
  machine protocol (`read_text`, `glob`, `path_kind`, `readlink`, `query_core`, `file_size`, `file_digest`,
  `read_appimage_text`) whose every operation reports an explicit outcome — missing is not unreadable is not invalid
  text — because the emulators make those distinctions and health reporting depends on them. In production the seam is
  the real machine. In tests and conformance vectors it is a fixture machine — files, directories, symlinks, AppImage
  contents and core answers as plain data — so detection, config parsing, and override chains are all provable from
  data, failure states included.
- **Placements are templates, not paths.** Where a concrete path cannot be known from configs alone, the answer carries
  named holes: `<content_dir>` when the layout keys on the ROM's own folder and no ROM was named, `<library_name>` when
  the core would not load, `<save_id>` where the emulator keys the save off a serial or title id it reads from the ROM
  itself — in file names (Flycast's per-game VMUs) and in directory segments alike (Cemu's per-title MLC subtree),
  because a file set and a tree are templates too. Whoever can fill a hole fills it;
  [sigil](https://github.com/rommforge/argosy-sigil) is one supplier of `save_id`, not a dependency.
- **Every answer carries provenance.** Which config file said so, which default applied. Debugging a user's broken setup
  is the daily reality of every consumer; explainability is a feature, not a log line.

emu-atlas depends on nothing and nothing depends on it: sigil identifies, atlas locates,
[gavel](https://github.com/danielcopper/romm-gavel) decides — three independent libraries a client composes.

Map of the surface — layers, handles, answer types, and what to import from where: `docs/architecture.md`.

## What lives here

1. **RetroArch knowledge** — interpretation of `retroarch.cfg` (the three save-layout keys and their override
   semantics), the save-directory math (`sort_by_content` / `sort_by_core`), core `.info` parsing, and the probe
   locations per install flavor (flatpak, native, RetroDECK, EmuDeck) as data.
2. **Firmware** — split at the boundary rule. _Which_ files a core wants is read live off the machine, from the `.info`
   files RetroArch ships next to its cores, so it can never drift against the cores an installation actually has. _What
   a correct file's bytes are_ — the `md5` / `sha1` / `size` triple — is world knowledge and ships as a packaged,
   versioned, source-cited table (388 identities); its generator and data provenance
   (`scripts/generate_firmware_hashes.py`, `atlas/data/README.md`) live with the data.
3. **ES-DE knowledge** — `es_systems.xml` / `es_find_rules.xml` parsing and launch-command classification, generalized
   across the frontends that ship ES-DE (RetroDECK, EmuDeck, and a bare ES-DE install).
4. **Standalone emulators** — per-emulator config parsing and save placement rules, read the way each emulator reads its
   own configuration: Dolphin (GC slots and the Wii NAND), PPSSPP, xemu, Cemu (the per-title MLC unit), Azahar (the
   per-title unit on the 3DS's emulated SD), DuckStation (two memory-card slots, six modes), PCSX2 (slots whose card
   type is read off the disk, file or folder), melonDS (one `.sav` per game, beside the ROM by default), RPCS3 (the save
   tree the emulated PS3's own VFS names) and Vita3K (the `ux0` tree below its preference path) today, one emulator per
   sub-issue of the epic (#3) until every emulator RetroDECK ships resolves — on EmuDeck too, whichever way its
   catalogue launches them: AppImage, flatpak, or a binary unpacked out of one.
5. **The system vocabulary and the platform crosswalk** — the ids every question about a system takes are ES-DE's system
   names, shipped as packaged data cited to a stated build and guarded by a test that parses that build's own
   `es_systems.xml`; `known_systems()` and `from_esde_system()` let a client check its own map before using it. Beside
   them, the **platform** crosswalk answers how a catalogue's `<platform>` tags — read live — meet the public platform
   identities (IGDB by numeric id, libretro names, and the ScreenScraper/TheGamesDB ids ES-DE's own scrapers use):
   `systems_for_platform(vocabulary, value)` and `platform_ids(system)`. The identity table is world knowledge under the
   boundary rule — versioned, source-cited, regenerated from pinned upstreams — while which tags a system carries is
   always the machine's own statement (`DESIGN.md`, Vocabulary).

Conformance follows the gavel pattern: language-neutral vectors, each one a fixture machine in and the expected
installations + placements out.

## What does not live here

- ROM identification — which game a file is, and the platform-native id atlas leaves as the `<save_id>` hole in a
  declared file name rather than opening a ROM to read it: sigil's territory.
- Sync decisions — what to do when local and server disagree: gavel's territory.
- File transfer, UI, per-client policy: the client's territory.

## Integration opportunities

A consumer does not have to be Python: `emu-atlas` (or `python -m atlas`) answers every question as its contract JSON on
stdout, one question per invocation, held to the same bytes as the library by the conformance vectors — any language
runs a subprocess and parses. See [the command line](docs/how-to-use.md#the-command-line). A consumer without any Python
takes the release bundle instead — `emu-atlas-<tag>-x86_64-linux.tar.gz`, the CLI with its own pinned CPython 3.14,
smoke-tested against the full suite before every release; unpack anywhere, run `./emu-atlas`, nothing installs itself
elsewhere.

Where this knowledge plugs in — kinds of consumers, not commitments:

- **Save-sync and library clients**, in any language: per-installation paths and launch invocations, save placements
  with their granularity and holes, and BIOS state — the facts every such client currently re-derives and carries
  itself.
- **Backup tooling**: what to back up and what to leave (settings, screenshots, caches) is exactly the file-set and role
  vocabulary the answers speak; exporting detected installations into an established manifest format needs no library
  adoption at all.
- **The distributions themselves**: the arrangements maintain this knowledge as shell scripts today; a shared,
  conformance-tested base is the same offer gavel makes for sync decisions.

## Status

The resolver core is built and verified live against real installations — RetroDECK 0.10.9b and an EmuDeck at a pinned
backend revision — and a weekly canary deploys the newest RetroDECK from Flathub and runs the full suite against it,
re-reading the evidence off that deploy: the shipped binaries' own literals, and every component script the packaged
data cites, at the line it cites. Drift announces itself instead of waiting to be found. What exists now:

- `atlas.detect(home, machine=...)` finds RetroDECK, EmuDeck, the bare `org.libretro.RetroArch` Flatpak, and a native
  install — handles implementing one `Installation` protocol, with structured health (a list of finding caveats with
  stable codes: unreadable or invalid markers, missing roots, a stale EmuDeck whose claimed RetroArch config is gone),
  ordered markers (EmuDeck claims the Flatpak it configures), and never a silently chosen winner. Handles are live:
  every query re-reads its governing sources, each exactly once.
- `atlas.every_installation(home, machine=...)` puts the protocol's questions to every detected installation at once and
  answers each labelled with the handle that produced it, in detection order — fan-out only: it merges nothing, prefers
  nothing, and resolves nothing that a handle does not resolve. A machine with one installation answers once; a machine
  with none answers with nothing, which is a result and not an error.
- `installation.savefile_location(content_path=..., core_so=...)` resolves the save directory the way RetroArch does:
  platform-default roots, the four-layer override chain (gated by `auto_overrides_enable` / `game_specific_options` /
  `rgui_config_directory`), `library_name` read live from the core binary through the Flatpak-deployment translation,
  file sets observed literally (glob-escaped, RetroArch's `.ldci` bookkeeping filtered) or honestly unknown, granularity
  plus the option that switches it where a rule card exists (Flycast, LRPS2), and structured caveats for every stated
  degradation. Where the granularity is deliberately not stated — a core whose file set depends on options atlas does
  not interpret — the answer says so and names those options, so "unstated" never arrives looking like "nothing to
  report". A sorted directory that does not exist yet is a conditional answer with a structural `fallback_dir`; a
  placement reached through symlinks reports its `physical_dir`, and a dead `dir_prep` link is a stated caveat, not a
  silent path.
- `installation.savestate_location(content_path=..., core_so=...)` answers the same question for savestates, through
  RetroArch's savestate quartet of keys and the very same chain — one upstream function places both families, so atlas
  ports it once. Its answer is a `SavestatePlacement`: a save placement without `granularity`, because no core writes a
  savestate and no rule card for one can exist. In exchange it can name the files — `<stem>.state`, the numbered slots,
  the auto slot and their thumbnails are RetroArch's own naming — and a core whose `.info` declares no savestate support
  is stated as a caveat rather than left to be discovered.
- `installation.texture_pack_location(content_path=..., core_so=...)` answers where one emulator reads replacement
  textures from, joining a root read off the machine (the system directory as the core receives it, or the save root as
  it stands) to the fragment below it, which is per-core behaviour no config states and therefore packaged, versioned
  and source-cited. Beside the directory it states two things a client cannot derive: whether replacement is switched on
  right now — read from the options file RetroArch would read first, else the default the installed core registers, and
  honestly `None` where neither answered — and how the tree below the root is keyed per game, stated only where a
  citation backs it. Where nothing establishes an emulator's texture wiring the answer is a typed refusal that says so,
  never a directory nobody read; where the wiring is known but the root has never been observed in use, the directory is
  stated with `emulator-read-unestablished` beside it. Standalone emulators answer this question through the catalogue
  entry even where their saves refuse, and the asymmetry is the point: a save routes through a config atlas would have
  to model, while a standalone emulator's packs mostly sit at its own default below an XDG base a flatpak pins. Those
  answers leave the switch unstated and name the emulator configuration that would settle it, rather than reading an
  unread file as "off".
- `installation.mod_location(content_path=..., core_so=...)` answers where one emulator reads mods from, over the same
  join and the same grammar — with one difference: the answer is **plural**. An emulator may read mods from several
  directories that are different mechanisms rather than alternatives (FBNeo takes a replacement romset, an IPS patch set
  and a romdata file from three trees under one switch), so it answers a list of trees, each with its own directory,
  keying and the role that tells it apart; where an emulator has one tree the list has one entry and no role. Where the
  switch is a setting in a configuration atlas does not read the answer names that file — including on a core row, for a
  core whose switch is an ini inside the user tree it builds — and where no switch has been established at all it points
  nowhere rather than at a file that may govern nothing.
- `installation.soft_patch_candidates(content_path, core_so=...)` answers the other half of that question: the patch
  files RetroArch itself applies to content before any core sees it. It is the exact one — the four candidate paths in
  the order the frontend tries them, each with the nine indexed continuations that chain onto it, all composed from the
  content path by upstream arithmetic rather than looked up. Beside them it states whether this core's content is loaded
  into memory at all, which is what decides whether patching runs, and whether this RetroArch build was established to
  attempt each format, which no running machine states and which is therefore packaged per arrangement and pinned to the
  build it was read at. Nothing is written to disk: the patch is applied to the buffer the core is handed.
- `installation.emulators_for(system, content_path=...)` — on every handle — answers which emulators can launch a
  system. On RetroDECK it reads the ES-DE catalogue live (bundled + custom overlay) and resolves the effective default
  through the full hierarchy: per-game `altemulator` > per-system `alternativeEmulator` > declared order. On EmuDeck it
  reads the same hierarchy from its ES-DE's on-disk layers (EmuDeck's own `custom_systems` overlay, the gamelists,
  `es_settings.xml`) — and the bundled `es_systems.xml` sealed inside the ES-DE AppImage is read too, through atlas's
  own pure-stdlib squashfs reader, wherever the runtime has the image's codec (`compression.zstd` arrives with Python
  3.14; the published `backports.zstd` grants it to older interpreters). Where the codec is absent the sealed layer
  stays exactly that, stated with `emulator-catalogue-sealed` — the capability is the runtime's, never blamed on the
  machine. Entries carry their core, so placement answers on that path need no core argument; a standalone entry answers
  with a typed `Unresolved` outcome instead of raising. Where there are no entries the answer says which kind of none: a
  bare RetroArch ships no catalogue at all, an EmuDeck arrangement with no ES-DE on disk may have one atlas has not
  established the location of, a catalogue atlas could not read — missing, unreadable, or empty — is not an empty one,
  and a sealed catalogue's readable layers may simply not declare the system; four codes, because a client must not read
  the last three as "nothing here".
- `installation.rom_location(system)` — on every handle — answers where that system's ROMs live and which file
  extensions the frontend will launch, both off the same `<system>` declaration, so neither has to be recomputed from a
  table that cannot follow a user who moved their library. The directory is the declared `<path>` with `%ROMPATH%`
  substituted from the setting the frontend itself substitutes it from, resolved the way the frontend resolves it —
  including its own home-relative default where that setting is genuinely unset, because on this arrangement the home
  behind it is read rather than assumed. A `dir` reached through symlinks reports its `physical_dir` beside it, as a
  save placement does. Where nothing was resolved the answer says which kind of nothing: no catalogue, an unread one, a
  sealed one whose readable layers declare no such system, a system declared without a path, a setting that is not an
  absolute path, a settings file that exists and could not be read, or a relocated config home (a Flatpak override on
  RetroDECK, a `portable.txt` next to the AppImage on EmuDeck). The extensions are the declaration verbatim — both cases
  where the file lists both, mistakes included — because which of them to act on is the frontend's business.
- The audit trail: `docs/research/coverage-matrix.md` (generated, with full source identity) tracks every referenced
  emulator's verdict and per-arrangement verification; `atlas/data/core_audit.json` enforces card maintenance by test;
  verification fails closed — drifted **and** unverifiable live versions raise an `unverified-version` caveat at answer
  time. The arrangement itself is held to the same standard: every answer from one no live installation has confirmed
  carries `arrangement-unverified` (today the two bare-RetroArch handles; RetroDECK and EmuDeck were verified against
  running installations), and one that has been confirmed says so when the machine moved past it —
  `arrangement-version-drifted` names both versions and points at `docs/re-verification.md`, so pinned knowledge cannot
  age in silence. The claim is about atlas's evidence, not about the machine — the config chain is source-verified
  everywhere — and the status is packaged data, so verifying an arrangement retires the caveat without touching a
  resolver.
- Firmware, in four calls over one live read — `firmware_for_core(core_so)`, `firmware_for_system(system)`,
  `firmware_inventory()`, `identify_firmware(md5=...)`. Every installed core's declarations come from
  `libretro_info_path` (sandbox paths translated to the Flatpak deployment, limited to cores whose `.so` is actually
  there) and each requirement states its **absolute destination** under the live `system_directory` whether or not a
  file is sitting there. Two axes stay apart: `need` is `required` / `optional`, `checked` is `verified` / `mismatch` /
  `unchecked` (identity known, not asked about) / `unknown` (cannot be established) / `not-comparable` (the bytes differ
  and the identity is an archive, whose hash pins a packaging rather than a content) — "we did not look" is never the
  same answer as "we looked and cannot tell", and neither is "we looked and it settles nothing". `requirements_met` is
  `true` only when every required file is there and atlas _established_ that it is the right one: a present file with
  the wrong bytes makes it `false`, and one that was never verified — the default — makes it `null`, so a green light is
  asked for rather than assumed. A core that is installed and declares nothing answers "needs nothing"; one whose
  `.info` cannot be read answers `declaration="unreadable"`, one that is not here answers `"absent"`, and a standalone
  emulator — installed, but outside the resolver's coverage — answers `"unsupported"` — the same empty list never means
  four things. `identify_firmware` runs the download flow off content: one md5 comes back with every name it is known as
  and every destination on this machine that wants it. Files nobody declares are listed separately and identified by
  bytes; save data the rule cards claim (Flycast's VMUs, PCSX2's memory cards) is excluded outright. Where a file's
  system had to be derived from what its whole core is called — the per-file table is derived and deliberately
  incomplete — that emulator's entry says so and names the files, and a core shipping no `systemname` at all is its own
  stated case. An empty answer distinguishes "this identifier is unknown here" from "nothing declares firmware for it";
  the two mean different things to a client.
- `installation.systems()` enumerates what the frontend catalogue declares,
  `installation.launchable(system, content_path)` answers whether a file would launch as that system's content and why
  not, and `installation.screenshot_location(...)` keeps screenshots out of every sync's file set — each with the same
  caveat grammar as the rest.
- `installation.systems_for_platform(vocabulary, value)` and `installation.platform_ids(system)` translate between the
  machine's catalogue and the public platform identities in both directions — the `<platform>` tags read live off the
  catalogue, the identity table packaged, versioned and regenerated from pinned upstreams.
- `atlas/contract.py` is the canonical JSON-shaped serialization of every answer — the same code the conformance run
  asserts with exact equality, available to consumers. The `emu-atlas` CLI speaks exactly these shapes on stdout, the
  conformance vectors are run through its dispatch too, and each release attaches the vectors, the wheel, and the
  self-contained bundle (CLI plus pinned CPython 3.14) for consumers without a Python.
- The conformance vectors (`vectors/`, schema 3) are whole fixture machines — files, directories, symlinks, AppImage
  contents, core answers, firmware blobs and read-failure states — each replayed against the canonical serialization and
  asserted with exact equality, alongside the unit suite on every push. Zero runtime dependencies.

What is not covered yet, and in which order it comes: `ROADMAP.md`. The systematic core-by-core state:
`docs/research/coverage-matrix.md`.
