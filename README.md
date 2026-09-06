<div align="center">

<img src="assets/compass-rose-animated.gif" alt="emu-atlas" width="180">

# emu-atlas

<h3>Where your emulators actually keep everything — read off the running machine, not off a list</h3>

[How to use](docs/how-to-use.md) · [Design](DESIGN.md) · [Architecture](docs/architecture.md) ·
[Contract reference](docs/contract-reference.md)

[Roadmap](ROADMAP.md) · [Coverage matrix](docs/research/coverage-matrix.md) ·
[Core audit method](docs/research/core-audit.md) · [Re-verification](docs/re-verification.md)

<a href="docs/how-to-use.md"><img alt="How to use" src="https://img.shields.io/badge/how%20to%20use-read-649a83?style=for-the-badge&labelColor=16382c"></a>
<a href="https://github.com/danielcopper/emu-atlas/releases/latest"><img alt="Release" src="https://img.shields.io/github/v/release/danielcopper/emu-atlas?style=for-the-badge&label=release&color=649a83&labelColor=16382c"></a>
<a href="https://github.com/danielcopper/emu-atlas/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/danielcopper/emu-atlas?style=for-the-badge&color=649a83&labelColor=16382c"></a>
<a href="pyproject.toml"><img alt="Requires Python 3.11 or newer" src="https://img.shields.io/badge/Python-%E2%89%A5%203.11-649a83?style=for-the-badge&labelColor=16382c"></a>
<a href="pyproject.toml"><img alt="Zero runtime dependencies" src="https://img.shields.io/badge/runtime%20dependencies-zero-649a83?style=for-the-badge&labelColor=16382c"></a>

</div>

A Python library that answers, for any emulator installation on a machine, which config files govern it, how they
override each other, and where saves, savestates, firmware, texture packs, mods, screenshots and ROMs actually live. It
also answers what the installed frontend declares: its systems, the emulators that launch them, whether a file would
launch, and how its systems meet the public platform ids (IGDB, libretro, ScreenScraper, TheGamesDB).

_A resolver, not a lookup — every answer is read off the running machine the way the emulator reads it._

> **Pre-1.0 (v0.x).** The resolver core is built and verified live, but coverage is not complete: the per-core audit and
> the standalone emulators are worked through one at a time, and [the coverage matrix](docs/research/coverage-matrix.md)
> carries the current count per question. [`ROADMAP.md`](ROADMAP.md) says what comes next and in which order. Signatures
> are not frozen.

---

> [!NOTE]
> **How this is built.** The code is written by an AI coding agent working under my direction. The architecture, the
> design decisions and the review are mine, and resolver changes are verified live against real installations before
> they ship.

## Why

Every tool that touches emulator data re-learns the same facts: a save-sync client needs the save directory, a backup
tool needs it too, a BIOS manager needs the firmware folder and which files belong in it. Today that knowledge lives as
prose in wikis, as static path lists that go stale, and as private code inside each frontend and client.

Static lists are the trap. RetroArch's actual save layout depends on live config values (`savefiles_in_content_dir`,
`sort_savefiles_by_content_enable`, `sort_savefiles_enable`), on the active core, and on which of several install
flavors is present. A path list is wrong the moment a user flips a setting. The truth lives in the configs — so the
library reads them the way the emulator does: probe order, override chains, defaults.

## Design

Four principles, fixed before any code.

- **Installations are handles.** `detect(home)` finds what is present — RetroDECK, EmuDeck, bare RetroArch installs, any
  of them side by side — and every question is asked _of an installation_, never of a global "the system". Every
  question in the placement, catalogue and firmware families rides on every handle — the ones without a frontend
  catalogue answer with the reason rather than an empty list. Choosing one of them is optional:
  `every_installation(home)` asks them all and labels each answer with the handle it came from, because two arrangements
  on one machine give two true answers and picking a winner would be the guess.
