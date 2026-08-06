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

## The standard query pattern

Every query follows the same five steps:

```python
installations = atlas.detect(home="/home/deck")   # 1. find what is installed
inst = installations[0]                           # 2. pick a handle (the caller chooses — atlas never picks a winner)
health = inst.health()                            # 3. check health before trusting answers
answer = inst.save_location(core_so="mgba_libretro.so", content_path=rom_path)   # 4. ask the handle
for caveat in answer.caveats:                     # 5. read the caveats — always
    handle_or_log(caveat.code, caveat.data)
```

Rules that hold for every answer:

- **Answers are frozen value objects.** Fields are data, not behavior; serialize any answer to plain JSON with the
  matching function in `atlas.contract`.
- **Caveats are the degradation channel.** Every stated limitation arrives as a `Caveat` with a stable `code` (part of
  the API contract — branch on it), machine-readable `data`, and a human `message` (not contractual — log it, never
  parse it). An answer without caveats is as good as atlas can make it; an answer with caveats is still an answer, just
  with stated limits.
- **A hole is not an unknown.** `needs` lists holes _you_ fill: `content_dir` from the content at hand, `library_name`
  when the core would not load, and `save_id` when the core names the save after the content's own id. Every hole is
  filled from the content — a value the configs state is never one, because you could not supply it either; atlas
  resolves those itself or states a caveat. Holes are not confined to the directory: a declared file set can be a
  template too. An unknown is something atlas refuses to state — it never guesses to keep a field non-empty.
- **Pass `home` explicitly.** The caller knows which user it serves. A backend running as root must pass the target
  user's home; `os.path.expanduser("~")` is only correct when the process runs as that user.

## Finding installations

```python
installations = atlas.detect(home="/home/deck")
for inst in installations:
    print(inst.kind, inst.kinds, inst.root())
# retrodeck ('retrodeck',) /run/media/deck/Emulation/retrodeck
# emudeck ('emudeck', 'standalone_retroarch_flatpak') /home/deck/Emulation
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

Issue codes (`atlas.HEALTH_ISSUE_*`): `marker-missing`, `marker-unreadable`, `marker-invalid`, `root-missing`,
`saves-root-missing`, `config-unreadable`, `companion-config-missing`. Health is also mirrored into every placement's
caveats (code `health`, the issue code in `data["issue"]`), so a placement computed on a broken installation says so
itself.

## Where does this save live?

The direct route — you name the core:

```python
placement = inst.save_location(
    content_path="/run/media/deck/Emulation/roms/n64/Paper Mario (USA).z64",
    core_so="mupen64plus_next_libretro.so",
)
placement.dir          # '/run/media/deck/Emulation/retrodeck/saves/n64'  — concrete, holes filled
placement.root_kind    # 'savefile_directory' | 'content_directory' | 'system_directory'
placement.needs        # ()  — nothing left for you to fill
placement.file_set     # what the save consists of — see below
placement.granularity  # a Granularity — the value plus the option that selects it — where a rule card states it;
                       # None elsewhere
placement.fallback_dir # set when dir does not exist yet: RetroArch reverts here if it cannot create dir
placement.physical_dir # set when dir reaches its files through symlinks: the real backing path
placement.sources      # provenance — which config said what (prose, for debugging)
```

Without `content_path`, the answer is a template and `needs` names the holes:

```python
placement = inst.save_location(core_so="mupen64plus_next_libretro.so")
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
  whether a verified rule card closes the universe.
