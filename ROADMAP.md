# Roadmap

Where atlas goes from here, in order. Working docs: `docs/research/coverage-matrix.md` (generated — where to pick up
core work), `docs/tasks/save-detection.md` (itemized gaps). Finding IDs (`H*`/`M*`) below refer to the external review
of the resolver rebuild (PR #13; not tracked in the repo). The boundary rule and settled decisions live in `DESIGN.md`.

## Done (this branch)

Resolver architecture (machine seam, override chain, per-flavor knowledge), rule cards (Flycast, LRPS2) with
verification matrix and enforced maintenance, ES-DE catalogue with the full selection hierarchy (per-game > per-system >
declared), structured caveats (16 codes), generated coverage matrix, and the review's correctness batch:
platform-default save roots (H1), RetroArch-faithful cfg parsing — first duplicate wins, `1` as true, comments (H2),
override/option gates `auto_overrides_enable` / `game_specific_options` / `rgui_config_directory` (H3), rejected
save-root validation (H4), sorting after content-root selection (H6), audit-verdict caveats for suspect/unaudited cores
(H7), narrowed unconfirmed-mode handling (H8), `~`-expansion for core dirs (H9), path-aware per-game selection matching
(H11), core-default fallback on invalid option values (M1), catalogue-caveat propagation (M9), no failure caching in
core probes (M11), `__all__`/`py.typed` (M13).

## Next: merge, then follow-up branches

Small branches from main, one concern each, per-branch PRs.

### 1. Seam status model (REVIEW H5, H10, M6, M7)

The one coherent rework the review's structural findings point at: machine operations need explicit outcomes
(`value | missing | unreadable | invalid`), path-kind (file vs directory), and the resolver must consume `readlink`
(logical vs physical path, dead-link caveats). Health becomes structured; conditional placements (intended dir +
fallback) become representable. This unblocks the exact H4/H5 cases `exists()` cannot distinguish today.

### 2. Vector contract breadth (REVIEW M5, M6)

Versioned JSON schema for vectors; exact-equality assertions over all stable fields (sources structure, granularity,
caveat data); installation selector so coexistence vectors prove no-fall-through on secondary handles; entry-route
(`EmulatorEntry.save_location`) vectors; fixture/real-filesystem parity tests; global duplicate-input rejection.

### 3. Core-by-core audit (continuous)

The grind toward "libretro complete" — queue in the coverage matrix (11/159 audited). Method pinned in
`docs/research/core-audit.md`. Next: cores whose options scan shows save-related keys; the suspect trio
(dolphin/azahar/ppsspp-libretro) needs one live run each (user). Multi-option cores (Beetle family, ReARMed,
SwanStation) need the code-rule-plus-card route for file-set/granularity precision.

### 4. Remaining resolver gaps (docs/tasks/save-detection.md)

`#include` in cfg parsing (H2 remainder, needs Machine access in the parser), observed-file-set semantics + companion
filters (M2), option validation against live definitions (M1 remainder), fail-closed verification when live versions are
unreadable (M3), snapshot-vs-live consistency decision (M4), installation/emulator protocol instead of the union +
standalone entries as domain outcomes (M8), value-object invariants via enums/validation (M10), savestates, subsystem
content, platform default core dirs (H9 remainder), playlists as the bare-RetroArch catalogue.

### 5. Card variants via feature detection

Multi-generation cards dispatched on observable facts (which option keys the core registers), enabled by extending
`query_core` to capture option definitions from `retro_set_environment` — also turns option defaults into live reads.
One vector per generation, kept forever. Design in `docs/research/core-audit.md`.

### 6. Standalone emulators (the second big block)

The `saves/<system>/<emulator>/` family: per-emulator config parsers and placement rules, target list derived from
`es_systems.xml` (22 runners, 0 audited). DuckStation first (PerGameTitle naming → `<save_id>` hole), then PCSX2,
Dolphin, melonDS, PPSSPP-SA.

### 7. EmuDeck reality

Everything EmuDeck is vector-tested only, never validated against a real installation (issue #11): detection markers in
the wild, its own emulator set (coverage-matrix `?` cells), frontend variants (ES-DE elsewhere / Pegasus / SRM),
companion-health semantics.

### 8. Packaging and release honesty (REVIEW M14, M15)

One version source (pyproject currently 0.0.0 vs manifest 0.1.0), CI on 3.11+3.12, wheel/sdist build + clean-install
import tests, vectors published as a versioned artifact, generated data carrying full source identity.

## Open research (needs the user's machine)

Paper Mario stage 2 (FlashRAM region), ParaLLEl N64 same-game comparison, per-game VMU filename scheme, one live run per
suspect core.
