# Packaged data

## `core_oddities.json` — the rule cards

One card per libretro core whose save behaviour deviates from RetroArch's standard rule. A card says _which live config
governs the core_ and what each of its values means; the value itself is always read off the machine. The audit verdict
behind every card lives in `core_audit.json`, and a test fails if a card has no entry there.

**A card states only what no read of the machine recovers, and what it states is machine-checked wherever a machine can
check it.** Three consequences run through the format:

- The card key is the core's canonical short name — the `.so` basename without `_libretro.so` — and the `.so` name is
  **derived** from it rather than restated. A second spelling could only ever be a way for the two to disagree about
  which core a card describes, so the loader refuses a card that still carries `identifiers.so`. What the key does not
  give is the display name the binary reports (`Flycast`, `LRPS2`); `identifiers.library_name` carries that, and lookup
  works from either side.
- `governing_option.default` is recorded **only for a core that registers none**, and a test fails the redundant copy
  rather than keeping it correct by hand. Where a core registers its options during `retro_set_environment`,
  `query_core` reads the default off the shipped binary; a card's copy would be a second, ageing one. LRPS2 registers
  later than that, which is why its card keeps a default and says so in its provenance. The consequence is stated, not
  papered over: on a machine where such a core's registration cannot be read **and** no options file names the value,
  nothing establishes which mode is in force, so the card steps aside and the answer carries
  `core-option-value-unestablished`.
- Every file name and subdir fragment a card records is pinned to the binary it was read from — see _Anchors_ below.

A mode — one value of the governing option — states the root it anchors at, an optional `subdir`, its `granularity`, and
the files the save consists of. Three of those fields are closed vocabularies — all three the placement's own, imported
by the loader rather than respelled here, so a card cannot select a value the contract cannot carry — and one is a type
rule:

| field         | accepted values                                                   |
| ------------- | ----------------------------------------------------------------- |
| `root`        | `savefile_directory`, `system_directory`, `content_directory`     |
| `also_under`  | the same three, and not the mode's own `root`                     |
| `granularity` | `shared-card`, `per-game-file`, `per-game-files`                  |
| `complete`    | a JSON boolean, `true` or `false` — never a string, never coerced |

`granularity` reaches the caller as the contractual `Granularity.value`, so a misspelling would be stated as this
machine's actual grouping; `complete` is a claim about the save, and `bool("false")` is `True` in Python, so a quoted
boolean fails the load instead of silently asserting completeness.

```json
"All VMUs": {
  "root": "savefile_directory",
  "subdir": null,
  "files": ["<save_id>.A1.bin", "<save_id>.B1.bin"],
  "files_without_save_id": ["<rom_stem>.A1.bin", "<rom_stem>.B1.bin"],
  "granularity": "per-game-files"
}
```

### File names are templates in the placement's own hole vocabulary

A declared name may carry exactly two tokens, and they are not local to this file: they are the holes
`SavefilePlacement.needs` speaks (`atlas/placement.py`, which the loader imports — one definition, not a second spelling
here).

- `<rom_stem>` — the resolver fills it from the content path. Not a hole in an answer: either it is substituted or the
  file set is honestly unknown.
- `<save_id>` — the content's platform-native id (Flycast names a per-game VMU after the disc's product number). atlas
  never fills it, because reading an id out of a ROM is identification, not location. It stays in the stated name and
  `save_id` joins `needs`, so a caller sees a template rather than a resolved-looking name.

The loader rejects any other token in a declared name, and the check is **subtractive**: it removes the known templates
and refuses whatever still contains `<` or `>`. Scanning for well-formed `<…>` would pass `<rom_stem.A1.bin` (bracket
never closed) and `<<rom_stem>>.A1.bin` (nested) — both of which atlas would then state verbatim as a filename. That is
the point of keeping one vocabulary: a card is data, and without the check a typo travels silently into a name atlas
states as fact — the failure mode the "never guess" rule exists to prevent. A card that needs a new hole adds it to the
placement vocabulary first. An empty list, an empty name and a literal angle bracket are refused for the same reason.

