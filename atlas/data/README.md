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

A mode — one value of the governing option, or one name a governing _rule_ selects (see below) — states the root it
anchors at and its **groups**: one entry per directory _and meaning_, because a save is not always one list of files
that mean one thing. MAME keeps a machine's battery memory under `nvram/`, its dip switches under `cfg/` beside an
emulator-wide `default.cfg`, and disk write-differences under `diff/`; Flycast's shared mode keeps four memory cards and
the console's own flash side by side under `dc/`. A group carries its own `subdir`, its own `granularity`, a `role`, and
its own file and evidence fields.

Four of those fields are closed vocabularies — all four the placement's own, imported by the loader rather than
respelled here, so a card cannot select a value the contract cannot carry — and one is a type rule:

| field         | on    | accepted values                                                                       |
| ------------- | ----- | ------------------------------------------------------------------------------------- |
| `root`        | mode  | `savefile_directory`, `system_directory`, `content_directory`, `working_directory`    |
| `granularity` | group | `shared-card`, `shared-file`, `per-game-file`, `per-game-files`, `per-game-directory` |
| `role`        | group | `battery`, `memory-card`, `disk-diff`, `high-score`, `settings`                       |
| `complete`    | group | a JSON boolean, `true` or `false` — never a string, never coerced                     |

`granularity` and `role` reach the caller as contract values, so a misspelling would be stated as this machine's actual
grouping or would send a save sync past real save data; `complete` is a claim about the save, and `bool("false")` is
`True` in Python, so a quoted boolean fails the load instead of silently asserting completeness.

`working_directory` is the root that is a property of the launch rather than of the machine: DeSmuME 2015 composes its
save path from a variable its build never fills, so the file lands relative to wherever the launching process was
started. Such a mode's answer is always the `<cwd>` template with the `cwd` hole in `needs` — the file names are stated,
the directory is the caller's to fill — and it rides the `save-dir-launch-dependent` caveat. Nothing on the machine is
read for it: a template names nothing to observe.

**Why grouping and role are two fields.** They answer different questions — _whose is it_ and _what is it_ — and MAME's
two `.cfg` files are the proof neither can carry the other's meaning: `<machine>.cfg` and `default.cfg` share a
directory and a role and differ only in whom they belong to. A client syncing save data takes every role but `settings`,
and never copies a `shared-*` group between machines blind.

**Which role to pick.** `battery` is the cartridge or board memory a game saves its own progress into, `memory-card` a
removable card the console writes several games onto, `disk-diff` the changes written back to a disk image, `settings`
configuration the emulator keeps beside the saves. `high-score` is the arcade family's, and it is separate from
`battery` for one reason: the merge. A machine keeps one score table for everyone who ever played it, so two devices
that both played a game hold two tables of which neither is stale — the answer is the higher entries, not the newer
file. Anything that is genuinely one player's progress is `battery` even where the hardware is unusual; the score table
is the case that is not.

```json
"All VMUs": {
  "root": "savefile_directory",
  "groups": [
    {
      "subdir": null,
      "files": ["<save_id>.A1.bin", "<save_id>.B1.bin"],
      "files_without_save_id": ["<rom_stem>.A1.bin", "<rom_stem>.B1.bin"],
      "granularity": "per-game-files",
      "role": "memory-card"
    }
  ]
}
```

**The first group is the mode's own answer**, and the groups sharing its directory are what `dir` and `file_set.files`
have always described — the names lying in one directory. Splitting such a list by role therefore moves no name out of
an answer: the resolver states their concatenation, in card order, and both the loader and the vector validator refuse a
card whose parts do not add back up. Groups in _other_ directories reach the caller through `file_set.groups` alone. Two
consistency rules hold within one directory: its groups either all declare files or none do (one unverified part would
silently shorten a list stated as the whole), and at most one of them may scope its list with `files_established_for`,
since the mode's answer can carry only one scope.

### A tree whose names cannot be derived — `unnamed`

