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
- _Packaging honesty_ (M14, M15): one version source (pyproject, release-please python type), CI on 3.11+3.12,
  wheel/sdist built and verified from a clean install, vectors in the sdist and attached to releases, generated docs
  with full source identity.

**One answer grammar** (item 17, in progress): health findings are caveats — `{code, data}` everywhere, no envelope —
and every answer from a broken installation states them. On the firmware route, a standalone emulator answers
`declaration="unsupported"` with the placement route's own `standalone-unsupported`, and an absent `system_directory`
resolves to RetroArch's platform default instead of refusing, leaving `system-directory-cleared` to mean only a key set
to nothing. Still open in the item: `identify_firmware`'s untyped refusal, the naming sweep, the summary-field
convention.

## Next: follow-up branches

Small branches from main, one concern each, per-branch PRs.

### 1. Core-by-core audit (continuous)

The grind toward "libretro complete" — queue and current audited count in the coverage matrix. Method pinned in
`docs/research/core-audit.md`. Next: cores whose options scan shows save-related keys; the suspect trio
(dolphin/azahar/ppsspp-libretro) needs one live run each (user). Multi-option cores (Beetle family, ReARMed,
SwanStation) need the code-rule-plus-card route for file-set/granularity precision.

### 2. Remaining resolver gaps (docs/tasks/save-detection.md)

`#include` in cfg parsing (H2 remainder, needs Machine access in the parser), option validation against live definitions
(M1 remainder), savestates, subsystem content, platform default core dirs (H9 remainder), playlists as the
bare-RetroArch catalogue, override enumeration without a core, deviation warnings against shipped reference configs.

### 3. Card variants via feature detection

**Foundation done:** `query_core` captures registered option definitions; card applicability is decided on the
observable key (registered → confirmed, version drift demoted to provenance; missing → card retired with
`card-generation-mismatch`); registered defaults and value sets are live reads. Remaining: per-generation card
_variants_ keyed by their option signature — added when an old generation actually gets audited — and distinct
probe-failure reporting. Design in `docs/research/core-audit.md`.

### 4. Standalone emulators (the second big block)

The `saves/<system>/<emulator>/` family: per-emulator config parsers and placement rules, target list derived from
`es_systems.xml` (22 runners, 0 audited). DuckStation first (PerGameTitle naming → `<save_id>` hole), then PCSX2,
Dolphin, melonDS, PPSSPP-SA. Replaces the `Unresolved` standalone outcome route by route.

### 5. EmuDeck reality

Everything EmuDeck is vector-tested only, never validated against a real installation (issue #11): detection markers in
the wild, its own emulator set (coverage-matrix `?` cells), frontend variants (ES-DE elsewhere / Pegasus / SRM),
companion-health semantics beyond the config-missing case.

### 6. Firmware follow-ups

The four firmware entry points ship: live `.info` declarations from the installed cores, stated against the live
`system_directory`, with the packaged identity table doing only what it can. What is left:

- **Non-comparable identities.** 21 of the 388 packaged identities are archives or data packs (MAME-style romset zips,
  `scummvm.zip`, `ecwolf.pk3`), whose whole-file hash changes with romset version and merge mode. A `mismatch` there may
  be structurally meaningless — and `neogeo.zip` is one of the mandatory files this work exists to surface. The fix
  belongs in the table (a per-entry statement of what kind of identity it is, with provenance), not in a file-extension
  heuristic; only then can `checked` grow a fifth value that means something.
- **The system vocabulary.** `firmware_for_system` speaks ES-DE's system name where a catalogue exists and an atlas slug
  where none does, and says which via a caveat. The canonical translation table is the real fix.
- **Per-file system assignment.** `FIRMWARE_SYSTEM_OVERRIDE` is `[D]` and deliberately incomplete: it is atlas's own
  reading, cross-read against RomM's `known_bios_files.json`, and the two disagree (the Super Game Boy dumps are `snes`
  here, `super-gb` there). Where a declaration falls back on a multi-system core the answer states it — 33 of the 96
  declaring cores on a real RetroDECK, plus 2 cores that ship no `systemname` at all. Growing the table by hand is a
  race lost to every core release; a real fix needs a per-file source of truth, and none exists upstream today (`.info`
  has one `systemname` per core, `System.dat` keys by name without a system). Two known limits of the signal: a core
  with a `systemname` and no `database` has only one source and is taken at its word, and the "two sources disagree"
  reading needs both names mapped, which the largely-unmapped database vocabulary often prevents (vice_x128 says `C128`
  while its database says `Commodore - 64`, and atlas cannot compare them).
- **The unclaimed bucket has substructure.** It currently mixes genuine alternative BIOS revisions with core runtime
  data (blueMSX machine ROMs, PPSSPP assets, Dolphin `Sys`). Save artifacts are already excluded via the rule cards;
  runtime data needs the same treatment, and `docs/tasks/save-detection.md` task 1 draws exactly that line on the save
  side.
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