### Two fields for what one file list cannot say

- `files_without_save_id` — the same set as the emulator names it when the content carries **no** id. Flycast's
  `getVmuPath` takes the id branch only for console content with a readable disc header and falls back to the ROM's name
  otherwise, so the set is conditional on a fact atlas does not read. The resolver states the id-keyed set and hands
  this one to the caller in `filenames-content-conditional`, filled as far as it can fill it. Only meaningful next to a
  `<save_id>` set, and it may not name an id itself; the loader enforces both.
- `files_established_for` + `files_citation` — the class of content the list was established for, and the source that
  says so. Not every difference between content classes is a spelling: Flycast connects four VMUs on a Dreamcast and two
  on a Naomi board, so for arcade content two of the four declared names can never exist. Both travel into the same
  caveat as data, so a client can tell "this list is scoped" from "this list is universal" without reading prose. The
  scope needs a declared `files` to scope, and the citation needs a scope to cite; the loader enforces both.
- `also_under` — a second root this mode's save data lives under (Flycast's `VMU A1` moves one controller's VMU and
  leaves the rest on the shared card). A card states one root per mode, so such a mode declares **no** `files` at all —
  the loader refuses both together — and the answer carries `file-set-spans-roots` instead of offering the visible part
  as the whole save. What the schema would need to state it properly is task 16 in `docs/tasks/save-detection.md`.

`complete` is the explicit claim that the mode's candidate universe is closed. A template can in principle carry it —
the hole is in the name, not in the membership — but only source-verified provenance earns it.

### Anchors — every recorded name, pinned to the string it was read from

A card names files and subdirectories that reach a caller as fact, and `saves.anchors` records where each of those names
came from. One entry per recorded name — the governing option key, every segment of a `subdir`, and every name in
`files`, `observe` and `files_without_save_id` — carrying exactly one of three kinds:

| kind          | means                                                                                |
| ------------- | ------------------------------------------------------------------------------------ |
| `literal`     | the whole NUL-delimited byte string the auditor read in the shipped `.so`            |
| `unprotected` | no literal spells this name — the core composes it at run time, and the text says so |
| `arrangement` | the name is not the core's word at all: the arrangement builds that path             |

```json
"anchors": {
  "vmu_save_A1.bin": { "literal": "vmu_save_" },
  "dc_nvmem.bin": { "unprotected": "composed at run time from the platform prefix and the flash file's own name — …" }
}
```

A test re-reads every `literal` in the deployed core and fails when it is gone, so a build that renames `vmu_save_` or
`Mcd%03u.ps2` is caught instead of leaving the card describing names the core no longer writes. The check is
whole-string containment between NUL bytes, not a substring scan: flycast really does carry the literal `/dc`, and it is
the texture-dump path, nothing to do with saves.

**A recorded name with none of the three kinds fails the tests** — there is no silent opt-out, because a name that looks
checked and is not is worse than one marked as what it is. What the tripwire cannot catch is the _grammar_ around a
literal (that `%s.ps2` still names the save and not something else) and the names no literal carries; those stay
`unprotected`, and each one's reason states what does stand behind it — live observation and the next re-audit, or
source the byte check simply cannot reach (Flycast's `dc_nvmem.bin` is assembled in `.text` from instruction
immediates).

Mode keys are deliberately not anchored. They are the governing option's own values and the deployed core registers
them, so the tests measure that set against the binary directly — a measurement beats an anchor.

The loader validates the block and then drops it: anchors are audit machinery, and nothing that reaches a caller holds
them.

### Provenance

`provenance.source` is the evidence prose the answer carries in its `sources` — the one field of the block a resolver
reads. `provenance.status` states the evidence **per mode**: every mode has an entry, and the flycast card opens each
with the project's evidence marker (`[V-live]`, `[D]`, `[O]`) so an observed mode and a derived one cannot be read as
the same claim. That is a maintenance invariant enforced by tests over this file, not an input to any answer — no client
receives anything shaped by it.

