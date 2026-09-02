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
started; melonDS opens a relative `SaveFilePath` the same way, and xemu opens a relative `[sys.files]` value the same
way. Such a mode's answer is always the `<cwd>` template with the `cwd` hole in `needs` — the file names are stated, the
directory is the caller's to fill — and it rides the `save-dir-launch-dependent` caveat. Nothing on the machine is read
for it: a template names nothing to observe.

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
`tests/test_anchor_tripwire.py` (empty today: even LRPS2's source-level settings key pins its own last segment). A
configured directory contributes **three** names to that vocabulary — the section, the key and the compiled default —
because a key that survives a rename of the section around it reads nothing at all, which a tripwire watching only the
key would let through.

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
    "settings": "GFX.ini"
  },
  "provenance": { "source": "[V-binary/V-script] …" }
}
```

`settings` is **required** and is the one field here that is never read: it names the settings file that would establish
whether replacement is on, and every standalone answer carries `emulator-config-unread` pointing at it with `enabled`
left `None`. A card without one would tell a client the switch is unknown and give it nowhere to look. It is a **name**,
not an address — where that file lives is stated once in `emulator_settings.json`, below. `subdir` hangs off the
emulator's own directory, which is stated there too, so a row states the tree and never the emulator's name for itself;
`base` and `subdir` are otherwise held to the same rules as the core block's root and fragment, and `keying` to the same
cited-or-absent rule.

A card states its directory one of two ways, and exactly one. Most name a fixed subpath below an XDG base — the default
the emulator opens. PCSX2 names a **configuration key** instead (`textures.directory`: `[Folders] Textures`, with the
compiled default and a citation), because that is where its directory really comes from, and a `switch` beside it
(`[EmuCore/GS] LoadTextureReplacements`) makes `enabled` a live reading rather than `None`. The second shape needs a
resolver registered beside it in `atlas/installations.py`, and raises when the question reaches it without one, the same
way a save card does — the card loads, and the answer is where the two shipping out of step becomes visible: reading a
configuration is code, never a card DSL. PCSX2's answer also states the **load stage** rather than the root —
replacements are read from `<Textures>/<serial>/replacements` — because an answer naming only the root would send a
caller placing a pack one level above everything that reads it. Both readings are the _global_ ones: PCSX2 layers
`<DataRoot>/gamesettings/<serial>_<crc>.ini` over the whole configuration while that game runs, and every core key is
read through the layer, so the switch and the directory can each answer differently for one game. Which game runs is not
a fact atlas holds, so the answer keeps the global reading and carries `per-game-overrides-present` — with their count
and directory — where such files exist on the machine.

DuckStation names the same key (`[Folders] Textures`, default `textures`) for a different reason, and it is the one
worth learning from: its directory hangs off a **DataRoot the launch environment picks** — the config home where
`XDG_CONFIG_HOME` is set and absolute, else `~/.local/share/duckstation`. The card used to name `config` plus a subpath,
which is right on RetroDECK, whose flatpak sets the variable, and wrong on an EmuDeck AppImage, where the same
emulator's mod card already read the other root. One emulator's two answers naming two roots is exactly the state a card
exists to prevent, so the directory is stated as the key it is and read through the probe every question of this
emulator shares. It states no `switch`, so `enabled` stays `None` with the settings file that would answer it named.

Vita3K is still **absent on purpose**, and a test holds the absence down: it opens no default either — its texture tree
hangs off `pref-path` in `config.yml` — and that file is YAML, which atlas has no reader for and no runtime dependency
to gain one. Quoting the path the installer intended would state an arrangement's directory as an emulator's read
location, so the entry refuses with `standalone-unsupported`. The split inside the standalone kind is evidence rather
than policy, and it moves when the evidence does: PCSX2 sat on the refusing side until its configuration was read.

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

A card is deliberately **thin**: the name of the settings file that governs the emulator's save tree, the catalogue
systems the card answers for, and the provenance behind both. The file is **named, not addressed** — where it lives is
stated once in `emulator_settings.json`, because up to four questions of one emulator open it — and the base stated
there is the one the **emulator** opens it under, never the one an arrangement finds tidy to keep it in: a distribution
is free to put the real directory elsewhere and link it into place, and the read walks the link like any other. Naming
the storage side instead would answer correctly on the arrangement that happens to build that link and wrongly
everywhere else — which is precisely what a card exists to prevent, and what a single address makes impossible to get
differently wrong per question. Everything else — what `SlotA = 8` means, what an empty path key defaults to, how a
configured card path templates its region — is knowledge written nowhere on the machine, so it lives as cited code
beside the card (`atlas/installations.py`), the same split the rule cards make with `atlas/mode_rules.py`. The loader
refuses a card whose token has no resolver registered.

### `citations` — the evidence a shared reading speaks

A reading can serve two emulators that are not the same source. PrimeHack is a Dolphin fork with Dolphin's save shape,
read by Dolphin's resolver, and every file it inherits sits at different lines in its own tree. So the line ranges an
answer names — the `.gci` naming rule behind a `file-names-unestablished` caveat, the NAND's title tree, the compiled-in
slot defaults behind an unset key — are stated by the **card**, keyed by the slot the code asks for, and the resolver
speaks the card's rather than its own.

They go one step further, for the same reason the emulator's own directory does: a citation belongs to a **build**. The
PrimeHack revision RetroDECK's component is built from and the one Flathub ships are three years apart, and five of the
seven lines a save answer names differ between them — so the reserved `installations` key states one full set per
flatpak app id:

```json
"citations": {
  "build": "shiiion/dolphin 81bfb96",
  "nand_tree": "NandPaths.cpp:49-58",
  "installations": {
    "io.github.shiiion.primehack": { "build": "shiiion/dolphin 53f53e0", "nand_tree": "NandPaths.cpp:63-71" }
  }
}
```

An override must state **every** slot the default does: a partial one would answer one sentence with two builds'
evidence. Asking for a slot the card does not state raises rather than falling back on a sibling card's, and `flatpak`
has no default at the call — a reading that simply forgot it would name the arrangement's own build's lines for somebody
else's answer and look exactly like a verified one. Two tests cross the slots with the code: every slot a reading names,
its card states, and every slot a card states, some reading names.

Eleven cards today, each at the release RetroDECK ships. Dolphin (2603a): GameCube card slots read from `Dolphin.ini`'s
EXI device ids, the GCI folder and raw card schemes as region-keyed templates with the `region` hole, and the Wii NAND's
unnamed `title/` tree. PrimeHack (shiiion/dolphin 81bfb96, and 53f53e0 for the Flathub build EmuDeck installs): the same
reading, the same shape, and its own evidence — the fork inherits Dolphin's save tree whole and hangs it off a user
directory whose name belongs to the build (#246). PPSSPP (v1.20.4): the Linux memstick is compiled in — the card's
`settings` is `null`, the honest spelling of "no file governs this" — and savedata is one unnamed directory per game
below `PSP/SAVEDATA`. xemu (v0.8.135): every save lives inside the emulated Xbox hard disk named by `xemu.toml` — read
under the **data** home, where the emulator opens it — stated as one shared file with the `save-inside-image` caveat
carrying the inside layout (`UDATA/<title id>`), the EEPROM beside it as a named settings group; a **relative**
`[sys.files]` value is opened by the process verbatim (composed unchanged into the QEMU options, system/vl.c:2983-3095,
probed with plain fopen/access, vl.c:2527-2535 with osdep.h:645-653, and no launch step chdirs, ui/xemu.c:1278-1379), so
it anchors at the launching process's working directory and the answer takes the `working_directory` shape above, per
value — a relative EEPROM beside an absolute disk keeps the disk's root and carries its own `<cwd>` group, the hole in
`needs` either way. Cemu (2.6): the MLC resolved the way the emulator resolves it (`--mlc` flag outranks `settings.xml`
outranks the default), and the per-title unit templated below it — `usr/save/<save_id>`, granularity
`per-game-directory`, the fill spelled in the caveat (nn_save.cpp:133-145). Azahar (2125.1.1): the emulated SD read from
`qt-config.ini`'s `[Data Storage]` group the way the emulator reads it (`use_custom_storage` routes `sdmc_directory`,
`\default` companions honored — ReadSetting, config.cpp:1442-1450), the per-title unit
`Nintendo 3DS/<ID0>/<ID1>/title/<save_id>/data/00000001` below it with compile-time all-zero ids (archive.h:22-24), and
the extdata tree stated beside it as its own group. DuckStation (the "Legacy" build RetroDECK froze from the 2024-09
rolling release; citations at stenzek/duckstation@64655818e): two memory-card slots, six modes each — the Dolphin GC
shape in DuckStation's vocabulary — a per-game `<name>_<slot>.mcd` below `[MemoryCards] Directory` keyed by serial,
sanitized title, or the content file's own stem (which the resolver fills itself), a shared card at its configured or
default path, and the DataRoot probed on both spellings the launch environment can pick (qthost.cpp:562-582). PCSX2
(v2.6.3): up to eight slots — two console ports and six multitap slots that join only when enabled — each card's type
read off the disk the way `FileMcd_SetType` reads it: a directory at the card's full path is a folder card (per-game
saves as auto-managed subdirectories, stated with its names refused), anything else the shared `.ps2` image it is; one
config-side DataRoot either way (Pcsx2Config.cpp:2197-2217), completing the ps2 pair beside the LRPS2 core rule. melonDS
(1.1): one `<rom stem>.sav` per game in the directory `[Instance0] SaveFilePath` names, the config read the way
`Config::Load` reads it — `melonDS.toml`, a missing TOML falling back to the pre-1.0 `melonDS.ini` line by line as the
built-in migration, an unparseable TOML yielding factory defaults — and the empty default landing the save beside the
ROM itself (root `content_directory`); the stem is filled from named content, held open as `<rom_stem>` for archives,
whose saves are named after the file inside. RPCS3 (build 7c6b3dcd): the save tree hangs off the emulated PS3's internal
drive, and `vfs.yml` states where that drive lives — `/dev_hdd0/`, composed off `$(EmulatorDir)`, which `cfg_vfs::get`
replaces everywhere it appears and which means the emulator's config directory when empty (vfs_config.cpp:14-62). It is
the first card read through the YAML scalar reader (`atlas.yaml_scalars`), which names the one key of that file it does
not read rather than guessing at it. Below the drive the unit is `home/<user>/savedata`, one directory per title id; the
active user is a runtime selection no file records, so **every user home that exists becomes its own group** and a
caveat says which ones were found — the same stance the Dolphin card takes with its region trees. Where none exists the
answer says so outright rather than presenting the compiled default as a home somebody found, and a tree that cannot be
listed carries `save-dir-unlistable` rather than a clause glued onto another caveat's prose. A second save location is
named and not walked: `savedata/vmc`, the virtual memory cards for PS1 and PS2 classics, which a sync walking only the
per-user tree would miss. It is a **directory**, so it is a group of its own with its names left open (`files: null`)
beside `file-set-spans-roots` — not `save-inside-image`, which means the answer named a file and nothing inside it is
addressable, the opposite of what is true here. Vita3K (build 3996, commit `cb1f592c`): one key carries the whole tree —
`pref-path` in `config.yml`, with everything the emulator keeps hanging off it as `ux0/…`, and saves at
`ux0/user/<user>/savedata`, one directory per title id (io.cpp:136-143). Its user segment states every user directory
that exists as its own group, the way RPCS3's does — but this emulator **does** write down the user it opened, as
`user-id` in the same `config.yml` (select_and_open_user, user_management.cpp:329-331), and the record reaches further:
`init_home` reopens the recorded user when the id is among the users the emulator itself listed and either the launch
names an app on the command line — which is how a frontend launches — or `user-auto-connect` is on, and otherwise the
user manager opens for the player to pick (gui.cpp:688-696); the list is built from the directories under `ux0/user`
whose `user.xml` loads, keyed by the file's `id` attribute or, lacking one, the directory name's stem (get_users_list,
user_management.cpp:83-97), and the emulator's own writes keep that key equal to the directory name (save_user,
user_management.cpp:145-158) — atlas reads each `user.xml` the same way, so the users it checks the record against are
the emulator's own list. So the answer's headline `dir` names the recorded user's tree where that listing holds it —
composed from the identity the user.xml states, created on the first save where no directory of that name exists yet —
with the recorded id stated beside every tree as a reading and as `configured_user` in the caveat; a recorded user the
listing does not hold moves nothing, the caveat's reason saying why (no tree of that name, a directory that is not set
up as that user, or a `user.xml` that could not be read), and a tree that could not be listed keeps claiming nothing. An
empty `pref-path` is a refusal rather than a guess: the emulator falls back to a default it derives at run time and
writes nowhere (config.cpp:189-190). A configuration that exists and cannot be read refuses the whole question with
`emulator-config-unreadable`; one that reads fine but states an absolute path only the emulator's sandbox can spell
refuses with `emulator-config-path-untranslatable`, the stated value carried in `data.path` — the caveat vocabulary's
`sandbox-path-untranslated` said as an outcome, for the routes where the whole answer hangs on that one path
(`data.path` is the primary; an aggregate refusal naming more than one file — xemu's save answer — also carries
`data.paths`, every untranslatable value, the disk image first and then the EEPROM). The answers root at
`emulator_directory` — no frontend hands a standalone emulator a save directory — except where the emulator's own
default walks into the content's. On EmuDeck a standalone emulator is identified by `%EMULATOR_…%` token or by an
allowlisted launcher script (`cemu.sh`, `azahar.sh`, `duckstation.sh`, `pcsx2-qt.sh`, `melonds.sh` and `vita3k.sh`
today), and either way the launch's binary variant gates the answer. Three variants are established: an **AppImage**
under `~/Applications` reads the host's own XDG tree; a **flatpak** whose app id the settings table names (`flatpak` on
the row — melonDS's `net.kuribo64.melonDS`, which `melonds.sh` runs outright, probing nothing) reads its own homes below
`~/.var/app`; and an **extracted binary** at `~/Applications/<Name>/<Name>`, which EmuDeck unpacks some emulators into
(Vita3K) and which ES-DE's own find rule looks for right after the AppImage patterns, reads the host's tree like an
AppImage does. The rest refuse with `standalone-variant-unestablished`. The id lives in the table rather than on a card
because the gate is one question about the launch and every card family asks it — while it sat on the **save** card, an
emulator without one could reach no trees at all, which is what MAME's savestate answer was refusing over (#288).

## `standalone_savestates.json` — which standalone emulators the savestate question answers for

Read by `atlas.standalone_savestates`, keyed like the save cards beside it, and dispatched the same way: the savestate
question answers a standalone catalogue entry exactly where a card here covers its emulator and the entry's system, and
refuses with `standalone-unsupported` everywhere else — byte-identically to the blanket refusal that preceded the family
(#225), because an absent card is the same absence it always was. On EmuDeck the answer runs through the same launch
identity and binary-variant gate as the save answer, so the two questions about one entry can never disagree about which
binary runs; which flatpak app id that gate reads trees under is stated once in `emulator_settings.json` — this file
deliberately has no `flatpak` field, because a copy could only drift from the first.

A card makes its statement one of five ways and exactly one (#284 widened the standalone texture cards' two-way rule):
`base` plus `subdir` for a compiled join below the emulator's own directory — Dolphin's and PrimeHack's `StateSaves`
under the data tree, PPSSPP's `PSP/PPSSPP_STATE` and RPCS3's `savestates` under the config tree, Azahar's `states` under
data — or a `directory` setting (section, key, compiled default) for an emulator whose configuration names it: PCSX2's
`[Folders] Savestates` (default `sstates` below the DataRoot), melonDS's `[Instance0] SavestatePath` (default empty —
the state lands beside the ROM), DuckStation's `[Folders] SaveStates` (default `savestates` below the probed DataRoot).
The two ini-kept keys are matched the way SimpleIni matches them — ASCII case-insensitively, last occurrence winning —
which is the fact the family's first question turned on: RetroDECK writes PCSX2's key spelled `SaveStates` while the
source reads `"Savestates"` (Pcsx2Config.cpp:2284 at v2.6.3), and the written line governs.

The three #284 shapes state what no directory statement can spell. `inside_image` (xemu): the states are QEMU internal
snapshots written INTO the qcow2 that `[sys.files] hdd_path` names — no file per state exists — so the answer names the
image and the `savestate-inside-image` caveat carries it with the entry naming (user-chosen, else `vm-YYYYMMDDhhmmss`);
a relative `hdd_path` anchors at the launching process's working directory exactly as on the save route, so the answer
roots at the state family's `working_directory` kind and the image stays a `<cwd>` template inside the caveat.
`launch_ini` (MAME): the governing `mame.ini` is addressed by the launch command's `-inipath` (else the shipped builds'
compiled `$HOME/.mame;/app/share/mame/ini` search path — the Flathub build define, byte-proven in the shipped binary,
not upstream's `#ifndef` fallback; the `/app` element resolves against the running deploy, which for RetroDECK carries
no `share/mame` at all), the one case `emulator_settings.json` deliberately cannot state, so the card names the file and
the keys (`state_directory` default `sta`, `statename` default `%g`) and the resolver reads them with MAME's own
grammar; every MAME answer carries `savestate-support-machine-dependent`, because `MACHINE_SUPPORTS_SAVE` is compiled
per driver and an unflagged machine still writes the file with a warning. `absent` (Cemu, Vita3K, Ryubing, Ruffle,
GZDoom, ironwail, OpenBOR, PICO-8, Solarus): the emulator has **no savestates**, stated as a cited fact — the answer
serializes as `no_savestates` with the citation, an answer and never a refusal; an absence card states no names, no
settings and no anchors, registers no resolver, and answers before the EmuDeck variant gate, because the fact is the
emulator's and not the launch's. Where no shipped build pins the claim (nothing ships Ryubing or ironwail; PICO-8's
binary is the user's own), the card's `build_unestablished` sentence rides the answer as the `unverified-version`
caveat, the arrangement's evidence caveats ride it like any placement's — a stated no is world knowledge pinned to a
verified arrangement's build — and so do the entry's catalogue-status and per-game-override caveats, because a gamelist
that would launch a different emulator for this game is a statement about emulator identity, not about a path.
Tree-derived caveats (health findings, link walks) stay off, because the absence names no path for them to qualify. The
source ports' savegame and quicksave trees are the **save** question's business and their cards say so — a Doom savegame
is not a machine snapshot, and the two questions stay unblurred.

`names` is the field the save cards never needed: every one of these emulators names its states itself, from an identity
of the running game — PCSX2's `<serial> (<crc>).<slot>.p2s`, Dolphin's `<game_id>.s<slot>`, PPSSPP's
`<disc_id>_<disc_version>_<slot>.ppst`, RPCS3's per-title `<title>/<title>_<prefix>_<id>.SAVESTAT` directories, Azahar's
`<program_id>.<slot>.cst` — that no content path derives, so the card states the pattern with its citation and the
resolver hands both over in the `file-names-unestablished` caveat (`data["pattern"]`, `data["citation"]`) instead of
listing files nobody can name. melonDS is the one exception and the one derived set: `<rom stem>.ml1`–`.ml8`, concrete
where content is named, the `<rom_stem>` hole held open for archives — its `.sav` answer's shapes exactly, read through
the same `Config::Load` chain (TOML, the pre-1.0 INI migration line EmuDeck still writes, factory defaults for an
unparseable TOML).

`citations` follow the save cards' rule (#246): the shared fixed-tree resolver speaks the card's `build`/`tree`/`names`
slots rather than its own line numbers, per build where the builds differ — PrimeHack's `installations` block cites
shiiion/dolphin 81bfb96 for RetroDECK's component and 53f53e0 for the Flathub flatpak EmuDeck installs. The bespoke
readings (PCSX2, melonDS, DuckStation, xemu, MAME) serve one emulator each and carry their citations inline, like their
savefile twins. Anchors ride the byte tripwire the texture and mod tables ride (#105): every recorded word —
`StateSaves`, `PPSSPP_STATE`, `Savestates`, `sstates`, `SavestatePath`, `hdd_path`, `state_directory`, `statename`, the
settings file names — is re-read raw from the shipped binary it was verified in, with one curated opt-out: `mame.ini` is
composed at run time from `get_configname() + ".ini"` and exists nowhere in the binary as bytes.

## `standalone_firmware.json` — what a standalone emulator expects beside its content

Read by `atlas.standalone_firmware`, keyed like the save cards beside it, and consumed by the firmware questions: a
carded standalone catalogue entry answers `declaration: "packaged"` with real requirements instead of refusing
`unsupported`. The card states what no machine read can recover — which file the emulator probes and where, established
from its source at the shipped release — and everything about this machine stays live: the destination is the join the
emulator performs against the bases its launch reads, resolved through symlinks, and `found` is what actually sits
there. A card names the **emulator's probe**, never an installer's staging spot: Cemu (2.6) reads its keys at
`GetUserDataPath("keys.txt")` (KeyCache.cpp:63) — RetroDECK links `bios/cemu/keys.txt` to exactly that path, while
EmuDeck's installer parks a found `keys.txt` in the config directory the Linux build's key probe never reads, which is
precisely why the card records the door and not the intention. No packaged identity exists for a user-supplied key file,
so a present file stays `checked: "unknown"`.

A card states its probes one of three ways, and exactly one of them: `files` names fixed paths below an XDG base (Cemu's
`keys.txt`), `config_files` names the **configuration keys whose values are the paths** — melonDS (1.1), whose seven
BIOS, firmware and NAND keys point wherever the user pointed them — or `search` names a **directory to look in**, for
the emulator that names no file at all (DuckStation, below). Either of the latter two needs a resolver registered beside
it in `atlas/firmware.py`, and raises loudly when the question reaches it without one, the same way a save card does —
at query time, not at load: which keys a launch probes at all is the emulator's own live decision, not something a card
can spell. melonDS's `verifySetup` (EmuInstance.cpp:633-667) asks two switches — `Emu.ExternalBIOSEnable`, whose
compiled default is **off** because the emulator carries a built-in replacement, and `Emu.ConsoleType`, where DSi mode
requires its BIOS pair and NAND either way. With the switch off the answer is an empty requirement list plus
`firmware-builtin-replacement` naming the switch; the two arrangements sit on opposite sides of it, RetroDECK switching
it on and EmuDeck shipping `ExternalBIOSEnable=0` beside all seven configured paths.

PCSX2 (v2.6.3) is the second `config_files` card, and its expectation takes **two** settings rather than one:
`[Folders] Bios` names the directory — the same `LoadPathFromSettings` shape the memory-card and texture directories use
— and `[Filenames] BIOS` names the image inside it. `FullpathToBios` combines them and composes nothing at all while the
name is empty (Pcsx2Config.cpp:2057-2062), which is the state a fresh install is in: RetroDECK points the directory at
its BIOS root and leaves the choice to the user. So an empty name is stated with `firmware-path-names-no-file` carrying
the directory a BIOS belongs in, rather than being passed over — a PlayStation 2 boots nothing until one is picked, and
that is worth saying.

xemu (v0.8.135) is the third, and its interest is which keys belong here at all. Four files sit in `[sys.files]` and
only three are firmware: the MCPX boot ROM, the flash image and the hard disk each refuse the start when missing, and
the shipped binary says so in whole strings. The fourth, `eeprom_path`, is left to the save answer on purpose — xemu
**generates** an EEPROM where none exists, and it holds the console's own settings, which the standalone save card
already states as a named settings group; claiming it here too would file save data under firmware and state one file
twice. The hard disk is the opposite case and is claimed by both answers deliberately: a console does not start without
one, and every save lives inside it, so each answer names it and says which aspect it means. Note also where xemu keeps
its settings — under the **data** home, not the config one — which is why a card's resolver receives both bases and
takes the one its emulator uses. A **relative** value has no destination here at all: xemu opens it relative to its own
process's working directory (verbatim into the QEMU options, system/vl.c:2983-3095; plain fopen/access probes,
vl.c:2527-2535 with osdep.h:645-653 at v0.8.135), and a firmware requirement's `path` is contractually the absolute
observed destination — so the file stays out of the requirement list and the `firmware-path-launch-dependent` caveat
carries the anchor as data: the key, the declared value, and the `<cwd>`-templated path the launcher's working directory
completes. The placement families state the same fact as their `working_directory` root; this is that fact in the
firmware grammar's own words.

DuckStation (the fork build frozen 2024-09-19) is the fourth, and the only `search` card: it names **no file**.
`[BIOS] SearchDirectory` names a directory — read the same `LoadPathFromSettings` way, so an unset value is `bios` below
the DataRoot — and three per-region keys (`PathNTSCU`, `PathNTSCJ`, `PathPAL`) may name an image inside it. One launch
reads exactly one of the three — the console region the disc sets decides (`GetBIOSImage`, bios.cpp:321-338) — so the
moment any key names an image the answer is a single alternatives group, each option carrying the regions whose launch
it serves, never several files stated as if one launch needed them all. Where they are empty, which is the state both
arrangements ship, the emulator keeps every file whose size is exactly one of three and recognises what is left by
**hashing it** against its own table (`duckstation_bios.json`, below). Three consequences are stated rather than
smoothed over. Without a content check there is nothing to answer with, so an unverified query gets the directory, a
count of accepted-size files and `firmware-search-unverified` — never a claim that a BIOS is there. An image the table
does not know still boots (`Using an unknown BIOS`), so it is the pick with `firmware-content-unidentified` beside it
rather than a fault. And where several images rank alike the emulator keeps whichever one the directory hands it last,
an order no read reproduces — `firmware-image-ambiguous`, which on the reference machine's 27 accepted-size files fires
over five equally ranked images.

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

### A tree is a fixed place, or a configured one

A tree states `subdir` — a fragment below the card's XDG base — or `directory`, the configuration key whose **value** is
the directory, and exactly one of them. DuckStation is why the second shape exists, and the reason is the one #250 made
expensive: its DataRoot is `$XDG_CONFIG_HOME/duckstation` where that variable is set and absolute, else
`~/.local/share/duckstation` (qthost.cpp:562-582), so a card naming either base outright would answer correctly on one
arrangement and wrongly on the other. The key is `[Folders] Cheats` with the compiled default `cheats`, read through the
same `LoadPathFromSettings` shape every folder of that emulator goes through:

```json
"DUCKSTATION": {
  "mods": {
    "trees": [
      {
        "directory": { "section": "Folders", "key": "Cheats", "default": "cheats", "citation": "[V-source] …" },
        "keying": { "value": "title", "citation": "[V-binary/V-source] …" }
      }
    ],
    "settings": "settings.ini"
  }
}
```

Such a card names **no** `base` — the root is what the configuration decides — and needs a resolver registered beside it
in `atlas/installations.py`, failing the load without one exactly as a texture card does. A **core** card may not state
a configured tree at all: RetroArch hands a core its root, so a setting of an emulator's own has nothing to name on that
side, and the loader says so.

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

### `settings` — optional here, unlike the texture cards

A standalone card's `settings` names the settings file that would establish whether loading is on, and its answer then
carries `emulator-config-unread` pointing at it. Here the field may be **`null`**, and exactly one shipped card uses
that (a test holds the count down): for Azahar nobody has established that any switch exists at all — not a core option,
not a CLI flag — so naming a file would signpost one that may govern nothing. That row states `enabled` as unanswered
and points nowhere, which is the weaker and the honest claim.

A **core** card may name one too, and that is where `emulator-config-unread` first rides a libretro row: the Dolphin
core's mod switch is not a core option but an ordinary `GFX.ini` inside the user tree the core builds, so the card gives
a `path` relative to the same root the trees hang off (no `base` — the root is not an XDG one).

MAME is **absent on purpose**, the same way Vita3K is absent from the texture table: its plugin directories are values
RetroDECK writes into `mame.ini` rather than defaults the emulator opens, and nothing here reads that file. PCSX2 goes
the other way in this block and **answers from a default**, because nothing writes `Folders/Patches` — the patches
directory stays the emulator's own default, which is a path join. (Its texture row does read a configuration, so the two
blocks now answer the same emulator by different means: the split runs on what has been established per question, not on
the emulator.) A core this file does not reach answers `mod-wiring-unestablished`; a standalone emulator it does not
reach answers `standalone-unsupported`.

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

