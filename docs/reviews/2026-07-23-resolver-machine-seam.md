# Review: resolver machine seam

Reviewed commit: `58b69c0` (`refactor/resolver-machine-seam`)

Review date: 2026-07-23

Scope: static review of the resolver architecture, RetroArch behavior, installation detection, catalogue resolution,
rule cards, audit data, machine seam, placement model, tests, vectors, public API, packaging, and documentation.
Upstream behavioral claims were checked against the RetroArch revision pinned by the research
(`a79435a8d110d01eb2c89235cc41564281fe4cca`).

No tests or builds were run; the review was limited to static inspection. No critical finding was identified. The
high-severity findings can nevertheless produce a concrete but wrong save location and should be resolved before the
branch claims general RetroArch fidelity.

## High-severity findings

### H1. Missing or `default` save directories resolve to the wrong effective root

References: `atlas/retroarch_cfg.py:117-126`, `atlas/retroarch_cfg.py:172-182`, `atlas/placement.py:160-172`

When `savefile_directory` is absent, blank, or the literal `default`, atlas stores `None` and resolves saves to the
content directory. This conflates a missing configuration key with an empty effective runtime path.

RetroArch initializes platform-specific default directories before applying the configuration. On native Unix, for
example, `DEFAULT_DIR_SRAM` is initialized to the RetroArch config tree's `saves` directory. A literal `default` resets
the setting to that platform default. The content-directory fallback in `runloop.c` applies only when the effective
runtime save directory is still empty, not whenever a key is absent from one config file.

The same distinction applies to `RETRODECK_DEFAULTS` and `EMUDECK_DEFAULTS` in `atlas/retroarch_cfg.py:52-67`. Those
values describe keys present in the distributors' shipped config files. They are not necessarily the fallback RetroArch
uses after a user removes one of those keys from the live file. In that case the core RetroArch compile/runtime default
applies unless the distributor patched it.

Impact: native, Flatpak, partial, damaged, or hand-edited configurations can resolve to an entirely wrong directory or
sorting layout.

Recommended direction: model effective platform/runtime defaults separately from shipped reference configurations.
Initialize the runtime state first, then apply every present config value in RetroArch's order. Keep distributor
reference values for deviation reporting, not as automatic fallbacks for absent live keys.

Upstream references:

- <https://github.com/libretro/RetroArch/blob/a79435a8d110d01eb2c89235cc41564281fe4cca/frontend/drivers/platform_unix.c#L2133-L2134>
- <https://github.com/libretro/RetroArch/blob/a79435a8d110d01eb2c89235cc41564281fe4cca/configuration.c#L6916-L6932>

### H2. The config parser does not reproduce valid RetroArch config semantics

Reference: `atlas/retroarch_cfg.py:89-114`

`parse_cfg_text()` implements a useful subset of the syntax, but the resolver presents it as if it were reading the same
configuration RetroArch reads. Several valid inputs diverge:

- RetroArch accepts numeric boolean values such as `1`; atlas treats every value except case-insensitive `true` as
  false.
- RetroArch's parser uses the topmost duplicate entry; atlas overwrites entries while iterating, so the final duplicate
  wins.
- Inline/trailing comments are not interpreted like RetroArch comments.
- `#include` directives are ignored, including relative includes and recursively included settings.

These differences affect not only save sorting but also override controls, option-file controls, directories, and every
future key resolved through the same parser.

Impact: a valid real-world RetroArch configuration can silently produce a wrong root, wrong override chain, or wrong
core-option mode.

Recommended direction: port the relevant `config_file` grammar rather than expanding the current line parser one case at
a time. Parsing needs the originating path and `Machine` access so relative includes can be resolved with cycle/error
handling and provenance.

Upstream reference:

- <https://github.com/libretro/RetroArch/blob/a79435a8d110d01eb2c89235cc41564281fe4cca/libretro-common/file/config_file.c>

### H3. Override and core-option control settings are ignored

References: `atlas/installations.py:158-173`, `atlas/installations.py:257-282`, `atlas/installations.py:790-799`