What a card was verified _against_ is not written here. It lives in `core_audit.json` as `verified.<arrangement>` — the
arrangement version, the core's `library_version`, the date — where it is structured, compared against the running
machine, and carries `unverified-version` when the two differ. A prose twin beside the card could only be a second copy
going stale in silence.

## `core_audit.json`

The machine-readable core of `docs/research/core-audit.md`: per core, the verdict, the evidence `note`, whether per-game
saves are a proven capability, and — per arrangement — the versions the knowledge was verified against. The file's own
`spec` field carries the rules; the research doc carries the method.

## `texture_packs.json` — where each emulator reads texture packs

One card per core, read by `atlas.textures`, and the split it makes is the boundary rule at its sharpest: **the root is
not in this file, and neither is the option's value.** A card names a root _kind_ that the resolver resolves live — the
system directory as the core receives it, or the save root as it stands — plus the option that gates replacement and
which of its values mean _on_. Whether it is on is then read from the options file RetroArch would read first, or from
the default the installed core registers. What is left is what no machine states: the fragment below the root, the
option's identity, and how the tree is keyed per game.

```json
"flycast": {
  "identifiers": { "library_name": ["Flycast"] },
  "textures": {
    "root": "system_directory",
    "subdir": "dc/textures",
    "keying": { "value": "game-id", "citation": "[V-binary] flycast_libretro.so (1dac369): …" },
    "replacement_option": { "setting": "reicast_custom_textures", "values": { "enabled": true, "disabled": false } }
  },
  "provenance": { "source": "[V-binary] … carries \"system/dc/textures/<game-id>/\" as a NUL-delimited literal …" }
}
```

- The key is the `.so` basename without `_libretro.so`, and the `.so` name is **derived** from it — a restated one could
  only ever disagree, so the loader refuses `identifiers.so`. `library_name` makes lookup work from either side.
- `root` must be one of the placement's own root kinds; `subdir` must be relative and free of `..`, because it is joined
  onto a directory resolved from a config and an absolute fragment would replace that root instead of extending it.
- `keying` is the one field no read of any machine can contradict, so **it is refused without a citation**: the
  vocabulary is `game-id | serial | title-id | rom-name | pack`, and a row whose evidence stops short states nothing at
  all rather than a derived value wearing the same field name as an established one.