A group states `unnamed` instead of `files` when the directory is knowable and its file names are not. MAME's
differencing images for CHD hard disks are the case it exists for: the name is the disk image's own entry in the
machine's ROM table inside the binary, which is no read of _this_ machine, and upstream says on the line that builds it
that the scheme "doesn't scale". MAME's memory cards are the same shape for a different reason: they are named after an
index chosen in the emulator's own interface.

Such a group **is** a `FileGroup` in the answer, with `files=None` — the directory, the granularity and the role are all
stated, and only the list is refused. That is what lets one walk over `file_set.groups` reach every place a save lives.
The answer also names the directory in a `file-names-unestablished` caveat, with this text as `data["citation"]` and the
group's `role` beside it; that caveat is the sentence a person reads and the citation behind it, not the only carrier.

The alternative would have been silence, and silence there is the expensive kind: a client that never learns the
directory exists loses the player's progress on every machine with a hard disk. The loader refuses `unnamed` together
with `files` — the field's whole reason is that no list can be given.

A mode whose **every** group is unnamed is a real statement, not an empty one: it names the directory the save lives in
and says why no file name follows from anything atlas reads. ScummVM is its one customer — the slot files land flat in
the frontend's save directory, named per engine from the ScummVM _target_, which is launcher configuration. Such a
mode's answer is a declared set of no statable names (`files: []`, `complete: false`) with the one `files=None` group
and the `file-names-unestablished` caveat carrying the reason; it can never claim completeness, because the names exist
and are simply not derivable.

### File names are templates in the placement's own hole vocabulary

A declared name may carry exactly two tokens, and they are not local to this file: they are the holes
`SavefilePlacement.needs` speaks (`atlas/placement.py`, which the loader imports — one definition, not a second spelling
here).

- `<rom_stem>` — the resolver fills it from the content path. Not a hole in an answer: either it is substituted or the
  file set is honestly unknown.
- `<save_id>` — the content's platform-native id (Flycast names a per-game VMU after the disc's product number). atlas
  never fills it, because reading an id out of a ROM is identification, not location. It stays in the stated name and
  `save_id` joins `needs`, so a caller sees a template rather than a resolved-looking name.

A group may also carry `observe`: candidates wider than the declared defaults, probed on the machine because they exist
only when configured (Flycast's slot-2 VMUs beside the four port-1 cards). Since issue #89 an **observation gate** can
narrow them back — code keyed by `(card, mode)` in `atlas/installations.py`, the same code-beside-data split the
selection rules make: the card states what _can_ exist, the gate reads the live switches that rule a candidate out here
(`reicast_device_port{1..4}_slot2` holding anything but the VMU device), and the consulted switches ride the answer's
granularity readings. A gate only ever removes candidates, and only on an established value — a switch nobody could read
excludes nothing, because "cannot exist" is a claim, not a default.

The loader rejects any other token in a declared name, and the check is **subtractive**: it removes the known templates
and refuses whatever still contains `<` or `>`. Scanning for well-formed `<…>` would pass `<rom_stem.A1.bin` (bracket
never closed) and `<<rom_stem>>.A1.bin` (nested) — both of which atlas would then state verbatim as a filename. That is
the point of keeping one vocabulary: a card is data, and without the check a typo travels silently into a name atlas
states as fact — the failure mode the "never guess" rule exists to prevent. A card that needs a new hole adds it to the
placement vocabulary first. An empty list, an empty name and a literal angle bracket are refused for the same reason.

A `subdir` segment may be a template too, from its own two-token vocabulary: `<rom_stem>` (prboom creates
`<save dir>/<rom_stem>/`) and `<content_dir_name>` — the basename of the content's directory (the vitaquake2 family
creates `<save dir>/baseq2/` for content in `baseq2/`). A token must be the **whole segment**: the resolver undoes a
subdir by counting segments, and that arithmetic is exact only while one template fills to exactly one segment. Both are
established only under the `savefile_directory` root — the loader refuses them elsewhere, because no read core keys a
system or content subdirectory on the content. The resolver fills them from the content path; a content-less question
keeps the token in `dir` and puts `rom_stem` / `content_dir_name` into `needs`, the shape `<content_dir>` has always
had.

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