Atlas currently reads the core/content/game override files whenever it can construct their paths. It also considers
game- and folder-specific `.opt` files whenever they exist. RetroArch gates those behaviors:

- `auto_overrides_enable = false` disables automatic `.cfg` overrides.
- `game_specific_options = false` disables game- and folder-specific `.opt` selection.
- `rgui_config_directory` changes the application config directory used for overrides and per-core options.

The resolver currently uses hard-coded `<retroarch-config>/config` directories and does not apply those gates.

Impact: atlas may apply files RetroArch ignores, or miss files RetroArch applies. Either case can change the save root,
sorting components, Flycast mode, LRPS2 mode, and provenance.

Recommended direction: resolve the effective application config directory and the controlling booleans before
enumerating either override chain. The decisions should be represented in provenance and vectors.

Upstream references:

- <https://github.com/libretro/RetroArch/blob/a79435a8d110d01eb2c89235cc41564281fe4cca/runloop.c#L1189-L1207>
- <https://github.com/libretro/RetroArch/blob/a79435a8d110d01eb2c89235cc41564281fe4cca/runloop.c#L5002-L5003>

### H4. Configured save roots are accepted where RetroArch would reject them

References: `atlas/retroarch_cfg.py:172-182`, `atlas/installations.py:284-291`

A non-empty `savefile_directory` becomes the effective atlas root without checking whether it is an existing directory.
RetroArch applies this config value only when `path_is_directory()` succeeds; otherwise it keeps the prior effective
runtime directory and logs a warning.

Trigger: `savefile_directory = "/mnt/missing/saves"`, a regular file at that path, an inaccessible mount, or a stale SD
card path.

Impact: this is the exact unmounted-storage case the health model is intended to expose, but atlas can currently return
the configured string as a concrete save location even when RetroArch will ignore it.

Recommended direction: add path-kind/status information to `Machine`, then apply RetroArch's validation while resolving
each final path value. Preserve the previous effective value when the configured path is invalid and attach structured
provenance/caveats for the rejected setting.

Upstream reference:

- <https://github.com/libretro/RetroArch/blob/a79435a8d110d01eb2c89235cc41564281fe4cca/configuration.c#L6916-L6932>

### H5. The sorted-directory fallback cannot be represented as one known directory

References: `atlas/machine.py:51-70`, `atlas/installations.py:478-500`, `DESIGN.md:101-114`,
`docs/research/retrodeck-save-placement.md:101-103`

When a calculated sorted directory is absent, atlas returns that directory and adds a `sorted-dir-missing` caveat.
RetroArch attempts to create the directory and silently falls back to the unsorted root if creation fails.

`Machine.exists()` cannot determine which branch will occur. It cannot distinguish a directory from a regular file,
represent permissions/ACLs, or establish whether a future `mkdir` will succeed. An absent directory is therefore not a
known effective sorted directory; it is a conditional result.

Impact: `SavePlacement.dir` can look authoritative while the actual write may go to the fallback root.

Recommended direction: represent intended and fallback directories structurally when creation success is unknown. If the
seam is extended to expose path kind and accessibility, it can narrow the condition, but it should not claim to predict
every future writeability outcome.

### H6. Content-directory mode incorrectly skips enabled sorting stages

Reference: `atlas/placement.py:160-188`

`build_save_placement()` treats `savefiles_in_content_dir` as a final directory decision. RetroArch uses it to select
the intermediate root, then still applies enabled content/core sorting stages.

Trigger: content `/roms/gba/game.gba`, `savefiles_in_content_dir = true`, and `sort_savefiles_by_content_enable = true`.

Actual atlas result: `/roms/gba`.

Expected RetroArch result: the content directory is selected as the root and the content component is appended, e.g.
`/roms/gba/gba` (followed by `library_name` as well when core sorting is enabled).

Impact: every configuration combining content-directory mode with either sorting flag resolves incorrectly.

Recommended direction: select the effective intermediate root first, then run one common sorting pipeline regardless of
how that root was selected.