- `replacement_option` may be `null` (nothing switches this core's replacement). Its `setting` is the core option's own
  name — spelled the way `absent_switch` spells its own, so the two blocks read alike. Its `values` must name at least
  one value meaning enabled and one meaning disabled — an option whose every value means the same thing governs nothing,
  and would report a feature as permanently on while the machine could say otherwise. Booleans are never coerced from
  strings. No default lives here: RetroArch falls back to the **installed core's** own registration, which is a live
  read.
- `provenance.source` is required on every card, for the same reason `keying` needs its citation.

### `emulators` — the standalone half

A second block, keyed by the `%EMULATOR_…%` token the frontend's own launch command names, because for a standalone
entry that token is the only identifier there is (no `.so` basename exists). The split from `cores` is not
organisational: the two kinds of emulator are handed their root by different parties. A libretro core is handed one by
RetroArch; a standalone emulator opens **its own default directory below an XDG base**, so a card names which base
(`data` or `config`) and the fixed subpath, and the arrangement resolves where that base is. Inside a flatpak the bases
are pinned, which is exactly why these rows need no config of the emulator's to place.

```json
"DOLPHIN": {
  "textures": {
    "base": "data",
    "subdir": "dolphin-emu/Load/Textures",
    "keying": { "value": "game-id", "citation": "[V-binary] the shipped dolphin-emu carries \"…<game_id>/…\"" },
    "config": { "base": "config", "path": "dolphin-emu/GFX.ini" }
  },
  "provenance": { "source": "[V-binary/V-script] …" }
}
```

`config` is **required** and is the one field here that is never read: it names the settings file that would establish
whether replacement is on, and every standalone answer carries `emulator-config-unread` pointing at it with `enabled`
left `None`. A card without one would tell a client the switch is unknown and give it nowhere to look. `base` and
`subdir` are held to the same rules as the core block's root and fragment; `keying` to the same cited-or-absent rule.

Two standalone emulators are **absent on purpose**, and a test holds the absence down: PCSX2 and Vita3K do not open a
default at all — RetroDECK writes their texture directory _into the emulator's own configuration_ (`Folders/Textures` in
`PCSX2.ini`, `pref-path` in Vita3K's `config.yml`). Reading that means modelling the configuration; quoting the path the
installer intended would state an arrangement's directory as an emulator's read location. They refuse with
`standalone-unsupported`, and the split inside the standalone kind is evidence rather than policy.

### Absence is a statement about atlas, not about the emulator

Neither block is a census of emulators with texture packs. A **core** this file does not reach answers
`texture-wiring-unestablished`; a **standalone** emulator it does not reach answers `standalone-unsupported`. Both say
atlas has not established where that emulator reads — never that it reads nowhere.

### `absent_switch` — a feature the build offers no way to switch on

The third thing a core card can say about the switch, beside naming an option and saying nothing. LRPS2 is the case that
earned it: at 14d19f8 texture replacement is compiled in and aimed at
`<system_directory>/pcsx2/textures/<serial>/replacements/` recursively — the card's `keying` is `serial` and the fixed
`replacements` level is stated in provenance, because no decided field carries a constant below the keyed level — while
the setting that enables it, `EmuCore/GS/LoadTextureReplacements`, defaults false and has **no writer anywhere in the
build**. So `enabled` is `false` as a fact about the binary rather than a reading of any file, and the answer carries
`feature-switch-absent`.

```json
"absent_switch": {
  "setting": "EmuCore/GS/LoadTextureReplacements",
  "enabled": false,
  "verified_core": "14d19f8",
  "citation": "[V-source/V-binary] defaults false (Pcsx2Config.cpp:433); memory-only settings store …"
}
```

Three rules keep the strongest negative in this file honest. A card states an **option or an absent switch, never both**
— they are contradictory claims about one build, and the loader refuses the pair. `enabled` is a JSON boolean, never
coerced. And the claim is **pinned to the build it was proven against** by `verified_core`, because a build is exactly
what could add a writer: a machine whose core reports a different version gets `unverified-version` beside the claim
rather than inheriting it unexamined.

That pin is the card's **own** field rather than `core_audit.json`'s `core_library_version`, and the difference is not
cosmetic. The audit's version moves whenever a live round re-verifies a core's _save_ behaviour, so keying this claim on
it would let a bump made for an unrelated reason silently re-validate it against a build nobody examined for texture
replacement. A field of its own moves only when someone re-examines the build for this.

It can never ride with `emulator-read-unestablished`, because the two say opposite kinds of thing — there the read path
is in doubt, here it is established and simply never taken. A card with an `absent_switch` therefore belongs to a core
whose audit verdict is not `suspect`, and a test asserts exactly that over the shipped cards.

One fact belongs beside the row because it invalidates the obvious shortcut: the core **creates that directory on every
`retro_load_game`** (`libretro/main.cpp:1922` → `Pcsx2Config.cpp:1084-1085`), so finding it on disk, empty or not, is
evidence of nothing.

An earlier version of this file recorded LRPS2 as spelling no texture path in any string encoding, and refused it a row
on that basis. That was wrong, and the reason is now the third trap in `docs/research/core-audit.md`: `textures` and
`Textures` are linker-tail-merged into `GL_EXT_protected_textures` and `glBindTextures`, so no `strings` pass surfaces
them at any encoding — only a raw-byte search does.

Three cards (`azahar`, `dolphin`, `ppsspp`) describe cores that port a standalone emulator and build a user directory
under a root nobody has watched them choose. Their answers carry `emulator-read-unestablished`, and **that caveat is
driven by `core_audit.json`, not by this file or by a resolver**: it fires on the `suspect` verdict those cores already
carry for their saves, so closing the verdict there retires it here, the way `arrangement_evidence.json` retires
`arrangement-unverified`.

## `save_memory.json` — which files RetroArch writes for a core, per system

Read by `atlas.save_memory`, and it is the only table here keyed by **core _and_ system**. The name of a save is not in
this file and never will be: RetroArch builds it from the content's own stem plus `.srm` (`runloop.c:8720-8723` at
RetroArch a79435a) and derives the `.rtc` twin from it, registering the pair as `RETRO_MEMORY_SAVE_RAM` and
`RETRO_MEMORY_RTC` before any core is asked (`save.c:710-724`, reached for all non-subsystem content at
`runloop.c:4461`); the three remaining `RETRO_MEMORY_*` ids never reach a file. That rule is code, not data — no machine
states it and no core changes it. What is here is the half no machine states: RetroArch writes a file only where the
core answers with a pointer **and** a non-zero size (`save.c:480`), and a core answers that only once content is loaded,
which atlas never does.