- **All machine access goes through an injected seam.** The library never touches the machine directly. It asks a narrow
  machine protocol (`read_text`, `glob`, `path_kind`, `readlink`, `query_core`, `file_size`, `file_digest`,
  `read_appimage_text`, `read_ps2_bios_header`) whose every operation reports an explicit outcome — missing is not
  unreadable is not invalid text — because the emulators make those distinctions and health reporting depends on them.
  In production the seam is the real machine; in tests and conformance vectors it is a fixture machine, so detection,
  config parsing and override chains are all provable from data, failure states included.
- **Placements are templates, not paths.** Where a concrete path cannot be known from configs alone, the answer carries
  named holes: `<content_dir>` when the layout keys on the ROM's own folder and no ROM was named, `<library_name>` when
  the core would not load, `<save_id>` where the emulator keys the save off a serial or title id it reads from the ROM
  itself — in file names (Flycast's per-game VMUs) and in directory segments alike (Cemu's per-title MLC subtree),
  because a file set and a tree are templates too. Whoever can fill a hole fills it;
  [sigil](https://github.com/rommforge/argosy-sigil) is one supplier of `save_id`, not a dependency.
- **Every answer carries provenance.** Which config file said so, which default applied. Debugging a user's broken setup
  is the daily reality of every consumer; explainability is a feature, not a log line.

atlas depends on nothing and nothing depends on it: sigil identifies, atlas locates,
[gavel](https://github.com/danielcopper/romm-gavel) decides — three independent libraries a client composes. The map of
the surface — layers, handles, answer types, and what to import from where — is
[`docs/architecture.md`](docs/architecture.md).

## What atlas answers

Every question below is asked of a handle, and every answer carries its caveats: stable codes with machine-readable
data, never free text alone and never silence.

- **Detection** — `detect(home)` finds RetroDECK, EmuDeck, the bare `org.libretro.RetroArch` Flatpak and a native
  install, each a handle behind one `Installation` protocol. Health is structured (unreadable or invalid markers,
  missing roots, a stale EmuDeck whose claimed RetroArch config is gone), markers are ordered, and a winner is never
  silently chosen. Handles are live: every query re-reads its governing sources, each exactly once.
- **Every installation at once** — `every_installation(home)` puts the protocol's questions to every detected
  installation and answers each labelled with the handle that produced it, in detection order. Fan-out only: it merges
  nothing, prefers nothing, and resolves nothing a handle does not resolve. A machine with none answers with nothing,
  which is a result and not an error.
- **Saves** — `savefile_location(content_path=..., core_so=...)` resolves the save directory the way RetroArch does:
  platform-default roots, the four-layer override chain (gated by `auto_overrides_enable` / `game_specific_options` /
  `rgui_config_directory`), `library_name` read live from the core binary through the Flatpak-deployment translation,
  file sets observed literally (glob-escaped, RetroArch's `.ldci` bookkeeping filtered) or honestly unknown, and
  granularity plus the option that switches it where a rule card exists (Flycast, LRPS2). Where granularity depends on
  options atlas does not interpret, the answer says so and names those options, so "unstated" never arrives looking like
  "nothing to report". A sorted directory that does not exist yet is a conditional answer with a structural
  `fallback_dir`; a placement reached through symlinks reports its `physical_dir`; a dead `dir_prep` link is a stated
  caveat, not a silent path.
- **Savestates** — `savestate_location(...)` answers the same question through RetroArch's savestate quartet of keys and
  the very same chain, because one upstream function places both families. Its `SavestatePlacement` carries no
  `granularity` — no core writes a savestate, so no rule card for one can exist — and in exchange it names the files:
  `<stem>.state`, the numbered slots, the auto slot and their thumbnails are RetroArch's own naming. A core whose
  `.info` declares no savestate support is stated as a caveat rather than left to be discovered.
- **Texture packs** — `texture_pack_location(...)` joins a root read off the machine (the system directory as the core
  receives it, or the save root as it stands) to the fragment below it, which is per-core behaviour no config states and
  is therefore packaged, versioned and source-cited. Beside the directory it states two things a client cannot derive:
  whether replacement is switched on right now — read from the options file RetroArch would read first, else the default
  the installed core registers, and honestly `None` where neither answered — and how the tree below the root is keyed
  per game, stated only where a citation backs it. Where nothing establishes an emulator's texture wiring the answer is
  a typed refusal, never a directory nobody read; where the wiring is known but the root has never been observed in use,
  the directory is stated with `emulator-read-unestablished` beside it. Standalone emulators answer this question
  through the catalogue entry even where their saves refuse, and the asymmetry is the point: a save routes through a
  config atlas would have to model, while a standalone emulator's packs mostly sit at its own default below an XDG base
  a flatpak pins. Those answers leave the switch unstated and name the emulator configuration that would settle it,
  rather than reading an unread file as "off".
- **Mods** — `mod_location(...)` runs the same join and the same grammar, with one difference: the answer is **plural**.
  An emulator may read mods from several directories that are different mechanisms rather than alternatives — FBNeo
  takes a replacement romset, an IPS patch set and a romdata file from three trees under one switch — so it answers a
  list of trees, each with its own directory, keying and the role that tells it apart; where an emulator has one tree
  the list has one entry and no role. Where the switch is a setting in a configuration atlas does not read, the answer
  names that file — including on a core row, for a core whose switch is an ini inside the user tree it builds — and
  where no switch has been established at all it points nowhere rather than at a file that may govern nothing.
- **Soft patching** — `soft_patch_candidates(content_path, ...)` answers the patch files RetroArch itself applies before
  any core sees the content: the four candidate paths in the order the frontend tries them, each with the nine indexed
  continuations that chain onto it, composed from the content path by upstream arithmetic rather than looked up. Beside
  them it states whether this core's content is loaded into memory at all, which is what decides whether patching runs,
  and whether this RetroArch build was established to attempt each format — which no running machine states, so it is
  packaged per arrangement and pinned to the build it was read at. Nothing is written to disk: the patch is applied to
  the buffer the core is handed.
- **The frontend catalogue** — `emulators_for(system, content_path=...)` answers which emulators can launch a system. On
  RetroDECK it reads the ES-DE catalogue live (bundled plus custom overlay) and resolves the effective default through
  the full hierarchy: per-game `altemulator` > per-system `alternativeEmulator` > declared order. On EmuDeck it reads
  the same hierarchy from its ES-DE's on-disk layers, and the `es_systems.xml` sealed inside the ES-DE AppImage is read
  too, through atlas's own pure-stdlib squashfs reader, wherever the runtime has the image's codec (`compression.zstd`
  arrives with Python 3.14; the published `backports.zstd` grants it to older interpreters). Where the codec is absent
  the sealed layer stays exactly that, stated with `emulator-catalogue-sealed` — the capability is the runtime's, never
  blamed on the machine. Entries carry their core, so placement answers on that path need no core argument; a standalone
  entry answers with a typed `Unresolved` outcome instead of raising. Where there are no entries the answer says which
  kind of none: a bare RetroArch ships no catalogue at all, an EmuDeck arrangement with no ES-DE on disk may have one
  atlas has not established the location of, a catalogue atlas could not read — missing, unreadable, or empty — is not
  an empty one, and a sealed catalogue's readable layers may simply not declare the system; four codes, because a client
  must not read the last three as "nothing here".
- **ROMs** — `rom_location(system)` answers where that system's ROMs live and which file extensions the frontend will
  launch, both off the same `<system>` declaration, so neither has to be recomputed from a table that cannot follow a
  user who moved their library. The directory is the declared `<path>` with `%ROMPATH%` substituted from the setting the
  frontend itself substitutes it from, resolved the way the frontend resolves it, including its own home-relative
  default where that setting is genuinely unset. A `dir` reached through symlinks reports its `physical_dir`. Where
  nothing was resolved the answer says which kind of nothing: no catalogue, an unread one, a sealed one whose readable
  layers declare no such system, a system declared without a path, a setting that is not an absolute path, a settings
  file that exists and could not be read, or a relocated config home (a Flatpak override on RetroDECK, a `portable.txt`
  next to the AppImage on EmuDeck). The extensions are the declaration verbatim, both cases where the file lists both
  and mistakes included, because which of them to act on is the frontend's business.
- **Firmware** — four calls over one live read: `firmware_for_core(core_so)`, `firmware_for_system(system)`,
  `firmware_inventory()`, `identify_firmware(md5=...)`. Every installed core's declarations come from
  `libretro_info_path` (sandbox paths translated to the Flatpak deployment, limited to cores whose `.so` is actually
  there) and each requirement states its **absolute destination** under the live `system_directory` whether or not a
  file is sitting there. Two axes stay apart: `need` is `required` / `optional`, `checked` is `verified` / `mismatch` /
  `unchecked` (identity known, not asked about) / `unknown` (cannot be established) / `not-comparable` (the bytes differ
  and the identity is an archive, whose hash pins a packaging rather than a content) — "we did not look" is never the
  same answer as "we looked and cannot tell", and neither is "we looked and it settles nothing". `requirements_met` is
  `true` only when every required file is there and atlas _established_ it is the right one: wrong bytes make it
  `false`, and never verified — the default — makes it `null`, so a green light is asked for rather than assumed. A core
  that declares nothing answers "needs nothing"; one whose `.info` cannot be read answers `declaration="unreadable"`,
  one that is not here `"absent"`, and a standalone emulator outside the resolver's coverage `"unsupported"` — the same
  empty list never means four things. `identify_firmware` runs the download flow off content: one md5 comes back with
  every name it is known as and every destination on this machine that wants it. Files nobody declares are listed
  separately and identified by bytes; save data the rule cards claim (Flycast's VMUs, PCSX2's memory cards) is excluded
  outright. Where a file's system had to be derived from what its whole core is called — the per-file table is derived
  and deliberately incomplete — that emulator's entry says so and names the files, and a core shipping no `systemname`
  at all is its own stated case. An empty answer distinguishes "this identifier is unknown here" from "nothing declares
  firmware for it"; the two mean different things to a client.
- **Systems, launchability, screenshots** — `systems()` enumerates what the frontend catalogue declares,
  `launchable(system, content_path)` answers whether a file would launch as that system's content and why not, and
  `screenshot_location(...)` keeps screenshots out of every sync's file set.
- **The platform crosswalk** — `systems_for_platform(vocabulary, value)` and `platform_ids(system)` translate between
  the machine's catalogue and the public platform identities in both directions: the `<platform>` tags read live off the
  catalogue, the identity table packaged, versioned and regenerated from pinned upstreams.

Answers are frozen value objects. [`atlas/contract.py`](atlas/contract.py) is their canonical JSON-shaped serialization
— the same code the conformance run asserts with exact equality, and the same bytes the CLI writes.
[`docs/contract-reference.md`](docs/contract-reference.md) is the generated per-question lookup table: every field, its
nullability, what it means, and which caveat code rides which question with which data keys.

## What lives here

1. **RetroArch knowledge** — interpretation of `retroarch.cfg` (the three save-layout keys and their override
   semantics), the save-directory math (`sort_by_content` / `sort_by_core`), core `.info` parsing, and the probe
   locations per install flavor (flatpak, native, RetroDECK, EmuDeck) as data.
2. **Firmware** — split at the boundary rule. _Which_ files a core wants is read live off the machine, from the `.info`
   files RetroArch ships next to its cores, so it can never drift against the cores an installation actually has. _What
   a correct file's bytes are_ — the `md5` / `sha1` / `size` triple — is world knowledge and ships as a packaged,
   versioned, source-cited table of 388 identities; its generator and data provenance
   ([`scripts/generate_firmware_hashes.py`](scripts/generate_firmware_hashes.py),
   [`atlas/data/README.md`](atlas/data/README.md)) live with the data.
3. **ES-DE knowledge** — `es_systems.xml` / `es_find_rules.xml` parsing and launch-command classification, generalized
   across the frontends that ship ES-DE (RetroDECK, EmuDeck, and a bare ES-DE install).
4. **Standalone emulators** — per-emulator config parsing and save placement rules, read the way each emulator reads its
   own configuration: Dolphin (GC slots and the Wii NAND), PPSSPP, xemu, Cemu (the per-title MLC unit), Azahar (the
   per-title unit on the 3DS's emulated SD), DuckStation (two memory-card slots, six modes), PCSX2 (slots whose card
   type is read off the disk, file or folder), melonDS (one `.sav` per game, beside the ROM by default), RPCS3 (the save
   tree the emulated PS3's own VFS names) and Vita3K (the `ux0` tree below its preference path) today — one emulator per
   sub-issue until every emulator RetroDECK ships resolves, on EmuDeck too, whichever way its catalogue launches them:
   AppImage, flatpak, or a binary unpacked out of one.
5. **The system vocabulary and the platform crosswalk** — the ids every question about a system takes are ES-DE's system
   names, shipped as packaged data cited to a stated build and guarded by a test that parses that build's own
   `es_systems.xml`; `known_systems()` and `from_esde_system()` let a client check its own map before using it. Beside
   them, the **platform** crosswalk answers how a catalogue's `<platform>` tags — read live — meet the public platform
   identities (IGDB by numeric id, libretro names, and the ScreenScraper/TheGamesDB ids ES-DE's own scrapers use):
   `systems_for_platform(vocabulary, value)` and `platform_ids(system)`. The identity table behind the platform
   crosswalk is world knowledge under the boundary rule — versioned, source-cited, regenerated from pinned upstreams —
   while which tags a system carries is always the machine's own statement.

The boundary rule that decides every "table or live?" question, and the reasoning behind all of it, is
[`DESIGN.md`](DESIGN.md):

> What is on the running machine is read — always. What is written nowhere on the machine is world knowledge, and world
> knowledge is marked, versioned, and source-cited.

## What does not live here

- **ROM identification** — which game a file is, and the platform-native id atlas leaves as the `<save_id>` hole in a
  declared file name rather than opening a ROM to read it: sigil's territory.
- **Sync decisions** — what to do when local and server disagree: gavel's territory.
- **File transfer, UI, per-client policy** — the client's territory.

## Using it

Zero runtime dependencies is a contract (`dependencies = []` in [`pyproject.toml`](pyproject.toml)), which makes
vendoring a directory copy. Four routes, in rising order of how little of your runtime you control:

1. **Vendor the package.** Take `atlas/` out of the release wheel or the repository at a release tag, drop it under a
   parent package of your own, and `from _vendor import atlas`. No module imports the package by its own name and no
   packaged-data read anchors on the literal `atlas`, so the copy answers under any name — a tested property, not a
   convention.
2. **Install the wheel** attached to each release, and `import atlas`.
3. **Run the CLI.** `emu-atlas` (or `python -m atlas`) takes one question per invocation and writes its contract JSON to
   stdout — the same bytes the library serializes, held there by the conformance vectors, which are run through the CLI
   too. Any language runs a subprocess and parses.
4. **Take the bundle.** `emu-atlas-<tag>-x86_64-linux.tar.gz` is the CLI with its own pinned CPython 3.14, for consumers
   that own no interpreter. The full suite runs against every built bundle before it is attached; unpack anywhere, run
   `./emu-atlas`, nothing installs itself elsewhere.

```python
import atlas

installations = atlas.detect(home="/home/deck")   # 1. find what is installed
inst = installations[0]                           # 2. choose a handle — or ask all of them
health = inst.health()                            # 3. check health before trusting answers
answer = inst.savefile_location(core_so="mgba_libretro.so", content_path=rom_path)   # 4. ask the handle
for caveat in answer.caveats:                     # 5. read the caveats — always
    handle_or_log(caveat.code, caveat.data)
```

```bash
emu-atlas detect
emu-atlas savefile-location --core mgba_libretro.so --content /roms/gba/Game.gba
emu-atlas emulators-for gba
emu-atlas firmware-for-core --core mgba_libretro.so --verify
```

Every question, every argument, the chained flows a save-sync client actually runs, and the boundaries of what atlas
will tell you: [`docs/how-to-use.md`](docs/how-to-use.md).

## Where this plugs in

Kinds of consumers, not commitments:

- **Save-sync and library clients**, in any language: per-installation paths and launch invocations, save placements
  with their granularity and holes, and BIOS state — the facts every such client currently re-derives and carries
  itself.
- **Backup tooling**: what to back up and what to leave (settings, screenshots, caches) is exactly the file-set and role
  vocabulary the answers speak; exporting detected installations into an established manifest format needs no library
  adoption at all.
- **The distributions themselves**: the arrangements maintain this knowledge as shell scripts today; a shared,
  conformance-tested base is the same offer gavel makes for sync decisions.

## Verification, and how drift announces itself

The resolver core is verified live against real installations — RetroDECK 0.10.9b and an EmuDeck at a pinned backend
revision — and a weekly canary deploys the newest RetroDECK from Flathub and runs the full suite against it, re-reading
the evidence off that deploy: the shipped binaries' own literals, and every component script the packaged data cites, at
the line it cites.

Verification fails closed. The claim is about atlas's evidence, not about the machine — the config chain is
source-verified everywhere:

- [`docs/research/coverage-matrix.md`](docs/research/coverage-matrix.md) (generated, with full source identity) tracks
  every referenced emulator's verdict and per-arrangement verification;
  [`atlas/data/core_audit.json`](atlas/data/core_audit.json) enforces card maintenance by test.
- A core version that has drifted **and** cannot be verified live raises an `unverified-version` caveat at answer time.
- Every answer from an arrangement no live installation has confirmed carries `arrangement-unverified` — today the two
  bare-RetroArch handles.
- A confirmed arrangement that the machine has moved past raises `arrangement-version-drifted`, which names both
  versions and points at [`docs/re-verification.md`](docs/re-verification.md), so pinned knowledge cannot age in
  silence. The status is packaged data, so verifying an arrangement retires the caveat without touching a resolver.

The conformance vectors (`vectors/`, schema 3) are whole fixture machines — files, directories, symlinks, AppImage
contents, core answers, firmware blobs and read-failure states — each replayed against the canonical serialization and
asserted with exact equality, alongside the unit suite on every push. Each release attaches the vectors, the wheel, the
bundle and a `SHA256SUMS` manifest.

## Contributing

```bash
mise run setup      # editable install + dev deps into the local venv
mise run test       # pytest (unit + vector runner)
mise run validate   # vector shape validation
basedpyright atlas tests scripts
deno fmt --check
```

User-facing behavior changes need vectors, and vectors for old generations of an emulator's behavior are never deleted.
[`CLAUDE.md`](CLAUDE.md) carries the ground rules — never guess, evidence levels are part of the work, upstream
citations by `file:line` — and [`docs/research/core-audit.md`](docs/research/core-audit.md) pins the method for a rule
card.

[![CI](https://github.com/danielcopper/emu-atlas/actions/workflows/ci.yml/badge.svg)](https://github.com/danielcopper/emu-atlas/actions/workflows/ci.yml)
[![Canary](https://github.com/danielcopper/emu-atlas/actions/workflows/canary.yml/badge.svg)](https://github.com/danielcopper/emu-atlas/actions/workflows/canary.yml)
[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=danielcopper_emu-atlas&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=danielcopper_emu-atlas)
[![Coverage](https://img.shields.io/sonar/coverage/danielcopper_emu-atlas?server=https%3A%2F%2Fsonarcloud.io)](https://sonarcloud.io/summary/new_code?id=danielcopper_emu-atlas)
[![Maintainability](https://sonarcloud.io/api/project_badges/measure?project=danielcopper_emu-atlas&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=danielcopper_emu-atlas)
[![Reliability](https://sonarcloud.io/api/project_badges/measure?project=danielcopper_emu-atlas&metric=reliability_rating)](https://sonarcloud.io/summary/new_code?id=danielcopper_emu-atlas)
[![Security](https://sonarcloud.io/api/project_badges/measure?project=danielcopper_emu-atlas&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=danielcopper_emu-atlas)

## License

MIT. This is an independent project and is not affiliated with, endorsed by, or sponsored by RetroDECK, EmuDeck, the
RetroArch project, or any of the emulators it reads.