Upstream reference:

- <https://github.com/libretro/RetroArch/blob/a79435a8d110d01eb2c89235cc41564281fe4cca/runloop.c#L8785-L8844>

### H7. Suspect and unaudited cores receive unqualified standard placements

References: `atlas/installations.py:294-301`, `atlas/oddities.py:97-105`, `docs/research/core-audit.md:32-35`,
`docs/research/coverage-matrix.md:10-12`

Rule cards are consulted when one exists, but a missing card currently means the standard RetroArch placement is
returned. The audit data distinguishes verified-standard, suspect, and unaudited cores. A missing card is not evidence
that the standard rule is complete.

Examples include known suspects such as Dolphin, PPSSPP, and Azahar, plus the large unaudited remainder. Dolphin is
already documented as potentially requiring a fourth root kind.

Impact: atlas can return a concrete, uncaveated standard placement for the cores most likely to have additional or
different save stacks. A backup consumer may consequently omit save data.

Recommended direction: make the audit verdict part of resolution. Only a verified-standard verdict should permit an
unqualified standard answer. Suspect and unaudited results should carry stable structured caveats or an explicitly
partial/unknown coverage state even when the standard RetroArch files are also resolvable.

### H8. An unqueryable card-carrying core can still receive a guessed concrete mode

References: `atlas/installations.py:228-246`, `atlas/installations.py:296-360`

Cards can match a `.so` basename even when `query_core()` fails. Without `library_name`, atlas cannot inspect the
effective per-core/game/folder option files whose path uses that name. It can nevertheless apply the card default or a
less-specific option source and return a concrete root.

Example: Flycast cannot be loaded on the host, while `config/Flycast/Flycast.opt` enables per-game VMUs. Atlas cannot
read the governing file but may still return shared VMUs under `system_directory/dc`.

Impact: the result's caveat admits an unchecked layer, but `dir` still states one of several root-changing modes as if
it were known. This violates the design's `never guess` rule.

Recommended direction: if an unchecked option layer can alter root or granularity, make that portion of the placement
unknown or conditional. Do not apply a less-specific/default mode merely to retain a concrete directory.

### H9. Core directories are not expanded or defaulted like RetroArch paths

References: `atlas/installations.py:688-703`, `atlas/installations.py:874-884`, `atlas/installations.py:936-949`

Configured `libretro_directory` values are joined directly. A native value such as `~/cores` remains literal, and an
absent key produces no path rather than the platform default. Native Unix RetroArch normally initializes a core
directory under its config tree.

Impact: core queries fail even though RetroArch can load the core. That cascades into unknown `library_name`, missed
override files, unresolved sort-by-core paths, and incorrectly applied or skipped rule cards.

Recommended direction: apply the same special-path expansion and platform defaults used for other effective runtime
paths. Keep sandbox-to-host translation as a separate installation-specific step.

Upstream reference:

- <https://github.com/libretro/RetroArch/blob/a79435a8d110d01eb2c89235cc41564281fe4cca/frontend/drivers/platform_unix.c#L1943-L1965>

### H10. Health reporting cannot expose the states promised by the design

References: `atlas/machine.py:51-70`, `atlas/detect.py:47-62`, `atlas/installations.py:624-632`,
`atlas/installations.py:677-683`, `atlas/installations.py:865-869`, `atlas/installations.py:932-934`, `DESIGN.md:61-63`

`read_text()` collapses missing, unreadable, invalid UTF-8, and general I/O failure into `None`. Detection then behaves
inconsistently:

- An unreadable RetroDECK or EmuDeck marker is not detected at all, so it cannot report `config_unreadable`.
- Bare RetroArch is detected through `exists()` but always reports `ok`, even if the config cannot be read.
- A syntactically valid empty RetroDECK object and malformed JSON both become an empty mapping and are classified the
  same way.
- RetroDECK health checks `rd_home_path`, not necessarily the effective save root that may point to an unmounted device.
- EmuDeck health checks its saves root but not the claimed standalone RetroArch config.
- A stale EmuDeck settings file suppresses the bare Flatpak handle even when the Flatpak config is absent.