```json
"mgba": {
  "identifiers": { "library_name": ["mGBA"] },
  "systems": {
    "gba": { "memory_types": ["save_ram"], "verified_core": "0.11-dev c758314", "citation": "[V] …libretro.c:2338-2347 …" },
    "gb":  { "memory_types": ["save_ram", "rtc"], "verified_core": "0.11-dev c758314", "citation": "[V] …:2349-2351, :2357-2366 …" }
  },
  "provenance": { "source": "read at the revision the installed binary names" }
}
```

- **Why the system is part of the key.** A core is not one behaviour. mGBA answers a Game Boy cartridge's clock and a
  Game Boy Advance cartridge's not at all, so a record spelled `mgba` alone would have to be wrong about one of them.
  Where the caller did not name a system the resolver states nothing rather than picking one of a record's entries.
- **Every entry is an upper bound.** Whether _this_ cartridge carries a battery or a clock is a fact about the game —
  mGBA reads it out of the ROM, gambatte off header byte `0x147` — and no table can hold it. A record states which files
  can occur at all, which is the candidate set a save-syncing client needs.
- **`verified_core` is the core's own `library_version`**, which for these cores names the very commit the binary was
  built from (`0.11-dev c758314`). That is what makes the citation checkable, and a machine running another build gets
  the claim with `unverified-version` beside it — a rebuild is exactly what can add or drop a memory id. The pin lives
  here rather than in `core_audit.json` for the reason `absent_switch` keeps its own: that record's version moves when a
  live round re-verifies a core's _placement_, and a bump for an unrelated reason would silently re-validate a claim
  nobody re-read the source for.
- **An extension a core writes itself can never appear here**, and that is what "memory" in the file's name means: this
  table records the two ids a core hands the _frontend_, so Flycast's `.bin` VMUs, Beetle Saturn's `.bcr`/`.bkr`/`.smpc`
  and beetle_psx's `.mcr` are outside it by construction — the core writes those past the interface. They belong to
  `core_oddities.json`, where several of them already are, and the loader enforces the boundary: `memory_types` accepts
  `save_ram` and `rtc` and refuses every other word.
- **A card wins.** Where `core_oddities.json` carries a card for a core, this file is not consulted at all — not merely
  where that card declares files. A carded core is a _deviating_ core, and a record that filled in the file names of a
  save the card has moved elsewhere would be right about the names and wrong about the save.
- **A core absent from this file writes nothing that is stated**, not nothing at all: its source has not been read yet,
  and the resolver keeps whatever answer it gave before.
- **A core that could not be examined gets no claim either.** A record is read out of one build's source, so applying it
  on the strength of a `.so` file name would state a source-verified claim about a binary nobody read — the decision
  `_select_card` already makes for a rule card in the same state. The answer then carries
  `core-generation-unestablished` and names no files.

Method note, hard-won on the first two records: mGBA's `retro_get_memory_data` has no `break` in its RTC branch, so for
a GBA cartridge that id falls through and returns a valid work-RAM pointer. Reading only that function would put a
`.rtc` into the answer for every GBA game — the size function is the one that decides, because `save.c:480` requires
both.

## `mods.json` — where each emulator reads mods, and what a build patches with