### A group at another root — the spanning save

A group may anchor at a different root than its mode (`root` on the group; the retired `also_under` field's successor,
issue #97). Flycast's per-game modes move the governed VMU under the save root while the console flash — and in `VMU A1`
the three unmoved shared cards — stay under the system directory's `dc`, and the mode now states every part with its
files instead of naming a second root it cannot list. The shape is narrow on purpose, like every first shape: only a
`savefile_directory` mode may keep parts behind, only under `system_directory` or `content_directory`, never as the
first group, always with files, and without the home-directory machinery (`observe`, `unnamed`, the list scopes) — each
of those answers for the mode's own directory, and a cross-root group is outside all of it until a core needs otherwise.
A cross-root part reaches the caller twice on purpose: as a `FileGroup` where the set is declared, and always as a
`file-set-spans-roots` caveat whose data names the resolved directory and the files — the carrier that survives an
observed answer, exactly the way `file-names-unestablished` carries MAME's unnamed tree. A card still spelling
`also_under` fails the load loudly, because it would otherwise silently lose the claim it thinks it makes.

`complete` is the explicit claim that the mode's candidate universe is closed. A template can in principle carry it —
the hole is in the name, not in the membership — but only source-verified provenance earns it.

### `inside_content` — the mode with no separate save file

Some cores can write straight into the loaded content file: quasi88 with its option on puts every sector write into the
`.d88` image itself. Such a mode states `inside_content` (required prose: the reason, with citations) **instead of**
`groups`, and its `root` must be `content_directory`. The answer is then a **declared emptiness** — true as stated, no
separate save file exists — plus the `save-inside-content` caveat carrying the fact machine-readably. Deliberately never
"the content file, declared as a save": a client already manages that file as the content, and handing it over under a
second name would make a sync client treat the ROM as a save. What to make of a content file that doubles as the save
(back it up, copy it, leave it) is the caller's decision; atlas states the fact and stops.

### `writes_discarded` — the mode where nothing keeps a save at all

The inside-content statement's harder sibling: hatari with write protection on throws the modified image away at eject,
so no save exists _anywhere_ — not beside the content, not inside it. Same construction (required prose instead of
`groups`, the two statements refuse to ride one mode together), its own caveat `save-writes-discarded`, and one
difference a client reads: the granularity value is `none`, the one value no file group may ever carry, because a group
is a place save data lives and this mode says none does. The granularity block still travels — its readings and
alternatives are how a caller sees which switch would make saves exist again. The root is where the writes would have
landed: the content's own tree for hatari, and the frontend's save root for SwanStation with both memory-card slots set
to none — the one root kind `inside_content` can never take, since that statement is about the loaded content file
itself.

### `governing_rule` — modes a rule selects instead of one option's value

Some cores' behaviour is the **product** of several interacting settings, and no single option's value can name a mode:
Beetle Saturn's two sharing switches make four modes, hatari's governing option depends on the loaded content's class,
ScummVM's save directory is a setting in the emulator's own ini. Such a card states `governing_rule` (mutually exclusive
with `governing_option`) with the core options its rule reads — `options` may be empty where the rule reads none — and
its modes carry **freely chosen names** (`per-game`, `internal-shared`, `floppy-writeback`): atlas's own vocabulary,
like caveat codes, since no binary registers them as values.

The rule itself is code, in `atlas/mode_rules.py`, keyed by the card key — the format grows _code plus a card
referencing it_, never a DSL. The card stays what a card always was (**what can exist**), the rule decides **what holds
here**, and everything it decides on is a live read the resolver hands it: the declared options' values, the content's
class, a file of the emulator's own. The loader refuses a `governing_rule` with no registered rule behind it, and the
tests hold the mirror claim. Feature detection covers the plural: every declared option must be registered by the
installed core, or the card steps aside as a generation mismatch exactly as a single-option card does.

A card may also record **`retired_options`** — the spellings an older generation of the same core wrote and this one no
longer reads (issue #79: RetroArch never prunes the options file, so the entry stays and its value silently stops
applying). Each entry needs its citation, and retirement is a _negative_ binary fact: the key is absent from the shipped
`.so` even as a substring while its replacement is a whole literal — which is also why these keys are deliberately
outside the anchor vocabulary (an anchor demands a literal the binary must carry, and a retired key's whole point is
that it must not), and why the statement works for a core that registers its options too late for any probe (LRPS2).
When the governing options file carries such an entry, the answer states it under `option-entry-retired`, riding the
same parse the value lookup already made — the loader refuses an entry without a citation, one colliding with a key the
generation still reads, and the field on a card that reads no options at all.

The answer's granularity block records the decision so a client can act on it: one reading per switch the rule actually
consulted (its live value, its provenance, the file where it would change — hatari reads one of its two write-protect
options, never both, because which one governs is the content's class), and alternatives with the full option
combination that selects each — every alternative lists each distinct grouping of its mode (`values`, the mode's own
first), so a mixed mode cannot hide the card every game shares behind its per-game headline. Which other modes an answer
lists is the rule's judgment: a small space lists every other mode (Beetle Saturn's three), a large one the one-edit
neighbours — every switch, changed once — rather than the whole product (SwanStation's twenty, Genesis Plus GX's
twenty-six CD combinations). A rule that cannot decide selects nothing and says why — `core-mode-unestablished` with the
reason, or the sharper codes where they exist (`core-option-value-unestablished` per unreadable switch,
`save-root-redirected` where ScummVM's `savepath` points outside every root kind the format can anchor, with the
configured path in the caveat's data). Stepping aside can also be the whole truth of a mode rather than a failure to
read one: MAME handed its own paths (`mame_mame_paths_enable`) anchors its save trees at the frontend process's working
directory — process state, written nowhere on the machine — so the rule states the relative trees machine-readably
(`save-root-unresolvable`) instead of claiming a root; with `mame_read_config` also on it first reads `mame.ini` and the
driver's `<stem>.ini` along `$HOME/.mame` → `<system>/mame/ini` the way the emulator does, and absolute values become
per-tree `save-root-redirected` caveats. The card keeps no mode for that world — a mode whose directory nobody can state
is a caveat, not a mode.

### Anchors — every recorded name, pinned to the string it was read from

A card names files and subdirectories that reach a caller as fact, and `saves.anchors` records where each of those names
came from. One entry per recorded name — the governing option key (or every option a `governing_rule` declares), and
from **every group of every mode** each segment of its `subdir` and every name in its `files`, `observe` and
`files_without_save_id` — carrying exactly one of three kinds:

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

Mode keys are deliberately not anchored. On an option-governed card they are the option's own values and the deployed
core registers them, so the tests measure that set against the binary directly — a measurement beats an anchor. On a
rule card they are atlas's own vocabulary, chosen the way caveat codes are; nothing in a binary spells them, so there is
nothing to pin.

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

Every row here and in the mods table carries an `anchors` block, the byte tripwire the rule cards already have — with
two differences the round that added it forced (issue #105). Containment is **raw bytes**, never a NUL-delimited run:
several of these literals exist only tail-merged into longer strings, which a delimited needle would miss and call a
rename. And the **encoding travels with the anchor** (`utf-8` unless stated): one shipped name — mupen64plus_next's
`hires_texture` — exists only as UTF-32LE. A standalone row's block also names the component `binary` its literals were
read from, because nothing derives it; a core row's binary is its key. Every recorded name — path segments, option
settings, config file names — is either anchored or opted out with a reason, and the opt-out list is curated in
`tests/test_anchor_tripwire.py` (empty today: even LRPS2's source-level settings key pins its own last segment).

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

Four cards (`azahar`, `citra`, `dolphin`, `ppsspp`) describe cores that port a standalone emulator and build a user
directory under a root nobody has watched them choose. Their answers carry `emulator-read-unestablished`, and **that
caveat is driven by `core_audit.json`, not by this file or by a resolver**: it fires on the `suspect` verdict those
cores already carry for their saves, so closing the verdict there retires it here, the way `arrangement_evidence.json`
retires `arrangement-unverified`.

## `standalone_saves.json` — which standalone emulators the save question answers for

Read by `atlas.standalone_saves`, and keyed by the `%EMULATOR_…%` token an ES-DE launch command names — the same key the
texture table's standalone half uses, because for a standalone entry that token is the only identifier there is. The
savefile question answers a standalone catalogue entry exactly where a card here covers its emulator, and refuses with
`standalone-unsupported` everywhere else; which of the two it is runs on evidence, never on the kind of entry.

A card is deliberately **thin**: the configuration file that governs the emulator's save tree (below the XDG base the
arrangement pins), the catalogue systems the card answers for, and the provenance behind both. Everything else — what
`SlotA = 8` means, what an empty path key defaults to, how a configured card path templates its region — is knowledge
written nowhere on the machine, so it lives as cited code beside the card (`atlas/installations.py`), the same split the
rule cards make with `atlas/mode_rules.py`. The loader refuses a card whose token has no resolver registered.

Five cards today, each at the release RetroDECK ships. Dolphin (2603a): GameCube card slots read from `Dolphin.ini`'s
EXI device ids, the GCI folder and raw card schemes as region-keyed templates with the `region` hole, and the Wii NAND's
unnamed `title/` tree. PPSSPP (v1.20.4): the Linux memstick is compiled in — the card's `config` is `null`, the honest
spelling of "no file governs this" — and savedata is one unnamed directory per game below `PSP/SAVEDATA`. xemu
(v0.8.135): every save lives inside the emulated Xbox hard disk named by `xemu.toml`, stated as one shared file with the
`save-inside-image` caveat carrying the inside layout (`UDATA/<title id>`), the EEPROM beside it as a named settings
group. Cemu (2.6): the MLC resolved the way the emulator resolves it (`--mlc` flag outranks `settings.xml` outranks the
default), and the per-title unit templated below it — `usr/save/<save_id>`, granularity `per-game-directory`, the fill
spelled in the caveat (nn_save.cpp:133-145). Azahar (2125.1.1): the emulated SD read from `qt-config.ini`'s
`[Data Storage]` group the way the emulator reads it (`use_custom_storage` routes `sdmc_directory`, `\default`
companions honored — ReadSetting, config.cpp:1442-1450), the per-title unit
`Nintendo 3DS/<ID0>/<ID1>/title/<save_id>/data/00000001` below it with compile-time all-zero ids (archive.h:22-24), and
the extdata tree stated beside it as its own group. The answers root at `emulator_directory` — no frontend hands a
standalone emulator a save directory. On EmuDeck the catalogue names no token — its commands run launcher scripts — so
an allowlisted launcher (`cemu.sh` and `azahar.sh` today) reaches the same card through the launcher route,
variant-gated: only the AppImage variant's config tree is established, and the other variants refuse with
`standalone-variant-unestablished`.

## `standalone_firmware.json` — what a standalone emulator expects beside its content

Read by `atlas.standalone_firmware`, keyed like the save cards beside it, and consumed by the firmware questions: a
carded standalone catalogue entry answers `declaration: "packaged"` with real requirements instead of refusing
`unsupported`. The card states what no machine read can recover — which file the emulator probes and where, established
from its source at the shipped release — and everything about this machine stays live: the destination is the join the
emulator performs against the arrangement's own XDG bases, resolved through symlinks, and `found` is what actually sits
there. A card names the **emulator's probe**, never an installer's staging spot: Cemu (2.6) reads its keys at
`GetUserDataPath("keys.txt")` (KeyCache.cpp:63) — RetroDECK links `bios/cemu/keys.txt` to exactly that path, while
EmuDeck's installer parks a found `keys.txt` in the config directory the Linux build's key probe never reads, which is
precisely why the card records the door and not the intention. No packaged identity exists for a user-supplied key file,
so a present file stays `checked: "unknown"`.

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

- **An empty `memory_types` is the commonest claim, and it is a claim.** Most cores fill no id at all, so RetroArch
  writes them no save file — the reading of one batch of ten found nine. `[]` records that; a system the record leaves
  out records the opposite (nobody looked). The answer is a **declared set of no files**, which `file_set.state` keeps
  apart from the unknown an unrecorded core gets, and it travels with `core-own-writes-unestablished`: the emptiness is
  a fact about the _frontend_, never a claim that the content has no save. DeSmuME fills no id and still keeps DS saves
  somewhere; where, is a rule card's question. No granularity rides along either — there is no save here to group.
- **Why the system is part of the key.** A core is not one behaviour. mGBA answers a Game Boy cartridge's clock and a
  Game Boy Advance cartridge's not at all, so a record spelled `mgba` alone would have to be wrong about one of them.
  Where the caller did not name a system the resolver states nothing rather than picking one of a record's entries.
- **Which systems a record carries**: every system the frontend catalogue offers that core for, not only the one it
  leads with. A frontend lists several emulators per system and the user picks; an arcade library run on a FinalBurn
  romset is the ordinary case, not an exotic one, and a record that stopped at the leading entry would answer _unknown_
  for it while holding the reading that settles it. Each system still gets its own entry with the citation that covers
  it — where a core's reading branches by platform, the branch decides which entry a system copies (mGBA's Super Game
  Boy is Game Boy content and takes the Game Boy branch, clock file included).
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

## `content_tree_wiring.json` — the symlink pairs an arrangement's preparation promises

Read by `atlas.content_tree_wiring`, behind the `content-tree-unwired` health finding (issue #104). RetroDECK reaches
its two content hubs (`texture_packs/`, `mods/`) from each emulator by replacing the emulator-side directory with a
symlink — `dir_prep` creates hub tree and link **together**, and only on prepare, reset and folder moves; an in-place
upgrade runs version-gated patches that re-create only some pairs. The table records every pair one RetroDECK version
promises, each row citing the `component_prepare.sh` line in the shipped Flatpak that makes it.

This is **arrangement** knowledge, deliberately not on the texture or mods cards: a card states where the emulator
reads, and the installer's link target is provably not always that path — Citra's card derives `citra-emu/load/textures`
from the core's own literals while the installer links `saves/Citra/load/textures`, the very gap issue #98 tracks. A
health check built on card paths would probe paths the installer never linked.

A row is `family` (which hub), `hub` (the tree below that family's root), `base` + `path` (the emulator-side location:
`bios` and `storage` resolve from the marker, `xdg-data`/`xdg-config` are the flatpak's pinned homes), and `source`. The
check fails closed on every axis: a marker naming any version but the pinned one is measured against nothing, a hub tree
that does not exist files nothing, an emulator-side path whose `stat` fails supports no claim — and a link settling
_anywhere_ in the family's hub counts as wired, because older versions linked coarser layouts and those links still
route. Three absences are deliberate, spelled out in the file's own spec: PCSX2 standalone and MAME wire by
configuration value rather than by link, and the legacy rows in `component_update.sh` files are migrations for layouts
the pinned version no longer prepares.

## `launch_formats.json` — formats that need an installation step before anything launches

Read by `atlas.launch_formats`, behind the `needs-installation` verdict of the launchability question (issue #36). The
accept-list read off the machine states only that the frontend will not scan a file — never why. For some files the why
matters more than the no: a PSN `.pkg` is the distribution form of the content itself, RPCS3 has to install it into
`dev_hdd0` before anything can launch, and for digital-only titles no other form exists — so "not accepted, pick another
file" is the wrong advice. That is world knowledge about a platform, written nowhere on the machine, so it lives here:
keyed by the atlas system id and the exact extension token ES-DE would derive (case-sensitive, leading dot), each entry
with its statement and its citation. The table is consulted only where the extension is already outside the machine's
own accept-list — what the catalogue declares launchable, is launchable, and this file never overrides a read.

The `emulators` block is the per-entry half of the same question (issue #66): what a **standalone** launch entry's own
loader reads, keyed by the `%EMULATOR_…%` token the way every standalone card family is. It exists because the two kinds
of entry split along the boundary rule — a libretro entry's claims are read live off the installed core and its archives
RetroArch opens for it, while a standalone opens the file itself and refuses what its loader does not know, and that
knowledge is written nowhere on the machine atlas can read as text. `accepts` are extension tokens recorded lowercase
and matched case-insensitively (the gate is the emulator's loader, not ES-DE's case-exact scan); `archives` is whether
the loader opens containers at all — no RetroArch stands in front of a standalone to pick a file out of a zip. An
emulator absent from the block is one whose loader nobody has read: the answer states `entry-format-unestablished`,
never "refuses" — the same absence discipline the mods table spells out.

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

Schema 2 added `platforms`: each system's `<platform>` tags from the same build, read the way ES-DE reads them
(lowercased, `readList`-split, `ignore` clears the list). This is the snapshot column the platform questions fall back
to when a system's tags cannot be read live — a sealed catalogue's derived systems, a system this installation does not
declare — and every such answer says `tags_source: vocabulary` rather than passing the snapshot off as a read. The same
guard test holds this column to the deployed file, tag for tag.

**No foreign vocabulary belongs here.** Another product's platform identifiers — a library manager's slugs, a metadata
service's ids — stay out; the public vocabularies atlas translates (IGDB, libretro, the two scraper id spaces) live in
`platform_ids_crosswalk.json` below, keyed by platform rather than by system.

**The loader is fail-closed**: an unreadable schema, an empty list, an entry that is not a non-empty string, a repeated
id and a platform column that does not cover exactly the id set each fail the load. A vocabulary whose own account of
itself does not add up would answer "not an id" for names that are, and a caller validating against it would delete the
mapping that was right.

## `platform_ids_crosswalk.json` — the public identities of each platform

What a **platform** — the family a `<platform>` tag names, `snes`, `arcade`, `ps4` — is called in the public
vocabularies: its IGDB platform records, its libretro database names, and the ScreenScraper / TheGamesDB numeric ids
ES-DE's own scrapers use. Read by `atlas.platforms`; regenerated by `scripts/generate_platform_crosswalk.py` from pinned
sources (ES-DE v3.4.1 for the vocabulary and the two scraper maps; RomM 5.1.0's maintained tables for the IGDB and
libretro identities), with the platform-to-identity join hand-curated in that script — no upstream publishes it, and the
namespaces are not transformable by rule (`cdimono1` vs `philips-cd-i`).

```json
"gba": {
  "igdb": [{ "id": 24, "slug": "gba", "name": "Game Boy Advance" }],
  "libretro": ["Nintendo - Game Boy Advance"],
  "screenscraper": 12,
  "thegamesdb": 5
}
```

Three shape rules carry the meaning. The IGDB **numeric id is the stable key** — IGDB renamed platform 117's slug from
`philips-cd-i` to `philips-cdi` under the same number, so the slug and name are conveniences recorded at the pin, never
keys. The identity lists are **family-canonical overlaps**: the public platforms whose games belong on this ES-DE
platform (`snes` carries IGDB's `snes` _and_ `sfam`; `naomi` carries `arcade`), which is why one IGDB id may answer
several platforms and the consumer sees them all. And an **empty list or null is a decided absence** at the pinned
revisions (game engines, fantasy consoles, hardware IGDB does not carry) — the generator refuses a platform it cannot
place, so nothing here is a fallthrough. One upstream error is excluded by hand and documented in the generator: RomM
5.1.0 names the PC Engine playlist as PC-88's libretro entry, and a known wrong value is not world knowledge.

The machine-read half — which systems declare which platform, and whether they are declared, disabled or absent on this
installation — is never tabled here; the resolvers read the catalogue's own `<platform>` tags live.

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
