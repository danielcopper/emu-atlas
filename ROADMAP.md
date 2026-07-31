# Roadmap

Where atlas goes from here, in order. Working docs: `docs/research/coverage-matrix.md` (generated — where to pick up
core work), `docs/tasks/save-detection.md` (itemized gaps). Finding IDs (`H*`/`M*`) below refer to the external review
of the resolver rebuild (PR #13; not tracked in the repo). The boundary rule and settled decisions live in `DESIGN.md`.

## Done

**Resolver rebuild** (PR #13): machine seam, override chain, per-flavor knowledge, rule cards (Flycast, LRPS2) with a
verification matrix and enforced maintenance, ES-DE catalogue with the full selection hierarchy, structured caveats,
generated coverage matrix, and the review's correctness batch (H1–H4, H6–H9, H11, M1, M9, M11, M13).

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

## Next: follow-up branches

Small branches from main, one concern each, per-branch PRs.

### 1. Core-by-core audit (continuous)

The grind toward "libretro complete" — queue in the coverage matrix (11/159 audited). Method pinned in
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

### 6. BIOS entry point

`bios_location()` on the emulator handle (DESIGN target sketch): compose the registry's world knowledge with the live
`system_directory`/`bios_path` resolution the save path already performs.

## Open research (needs the user's machine)

Paper Mario stage 2 (FlashRAM region), ParaLLEl N64 same-game comparison, per-game VMU filename scheme, one live run per
suspect core.