The texture file's grammar, read by `atlas.mods`, split the same way for the same reason: `cores` for libretro cores
(handed a root by RetroArch) and `emulators` for standalone ones (opening their own default below an XDG base). Roots
and option values are read live; the fragment, the keying and the option's identity are here. Three things are this
file's own.

### `trees` — a card may state several directories

An emulator can read mods from directories that are **different mechanisms rather than alternatives**. FBNeo takes a
replacement romset from one, an IPS patch set from a second and a romdata file from a third, all under one switch, so a
card states a list and each entry carries its own `subdir` and `keying`:

```json
"fbneo": {
  "identifiers": { "library_name": ["FinalBurn Neo"] },
  "mods": {
    "root": "system_directory",
    "trees": [
      { "role": "patched", "subdir": "fbneo/patched", "keying": { "value": "rom-name", "citation": "[V-source] …" } },
      { "role": "ips", "subdir": "fbneo/ips", "keying": { "value": "rom-name", "citation": "[V-source] …" } },
      { "role": "romdata", "subdir": "fbneo/romdata", "keying": { "value": "rom-name", "citation": "[V-source] …" } }
    ],
    "option": { "setting": "fbneo-allow-patched-romsets", "values": { "enabled": true, "disabled": false }, "default": { … } }
  },
  "provenance": { "source": "[V-source/V-binary] …" }
}
```

`role` is the emulator's own word for a tree and is what tells several apart. A card stating **one** tree names **no**
role — there is nothing to tell apart, and a made-up name would be vocabulary a client has to learn to ignore — while a
card stating several requires one on each, all distinct. The loader refuses every other combination.

### `option.default` — a switch value written down because no machine states it

Ordinarily no default lives in a card: RetroArch falls back to the installed core's own registration, which is a live
read. FBNeo cannot be read that way — it registers its options **after** `retro_set_environment`, so no probe captures
them and no options file mentions the key — so the card carries the upstream value:

```json
"default": {
  "value": "enabled",
  "verified_core": "v1.0.0.03  01e29d5",
  "citation": "[V-source] fbneo@01e29d5 retro_common.cpp:236-249 … default_value \"enabled\", and :53 initialises …"
}
```

The precedence is the point: an options file wins over this, and a default the probe **does** capture wins over it too —
the record is the last resort, not the first word. Like every claim about a build it is pinned by `verified_core`, so a
machine running another build gets the value with `unverified-version` beside it. The loader refuses a default outside
the option's own `values`, which would reach an answer as a value it then declines to interpret.

### `config` — optional here, unlike the texture cards

A standalone card's `config` names the settings file that would establish whether loading is on, and its answer then
carries `emulator-config-unread` pointing at it. Here the field may be **`null`**, and exactly one shipped card uses
that (a test holds the count down): for Azahar nobody has established that any switch exists at all — not a core option,
not a CLI flag — so naming a file would signpost one that may govern nothing. That row states `enabled` as unanswered
and points nowhere, which is the weaker and the honest claim.

A **core** card may name one too, and that is where `emulator-config-unread` first rides a libretro row: the Dolphin
core's mod switch is not a core option but an ordinary `GFX.ini` inside the user tree the core builds, so the card gives
a `path` relative to the same root the trees hang off (no `base` — the root is not an XDG one).

MAME is **absent on purpose**, the same way PCSX2 and Vita3K are absent from the texture table: its plugin directories
are values RetroDECK writes into `mame.ini` rather than defaults the emulator opens. PCSX2 goes the other way here and
**answers**, because nothing writes `Folders/Patches` and the directory is the emulator's own default — the split runs
on evidence, not on the emulator. A core this file does not reach answers `mod-wiring-unestablished`; a standalone
emulator it does not reach answers `standalone-unsupported`.

### `soft_patching` — what a shipped RetroArch was built to attempt

The other question's world knowledge, keyed by **installation kind** rather than by emulator, because it is a fact about
a frontend build: patching as a whole is `HAVE_PATCH` and the `.xdelta` applier `HAVE_XDELTA`
(`Makefile.common:260-267`), both compile-time and stated by no setting, log or file on any running machine.