- `"declared"` — `files` come from a source-verified rule card (e.g. Flycast's VMU set). A declared name may still be a
  **template** — see below.
- `"unknown"` — atlas refuses to guess; `files` is empty. Fall back to your own knowledge, and treat that fallback as
  yours, not as atlas's answer.

```python
fs = placement.file_set
if fs.state in ("observed", "declared") and not placement.needs:
    paths = [os.path.join(placement.dir, name) for name in fs.files]
else:
    paths = my_own_fallback(system)   # "unknown", or a name only you can complete — your table, your risk
```

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

**A save can lie under two roots.** Where a mode moves only part of the save — Flycast's `VMU A1` moves the first
controller's VMU and leaves the other three plus the console flash on the shared card — atlas states no file set at all
and says why with `file-set-spans-roots`, whose `data["also_under"]` names the other root — as a `root_kind` value, and
resolved the same way `root_kind` itself is, so a mode that leaves the rest "in the system directory" reports
`content_directory` on a machine whose flag moved that directory. `dir` still answers where the moved part goes. A card
describes one root per mode, so the alternative would be to present a fragment as the whole save; treat this answer as
"directory yes, file set no".

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

| Code                                        | Meaning                                                                                       |
| ------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `sorted-dir-missing`                        | `dir` does not exist yet; RetroArch creates it on first save or reverts to `fallback_dir`     |
| `sorted-dir-uncreatable`                    | a file blocks the sorted dir — `dir` already is the unsorted root, `fallback_dir` is `None`   |
| `dead-symlink`                              | the directory is reached through a dead link; nothing can land there                          |
| `symlink-loop`                              | the link chain never settles (`ELOOP`); nothing can land there either — check both codes      |
| `per-game-override` / `…-overrides-present` | a per-game config changes (or could change) the layout                                        |
| `core-unaudited` / `core-suspect`           | no rule card for this core yet / options scan shows save-related keys nobody has verified     |
| `core-multi-option`                         | granularity deliberately unstated — depends on options atlas does not interpret (named in it) |
| `filenames-content-conditional`             | the file set depends on the content: `data` carries the id-less spelling and the scope        |
| `file-set-spans-roots`                      | part of the save stays under another root (`data["also_under"]`) — no file set is stated      |
| `core-unqueryable`                          | the core would not load, `library_name` unknown — a `<library_name>` hole may remain          |
| `content-dir-observation`                   | the files were observed in the ROM's own directory — content files share the name, see below  |
| `content-path-unnamed`                      | the content path names no file; no file names stated, nothing observed                        |
| `health`                                    | the installation itself has issues; `data["issue"]` carries the health code                   |
| `unverified-version`                        | the rule card was never verified against this emulator version                                |
| `sandbox-path-untranslated`                 | a configured path exists only inside the emulator's Flatpak sandbox; nothing there was read   |

Treat caveat codes you do not recognize conservatively: the answer stands, but something about it is degraded.

`content-dir-observation` is the one to plan for if you sync files: with `savefiles_in_content_dir` the save lies next
to the ROM, and the observation matches everything there under the ROM's name — the remaining tracks of a `.cue`, the
cover art, the archive the ROM came in. atlas states the whole set rather than filtering by an invented list of content
extensions; `complete` is `false`, and deciding which of those files are yours to upload is the client's call.

## Which emulator would launch this? (the catalogue)

**RetroDECK handles only, today.** EmuDeck's frontend variants and bare RetroArch (no catalogue at all) are roadmap work
— on those handles you name the core yourself via `save_location(core_so=...)`.

```python
rd = next(i for i in installations if i.kind == "retrodeck")
rd.systems()                       # every system the ES-DE catalogue declares
entries = rd.emulators_for("n64", content_path=rom_path)
entry = entries[0]                 # the effective default (per-game altemulator > per-system choice > declared order)
entry.label, entry.kind            # 'Mupen64Plus-Next', 'libretro'
entry.core_so                      # 'mupen64plus_next_libretro.so' — or None for a standalone emulator
```

The per-game step matches on the path, so pass the ROM the way it lies under the system's ROM directory
(`rd.roms_dir()` + system): gamelist entries are relative to that directory and are resolved against it, and a folder
entry covers the files directly inside it. A path somewhere else — a copy, a staging directory — matches no game, and
the answer is then the per-system one. Two files of the same name at different depths are two different games, and only
the one the gamelist names carries the override.

That comparison is **lexical** — `.`, `..` and repeated slashes are folded, symlinks are not followed. RetroDECK's tree
uses symlinks liberally, so a ROM spelled through one (or through any other route to the same file) will not match its
per-game entry, and you get the per-system answer instead. Resolving links would cost a read per gamelist entry per
query, which is the one thing atlas's one-read-per-source rule exists to avoid; spelling the path the way it lies under
`roms_dir()` costs you nothing.

The entry answers the save question itself, so the core never round-trips through your code:

```python
result = entry.save_location(content_path=rom_path)
if isinstance(result, atlas.Unresolved):
    # a typed domain outcome, not an exception — e.g. standalone emulators are not resolvable yet
    print(result.code)             # 'standalone-unsupported'
else:
    use(result.dir)
```

Always handle the `Unresolved` branch: standalone emulators (DuckStation, PCSX2-SA, …) are catalogued but not yet
resolvable, and pretending otherwise is exactly what atlas refuses to do.

## Firmware

Four questions, verification strictly opt-in. The first three share one answer shape (`FirmwareAnswer`);
`identify_firmware` answers off content, so it has its own (`FirmwareIdentification`):

```python
inst.firmware_for_core(core_so="mgba_libretro.so")          # what does this core want, and where?
inst.firmware_for_system(system="gba")                      # which cores run this system, what does each want?
inst.firmware_inventory(verify=True)                        # everything — declared, present, and unclaimed
inst.identify_firmware(md5="32fbbd84…")                     # this content: what is it, where does it go?
```

Reading a `FirmwareAnswer`:

```python
answer = inst.firmware_for_core(core_so="pcsx_rearmed_libretro.so", verify=True)
answer.root                        # the live system_directory (None + caveat when unset)
for core in answer.cores:
    core.declaration               # 'read' | 'absent' (core not installed) | 'unreadable' — three different empties
    core.requirements_met          # True | False | None — THE field to render (see below)
    for req in core.requirements:
        req.file_name, req.path    # what the core opens, and the absolute resolved destination
        req.need                   # 'required' | 'optional'   — what the emulator asks for
        req.present                # True | False | None       — what lies at the destination
        req.checked                # 'verified' | 'mismatch' | 'unchecked' | 'unknown' | None (nothing there to check)
        req.satisfied              # True | False | None       — present AND nothing contradicts it
    core.refused                   # declarations atlas would not follow, each with the reason it was refused
answer.unclaimed                   # files in the firmware tree that no installed core declares, identified by content
```

`unclaimed` never lists dot-files — the scan globs each directory and a wildcard does not match a leading dot, so
tooling residue like `.directory` stays out of the answer by design (a core that _declares_ a dotted path still gets its
requirement: declarations are resolved, never globbed).

A core's requirement list is what its `.info`'s own `firmware_count` enumerates — RetroArch reads `firmware0_…` up to
`firmware<count-1>_…` and nothing else, so a `.info` without a readable count declares no firmware at all however many
paths it lists, and a path past the count is never asked for. Where the file and that enumeration disagree the core
carries a `firmware-declaration-unread` caveat naming the keys nobody reads. A declared path is composed with the
firmware root the way RetroArch composes it, which has no special case for an absolute one:
`firmware0_path =
"/etc/passwd"` lands at `<root>/etc/passwd`. Read `req.path`, never re-join `req.declared` yourself.

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

A per-system or per-core answer whose emulators were all read and declare nothing carries no such caveat: each entry
says it itself with `declaration="read"` and an empty requirement list, which is the honest "needs nothing".

## Answers as plain JSON

Every answer type has one canonical serializer — the same code the conformance vectors assert:

```python
from atlas import placement_contract, firmware_contract, installation_contract
json.dumps(placement_contract(placement))
```

Structured fields in these dicts are contractual; prose (`sources`, caveat messages) is deliberately absent.

## Chained flows

The composite questions a sync plugin actually asks, each as a chain of the queries above. The shapes follow
decky-romm-sync's flows.

### Flow 1 — "Upload this ROM's save"

```python
installations = atlas.detect(home=user_home)
inst = pick_installation(installations)          # your policy — the catalogue step below needs a retrodeck handle
if not inst.health().ok:
    return surface_health(inst.health())         # don't sync against a broken installation

system = my_slug_map[rom.platform_slug]          # RomM slug → system id is YOUR table today (see "Boundaries")
entries = inst.emulators_for(system, content_path=rom.file_path)
entry = apply_user_overrides(entries, rom)       # your per-game/per-platform pins beat the frontend default

result = entry.save_location(content_path=rom.file_path)
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
answer = inst.firmware_for_system(system=system)          # verify=False: fast, presence-only
for core in answer.cores:
    row = render_core(core.label, light=core.requirements_met)   # True/False/None → green/red/grey
    for req in core.requirements:
        row.add(req.file_name, need=req.need, present=req.present, checked=req.checked)
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
placement = entry.save_location(content_path=rom.file_path)
if placement.dir != last_seen_dir(rom):
    migrate(from_=last_seen_dir(rom), to=placement.dir)
    # placement.sources names which config produced the change — log it for the user
```

## Boundaries — what atlas will not tell you

Honest limits you must cover yourself today (roadmap: `ROADMAP.md`):

- **Vocabulary translation.** Atlas speaks the frontend's system names (ES-DE) where a catalogue exists and an atlas
  slug where none does — `firmware_for_system` states that switch with the `emulator-catalogue-unavailable` caveat
  rather than translating. RomM `platform_slug` → system id is your mapping table until the canonical vocabulary lands.
- **Savestates.** Only savefiles are resolved; `savestate_directory` and the `sort_savestates_*` keys are unread.
- **Standalone emulators.** Catalogued, but placements answer `Unresolved` until the standalone block lands.
- **Reverse lookup.** Atlas is forward-only (ROM → placement); "which ROM owns this save path" is yours.
- **File metadata.** Placements name files; mtime/size/hash of save files are yours to gather.
- **Sync decisions.** What to do when local and server disagree is deliberately out of scope (gavel's territory).
