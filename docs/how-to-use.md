# How to use atlas

A practical guide for plugin and tool developers. It shows the standard query pattern, every question atlas answers
today, and how to chain queries into the flows a save-sync client actually runs. The spec behind it is `DESIGN.md`; this
document only shows usage.

## Getting it

Atlas is pure Python with zero runtime dependencies — vendoring is a directory copy:

```bash
pip install --target py_modules/ /path/to/emu-atlas   # decky-style vendoring, from a checkout
# or, for development:
pip install -e .
```

```python
import atlas
```

Everything a client needs is in that namespace: the entry points, the handles, the answer types, the vocabularies and
the serializers. The machine seam, the parsers and the packaged-data loaders are port and tooling surface and live in
their own modules (`from atlas.machine import FixtureMachine`) — `docs/architecture.md` has the map.

## The standard query pattern

Every query follows the same five steps:

```python
installations = atlas.detect(home="/home/deck")   # 1. find what is installed
inst = installations[0]                           # 2. choose a handle — or ask all of them (see below)
health = inst.health()                            # 3. check health before trusting answers
answer = inst.savefile_location(core_so="mgba_libretro.so", content_path=rom_path)   # 4. ask the handle
for caveat in answer.caveats:                     # 5. read the caveats — always
    handle_or_log(caveat.code, caveat.data)
```

