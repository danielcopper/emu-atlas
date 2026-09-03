# Roadmap

Where atlas goes from here, in order. Working docs: `docs/research/coverage-matrix.md` (generated — where to pick up
core work), `docs/tasks/save-detection.md` (itemized gaps). Finding IDs (`H*`/`M*`) below refer to the external review
of the resolver rebuild (PR #13; not tracked in the repo). The boundary rule and settled decisions live in `DESIGN.md`.

## Done

**Resolver rebuild** (PR #13): machine seam, override chain, per-flavor knowledge, rule cards (Flycast, LRPS2) with a
verification matrix and enforced maintenance, ES-DE catalogue with the full selection hierarchy, structured caveats,
generated coverage matrix, and the review's correctness batch (H1–H4, H6–H9, H11, M1, M9, M11, M13).

**Firmware at the boundary rule**: the packaged BIOS registry fused declarations (on the machine) with identities (not
on the machine) and drifted in both directions — it missed three mandatory files RetroDECK's own cores declare and
carried 181 paths for cores it does not ship. Declarations are now a live `.info` read limited to installed cores;
identities stay a packaged, versioned table; and `is_required() -> bool` is gone, because a bool cannot say "I don't
know" and collapsed it into "not required" → "nothing missing" → green.

**Non-comparable firmware identities** (#337): a packaged identity now states its `kind`, and the 24 archives among the
388 — MAME romset sets, core-bundled data packs, the FreeJ2ME program jars — state why their bytes move (`romset` or
`core-bundled`). `checked` has a fifth value: a difference from an archive's pinned bytes answers `not-comparable` with
a caveat and no verdict, never `mismatch`, while an exact hit still verifies. The list is curated in
`scripts/generate_firmware_hashes.py` with its own version and reviewed date, carried in the data file, and never
guessed from a file extension.

**Structural cleanup** (this branch) — everything the review flagged about the existing structure:

- _Seam status model_ (H5, H10, M6, M7): explicit operation outcomes (`ReadResult`, `PathKind`), structured health as
  issue caveats with detection-on-existence, conditional placements with a structural `fallback_dir` (a file in the way
  makes the fallback the answer), `readlink` consumed — `physical_dir` and dead-link caveats — and a faithful
  whole-machine fixture (empty dirs, unreadable/invalid files, inaccessible paths, segment-wise glob) with
  fixture-vs-real parity tests.
- _Vector contract_ (M5, M6): schema-versioned vector files, canonical contract serializations (`atlas/contract.py`,
  public) asserted with exact equality over every stable field, installation selectors (no-fall-through proven on
  secondary handles), entry-route vectors incl. the `Unresolved` outcome, global duplicate-input rejection.
- _Observation honesty_ (M2): literal glob-escaped matching, source-cited companion filter (`.ldci`), explicit
  completeness claims (`FileSet.complete`, card-only), card observation lists beyond declared defaults (Flycast slot-2
  VMUs).
- _Fail-closed verification_ (M3): four verification states (verified / drifted / runtime-version-unknown /
  never-verified) — missing live evidence is never success; schema-versioned, strictly validated packaged data.
- _Consistency model_ (M4): live handles; one read per governing source per query.
- _API shape_ (M8, M10): `Installation` protocol instead of a union, standalone entries as typed `Unresolved` domain
  outcomes, Literal vocabularies with validated constructors, deeply immutable value objects, no boolean coercion at
  data boundaries.
- _Packaging honesty_ (M14, M15): one version source (pyproject, release-please python type), CI on 3.11/3.12/3.14 (the
  matrix covers both sides of the zstd capability), wheel and sdist built and verified from a clean install, generated
  docs with full source identity.

**One answer grammar** (item 17, in progress): health findings are caveats — `{code, data}` everywhere, no envelope —
and every answer from a broken installation states them. On the firmware route, a standalone emulator answers
`declaration="unsupported"` with the placement route's own `standalone-unsupported`, and an absent `system_directory`
resolves to RetroArch's platform default instead of refusing, leaving `system-directory-cleared` to mean only a key set
to nothing. Still open in the item: `identify_firmware`'s untyped refusal, the naming sweep, the summary-field
convention.

**The consumable surface** (#196, #68, #65, #202, #108): the `emu-atlas` CLI — one question per invocation, the contract
JSON on stdout, the conformance vectors run through its dispatch; the platform crosswalk (`systems_for_platform` /
`platform_ids`, live `<platform>` tags against a pinned identity table); the AppImage reader (`atlas.squashfs`, pure
stdlib) opening the catalogue sealed inside ES-DE's AppImage wherever the runtime has the zstd codec (3.14's
`compression.zstd`, or `backports.zstd` a host vendors); the release artifacts — vectors, wheel, and the self-contained
bundle (CLI + pinned CPython 3.14) for consumers without a Python; and the weekly canary deploying the newest RetroDECK
from Flathub against the full suite, so drift announces itself.

## Next: follow-up branches

Small branches from main, one concern each, per-branch PRs.

### 1. Core-by-core audit (continuous)

The grind toward "libretro complete" — queue and current audited count in the coverage matrix. Method pinned in
`docs/research/core-audit.md`. Next: cores whose options scan shows save-related keys; the suspect trio
(dolphin/azahar/ppsspp-libretro) needs one live run each (user). Multi-option cores (Beetle family, ReARMed,
SwanStation) need the code-rule-plus-card route for file-set/granularity precision.

### 2. Remaining resolver gaps (docs/tasks/save-detection.md)

`#include` in cfg parsing (H2 remainder, needs Machine access in the parser), option validation against live definitions
(M1 remainder), subsystem content, platform default core dirs (H9 remainder), playlists as the bare-RetroArch catalogue,
override enumeration without a core, deviation warnings against shipped reference configs.

### 3. Card variants via feature detection

**Foundation done:** `query_core` captures registered option definitions; card applicability is decided on the
observable key (registered → confirmed, version drift demoted to provenance; missing → card retired with
`core-generation-mismatch`); registered defaults and value sets are live reads. Remaining: per-generation card
_variants_ keyed by their option signature — added when an old generation actually gets audited — and distinct
probe-failure reporting. Design in `docs/research/core-audit.md`.

### 4. Standalone emulators (the second big block — issue #3)

Per-emulator config parsers and placement rules, one sub-issue per emulator, target list derived from RetroDECK's own
`es_systems.xml`. Carded and shipped: Dolphin (GC slots, Wii NAND), PPSSPP, xemu, Cemu — including the per-title MLC
unit and its keys.txt BIOS expectation as a `packaged` firmware declaration (#207, #208) — Azahar (the 3DS virtual SD's
per-title unit, the second half of the rommapp/romm#3866 demand, #213), DuckStation (two slots, six modes, #215), PCSX2
(slots whose card type is read off the disk, #217) and melonDS — one `.sav` per game with the TOML/legacy-INI read and
the first flatpak-variant answer on EmuDeck (#219), plus its BIOS expectations as the first `config_files` firmware card
(#220: the paths are configuration values, and which of them a launch probes is `verifySetup`'s own live decision), and
RPCS3 (#231: the save tree the emulated PS3's own VFS names, one directory per title id below every user home that
exists — read through the YAML scalar reader of #229, which is what unblocked it), and Vita3K (#233: the `ux0` tree
below `pref-path`, plus the extracted-binary launch variant EmuDeck installs it as), and DuckStation's BIOS expectations
(#236: the first `search` firmware card — the emulator names no file, so the answer is a directory, the emulator's own
size filter, and its recognition table packaged from source, with the pick stated only where a content check was asked
for) and its cheat files as the mod row of the same emulator — the first tree stated as a configuration key rather than
an XDG subpath, because the root it hangs off is the one the launch environment picks. Each sub-issue replaces the
`Unresolved` standalone outcome for its emulator, save placement + BIOS expectations + config sources, on RetroDECK and
EmuDeck both. Candidates next: Vita3K's texture directory and its firmware, RPCS3's own BIOS slice (`dev_flash`), PPSSPP
deepening.

### 5. EmuDeck reality

The arrangement was verified against a live installation at a pinned backend revision (issue #11, closed) and its
answers carry that evidence. All four standalone questions now go through one variant gate (#226): which binary the
launch runs decides which XDG bases its trees hang off, so the texture and mod routes answer where the save and firmware
routes do and refuse with the variant named where nothing is established. What remains: its own emulator set
(coverage-matrix `?` cells), frontend variants (ES-DE elsewhere / Pegasus / SRM), companion-health semantics beyond the
config-missing case, and the two variants that establish nothing yet — the Windows build under Proton, and a flatpak
`emulator_settings.json` names no app id for (#288 moved that id out of the save card and onto the emulator's own row,
so the gate no longer needs the emulator to have a save card; what is left is the six EmuDeck installs as AppImages or
unpacked binaries, whose flatpak ids nobody has established).

### 6. Firmware follow-ups

The four firmware entry points ship: live `.info` declarations from the installed cores, stated against the live
`system_directory`, with the packaged identity table doing only what it can. What is left:

- **An archive's contents are still uncompared.** The whole-file question is settled (see Done), but comparing what is
  _inside_ a romset or a data pack, member by member, is not in reach: `System.dat` carries one whole-file md5 per name
  and no member list, so nothing packaged says which ROMs a correct `neogeo.zip` holds. Atlas would have to open the
  archive and own a second source of truth for its contents. Until such a source exists, "present, and its packaging
  differs" is the strongest true statement about these files.
- **What lies inside a declared folder is answered by size and by table, not by header.** A present folder declaration
  is judged the way the core judges it — `FIRMWARE_DECLARED_DIRECTORY` version 2 states LRPS2's size filter and the
  `pcsx2/bios/` identity prefix — so a recognised image inside satisfies it, a folder with nothing of an accepted size
  fails it, and the rest stays `None` with the reason stated. What atlas does not do is read the ROMDIR header `IsBIOS`
  validates by (`pcsx2/ps2/BiosTools.cpp:62-106` at 14d19f8): the packaged identities are a subset of what the core
  accepts, so an image the table does not list is stated as unidentified rather than judged, and closing that gap needs
  a binary read at the machine seam, which does not exist. The `pcsx2_bios` core option — set, `LoadBIOS` opens the
  named file rather than the first the listing found — is a separate question (#360), and so is every other core's
  folder declaration: the table grows one core at a time and only from source.
- **Per-file system assignment.** `FIRMWARE_SYSTEM_OVERRIDE` is `[D]` and deliberately incomplete: it is atlas's own
  reading, cross-read against RomM's `known_bios_files.json`, and the two disagree (the Super Game Boy dumps are `snes`
  here, `super-gb` there). Where a declaration falls back on a multi-system core the answer states it. The vocabulary
  itself is settled (item 20b shipped: the map's values are ES-DE ids, `DESIGN.md` Vocabulary); what remains is evidence
  per file, and the standing list is `docs/tasks/firmware-system-evidence.md` — 173 inherited declarations across 42
  uncertain cores at the current snapshot. Growing the table by hand is a race lost to every core release; a real fix
  needs a per-file source of truth, and none exists upstream today (`.info` has one `systemname` per core, `System.dat`
  keys by name without a system). Two known limits of the signal: a core with a `systemname` and no `database` has only
  one source and is taken at its word, and the "two sources disagree" reading needs both names mapped, which the
  largely-unmapped database vocabulary often prevents (vice_x128's database says `Commodore - 64`, a name the systemname
  map does not carry, so atlas cannot compare the pair).
- **The unclaimed bucket has substructure.** It currently mixes genuine alternative BIOS revisions with core runtime
  data (blueMSX machine ROMs, PPSSPP assets, Dolphin `Sys`). Save artifacts are already excluded via the rule cards;
  runtime data needs the same treatment, and `docs/tasks/save-detection.md` task 1 draws exactly that line on the save
  side. The requirement side of the same substructure shipped with #346: a requirement whose file is byte-identical to
  the copy the distribution's own prepare step places states `supplied_by`, off `atlas/data/distribution_supplied.json`.
  Every name in that list is one an `UnclaimedFile` can carry too — `prboom.wad` and `capsimg.so` are unclaimed on the
  reference machine, because no installed core's `.info` declares them — so the same equality is what this bullet needs
  on the unclaimed side.
- **The copy list covers one component's prepare step, and two more write into the firmware root.** The entries in
  `distribution_supplied.json` are the RetroArch component's, and each is a `cp` whose source stays on disk to be
  hashed. Two other steps place files there and each is blocked on something different. PPSSPP's own component extracts
  `ppsspp_foss_bios.tar.gz` into `$bios_path/PPSSPP` (`components/ppsspp/component_prepare.sh:37`,
  `--strip-components=1 assets/`) — the same destination the RetroArch entry already covers, so what lands there has two
  origins and only one of them leaves a file to compare against; this one wants the archive-member comparison the
  unclaimed half above needs anyway. xemu copies `xbox_hdd.qcow2` to the firmware root when it is absent
  (`components/xemu/component_prepare.sh:32`, guarded by the `-f` test on `:31`) from `$component_extras`, its own
  `components/xemu/rd_extras` (`:6`) — and that one is a plain `cp` whose source is shipped, so an entry would state
  `supplied_by` today. What stops it is the route, not the file: `xbox_hdd.qcow2` is a standalone card's requirement
  (`standalone_firmware.json`, `XEMU`, `sys.files/hdd_path`), and the provenance check runs on the declaration route
  alone, so nothing would ask. Its source root is also per-component rather than the one `source_root` a card states.
- **A repeated key states nothing.** `.info` files go through RetroArch's parser, where the first of a repeated key wins
  and the later line sets nothing — silently, on both sides. One shipped `.info` on the reference machine does this
  (`FreeIntvTSOverlay`, a `firmware1_path` typed as a second `firmware0_path`), so the file names a file the answer
  never mentions. Stating it means carrying duplicates out of `parse_cfg`, which is the same plumbing a dropped line in
  an `.info` would need.
- **TOCTOU between resolving and reading.** The root bound is checked on resolved paths, but the read that follows is a
  second syscall against the same name, so a path swapped in between is not covered. Closing it fully needs
  `openat2(RESOLVE_BENEATH)` — a syscall the seam does not expose today — so what exists is a bound, not a sandbox.
- **`cores_read=False` prose overstates.** When the core enumeration comes back empty, the caveat text says the cores
  "could not be enumerated", which is the safe reading but not always what happened — an installation genuinely shipping
  no cores gets the same sentence. Caveat text is explicitly non-contractual (`atlas/contract.py`), so this is wording,
  not behavior.
- **The emulator-handle route.** A per-entry `firmware_for_core()` on `EmulatorEntry`, so the catalogue answer and the
  firmware answer share a subject without the caller passing a `core_so` back in.
- **Standalone emulators declare nothing.** An emulator without a libretro core ships no `.info`; the catalogue route
  lists it and states it as unresolvable. Part of block 4.
- **`systemfiles_in_content_dir` on the firmware route.** RetroArch's own firmware check moves to the content's
  directory when the flag is set and content is loaded ([V] `menu/menu_displaylist.c:854-878`). A firmware answer has no
  content, so atlas answers for the flagless case and does not state the difference yet.

## Open research (needs the user's machine)

Paper Mario stage 2 (FlashRAM region), ParaLLEl N64 same-game comparison, one live run per suspect core. The per-game
VMU filename scheme is settled (2026-08-05 live run, `docs/research/retrodeck-save-placement.md` §8), including the
ROM-named branch for content without a disc id, which the answer now states as a condition. Still open there: a live
Naomi run, because which ports a per-content mode covers is content-dependent — and a card model that can express a mode
split across roots, which is what `file-set-spans-roots` currently reports instead of a file set.