Impact: present-but-broken arrangements disappear or appear healthy, including the missing-mount failure the health
model was specifically introduced to surface.

Recommended direction: separate marker existence, read status, parse status, required companion state, and effective
root status. The machine operation needs an explicit result such as `value | missing | unreadable | invalid-text`, and
health should be a structured value rather than one lossy string.

### H11. Per-game ES-DE selections collide when content basenames repeat

References: `atlas/esde.py:142-148`, `atlas/installations.py:520-529`

`gamelist.xml` selections are indexed by basename. Two entries such as `./USA/Game.iso` and `./Japan/Game.iso` collapse
to the same `Game.iso` key; whichever is parsed last controls both games.

Impact: atlas can select the wrong emulator/core for one game and then correctly resolve the wrong emulator's save
placement.

Recommended direction: preserve and normalize the full gamelist-relative path. Match it against the content path
relative to that system's ROM root, with explicit handling for case and separator semantics.

## Medium-severity findings

### M1. Invalid stored core-option values select the wrong fallback behavior

Reference: `atlas/installations.py:350-370`

An option value unknown to a rule card currently abandons the card and falls back to the standard save rule. RetroArch's
option manager validates persisted values against the live option definition and retains the core-declared default when
the stored value is invalid. For Flycast, that default is shared VMUs under `system_directory/dc`, not the standard save
directory.

Recommended direction: validate against live option definitions where feasible. If live definitions remain outside the
seam, apply the versioned card default for an invalid persisted value and attach an invalid-value caveat; do not switch
to an unrelated standard root.

### M2. `observed` file sets can contain non-saves and omit real saves

References: `atlas/installations.py:434-477`, `atlas/data/core_oddities.json:19-28`,
`docs/tasks/save-detection.md:59-60`

Generic observation accepts every `<rom_stem>.*` match. This can include RetroArch companion files such as `.ldci`,
same-stem content companions, or unrelated files. It cannot find valid non-stem/shared files such as a second shared
memory card. Flycast's declared/observed list checks A1-D1 even though the card provenance notes that A2-D2 can exist
when slot 2 is configured as a VMU.

Glob metacharacters in common ROM names, especially `[` and `]`, are also interpreted rather than matched literally.

Impact: a consumer can copy unrelated files while omitting actual save data. The name `observed` reads as complete even
though the operation is only a heuristic snapshot.

Recommended direction: make observation literal and conservative, filter source-verified companion files, model
non-stem/shared files per audited core, and add a partial-observation state when completeness is not established.

### M3. Rule-card verification fails open when the live version is unavailable

References: `atlas/installations.py:301-337`, `atlas/data/core_oddities.json`, `atlas/data/core_audit.json`

Version comparison warns on a known mismatch, but not when an expected pinned arrangement/core version exists and the
live version is missing. A card can therefore be applied without an `unverified-version` caveat even though its required
verification input was unavailable.

The card and audit formats also lack a machine-readable schema version. Some provenance is version-pinned in prose but
not represented in the public result.

Recommended direction: model verification explicitly as `verified`, `drifted`, `runtime-version-unknown`, or
`never-verified`. Missing live evidence must not equal successful verification. Version the packaged schemas and expose
the relevant procedure/card version in portable output.

### M4. Installation handles mix cached and live machine state

References: `atlas/installations.py:642-645`, `atlas/installations.py:688-703`, `atlas/installations.py:841-844`,
`atlas/__init__.py:22-32`

`RetroDeck` and `EmuDeck` accept marker text separately from `Machine`, parse it once, and cache it. Other inputs such
as RetroArch config and gamelists are read during each later query. Public callers can construct a handle whose cached
marker text does not match the injected machine, and a live config change after detection is reflected only in some
parts of the answer.

Within one query, the same RetroArch config may be read once for core-path resolution and again for placement. A
concurrent edit can combine state from two revisions.