Step 2 is the caller's, and it is optional: atlas never picks a winner, but it will ask every installation for you and
label each answer with the handle it came from — see
[Choosing is optional](#choosing-is-optional--ask-every-installation).

Rules that hold for every answer:

- **Answers are frozen value objects.** Fields are data, not behavior; serialize any answer to plain JSON with the
  matching function in `atlas.contract`.
- **Caveats are the degradation channel.** Every stated limitation arrives as a `Caveat` with a stable `code` (part of
  the API contract — branch on it), machine-readable `data`, and a human `message` (not contractual — log it, never
  parse it). An answer without caveats is as good as atlas can make it; an answer with caveats is still an answer, just
  with stated limits.
- **A hole is not an unknown.** `needs` lists holes _you_ fill: `content_dir` from the content at hand, `library_name`
  when the core would not load, and `save_id` when the core names the save after the content's own id. Two more appear
  only on a content-less question about a core whose card keys a directory on the content — `rom_stem` (prboom names its
  save directory after the content's stem) and `content_dir_name` (the vitaquake2 family names it after the content's
  directory); name the content and the resolver fills both itself. One hole no content can fill is `cwd`: a
  `working_directory`-rooted answer (DeSmuME 2015 writes relative to wherever RetroArch was started) is always the
  `<cwd>` template, and only the launcher knows that directory. And `region` is the hole a region-keyed standalone
  answer keeps: which of Dolphin's per-region GameCube trees a game saves into is the disc's own region field, which
  atlas does not read out of content — a caller with metadata (a library server knows its games' regions) fills it and
  narrows the `<region>` template to one tree, while the groups list every tree either way. Every hole is filled from
  the content or the launch — a value the configs state is never one, because you could not supply it either; atlas
  resolves those itself or states a caveat. Holes are not confined to the directory: a declared file set can be a
  template too. An unknown is something atlas refuses to state — it never guesses to keep a field non-empty. And a
  declared **emptiness** is a third thing, distinct from both: a placement with `file_set.state == "declared"` and no
  files says no separate save file exists. When the `save-inside-content` caveat rides beside it (quasi88 writing
  straight into the loaded disk image, hatari writing a floppy back into its own file at eject), the loaded content file
  itself takes the writes — atlas will not hand you the ROM under a second name, so what to make of a content file that
  doubles as the save (back it up, copy it, leave it) is your decision, made on `atlas.CAVEAT_SAVE_INSIDE_CONTENT`. Its
  harder sibling is `atlas.CAVEAT_SAVE_WRITES_DISCARDED` (hatari with write protection on): the same declared emptiness,
  and this time it is the whole truth — nothing anywhere keeps the progress, and the granularity block beside it says
  which switch would change that. Branch on `atlas.HOLE_CONTENT_DIR` / `HOLE_LIBRARY_NAME` / `HOLE_SAVE_ID` /
  `HOLE_ROM_STEM` / `HOLE_CONTENT_DIR_NAME` / `HOLE_CWD` rather than the strings, the way you would on any other closed
  set here: every one ships per-value names beside its tuple (`atlas.ROOT_SAVEFILE_DIRECTORY` … in `atlas.ROOT_KINDS`,
  `atlas.GRANULARITY_SHARED_CARD` … in `atlas.GRANULARITIES`).
- **Pass `home` explicitly.** The caller knows which user it serves. A backend running as root must pass the target
  user's home; `os.path.expanduser("~")` is only correct when the process runs as that user.
- **Arguments follow one rule: the question's subject may be positional, everything else is keyword-only.** The subject
  is what the question is _about_ — the system in `emulators_for("n64")` and `firmware_for_system("gba")`, the core in
  `firmware_for_core("mgba_libretro.so")`. Modifiers never are: `verify=`, `content_path=`, `core_so=` on
  `savefile_location`, and the digests on `identify_firmware` all read as noise at a call site without their names.
  Passing a subject by keyword keeps working — the rule permits positional, it does not demand it.

## Finding installations

```python
installations = atlas.detect(home="/home/deck")
for inst in installations:
    print(inst.kind, inst.kinds, inst.root())
# retrodeck ('retrodeck',) /run/media/deck/Emulation/retrodeck
# emudeck ('emudeck', 'bare_retroarch_flatpak') /home/deck/Emulation
```

A machine can carry several arrangements at once; each answers only for itself (no cross-installation fall-through).
`kinds` can carry more than one description — EmuDeck _is_ a configured RetroArch Flatpak, so its handle claims both. An
empty list means nothing was detected.

A detected installation can be broken. Health is structural, never a boolean:

```python
health = inst.health()
if not health.ok:
    for issue in health.issues:
        print(issue.code, dict(issue.data))
# e.g. root-missing {'path': '/run/media/deck/Emulation/retrodeck'}  → the SD card is not mounted
```

A finding is a `Caveat` like any other — stable `code`, machine-readable `data`, human `message` — so the same branching
works on it. What each finding carries:

| code                       | `data`                                         | what it says                                                        |
| -------------------------- | ---------------------------------------------- | ------------------------------------------------------------------- |
| `marker-missing`           | `path`                                         | the config marker this arrangement is detected by is not there      |
| `marker-unreadable`        | `path`, `status`                               | it exists and its bytes could not be read (`status` is the read)    |
| `marker-invalid`           | `path`[, `key`]                                | it parsed to something unusable; `key` names the offending entry    |
| `root-missing`             | `path`                                         | the installation's own root is not an existing directory            |
| `saves-root-missing`       | `path`                                         | its saves root is not an existing directory                         |
| `config-unreadable`        | `path`, `status`                               | a bare RetroArch's `retroarch.cfg` could not be read                |
| `companion-config-missing` | `path`, `status`                               | EmuDeck's claimed `org.libretro.RetroArch` config is gone or broken |
| `catalogue-invalid`        | `path`, `problem`                              | an `es_systems.xml` ES-DE refuses its **whole** catalogue load on   |
| `content-tree-unwired`     | `family`, `hub`, `path`, `problem`[, `target`] | a texture/mods hub tree exists that no emulator-side link reaches   |

`catalogue-invalid` is the hardest of them (issue #100): a systems file that does not parse (`problem: "parse-error"`)
or carries no document-level `<systemList>` (`"missing-systemlist"`) aborts ES-DE's whole load — the frontend runs with
**no systems at all**, so the catalogue answers state exactly that (an empty enumeration with this finding beside it)
instead of enumerating layers the running frontend does not have. It fires only where atlas _read_ the file: a file
atlas cannot read may parse fine for the frontend, and stays the unknown it always was. It rides the `health()` question
and every answer whose question reads the catalogue — an answer that never opens the catalogue cannot state its parse
state, by the one-read consistency model.

`content-tree-unwired` is the upgraded-without-reset state (issue #104). RetroDECK files texture packs and mods in two
hub directories and reaches them from each emulator by replacing the emulator-side directory with a symlink — a pair its
preparation creates together, and an in-place upgrade re-creates only in version-gated patches. An installation upgraded
without a reset can therefore carry a hub tree whose emulator-side path is a plain directory of the emulator's own:
content filed in the hub then never reaches it, silently. The finding names the pair (`family` is `texture_packs` or
`mods`, `hub` the tree, `path` the emulator side) and how it is broken (`problem`: `"missing"`, `"not-a-link"`, or
`"diverted"` with `target` naming where the link actually settles). Three gates keep it honest: it fires only when the
marker names exactly the RetroDECK version the packaged wiring table was read at (any other version made promises atlas
never read), only for a hub tree that exists (nothing can be filed in one that is not there), and the wired test is
deliberately weak — a link settling _anywhere_ in the family's hub counts as wired, because older RetroDECK versions
linked coarser hub layouts and those links still route. Unlike `catalogue-invalid` it rides the `health()` question
alone: no other answer's question reads the wiring state.

The findings also travel **in the answers themselves**: every answer computed on a broken installation — a placement, a
catalogue answer, a systems listing, any of the four firmware answers — carries them in its own `caveats`, ahead of what
the query itself could not resolve, under these same codes with this same data. Nothing wraps them — branch on
`marker-invalid`, not on a category code with the condition buried in `data`.

They ride there whether or not they bear on what you asked: a finding is a true statement about the installation, and
atlas does not decide for you which of them matter to the question at hand. So an answer can be perfectly usable and
still carry findings — a broken marker degrades an installation without emptying its catalogue.

```python
atlas.health_contract(inst.health())
# {'ok': False,
#  'issues': [{'code': 'root-missing', 'data': {'path': '/run/media/deck/Emulation/retrodeck'}}]}
```

`ok` is the summary field, the same role `requirements_met` plays on a firmware answer. Inside `installation_contract()`
health is a _field_ rather than an answer, so it carries the `issues` list alone — empty means ok there.

## Choosing is optional — ask every installation

On a machine with two arrangements, "where does this save live?" has two true answers. `every_installation` puts one
question to all of them and hands back every answer, labelled with the handle that produced it, in detection order:

```python
everywhere = atlas.every_installation(home="/home/deck")     # detect(), then ask — same arguments, same probe order
for answered in everywhere.savefile_location(content_path=rom_path, core_so=core):
    if isinstance(answered.answer, atlas.Unresolved):
        print(answered.installation.kind, "refused:", answered.answer.code)
    else:
        print(answered.installation.kind, answered.answer.dir)
# retrodeck /run/media/deck/Emulation/retrodeck/saves/n64     — RetroDECK's shipped cfg sorts by content
# emudeck   /home/deck/Emulation/saves/retroarch/saves        — EmuDeck's is flat: two arrangements, two layouts
```

The refusal branch is not decoration here: whether an arrangement has the core you named is exactly the kind of thing
that differs between two installations on one machine, so this is the route where one answer and one refusal side by
side is the normal case.

Every question a handle answers, the aggregate asks all of them: `health()`, `savefile_location()`, `systems()`,
`emulators_for()`, `rom_location()`, `firmware_for_core()`, `firmware_for_system()`, `firmware_inventory()`,
`identify_firmware()` — same arguments, same answers. Each returns a tuple of labelled answers:

- `answered.installation` is the handle itself, not a copy of its identity: read `kind`, `kinds`, `root()` and
  `health()` off it, or ask it the next question (its `emulators_for`, then that entry's own `savefile_location`).
- `answered.answer` is exactly what the handle route returns for that question — the same `SavefilePlacement`,
  `SavestatePlacement`, `CatalogueAnswer`, `RomPlacement` or `FirmwareAnswer`, unchanged, with its own caveats. For the
  two save families it is also whatever refusal that handle would have given you directly: an `Unresolved`, for a core
  that arrangement does not have. Branch on the type before reading `dir`.

The aggregate resolves nothing itself. It merges nothing, drops no duplicates, and prefers nothing beyond detection
order (RetroDECK, EmuDeck, bare Flatpak, bare native): a machine that runs PPSSPP under two arrangements gives you
PPSSPP twice, once with each arrangement's wiring, and the label is which is which. Handing you both is not guessing —
picking one would be.

An empty result is `atlas.detect`'s empty result: nothing is installed. That is its only meaning, because detection
triggers on marker existence — a present-but-broken installation is still detected, still answers, and still carries its
health issues.

Already holding handles? Wrap them rather than detecting twice — `atlas.EveryInstallation(installations)`. Serializing
works the same way the answers do, label plus the question's own serializer:

```python
atlas.installation_answers_contract(everywhere.savefile_location(content_path=rom_path), atlas.savefile_placement_contract)
# [{'installation': {'kind': 'retrodeck', 'kinds': [...], 'root': ..., 'health': []},
#   'answer': {'dir': '/run/media/deck/Emulation/retrodeck/saves/n64', ...}}, ...]
```

The handle route underneath is unchanged: a consumer that has chosen — decky mostly asks RetroDECK directly — keeps
using it.

## What atlas has actually seen — `arrangement-unverified`

Every answer from an arrangement atlas has never observed on a live installation carries one caveat,
`arrangement-unverified`, with the installation kind in `data["kind"]`. Today that is every bare RetroArch (the Flatpak
and a native install); RetroDECK was verified against a running 0.10.9b installation, EmuDeck against a running
installation at backend commit `863ab69` (ES-DE 3.4.1), and their answers say nothing.

```python
for c in answer.caveats:
    if c.code == "arrangement-unverified":
        mark_as_derived(c.data["kind"])      # 'bare_retroarch_flatpak' | 'bare_retroarch_native'
```

What it means: **no machine running this arrangement has confirmed the wiring end to end.** What it does _not_ mean:

- **Not** that atlas guessed. The config chain is read the same way for every arrangement, source-verified against
  pinned upstream RetroArch — the caveat is about the missing observation, not about the reading.
- **Not** that the installation is broken. That question is `health()`, which deliberately stays free of this: an
  evidence note is not a machine defect, and an installation with no health issues is still `ok` here.
- **Not** a reason to refuse the answer. It is the same answer, with its evidence level attached — treat it the way you
  treat any derived fact.

It rides on every answer that carries caveats: `savefile_location()`, `systems()`, `emulators_for()`, the three firmware
calls, `identify_firmware()`, and the entry route's `EmulatorEntry.savefile_location()` — through the aggregate too,
since that delegates. The status is packaged data (`atlas/data/arrangement_evidence.json`), so the day an arrangement is
verified on a reference machine, the record changes and the caveat stops appearing; no client change is needed either
way.

## When the machine moved on — `arrangement-version-drifted`

An arrangement is verified against _one version of itself_. The record pins that version, the machine states the one it
runs, and when the two differ every answer carries `arrangement-version-drifted`:

```python
for c in answer.caveats:
    if c.code == "arrangement-version-drifted":
        show_pending_reverification(c.data["verified"], c.data["observed"])   # '0.10.9b', '0.11.0b'
```

What it means: **atlas's knowledge of this arrangement was confirmed against another version, and nobody has confirmed
it against the one running here.** What it does _not_ mean: that the answer is wrong. The configs are read the way
upstream reads them on either version; what is pending is the re-verification (`docs/re-verification.md`), not a
correction. It is not a health finding either — the installation is fine, atlas's record of it is what aged — and it
rides on the same answers as `arrangement-unverified`, once each.

The absence of this caveat is worth reading precisely: it means **no drift was established**, which is not quite "no
drift". The comparison runs only when both sides state a version, so an arrangement whose marker names none stays silent
rather than claiming a comparison nobody made. (Where a missing live version does decide something — a rule card pinned
to one — `unverified-version` says so at that point.) Nothing above applies to an arrangement that was never verified at
all: with no pin there is nothing to drift from, and `arrangement-unverified` is already the more general statement.

What "the version the machine states" is depends on the arrangement: RetroDECK states one in its `retrodeck.json`;
EmuDeck states none there, so its statement is the backend checkout's git HEAD, read as the two plain files under
`~/.config/EmuDeck/backend/.git` (the `HEAD` symref, then the loose ref — no git invocation). A machine where that read
stops — a missing or unreadable file, a ref packed away — states no version and stays silent, per the paragraph above.
The deployed ES-DE's version is part of the verification record's provenance, not of the runtime comparison: on disk it
only exists in `~/ES-DE/logs/es_log.txt` after a first launch, and a fresh installation has none.

## Arriving from a public platform id

Every question about a system — `emulators_for`, `firmware_for_system` — takes an id in **ES-DE's vocabulary**: `gb`,
`n64`, `dreamcast`. That is what a frontend catalogue on the machine declares, so it is what an answer can be about.

If your catalogue speaks a public vocabulary — IGDB, libretro's database names, ScreenScraper or TheGamesDB ids — atlas
translates it, and qualifies the translation by this installation:

```python
answer = inst.systems_for_platform("igdb", "dc")     # slug or numeric id, both match
# answer.platforms == ('dreamcast',)                  ← the crosswalk half: what the id corresponds to
# answer.matches   == (PlatformSystemMatch(system='dreamcast', status='declared',
#                                          platforms=('dreamcast',), tags_source='catalogue'),)
```

The question is answered from its two rightful places. What the id _corresponds to_ is world knowledge — a pinned,
source-cited crosswalk (`atlas/data/platform_ids_crosswalk.json`), keyed on ES-DE's **platform** vocabulary and, for
IGDB, on the **numeric id** (IGDB slugs drift: platform 117 renamed `philips-cd-i` → `philips-cdi` under the same
number). Whether the corresponding systems _are here_ is read off the machine: every catalogue system carries a
`<platform>` tag connecting it to its platform family (`sfc` → `snes`, `naomi` → `arcade`), so a system the user added
by hand translates without any table knowing it.

Each match's `status` says what your next step is, and the three never collapse:

- **`declared`** — this installation's catalogue lists it: place content now.
- **`disabled`** — the catalogue's text carries it only inside an XML comment (RetroDECK ships 31 systems that way):
  present and deliberately off — the user would have to enable it.
- **`absent`** — the vocabulary knows the system and this installation does not have it: don't place content here.

`tags_source` says where the tags came from: `catalogue` is a live read; `vocabulary` is the stated build's snapshot
column (what backs an `absent` system, and a sealed catalogue's derived systems). A value nothing maps to answers no
platforms plus the `platform-unmapped` caveat — placing content under the raw id would name a folder no catalogue
declares, which is exactly the quiet failure this question exists to prevent.

The reverse direction reads the system's own tags and translates them out:

```python
answer = inst.platform_ids("sfc")
# answer.platforms  == ('snes',)                      ← the tag, read live when declared
# answer.identities[0].igdb  == (IgdbIdentity(id=19, slug='snes', ...), IgdbIdentity(id=58, slug='sfam', ...))
# answer.identities[0].libretro == ('Nintendo - Super Nintendo Entertainment System',)
```

Two caveats state the tags a translation cannot use: `platform-unknown` (a token outside the platform vocabulary — the
same token ES-DE itself warns about and drops) and `platform-scraping-ignored` (the catalogue's deliberate `ignore`
opt-out). RomM's own slugs are deliberately not a vocabulary here: RomM publishes each platform's IGDB identifiers
itself, so a RomM client arrives via `igdb` without atlas learning a server product's private namespace.

**If you keep a private map anyway**, validate it — the target set is still the contract:

```python
atlas.from_esde_system("dreamcast")       # 'dreamcast' — a name that IS an id, echoed back
atlas.from_esde_system("sega-dreamcast")  # None — not an id, whatever it looks like
atlas.known_systems()                     # every id, sorted — the whole vocabulary
```

The id set and the crosswalk are packaged data, cited to pinned builds and guarded by tests. They move with atlas
releases, not with your code — so pin your atlas version and re-run your checks when you bump it.

## Where does this save live?

The direct route — you name the core:

```python
placement = inst.savefile_location(
    content_path="/run/media/deck/Emulation/roms/n64/Paper Mario (USA).z64",
    core_so="mupen64plus_next_libretro.so",
)
placement.dir          # '/run/media/deck/Emulation/retrodeck/saves/n64'  — concrete, holes filled
placement.root_kind    # 'savefile_directory' | 'content_directory' | 'system_directory' | 'working_directory'
                       # | 'emulator_directory' (a standalone emulator's own tree — see the entry route)
placement.needs        # ()  — nothing left for you to fill
placement.file_set     # what the save consists of — see below
placement.granularity  # a Granularity — how the save is grouped, plus every switch that selects it (see below);
                       # stated wherever the file set is, and None where atlas states no file set either
placement.fallback_dir # set when dir does not exist yet: RetroArch reverts here if it cannot create dir
placement.physical_dir # set when dir reaches its files through symlinks: the real backing path
placement.sources      # provenance — which config said what (prose, for debugging)
```

**Both save questions can refuse instead of answering, so branch on the type first.** When the core you name is not
installed on that arrangement — atlas read the directory RetroArch loads cores from and it is not in there — there is no
location to give, and inventing the directory a core that cannot run would use is exactly what atlas does not do. You
get an `Unresolved` with the code `core-not-installed`, the same word the firmware route uses for the same fact, and
`data["core_so"]` naming what you asked for:

```python
outcome = inst.savefile_location(content_path=rom, core_so="pcsx2_libretro.so")
if isinstance(outcome, atlas.Unresolved):
    ...  # outcome.code == atlas.UNRESOLVED_CORE_NOT_INSTALLED — this arrangement has no such core
else:
    outcome.dir
```

This is per arrangement, not per machine: on a two-arrangement machine one installation can answer while the other
refuses, which is what the aggregate route is for. A core that _is_ installed and will not load is a different case and
still answers with a placement — see `core-generation-unestablished` in the caveat table. So is a core directory atlas
could not read: nothing was established there, so nothing is claimed.

Without `content_path`, the answer is a template and `needs` names the holes:

```python
placement = inst.savefile_location(core_so="mupen64plus_next_libretro.so")
placement.dir    # '/…/saves/<content_dir>'
placement.needs  # ('content_dir',)
```

A hole is named once even when the template repeats it: with `savefiles_in_content_dir` _and_ sort-by-content the
directory really is `<content_dir>/<content_dir>`. The two positions are **not** the same string — the root is the ROM's
directory, the sort stage is that directory's _name_, so a ROM in `/roms/psx` lands in `/roms/psx/psx`. Pass
`content_path` and atlas fills both correctly; `needs` only tells you which fact is missing.

### What `content_path` may be

Pass the path the way RetroArch gets it, and atlas names the content the way RetroArch names it
(`runloop_path_set_basename`):

- **Content inside an archive** is `"<archive>#<entry>"` — `…/Pack.zip#Game.n64` is the ROM `Game` in `…/`, so its save
  is `Game.srm`, not `Pack.zip#Game.srm`. A `#` that is not preceded by `.zip`/`.7z`/`.zst`/`.apk` is an ordinary
  character in a file name.
- **A trailing slash** changes nothing (`…/Game.cue/` is `…/Game.cue`) — unless the last component carries no dot at
  all, in which case RetroArch derives no name and atlas says so (`content-path-unnamed`) instead of guessing.
- **A dot in a directory name** truncates the path there when the ROM itself has no extension: `/roms/My.Games/rom` is
  named `/roms/My` and the save lands one level up. That is upstream behaviour, mirrored deliberately.

### Reading the file set

`file_set.state` is one of three honest states — branch on it:

- `"observed"` — `files` are real basenames currently on disk. A snapshot, **not** the complete save: `complete` says
  whether a verified rule card closes the universe. **`complete` is reserved and always `false` today** — closing the
  universe means establishing which files the core can write at all for the active mode, and no shipped card's evidence
  goes that far yet. Treat `false` as "no completeness claim", never as "atlas checked and the set is open".
- `"declared"` — `files` are the names this configuration writes, from source-verified world knowledge rather than a
  look at the directory: either a rule card for a deviating core (Flycast's VMU set), or, for an ordinary core, the
  standard rule — RetroArch names the save after the content and writes only `.srm` and `.rtc`, and which of the two a
  core fills is recorded per core and system. A declared name may still be a **template** — see below.
- `"unknown"` — atlas refuses to guess; `files` is empty. Fall back to your own knowledge, and treat that fallback as
  yours, not as atlas's answer.

**A declared set can be empty, and that is not the same as `"unknown"`.** For most cores RetroArch writes no save file
at all — they fill none of the two memory ids — and the answer says so: `state == "declared"` with `files == ()`. Read
`state`, not the length of `files`: empty-and-declared means atlas established there are none, empty-and-unknown means
atlas has not looked. The declared emptiness always carries `core-own-writes-unestablished`, because it is a statement
about the **frontend** only: DeSmuME fills no memory id and still keeps Nintendo DS saves somewhere of its own. Treat it
as "nothing to fetch through RetroArch's naming rule", never as "this content has no save".

**Declared beats what is lying there, and that is the point.** For a core whose files are recorded, atlas does not look
in the save directory at all: a file under the content's stem is evidence about the _past_ — what a core option wrote
before it was switched, what another core left behind — and it cannot carry a claim about where this configuration
writes now. So a stale `Game.rtc` beside a core that cannot write one never appears in the answer.

Two consequences worth planning for. The set is an **upper bound over the system**: whether _this_ cartridge carries a
battery or a clock is a fact about the game, which atlas does not read, so treat the names as the files to look for
rather than files that must exist. And the standard rule needs to know **which system** is being asked about, because
one core is not one behaviour — mGBA answers a Game Boy cartridge's clock and a Game Boy Advance cartridge's not at all.
The catalogue route knows the system and gets the declaration; `savefile_location(core_so=…)` asked on its own does not,
and the set stays `"unknown"` rather than narrowing to a guess:

```python
entry = install.emulators_for("gb").entries[0]        # the catalogue names the system
entry.savefile_location(content_path=rom).file_set    # declared: ('Game.srm', 'Game.rtc')

install.savefile_location(content_path=rom, core_so="mgba_libretro.so").file_set   # unknown — no system
```

### Naming the system yourself

The catalogue is where a system normally comes from, and one arrangement has none: a **bare RetroArch** carries no
frontend catalogue at all, so `emulators_for` there answers `emulator-catalogue-unavailable` and nothing ever names a
system. Say it yourself and the answer follows:

```python
install.savefile_location(content_path=rom, core_so="mgba_libretro.so", system="gb")
#   file_set: declared ('Game.srm', 'Game.rtc')
install.savefile_location(content_path=rom, core_so="mgba_libretro.so", system="gba")
#   file_set: declared ('Game.srm',)          — same core, same ROM path, different system
install.savefile_location(content_path=rom, core_so="mgba_libretro.so", system="n64")
#   file_set: unknown ()                       — the record covers no such system; nothing is invented
```

`system` speaks the same vocabulary every other question here does — ES-DE's ids, `atlas.known_systems()` — and it is
never derived from the core. A core's own metadata says which systems it _can_ run (mGBA declares Game Boy, Game Boy
Color and Game Boy Advance); it cannot say which one the content in your hand is, and guessing would answer a Game Boy
question with Game Boy Advance behaviour. Name it, or accept the answer below.

**Without a system, a record still answers where its systems agree.** Most cores are one behaviour: of the 66 recorded
cores, 65 write the same files for every system they cover, so the answer holds whichever of them the content is. It
comes with `file-set-across-systems`, whose `data["systems"]` lists exactly the systems the claim is scoped to:

```python
c = next(c for c in placement.caveats if c.code == "file-set-across-systems")
c.data["systems"]      # 'gb, gbc' — the answer holds for these, and states nothing about any other
```

The one core where the systems disagree is mGBA, which is also the reason the records are keyed by system at all — it
answers a Game Boy cartridge's clock and a Game Boy Advance cartridge's not at all. There the set stays `unknown` until
you name one.

```python
fs = placement.file_set
if fs.state in ("observed", "declared") and not placement.needs:
    paths = [os.path.join(placement.dir, name) for name in fs.files]
else:
    paths = my_own_fallback(system)   # "unknown", or a name only you can complete — your table, your risk
```

### When one save is several kinds of file — `file_set.groups`

`dir` and `files` describe **one directory**, and for most answers that is the whole save. Some emulators write more
than one kind of thing, in more than one place, for one game: MAME keeps a machine's battery memory in `mame/nvram/`,
its dip switches in `mame/cfg/` beside an emulator-wide `default.cfg`, and hard-disk write-differences in `mame/diff/`.
Handing that back as one flat list would tell you the names and hide which of them are the player's progress and which
belong to every game at once.

`groups` is that decomposition, and it is **the complete list of places** — `dir` and `files` are one of them, the
first. Each entry carries its own resolved `dir`, its own `files`, and two fields that answer two different questions:

```python
for g in placement.file_set.groups:
    g.dir          # '/…/saves/arcade/mame/cfg'  — resolved, this group's own directory
    g.files        # ('default.cfg',)            — basenames within it, or None (see below)
    g.granularity  # whose is it: 'per-game-file' | 'per-game-files' | 'per-game-directory' | 'shared-card' | 'shared-file'
    g.role         # what is it:  'battery' | 'memory-card' | 'disk-diff' | 'high-score' | 'settings'
```

`per-game-directory` says the directory itself is the unit: everything below it belongs to one game, so a sync client
packs and moves the tree whole (Cemu's `usr/save/<save_id>` — the per-title MLC subtree — is the first). Its `dir` may
carry a `<save_id>` template, with the hole in `needs` and the fill spelled out in the answer's caveat, exactly as the
file-name templates do it.

One walk over `groups` reaches every directory the answer knows about. `placement.dir` and `placement.file_set.files`
stay exactly what they always were — the first group's directory and the names in it — so a client that reads only those
keeps working unchanged and gets the save's own state, which is the part cards state first.

**The two fields are separate because they are different facts**, and MAME is the case that proves it: `<machine>.cfg`
and `default.cfg` sit in one directory with the same role and differ only in whom they belong to. So neither field can
be read off the other, and a client needs both.

**The rule for a save-syncing client** is one line, and it is the reason `role` exists:

```python
mine = [g for g in placement.file_set.groups if g.role != atlas.ROLE_SETTINGS]
```

Take every role but `settings` and `notes` — dip switches and input maps are configuration, not progress, and notes
(`notes`: MAME 2010 keeps the debugger's per-machine comment files as `<driver>.cmt`) are the user's own words, worth
lifting deliberately — into a library's per-game notes, say — rather than copying blind as save data. And never copy a
`shared-card` or `shared-file` group between machines without thinking: those files belong to every game at once, so
restoring one game's copy overwrites every other game's state in them. A tool making a _complete_ backup takes them all;
that is the caller's decision, which is exactly why atlas names them rather than filtering for you.

**`high-score` is in that set, and it is the one role whose merge is different.** An arcade machine keeps one score
table for everyone who ever played it, so it is not one player's progress the way a battery save is. When two devices
have both played the same game, a battery save merges by taking the newer one — and a score table does not: neither
side's is stale, and the right answer is the higher entries from both. A client that treats every non-`settings` group
alike still does no harm here, because copying the newer table only loses scores rather than a save; a client that reads
the role can merge instead of overwrite. That difference is the whole reason it is not spelled `battery`.

**A directory can be stated without its files, and it is still a group.** Where an emulator writes save data under names
that follow from nothing atlas reads — MAME names a hard disk's differencing image after the disk's entry in the
machine's own ROM table, and its memory cards after an index chosen in the emulator's own interface — the group carries
`files=None`:

```python
for g in placement.file_set.groups:
    if g.files is None:
        take_whole_directory(g.dir)      # there is save data here and I cannot list it
    else:
        take(g.dir, g.files)
```

`None` is not `()`. An empty list would say _this directory holds nothing_, which is the one thing such a group does not
say. A group with `None` contributes nothing to the flat `files`, which keeps that list exactly the names you can look
for. The reason travels beside the answer as a `file-names-unestablished` caveat carrying `dir`, `role` and the
`citation` behind the reading — that is where the sentence a person reads lives, but it is no longer the only place the
directory appears, so one loop over `groups` is enough and there is no second structure to correlate.

**The granularity block is how you show "is the wanted option active, and where does it change".** `value` is the
grouping in force and `mode` names the rule-card mode behind it (an option value like `"enabled"` where one option
governs, the card's own mode name like `"per-game"` where a rule selects, `None` where no card speaks). `readings` is
one entry per switch that went into the selection — for Flycast one, for Beetle Saturn its two sharing switches, and for
hatari exactly the one write-protect option the loaded content's class made relevant:

```python
g = placement.granularity
for r in g.readings:
    r.key           # 'beetle_saturn_shared_int'
    r.value         # 'disabled'   — live-read; None where nothing states it and the provenance says what applies
    r.provenance    # 'core default: …' | 'retroarch-core-options.cfg: …'  — where the value came from (prose)
    r.options_file  # the file to edit to change this switch — change it, ask again, the new answer confirms
for a in g.alternatives:
    a.mode          # 'internal-shared'                       — another reachable mode
    a.options       # (('beetle_saturn_shared_int', 'enabled'), ('beetle_saturn_shared_ext', 'disabled'))
    a.values        # ('shared-file', 'per-game-file')        — every grouping in that mode, its own first
```

An alternative names the **full option combination** that reaches its mode, so "switch to per-game saves" is a concrete
edit of concrete keys in a concrete file, not a guess. One special value: `g.value == atlas.GRANULARITY_NONE` (only ever
beside `save-writes-discarded`) means this configuration keeps no save at all — the readings and alternatives are then
exactly the way out.

**A mixed mode states every grouping.** `granularity.value` is _one_ word — the first group's — and for the mode in
force that is exact, because `file_set.groups` carries every part with its own grouping and role. An alternative has no
groups to show, so its `values` lists every distinct grouping of the mode it names, the mode's own first: switching to
FinalBurn Neo's shared mode adds a card every game shares beside the per-game save, and
`('per-game-file',
'shared-card')` says so where a single word used to hide it. A client that wants one word reads
`values[0]`.

**Reading nothing of this keeps today's answer.** `groups` is empty unless a rule card decomposed the answer — every
observation, every unknown and every standard-rule declaration has none, and empty means _not decomposed_, never _no
files_. Where it is populated, `files` is still exactly the names lying in `dir`: every group under the first group's
directory, in order. So a card that splits one list into two by role moves no name out of `files`; the groups under
_other_ directories are the part you only see here.

### A file set can carry a hole

`needs` is the answer's holes, not the directory's alone. Some cores name a save after the content's own platform-native
id rather than its file name — Flycast in its `All VMUs` mode writes `<save_id>.A1.bin` … `.D1.bin` (one per connected
port; `VMU A1` mode moves port A1 alone), where `save_id` is the disc's product number read from the ROM's header. atlas
states that set in full, keeps the `<save_id>` token in the names, and lists `save_id` in `needs`:

```python
placement.dir        # '/…/saves/dreamcast'      — resolved, nothing left to do
placement.needs      # ('save_id',)              — one fact still missing, and it is a file-name fact
placement.file_set   # declared: ('<save_id>.A1.bin', '<save_id>.B1.bin', '<save_id>.C1.bin', '<save_id>.D1.bin')
```

So check `needs` before joining names onto `dir`, exactly as you already do before using `dir` itself. A template is the
whole answer atlas can give: identifying content is not locating a save, so atlas never opens a ROM to read an id out of
it.

**Composing the two.** Fill `save_id` from whatever supplier knows the platform's id scheme — for example
[argosy-sigil](https://github.com/rommforge/argosy-sigil), which derives platform-native ids from ROM binaries and
deliberately leaves the emulator-side prefix and suffix to its consumer. It is _one_ supplier, not a dependency: atlas
neither imports it nor assumes it, and it does not cover Dreamcast today, so this particular hole stays yours to fill
(the id is the 10-byte product number in the disc header, trailing blanks trimmed, with each of `/\:*?|<>` — the leading
space included — replaced by `_`). Where no supplier knows the id, an unfilled template still tells you the shape, the
count and the directory — enough to recognize the files once they exist.

**When there is no id, the names change — and atlas says so.** The emulator uses the id only if the content carries one:
Flycast reads it from a Dreamcast disc header, and arcade content (or a disc that states none) is named after the ROM
instead. atlas cannot tell those apart — reading a ROM's header is identification, not location — so it states the
id-keyed set and puts the alternative in a `filenames-content-conditional` caveat, machine-readably and already filled
as far as atlas can fill it:

```python
c = next(c for c in placement.caveats if c.code == "filenames-content-conditional")
c.data["files"]                   # '<save_id>.A1.bin, <save_id>.B1.bin, …'  — the stated set
c.data["files_without_save_id"]   # 'Game.A1.bin, Game.B1.bin, …'            — if the content has no id
c.data["files_established_for"]   # 'console'  — which content class the set itself was established for
c.data["citation"]                # the source behind both, for when you need to check
```

The branch rule is the same question as filling the hole: ask your id supplier: an id → the first set with `save_id`
substituted; no id for this content → the second set, which needs nothing from you.

`files_established_for` is the second half, and it is about _which files exist_, not how they are spelled: Flycast
connects four VMUs on a Dreamcast and two on a Naomi board, so for arcade content two of the four stated names never
appear. When the key is present, treat the list as established for that content class only — outside it, the set is a
shape, not an inventory.

**A save can lie under two roots — and every part is stated.** Where a mode moves only part of the save — Flycast's
`VMU A1` moves the first controller's VMU under the save root and leaves the other three plus the console flash behind —
the parts that stay carry their own directory and files: as entries in `file_set.groups` where the set is declared, and
always as one `file-set-spans-roots` caveat per part, whose `data` names the resolved `dir` and the `files`. Resolved
means resolved: a part that stays "in the system directory" reports the content's own directory on a machine whose flag
moved that root, the same way the answer's `root_kind` would. The flat `files` stays the answer's own directory, as it
always was — walk `groups`, or take the caveats' data, and no part is missed on any state of the file set.

**`root_kind` says which anchor won, and a card does not decide it alone.** A core whose card roots its saves in the
system directory (Flycast's shared VMUs) is not automatically anchored at `system_directory`: RetroArch hands such a
core the _content's_ own directory when `systemfiles_in_content_dir` is set or the key is cleared to nothing, and atlas
answers `content_directory` there — with `content_dir` in `needs` when you named no content. Where no config states the
key at all, the answer is RetroArch's platform default (`system` under the config tree), not a hole: a configured value
is never something you are asked to fill.

**The layering trap.** An id from such a supplier describes the platform's own structure, which is not automatically a
structure on the host. sigil's PS2 `save_id` (`BASLUS-…`) names a directory _inside_ a memory card image; whether
anything of that is visible as a file at all is decided by the emulator's mode — which is what atlas's `granularity`
answers (`shared-card` for LRPS2's default: one `Mcd001.ps2` for every game, no per-game path anywhere). Ask atlas
first, then decide whether the identifier is relevant to a filesystem operation at all.

### Placement caveats worth branching on

| Code                                        | Meaning                                                                                             |
| ------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `sorted-dir-missing`                        | `dir` does not exist yet; RetroArch creates it on first save or reverts to `fallback_dir`           |
| `sorted-dir-uncreatable`                    | a file blocks the sorted dir — `dir` already is the unsorted root, `fallback_dir` is `None`         |
| `dead-symlink`                              | the directory is reached through a dead link; nothing can land there                                |
| `symlink-loop`                              | the link chain never settles (`ELOOP`); nothing can land there either — check both codes            |
| `save-dir-unlistable`                       | the directory could not be listed (`data["path"]`): `file_set` is _unknown_, not "no saves"         |
| `per-game-override` / `…-overrides-present` | a per-game config changes (or could change) the layout                                              |
| `per-game-layer-unread`                     | whether any per-game config exists was not checked (`data["dir"]`, `data["key"]`) — not "none does" |
| `cfg-value-rejected`                        | the file sets a value the emulator cannot read, so the value it had keeps governing                 |
| `core-unaudited` / `core-suspect`           | no rule card for this core yet / options scan shows save-related keys nobody has verified           |
| `core-multi-option`                         | granularity deliberately unstated — depends on options atlas does not interpret (named in it)       |
| `filenames-content-conditional`             | the file set depends on the content: `data` carries the id-less spelling and the scope              |
| `file-set-spans-roots`                      | part of the save stays under `data["dir"]` (`data["files"]`) — also in `groups` when declared       |
| `file-names-unestablished`                  | save data lives in `data["dir"]` and its names follow from nothing atlas reads — back it up whole   |
| `file-set-across-systems`                   | no system was named; the set holds for every system in `data["systems"]` and for no other           |
| `core-unqueryable`                          | the core would not load, `library_name` unknown — a `<library_name>` hole may remain                |
| `core-generation-mismatch`                  | the recorded deviation names an option this core does not register — not applied, standard frame    |
| `core-generation-unestablished`             | the core could not be read, so its generation is unknown — the recorded deviation is not applied    |
| `core-option-value-unestablished`           | the core fits the card, but nothing states the value governing it — not applied, standard frame     |
| `core-mode-unestablished`                   | the card's selection rule could not decide (`data["reason"]` says why) — not applied                |
| `option-entry-retired`                      | the options file carries a key this core generation retired — the value there stopped applying      |
| `save-inside-content`                       | no separate save file exists: the loaded content file itself takes the writes — your call           |
| `save-inside-image`                         | the saves live inside `data["image"]` (a disk image), laid out per `data["layout"]` — whole-file    |
| `save-writes-discarded`                     | nothing keeps a save at all: the writes are discarded — the granularity block names the way out     |
| `save-root-redirected`                      | the emulator's own config routes saves to `data["path"]`, outside every root kind — do not skip     |
| `save-root-revoked`                         | a flatpak override revokes the tree the save root lives under — writes never land here on the host  |
| `save-root-unresolvable`                    | the save trees anchor at the frontend _process's_ state (its working directory) — `data` names them |
| `content-dir-observation`                   | the files were observed in the ROM's own directory — content files share the name, see below        |
| `content-path-unnamed`                      | the content path names no file; no file names stated, nothing observed                              |
| `marker-missing`                            | health: the config marker this installation is detected by is gone                                  |
| `marker-unreadable`                         | health: the marker exists and its bytes could not be read                                           |
| `marker-invalid`                            | health: the marker parsed to something unusable                                                     |
| `root-missing`                              | health: the installation's own root is not an existing directory                                    |
| `saves-root-missing`                        | health: its saves root is not an existing directory                                                 |
| `config-unreadable`                         | health: a bare RetroArch's `retroarch.cfg` could not be read                                        |
| `companion-config-missing`                  | health: EmuDeck's claimed `org.libretro.RetroArch` config is gone or broken                         |
| `catalogue-invalid`                         | health: an `es_systems.xml` ES-DE refuses its whole catalogue load on — no systems run              |
| `content-tree-unwired`                      | health: a texture/mods hub tree exists that no emulator-side link reaches — filed content is lost   |
| `unverified-version`                        | the rule card was never verified against this emulator version                                      |
| `arrangement-unverified`                    | this arrangement has never been observed live — the answer is derived (see above)                   |
| `arrangement-version-drifted`               | it was observed, on another version than this machine runs — re-verification pending                |
| `sandbox-path-untranslated`                 | a configured path exists only inside the emulator's Flatpak sandbox; nothing there was read         |
| `config-home-relocated`                     | EmuDeck entry route: a `portable.txt` may have moved ES-DE's tree — reads may not be in force       |
| `entry-format-unclaimed`                    | launchable: the running core does not claim this format — attempted, never refused                  |
| `archive-contents-unread`                   | launchable: RetroArch opens the container and picks by the core's claims — the inside is unread     |
| `entry-format-unestablished`                | launchable: what the running entry reads was never established — not the same as "refuses"          |
| `emulator-list-derived`                     | the entries/systems come from the installed cores' own `.info`, not a catalogue — no launch command |
| `platform-unmapped`                         | the asked id or system corresponds to no platform knowledge — don't invent a folder from the raw id |
| `platform-unknown`                          | a catalogue's `<platform>` token is outside the platform vocabulary — ES-DE warns and drops it too  |
| `platform-scraping-ignored`                 | the catalogue tags this system `ignore` — a deliberate opt-out, not a missing tag                   |

Treat caveat codes you do not recognize conservatively: the answer stands, but something about it is degraded.

The nine `health:` rows are the installation's own findings, riding here with the same codes and the same `data` that
`health()` reports — their `data` keys are in the table under [Finding installations](#finding-installations). The
marker, root and config findings travel in every answer, not just placements, ahead of what the query itself could not
resolve; the two findings with their own reads follow the one-read model instead — `catalogue-invalid` rides the answers
whose question reads the catalogue, `content-tree-unwired` rides `health()` alone. Do not key on the position: on the
entry route (`EmulatorEntry.savefile_location()`) the entry's own catalogue caveats precede them. Match on the codes.

`content-dir-observation` is the one to plan for if you sync files: with `savefiles_in_content_dir` the save lies next
to the ROM, and the observation matches everything there under the ROM's name — the remaining tracks of a `.cue`, the
cover art, the archive the ROM came in. atlas states the whole set rather than filtering by an invented list of content
extensions; `complete` is `false`, and deciding which of those files are yours to upload is the client's call.

### Where a standalone emulator's saves live

A standalone catalogue entry answers `savefile_location` where a **standalone save card** covers the emulator its launch
command names — Dolphin, PPSSPP, xemu, Cemu, Azahar, DuckStation, PCSX2, melonDS, RPCS3 and Vita3K today, keyed by the
`%EMULATOR_…%` token the way the texture cards are. An EmuDeck catalogue may name the token or run a per-emulator
launcher script — an allowlisted launcher (`tools/launchers/cemu.sh`, `melonds.sh`, …) reaches the same card — and
either way the launch's binary variant gates the answer. Three variants are established: an AppImage under
`~/Applications` reads the host's own XDG tree; an **extracted binary** at `~/Applications/<Name>/<Name>`, which EmuDeck
unpacks some emulators into (Vita3K) and which ES-DE's own find rule looks for right after the AppImage patterns, reads
that same host tree; and a flatpak whose app id the card names reads the app's own homes below `~/.var/app` — melonDS's
`net.kuribo64.melonDS`, which `melonds.sh` runs outright and probes nothing for, plus xemu, Dolphin and PPSSPP, which
EmuDeck installs as flatpaks and the probe finds among the installed ones. A variant whose config is not established
(the Windows build under Proton via `-w`, a flatpak no card names an id for) refuses with
`standalone-variant-unestablished` and the variant named in `data`. No frontend hands these emulators a save directory,
so the answer's `root_kind` is `emulator_directory`: the emulator's own tree, its shape read from the emulator's own
configuration the way the emulator reads it (Dolphin's `Dolphin.ini`, xemu's `xemu.toml`, Cemu's `settings.xml` — each
at the shipped release; PPSSPP's Linux memstick is fixed by the build, so its card names no config at all). Each is
looked for under the XDG base the **emulator** opens it from, which is not always where an arrangement keeps it — xemu's
`xemu.toml` lives under the data home while RetroDECK keeps the real directory in its config tree and links it into
place — and rerouted with symlinks the answer walks. The one exception is the emulator whose own default walks into the
content's directory — melonDS below.

```python
entry = inst.emulators_for("gc").entries[0]   # 'Dolphin (Standalone)'
p = entry.savefile_location()
p.root_kind          # 'emulator_directory'
p.dir                # '…/data/dolphin-emu/GC/<region>/Card A' — the GCI folder, one .gci per save
p.needs              # ('region',) — the disc's region picks the tree; fill it from your metadata
p.file_set.groups    # all three region trees, so you see the candidate set without filling anything
p.granularity.mode   # 'folder+none' — what sits in card slots A and B, read from Dolphin.ini
```

The granularity block is the same machinery the rule cards use: the readings name the switches that decided the mode
(`SlotA = 8` is the GCI-folder device) and the alternative names the one edit to the other scheme — `SlotA = 1`, the raw
card, where every game of a region shares one `MemoryCardA.<region>.raw` and the granularity says so. The Wii answer
(`system="wii"`) is the NAND's `title/` tree: one unnamed directory per title, `file-names-unestablished` carrying the
citation, and `physical_dir` pointing at the real tree behind the arrangement's symlink. A config that exists and cannot
be read refuses the whole question with `emulator-config-unreadable` — there is no standard frame to step aside to. A
config that reads fine but states an absolute directory only the emulator's sandbox can spell refuses with
`emulator-config-path-untranslatable` instead — the same fact the `sandbox-path-untranslated` caveat states where an
answer still stands around it, said as the outcome where nothing else anchors, with the stated value in `data.path`.
`data.path` is always the primary value; where one refusal covers several stated files (xemu's save answer, naming a
disk image and an EEPROM), `data.paths` additionally lists every untranslatable value — the disk image first, then the
EEPROM — whenever more than one is named. Savestates are their own wiring and their own card family
(`standalone_savestates.json`, #225): the same entry answers `savestate_location` through it, and an emulator without a
savestate card keeps the `standalone-unsupported` refusal there even where its save answers (since #284 every
save-carded emulator carries a savestate card too — for Cemu and Vita3K it is the stated no — so today that refusal
marks the rows neither family has examined).

Three more cards follow the same shapes. PPSSPP is one unnamed savedata directory per game below the memstick's
`PSP/SAVEDATA`. Cemu keys the per-title unit: `dir` is `usr/save/<save_id>` below the MLC — the MLC resolved the way the
emulator resolves it (an `--mlc` launch flag outranks `settings.xml`, and the answer says so when it sees one) — with
`needs: ('save_id',)`, granularity `per-game-directory`, and the caveat spelling the fill (the title id, high then low
word, each 8 lowercase hex digits, as two path segments) with its citation. That unit is what a per-game sync client
moves whole — the directory-based save shape rommapp/romm#3866 asks for. xemu is the boundary case: every game's save
lives **inside** the emulated Xbox hard disk, so the answer names the image as one shared file and `save-inside-image`
carries the inside layout machine-readably (`data["layout"]` is `UDATA/<title id>`) — a file-level client backs the
image up whole or leaves it, a tool that parses FATX has the layout stated instead of rediscovered, and per-game sync is
honestly not on offer from outside.

Vita3K's tree hangs off one key, `pref-path`, with saves at `ux0/user/<user>/savedata` — the same per-user shape as
RPCS3, and an empty `pref-path` is a refusal rather than a guess, because the emulator falls back to a default it
derives at run time and writes nowhere. The user segment reaches a stronger answer than RPCS3's, and the readings spell
out why. Vita3K _does_ record the user it opened, as `user-id` in that same `config.yml`, and the emulator honours the
record when the id is among the users it listed itself and either the command line names an app to run — which is how
both frontends start a game — or `user-auto-connect` is on; that list comes from the directories under `ux0/user` whose
`user.xml` loads, keyed by the file's `id` attribute or the directory name's stem, and atlas reads each `user.xml` the
same way, so the check is the emulator's own rather than a guess from directory names. So where that listing holds the
recorded id, a frontend launch reopens exactly that user, and **the answer's `dir` names its tree** — composed from the
identity, which the first save creates where no directory of that name exists yet. **Every user directory that exists
stays a group of its own**, with the recorded id beside them as a `user-id` reading and as `configured_user` in the
`core-mode-unestablished` caveat, whose `reason` says which case this machine is: the recorded user's tree is the one
named; the recorded user is not set up here (its directory exists, but no `user.xml` lists it as that user — the player
picks); the recorded user has no tree here at all; whether it is set up could not be read (a `user.xml` atlas could not
read); or the tree could not be listed and the answer claims nothing new. A plain launch of the emulator without
`user-auto-connect` opens the user manager whatever is recorded — the headline follows the launch a frontend makes.

RPCS3 is the one whose directory takes two steps to reach. `vfs.yml` maps the emulated PS3's internal drive
(`/dev_hdd0/`) to a host directory, composed off a `$(EmulatorDir)` variable the same file defines — empty means the
emulator's own config directory. Below the drive, saves are one directory per title id under `home/<user>/savedata`.
Which user the emulator runs as is a runtime selection nothing on disk records, so **every user home that exists is a
group of its own** and a caveat lists them: a machine with two accounts gets two trees rather than a guess at which is
in force. Where the tree holds no user home at all, the caveat says exactly that — the directory named is the one the
emulator would create on the first save, not one found here — and a tree that could not be listed carries
`save-dir-unlistable` of its own, so "which users exist is unknown" is something a client can branch on. Two more things
ride along: the per-title directories keep their names refused (they are the games' own), and a _second_ place saves
live rides as its own group — `savedata/vmc`, the virtual memory cards for PS1 and PS2 classics, outside the per-user
tree entirely, with `files: null` because what lands there has not been read, and `file-set-spans-roots` saying a sync
that walks only the per-user tree misses it. That card is also the first to read YAML, through a reader that names the
keys it does not read rather than guessing (`atlas.yaml_scalars`).

melonDS is the simplest card and the one whose default leaves the emulator's tree: one `<rom stem>.sav` per game where
`[Instance0] SaveFilePath` points, and an empty or absent value lands the save **beside the ROM itself** — the answer's
`root_kind` becomes `content_directory`, the directory a `<content_dir>` template until content is named. The resolver
fills the stem from a named content file itself; for a ROM inside an archive the hole stays open with the caveat saying
why (melonDS names the save after the file _inside_ the archive). The config is read the way `Config::Load` reads it:
`melonDS.toml`, a missing TOML falling back to the pre-1.0 `melonDS.ini` line by line — the file EmuDeck still writes,
so the migration door is one a real arrangement walks through — and an unparseable TOML yielding factory defaults rather
than a refusal, because that is what the emulator itself does.

## Where do this ROM's savestates live?

### Where screenshots go

`screenshot_location` answers where RetroArch writes its screenshots (issue #142) — its own question beside the save
ones, because a screenshot is not save data and must never land in a sync's file set:

```python
placement = inst.screenshot_location(content_path=rom, core_so="mupen64plus_next_libretro.so")
placement.dir        # '/…/screenshots/n64' — or the content's own directory
placement.root_kind  # 'screenshot_directory' | 'content_directory' — the question's own two words
placement.needs      # ('content_dir',) when content-rooted and no content was named
```

Both arguments are optional: the core matters only because a per-core or per-game override can move the screenshot keys.
Three things this answer deliberately does not carry: no `file_set` (the default names are dated,
`<stem>-YYMMDD-HHMMSS.png`, so no closed set exists to state), no `fallback_dir` (RetroArch creates the directory at the
moment of the shot and simply fails the shot when it cannot), and no `granularity`. The family's own quirk is the
`invalid-screenshot-directory` caveat: a configured directory that does not exist is cleared at config load rather than
created, and the shots land beside the content — the caveat names the configured spelling so a client can say which line
to fix. The question answers on the direct route and the aggregate; the catalogue-entry route does not carry it yet.

`savestate_location` is `savefile_location`'s twin, and it takes the same two optional arguments:

```python
placement = inst.savestate_location(
    content_path="/run/media/deck/Emulation/roms/n64/Paper Mario (USA).z64",
    core_so="mupen64plus_next_libretro.so",
)
placement.dir        # '/run/media/deck/Emulation/retrodeck/states'
placement.root_kind  # 'savestate_directory' | 'content_directory'
placement.file_set   # the states lying there — '<stem>.state', '<stem>.state1', '<stem>.state.auto', '.png' thumbs
```

It answers off the same configs through RetroArch's savestate keys (`savestate_directory`, `savestates_in_content_dir`,
`sort_savestates_by_content_enable`, `sort_savestates_enable`), and everything you know about save placements holds: the
same override chain, the same holes in `needs`, the same `fallback_dir` when a sorted directory does not exist yet, the
same `physical_dir` through symlinks, the same caveat codes for the same conditions. The entry route has it too
(`entry.savestate_location(content_path=…)`), and so does the aggregate.

Three differences are worth knowing, and all three are things the answer states rather than things you have to remember:

- **No `granularity`.** The field is absent, not `None`. It says how a _core_ groups the save data it writes, and no
  core writes a savestate — RetroArch serializes it and never tells the core where it goes — so there is nothing for a
  rule card to state, now or later.
- **`root_kind` speaks the question's own vocabulary.** A savestate is never anchored at the saves root and never at a
  core's system directory. On RetroArch's routes it is `savestate_directory` or `content_directory`; a standalone
  entry's answer (below) adds the savefile family's `emulator_directory` — the tree the emulator owns — and
  `working_directory` for melonDS's relative value.
- **The file set is narrower and sharper.** A savefile's extensions are the core's own, so the observation has to match
  everything under the ROM's stem; a savestate's names are RetroArch's own, so the observation matches `<stem>.state*`
  and nothing else. Two consequences: `content-dir-observation` does not appear on a state placement even when the
  states sit next to the ROM, and an input-movie `.replay` sharing the directory is not in the set. It is still an
  observation and never `complete` — how many slots were ever written is not written anywhere.

One caveat is this question's own:

| Code                          | Meaning                                                                                     |
| ----------------------------- | ------------------------------------------------------------------------------------------- |
| `core-savestates-unsupported` | this core's `.info` declares `savestate = "false"` — the directory resolves, states may not |

Take it as a warning, not a refusal. Two things override the declaration and atlas says so in the message: the cfg key
`core_info_savestate_bypass`, and a running core reporting a nonzero `retro_serialize_size()`, which nothing on disk can
answer. Where the `.info` could not be read at all you get `core-info-unreadable` or `info-path-unresolved` instead —
"atlas could not look" is never spelled as "states work here".

### Standalone entries answer through their own card family

A standalone catalogue entry answers `savestate_location` through `standalone_savestates.json` (#225), the savefile
family's twin: Dolphin, PrimeHack, PPSSPP, RPCS3 and Azahar keep a compiled states tree below their own directory —
`root_kind` is `emulator_directory`, `physical_dir` walks the arrangement's symlink — while PCSX2, melonDS and
DuckStation read the directory out of their own configuration (`[Folders] Savestates`, `[Instance0] SavestatePath`,
`[Folders] SaveStates`), through the same variant gate EmuDeck's save answers run. The ini keys are matched the way
SimpleIni matches them — ASCII case-insensitively — which is what makes RetroDECK's `SaveStates` spelling govern over
PCSX2's compiled `sstates`.

What no standalone card can give you is the file list: these emulators name their states from the running game's own
identity (PCSX2's `<serial> (<crc>).<slot>.p2s`, Dolphin's `<game_id>.s<slot>`, PPSSPP's
`<disc_id>_<disc_version>_<slot>.ppst`, RPCS3's per-title `<title>/<title>_…SAVESTAT` directories, Azahar's
`<program_id>.<slot>.cst`), which no content path derives — so the `file-names-unestablished` caveat carries the cited
pattern in `data["pattern"]` and the file set stays `unknown`. melonDS is the one exception: its states are the
content's own stem plus `.ml1`–`.ml8`, so with content named the declared set is concrete, an empty `SavestatePath`
lands them beside the ROM, and an archive keeps the `<rom_stem>` hole open — exactly the shapes its `.sav` answer takes.
An emulator without a savestate card refuses `standalone-unsupported`, byte-identically to the blanket refusal that
preceded the family.

Two heavyweights answer through shapes of their own (#284). xemu's savestates are QEMU internal snapshots written
**inside** the qcow2 hard disk `[sys.files] hdd_path` names — no file per state exists — so the answer names the image
as its one declared file and `savestate-inside-image` carries the image path and the entry naming (user-chosen, else
`vm-YYYYMMDDhhmmss`); a machine with no disk configured cannot snapshot at all and the question refuses with that fact
named. MAME's states root is `state_directory` out of whichever `mame.ini` the launch's `-inipath` names (else the
compiled `$HOME/.mame;/app/share/mame/ini` search path (the Flathub build define, byte-proven in the shipped binary —
not upstream's `#ifndef` fallback; the `/app` element resolves against the running deploy, and RetroDECK's carries no
`share/mame` at all) — read, not assumed: the RetroDECK commands that pass no `-inipath` land their states under
`~/.mame/sta`, not under the arrangement's tree), with one subdirectory per machine below it — the command's positional
system word, or the ROM's own stem on the `%BASENAME%` arcade rows — and the declared slots `auto`/`quick`/`0-9`/`a-z`,
each `<slot>.sta`. Every MAME answer carries `savestate-support-machine-dependent`: whether the launched system's driver
is flagged `MACHINE_SUPPORTS_SAVE` is compiled into the binary, and an unflagged one still writes the file with a
warning.

### A card can state the emulator has no savestates — and that is an answer

For nine emulators the honest finding is that the feature does not exist, and #284 makes that a stated, cited fact
instead of a generic refusal. The answer is the question's third shape:

```python
outcome = entry.savestate_location()

outcome.emulator   # 'CEMU'
outcome.citation   # the spans and scans that establish the no — contractual
outcome.caveats    # () — or the one caveat a stated no can carry (below)
```

It serializes as `{"no_savestates": {"emulator": …, "citation": …, "caveats": […]}}` — its own top-level key, so the
three shapes are never mistakable. Cemu 2.6 and Vita3K ship no state serializer; the Ryujinx lineage (Ryubing) never had
one; Ruffle's persistence is the SharedObjects tree; GZDoom's, ironwail's, OpenBOR's, PICO-8's and Solarus's whole
serialization is their savegame/cartdata system — the **save** question's business, and the cards say so rather than
blurring a quicksave into a machine snapshot. No tree-derived caveat ever rides a stated no (health findings and link
walks qualify paths, and the absence names none — it answers even where the EmuDeck variant gate would refuse the save
question). What does ride is everything that qualifies the claim itself: `unverified-version` with
`verification: "build-unestablished"` for the emulators no arrangement ships a build of (Ryubing, ironwail, PICO-8 — the
citation then names the release or manual the record read); the arrangement evidence caveats (`arrangement-unverified` /
`arrangement-version-drifted`) exactly as on a placement — a stated no is world knowledge pinned to the build a verified
arrangement ships, and an arrangement atlas has not confirmed on this version says so; and the entry's catalogue-status
and `per-game-override` caveats, because a gamelist that would launch a different emulator for this game is a statement
about emulator identity — "Cemu has no savestates" needs the rider that Cemu may not be what runs.

## Where do texture packs go?

`texture_pack_location` answers where one emulator, configured as it is, reads replacement textures from — the directory
you install a pack into.

```python
outcome = inst.texture_pack_location(core_so="flycast_libretro.so")

outcome.dir           # '/run/media/deck/Emulation/retrodeck/bios/dc/textures'
outcome.physical_dir  # '/run/media/deck/Emulation/retrodeck/texture_packs/retroarch-core/Flycast/textures'
outcome.enabled       # True | False | None — is replacement switched on right now?
outcome.keying        # 'game-id' — how the tree below dir is divided per game (None where nothing cited says)
outcome.needs         # the holes left, same vocabulary as a save placement
outcome.caveats       # stated degradations
```

It takes the same two optional arguments the save questions take, the entry route has it
(`entry.texture_pack_location(content_path=…)`), and so does the aggregate. `content_path` never moves the directory — a
texture root belongs to the emulator, not to one game — but it does decide which per-game options file governs
`enabled`, so pass it when you have it.

**Two halves, two kinds of knowledge.** The root is read off your machine (the system directory as the core receives it,
or the save root as it stands); the fragment below it is per-core behaviour written in no config, so it is packaged,
versioned and source-cited (`atlas/data/texture_packs.json`). Move `system_directory` and the answer moves with it.

**`enabled` is a live read, and `None` is not "off".** The switch comes from the options file RetroArch would read first
(game `.opt`, folder `.opt`, per-core `.opt`, then the global one), and where no file states it, from the default the
**installed core** registers. `None` means neither answered — nothing on this machine states the option and the core
declared no default, or it is set to a value the record cannot interpret (`unknown-option-value` says which). A packs
directory whose feature is off is still the right directory: that is why these are two fields and not one hedged answer.

**`keying` is stated only where a citation backs it.** `game-id | serial | title-id | rom-name | pack | title` —
Flycast's own binary documents `system/dc/textures/<game-id>/`, so that row states `game-id`; a row whose evidence stops
short states `None`, which is not the claim that the tree is undivided. `title` is the odd one and is deliberately not
`rom-name`: DuckStation names a cheat file after the game's **title** — a custom one you set in its own list, else the
title its database gives the disc, and only failing both the content file's stem — so it is read off the emulator, not
derived from the path.

**Standalone emulators answer here without a config read — the save question needs one, and answers only where a card
carries it.** A texture pack usually lives at a default the emulator opens below an XDG base the distribution's flatpak
pins, so every carded emulator answers this question; a save routes through the emulator's own configuration, so
`savefile_location` answers only for emulators with a standalone _save_ card (the eight named in
[the standalone save answer](#where-a-standalone-emulators-saves-live)) and refuses for the rest. The same catalogue
entry can therefore answer this question and refuse that one:

```python
entries = inst.emulators_for("wii").entries
entry = next(e for e in entries if e.label == "PrimeHack (Standalone)")
entry.savefile_location()                            # Unresolved: standalone-unsupported
outcome = entry.texture_pack_location()
outcome.dir           # '/home/deck/.var/app/net.retrodeck.retrodeck/data/primehack/Load/Textures'
outcome.physical_dir  # the shared tree the distribution linked it into
outcome.enabled       # None — always, on a standalone row
```

`enabled` is always `None` there and always carries `emulator-config-unread` naming the file that would answer it
(Dolphin's `GFX.ini`, PPSSPP's `ppsspp.ini`, …): reading those means modelling each emulator's configuration, which is
its own roadmap block. Standalone rows are asked **through the entry route only** — the handle route's subject is a
core, and a standalone emulator has none.

Not every standalone emulator answers, and two of them answer differently. Where the texture directory is a value in the
emulator's own settings, a card may state the **key** instead of a subpath and its resolver reads it — PCSX2 does that
with `[Folders] Textures`, and reads `[EmuCore/GS] LoadTextureReplacements` from the same file, so `enabled` is a
reading there rather than `None`. **PCSX2's** `dir` is the load stage, `<Textures>/<serial>/replacements`, with
`save_id` in `needs`: replacements are read one level below the per-game directory, and naming only the root would send
a caller placing a pack where nothing reads it. That reading is the _global_ one, and PCSX2 has a second settings source
— a running game installs `<DataRoot>/gamesettings/<serial>_<crc>.ini` as a layer over the whole configuration, so any
key can answer differently for one game. Which game runs is not a fact atlas holds, so the answer stays the global
reading and carries `per-game-overrides-present` where such files exist here, with their count and directory.
DuckStation states the directory key for a different reason: the root it resolves against is the config home or the data
home depending on how the launch was started, so a fixed base would answer correctly on one arrangement and wrongly on
the other — and its texture answer would disagree with its own cheat answer about where the emulator keeps things. It
reads no per-serial `replacements` tree of its own. Where nobody has read an emulator's configuration the entry still
refuses with `standalone-unsupported`, so the split runs on evidence, not on the kind of entry. **On EmuDeck the same
cards answer**, below the bases the launch's own binary reads: an AppImage under `~/Applications` (or the executable
EmuDeck unpacks from one) reads the host's XDG tree, and a flatpak whose app id the save card names reads the app's own
homes. A launch whose binary establishes neither — the Windows build under Proton, a flatpak no card names an id for —
refuses with `standalone-variant-unestablished` and the variant in `data`, which is a different instruction from "this
emulator is not covered".

Four ways this question answers with `Unresolved` instead of a directory, and each is a different instruction:

| Code                               | Meaning                                                                                                             |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `core-not-installed`               | the machine established the core is not here — the same code the save routes use                                    |
| `standalone-unsupported`           | a standalone emulator whose directory is named only in its own config, so no card covers it                         |
| `standalone-variant-unestablished` | the card covers it, but nothing establishes which tree the launch's binary reads (EmuDeck; `data.variant` names it) |
| `texture-wiring-unestablished`     | atlas carries no texture wiring for this core                                                                       |

The last is a statement about atlas, never about the emulator. It does **not** say the emulator has no texture-pack
feature; most cores are simply outside the packaged knowledge.

Three caveats are this question's own:

| Code                          | Meaning                                                                          |
| ----------------------------- | -------------------------------------------------------------------------------- |
| `emulator-read-unestablished` | the directory is stated and nobody has established that this emulator reads it   |
| `emulator-config-unread`      | `enabled` is unanswered because the setting lives in a config atlas did not read |
| `feature-switch-absent`       | `enabled` is `false` and this build offers no way to turn it on                  |

The first rides the three cores that port a standalone emulator into a libretro core (Azahar, Dolphin, PPSSPP): they
build a user directory under a root nobody has watched them choose, which is the same open question atlas's audit
already carries for their saves. Take it as "install here, and verify before you trust it". The symlink and read caveats
you already know (`dead-symlink`, `symlink-loop`, `sandbox-path-untranslated`, the health findings) mean here exactly
what they mean on a save placement.

The second rides every standalone row, always with `enabled` at `None`, and its `data` names the emulator and the file:
`{'emulator': 'DOLPHIN', 'config': '…/dolphin-emu/GFX.ini'}`. Read it as "the directory is right, go look in that file
yourself" — never as "replacement is off".

The third is the opposite kind of statement to the first, and the two can never appear together. `feature-switch-absent`
says the read path is established and this build gives you no way to use it: LRPS2 reads packs from
`<system_directory>/pcsx2/textures/<serial>/replacements/`, and the setting that would enable it
(`EmuCore/GS/LoadTextureReplacements`) is not a core option — the only thing that writes it anywhere in the build is the
defaults pass, writing the compiled `false`. So `enabled` is `false` as a fact about the binary rather than a reading of
a file, and no configuration you can edit changes it — only a different core build would. Because a build is exactly
what could add a writer, an installation running a different core version gets `unverified-version` beside the claim
instead of inheriting it.

**The shared browsing trees are not modelled.** RetroDECK links each emulator's own texture directory into
`texture_packs/`, so `dir` is the path the emulator opens and `physical_dir` the tree behind it — both true, and a
client copying files can use either. EmuDeck wires the opposite direction (links in `texturepacks/` pointing _into_ the
emulator's real directory), so nothing on the read path passes through them and no answer mentions them.

## Where do mods go?

`mod_location` answers where one emulator, configured as it is, reads mods from. It is a pair with the question after
it: this one is about **directories an emulator opens**, the next about **files the frontend applies to the ROM**. A
SNES hack is the clearest case of the split — no core in this table has a SNES mod directory at all, and the mechanism
that installs the hack is soft patching, the next question down.

```python
outcome = inst.mod_location(core_so="fbneo_libretro.so")

for tree in outcome.trees:
    tree.role          # 'patched' | 'ips' | 'romdata' — or None where the emulator has one tree
    tree.dir           # '/run/media/deck/Emulation/retrodeck/bios/fbneo/patched'
    tree.physical_dir  # the shared tree the distribution linked it into, or None
    tree.keying        # 'rom-name' — how the tree below dir is divided per game
outcome.enabled        # True | False | None — is mod loading switched on right now?
outcome.needs          # the holes left, same vocabulary as a save placement
outcome.caveats
```

**The answer is plural, and that is the point.** Most emulators read mods from one directory and `trees` is a
one-element list. FBNeo reads from three that are not alternatives but different mechanisms — a replacement romset, an
IPS patch set, a romdata file — all governed by one switch, so an answer that named one of them would be two-thirds
wrong for a caller holding an IPS patch. `role` tells them apart and is `None` where there is nothing to tell apart.
(The same shape will be needed elsewhere: a single question can have several true locations at once, which the save side
runs into where one core writes to two roots for one game.)

Everything else is the texture question's grammar, and means the same: the root is read off your machine and the
fragment below it is packaged, versioned and source-cited (`atlas/data/mods.json`); `enabled` is a live read of the
option that governs the feature, `None` where neither an options file nor the core stated one; `keying` is stated only
where a citation backs it. Both handle and entry routes have it, and so does the aggregate.

**One switch is written down rather than read**, because no machine states it: FBNeo registers its core options too late
for any probe to capture, and no options file mentions the key, so the card carries the upstream default (`enabled`)
with the build it was read at. An options file still wins wherever it speaks, and a machine running another build of the
core gets `unverified-version` beside the value.

**A tree may hang off a configuration value rather than an XDG join**, and DuckStation is the row that needs it: its
cheat files — the same mechanism PCSX2 keeps as `.pnach` patches — sit in `[Folders] Cheats`, and the root that key
resolves against is the config home or the data home depending on how the launch was started. So the card names the key
and its compiled default (`cheats`) instead of a base, and the answer follows the same read the save and BIOS questions
of that emulator make. Where no `settings.ini` exists on either candidate, the answer names the environment-unset side
and says so with `core-mode-unestablished` — the same word the save route uses, so the two never disagree about what is
unknown. Where the file exists and cannot be read, the question is refused rather than answered with a default that file
may well have overridden.

Four ways this question answers with `Unresolved`:

| Code                               | Meaning                                                                                                            |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `core-not-installed`               | the machine established the core is not here                                                                       |
| `standalone-unsupported`           | a standalone emulator whose mod directory is named only in its own config (MAME's `pluginspath`)                   |
| `standalone-variant-unestablished` | the card covers it, but which tree the launch's binary reads is not established (EmuDeck; `data.variant` names it) |
| `mod-wiring-unestablished`         | atlas carries no mod wiring for this core — a statement about atlas, never about the emulator                      |

Standalone emulators answer through the catalogue entry, exactly as they do for texture packs and with the same
asymmetry against their saves. Two caveats are worth branching on, and one absence:

| Code                          | Meaning                                                                          |
| ----------------------------- | -------------------------------------------------------------------------------- |
| `emulator-read-unestablished` | the directory is stated and nobody has established that this emulator reads it   |
| `emulator-config-unread`      | `enabled` is unanswered because the setting lives in a config atlas did not read |
| `soft-patching-applies`       | this core's content is also patched by the frontend — see the next question      |

`emulator-config-unread` now rides a **core** row too: the Dolphin core's mod switch is not a core option at all but an
ordinary `GFX.ini` inside the user tree the core builds, and the caveat names that file on your machine. And where a row
carries **no** config caveat and `enabled` is still `None` — Azahar, in both its standalone and its core row — that is
the weaker statement on purpose: nobody has established that any switch exists, so the answer points nowhere rather than
at a file that may govern nothing.

**PCSX2 answers here from a default and answers the texture question from a configuration — the same emulator, two
different means.** That is evidence, not inconsistency: RetroDECK writes PCSX2's texture directory into the emulator's
own `PCSX2.ini` (`Folders/Textures`), which the texture card now reads, while nothing writes `Folders/Patches` at all —
the patches directory stays the emulator's own default, which is a path join. If you set `Folders/Patches` yourself, the
mod answer moves out from under you and atlas will not see it, because this block reads no configuration.

**Cemu appears in this question and in the texture one, with the same directory.** On that emulator a graphic pack is
one mechanism: `rules.txt` replaces textures and `patches.txt` beside it patches the running title's code. Both
questions answer `Cemu/graphicPacks` because both are true, and neither is made to say "ask the other one".

## What patches this ROM before it loads?

`soft_patch_candidates` answers where a ROM hack goes. It is the other half of the question above: RetroArch's own
mechanism, not an emulator's — before a core sees the content, the frontend looks for a patch file **beside the ROM**
and applies it to the copy in memory. Where `mod_location` refuses because no core in the table has a mod directory for
that system, this is usually the answer the caller actually wanted.

```python
outcome = inst.soft_patch_candidates("/roms/snes/Chrono Trigger.sfc", core_so="snes9x_libretro.so")

[c.path for c in outcome.candidates]   # the four names, in the order RetroArch tries them:
# ['/roms/snes/Chrono Trigger.ips', '.../Chrono Trigger.bps',
#  '.../Chrono Trigger.ups', '.../Chrono Trigger.xdelta']
outcome.candidates[0].continuations    # ['…Trigger.ips1', … '…Trigger.ips9'] — the chain, in order
outcome.candidates[0].attempted        # True | False | None — does this build try this format?
outcome.applies                        # True | False | None — is this core patched at all?
```

The content path is the question's subject, so it is positional and required; `core_so` is optional and decides one
field. The aggregate has this question too.

**Nothing is written to disk.** The patch is applied to the in-memory buffer the core is handed — the ROM beside the
patch file is never modified, so "install a hack" here means "drop the patch file next to the ROM", and the original
stays what it was.

**How the names are built.** Take the content path, cut its last extension, append the format's own. Content inside an
archive is named after the **entry**, in the archive's directory: `roms/nes/pack.zip#Game.nes` is patched by
`roms/nes/Game.ips`. The first patch that applies wins; then indexed continuations `<name>1` … `<name>9` are applied on
top of it, stopping at the first gap, which is why each candidate carries its nine.

**`applies` is about the core, and it is a live read.** RetroArch patches the content _buffer_, so it patches only what
it loads into memory — every core that does not need a full path. Atlas reads the `needs_fullpath` declaration in the
core's own `.info`; `True` means this core is patched, `False` means it never is (disc and full-path cores: LRPS2,
PPSSPP, the Dolphin and Azahar cores…), and `None` means nothing established it — no core named, or that `.info` states
nothing (118 of the 292 files a stock RetroDECK ships state nothing), or it could not be read (`core-info-unreadable`,
`info-path-unresolved` say which). Two further conditions ride every `True` and are not fields because neither is a fact
about your machine: only the **first** content file is patched, and only when it is not a media type. A single-file
launch — what every launcher does — meets both.

**`attempted` is about the build, and it is not readable at all.** Patching and its `.xdelta` applier are compile-time
flags; no setting, log or file on a running machine states how they were set. So atlas states them only where someone
read the binary: RetroDECK's shipped RetroArch was, and answers `True` for all four. Everywhere else each candidate is
`None` beside `patch-formats-unestablished` — which is _not_ "this format is unsupported", it is "nobody looked". The
file names are exact either way.

| Code                          | Meaning                                                                    |
| ----------------------------- | -------------------------------------------------------------------------- |
| `patch-formats-unestablished` | nobody has read this RetroArch build's patch flags                         |
| `content-path-unnamed`        | the path names no file, so no candidate is named (you get an empty list)   |
| `unverified-version`          | the build claim was established against another version of the arrangement |

One refusal, and it is the family's usual: naming a core this installation does not have gives `core-not-installed`. The
candidate names would still be true — they are the content's own — but the question was asked about a core that cannot
run here.

**Nothing on the machine switches this on or off.** There is no configuration key for soft patching anywhere in
RetroArch; the only things that change it are command-line flags (`--ips=`/`--bps=`/`--ups=`/`--xdelta=` force one
format, `--no-patch` blocks patching), and no launcher on either arrangement passes one. So on a normal launch the four
candidates above are simply what happens.

## Which emulator would launch this? (the catalogue)

**Every handle answers this**, and the ones that cannot answer it from a catalogue say why — so you never have to narrow
to a concrete handle to find out.

```python
answer = inst.emulators_for("n64", content_path=rom_path)
answer.entries                     # the launch entries, in effective priority order
answer.sources                     # what was read to say so (prose, for debugging)
answer.caveats                     # why there are no entries, when there are none

entry = answer.entries[0]          # the effective default (per-game altemulator > per-system choice > declared order)
entry.label, entry.kind            # 'Mupen64Plus-Next', 'libretro'
entry.core_so                      # 'mupen64plus_next_libretro.so' — or None for a standalone emulator
entry.system                       # 'n64' — an entry says what it launches, wherever it travels
entry.selection                    # why it is first, when a user promoted it — None for declared order
entry.caveats                      # this entry's own degradations, e.g. per-game overrides nobody checked
entry.provenance                   # which catalogue layer declared it (prose, for debugging)

inst.systems().systems             # every system the catalogue declares (same answer shape, same caveats)
```

An empty `entries` is six different facts, and the five `emulator-catalogue-*` codes are how you tell them apart —
**none of them present** means the catalogue was read and declares no emulator for that system, an answer about the
machine and one of the two you may act on as "nothing here":

| caveat code                        | what it means                                                                                                                                       | what to do                                                                               |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| _(none of the five)_               | read, and the frontend knows no emulator for this system                                                                                            | trust it                                                                                 |
| `emulator-catalogue-unavailable`   | this arrangement ships no frontend catalogue at all — the entries beside it are **derived** (see below)                                             | use them; `emulator-list-derived` states their nature                                    |
| `emulator-catalogue-unestablished` | it may have one; atlas has not established where                                                                                                    | same — but do not report "no emulators"                                                  |
| `emulator-catalogue-unreadable`    | atlas could not read a catalogue here — missing, unreadable, or empty                                                                               | surface it; the machine may be broken                                                    |
| `emulator-catalogue-sealed`        | part of the catalogue is sealed away (EmuDeck's AppImage-embedded bundled layer could not be opened); only the on-disk layers answered              | use the entries you got; an empty list is "nothing readable declares this", never "none" |
| `emulator-catalogue-exclusive`     | the custom `es_systems.xml` declares itself the whole catalogue (`<loadExclusive/>`); the bundled layer is not loaded — by the frontend or by atlas | trust it like a read catalogue; an empty list is a real "none"                           |

`sealed` and `exclusive` are the two of the five that also accompany real entries, and they hedge in opposite
directions. On an EmuDeck machine with ES-DE, a system EmuDeck's own `custom_systems` overlay declares answers fully
(ES-DE's merge replaces a same-name bundled system entirely), and `sealed` says the frontend may declare more systems
than the answer can list. `exclusive` says the opposite: a document-level `<loadExclusive/>` in the custom
`es_systems.xml` makes ES-DE skip the bundled file wholesale, so the enumeration you got is the complete catalogue in
force — on EmuDeck it replaces `sealed`, because nothing sealed away applies.

`sealed` is also capability-dependent since the AppImage reader landed: atlas opens the AppImage's embedded
`es_systems.xml` itself (a pure-stdlib squashfs walk, `atlas.squashfs`) wherever the runtime has the image's codec —
`compression.zstd` arrives with Python 3.14, and `backports.zstd` (the same code, published for older interpreters) is
accepted equally, so a host application that vendors the backport grants its 3.11 runtime the capability without atlas
gaining a dependency — and the catalogue is then complete: nothing sealed, no list derived, and an embedded system's
`<platform>` tag reads live like any other. On older interpreters, and wherever the image is missing, replaced or
restructured, the sealed state stays exactly what it always was. The capability is the _runtime's_, so the same machine
can answer differently under two Pythons — both answers are honest, and the caveat says which one you got.

Read the four codes, not the emptiness of `caveats`: a broken installation puts its health findings in front of any of
these, so `if not answer.caveats:` is not the "read and declares nothing" test — it never fires on a broken
installation. Filter to the codes (or ask `health()` separately, which is the same question asked directly).

```python
refusals = {c.code for c in answer.caveats} & {
    "emulator-catalogue-unavailable", "emulator-catalogue-unestablished", "emulator-catalogue-unreadable",
    "emulator-catalogue-sealed",
}
if not answer.entries and not refusals:
    nothing_here(system)          # the frontend genuinely knows no emulator for it
```

**The derived enumeration (issue #133).** A machine with no readable catalogue still carries an answer: every installed
core declares what it is for in the `.info` beside it. On a bare RetroArch — and on EmuDeck for a system its readable
layers do not declare — `emulators_for` now answers with entries **derived** from those declarations, through the same
versioned systemname map (and the same selection) the firmware route has always used, so the two questions can never
derive different lists. Such an answer carries `emulator-list-derived`, and the code means three things at once: the
order claims no default (no catalogue, no user selection — there is no "entry that would run"), no entry carries a
launch command (`command` is empty — a catalogue declares those, and inventing one would be a guess), and a catalogue
could declare a different list. The entries are otherwise real: `core_so` names the core, the label is the core's own
`corename`, and the entry routes work — `entry.savefile_location(...)` answers, because a derived entry is a core and
the core question was always answerable there. `systems()` on these arrangements lists the systems the cores file under,
marked the same way. What stays unchanged: `launchable` (the accept-list is catalogue knowledge), and a system the
readable EmuDeck layers _do_ declare (the catalogue's own answer, never overridden by a derivation).

`unavailable` is a statement about the machine; `unestablished` and `sealed` are statements about atlas, and a client
that renders either as an absence is telling its user something nobody checked. `exclusive` is a statement about the
machine and deliberately **not** in the refusal set above: with it riding, an empty list is the frontend's real answer,
so the snippet's `nothing_here` branch is exactly where it should land. Two more codes can ride along without being
refusals, both of them EmuDeck's: `config-home-relocated` (a `portable.txt` next to the ES-DE AppImage may have moved
the tree this handle reads out from under it) and `frontend-marker-mismatch` (the disk and `settings.sh`'s
`doInstallESDE` record disagree about ES-DE being installed — the disk decided the answer).

The per-game step is ES-DE's, so it happens on the ES-DE-driven handles (RetroDECK, and EmuDeck where an ES-DE is
present), and it matches on the path: pass the ROM the way it lies under the system's ROM directory — which is exactly
`rom_location(system).dir`, the same directory the ROM question answers. Gamelist entries are relative to that directory
and are resolved against it, and a folder entry covers the files directly inside it. A path somewhere else — a copy, a
staging directory — matches no game, and the answer is then the per-system one. Two files of the same name at different
depths are two different games, and only the one the gamelist names carries the override.

That comparison is **lexical** — `.`, `..` and repeated slashes are folded, symlinks are not followed. RetroDECK's tree
uses symlinks liberally, so a ROM spelled through one (or through any other route to the same file) will not match its
per-game entry, and you get the per-system answer instead. Resolving links would cost a read per gamelist entry per
query, which is the one thing atlas's one-read-per-source rule exists to avoid; spelling the path the way
`rom_location(system).dir` gives it to you costs you nothing.

**If the anchor cannot be resolved, the per-game step is skipped and the answer says so.** The directory comes off the
frontend's own `ROMDirectory`, and that can refuse — see the ROM section below for the four codes. When one of them
appears on `emulators_for(system, content_path=…)` or on an entry's `savefile_location`, read it as "a per-game override
may apply here and atlas could not check": the entry order you got is the per-system one. It does not happen on a stock
installation — the distribution writes that setting in the same step that generates the ROM tree, so a machine with
system directories has the setting too.

The entry answers the save question itself, so the core never round-trips through your code:

```python
result = entry.savefile_location(content_path=rom_path)
if isinstance(result, atlas.Unresolved):
    # a typed domain outcome, not an exception — e.g. standalone emulators are not resolvable yet
    print(result.code)             # 'standalone-unsupported'
else:
    use(result.dir)
```

Always handle the `Unresolved` branch: standalone emulators (DuckStation, PCSX2-SA, …) are catalogued but outside the
resolver's coverage, and pretending otherwise is exactly what atlas refuses to do. The entry route refuses on one more
code than that — `core-not-installed`, when the catalogue names a libretro core this installation does not have — so
match on the code rather than assuming which refusal you got.

`standalone-unsupported` is one word on both routes: the placement route answers it as the `Unresolved` code above, and
the firmware route as a caveat on a core whose `declaration` is `"unsupported"`. Both say the same thing — the emulator
is installed, atlas has no source for its rules — which is a different axis from `arrangement-unverified`: that one says
a reading was never confirmed on a live machine, this one says there was no reading to confirm.

A carded standalone emulator answers instead of refusing: its `declaration` is `"packaged"`, and the entry carries real
requirements — Cemu's `keys.txt` is the first (the title/disc keys encrypted dumps need; `need` is `optional` because
decrypted content runs without it). Packaged means exactly what it says: the emulator ships no `.info`, so what it
expects is atlas's card, established from the emulator's source at the shipped release and stated as such by the
`firmware-packaged-declaration` caveat on the entry — never dressed up as a machine read. Everything about _this_
machine stays live: the destination is the emulator's own probe path resolved against this arrangement's trees (through
symlinks, like every requirement path), `found` is what actually sits there, and `checked` stays `"unknown"` on a
present file because no packaged identity can exist for a user-supplied key file. Such a requirement carries
`core_so: null` and `system_source: "card"`, and its `path` may lie outside the firmware root — the emulator's own tree
is the door that matters.

A card states its probes one of three ways, and melonDS is the second: **the paths are configuration values**, not fixed
names under a tree. Its card names the keys (`[DS] BIOS9Path`, `[DSi] NANDPath`, …) and the resolver reads each value
the way `OpenLocalFile` reads it — absolute as spelled, anything else below the melonDS config directory. Which keys are
probed at all is read live too, because the emulator decides it live: `verifySetup` asks two switches, so DSi mode adds
the DSi BIOS pair and the NAND, and with `Emu.ExternalBIOSEnable` **off** — melonDS's compiled default — DS games boot
on the emulator's built-in replacement and nothing external is probed:

```python
answer = inst.firmware_for_system("nds")
core = answer.cores[0]                    # 'melonDS (Standalone)'
core.declaration                           # 'packaged'
[r.file_name for r in core.requirements]   # [] with the switch off — the true answer, not an unread one
[c.code for c in core.caveats]             # [..., 'firmware-builtin-replacement']
```

That empty list is a statement, and `firmware-builtin-replacement` is what keeps it from reading as "nothing is
configurable": its data names the switch (`Emu.ExternalBIOSEnable`) whose flip creates the requirements, and the console
type as read. The two arrangements land on opposite sides of it — one switches external BIOS on and points the paths at
its BIOS directory, the other writes all seven paths and leaves the switch off, so they are parked behind something that
is not reading them. A probed key whose value names no file (unset, or ending in a directory step) is refused by name
with `firmware-path-names-no-file` rather than answered with the config directory dressed up as a BIOS file, and the
other keys of the same probe set still answer — so a caller sees exactly which one is unconfigured. When the governing
configuration exists and cannot be read, the entry's `declaration` is `"unreadable"` with `emulator-config-unreadable`:
which files this launch would probe is unknown, which is not "needs nothing".

**PCSX2 takes two settings to name one file**, and the pair is worth knowing about: `[Folders] Bios` gives the directory
and `[Filenames] BIOS` the image inside it, chosen by the user in the emulator. A fresh install has the directory set
and the name empty, and then no game boots at all — so an empty name is stated with `firmware-path-names-no-file` whose
data carries the `dir` a BIOS belongs in. That is a different answer from a _chosen_ image that is not there, which is a
required requirement reported `found: "missing"`, and a client would act differently on the two: one says "pick one",
the other says "fetch this one".

**xemu shows which files a card deliberately leaves out.** Four paths sit in its machine settings and only three are
firmware: the boot ROM, the flash image and the hard disk each refuse the start when missing. The EEPROM does not appear
in the firmware answer at all, because the emulator generates one where none exists and it is the console's own settings
— the save answer already names it as a settings group, and stating it twice would file save data under firmware. The
hard disk _is_ in both answers on purpose: the console needs one to start, and every save lives inside it, so each
answer names it and says which aspect it means.

**DuckStation names no file at all**, which is the third way a card can state its probes and the one that changes what
`verify` means. `[BIOS] SearchDirectory` names a directory, three per-region keys may name an image inside it, and where
they are empty — the state both arrangements ship — the emulator keeps every file of an accepted size and works out what
it is by hashing it against a table compiled into its binary. Atlas carries that table, so the answer can name the
image; what it will not do is claim one without reading the bytes:

```python
answer = inst.firmware_for_system("psx")             # no verify: presence is not the question here
core = answer.cores[0]                                # 'DuckStation (Legacy) (Standalone)'
[r.file_name for r in core.requirements]              # [] — nothing was hashed, so nothing is claimed
[c.code for c in core.caveats]                        # [..., 'firmware-search-unverified']

answer = inst.firmware_for_system("psx", verify=True)
core = answer.cores[0]
[r.file_name for r in core.requirements]              # ['scph5500.bin'] — the image this launch would boot
[c.code for c in core.caveats]                        # [..., 'firmware-image-identified', 'firmware-image-ambiguous']
```

Read those three codes as one sentence about content. `firmware-search-unverified` carries the directory, the count of
files whose size the emulator accepts — on the reference machine 27, several of them Saturn dumps that happen to be
exactly 512 KiB, which is why the size test cannot be the answer — and the `regions` the unanswered search speaks for.
`firmware-image-identified` names what the picked file _is_ in the emulator's own words (`SCPH-5500 (v3.0 09-09-96 J)`)
and the region it belongs to. And `firmware-image-ambiguous` is the honest limit: where several images rank alike, the
emulator keeps whichever one the directory hands it last, and no read reproduces that order — five images tie on the
reference machine, so the file named is one of them rather than the one that boots. An image the table does not know is
not a fault either: DuckStation boots it with a warning, so it is stated as the pick with
`firmware-content-unidentified` beside it.

**When a region key names an image, the answer becomes alternatives.** One launch reads exactly one of `PathNTSCU`,
`PathNTSCJ` and `PathPAL` — `GetBIOSImage` switches on the console region, which under the shipped Auto setting is the
running disc's own — so a named image and the search's find for the remaining regions are never two files one launch
needs. The entry then answers a single `FirmwareAlternatives` group: the named image as the option of its one region,
the search's find (or its degradation caveats) covering the regions left over, every option carrying its `regions` as
data. Pick the option whose `regions` contain your disc's region; a region no option lists has nothing stated for it and
the caveats say why. Only the everything-searched state — every key empty, which is what both arrangements ship — stays
a plain, unconditional requirement.

## Where do this system's ROMs live? (and what launches them)

The same catalogue declares, per system, the directory its ROMs sit in and the file extensions the frontend will launch.
Both are read off the machine:

```python
placement = inst.rom_location("n64")
placement.dir            # '/run/media/deck/Emulation/retrodeck/roms/n64'  — or None, see below
placement.physical_dir   # None normally; the backing directory when `dir` goes through symlinks
placement.extensions     # ('.n64', '.N64', '.z64', '.Z64', '.zip', '.ZIP')
placement.caveats
```

`dir` and `physical_dir` are the same pair a save placement answers with, and they mean the same thing here: where a
distribution wires a tree into place with a symlink, the frontend-side path and the physical path are both true and
answer different questions. Write through `dir` unless you specifically need the backing location.

**`dir` is also the anchor** the per-game emulator override matches against, because it is the directory ES-DE launches
from — so a ROM named the way it lies under `rom_location(system).dir` is a ROM the catalogue question can place
per-game.

Need the ROM **root** rather than one system's directory? That is `roms_dir()` on the RetroDECK and EmuDeck handles: the
value the frontend substitutes for `%ROMPATH%`, with no `<path>` applied. The per-system directory is usually that root
plus the system name and not reliably so — the catalogue declares the `<path>`, and a custom system may declare anything
— which is why they are two questions and only `rom_location` answers the second. `roms_dir()` returns `None` where the
resolution refuses, for those of the reasons below that belong to the root — on EmuDeck that includes an arrangement
running no ES-DE atlas can find; it cannot carry which, so ask `rom_location(system)` when you need that.

**Do not recompute either of these from a table of your own.** The directory is the catalogue's `<path>` with ES-DE's
`%ROMPATH%` substituted from the setting ES-DE substitutes it from, so it follows a user who moved their library; a map
in your code does not, and it is wrong silently. The extensions are the declaration verbatim — both cases where the file
lists both cases — because which of them to act on is the frontend's business and not something atlas filters for you.

**`dir` is `None` whenever atlas resolved no directory, and the caveat says which of eight reasons.** Four are the ones
the catalogue question already has — the arrangement ships no catalogue (`emulator-catalogue-unavailable`), atlas has
not established where it keeps one (`emulator-catalogue-unestablished`), it could not be read
(`emulator-catalogue-unreadable`), or the readable layers declare nothing for this system while the rest is sealed away
(`emulator-catalogue-sealed`). Four belong to this question, and they split along the line that decides what you can do
about them — the first two are facts about the machine, the last two statements about atlas:

| caveat                         | what it means                                                                               |
| ------------------------------ | ------------------------------------------------------------------------------------------- |
| `rom-path-undeclared`          | the catalogue was read and declares no such system, or declares it without a `<path>`       |
| `rom-path-unresolved`          | the frontend's ROM-directory setting holds something that is not an absolute path           |
| `frontend-settings-unreadable` | the file that setting lives in is there and atlas could not read it                         |
| `config-home-relocated`        | EmuDeck: a `portable.txt` may have moved the tree the frontend's own default is relative to |

**An unset ROM-directory setting is not one of those cases** — it resolves. The frontend has a documented home-relative
default, and on both arrangements its home is knowable rather than guessable: on RetroDECK the launcher hands ES-DE an
explicit `--home` pointing at the app's own config tree, which is the same tree the setting was read from; on EmuDeck it
hands none, so the home is the user's own and the default is `~/ROMs`. So `dir` comes back as that home's `ROMs`
directory, and the answer is a reading rather than an assumption. Resolving is not asserting the directory exists —
nothing stats it, and an absent one is the ordinary missing-directory state.

A settings file that is **missing** is the unset case, and one that is **there and unreadable** is not: the frontend
reads that file without trouble, so whatever it says is the configuration in force, and answering the default would name
a directory belonging to a configuration nobody established. That is what `frontend-settings-unreadable` says, and it
carries the read status in `data["status"]` (`unreadable`, `invalid-text`, or `unparseable`).

`config-home-relocated` is worth handling on its own rather than as one more "no directory", and it is **EmuDeck's**
code alone. It fires when a `portable.txt` sits next to the ES-DE AppImage (`data` names it in `path`): ES-DE reads that
file to move its application data directory away from `~/ES-DE`, so the presence of one puts the tree the frontend's
default is relative to in doubt. The caveat is a statement about the handle's answers as a whole, and it rides them:
every catalogue-shaped answer carries it while the `portable.txt` is present, the firmware answers its catalogue informs
included, because the `~/ES-DE` reads are what is in doubt — and it reaches the entry route's placements too, which
re-read those sources when content is named. The riding answers still state what the on-disk files say — the caveat
carries the doubt. Where it refuses instead is this ROM question's home-derived resolutions (the unset default, and a
`~` expansion): `dir` comes back `None`, and the riding caveat is that refusal's own statement — one fact, one code,
once per answer. A configured absolute directory is still answered, with the caveat riding.

RetroDECK has no such doubt to state, and none of its answers carry the code: its config home is pinned by Flatpak
itself — the `XDG_*_HOME` variables are force-set to the per-app directories after every override file has been applied
— so nothing an override says can move the tree, and the answers resolve against the config home atlas read.

Never read `None` as "look in the default place" — where there is a default worth standing behind, atlas has already
applied it. `rom-path-unresolved` carries the declared path in `data["declared"]`, so a client that knows its own setup
can finish the substitution atlas refused to guess at.

Extensions survive an unresolved directory: which files launch is declared in the same element and does not depend on
where they sit.

**Treat the extension list as declared text, not as a clean vocabulary.** atlas passes the tokens through exactly as the
catalogue writes them, and real catalogues contain mistakes: the shipped build this was measured against declares one
system's list with a token whose leading dot is simply missing, and another system's with the same token twice. So do
not assume every token starts with `.`, and do not assume the list is a set. Normalize into whatever shape your own
matching needs — but do it on your side, on a copy. atlas will not clean the list, because a cleaned list is a claim
about what the frontend launches, and the frontend launches what its own typo says: a dot-less token matches nothing
there either, and inventing the dot would make atlas's answer disagree with the machine.

## Is this file launchable here?

A client that has a file and a system needs to know whether anything on this machine will launch it — and when the
answer is no, **which kind of no** (issue #36). The accept-list above is the read; this question is the judgment:

```python
answer = inst.launchable("dreamcast", "/run/media/deck/roms/dreamcast/Game (Track 1).bin")
answer.verdict       # 'launchable' | 'not-accepted' | 'entry-not-accepted' | 'needs-installation' | 'unknown'
answer.extension     # '.bin' — the token ES-DE derives (name from the last dot, case preserved)
answer.accepted      # ('.chd', '.cue', '.gdi') — the system's declared list, verbatim
answer.entry         # the entry that would run — on 'launchable' and 'entry-not-accepted'
answer.alternatives  # entry labels established to take the file — on 'entry-not-accepted' alone
```

The match is exactly ES-DE's: the file name's token from its **last** dot, case preserved, compared **exactly** against
the declared tokens — which is why catalogues list `.z64` and `.Z64` separately, and why a `.CHD` file on a `.chd`-only
list is genuinely `not-accepted` (the frontend never scans it). A name without a dot yields `"."`, ES-DE's own sentinel.

The five verdicts never collapse:

- **`launchable`** — the list accepts the token; `entry` is the emulator that would run, resolved through the same
  selection hierarchy `emulators_for` applies (per-game override first), and judged one level further (issue #66, see
  below).
- **`not-accepted`** — no declared token equals the derived one. The remedy is the consumer's: pick the `.gdi` instead
  of its track file, unpack the container, or select an entry that accepts it. `extension` and `accepted` carry
  everything that decision needs.
- **`entry-not-accepted`** — the list accepts the token and the entry that would run is **established** not to read it:
  the accept-list is declared per _system_ (the union over every entry) while the command is per _emulator_, so the
  union can say yes and the machine then does nothing at all. `entry` names the refusing entry — it _is_ the finding —
  and `alternatives` the declared entries established to take the file; the remedies are that verdict's own (unpack, or
  select one of them), which is why it never collapses into `not-accepted`.
- **`needs-installation`** — the format is real content for the platform and an installation step has to run before
  anything launches: recorded world knowledge with its citation in the sources (`atlas/data/launch_formats.json` — a PSN
  `.pkg` is the case that founded it). Never collapsed into `not-accepted`, because "find another file" is the wrong
  advice for a digital-only title whose only form is the package.
- **`unknown`** — the system is not one this catalogue declares, the catalogue could not be read, or (EmuDeck) the layer
  that might declare it is sealed inside the AppImage. A statement about the look, never about the file — the riding
  caveats say which, and a client that treated it as either 'no' would be told something nobody checked.

**The per-entry judgment** splits along the boundary rule. A **libretro** entry's formats are read live off the
installed core, and they are _claims_, not gates: RetroArch checks nothing on a direct load and hands the file over, so
a file outside the claims stays `launchable` with `entry-format-unclaimed` stating the absent promise. Archives run
through RetroArch's own hands — a container the core does not claim is opened and searched for the first file matching
the claims, so the verdict stands with `archive-contents-unread` (what is inside is something atlas does not read); a
`block_extract` core handed a container it never claimed is the one libretro refusal this can establish. A
**standalone** entry opens the file itself: its recorded loader decides (`launch_formats.json`'s `emulators` block,
matched case-insensitively — the gate is the loader, not ES-DE's case-exact scan), and an emulator without a card earns
`entry-format-unestablished` — never "refuses", because an entry nobody read is not an entry known to refuse the file.

## Firmware

Four questions, verification strictly opt-in. The first three share one answer shape (`FirmwareAnswer`);
`identify_firmware` answers off content, so it has its own (`FirmwareIdentification`):

```python
inst.firmware_for_core("mgba_libretro.so")                  # what does this core want, and where?
inst.firmware_for_system("gba")                             # which cores run this system, what does each want?
inst.firmware_inventory(verify=True)                        # everything — declared, present, and unclaimed
inst.identify_firmware(md5="32fbbd84…")                     # this content: what is it, where does it go?
```

`firmware_for_system` enumerates the way the arrangement does. Where a frontend catalogue answers (RetroDECK; EmuDeck
while an ES-DE is on disk), the emulator list is the frontend's own — entries whose core is not installed and standalone
emulators included, stated as such — and the same `emulator-catalogue-*` statements that ride the catalogue question
ride this answer: EmuDeck's sealed statement included, with `config-home-relocated` and `frontend-marker-mismatch`
beside it when their conditions hold. Where none does, the list is derived from the installed cores' own `systemname`
and `emulator-catalogue-unavailable` says so. An id the readable layers of a sealed catalogue do not declare answers
empty with `firmware-declaration-unknown` — a look that failed, never `system-unknown` — because the declaration may sit
in the layer nobody could read.

Reading a `FirmwareAnswer`:

```python
answer = inst.firmware_for_core("pcsx_rearmed_libretro.so", verify=True)
answer.root                        # the live system_directory (None + caveat only when the key is cleared)
for core in answer.cores:
    core.declaration               # 'read' | 'absent' (not installed) | 'unreadable' | 'unsupported' — four empties
    core.requirements_met          # True | False | None — THE field to render (see below)
    for entry in core.requirements:
        if isinstance(entry, atlas.FirmwareAlternatives):   # one launch needs exactly ONE option — see below
            reqs = entry.options   # each option carries req.regions, the console regions it serves
        else:
            reqs = (entry,)        # an unconditional requirement (entry.regions is None)
        for req in reqs:
            req.file_name, req.path    # what the core opens, and the absolute resolved destination
            req.need                   # 'required' | 'optional'   — what the emulator asks for
            req.present                # True | False | None       — what lies at the destination
            req.checked                # 'verified' | 'mismatch' | 'unchecked' | 'unknown' | None (nothing to check)
            req.satisfied              # True | False | None       — present AND nothing contradicts it
    core.refused                   # declarations atlas would not follow, each with the reason it was refused
answer.unclaimed                   # files in the firmware tree that no installed core declares, identified by content
```

A core's `requirements` list is a conjunction — every entry is needed — and one entry may be a `FirmwareAlternatives`
group, and then what is needed is exactly one of its options: the console region decides which, and each option's
`regions` names the regions whose launch it serves (disjoint across the group, so the pick is never ambiguous). Today
one emulator emits it — DuckStation, whose per-region BIOS keys are read one-per-launch — and everything else stays
plain requirements. The group's own `satisfied` is the honest lift over the region atlas cannot read: `True` when every
option is met, `False` when every option fails, `None` for the mixed group, and `requirements_met` folds it in through
exactly that.

`unclaimed` never lists dot-files — the scan globs each directory and a wildcard does not match a leading dot, so
tooling residue like `.directory` stays out of the answer by design (a core that _declares_ a dotted path still gets its
requirement: declarations are resolved, never globbed).

A core's requirement list is what its `.info`'s own `firmware_count` enumerates — RetroArch reads `firmware0_…` up to
`firmware<count-1>_…` and nothing else, so a `.info` without a readable count declares no firmware at all however many
paths it lists, and a path past the count is never asked for. Where the file and that enumeration disagree the core
carries a `firmware-declaration-unread` caveat naming the keys the emulator does not take, and its message says why for
each: a slot past the count or without one, a spelling RetroArch never composes (`firmware_path`, `firmwareA_path`,
`firmware00_path`), or an empty value it discards. No `firmware…path` key is ever dropped in silence — it is read or it
is stated. A declared path is composed with the firmware root the way RetroArch composes it, which has no special case
for an absolute one: `firmware0_path =
"/etc/passwd"` lands at `<root>/etc/passwd`. Read `req.path`, never re-join
`req.declared` yourself.

The two axes never merge: `need` is what the emulator asks for, `checked` is what the machine says. `"unchecked"` means
_we did not look_ (you passed `verify=False`); `"unknown"` means _we looked and cannot tell_ (no packaged identity for
this file). Render `requirements_met` as your traffic light: `True` only when everything required is there and nothing
established contradicts it, `False` when something is missing or has wrong bytes, `None` when it could not be
established — never coerce `None` to green.

The download flow runs off content, not names:

```python
ident = inst.identify_firmware(md5=server_row["md5_hash"])
ident.known_as                     # every name this dump is known under
for req in ident.requirements:     # every destination on THIS machine that wants it
    place_file_at(req.path)
```

Pass at least `md5` or `sha1`: a size is not an identity, and a request carrying only one is answered with an empty
identification plus `firmware-content-unstated` rather than an exception. Two more codes sit next to it when `identity`
comes back `None`: `firmware-content-unidentified` (the packaged table does not cover this content — a normal answer)
and `firmware-content-contradictory` (the table knows a digest you passed, and the entry it names disagrees with
something else you passed — check your own values, not the table). Each caveat's `data` carries every field you stated,
so you can see which one was rejected.

An answer with no requirements says which kind of empty it is, and the code is the branch — never the message:

| caveat                         | what it means                                          | what to do                                      |
| ------------------------------ | ------------------------------------------------------ | ----------------------------------------------- |
| `system-unknown`               | nothing here covers that identifier                    | check your vocabulary (RomM slug vs ES-DE name) |
| `no-firmware-declaration`      | read, and nothing declares it — an established absence | nothing needed                                  |
| `no-firmware-requirement`      | declared, but nothing became a requirement             | read `core.refused` and the core caveats        |
| `firmware-declaration-unknown` | atlas could not establish what is declared             | treat as unknown; never as "nothing needed"     |
| `system-directory-cleared`     | the key is set to nothing, so the root is per-run      | name content, or fix the config                 |

These are **answer-level** codes, in `answer.caveats`. Two facts about an individual emulator live on the entry instead,
in `core.caveats`: `core-not-installed`, and `standalone-unsupported` for one that is here but whose rules are not
covered (`declaration="unsupported"`). A system whose only emulator is standalone therefore answers
`firmware-declaration-unknown` at the top and carries the reason on the core — branch on both collections.

A per-system or per-core answer whose emulators were all read and declare nothing carries no such caveat: each entry
says it itself with `declaration="read"` and an empty requirement list, which is the honest "needs nothing".

**An absent `system_directory` is not one of these.** RetroArch seeds `system` under its config tree before reading any
config, so atlas resolves the key's absence to that directory and answers in full — the same directory the placement
route has resolved since it started answering for cores rooted there. Only a key set to blank or the literal `default`
refuses, because what a core is handed then depends on the run: with content loaded RetroArch passes the content's own
directory, and a firmware question names no content.

Two more say a directory could not be read, and both mean the answer is narrower than the machine:

| caveat                        | what it means                                    | what to do                                             |
| ----------------------------- | ------------------------------------------------ | ------------------------------------------------------ |
| `core-enumeration-incomplete` | the core directory could not be listed           | the core list is what was visible, not what is shipped |
| `firmware-scan-incomplete`    | a scanned firmware directory could not be listed | `unclaimed` is partial; do not read it as a clean tree |

Both carry `data["path"]` — the directory that could not be read.

## Answers as plain JSON

Every answer type has one canonical serializer — the same code the conformance vectors assert:

```python
from atlas import savefile_placement_contract, firmware_contract, health_contract, installation_contract
json.dumps(savefile_placement_contract(placement))
json.dumps(health_contract(inst.health()))   # what installation_contract() puts under 'health'
```

Structured fields in these dicts are contractual; prose (`sources`, caveat messages) is deliberately absent. An
aggregate answer has no serializer of its own — `installation_answers_contract` composes the label with whichever of
these you asked for, so a labelled answer and a handle-route answer serialize identically.

### Which answers have a summary field, and which never will

Three subjects carry one, each because the first thing a client asks is exactly what the field states:

| subject                | field              | shape                 | why                                                        |
| ---------------------- | ------------------ | --------------------- | ---------------------------------------------------------- |
| `health()`             | `ok`               | `True` / `False`      | always answerable — ok is the absence of findings          |
| a firmware core        | `requirements_met` | `True`/`False`/`None` | `None` where it cannot be established; never read as green |
| a firmware requirement | `satisfied`        | `True`/`False`/`None` | present _and_ nothing contradicts it; `None` when unjudged |

The shape rule is the difference between them: a plain boolean only where the fact can always be established, and the
third state wherever "cannot tell" is reachable.

A summary combines fields — `satisfied` reads `found`, `checked`, `identity` and `need` together. The other booleans on
these answers do something else, and none of them answers "is this good?":

- **what the run did**: `hash_checked` (you passed `verify`), `answer.cores` being listed at all;
- **what one field contains**: `file_set.complete` says the list is closed, not that the placement is usable (and it is
  reserved today — see [Reading the file set](#reading-the-file-set));
- **a shorthand for one richer field**: `req.present` is `req.found` collapsed to a boolean, and `found` stays the
  authoritative one — a directory sitting at the destination is not a missing file, and only `found` can say so.

Read the shorthand when it answers your question, and the field behind it when the distinction matters.

No other answer gets one, and that is deliberate rather than pending. A placement's summary would restate `dir`, a
catalogue's would restate `entries` plus the refusal codes, an identification's would restate `identity` — a second
spelling of the same fact, to be kept in step through every future change, and the day the two disagree you would
believe the summary. Read the field that _is_ the answer.

## The command line

The same answers from any language: `emu-atlas` (installed with the package; `python -m atlas` works without the console
script) takes one question per invocation and writes its contract JSON to stdout. The output is exactly the
serialization above — no CLI dialect — and the conformance vectors are run through the CLI too, so a subprocess consumer
is held to the same bytes a Python caller is.

```bash
emu-atlas detect
emu-atlas savefile-location --core mgba_libretro.so --content /roms/gba/Game.gba
emu-atlas savefile-location --core mgba_libretro.so --installation retrodeck
emu-atlas emulators-for gba
emu-atlas launchable dreamcast /roms/dc/Game.chd
emu-atlas firmware-for-core --core mgba_libretro.so --verify
emu-atlas identify-firmware --md5 a860e8c0b6d573d191e4ec7db1b1e4f6
emu-atlas health
```

Every question above has a subcommand under its own name (`savestate-location`, `screenshot-location`,
`texture-pack-location`, `mod-location`, `soft-patch-candidates`, `systems`, `systems-for-platform`, `platform-ids`,
`rom-location`, `firmware-for-system`, `firmware-inventory`); `emu-atlas --help` lists them. Two answer forms, both from
the sections above: without `--installation` every detected installation answers and the output is the labelled list
(`installation_answers_contract`); with `--installation <kind>` the first handle of that kind answers alone, in the
question's bare shape. `--home` asks about another home directory.

The exit code separates answering from asking. `0` is an answer — an `unresolved` payload and an empty list are answers,
exactly as in the library. `2` is a question that could not be put: a usage error, or an `--installation` kind this
machine does not have (stderr says so; stdout stays empty). Nothing else rides the exit code, so `emu-atlas ... | jq`
never parses a half-answer.

The entry routes (asking one catalogue entry directly) stay library-only for now: naming an entry on a command line is a
design of its own, and the conformance suite names them as deliberately uncovered.

### No Python at all

Each release ships `emu-atlas-<tag>-x86_64-linux.tar.gz`: the CLI with its own pinned CPython 3.14, for consumers that
own no interpreter — a Go client, a shell script, a plugin whose host runtime is not yours to choose. Unpack it anywhere
and run `./emu-atlas <question>`; the tree answers from where it sits, writes nothing outside it, and carries every
capability the library has (3.14 is the first interpreter with `compression.zstd`, so the AppImage reader is fully
open). The bundle is a directory of plain files — the unmodified
[python-build-standalone](https://github.com/astral-sh/python-build-standalone) runtime, the release wheel unpacked into
its `site-packages`, and a launcher script short enough to read — and the full test suite runs against every built
bundle before it is attached. A consumer with a Python does not want this artifact: install the wheel, or vendor the
package (zero dependencies — a directory copy).

## Chained flows

The composite questions a sync plugin actually asks, each as a chain of the queries above. The shapes follow Tender's
flows.

### Flow 1 — "Upload this ROM's save"

```python
installations = atlas.detect(home=user_home)
inst = pick_installation(installations)          # your policy — every handle answers the catalogue step below
if not inst.health().ok:
    return surface_health(inst.health())         # don't sync against a broken installation

system = my_platform_map[rom.platform_slug]      # your table, checked against known_systems() in your own suite
catalogue = inst.emulators_for(system, content_path=rom.file_path)
if not catalogue.entries:                        # no entries is four facts — the caveat says which
    return needs_a_core_from_you(catalogue.caveats)
entry = apply_user_overrides(catalogue.entries, rom)  # your per-game/per-platform pins beat the frontend default

result = entry.savefile_location(content_path=rom.file_path)
if isinstance(result, atlas.Unresolved):
    return unsupported(result.code)              # standalone emulator — atlas will not guess

if result.root_kind == atlas.ROOT_CONTENT_DIRECTORY:
    return skip("saves live next to the ROM — sync policy decision")

save_dir = result.physical_dir or result.dir     # sync the real backing files behind RetroDECK's symlinks
if result.file_set.state in ("observed", "declared") and not result.needs:
    names = result.file_set.files
else:
    names = my_extension_fallback(system)        # unknown, or a template hole only you can fill (see needs)

upload(stat_and_hash(os.path.join(save_dir, n)) for n in names)   # mtime/size/hash are yours to gather
```

### Flow 2 — "Download a save before launch"

Same chain down to `save_dir`, then the direction flips. `dir` is the target either way — one caveat changes what you
have to do first:

```python
if any(c.code == "sorted-dir-missing" for c in result.caveats):
    try:
        os.makedirs(result.dir, exist_ok=True)   # create what RetroArch would create on first save
        target_dir = result.dir
    except OSError:
        target_dir = result.fallback_dir         # creation failed — RetroArch would revert to this root too
else:
    target_dir = result.dir                      # 'sorted-dir-uncreatable' included: dir already IS the unsorted root
```

For a save that does not exist locally yet, atlas can only name the files where a rule card declares them
(`file_set.state == "declared"`); otherwise the expected filename is your call (decky derives
`<rom_stem>.<server extension>`). A declared set with `save_id` in `needs` is the in-between case: the names are stated
but one of them is yours to complete, so a download that writes `<rom_stem>.…` there would land beside the save the
emulator reads, not on it.

### Flow 3 — "Render the BIOS page for a platform"

```python
answer = inst.firmware_for_system(system)                 # verify=False: fast, presence-only
for core in answer.cores:
    row = render_core(core.label, light=core.requirements_met)   # True/False/None → green/red/grey
    for entry in core.requirements:
        if isinstance(entry, atlas.FirmwareAlternatives):        # one of these, the console region decides
            for req in entry.options:
                row.add(req.file_name, need=req.need, present=req.present,
                        checked=req.checked, regions=req.regions)
        else:
            row.add(entry.file_name, need=entry.need, present=entry.present, checked=entry.checked)
# user clicks "verify" → same call with verify=True; hashing is opt-in by design, cache the result yourself
```

### Flow 4 — "The user downloaded firmware — install it"

```python
ident = inst.identify_firmware(md5=downloaded_md5)
if any(c.code == "firmware-content-unidentified" for c in ident.caveats):
    return ask_user()                             # atlas does not know this dump — normal, not an error
for req in ident.requirements:
    copy(tmp_file, req.path)                      # every destination that wants this content, resolved
```

### Flow 5 — "Did the layout drift since the last sync?"

Handles are live — every query re-reads its sources. So drift detection is: ask again, compare.

```python
placement = entry.savefile_location(content_path=rom.file_path)
if isinstance(placement, atlas.Unresolved):
    return                                        # nothing to compare: no location was answered
if placement.dir != last_seen_dir(rom):
    migrate(from_=last_seen_dir(rom), to=placement.dir)
    # placement.sources names which config produced the change — log it for the user
```

## Boundaries — what atlas will not tell you

Honest limits you must cover yourself today (roadmap: `ROADMAP.md`):

- **Vocabulary translation.** Mapping your library's platform identifiers to atlas's system ids is **yours, by design**
  and not on the roadmap — atlas carries no foreign product's vocabulary (`DESIGN.md`, Vocabulary). What it gives you is
  the target set to validate that map against; see
  [Validating your own platform map](#validating-your-own-platform-map). Separately, atlas itself speaks the frontend's
  system names (ES-DE) where a catalogue exists and an atlas slug where none does — `firmware_for_system` states that
  switch with the `emulator-catalogue-unavailable` caveat rather than translating, and closing that seam is atlas's own
  open work.
- **Standalone emulators.** Catalogued, but placements answer `Unresolved` until the standalone block lands.
- **Reverse lookup is a non-goal, not a gap.** Atlas is forward-only (ROM → placement); "which ROM owns this save path"
  is not on the roadmap. Inverting a placement would mean reading a directory and guessing which content produced each
  name — precisely the guess the forward answer exists to avoid, and undecidable wherever a core names saves after
  something other than the ROM stem. **Invert it yourself instead**: walk your own library's forward answers and build
  the index from them, `{placement → rom}` for the ROMs you know about. That index is exact for every ROM you hold, it
  says nothing about files you never asked for (which is the honest answer), and it costs one pass you already have the
  inputs for.
- **File metadata is the client's job, deliberately.** Placements name files; mtime, size and hash are yours to gather.
  That is not atlas withholding a cheap field — it is refusing to make every placement pay for one. Answering it would
  add a stat per named file to answers that mostly do not want it, and a hash means reading the bytes: the seam prices
  them separately for exactly this reason (`file_size` is a stat, `file_digest` reads the file). Ask the filesystem
  directly, or reach through the same seam atlas uses — `Machine.file_size` and `Machine.file_digest` (`md5`/`sha1`) are
  the escape hatch when you want your reads to go through the fixture seam in tests too.
- **Sync decisions.** What to do when local and server disagree is deliberately out of scope (gavel's territory).