## `emulator_settings.json` — how the emulator installs, its own directory, and one address per settings file

Where each standalone emulator keeps a settings file, and which flatpak app the arrangement installs it as, keyed by the
`%EMULATOR_…%` token and then by the file's own name. Read by `atlas.emulator_settings`.

This table exists because the address was being stated once per **question**. A save card, a texture card and a mod card
named one path between them — up to three copies — with a fourth as a constant in the firmware resolver, and nothing
said the four were the same file. Two of them had already drifted in shipped releases: xemu's save card named the config
home while its BIOS resolver read the data one (#250), and DuckStation's texture card named a fixed base while its mod
card read the DataRoot the launch picks (#256). Both were found by hand, on a real machine, after they shipped.

So a card names a file by **name** and the address lives here, once. What is here is only _where_:

```json
"DUCKSTATION": {
  "flatpak": {
    "app_id": null,
    "citation": "[V-script] EmuDeck installs it as the DuckStation AppImage below $emusFolder rather than a flatpak …"
  },
  "directory": {
    "name": "duckstation",
    "citation": "[V-source] the DataRoot is the duckstation directory below whichever base the launch picks …",
    "anchors": { "binary": "duckstation/bin/duckstation-qt", "names": { "duckstation": { "literal": "duckstation" } } }
  },
  "files": {
    "settings.ini": {
      "bases": ["config", "data"],
      "path": "settings.ini",
      "citation": "[V-source] the DataRoot is $XDG_CONFIG_HOME/duckstation where that variable is set …"
    }
  }
}
```

- `flatpak` is the **identity of the installation**: the app id whose per-app XDG trees below `~/.var/app` a flatpak
  launch of this emulator reads. It is required on every row, and states either an id or a **cited no** — EmuDeck
  fetches Cemu, Azahar, DuckStation, PCSX2 and RPCS3 as AppImages and unpacks Vita3K as a plain executable, so for those
  no app id is established at all. There is no default, on purpose: a row that simply left the key out would read as
  "installs no flatpak" while establishing nothing, which is exactly how MAME came to refuse a savestate question it
  could answer (#288). It lives here rather than on the save card because it is a property of the **installation** and
  of no single question — the save card could only ever state it for an emulator that had a save card, and MAME does
  not.
- `directory` is the emulator's **own directory** below whichever XDG base a thing of its hangs off, and every path in
  this table is stated below it. It is one fact rather than a prefix: Dolphin's `Dolphin.ini`, its `Load/Textures` tree
  and its `GC` cards all live below the directory Dolphin itself calls the user directory, so spelling it into each of
  them is how one name comes to be written down four times. The texture and mod cards' `subdir` hangs off it too, and
  the loader refuses a `path` that begins with it.
- The **key is the file's own name**, and the loader refuses a key that is not the last segment of `path`: two spellings
  of one file is the thing this table exists to prevent.
- `bases` is a **list** because one emulator's root is a property of its launch rather than of the emulator. DuckStation
  picks `$XDG_CONFIG_HOME/duckstation` where that variable is set and absolute and the data home otherwise, so a reader
  probes in the stated order and the file that exists speaks. Everyone else states one base, and asking such a file for
  its single location is what resolvers do; asking a two-base file for one raises rather than answering the first
  candidate, because that would be a guess dressed as an address.
- `citation` is required like every other recorded fact here.
- A row may state **only** its `flatpak`. MAME's does: which `mame.ini` governs a launch is the launch's own fact,
  decided by `-inipath` over a compiled search path, so its savestate card takes the `launch_ini` shape instead of
  naming an address here — but how EmuDeck installs MAME is a fact of exactly this file's kind. `directory` and `files`
  are one statement and come as a pair; a row that states neither them nor an app id says nothing and is refused.
- Two tests cross the table with the cards: every card names a file the table carries, and the table carries no file no
  card asks for. Together they are what makes a disagreement inexpressible rather than merely unlikely.

### When the directory belongs to the build rather than to the emulator

PrimeHack renamed its user directory from `dolphin-emu` to `primehack` and later renamed it back, and the two
arrangements ship builds from either side of that change: RetroDECK's component is built from a revision that spells it
`primehack`, and the Flathub flatpak EmuDeck installs from one that spells it `dolphin-emu`. Both trees exist, under
those two names, on a machine that has both. So `directory` may state one name per **installation**:

```json
"PRIMEHACK": {
  "directory": {
    "name": "primehack",
    "citation": "[V-source/V-binary] … CommonPaths.h:22 at shiiion/dolphin 81bfb96 sets NORMAL_USER_DIR \"primehack\" …",
    "anchors": { "binary": "primehack/bin/primehack", "names": { "primehack": { "literal": ".primehack/" } } },
    "installations": {
      "io.github.shiiion.primehack": {
        "name": "dolphin-emu",
        "citation": "[V-source/V-binary/V-live] … CommonPaths.h:21 at 53f53e0 sets NORMAL_USER_DIR \"dolphin-emu\" …",
        "anchors": {
          "flatpak": "io.github.shiiion.primehack",
          "binary": "bin/dolphin-emu",
          "names": { "dolphin-emu": { "literal": ".dolphin-emu/" } }
        }
      }
    }
  }
}
```

The key is the flatpak app id whose build spells it differently — the id the row's own `flatpak` names, which is how a
resolver knows which installation this launch runs. An override stating the default's own name is refused: it reads as
"established for this installation" while establishing nothing.

`anchors` is why a stated name cannot rot quietly. The name is a compiled-in constant, so the binary that carries it is
the only honest check, and the block is the same tripwire the texture and mod rows carry (#105) with one addition: an
anchor may name a `flatpak`, because a build living in an app of its own is not below RetroDECK's components tree. Where
two spellings exist, each build must carry its own literal **and none of the others** — which is what turns a rename
into a red test instead of an answer pointing at a directory nothing writes to. RetroDECK's build repository has already
moved to a PrimeHack revision that spells the directory the other way; the release it currently ships has not, and the
weekly canary is what will say so first.

What a file _means_ for a question stays with the card and the code that reads it — which keys govern a save tree,
whether a switch exists, how a legacy file is migrated. An emulator may have two: Dolphin keeps its save settings in
`Dolphin.ini` and its graphics settings in `GFX.ini`, and the cards name one each.

## `duckstation_bios.json` — what a PlayStation BIOS _is_, by content

The other half of the emulator that names no file. DuckStation identifies a BIOS by hashing it against a table compiled
into its binary, so answering "is one here" at all means carrying that table: **104 images**, each with the md5 of the
whole file, the console region it belongs to, and the priority upstream ranks it by. Read by
`atlas.duckstation.bios_table`.

The hashes cannot be read off the shipped binary. They are `constexpr` in upstream's source and compile down to byte
arrays, so a strings scan finds the descriptions beside them (`SCPH-1001, DTL-H1001 (v2.0 05-07-95 A)`) and nothing else
— which is why this table is generated from the source at the pinned revision instead.

Shape:

```json
{
  "_meta": { "generated_from": "...", "revision": "64655818e", "generated_at": "...", "images": 104 },
  "sizes": { "ps1": 524288, "ps2": 4194304, "ps3": 4089584 },
  "openbios": { "signature": "OpenBIOS", "offset": 120 },
  "images": [{ "name": "...", "region": "ntsc-u", "md5": "...", "priority": 10, "fast_boot_patch": "type1" }]
}
```

- `sizes` is the first half of the same recognition rule, not a separate fact: a file of any other size is skipped
  before a byte of it is read, which is what makes the search cheap and what makes a Saturn dump of exactly 512 KiB a
  candidate the hash has to settle.
- `priority` reads backwards from the word — **lower wins**. Launch-console images sit at 50, PS2 ones at 100 and PAL
  PS2 ones at 150, each de-prioritised for a reason upstream states in a comment beside the table.
- `region` is `ntsc-u`, `ntsc-j`, `pal`, or `any` for the images upstream marks region-less. A console of unstated
  region matches every image, and a region mismatch is a warning rather than a refusal — so a launch needs _an_ image,
  not one per region.
- `openbios` is the one image with no hash at all: the free replacement BIOS is recognised by an eight-byte signature at
  offset `0x78`. No read through atlas's seam reaches an arbitrary offset, so it is recorded as the limit it is and
  named in the caveat an unidentified image carries.
- The table ages with the emulator. It is pinned to the revision it was read at, and a build that ships a longer table
  recognises images this one does not — which is a stated limit of the answer, not a silent one.

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
  "_meta": {
    "generated_from": "...",
    "version": "6.0.0",
    "generated_at": "2026-02-27",
    "archive_identities_version": "2",
    "archive_identities_reviewed": "2026-09-02"
  },
  "files": {
    "scph5501.bin": { "md5": "...", "sha1": "...", "size": 524288, "kind": "file" },
    "neogeo.zip": { "md5": "...", "sha1": "...", "size": 1859335, "kind": "archive", "archive_reason": "romset" }
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
- `kind` — `file` or `archive`, on **every** entry. It says whether these bytes are comparable whole-file at all, which
  decides what a difference from them means: over a `file` a `mismatch`, over an `archive` a `not-comparable` that
  states no verdict. It is on every entry rather than only on the archives so that counting them is mechanical.
- `archive_reason` — on an `archive` and nowhere else, one of two words for why its bytes move apart from its content:
  - `romset` — a MAME-style BIOS or device set (`neogeo.zip`, `cchip.zip`, Flycast's `dc/*.zip` boards). The whole-file
    bytes follow the romset version **and** the merge mode it was built under (split / merged / non-merged), so two
    correct copies of one BIOS can hash differently.
  - `core-bundled` — a data pack or program archive released and versioned with the project that builds the core
    (`ecwolf.pk3`, `Dinothawr.zip`, `prboom.wad`, `scummvm.zip`, the three FreeJ2ME `.jar` builds). Its bytes can change
    with a core release. "Bundled" is about versioning, not shipping: the RetroDECK Flatpak carries `ecwolf.pk3` and
    `prboom.wad` under `rd_extras/` and not one `.jar`, and `freej2me-lr.jar` is declared as _required_ firmware, which
    is the mark of a file the user supplies.
- `_meta.archive_identities_version` / `_meta.archive_identities_reviewed` — the version and review date of that curated
  list, versioned the way `FIRMWARE_SYSTEM_OVERRIDE` is. The list itself lives in the generator; what lives here is the
  kinds it stamped, which is why the version rides along with them: a consumer vendoring an older table must be able to
  read the version that actually stamped it, and `atlas.firmware` takes it from here rather than compiling in a copy. It
  reaches an answer through the `firmware-identity-not-comparable` caveat's `data["table_version"]`.
- **24 of the 388 entries are archives**, and which 24 is a **table statement, not a heuristic.** `System.dat` pins an
  md5 over the whole file and says nothing about what that file is, so the list is atlas's own `[D]` reading, curated in
  `scripts/generate_firmware_hashes.py` (`ARCHIVE_IDENTITIES`). Each line names its own evidence, and there are four
  kinds of it: the declaring core's own `firmware<N>_desc` where a shipped `.info` declares the file; that core's
  `notes` / `description` where its `.info` declares no firmware but names the file in prose; _sibling of a declared
  file from the same release_, where the table carries an undeclared file beside a declared one under a name and size
  that place it in the same release (`freej2me.jar`, `freej2me-sdl.jar`); and — for `scummvm.zip` alone — atlas's own
  inference, where no shipped `.info` mentions the file and no declared sibling stands beside it, so the reading rests
  on the successor files the core declares instead. Three of the 24 lines rest on one of those last two routes, and each
  says so. Nothing in atlas decides an identity's kind from a file extension; the guard in
  `tests/test_firmware_hashes_data.py` is the only place a suffix decides anything about a firmware identity, and it
  fails until a new `.zip` / `.pk3` / `.wad` / `.jar` name has a reviewed line.

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

It has a second mode, and the two are mutually exclusive on the command line:

```sh
python scripts/generate_firmware_hashes.py --restamp
```

`--restamp` reads the committed table and rewrites only what the script states about it — the `kind` / `archive_reason`
pair and the three `_meta` fields the script owns (`version`, `archive_identities_version`,
`archive_identities_reviewed`) — copying every identity through untouched. Both modes write through the same serializer,
so neither produces a formatting diff. Which mode applies is decided by what changed: a change to `ARCHIVE_IDENTITIES`
or to the schema is a `--restamp`, because it must not wait on a fresh upstream checkout and must not smuggle an
identity change in beside it; new or changed identities from upstream are a `--database` run. `_meta.generated_at` is
carried through by a restamp — no upstream data was read, so a fresh date would be a claim about where the identities
came from.

`duckstation_bios.json` is generated the same way, from the emulator's own source at the revision its card pins:

```sh
git clone https://github.com/stenzek/duckstation ~/src/duckstation

python scripts/generate_duckstation_bios.py --source ~/src/duckstation --revision 64655818e
```

The firmware-hash generator takes `-o <path>` to write elsewhere; with no `-o`, each output goes to its sibling beside
this file, resolved relative to the repo root so the command works from any working directory. The DuckStation one takes
no destination at all: it produces exactly one packaged file, and a writable destination would put a filesystem write in
the hands of whoever composed the command line rather than in the package layout.

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