Recommended direction: choose one consistency model. Either use fully live handles that read all governing state through
`Machine` for each answer, or produce explicit immutable snapshots. Within one query, read each source once and derive
all decisions from that snapshot.

### M5. The vectors assert only part of the portable contract

References: `tests/test_machine_vectors.py:43-78`, `scripts/validate_vectors.py:111-164`,
`vectors/machines/named-cases.json:565-588`, `DESIGN.md:139-143`

Vectors assert installation `kind`, `root`, and `health`, plus selected placement fields. Most do not constrain
unexpected caveats. They omit placement sources, `FileSet.source`, granularity provenance, `options_file`, alternatives,
installation `kinds`, audit/verification state, and capability differences.

Placement queries always target `installs[0]`, so coexistence vectors do not prove the
no-cross-installation-fall-through rule for secondary handles. Catalogue and placement are also exercised separately
rather than through the natural `EmulatorEntry.save_location()` route.

The breaking-change checker keys vectors by canonical input and can overwrite duplicate inputs; validation checks
duplicate names only within one file (`scripts/check_vector_breaking_change.py:54-59`,
`scripts/validate_vectors.py:195-203`).

Recommended direction: define a versioned JSON Schema and one canonical serialization of every stable result field.
Assert exact equality. Mark human prose as non-contractual, make structured caveats/provenance contractual, add an
installation selector, and reject duplicate canonical inputs globally.

### M6. `FixtureMachine` is not a faithful whole-machine model

References: `atlas/machine.py:142-203`, `scripts/validate_vectors.py:61-88`, `vectors/machines/named-cases.json:591-608`

The fixture cannot represent empty directories, file-versus-directory kind, unreadable files, invalid text, permissions,
inaccessible paths, or distinct core-probe failures. Empty directories are simulated with `.keep` files.

`FixtureMachine.glob()` applies `fnmatch` to a flat set of paths. Its `*` can match `/`, unlike normal filesystem glob
components, and symlinked matches need not preserve the link-prefixed paths returned by a real filesystem.

Recommended direction: encode explicit fixture nodes and operation outcomes. Specify path/glob semantics normatively and
add parity tests that execute the same cases against `FixtureMachine` and temporary real filesystem trees.

### M7. Dead save symlinks and physical targets are not represented in placements

References: `atlas/machine.py:51-70`, `atlas/installations.py:384-420`, `atlas/placement.py:117-135`, `DESIGN.md:86-88`

`readlink()` was introduced because emulator-visible and physical target paths are different truthful answers, and dead
links are important health states. The resolver does not currently use `readlink()`, while `SavePlacement` carries only
one directory.

Example: an LRPS2 memory-card link into an unmounted save volume can produce a declared placement without a dead-target
caveat.

Recommended direction: represent logical path, resolved backing path, and link status when they are decision-relevant.
Add live, relative, chained, and dead-link vectors that require this distinction.

### M8. The common installation/emulator API advertised by the design does not exist

References: `atlas/installations.py:532-592`, `atlas/installations.py:705-776`, `atlas/installations.py:989`,
`DESIGN.md:42-52`

`Installation` is a union rather than a common protocol. Only `RetroDeck` implements the current catalogue methods.
`EmulatorEntry` is coupled to `RetroDeck`, and standalone entries fail from `save_location()` with
`NotImplementedError`. The design's `bios_location()` and `save_granularity` emulator members do not exist in that form.

Recommended direction: define a minimal installation identity/health protocol and explicit capability protocols or typed
result variants. An unsupported placement should be a domain outcome, not an ordinary entry that throws at runtime.

### M9. Catalogue caveats are lost when an entry resolves a placement

References: `atlas/installations.py:572-615`, `atlas/installations.py:765-776`

`EmulatorEntry.caveats` can state that per-game overrides exist or that catalogue selection is uncertain.
`EmulatorEntry.save_location()` does not merge those caveats into the returned placement except for one newly computed
content-specific mismatch.

Impact: the natural entry-to-placement API returns an answer that appears more certain than the entry from which it was
derived.