```json
"soft_patching": {
  "retrodeck": {
    "formats": ["ips", "bps", "ups", "xdelta"],
    "verified_arrangement": "0.10.9b",
    "citation": "[V-binary] … four adjacent whole NUL-delimited runs at 0x78a06a-0x78a07f, and the help lines that exist only inside their own guard …"
  }
}
```

`formats` is the set that build was **proven** to attempt, so a format outside it is a claim as strong as the positive
one, and an empty list is a legal record (patching compiled out) rather than a missing one. An arrangement with no
record is one nobody examined: every candidate comes back with `attempted` null beside `patch-formats-unestablished`,
never with the upstream build defaults, which are a fact about the source tree and not about anyone's binary.

## `arrangement_evidence.json` — which arrangements have been seen alive

One record per installation kind, saying whether a live installation of that arrangement has ever confirmed atlas's
answers end to end. Read by `atlas.evidence`; an arrangement whose record has no `verified` block attaches
`arrangement-unverified` to every answer it gives.

```json
"emudeck": {
  "label": "an EmuDeck arrangement",
  "verified": null,
  "note": "[D] Read from EmuDeck's shipped configuration upstream …; no EmuDeck machine has been observed."
}
```

- `label` — how the arrangement is named in the caveat's prose.
- `note` — the evidence level (`[V]`/`[D]`/`[O]`, as in `docs/research/`) and what it rests on.
- `verified` — `null`, or `{version, date, reference}`: the arrangement version observed, when, and which installation.
  `version` and `reference` are required in a present record, because a record that pins nothing would read as verified
  everywhere and forever while establishing nothing.

`version` is also live machinery, not only provenance: the resolver compares it against the version the machine states
about itself (RetroDECK writes one into `retrodeck.json`) and attaches `arrangement-version-drifted` when the two
differ. That one comparison guards everything pinned at once — parser grammar, path layout, shipped-build behaviour —
and `docs/re-verification.md` is what retires it. The comparison needs both sides: a machine that states no version is
not compared, and stays silent rather than claiming a comparison nobody made.

Two rules make this file the whole mechanism. **A missing record counts as unverified** — omission is never evidence —
though a handle kind with no record at all fails the test suite, because a fact nobody wrote down should not ship.
**Verifying an arrangement retires its caveat here, not in a resolver**: add or re-pin the `verified` block, and the
answers stop carrying it. No code names an arrangement's evidence state. Re-pinning does move the vector corpus, though
— every fixture stating the old version is a drifted machine afterwards, and the runner enforces it
(`docs/re-verification.md`, step 5).

## `system_ids.json` — the whole system vocabulary

Every id a question about a system can take, read by `atlas.systems`. One list, nothing else in the file.

```json
"systems": ["3do", "gb", "n64", …]
```

The ids are **ES-DE's system names**, because that is what a frontend catalogue declares and what the questions answer
about. Cited to the `es_systems.xml` of a pinned build, so the set is checkable rather than an opinion: a name that file
does not declare is not an id, however plausible. Commented-out blocks are not declarations and are not included — the
guard test parses the file with ElementTree, which drops them, and asserts set-identity with this list.

**No foreign vocabulary belongs here.** Another product's platform identifiers — a library manager's slugs, a metadata
service's ids — are world knowledge atlas cannot check against any machine, and a client holding them owns the mapping
into these ids (`DESIGN.md`, Vocabulary). What ships for them is the target set to validate against, not a table.

**The loader is fail-closed**: an unreadable schema, an empty list, an entry that is not a non-empty string, and a
repeated id each fail the load. A vocabulary whose own account of itself does not add up would answer "not an id" for
names that are, and a caller validating against it would delete the mapping that was right.

## `firmware_hashes.json`

What a correct firmware file's bytes are: the `md5` / `sha1` / `size` triple that identifies it. This is world knowledge
by nature — no config on the machine states it — so it ships inside the `atlas` package and is read by
`atlas.firmware.load_hashes`. At the current release it holds **388 firmware identities**.