Recommended direction: propagate every decision-relevant catalogue caveat into the placement, or return a composite
resolution object that retains catalogue selection and placement as one answer.

### M10. Public value objects do not enforce their stated invariants

References: `atlas/placement.py:54-135`, `atlas/esde.py:109-121`, `atlas/oddities.py:36-46`,
`atlas/oddities.py:117-124`, `atlas/bios.py:25-57`

Closed states and codes are plain strings, so invalid combinations such as an unknown `FileSet` containing files or an
unrecognized root kind can be constructed. Several frozen dataclasses contain mutable dictionaries, so they are not
deeply immutable. Data loaders coerce malformed values; for example, `bool("false")` is true in Python.

Recommended direction: use enums or `Literal` types for closed vocabularies, immutable mappings, validated constructors,
and strict schema validation at packaged-data boundaries.

### M11. Failed core probes can remain stale for the `RealMachine` lifetime

References: `atlas/machine.py:73-114`, `tests/test_machine.py:88-111`

Core-query results, including `None`, are cached by `(path, mtime, size)`. Installing a previously missing dependency
can make the same unchanged core loadable, but the cached failure remains. Tests do not cover successful probing,
crashes, timeouts, cache hits, or invalidation.

Recommended direction: do not cache failures, or give failure caching an explicit short lifetime and richer identity.
Test every probe outcome and invalidation path.

### M12. README and task documentation describe an obsolete implementation

References: `README.md:19-34`, `README.md:39-54`, `README.md:81-104`, `DESIGN.md:37-53`, `DESIGN.md:179-185`,
`docs/tasks/save-detection.md:7-45`, `atlas/esde.py:16-20`

The README still documents `save_placement(system, core=...)`, `Reader`, 16 vectors, no EmuDeck, and no override chain.
The current code exposes `save_location(content_path=..., core_so=...)`, `Machine`, EmuDeck, catalogue resolution, rule
cards, structured caveats, and 25 vector cases.

The task document leaves completed Flycast, LRPS2, and per-game catalogue work under present-tense missing/wrong
headings. `atlas.esde` mentions an unimplemented `es_settings.xml` source while the research and code use
`gamelist.xml`.

Recommended direction: clearly separate current public API, settled target design, and open work. Make README examples
executable tests. Update task status in place instead of retaining completed work as current defects.

### M13. The top-level namespace and typing distribution are inconsistent

References: `atlas/__init__.py:18-125`, `pyproject.toml:17-21`

Several imported public-looking names, including catalogue helpers, oddity types/functions, and `Granularity`, are
absent from `__all__`. The package exposes extensive annotations but does not ship a `py.typed` marker.

Recommended direction: decide and test the supported top-level API, align imports and `__all__`, and ship `py.typed` if
annotations are part of the consumer contract.

### M14. Distribution metadata and CI do not verify the released contract

References: `pyproject.toml:5-21`, `.release-please-manifest.json`, `CHANGELOG.md:3-8`, `release-please-config.json`,
`.github/workflows/ci.yml:34-50`

The package installs as version `0.0.0` while the release manifest and changelog say `0.1.0`. CI uses one Python version
despite declaring Python 3.11+, performs an editable install, and does not build/install wheel and sdist artifacts.

Consequently CI does not catch wrong package versions, missing resources, missing vector artifacts, typing markers, or
installed-package import failures.

Recommended direction: establish one version source, make release automation update it, test Python 3.11 and 3.12, build
wheel/sdist, install them in clean environments, load every packaged resource, and assert metadata/public imports.

### M15. Portable artifacts and generated data are not fully reproducible

References: `pyproject.toml:17-21`, `atlas/data/README.md:39-65`, `scripts/generate_bios_registry.py:421-428`,
`scripts/generate_coverage_matrix.py:28-34`, `scripts/generate_coverage_matrix.py:86-111`,
`docs/research/retrodeck-save-placement.md:423-430`

Vectors are described as the portable contract but are not included in the Python distribution or published as a
separate artifact. BIOS generation instructions use fresh unpinned checkouts; generated metadata omits exact commit
identities and dirty state. Coverage generation reads the invoking machine's Flatpak deployment without recording its
complete identity. Some research extraction scripts live only outside the repository.

Recommended direction: publish vectors as a versioned artifact, record exact upstream SHAs and dirty state, include a
generator/schema version, and make every generated document carry enough source identity for exact reproduction.

## Validated non-findings

The following areas were checked and should not be treated as defects without new evidence:

- The cross-file override order is correctly global, core, content directory, then game.
- RetroArch's compile default for `sort_savefiles_enable` is `true`.
- The first existing `.opt` file is the governing source; a missing key in that file falls back to the core default
  rather than falling through to another file. The current high-level precedence implements that rule.
- Core probing is process-isolated and uses `retro_get_system_info`; `.info` metadata is not substituted for
  `library_name`.
- Flycast's `reicast_per_content_vmus` option key is correct.
- The revised LRPS2 rule is consistent with the audited `14d19f8` generation: `pcsx2_shared_memory_cards` defaults to
  enabled, while disabled mode uses a per-content `.ps2` card and disables slot 2. Its provenance should cite the full
  audited commit rather than an unpinned branch name.
- EmuDeck claiming the configured standalone RetroArch Flatpak as one handle is a coherent representation of identity
  overlap, provided stale-marker and companion-health cases are addressed.

## Architectural strengths

- The move from a lookup library to a live resolver is the right direction for the researched domain.
- Machine access is centralized instead of leaking direct filesystem reads through the resolver.
- Core probing is process-isolated, cached, and based on the value RetroArch actually uses.
- Installation coexistence and no cross-installation fall-through are explicit design decisions.
- `SavePlacement` distinguishes root kind, holes, caveats, granularity, and observed/declared/unknown file knowledge.
- Rule cards are an appropriate mechanism for source-cited behavior that is not represented in ordinary configuration.
- The audit and generated coverage matrix make uncertainty visible and provide a systematic route through the core
  matrix.
- ES-DE choice, system selection, and per-game `altemulator` form a useful hierarchy rather than inventing a default.
- The research records source locations, evidence levels, live observations, and open questions unusually well.
- The vector infrastructure, shape validator, and breaking-change gate are a strong base; the remaining issue is the
  breadth and exactness of the contract, not the absence of one.
- The package still honors the zero-runtime-dependency goal.

## Open questions and assumptions

- Is the public guarantee intended to cover only the explicitly audited RetroDECK/RetroArch versions, or every detected
  native/Flatpak RetroArch arrangement? The implementation and wording currently imply the latter, while much of the
  evidence is arrangement/version-specific.
- Should atlas report an intended write location, the effective location RetroArch would select at query time, or a
  conditional set when future directory creation determines the outcome? These are different contracts.
- Is `observed` intended to mean "matching files currently seen" or "complete save file set"? Consumers need a stable
  distinction before using it for backup deletion or synchronization.
- Should installation handles represent live views or immutable query snapshots? The current mixture makes provenance
  difficult to reason about under concurrent changes.
- Are structured caveats and provenance part of the cross-language contract? The design treats explainability as a core
  feature, but vectors currently leave much of it outside the portable contract.

## Residual risks

- The coverage matrix reports only a small audited subset of the full libretro and standalone-emulator matrix. Unknown
  behavior remains the normal case, not an edge case.
- Savestates, subsystem content, most standalone emulators, and several known nonstandard cores remain unresolved.
- Host-side probing cannot load every core whose dependencies exist only inside its Flatpak sandbox.
- Process isolation protects the caller from a crashing core, but `query_core()` still executes native code from the
  machine without a security sandbox.
- Without query-snapshot semantics, concurrent config and filesystem changes can produce internally mixed answers even
  after individual resolver rules are corrected.

## Verification status

This was a static review. The repository's tests, vector validator, type checker, package build, and formatter were not
run as part of the review. Runtime behavior, wheel contents, and type-check status therefore remain unverified by this
document.