Which files a core _wants_ is deliberately not in here. Those declarations sit in the `.info` files RetroArch ships next
to its cores, so atlas reads them off the running machine (`atlas.firmware.read_core_declarations`) instead of shipping
a snapshot that drifts against the cores an installation actually has. The filename is where that split shows.

Shape:

```json
{
  "_meta": { "generated_from": "...", "version": "5.0.0", "generated_at": "2026-02-27" },
  "files": {
    "scph5501.bin": { "md5": "...", "sha1": "...", "size": 524288 }
  }
}
```

- `_meta.generated_from` — the upstream source the file was built from.
- `_meta.version` — the table's schema/data version (bumped deliberately, independent of the package version).
- `_meta.generated_at` — the UTC date of the last regeneration (`YYYY-MM-DD`).
- Keys are whatever `System.dat` uses, verbatim: usually a bare file name (`scph5501.bin`), sometimes a relative path
  (`dc/dc_boot.bin`, `pcsx2/bios/…` — 91 of the 388 entries). `FirmwareHashes.for_path` therefore matches a declared
  path first and its base name second, which stays unambiguous only while no base name is claimed by two entries — a
  test pins that. Every entry carries all three of `md5`, `sha1`, and `size`.
- **One content, several names.** 18 of the 369 distinct contents are keyed under more than one name — `dmg_boot.bin` ≡
  `gb_bios.bin`, `dc/boot.bin` ≡ `dc/dc_boot.bin`, `bios.sms` ≡ `bios_E.sms` ≡ `bios_U.sms`.
  `FirmwareHashes.for_content` indexes that direction, which is what makes "these bytes belong at these three
  destinations" answerable. Note that a shared identity never satisfies a requirement under another name: SameBoy opens
  `dmg_boot.bin` and nothing else.
- The table covers only what `System.dat` covers. A declared file with no entry here is a **normal** state, not a gap in
  the data — `atlas.firmware` reports it as present with `checked` = `unknown`, never as verified.
- **Not every identity is a whole-file dump.** 21 entries are archives or data packs (MAME-style romset zips such as
  `neogeo.zip` and `dc/naomi.zip`, plus `scummvm.zip`, `Dinothawr.zip`, `ecwolf.pk3`, `prboom.wad`). A romset zip hashes
  differently per romset version and merge mode, and a data pack tracks its core's version, so a `mismatch` on one of
  these says less than it appears to. The table does not yet distinguish those entries from real dumps; doing so needs
  per-entry provenance, not a guess from the file extension (ROADMAP, block 6).

## Upstream source

The table is derived, offline, from [libretro-database](https://github.com/libretro/libretro-database) —
`dat/System.dat`, the hashes and sizes for known firmware files.

## Regenerating

Generation is a dev-time, offline step. The generator takes a local git checkout of the upstream repo as an argument and
touches the network for nothing:

```sh
git clone https://github.com/libretro/libretro-database ~/src/libretro-database

python scripts/generate_firmware_hashes.py --database ~/src/libretro-database
```

With no `-o`, the output defaults to `atlas/data/firmware_hashes.json` (this file's sibling), resolved relative to the
repo root so the command works from any working directory. Pass `-o <path>` to write elsewhere.

## Update discipline

- **A regeneration lands as a reviewable data diff in its own PR.** The point of committing generated data is that the
  diff is auditable: a reviewer can see exactly which entries were added, removed, or changed. Regenerate against a
  fresh upstream checkout, commit the resulting JSON, and let the diff speak.
- **A changed hash is a behavior change for consumers — say so in the PR.** An identity changing, or an entry appearing
  or disappearing, is not cosmetic: a file that verified before may report `mismatch` after, and one that reported
  `present` may start verifying. Call these out explicitly in the PR description so the change is not merged as a silent
  data bump.
- **The vector breaking-change gate does not watch `data/`.** `scripts/check_vector_breaking_change.py` guards only the
  conformance vectors under `vectors/`; it never inspects this table. The table is versioned by releases instead —
  consumers pin a release and get a stable snapshot — so a data change that matters to consumers relies on the PR
  description and the release notes to surface it, not on an automated gate.
