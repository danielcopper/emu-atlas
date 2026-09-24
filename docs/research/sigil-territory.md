# sigil's territory, mapped against atlas

atlas's account of [argosy-sigil](https://github.com/rommapp/argosy-sigil) describes identification alone. The README
calls sigil "one supplier of `save_id`" (`README.md:74`) and sums the split up as "sigil identifies, atlas locates"
(`:78`); its "What does not live here" gives sigil ROM identification (`:230-231`); the guide's "Composing the two"
names it as one supplier of the `<save_id>` hole (`docs/how-to-use.md:902-907`). At the revision read here sigil does
more: besides the id it resolves which files under a save root belong to one game, and hashes them the way the RomM
server does (sigil's `README.md:7-9`, `:11-12`). The second of those layers is built from the same kind of per-core
knowledge atlas's rule cards hold.

This document states what sigil covers at a pinned revision (§1), sets it fact by fact beside atlas (§2), and states
what follows, with the reason — sigil's layout table has no build dimension (§3). §4 defines how a disagreement between
the two is classified, gives the revisions behind each one about an emulator, and states atlas's own blind spot in the
same area; §5 sets sigil's ids beside the holes atlas leaves for them.

Every claim carries an evidence level:

- **[V]** verified — read from source, extracted from a binary, or observed on disk
- **[D]** derived — follows from verified facts by reading, but not directly observed
- **[O]** open — not yet established

A verdict about what the two sides _state_ is not a verdict about what the emulator _does_. A row sigil states from
observation is [V] for "sigil states it" and nothing more.

## The pins

**[V]** sigil is `rommapp/argosy-sigil`, read at `main` = `8a3b0089676e07f74da2d9ad08979dedf5cae7c0` (committed
2026-09-21 UTC). atlas's README and guide link `rommforge/argosy-sigil` (`README.md:74`, `docs/how-to-use.md:903`),
which answers `301` with `location: https://github.com/rommapp/argosy-sigil` (row S33). Every sigil citation is
`path:line` in that tree; every atlas citation is `path:line` in this repository at `cfb5f0d`. Where a path exists in
both trees (the README), the sentence says whose it is. Emulator and frontend sources are cited as `owner/repository` at
a commit hash; the upstream HEAD read for each is named in §4 with its commit date, as read on 2026-09-24.

**[V]** sigil builds at the pin (`cmake -B build -S . -DSIGIL_BUILD_SHARED=ON`, `cmake --build build`), and its unit
tests pass under `ctest --test-dir build`. The runs cited below call the built shared library: `sigil_save_resolve` with
`open` set to `NULL`, so names are resolved and nothing is hashed, and — to report which layout row answered —
`sigil_layout_find` (`src/save_layout.c:169`), an internal the shared build exports alongside the header's calls. A run
is cited as its input and its output.

## 1. sigil's territory at the pin

### 1.1 The calls

The public surface is the `SIGIL_API` declarations of `include/sigil.h` (`:173-295`). The README groups them into three
calls — "identify the game, locate its saves, hash them" (`README.md:11-12`) — and the language guides walk the same
three (`docs/c.md:8`, `:65`, `:130`).

**Identify** — `sigil_extract_from_path`, `sigil_extract_from_io` (`include/sigil.h:173-184`).

- Asks for a ROM path or a `sigil_io` stream, a platform hint (`AUTO` sniffs the extension), and options: Switch key
  material, the filename-fallback and 3DS-homebrew flags (`:150-168`).
- States `title_id`, `raw_serial`, `save_id`, `platform`, `source` (binary or filename), `usage`, `experimental`, the
  Switch content type and version, and `features` (`:115-136`).
- Leaves to the caller, for the 3DS: "Everything above it … is the emulator's prefix and the consumer's to supply"
  (`README.md:38-41`). Sega CD is not identified at all (`docs/c.md:56-57`).
- **[V]** The README still says of the whole library "Not a save manager or sync client. Sigil tells you the ID; what
  you do with it (find a save folder, upload it somewhere, restore it) is on you." (`README.md:84-86`), while its
  opening paragraph and its "Save units" section describe the library resolving and hashing a game's save files
  (`README.md:7-9`, `:197-206`).

**Platform slugs and streams** — `sigil_platform_from_slug`, `sigil_platform_to_slug` (`include/sigil.h:187-189`) turn a
slug into the enum and back; `sigil_io_open_file`, `_chd`, `_cso`, `_raw_cd`, `_zip`, `_zip_member`, `_zar`,
`sigil_io_close` and `sigil_load_header_key_from_prod_keys` (`:191-212`) open a random-access stream over a file or one
archive member, and load a Switch header key.

**Locate** — `sigil_save_resolve` (`include/sigil.h:279`), freed by `sigil_save_unit_free` (`:280`).

- Asks for `layout` (a core or emulator id), `platform` (optional), `content_path` verbatim, the identify result or
  stored `features`, core option pairs, a listing of relative paths under the root, and an optional `open` callback
  (`:249-262`).
- States `key`, `shape` (`NONE`/`SINGLE`/`MULTI`/`FOLDER`), `members` (present, with path, archive entry and role),
  `expected` (absent), `unkeyed` (shared files seen), `artifact`, and — when `open` is set — the two hashes
  (`:264-277`).
- Leaves to the caller: "Sigil never touches the filesystem here: the caller lists the root and the subfolders
  `sigil_save_layout_subdirs` names, and opens members on request." (`include/sigil.h:215-216`). The root is "the
  directory the emulator writes this game's save into; RetroArch: savefile_directory, plus the core-named subfolder when
  sort_savefiles_enable is on" (`docs/c.md:95-97`; row S2).
- **[V]** Which options to send, sigil's documents state two ways. The README's layout-table text says the default flag
  exists "so a caller sends only the options it has changed" (`README.md:295-296`), and its request table that "only the
  keys the layout row names matter" (`:216`); the C guide says "Pass all of them; only the keys the row names are read"
  (`docs/c.md:90-91`). The two instructions part where a caller's unchanged value is not the value the row's default
  flag assumes: left out, the flag decides; passed, the value does (§1.3).
- **[V]** One field is spelled two ways: the README's request table calls it `content_name` (`README.md:214`), the
  struct `content_path` (`include/sigil.h:253`).
- **[V]** The save-unit layer reads no file. `src/save_unit.c` and `src/save_layout.c` hold no `fopen`, `opendir` or
  `stat` call (a `grep` for the three over both files returns nothing), and hashing opens members only through the
  caller's callback (`src/save_unit.c:291`, `:324`).

**Hash** — `sigil_save_hash` (`include/sigil.h:283`) takes a resolved unit and an `open` callback and fills
`content_hash`, RomM's `content_hash` of the artifact, and `identity_hash`, the same over the non-clock members
(`README.md:243-257`); "hashing opens members through the request's `open` callback" (`README.md:204-206`).

**Helpers** — `sigil_save_layout_subdirs` (`include/sigil.h:285-287`) names the subfolders a layout writes into;
`sigil_content_stem` (`:289-292`) is RetroArch's base name for the content; `sigil_strerror` and `sigil_version`
(`:294-295`).

### 1.2 The layout table

Seventeen rows plus the default (`src/save_layout.c:129-148`), each naming one core by its `.so` short name, optionally
one platform, its member templates, its shared files and its subfolders. The macros that write them are
`M(template, role)`, `M_OPT(template, role, key, value, default)`, `S(template)` and
`S_OPT(template, key, value, default)` (`:4-7`), and the rows are `ROW`, `ROW_SHARED`, `ROW_FULL` and `ROW_DIRS`
(`:124-127`). **[V]** Every line below is read off `src/save_layout.c`: `when` is a member's option condition, `flag`
its default flag — whether an absent option counts as holding the value; the lines follow the table.

| row                        | member, kind                                        | when                                                      | flag  |
| -------------------------- | --------------------------------------------------- | --------------------------------------------------------- | ----- |
| `libretro`                 | `{stem}.srm` primary                                | always                                                    |       |
|                            | `{stem}.rtc` rtc                                    | always                                                    |       |
| `vba_next`                 | `{stem}.srm` primary                                | always                                                    |       |
| `gpsp`                     | `{stem}.srm` primary                                | always                                                    |       |
| `bsnes` `snes`             | `{stem}.srm` primary                                | always                                                    |       |
| `genesis_plus_gx` `segacd` | `{stem}.srm` primary                                | always                                                    |       |
|                            | `{stem}.brm` sidecar                                | `genesis_plus_gx_system_bram` = `per game`                | false |
|                            | `{stem}_{cart_size}_cart.brm` sidecar               | `genesis_plus_gx_cart_bram` = `per game`                  | false |
|                            | `scd_E.brm` shared                                  | `genesis_plus_gx_system_bram` = `per bios`                | true  |
|                            | `scd_U.brm` shared                                  | `genesis_plus_gx_system_bram` = `per bios`                | true  |
|                            | `scd_J.brm` shared                                  | `genesis_plus_gx_system_bram` = `per bios`                | true  |
|                            | `{cart_size}_cart.brm` shared                       | `genesis_plus_gx_cart_bram` = `per cart`                  | true  |
| `mednafen_psx_hw`          | `{stem}.srm` primary                                | `beetle_psx_hw_use_mednafen_memcard0_method` = `libretro` | true  |
|                            | `{stem}.{left_index}.mcr` primary                   | `beetle_psx_hw_use_mednafen_memcard0_method` = `mednafen` | false |
|                            | `{stem}.{right_index}.mcr` sidecar                  | `beetle_psx_hw_enable_memcard1` = `enabled`               | false |
|                            | `mednafen_psx_libretro_shared.0.mcr` shared         | `beetle_psx_hw_shared_memory_cards` = `enabled`           | false |
|                            | `mednafen_psx_libretro_shared.1.mcr` shared         | `beetle_psx_hw_shared_memory_cards` = `enabled`           | false |
| `pcsx_rearmed`             | `{stem}.srm` primary                                | always                                                    |       |
|                            | `pcsx-card2.mcd` shared                             | `pcsx_rearmed_memcard2` = `shared`                        | true  |
| `mednafen_saturn`          | `{stem}.srm` primary                                | `beetle_saturn_save_method` = `libretro`                  | true  |
|                            | `{stem}.bkr` primary                                | `beetle_saturn_save_method` = `mednafen`                  | false |
|                            | `{stem}.bcr` sidecar                                | always                                                    |       |
|                            | `{stem}.smpc` sidecar                               | always                                                    |       |
|                            | `mednafen_saturn_libretro_shared.bkr` shared        | `beetle_saturn_shared_int` = `enabled`                    | false |
|                            | `mednafen_saturn_libretro_shared.smpc` shared       | `beetle_saturn_shared_int` = `enabled`                    | false |
|                            | `mednafen_saturn_libretro_shared.bcr` shared        | `beetle_saturn_shared_ext` = `enabled`                    | false |
| `mednafen_ngp`             | `{stem}.flash` primary                              | always                                                    |       |
| `opera`                    | `opera/per_game/{stem}.{nvram_version}.srm` primary | `opera_nvram_storage` = `per game`                        | true  |
|                            | `opera/shared/nvram.{nvram_version}.srm` shared     | `opera_nvram_storage` = `shared`                          | false |
|                            | `opera/per_game/` subfolder                         | always                                                    |       |
|                            | `opera/shared/` subfolder                           | always                                                    |       |
| `pokemini`                 | `{stem}.eep` primary                                | always                                                    |       |
| `handy`                    | `{stem}.eeprom` primary                             | always                                                    |       |
| `melonds`                  | `{stem}.sav` primary                                | always                                                    |       |
| `fbneo`                    | `fbneo/{romset}.fs` primary                         | always                                                    |       |
|                            | `fbneo/{romset}.nv` sidecar                         | always                                                    |       |
|                            | `fbneo/{romset}.memcard` sidecar                    | `fbneo-memcard-mode` = `per-game`                         | false |
|                            | `fbneo/shared.memcard` shared                       | `fbneo-memcard-mode` = `shared`                           | false |
|                            | `fbneo/` subfolder                                  | always                                                    |       |
| `mame2003_plus`            | `mame2003-plus/nvram/{romset}.nv` primary           | `mame2003-plus_core_save_subfolder` = `enabled`           | true  |
|                            | `mame2003-plus/hi/{romset}.hi` sidecar              | `mame2003-plus_core_save_subfolder` = `enabled`           | true  |
|                            | `nvram/{romset}.nv` primary                         | `mame2003-plus_core_save_subfolder` = `disabled`          | false |
|                            | `hi/{romset}.hi` sidecar                            | `mame2003-plus_core_save_subfolder` = `disabled`          | false |
|                            | `mame2003-plus/nvram/` subfolder                    | always                                                    |       |
|                            | `mame2003-plus/hi/` subfolder                       | always                                                    |       |
|                            | `nvram/` subfolder                                  | always                                                    |       |
|                            | `hi/` subfolder                                     | always                                                    |       |
| `dosbox_pure`              | `{stem}.pure.zip` primary                           | always                                                    |       |
| `same_cdi`                 | `same_cdi/nvram/{stem}/` primary                    | `same_cdi_nvram_saves` = `enabled`                        | true  |
|                            | `same_cdi/nvram/` subfolder                         | always                                                    |       |
| `nestopia` `fds`           | `{stem}.srm` primary                                | always                                                    |       |
|                            | `{stem}.sav` sidecar                                | `nestopia_fds_savefile_format` = `sav_ups`                | true  |
|                            | `{stem}.ups` sidecar                                | `nestopia_fds_savefile_format` = `ups`                    | false |
|                            | `{stem}.ips` sidecar                                | `nestopia_fds_savefile_format` = `ips`                    | false |

Lines (`src/save_layout.c`): `libretro` `:129`, entries `:11`, `:12`; `vba_next` `:132`, entries `:16`; `gpsp` `:133`,
entries `:16`; `bsnes` `:134`, entries `:121`; `genesis_plus_gx` `:135`, entries `:20`, `:21`, `:22`, `:25`, `:26`,
`:27`, `:28`; `mednafen_psx_hw` `:136`, entries `:32`, `:33`, `:34`, `:37`, `:38`; `pcsx_rearmed` `:137`, entries `:54`,
`:57`; `mednafen_saturn` `:138`, entries `:42`, `:43`, `:44`, `:45`, `:48`, `:49`, `:50`; `mednafen_ngp` `:139`, entries
`:61`; `opera` `:140`, entries `:65`, `:68`, `:70`; `pokemini` `:141`, entries `:73`; `handy` `:142`, entries `:77`;
`melonds` `:143`, entries `:81`; `fbneo` `:144`, entries `:85`, `:86`, `:87`, `:90`, `:92`; `mame2003_plus` `:145`,
entries `:95`, `:96`, `:97`, `:98`, `:101`; `dosbox_pure` `:146`, entries `:105`; `same_cdi` `:147`, entries `:109`,
`:111`; `nestopia` `:148`, entries `:114`, `:115`, `:116`, `:117`.

**[V]** What sigil gives as evidence is the README's "Source" column (`README.md:309-327`), under the sentence "Every
row was read from the core's source or its libretro docs page; the names are the core's literals." (`:306-307`). Each
entry names a file and at most a function. One row cites a commit, `707d1be`, without naming its repository
(`mednafen_psx_hw`, `:315`); one cites an observation, "observed on device" (`pcsx_rearmed`, `:316`). The five commits
that touch `src/save_layout.c` and `src/save_unit.c` carry a subject line and no body
(`git log -- src/save_layout.c src/save_unit.c`).

**[V]** The table has no build dimension. A row is an id, a platform, members, shared files and subfolders
(`src/save_layout.h:24-33`); a member is a template, a role and one option condition (`:8-14`); the request carries no
core version (`include/sigil.h:249-262`). A search for `version`, `revision` or `build` over `src/save_layout.c`,
`src/save_layout.h`, `src/save_unit.c` and `include/sigil.h` finds only struct versions, the build macros and the Switch
content version. One row stands for every build of its core.

**[V]** sigil's own suite holds the table against listings it writes itself (`tests/unit_save_unit.c:189` for the Sega
CD row, `:233` for Beetle PSX and Beetle Saturn); no test names a shipped core binary (a `grep` for `_libretro` over
`tests/` finds only the shared-card name `mednafen_psx_libretro_shared.0.mcr`, `:236`, `:255`).

### 1.3 How a unit is matched

Only as far as it decides an output (`src/save_unit.c`):

- **[V] `{stem}`** is `sigil_content_stem` of the content path: the part after the last `#`, then after the last `/` or
  `\`, cut at the last `.` unless the name starts with it (`src/save_unit.c:19-37`). **`{romset}` is the same string**
  (`:75`). Run: `sigil_content_stem("/x/y/Game (USA).zip#Game (USA).md")` → `Game (USA)`; `"a/b.tar.gz"` → `b.tar`;
  `".hidden"` → `.hidden`. The unit's `key` is that stem for every layout (`:389`), a folder layout included, where the
  header says `save_id` (`include/sigil.h:266`).
- **[V] `{cart_size}`** comes from `genesis_plus_gx_cart_size`: `128k`…`4meg` to `128Kbit`…`4Mbit`, an absent option to
  `4Mbit`, any other value to nothing (`:56-65`, read at `:78-80`). **`{nvram_version}`** is `opera_nvram_version`,
  default `0` (`:81-84`). **`{left_index}`** and **`{right_index}`** are `beetle_psx_hw_memcard_left_index` and
  `_right_index`, defaults `0` and `1` (`:85-92`). `{title_id}` and `{save_id}` come from the identify result and are
  empty without one (`:76-77`). Every variable is filled from what the caller passed — the content path, the options,
  the identify result; none is read off a machine.
- **[V] A template whose variable has no value is skipped**, not reported (`:104`, `:189`): `genesis_plus_gx_cart_size`
  = `disabled` drops both cart files.
- **[V] An option the caller did not pass** takes the member's default flag: `condition_holds` answers the flag where
  the key is absent (`:48-54`, the flag at `src/save_layout.h:13`). Run: `mednafen_psx_hw`, content `Game.cue`, listing
  `Game.srm`, `Game.1.mcr`, `mednafen_psx_libretro_shared.0.mcr`, no options → `members` = `Game.srm` (primary),
  `expected` = none, `unkeyed` = none. The present `Game.1.mcr` is in no field: its member's flag says an absent
  `beetle_psx_hw_enable_memcard1` does not hold `enabled` (`src/save_layout.c:34`).
- **[V] `expected`** takes an applicable file template that is absent from the listing only where its role is `primary`,
  or `rtc` with `SIGIL_FEATURE_RTC` set (`src/save_unit.c:197-203`). An absent sidecar is dropped; an absent shared file
  is dropped (`:210`).
- **[V] `unkeyed`** takes a shared template only where the listing holds it (`:206-215`).
- **[V] The listing is walked only under a folder template.** `collect` walks the layout's templates (`:186-215`); a
  template ending in `/` sends it to `add_folder_members`, which takes every file of the listing below that folder as a
  member (`:158-172`), and the loop continues before the `expected` branch (`:191-194`), so an absent folder is in no
  field. Runs, `same_cdi`, content `/roms/cdimono1/Game.chd`: an empty listing → shape `NONE`, `members`, `expected` and
  `unkeyed` empty; the listing `same_cdi/nvram/Game/cdi.nvr`, `same_cdi/nvram/Game/sub/x.bin`, `other.srm` → shape
  `FOLDER`, both files under the folder as primary members (entries `Game/cdi.nvr`, `Game/sub/x.bin`), artifact
  `Game.zip`, and `other.srm` in no field. A file no template names and no folder template covers is passed over.
- **[V] The row** is the one whose layout id equals the request's and whose platform matches, else the one without a
  platform, else the libretro default row (`src/save_layout.c:169-178`). Platform aliases fold `scd`, `sega_cd`,
  `sega-cd`, `mega_cd`, `mega-cd` and `megacd` to `segacd`, `sfc`/`sfam` to `snes`, `ps1`/`playstation` to `psx`,
  `famicom_disk_system`/`fds` to `fds` (`:151-161`). Runs of `sigil_layout_find`: `genesis_plus_gx` with `megacd` or
  `segacd` → the `segacd` row; with `megacdjp`, `megadrive` or no platform → the default row; `bsnes` with `sfc` → the
  `snes` row, with `snesna` → the default row; `nestopia` with `famicom` → the default row.
- **[V] The default row** is `{stem}.srm` primary and `{stem}.rtc` rtc (`:10-13`, `:129`), and it answers every id
  without a row (`README.md:212`).
- **[V] The shape** is `NONE` with no member, `FOLDER` when a folder template matched, `SINGLE` for one member, `MULTI`
  otherwise (`src/save_unit.c:410-413`); the artifact is the member's own name, `<first member>.zip`, or `<key>.zip`
  (`:349-365`).

### 1.4 Identify, as far as atlas touches it

**[V]** Which platforms return an id, and its spelling. `save_id` defaults to `title_id` wherever the platform set no
value of its own, except under `folder-split` (`src/sigil.c:351-361`).

- `psx` — `title_id` four upper-case letters, `-`, five digits (`SLUS-12345`); `save_id` the same; `usage` `file-prefix`
  (`src/cnf_parser.c:100-103`).
- `ps2` — `title_id` as `psx`; `save_id` `BA`/`BE`/`BI` by the serial's region letter, then the `title_id`
  (`BASLUS-20152`); `usage` `folder-prefix` (`src/ps2.c:12-29`).
- `dreamcast` — `title_id` the IP.BIN product number, trailing whitespace trimmed, cut at a NUL; `save_id` the same;
  `usage` `file-prefix` (`src/dreamcast.c:44-70`, `src/sigil.c:351-361`).
- `wiiu` — `title_id` the low eight hex digits, upper case (`10143500`); `save_id` the same in lower case, the low word
  alone; `usage` `folder-exact` (`src/wiiu_wua.c:14-34`, `:98-102`).
- `3ds` — `title_id` sixteen hex digits, upper case; `save_id` `<high 8>/<low 8>`, lower case; `usage` `folder-split`
  (`src/threeds.c:264-274`).
- `wii` — from a disc, `title_id` eight hex digits of the ASCII game id, upper case, and `save_id` the same in lower
  case, `usage` `folder-exact` (`src/wii.c:75-77`, `:178-180`); from a `.wad`, sixteen hex digits and `<high>/<low>` in
  lower case, `usage` `folder-split` (`:134-137`).
- `gamecube` — `title_id` eight hex digits of the ASCII game id; `save_id` the four ASCII characters (`GZLE`); `usage`
  `file-prefix` (`src/wii.c:79-85`, `:181-183`).
- `psvita` — `TITLE_ID` from `param.sfo` for both; `usage` `folder-exact` (`src/psvita.c:39-45`).
- `xbox` — `title_id` the certificate serial (`MS-100`); `save_id` the raw hex id (`4D530064`); `usage` `folder-exact`
  (`src/xbox.c:108-116`).
- `gb`, `gbc`, `snes` — no id; `features` carries the cart's clock; `usage` `file-prefix` (`README.md:127-130`,
  `include/sigil.h:81-86`).

sigil also returns ids for `psp`, `ps3`, `switch` and `xbox360` (`README.md:108-125`). atlas declares no `<save_id>`
hole for any of these four (search: `<save_id>` in `atlas/data/*.json`, `TEMPLATE_SAVE_ID` in `atlas/installations.py`;
§5 lists the holes); for PPSSPP and RPCS3 it refuses the per-game directory names instead, rows I9 and I10.

**[V]** The filename fallback (`source` = `filename`) spells the same ids the same way: the PS2 card stem
(`src/filename.c:270`, `:307`), the Wii U low word in lower case (`:189`, `:199`, `:207`), the 3DS split path — left
empty where the name carries the low word alone (`:143-150`, `:158`) — and the Wii and GameCube ids from a
four-character bracket (`:214-232`).

## 2. The map

Every row states one fact. The verdicts are `same` (both state it alike), `different` (both state it, unalike),
`sigil only` and `atlas only`, and each is a verdict about what the two sides _state_. Every `different` row outside the
identify rows also carries one of five classes, which §4 defines and backs with the revisions: _different builds_,
_sigil defect_, _atlas defect_, _atlas's documents_, _shape_. The identify rows carry none; each has its own spelling
statement in §5.

What is compared where. sigil's rows are matched against atlas's rule card of the same key. Five rows whose core has no
card are matched against atlas's memory record for the core instead: the record says which of RetroArch's two
per-content files, `<stem>.srm` and `<stem>.rtc`, the core fills (`atlas/data/save_memory.json:3`). A template's _name_
is compared at the core's registered values (every cart size, every NVRAM version, the index defaults); _when_ a file
belongs to the unit, and as whose, is compared over every option combination both sides read (§2.2).

**[V]** Counts: sigil has 17 layout rows and the default; atlas has 53 rule cards, 61 audit entries and 72 memory
records. 12 of sigil's rows have an atlas card under the same key (`bsnes`, `dosbox_pure`, `fbneo`, `genesis_plus_gx`,
`mame2003_plus`, `mednafen_ngp`, `mednafen_psx_hw`, `mednafen_saturn`, `melonds`, `opera`, `pcsx_rearmed`, `pokemini`),
the other 5 a memory record and no card (`gpsp`, `handy`, `nestopia`, `same_cdi`, `vba_next`); 41 of atlas's cards have
no sigil row.

### 2.1 atlas's vectors handed to sigil

**[V]** Every savefile answer in `vectors/machines/` that names a core and a content path — 217 of them — was handed to
sigil: `layout` the core's short name, `platform` the system the question names, `content_path` the question's,
`options` the answer's `granularity.readings` that carry a value, and as `listing` every file of the fixture under
sigil's root, relative to it. sigil's root is the answer's `dir`, less the card's own subtree where `dir` ends in one
(row S4). Each vector ran twice: once on its own listing, and once with every name atlas's answer states added to the
listing, so a file sigil claims shows in `members` rather than `expected`, and a shared file shows in `unkeyed` wherever
sigil claims it. The verdict compares the second run: atlas's per-game names against `members` + `expected`, its shared
names against `unkeyed`. "atlas states no set" is an answer whose `file_set` is `unknown`.

**Answers on a core sigil has a row for.** 18 answers over 8 cores: 11 agree, 3 differ, and in 4 atlas states no set
while sigil still names one.

| core              | sigil's row                          | answers | same | different | atlas states no set |
| ----------------- | ------------------------------------ | ------- | ---- | --------- | ------------------- |
| `opera`           | `opera`                              | 8       | 4    | 0         | 4                   |
| `mednafen_saturn` | `mednafen_saturn`                    | 1       | 0    | 1         | 0                   |
| `genesis_plus_gx` | `genesis_plus_gx@segacd`, `libretro` | 3       | 2    | 1         | 0                   |
| `fbneo`           | `fbneo`                              | 1       | 1    | 0         | 0                   |
| `gpsp`            | `gpsp`                               | 1       | 1    | 0         | 0                   |
| `mame2003_plus`   | `mame2003_plus`                      | 1       | 0    | 1         | 0                   |
| `melonds`         | `melonds`                            | 1       | 1    | 0         | 0                   |
| `mednafen_ngp`    | `mednafen_ngp`                       | 2       | 2    | 0         | 0                   |

- `opera-nvram-subdir-unanswered-when-no-mode-is-established` (`opera`): atlas states no set; sigil claims
  `opera/per_game/<stem>.0.srm`.
- `opera-card-retires-when-the-core-registers-one-of-its-two-switches` (`opera`): atlas states no set; sigil claims
  `opera/per_game/<stem>.0.srm`.
- `opera-shared-nvram-unanswered-when-only-the-storage-switch-is-read` (`opera`): atlas states no set; sigil claims
  `opera/per_game/<stem>.0.srm`.
- `opera-nvram-version-outside-the-registered-set-retires-the-card` (`opera`): atlas states no set; sigil claims
  `opera/per_game/<stem>.0.srm`.
- `a-rule-card-observes-its-three-role-files` (`mednafen_saturn`): sigil only `<stem>.srm`; atlas only `<stem>.bkr`.
- `a-value-set-on-the-machine-beats-the-cards-recorded-default` (`mame2003_plus`): atlas only `cfg/<stem>.cfg`; a
  directory whose names atlas does not state `diff/<unnamed>/`, `memcard/<unnamed>/`.
- `a-cd-system-answers-with-the-cores-own-bram-tree` (`genesis_plus_gx`): sigil only `<stem>.srm`.

**[V]** The three differences recur in the sweep of §2.2, where Genesis Plus GX differs in all 29 runs, Beetle Saturn in
12 of 13 and MAME 2003-Plus in all 3. The four Opera answers are ones in which atlas establishes no mode — no switch it
could read, one of the two switches unregistered, a version outside the registered set — and sigil, told nothing,
answers its defaults.

**Answers on a core sigil has no row for.** 199 answers over 43 cores, which sigil answers from its default row: 36
agree, 98 differ, and in 65 atlas states no set.

| core                 | answers | same | different | atlas states no set |
| -------------------- | ------- | ---- | --------- | ------------------- |
| `DoubleCherryGB`     | 1       | 1    | 0         | 0                   |
| `applewin`           | 6       | 0    | 0         | 6                   |
| `arduous`            | 1       | 1    | 0         | 0                   |
| `bluemsx`            | 1       | 0    | 1         | 0                   |
| `boom3`              | 1       | 0    | 1         | 0                   |
| `cannonball`         | 1       | 0    | 1         | 0                   |
| `desmume2015`        | 1       | 0    | 1         | 0                   |
| `dice`               | 1       | 0    | 1         | 0                   |
| `fbalpha2012_cps2`   | 1       | 0    | 1         | 0                   |
| `fbalpha2012_neogeo` | 1       | 0    | 1         | 0                   |
| `flycast`            | 18      | 0    | 13        | 5                   |
| `gambatte`           | 1       | 1    | 0         | 0                   |
| `geolith`            | 1       | 0    | 1         | 0                   |
| `hatari`             | 3       | 0    | 2         | 1                   |
| `holani`             | 1       | 0    | 1         | 0                   |
| `mame`               | 6       | 0    | 1         | 5                   |
| `mame2000`           | 2       | 0    | 2         | 0                   |
| `mame2003`           | 1       | 0    | 1         | 0                   |
| `mame2010`           | 1       | 0    | 1         | 0                   |
| `mednafen_psx`       | 1       | 0    | 0         | 1                   |
| `melondsds`          | 1       | 1    | 0         | 0                   |
| `mgba`               | 16      | 7    | 1         | 8                   |
| `mupen64plus_next`   | 16      | 16   | 0         | 0                   |
| `neocd`              | 1       | 1    | 0         | 0                   |
| `noods`              | 1       | 0    | 1         | 0                   |
| `parallel_n64`       | 1       | 1    | 0         | 0                   |
| `pcsx2`              | 5       | 0    | 4         | 1                   |
| `picodrive`          | 1       | 1    | 0         | 0                   |
| `ppsspp`             | 12      | 0    | 0         | 12                  |
| `prboom`             | 1       | 0    | 1         | 0                   |
| `puae`               | 71      | 0    | 49        | 22                  |
| `puae2021`           | 3       | 0    | 3         | 0                   |
| `quasi88`            | 2       | 1    | 1         | 0                   |
| `race`               | 1       | 0    | 1         | 0                   |
| `sameboy`            | 1       | 1    | 0         | 0                   |
| `scummvm`            | 4       | 0    | 2         | 2                   |
| `snes9x`             | 1       | 1    | 0         | 0                   |
| `snes9x2005`         | 1       | 1    | 0         | 0                   |
| `swanstation`        | 2       | 0    | 2         | 0                   |
| `tyrquake`           | 1       | 0    | 1         | 0                   |
| `vbam`               | 2       | 2    | 0         | 0                   |
| `virtualjaguar`      | 4       | 0    | 2         | 2                   |
| `vitaquake2`         | 1       | 0    | 1         | 0                   |

**[V]** The agreeing ones are answers whose save is named `<stem>.srm` (and `<stem>.rtc`): RetroArch's own files, or,
for QUASI88 at its default, the core's differencing file under the same name. The differing ones are cores atlas has a
card for (Flycast, LRPS2, PUAE, SwanStation, the MAME and FB Alpha generations, and others in the table), where the
default row's `.srm` is not the save; cores whose memory record says they fill no save RAM (`bluemsx`, `dice`,
`holani`), where no `.srm` is written at all; and one `mgba` answer in which a `.rtc` left behind sits in the listing:
sigil bundles it as the clock member, atlas's record says mGBA writes no clock for a Game Boy Advance cartridge
(`a-file-left-behind-does-not-become-the-answer`).

### 2.2 Every option combination both sides read

**[V]** For each core both sides cover, a fixture machine of the arrangement atlas's vectors use, with one catalogue
entry for the core and the core's registered options as atlas's vectors model them, was answered by atlas once per
combination of the options both sides read (written to `retroarch-core-options.cfg`), and once with no options file at
all. sigil got the answer's readings as options, and as listing every name either side could state for the core. Where
no vector registers options for a core, the registration is atlas's: `mednafen_psx_hw` takes `mednafen_psx`'s under the
`beetle_psx_hw_` prefix — the card's "one implementation behind two option prefixes"
(`atlas/data/core_oddities.json:4062`) — and `pcsx_rearmed` the values and default its card states
(`atlas/data/core_oddities.json:4782`). For Beetle Saturn, the key only sigil reads, `beetle_saturn_save_method`, was
swept on sigil's side alone (absent, `libretro`, `mednafen`). `mednafen_psx` has no sigil row and is run for comparison:
sigil answers it from the default row. 96 runs: 34 agree, 62 differ.

| core              | sigil's row              | runs | same | different |
| ----------------- | ------------------------ | ---- | ---- | --------- |
| `genesis_plus_gx` | `genesis_plus_gx@segacd` | 29   | 0    | 29        |
| `mednafen_psx_hw` | `mednafen_psx_hw`        | 9    | 4    | 5         |
| `mednafen_psx`    | `libretro`               | 9    | 2    | 7         |
| `pcsx_rearmed`    | `pcsx_rearmed`           | 3    | 1    | 2         |
| `mednafen_saturn` | `mednafen_saturn`        | 13   | 1    | 12        |
| `opera`           | `opera`                  | 21   | 21   | 0         |
| `fbneo`           | `fbneo`                  | 4    | 0    | 4         |
| `mame2003_plus`   | `mame2003_plus`          | 3    | 0    | 3         |
| `bsnes`           | `bsnes@snes`             | 1    | 1    | 0         |
| `mednafen_ngp`    | `mednafen_ngp`           | 1    | 1    | 0         |
| `pokemini`        | `pokemini`               | 1    | 1    | 0         |
| `melonds`         | `melonds`                | 1    | 1    | 0         |
| `dosbox_pure`     | `dosbox_pure`            | 1    | 1    | 0         |

- `genesis_plus_gx`, `cd-bios-bram` (and 28 more runs alike): sigil only `<stem>.srm`.
- `mednafen_psx_hw`, `srm+second-card-shared`: sigil only `<stem>.1.mcr`, `mednafen_psx_libretro_shared.0.mcr`.
- `mednafen_psx_hw`, `srm-only`: sigil only `mednafen_psx_libretro_shared.0.mcr`, `mednafen_psx_libretro_shared.1.mcr`.
- `mednafen_psx_hw`, `mcr+second-card-shared`: sigil only `<stem>.0.mcr`, `<stem>.1.mcr`.
- `mednafen_psx_hw`, `mcr-only-shared`: sigil only `<stem>.0.mcr`, `mednafen_psx_libretro_shared.1.mcr`.
- `mednafen_psx_hw`, no options (sigil told nothing): atlas only `<stem>.1.mcr`.
- `mednafen_psx`, `srm+second-card-shared`: atlas only `mednafen_psx_libretro_shared.1.mcr`.
- `mednafen_psx`, `srm+second-card` (and 1 more run alike): atlas only `<stem>.1.mcr`.
- `mednafen_psx`, `mcr+second-card-shared`: sigil only `<stem>.srm`; atlas only `mednafen_psx_libretro_shared.0.mcr`,
  `mednafen_psx_libretro_shared.1.mcr`.
- `mednafen_psx`, `mcr+second-card`: sigil only `<stem>.srm`; atlas only `<stem>.0.mcr`, `<stem>.1.mcr`.
- `mednafen_psx`, `mcr-only-shared`: sigil only `<stem>.srm`; atlas only `mednafen_psx_libretro_shared.0.mcr`.
- `mednafen_psx`, `mcr-only`: sigil only `<stem>.srm`; atlas only `<stem>.0.mcr`.
- `pcsx_rearmed`, `enabled`: atlas only `pcsx-card2.mcd`.
- `pcsx_rearmed`, no options (sigil told nothing): sigil only `pcsx-card2.mcd`.
- `mednafen_saturn`, `both-shared` (and 1 more run alike): sigil only `<stem>.bcr`, `<stem>.smpc`, `<stem>.srm`.
- `mednafen_saturn`, `both-shared` with `beetle_saturn_save_method=mednafen`: sigil only `<stem>.bcr`, `<stem>.bkr`,
  `<stem>.smpc`.
- `mednafen_saturn`, `cartridge-shared` (and 1 more run alike): sigil only `<stem>.bcr`, `<stem>.srm`; atlas only
  `<stem>.bkr`.
- `mednafen_saturn`, `cartridge-shared` with `beetle_saturn_save_method=mednafen`: sigil only `<stem>.bcr`.
- `mednafen_saturn`, `internal-shared` (and 1 more run alike): sigil only `<stem>.smpc`, `<stem>.srm`.
- `mednafen_saturn`, `internal-shared` with `beetle_saturn_save_method=mednafen`: sigil only `<stem>.bkr`,
  `<stem>.smpc`.
- `mednafen_saturn`, `per-game` (and 2 more runs alike): sigil only `<stem>.srm`; atlas only `<stem>.bkr`.
- `fbneo`, `disabled` (and 3 more runs alike): sigil only `fbneo/<stem>.nv`.
- `mame2003_plus`, `enabled` (and 1 more run alike): atlas only `mame2003-plus/cfg/<stem>.cfg`; a directory whose names
  atlas does not state `mame2003-plus/memcard/`, `mame2003-plus/diff/`.
- `mame2003_plus`, `disabled`: atlas only `cfg/<stem>.cfg`; a directory whose names atlas does not state `memcard/`,
  `diff/`.

How the cells read. sigil's default flags decide in two places: in the "no options" runs, for every key, and wherever
`beetle_saturn_save_method` is absent, because atlas's readings never carry a key atlas does not read. In every other
run sigil gets atlas's readings, which carry the registered default of a switch no file states. In `mednafen_psx_hw`'s
`srm-only` and `srm+second-card-shared` modes sigil reports a shared card as `unkeyed` that atlas says the mode never
writes (T35, T37; §4); the listing holds it because a sweep listing holds every name any mode could leave behind, which
is also what a machine that switched modes holds.

### 2.3 The rows

**[V]** 234 rows: 145 `same`, 33 `different`, 22 `sigil only`, 34 `atlas only`. By kind: 17 layout rows (L), 90 template
rows (T), 58 option rows (O), 21 rows of what a card states beyond sigil's row (A), 35 answer-field rows (S) and 13
identify rows (I). Of the 29 `different` rows outside the identify rows, 10 are _sigil defect_, 5 _different builds_, 1
_atlas defect_, 3 _atlas's documents_ and 10 _shape_; the other 4 `different` rows are identify rows, §5's.

A core's rows follow under its own heading. A T row with `name · when` holds two facts under two ids: whether the name
agrees, and whether the file belongs to the unit under the same options and as the same party's (per game or shared) —
the second counted over the sweep's (combination, name) cases, with the no-options run left out, since there sigil is
told nothing. The note column gives a `different` row's class, and `atlas:` there is atlas's default for the key. An A
row names what atlas's card states beyond sigil's row. A card's _anchors_ pin each recorded name and option key either
to the whole string the auditor read in the shipped `.so`, "which a test re-reads there", or to a stated reason no such
string can carry it (`atlas/data/core_oddities.json:3`). A card's _content class_ is the kind of content its names hold
for, read off the content path. atlas writes the content's stem `<rom_stem>` in its cards; this document writes `<stem>`
for both sides. The identify rows I1–I13 are §5's list.

### `vba_next`

L1, a core both describe — same: atlas has no card; its memory record says which of the frontend's files the core fills
(`gba`: `save_ram`). Sources (atlas · sigil): `atlas/data/save_memory.json:1663` · `src/save_layout.c:132`.

| id | fact                   | verdict | note                                  |
| -- | ---------------------- | ------- | ------------------------------------- |
| T1 | `{stem}.srm` (primary) | same    | the frontend's file; record: save RAM |
| T9 | No `{stem}.rtc`        | same    |                                       |

Sources (atlas · sigil): T1 `atlas/data/save_memory.json:1670` · `src/save_layout.c:16`; T9
`atlas/data/save_memory.json:1671` · `README.md:312`.

### `gpsp`

L2, a core both describe — same: atlas has no card; its memory record says which of the frontend's files the core fills
(`gba`: `save_ram`). Sources (atlas · sigil): `atlas/data/save_memory.json:506` · `src/save_layout.c:133`.

| id  | fact                   | verdict | note                                  |
| --- | ---------------------- | ------- | ------------------------------------- |
| T2  | `{stem}.srm` (primary) | same    | the frontend's file; record: save RAM |
| T10 | No `{stem}.rtc`        | same    |                                       |

Sources (atlas · sigil): T2 `atlas/data/save_memory.json:513` · `src/save_layout.c:16`; T10
`atlas/data/save_memory.json:514` · `README.md:312`.

### `bsnes` on `snes`

L3, a core both describe — same: atlas has a rule card, audit verdict `card`. Sources (atlas · sigil):
`atlas/data/core_oddities.json:93` · `src/save_layout.c:134`.

| id       | fact                                | verdict     | note      |
| -------- | ----------------------------------- | ----------- | --------- |
| T11      | No `{stem}.rtc`                     | same        |           |
| T12, T13 | `{stem}.srm` (primary): name · when | same · same | sweep 1/1 |

Sources (atlas · sigil): T11 `atlas/data/core_oddities.json:122` · `README.md:313`; T12
`atlas/data/core_oddities.json:107` · `src/save_layout.c:121`; T13 the sweep · `src/save_layout.c:121`.

atlas only — A1 the card's anchors bind 1 of its 1 recorded names and keys to whole strings of the shipped `.so`.
Sources (atlas · sigil): A1 `atlas/data/core_oddities.json:115` · none (sigil states per-row sources, README table).

### `genesis_plus_gx` on `segacd`

L4, a core both describe — same: atlas has a rule card, audit verdict `card`. Sources (atlas · sigil):
`atlas/data/core_oddities.json:4074` · `src/save_layout.c:135`.

| id       | fact                                                           | verdict                | note                                 |
| -------- | -------------------------------------------------------------- | ---------------------- | ------------------------------------ |
| T14, T15 | `{stem}.srm` (primary): name · when                            | sigil only · different | sigil defect; in no mode; sweep 0/28 |
| T16, T17 | `{stem}.brm` (sidecar): name · when                            | same · same            | sweep 28/28                          |
| T18, T19 | `{stem}_{cart_size}_cart.brm` (sidecar): name · when           | same · same            | sweep 24/24                          |
| T20, T21 | `scd_E.brm` (shared): name · when                              | same · same            | sweep 28/28                          |
| T22, T23 | `scd_U.brm` (shared): name · when                              | same · same            | sweep 28/28                          |
| T24, T25 | `scd_J.brm` (shared): name · when                              | same · same            | sweep 28/28                          |
| T26, T27 | `{cart_size}_cart.brm` (shared): name · when                   | same · same            | sweep 24/24                          |
| O3       | `genesis_plus_gx_cart_size` fills `{cart_size}`                | same                   |                                      |
| O4       | Omitted `genesis_plus_gx_cart_size` reads as `4Mbit`           | same                   | atlas: `4meg`                        |
| O5       | Reads `genesis_plus_gx_cart_bram`                              | same                   |                                      |
| O6       | Reads `genesis_plus_gx_system_bram`                            | same                   |                                      |
| O7       | Value `per cart` of `genesis_plus_gx_cart_bram`                | same                   |                                      |
| O8       | Omitted `genesis_plus_gx_cart_bram` holds `per cart`           | same                   | atlas: `per cart`                    |
| O9       | Value `per game` of `genesis_plus_gx_cart_bram`                | same                   |                                      |
| O10      | Omitted `genesis_plus_gx_cart_bram` does not hold `per game`   | same                   | atlas: `per cart`                    |
| O11      | Value `per bios` of `genesis_plus_gx_system_bram`              | same                   |                                      |
| O12      | Omitted `genesis_plus_gx_system_bram` holds `per bios`         | same                   | atlas: `per bios`                    |
| O13      | Value `per game` of `genesis_plus_gx_system_bram`              | same                   |                                      |
| O14      | Omitted `genesis_plus_gx_system_bram` does not hold `per game` | same                   | atlas: `per bios`                    |

Sources (atlas · sigil): T14 none (every mode's groups) · `src/save_layout.c:20`; T15 the sweep ·
`src/save_layout.c:20`; T16 `atlas/data/core_oddities.json:4387` · `src/save_layout.c:21`; T17 the sweep ·
`src/save_layout.c:21`; T18 `atlas/data/core_oddities.json:4165` · `src/save_layout.c:22`; T19 the sweep ·
`src/save_layout.c:22`; T20 `atlas/data/core_oddities.json:4121` · `src/save_layout.c:25`; T21 the sweep ·
`src/save_layout.c:25`; T22 `atlas/data/core_oddities.json:4122` · `src/save_layout.c:26`; T23 the sweep ·
`src/save_layout.c:26`; T24 `atlas/data/core_oddities.json:4123` · `src/save_layout.c:27`; T25 the sweep ·
`src/save_layout.c:27`; T26 `atlas/data/core_oddities.json:4144` · `src/save_layout.c:28`; T27 the sweep ·
`src/save_layout.c:28`; O3 `atlas/data/core_oddities.json:4085` · `src/save_unit.c:79`; O4
`vectors/machines/save-file-sets.json:779` · `src/save_unit.c:57`; O5 `atlas/data/core_oddities.json:4084` ·
`src/save_layout.c:22`; O6 `atlas/data/core_oddities.json:4083` · `src/save_layout.c:21`; O7
`vectors/machines/save-file-sets.json:772` · `src/save_layout.c:28`; O8 `vectors/machines/save-file-sets.json:772` ·
`src/save_layout.c:28`; O9 `vectors/machines/save-file-sets.json:772` · `src/save_layout.c:22`; O10
`vectors/machines/save-file-sets.json:772` · `src/save_layout.c:22`; O11 `vectors/machines/save-file-sets.json:765` ·
`src/save_layout.c:25`; O12 `vectors/machines/save-file-sets.json:765` · `src/save_layout.c:25`; O13
`vectors/machines/save-file-sets.json:765` · `src/save_layout.c:21`; O14 `vectors/machines/save-file-sets.json:765` ·
`src/save_layout.c:21`.

- T15: sigil's `{stem}.srm` does not hold for Sega CD content at the build atlas reads or at upstream HEAD; see §4, Sega
  CD.

atlas only — A2 the card's anchors bind 18 of its 20 recorded names and keys to whole strings of the shipped `.so`.
Sources (atlas · sigil): A2 `atlas/data/core_oddities.json:4623` · none (sigil states per-row sources, README table).

### `mednafen_psx_hw`

L5, a core both describe — same: atlas has a rule card, audit verdict `card`. Sources (atlas · sigil):
`atlas/data/core_oddities.json:3910` · `src/save_layout.c:136`.

| id       | fact                                                                          | verdict          | note                               |
| -------- | ----------------------------------------------------------------------------- | ---------------- | ---------------------------------- |
| T28, T29 | `{stem}.srm` (primary): name · when                                           | same · same      | sweep 8/8                          |
| T30, T31 | `{stem}.{left_index}.mcr` (primary): name · when                              | same · different | sigil defect; sweep 6/8            |
| T32, T33 | `{stem}.{right_index}.mcr` (sidecar): name · when                             | same · different | sigil defect; sweep 6/8            |
| T34, T35 | `mednafen_psx_libretro_shared.0.mcr` (shared): name · when                    | same · different | sigil defect; sweep 6/8            |
| T36, T37 | `mednafen_psx_libretro_shared.1.mcr` (shared): name · when                    | same · different | sigil defect; sweep 6/8            |
| O15      | `beetle_psx_hw_memcard_left_index` fills `{left_index}`                       | same             |                                    |
| O16      | Omitted `beetle_psx_hw_memcard_left_index` reads as `0`                       | same             | atlas: `0`                         |
| O17      | `beetle_psx_hw_memcard_right_index` fills `{right_index}`                     | same             |                                    |
| O18      | Omitted `beetle_psx_hw_memcard_right_index` reads as `1`                      | same             | atlas: `1`                         |
| O19      | Reads `beetle_psx_hw_enable_memcard1`                                         | same             |                                    |
| O20      | Reads `beetle_psx_hw_shared_memory_cards`                                     | same             |                                    |
| O21      | Reads `beetle_psx_hw_use_mednafen_memcard0_method`                            | same             |                                    |
| O22      | Value `enabled` of `beetle_psx_hw_enable_memcard1`                            | same             |                                    |
| O23      | Omitted `beetle_psx_hw_enable_memcard1` does not hold `enabled`               | different        | different builds; atlas: `enabled` |
| O24      | Value `enabled` of `beetle_psx_hw_shared_memory_cards`                        | same             |                                    |
| O25      | Omitted `beetle_psx_hw_shared_memory_cards` does not hold `enabled`           | same             | atlas: `disabled`                  |
| O26      | Value `libretro` of `beetle_psx_hw_use_mednafen_memcard0_method`              | same             |                                    |
| O27      | Omitted `beetle_psx_hw_use_mednafen_memcard0_method` holds `libretro`         | same             | atlas: `libretro`                  |
| O28      | Value `mednafen` of `beetle_psx_hw_use_mednafen_memcard0_method`              | same             |                                    |
| O29      | Omitted `beetle_psx_hw_use_mednafen_memcard0_method` does not hold `mednafen` | same             | atlas: `libretro`                  |

Sources (atlas · sigil): T28 `atlas/data/core_oddities.json:3932` · `src/save_layout.c:32`; T29 the sweep ·
`src/save_layout.c:32`; T30 `atlas/data/core_oddities.json:3982` · `src/save_layout.c:33`; T31 the sweep ·
`src/save_layout.c:33`; T32 `atlas/data/core_oddities.json:3939` · `src/save_layout.c:34`; T33 the sweep ·
`src/save_layout.c:34`; T34 `atlas/data/core_oddities.json:3995` · `src/save_layout.c:37`; T35 the sweep ·
`src/save_layout.c:37`; T36 `atlas/data/core_oddities.json:3958` · `src/save_layout.c:38`; T37 the sweep ·
`src/save_layout.c:38`; O15 `atlas/data/core_oddities.json:3922` · `src/save_unit.c:86`; O16
`atlas/data/core_oddities.json:4062` · `src/save_unit.c:87`; O17 `atlas/data/core_oddities.json:3923` ·
`src/save_unit.c:90`; O18 `atlas/data/core_oddities.json:4062` · `src/save_unit.c:91`; O19
`atlas/data/core_oddities.json:3920` · `src/save_layout.c:34`; O20 `atlas/data/core_oddities.json:3921` ·
`src/save_layout.c:37`; O21 `atlas/data/core_oddities.json:3919` · `src/save_layout.c:32`; O22 `atlas/mode_rules.py:615`
· `src/save_layout.c:34`; O23 `atlas/data/core_oddities.json:4062` · `src/save_layout.c:34`; O24
`atlas/mode_rules.py:615` · `src/save_layout.c:37`; O25 none (no text of the card states it; atlas reads the default the
installed core registers) · `src/save_layout.c:37`; O26 `atlas/mode_rules.py:629` · `src/save_layout.c:32`; O27
`atlas/data/core_oddities.json:4062` · `src/save_layout.c:32`; O28 `atlas/mode_rules.py:629` · `src/save_layout.c:33`;
O29 `atlas/data/core_oddities.json:4062` · `src/save_layout.c:33`.

- T31: the per-game card needs two conditions, the method and sharing off; see §4, Beetle PSX sharing.
- T33: the per-game second card needs the second card on and sharing off; see §4, Beetle PSX sharing.
- T35: the shared first card needs sharing and the Mednafen method; see §4, Beetle PSX sharing.
- T37: the shared second card needs sharing and the second card on; see §4, Beetle PSX sharing.
- O23: the default was `enabled` at the build atlas reads and is `disabled` since a later commit; see §4, Beetle PSX
  second card.

atlas only — A3 the card's anchors bind 9 of its 10 recorded names and keys to whole strings of the shipped `.so`.
Sources (atlas · sigil): A3 `atlas/data/core_oddities.json:4028` · none (sigil states per-row sources, README table).

### `pcsx_rearmed`

L6, a core both describe — same: atlas has a rule card, audit verdict `card`. Sources (atlas · sigil):
`atlas/data/core_oddities.json:4726` · `src/save_layout.c:137`.

| id       | fact                                           | verdict          | note                                                    |
| -------- | ---------------------------------------------- | ---------------- | ------------------------------------------------------- |
| T38, T39 | `{stem}.srm` (primary): name · when            | same · same      | sweep 2/2                                               |
| T40, T41 | `pcsx-card2.mcd` (shared): name · when         | same · different | different builds; sweep 1/2                             |
| O30      | Reads `pcsx_rearmed_memcard2`                  | same             |                                                         |
| O31      | Value `shared` of `pcsx_rearmed_memcard2`      | different        | different builds; atlas's values: `disabled`, `enabled` |
| O32      | Omitted `pcsx_rearmed_memcard2` holds `shared` | different        | different builds; atlas: `disabled`                     |

Sources (atlas · sigil): T38 `atlas/data/core_oddities.json:4742` · `src/save_layout.c:54`; T39 the sweep ·
`src/save_layout.c:54`; T40 `atlas/data/core_oddities.json:4761` · `src/save_layout.c:57`; T41 the sweep ·
`src/save_layout.c:57`; O30 `atlas/data/core_oddities.json:4734` · `src/save_layout.c:57`; O31
`atlas/data/core_oddities.json:4734` · `src/save_layout.c:57`; O32 `atlas/data/core_oddities.json:4782` ·
`src/save_layout.c:57`.

- T41: the option changed values and default between the build atlas reads and upstream HEAD; see §4, PCSX ReARMed.
- O31: see T41.
- O32: see T41.

atlas only — A4 the card's anchors bind 2 of its 3 recorded names and keys to whole strings of the shipped `.so`.
Sources (atlas · sigil): A4 `atlas/data/core_oddities.json:4769` · none (sigil states per-row sources, README table).

### `mednafen_saturn`

L7, a core both describe — same: atlas has a rule card, audit verdict `card`. Sources (atlas · sigil):
`atlas/data/core_oddities.json:3098` · `src/save_layout.c:138`.

| id       | fact                                                         | verdict                | note                                     |
| -------- | ------------------------------------------------------------ | ---------------------- | ---------------------------------------- |
| T42, T43 | `{stem}.srm` (primary): name · when                          | sigil only · different | different builds; in no mode; sweep 4/12 |
| T44, T45 | `{stem}.bkr` (primary): name · when                          | same · different       | sigil defect; sweep 6/12                 |
| T46, T47 | `{stem}.bcr` (sidecar): name · when                          | same · different       | sigil defect; sweep 6/12                 |
| T48, T49 | `{stem}.smpc` (sidecar): name · when                         | same · different       | sigil defect; sweep 6/12                 |
| T50, T51 | `mednafen_saturn_libretro_shared.bkr` (shared): name · when  | same · same            | sweep 12/12                              |
| T52, T53 | `mednafen_saturn_libretro_shared.smpc` (shared): name · when | same · same            | sweep 12/12                              |
| T54, T55 | `mednafen_saturn_libretro_shared.bcr` (shared): name · when  | same · same            | sweep 12/12                              |
| O33      | Reads `beetle_saturn_save_method`                            | sigil only             |                                          |
| O34      | Reads `beetle_saturn_shared_ext`                             | same                   |                                          |
| O35      | Reads `beetle_saturn_shared_int`                             | same                   |                                          |
| O36      | Value `libretro` of `beetle_saturn_save_method`              | sigil only             | atlas states no values for this key      |
| O37      | Value `mednafen` of `beetle_saturn_save_method`              | sigil only             | atlas states no values for this key      |
| O38      | Value `enabled` of `beetle_saturn_shared_ext`                | same                   |                                          |
| O39      | Omitted `beetle_saturn_shared_ext` does not hold `enabled`   | same                   | atlas: `disabled`                        |
| O40      | Value `enabled` of `beetle_saturn_shared_int`                | same                   |                                          |
| O41      | Omitted `beetle_saturn_shared_int` does not hold `enabled`   | same                   | atlas: `disabled`                        |

Sources (atlas · sigil): T42 none (every mode's groups) · `src/save_layout.c:42`; T43 the sweep ·
`src/save_layout.c:42`; T44 `atlas/data/core_oddities.json:3117` · `src/save_layout.c:43`; T45 the sweep ·
`src/save_layout.c:43`; T46 `atlas/data/core_oddities.json:3124` · `src/save_layout.c:44`; T47 the sweep ·
`src/save_layout.c:44`; T48 `atlas/data/core_oddities.json:3131` · `src/save_layout.c:45`; T49 the sweep ·
`src/save_layout.c:45`; T50 `atlas/data/core_oddities.json:3143` · `src/save_layout.c:48`; T51 the sweep ·
`src/save_layout.c:48`; T52 `atlas/data/core_oddities.json:3157` · `src/save_layout.c:49`; T53 the sweep ·
`src/save_layout.c:49`; T54 `atlas/data/core_oddities.json:3176` · `src/save_layout.c:50`; T55 the sweep ·
`src/save_layout.c:50`; O33 none (governing options) · `src/save_layout.c:42`; O34 `atlas/data/core_oddities.json:3108`
· `src/save_layout.c:50`; O35 `atlas/data/core_oddities.json:3107` · `src/save_layout.c:48`; O36 none (the card's
options, `atlas/data/core_oddities.json`) · `src/save_layout.c:42`; O37 none (the card's options,
`atlas/data/core_oddities.json`) · `src/save_layout.c:43`; O38 `vectors/machines/named-cases.json:4999` ·
`src/save_layout.c:50`; O39 `vectors/machines/named-cases.json:4999` · `src/save_layout.c:50`; O40
`vectors/machines/named-cases.json:4992` · `src/save_layout.c:48`; O41 `vectors/machines/named-cases.json:4992` ·
`src/save_layout.c:48`.

- T43: the build atlas reads has no save method option; see §4, Beetle Saturn.
- T45: whether a `.bkr` is written at all is T43's build difference; at HEAD the per-game `.bkr` also needs
  `beetle_saturn_shared_int` off, which sigil's member does not state; see §4, Beetle Saturn.
- T47: the `.bcr` takes the shared stem under `beetle_saturn_shared_ext` at both revisions; see §4, Beetle Saturn.
- T49: the `.smpc` takes the shared stem under `beetle_saturn_shared_int` at both revisions; see §4, Beetle Saturn.

atlas only — A5 the card's anchors bind 8 of its 8 recorded names and keys to whole strings of the shipped `.so`.
Sources (atlas · sigil): A5 `atlas/data/core_oddities.json:3217` · none (sigil states per-row sources, README table).

### `mednafen_ngp`

L8, a core both describe — same: atlas has a rule card, audit verdict `card`. Sources (atlas · sigil):
`atlas/data/core_oddities.json:2542` · `src/save_layout.c:139`.

| id       | fact                                  | verdict     | note      |
| -------- | ------------------------------------- | ----------- | --------- |
| T56, T57 | `{stem}.flash` (primary): name · when | same · same | sweep 1/1 |

Sources (atlas · sigil): T56 `atlas/data/core_oddities.json:2556` · `src/save_layout.c:61`; T57 the sweep ·
`src/save_layout.c:61`.

atlas only — A6 the card's anchors bind 1 of its 1 recorded names and keys to whole strings of the shipped `.so`.
Sources (atlas · sigil): A6 `atlas/data/core_oddities.json:2564` · none (sigil states per-row sources, README table).

### `opera`

L9, a core both describe — same: atlas has a rule card, audit verdict `card`. Sources (atlas · sigil):
`atlas/data/core_oddities.json:2049` · `src/save_layout.c:140`.

| id       | fact                                                               | verdict     | note              |
| -------- | ------------------------------------------------------------------ | ----------- | ----------------- |
| T58, T59 | `opera/per_game/{stem}.{nvram_version}.srm` (primary): name · when | same · same | sweep 20/20       |
| T60, T61 | `opera/shared/nvram.{nvram_version}.srm` (shared): name · when     | same · same | sweep 20/20       |
| T62      | Subfolder `opera/per_game` to list                                 | same        |                   |
| T63      | Subfolder `opera/shared` to list                                   | same        |                   |
| O42      | `opera_nvram_version` fills `{nvram_version}`                      | same        |                   |
| O43      | Omitted `opera_nvram_version` reads as `0`                         | same        | atlas: `0`        |
| O44      | Reads `opera_nvram_storage`                                        | same        |                   |
| O45      | Value `per game` of `opera_nvram_storage`                          | same        |                   |
| O46      | Omitted `opera_nvram_storage` holds `per game`                     | same        | atlas: `per game` |
| O47      | Value `shared` of `opera_nvram_storage`                            | same        |                   |
| O48      | Omitted `opera_nvram_storage` does not hold `shared`               | same        | atlas: `per game` |

Sources (atlas · sigil): T58 `atlas/data/core_oddities.json:2069` · `src/save_layout.c:65`; T59 the sweep ·
`src/save_layout.c:65`; T60 `atlas/data/core_oddities.json:2199` · `src/save_layout.c:68`; T61 the sweep ·
`src/save_layout.c:68`; T62 `atlas/data/core_oddities.json:2067` · `src/save_layout.c:70`; T63
`atlas/data/core_oddities.json:2197` · `src/save_layout.c:70`; O42 `atlas/data/core_oddities.json:2059` ·
`src/save_unit.c:82`; O43 `vectors/machines/named-cases.json:3969` · `src/save_unit.c:83`; O44
`atlas/data/core_oddities.json:2058` · `src/save_layout.c:65`; O45 `vectors/machines/named-cases.json:3822` ·
`src/save_layout.c:65`; O46 `vectors/machines/named-cases.json:3822` · `src/save_layout.c:65`; O47
`vectors/machines/named-cases.json:3822` · `src/save_layout.c:68`; O48 `vectors/machines/named-cases.json:3822` ·
`src/save_layout.c:68`.

atlas only — A7 the card's anchors bind 25 of its 25 recorded names and keys to whole strings of the shipped `.so`.
Sources (atlas · sigil): A7 `atlas/data/core_oddities.json:2324` · none (sigil states per-row sources, README table).

### `pokemini`

L10, a core both describe — same: atlas has a rule card, audit verdict `card`. Sources (atlas · sigil):
`atlas/data/core_oddities.json:2507` · `src/save_layout.c:141`.

| id       | fact                                | verdict     | note      |
| -------- | ----------------------------------- | ----------- | --------- |
| T64, T65 | `{stem}.eep` (primary): name · when | same · same | sweep 1/1 |

Sources (atlas · sigil): T64 `atlas/data/core_oddities.json:2521` · `src/save_layout.c:73`; T65 the sweep ·
`src/save_layout.c:73`.

atlas only — A8 the card's anchors bind 1 of its 1 recorded names and keys to whole strings of the shipped `.so`.
Sources (atlas · sigil): A8 `atlas/data/core_oddities.json:2529` · none (sigil states per-row sources, README table).

### `handy`

L11, a core both describe — same: atlas has no card; its memory record says which of the frontend's files the core fills
(`atarilynx`: none). Sources (atlas · sigil): `atlas/data/save_memory.json:525` · `src/save_layout.c:142`.

| id | fact                      | verdict    | note                 |
| -- | ------------------------- | ---------- | -------------------- |
| T3 | `{stem}.eeprom` (primary) | sigil only | core-written (below) |

Sources (atlas · sigil): T3 `atlas/data/save_memory.json:532` (frontend files only) · `src/save_layout.c:77`.

atlas's answer for this core names no file and carries `core-own-writes-unestablished`: the frontend writes none, and
whether the core writes files of its own is not established. **[O]** The file sigil's row names (T3) is open item 5.

### `melonds`

L12, a core both describe — same: atlas has a rule card, audit verdict `card`. Sources (atlas · sigil):
`atlas/data/core_oddities.json:1890` · `src/save_layout.c:143`.

| id       | fact                                | verdict     | note      |
| -------- | ----------------------------------- | ----------- | --------- |
| T66, T67 | `{stem}.sav` (primary): name · when | same · same | sweep 1/1 |

Sources (atlas · sigil): T66 `atlas/data/core_oddities.json:1904` · `src/save_layout.c:81`; T67 the sweep ·
`src/save_layout.c:81`.

atlas only — A9 the card's anchors bind 0 of its 1 recorded names and keys to whole strings of the shipped `.so`.
Sources (atlas · sigil): A9 `atlas/data/core_oddities.json:1912` · none (sigil states per-row sources, README table).

### `fbneo`

L13, a core both describe — same: atlas has a rule card, audit verdict `card`. Sources (atlas · sigil):
`atlas/data/core_oddities.json:823` · `src/save_layout.c:144`.

| id       | fact                                                  | verdict                | note                                |
| -------- | ----------------------------------------------------- | ---------------------- | ----------------------------------- |
| T68, T69 | `fbneo/{romset}.fs` (primary): name · when            | same · same            | sweep 3/3                           |
| T70, T71 | `fbneo/{romset}.nv` (sidecar): name · when            | sigil only · different | atlas defect; in no mode; sweep 0/3 |
| T72, T73 | `fbneo/{romset}.memcard` (sidecar): name · when       | same · same            | sweep 3/3                           |
| T74, T75 | `fbneo/shared.memcard` (shared): name · when          | same · same            | sweep 3/3                           |
| T76      | Subfolder `fbneo` to list                             | same                   |                                     |
| O49      | Reads `fbneo-memcard-mode`                            | same                   |                                     |
| O50      | Value `per-game` of `fbneo-memcard-mode`              | same                   |                                     |
| O51      | Omitted `fbneo-memcard-mode` does not hold `per-game` | same                   | atlas: `disabled`                   |
| O52      | Value `shared` of `fbneo-memcard-mode`                | same                   |                                     |
| O53      | Omitted `fbneo-memcard-mode` does not hold `shared`   | same                   | atlas: `disabled`                   |

Sources (atlas · sigil): T68 `atlas/data/core_oddities.json:841` · `src/save_layout.c:85`; T69 the sweep ·
`src/save_layout.c:85`; T70 none (every mode's groups) · `src/save_layout.c:86`; T71 the sweep · `src/save_layout.c:86`;
T72 `atlas/data/core_oddities.json:883` · `src/save_layout.c:87`; T73 the sweep · `src/save_layout.c:87`; T74
`atlas/data/core_oddities.json:862` · `src/save_layout.c:90`; T75 the sweep · `src/save_layout.c:90`; T76
`atlas/data/core_oddities.json:839` · `src/save_layout.c:92`; O49 `atlas/data/core_oddities.json:831` ·
`src/save_layout.c:87`; O50 `atlas/data/core_oddities.json:831-832` · `src/save_layout.c:87`; O51
`atlas/data/core_oddities.json:831-832` · `src/save_layout.c:87`; O52 `atlas/data/core_oddities.json:831-832` ·
`src/save_layout.c:90`; O53 `atlas/data/core_oddities.json:831-832` · `src/save_layout.c:90`.

- T71: FinalBurn Neo writes `fbneo/<driver>.nv` for a board with an EEPROM at the build atlas reads and at HEAD; atlas's
  card leaves it out; see §4, FinalBurn Neo.

atlas only — A10 the card's anchors bind 5 of its 5 recorded names and keys to whole strings of the shipped `.so`.
Sources (atlas · sigil): A10 `atlas/data/core_oddities.json:891` · none (sigil states per-row sources, README table).

### `mame2003_plus`

L14, a core both describe — same: atlas has a rule card, audit verdict `card`. Sources (atlas · sigil):
`atlas/data/core_oddities.json:1610` · `src/save_layout.c:145`.

| id       | fact                                                                 | verdict     | note             |
| -------- | -------------------------------------------------------------------- | ----------- | ---------------- |
| T77, T78 | `mame2003-plus/nvram/{romset}.nv` (primary): name · when             | same · same | sweep 2/2        |
| T79, T80 | `mame2003-plus/hi/{romset}.hi` (sidecar): name · when                | same · same | sweep 2/2        |
| T81, T82 | `nvram/{romset}.nv` (primary): name · when                           | same · same | sweep 2/2        |
| T83, T84 | `hi/{romset}.hi` (sidecar): name · when                              | same · same | sweep 2/2        |
| T85      | Subfolder `mame2003-plus/nvram` to list                              | same        |                  |
| T86      | Subfolder `mame2003-plus/hi` to list                                 | same        |                  |
| T87      | Subfolder `nvram` to list                                            | same        |                  |
| T88      | Subfolder `hi` to list                                               | same        |                  |
| O54      | Reads `mame2003-plus_core_save_subfolder`                            | same        |                  |
| O55      | Value `disabled` of `mame2003-plus_core_save_subfolder`              | same        |                  |
| O56      | Omitted `mame2003-plus_core_save_subfolder` does not hold `disabled` | same        | atlas: `enabled` |
| O57      | Value `enabled` of `mame2003-plus_core_save_subfolder`               | same        |                  |
| O58      | Omitted `mame2003-plus_core_save_subfolder` holds `enabled`          | same        | atlas: `enabled` |

Sources (atlas · sigil): T77 `atlas/data/core_oddities.json:1628` · `src/save_layout.c:95`; T78 the sweep ·
`src/save_layout.c:95`; T79 `atlas/data/core_oddities.json:1639` · `src/save_layout.c:96`; T80 the sweep ·
`src/save_layout.c:96`; T81 `atlas/data/core_oddities.json:1628` · `src/save_layout.c:97`; T82 the sweep ·
`src/save_layout.c:97`; T83 `atlas/data/core_oddities.json:1639` · `src/save_layout.c:98`; T84 the sweep ·
`src/save_layout.c:98`; T85 `atlas/data/core_oddities.json:1626` · `src/save_layout.c:101`; T86
`atlas/data/core_oddities.json:1637` · `src/save_layout.c:101`; T87 `atlas/data/core_oddities.json:1670` ·
`src/save_layout.c:101`; T88 `atlas/data/core_oddities.json:1681` · `src/save_layout.c:101`; O54
`atlas/data/core_oddities.json:1618` · `src/save_layout.c:95`; O55 `atlas/data/core_oddities.json:1618-1619` ·
`src/save_layout.c:97`; O56 `atlas/data/core_oddities.json:1618-1619` · `src/save_layout.c:97`; O57
`atlas/data/core_oddities.json:1618-1619` · `src/save_layout.c:95`; O58 `atlas/data/core_oddities.json:1618-1619` ·
`src/save_layout.c:95`.

atlas only — A11 file `mame2003-plus/cfg/<stem>.cfg`: settings, not save data, one file per game; A12 directory
`mame2003-plus/memcard/`: memory-card data, one file every game shares, names not stated; A13 directory
`mame2003-plus/diff/`: disk-difference data, several files per game, names not stated; A14 file `cfg/<stem>.cfg`:
settings, not save data, one file per game; A15 directory `memcard/`: memory-card data, one file every game shares,
names not stated; A16 directory `diff/`: disk-difference data, several files per game, names not stated; A17 the stated
names hold for content class `driver-named-content`; A18 the card's anchors bind 6 of its 10 recorded names and keys to
whole strings of the shipped `.so`. Sources (atlas · sigil): A11 `atlas/data/core_oddities.json:1647` · none (every
template of the row); A12 `atlas/data/core_oddities.json:1653` · none (every template of the row); A13
`atlas/data/core_oddities.json:1659` · none (every template of the row); A14 `atlas/data/core_oddities.json:1691` · none
(every template of the row); A15 `atlas/data/core_oddities.json:1697` · none (every template of the row); A16
`atlas/data/core_oddities.json:1703` · none (every template of the row); A17 `atlas/data/core_oddities.json:1632` · none
(every template of the row); A18 `atlas/data/core_oddities.json:1711` · none (sigil states per-row sources, README
table).

### `dosbox_pure`

L15, a core both describe — same: atlas has a rule card, audit verdict `card`. Sources (atlas · sigil):
`atlas/data/core_oddities.json:357` · `src/save_layout.c:146`.

| id       | fact                                     | verdict     | note      |
| -------- | ---------------------------------------- | ----------- | --------- |
| T89, T90 | `{stem}.pure.zip` (primary): name · when | same · same | sweep 1/1 |

Sources (atlas · sigil): T89 `atlas/data/core_oddities.json:371` · `src/save_layout.c:105`; T90 the sweep ·
`src/save_layout.c:105`.

atlas only — A19 names atlas recognises on disk beyond the declared one: `<stem>.sav`, `<stem>-CDRIVE.sav`; A20 the
stated names hold for content class `default-save-name`; A21 the card's anchors bind 2 of its 3 recorded names and keys
to whole strings of the shipped `.so`. Sources (atlas · sigil): A19 `atlas/data/core_oddities.json:373` · none (every
template of the row); A20 `atlas/data/core_oddities.json:380` · none (every template of the row); A21
`atlas/data/core_oddities.json:387` · none (sigil states per-row sources, README table).

### `same_cdi`

L16, a core both describe — same: atlas has no card; its memory record says which of the frontend's files the core fills
(`cdimono1`: none). Sources (atlas · sigil): `atlas/data/save_memory.json:1298` · `src/save_layout.c:147`.

| id | fact                                                                        | verdict    | note                 |
| -- | --------------------------------------------------------------------------- | ---------- | -------------------- |
| T4 | `same_cdi/nvram/{stem}/` (primary), when `same_cdi_nvram_saves` = `enabled` | sigil only | core-written (below) |
| O1 | Reads `same_cdi_nvram_saves`                                                | sigil only |                      |

Sources (atlas · sigil): T4 `atlas/data/save_memory.json:1305` (frontend files only) · `src/save_layout.c:109`; O1 none
(no card for the core) · `src/save_layout.c:109`.

atlas's answer for this core names no file and carries `core-own-writes-unestablished`: the frontend writes none, and
whether the core writes files of its own is not established. **[O]** The file sigil's row names (T4) is open item 5.

### `nestopia` on `fds`

L17, a core both describe — same: atlas has no card; its memory record says which of the frontend's files the core fills
(`famicom`: `save_ram`, `fds`: `save_ram`, `nes`: `save_ram`). Sources (atlas · sigil):
`atlas/data/save_memory.json:1027` · `src/save_layout.c:148`.

| id | fact                                                                    | verdict    | note                                  |
| -- | ----------------------------------------------------------------------- | ---------- | ------------------------------------- |
| T5 | `{stem}.srm` (primary)                                                  | same       | the frontend's file; record: save RAM |
| T6 | `{stem}.sav` (sidecar), when `nestopia_fds_savefile_format` = `sav_ups` | sigil only | core-written (below)                  |
| T7 | `{stem}.ups` (sidecar), when `nestopia_fds_savefile_format` = `ups`     | sigil only | core-written (below)                  |
| T8 | `{stem}.ips` (sidecar), when `nestopia_fds_savefile_format` = `ips`     | sigil only | core-written (below)                  |
| O2 | Reads `nestopia_fds_savefile_format`                                    | sigil only |                                       |

Sources (atlas · sigil): T5 `atlas/data/save_memory.json:1041` · `src/save_layout.c:114`; T6
`atlas/data/save_memory.json:1041` (frontend files only) · `src/save_layout.c:115`; T7
`atlas/data/save_memory.json:1041` (frontend files only) · `src/save_layout.c:116`; T8
`atlas/data/save_memory.json:1041` (frontend files only) · `src/save_layout.c:117`; O2 none (no card for the core) ·
`src/save_layout.c:115`.

atlas's answer for this core names the frontend's `<stem>.srm` and states nothing about files the core writes itself.
**[O]** The files sigil's row names (T6–T8) are open item 5.

### The answer fields

The S rows set the answer fields beside each other: what a unit's paths are relative to, what a role means, how an
option the caller leaves out is read, and what atlas's documents say about sigil.

- **S1** Who lists the save root — atlas only: sigil takes a caller's listing and opens members through a callback.
  Sources: atlas's `README.md:225`; sigil's `include/sigil.h:215-216`.
- **S2** The directory unit paths are relative to — different (sigil defect): sigil's sentence names the core subfolder
  and not the content-directory one; atlas's `dir` carries both, in RetroArch's order; run: vector
  `retrodeck-sort-by-core-resolves-library-name` answers `<saves>/<content dir>/<library_name>`. RetroArch composes the
  root the same way at HEAD; see §4, the save root. Sources: atlas's `atlas/placement.py:1398`,
  `docs/research/retrodeck-save-placement.md:92-93`; sigil's `docs/c.md:95-97`.
- **S3** Which name the core subfolder carries — atlas only: sigil says 'core-named' without saying which name; atlas:
  `library_name`, which is not the `.so` short name (`Beetle PSX HW` for `mednafen_psx_hw`). Sources: atlas's
  `docs/research/retrodeck-save-placement.md:176`; sigil's `docs/c.md:97`.
- **S4** A card's own subtree in the directory — different (shape): atlas's `dir` ends in the subtree, sigil's templates
  start with it: sigil's root is `dir` minus the subtree. Sources: atlas's `vectors/machines/save-file-sets.json:1241`;
  sigil's `src/save_layout.c:85`.
- **S5** The backing directory behind symlinks — atlas only. Sources: atlas's `atlas/placement.py:1427`; sigil: none
  (`include/sigil.h`: no request or unit field).
- **S6** Where RetroArch falls back when the sorted directory cannot be made — atlas only. Sources: atlas's
  `atlas/placement.py:1423`; sigil: none (`include/sigil.h`: no request or unit field).
- **S7** Subfolders a core writes into — same: per core in the T rows. Sources: atlas's `atlas/placement.py:1074`;
  sigil's `include/sigil.h:287`.
- **S8** A core neither side has a row for — different (shape): sigil answers `<stem>.srm` + `<stem>.rtc` for every id
  without a row; atlas states per core and system which of the two the core fills where it holds a memory record, and
  answers `unknown` for a core it holds none for. Per core the default row's `.srm` is not the save wherever the core
  fills no save RAM or writes its own files (§2.1); no one revision decides the row. Sources: atlas's
  `atlas/data/save_memory.json:3`; sigil's `src/save_layout.c:129`, `README.md:212`.
- **S9** The stem of a frontend-written file, `archive.zip#member` included — same: run: vector driver,
  `archive-content-path-names-the-entry`. Sources: atlas's `vectors/machines/named-cases.json:6158`; sigil's
  `src/save_unit.c:19-37`.
- **S10** A core that names its file after something other than the content stem — different (sigil defect): sigil fills
  `{stem}` and `{romset}` from the content name for every row; atlas's cards state per core where the core's own name
  departs from it (an archived Neo Geo Pocket game, FinalBurn Neo's console subsystems and Neo Geo CD). Both cores still
  name their files after something other than the content stem at upstream HEAD, FinalBurn Neo's CD saves by a new name;
  see §4, stems. Sources: atlas's `atlas/data/core_oddities.json:2571`, `atlas/data/core_oddities.json:910`; sigil's
  `src/save_unit.c:75`.
- **S11** Absent files of the unit — different (shape): sigil lists absent primaries (and the clock file when the cart
  has one) as `expected`; absent sidecars and shared files are not reported. atlas's declared set names every file of
  the mode, present or not; run: `mednafen_psx_hw` with `<stem>.1.mcr` absent. Sources: atlas's
  `atlas/placement.py:1196-1199`; sigil's `include/sigil.h:270`.
- **S12** A file under the root the layout does not name — different (shape): sigil passes over it silently; atlas's
  observation states it as a group with role `unknown`; run: vector `a-rule-card-observes-its-three-role-files` on its
  own listing, `<stem>.bkr` in the listing and in no field of the unit. Sources: atlas's `atlas/placement.py:1049-1051`;
  sigil's `src/save_unit.c:181`.
- **S13** Files every game shares — same: both name the files every game shares; how an absent one is reported is S11.
  Sources: atlas's `atlas/placement.py:261-262`; sigil's `include/sigil.h:272`.
- **S14** The word for what a file is — different (shape): sigil's role says which member names the unit; atlas's says
  what kind of data it is — no word of one translates into the other. Sources: atlas's `atlas/placement.py:333-341`;
  sigil's `include/sigil.h:226-228`.
- **S15** A settings file beside the save — atlas only: a syncing client skips `settings`; sigil bundles Beetle Saturn's
  `.smpc` as a sidecar. Sources: atlas's `atlas/placement.py:295-296`; sigil: none (`include/sigil.h:226-228`: the roles
  are `primary`, `sidecar`, `rtc`).
- **S16** A directory whose file names follow from nothing read — atlas only: `file-names-unestablished`. Sources:
  atlas's `atlas/placement.py:1057`; sigil: none (`include/sigil.h`: no request or unit field).
- **S17** Where the names come from — different (shape): atlas: `observed`, `declared` or `unknown` for the whole set;
  sigil: `present` per member, and no refusal. Sources: atlas's `atlas/placement.py:1196`; sigil's
  `include/sigil.h:238`.
- **S18** Whether the names close the save — atlas only. Sources: atlas's `atlas/placement.py:1205-1208`; sigil: none
  (`include/sigil.h`: no request or unit field).
- **S19** Names bound to the shipped binary — atlas only: sigil's unit test holds the resolver against listings it
  writes itself. Sources: atlas's `atlas/data/core_oddities.json:3`; sigil's `tests/unit_save_unit.c:189`.
- **S20** How a mode groups save data, in one word — atlas only. Sources: atlas's `atlas/placement.py:1340`; sigil: none
  (`include/sigil.h`: no request or unit field).
- **S21** The option values a caller hands over — same: same spelling — the core's own strings; atlas's readings carry
  only the switches the selection read, and sigil's own documents say two things about how many it wants (§1.1).
  Sources: atlas's `atlas/placement.py:1272-1275`; sigil's `docs/c.md:88-91`.
- **S22** An option the caller does not state — different (shape): sigil decides by a flag in its table; atlas reads the
  core's registered default off the binary (or the card's where the core registers none); per key in the O rows; run:
  sweep, the no-options case per core. Sources: atlas's `atlas/placement.py:1265`; sigil's `README.md:295`.
- **S23** The other modes and the settings that reach them — atlas only. Sources: atlas's `atlas/placement.py:1353`;
  sigil: none (`include/sigil.h`: no request or unit field).
- **S24** Degradations of the machine — atlas only. Sources: atlas's `atlas/placement.py:1415`; sigil: none
  (`include/sigil.h`: no request or unit field).
- **S25** What picks the layout for one core that serves several systems — different (shape): sigil picks a row by
  platform slug, atlas by the content's class read off the extension; run: `sigil_layout_find` answers the `segacd` row
  for `megacd` and `segacd`, the default row for `megacdjp` (§1.3). Sources: atlas's
  `atlas/data/core_oddities.json:4693`; sigil's `src/save_layout.c:163`.
- **S26** The platform slug a row is limited to — different (shape): ES-DE's system name `megacdjp` or `snesna` misses
  sigil's platform rows; the `<platform>` tag atlas also holds (`segacd`, `snes`) hits them; run: `sigil_layout_find`
  (§1.3). Sources: atlas's `atlas/data/system_ids.json:409-411`; sigil's `src/save_layout.c:151`.
- **S27** The shape a unit travels in — sigil only. Sources: atlas: none (`atlas/placement.py`, the answer types);
  sigil's `include/sigil.h:219-222`.
- **S28** The RomM content hash of the unit — sigil only. Sources: atlas: none
  (`grep -rniE 'content_hash|compute_content_hash|zip_hash' atlas/`: no hit); sigil's `README.md:245`.
- **S29** The clock file — different (shape): atlas names `<stem>.rtc` wherever the core and system can fill it; sigil
  expects it only for a cart whose header says it has a clock. Sources: atlas's `atlas/data/save_memory.json:3`; sigil's
  `include/sigil.h:228`.
- **S30** The layout id — same: the `.so` short name. Sources: atlas's `atlas/data/core_oddities.json:3`; sigil's
  `docs/c.md:77`.
- **S31** The core's display name — atlas only: `identifiers.library_name`, the name sort-by-core puts in the path.
  Sources: atlas's `atlas/data/core_oddities.json:3912`; sigil: none (`include/sigil.h`: no request or unit field).
- **S32** The card-image index beyond its default — sigil only: sigil names `<stem>.<n>.mcr` for any index; atlas steps
  aside (`core-mode-unestablished`) off the registered defaults; vector
  `beetle-psx-a-diverging-card-index-names-the-options-and-their-values`. Sources: atlas's `atlas/mode_rules.py:650`;
  sigil's `src/save_unit.c:86`.
- **S33** atlas's link to sigil — different (atlas's documents): `rommforge/argosy-sigil` answers 301 to
  `rommapp/argosy-sigil`. Sources: atlas's `README.md:74`, `docs/how-to-use.md:903`; sigil:
  `curl -sI https://github.com/rommforge/argosy-sigil`.
- **S34** What atlas says sigil covers — different (atlas's documents): atlas's account names identification only;
  sigil's README names three calls. Sources: atlas's `README.md:230-231`, `README.md:78`; sigil's `README.md:11`.
- **S35** Whether sigil reads Dreamcast — different (atlas's documents): sigil identifies Dreamcast discs, marked
  experimental. Sources: atlas's `docs/how-to-use.md:905`; sigil's `include/sigil.h:71`, `README.md:122`.

## 3. What stays where

atlas keeps answering the save questions itself: the file set, the rule cards, the option readings. sigil stays the
supplier of identification — reading a ROM's header for the platform-native id atlas leaves as the `<save_id>` hole
(§5).

The reason is the build dimension sigil's table lacks (§1.2). A row states one revision's behaviour for every build of
its core, and the revision is whichever one its maintainer read: the Beetle PSX row cites a commit of 2026-07, the
Beetle Saturn row names a file, `mednafen/ss/ss.c`, that the tree atlas reads does not have, the PCSX ReARMed row
matches the options of 2026-05 onward (§4). The builds a distribution ships lag behind that: the one atlas reads for
each of those three cores predates the change, and the rows O23, T41, O31, O32 and T43 are the result — each side right
for a different build. atlas selects a card by the options the installed core registers
(`atlas/installations.py:1623-1648`) and reads an option's default off the installed core rather than off a table, so
its answer follows the build on the machine — within the limit §4 states as atlas's own blind spot. Handing the save
answer to sigil's table would state one revision's files for every build. The id sigil reads off the content does not
depend on the emulator's build; how an emulator spells that id into a name can, and §5 states it per hole.

## 4. Disagreements, and the version rule

For atlas's answers, atlas's cards govern. A `different` row outside the identify rows is then one of:

- **atlas defect** — the build atlas reads writes what atlas's card leaves out; fixed in atlas. **atlas's documents** is
  the same for atlas's own account of sigil.
- **sigil defect** — sigil's statement holds neither at the build atlas reads nor at upstream HEAD, nor at the revision
  sigil cites where it cites one; a matter for sigil, and nothing in atlas changes.
- **different builds** — both are right, each for the build it describes: atlas for the one the shipped binary names,
  sigil for upstream at or after the revision it read.
- **shape** — the two carry the same fact differently, or answer a different question; no revision decides it.

The version rule decides which: each side's fact is read at the revision it holds at — atlas's at the revision the
shipped binary names, sigil's at the revision its row cites or, where it cites none, at upstream HEAD — and again at
upstream HEAD. **[V]** for every entry of this table: each cited line was read at the revision named.

| rows               | atlas reads | sigil cites       | upstream HEAD (date)   | class             |
| ------------------ | ----------- | ----------------- | ---------------------- | ----------------- |
| T15                | `46a5521`   | file and function | `c2838c7` (2026-09-12) | sigil defect      |
| T31, T33, T35, T37 | `d6383bf`   | `707d1be`         | `5718ab9` (2026-09-21) | sigil defect      |
| O23                | `d6383bf`   | `707d1be`         | `5718ab9` (2026-09-21) | different builds  |
| T41, O31, O32      | `228c14e`   | an observation    | `ff81ed1` (2026-09-23) | different builds  |
| T43                | `ccba526`   | a file name       | `1382b85` (2026-09-06) | different builds  |
| T45, T47, T49      | `ccba526`   | a file name       | `1382b85` (2026-09-06) | sigil defect      |
| T71                | `01e29d5`   | two file names    | `aceeebe` (2026-09-23) | atlas defect      |
| S2                 | `a79435a`   | `docs/c.md:95-97` | `01cca3a` (2026-09-24) | sigil defect      |
| S10                | three (1)   | one stem rule     | three (1)              | sigil defect      |
| S33, S34, S35      | `cfb5f0d`   | the pin `8a3b008` | —                      | atlas's documents |

(1) Beetle NeoPop `139fe34`, FinalBurn Neo `01e29d5` and RetroArch `a79435a`; at HEAD `a50d5ac` (2026-06-14), `aceeebe`
(2026-09-23) and `01cca3a` (2026-09-24). Where a row cites no revision, "file and function", "a file name" and "an
observation" are what its README cell names. The repositories are named in the topic paragraphs below. The shape rows —
S4, S8, S11, S12, S14, S17, S22, S25, S26, S29 — state no single emulator fact that one revision decides; the evidence
behind each other row follows, by topic.

**Sega CD (T15).** **[V]** At `libretro/Genesis-Plus-GX` `46a5521` — the revision the shipped binary names
(`atlas/data/core_oddities.json:4693`) — `gen_init` takes the Mega Drive branch for Sega CD hardware, since `SYSTEM_MCD`
masked by `SYSTEM_PBC` is `SYSTEM_MD` (`core/system.h:56-59`, `core/genesis.c:72`), and there initialises the CD
hardware (`scd_init()`) instead of the cartridge (`md_cart_init()`) (`core/genesis.c:162-175`).
`retro_get_memory_size(RETRO_MEMORY_SAVE_RAM)` answers 0 while `sram.on` is unset (`libretro/libretro.c:3765-3769`). A
search over `core/` and `libretro/` (`grep -a` for `sram_init(` and `sram.on = 1`, which also finds the ISO-8859 files)
finds `sram.on` set only in `sram_init` itself (`core/cart_hw/sram.c`), the cartridge code
(`core/cart_hw/md_cart.c:784`) and the two EEPROM inits (`core/cart_hw/eeprom_i2c.c:218`,
`core/cart_hw/eeprom_spi.c:84`), all called from `md_cart_init` (`md_cart.c:408`, `:409`, `:593`), and in the Master
System cartridge code (`core/cart_hw/sms_cart.c:601`, `:604`), whose init runs only outside the Mega Drive branch
(`core/genesis.c:180`, `:198`) or when `(system_hw & SYSTEM_PBC)` is not `SYSTEM_MD` (`libretro/libretro.c:1822-1825`).
RetroArch writes no `<stem>.srm` for Sega CD content. At HEAD `c2838c7` the same holds: `core/genesis.c:72`, `:162-175`,
`:180`, `:198`; `core/cart_hw/md_cart.c:403`, `:404`, `:588`, `:843`; `core/cart_hw/eeprom_i2c.c:215`;
`core/cart_hw/sms_cart.c:604`, `:607`; `libretro/libretro.c:1822-1825`, `:3765-3769`. sigil's row cites
`libretro/libretro.c` `check_variables` and `bram_save` (sigil's `README.md:314`), which place the BRAM files, not the
`.srm`. T14, the name alone, is `sigil only`. **[O]** No Sega CD session under the shipped core has been observed.

**Beetle PSX sharing (T31, T33, T35, T37).** **[V]** At `libretro/beetle-psx-libretro` `d6383bf`
(`atlas/data/core_oddities.json:4062`), every core-written card is named by `MDFN_MakeFName(MDFNMKF_SAV, …)`, which
takes `mednafen_psx_libretro_shared` as the stem while sharing is on and the content's name otherwise
(`libretro.cpp:5243-5248`). Slot 0 is core-written only under the Mednafen method — under the libretro method it goes to
the frontend's `.srm` (`:2461-2465`); the digit in each name is the card-image index (`:2473-2478`, row S32). With the
second card disabled, port 2 emulates no card (`:1972-1974`), and `FrontIO::SaveMemcard` writes only a device that has
non-volatile memory (`mednafen/psx/frontio.cpp:995`). **[D]** Nothing is written for slot 1 then; **[O]** that rests on
the memory size of the device an unemulated port holds. So a per-game `.mcr` needs its slot's condition _and_ sharing
off; the shared `.0.mcr` needs sharing _and_ the Mednafen method; the shared `.1.mcr` needs sharing _and_ the second
card. The same three facts stand at sigil's cited `707d1be` (`libretro.c:6414`, `:3350-3354`, `:2679-2681`) and at HEAD
`5718ab9` (`libretro.c:7318`, `:3801-3805`, `:3096-3098`). sigil's members each take one option condition
(`src/save_layout.h:8-14`), and its own test asserts the per-game `.0.mcr` as a member with the Mednafen method and
sharing on (`tests/unit_save_unit.c:246-258`). **[O]** No session with sharing on has been observed.

**Beetle PSX second card (O23).** **[V]** At `d6383bf`, `libretro_core_options.h:657-668` registers `enable_memcard1`
with `enabled` as the default. Commit `b923925` (2025-12-28, "Core option cleanups") changed it to `disabled`
(`:724-735` there; its predecessor `3950835` still has `enabled`, `:657-668`); sigil's cited `707d1be` has `disabled`
(`:1072-1083`), and so does HEAD `5718ab9` (`:1209-1220`). **[D]** atlas's reading for a build of `b923925` or later is
`disabled` too: the rule reads the value the installed core registers (row S22), not the card's sentence "Slot 1 is
enabled by default" (`atlas/data/core_oddities.json:4062`).

**PCSX ReARMed (T41, O31, O32).** **[V]** At `libretro/pcsx_rearmed` `228c14e` (`atlas/data/core_oddities.json:4782`),
`pcsx_rearmed_memcard2` registers `disabled`/`enabled`, default `disabled` (`frontend/libretro_core_options.h:152-165`),
and `enabled` opens `<save dir>/pcsx-card2.mcd` (`frontend/libretro.c:3487-3517`). Commit `034a72c` (2026-05-02,
"libretro: more memcard options"; its parent `b08a9e6` still has the two values at `:154-165`) replaced it: HEAD
`ff81ed1` registers `pcsx_rearmed_memcard1` with `libretro`/`serial`/`shared`/`none`, default `libretro` (`:154-167`),
and `pcsx_rearmed_memcard2` with `serial`/`shared`/`none`, default `shared` (`:170-182`); `load_memcards` names a
`serial` card `<save dir>/<serial>_<slot>.mcd` and a `shared` one `<save dir>/pcsx-card<slot>.mcd`
(`frontend/libretro.c:3785-3792`). sigil's row matches `034a72c` onward and atlas's card `228c14e`. Neither atlas's card
nor sigil's row states `pcsx_rearmed_memcard1` or the `serial` cards of the later builds.

**Beetle Saturn (T43, T45, T47, T49).** **[V]** At `libretro/beetle-saturn-libretro` `ccba526`
(`atlas/data/core_oddities.json:3245`), `libretro_core_options.h` registers `beetle_saturn_shared_int` (`:607`) and
`beetle_saturn_shared_ext` (`:621`) and no save method; the core writes `<stem>.bkr` and `<stem>.smpc` through
`MDFNMKF_SAV` (`mednafen/ss/ss.cpp:1039`, `:1142`) and the cartridge's `.bcr` through `MDFNMKF_CART` (`:1124`, the
extension from `mednafen/ss/cart/backup.cpp:62`), and `MDFN_MakeFName` swaps the first stem under `shared_int` and the
second under `shared_ext` (`libretro.cpp:1053-1063`). Commit `a0c1c52` (2026-05-25) added `beetle_saturn_save_method`
with the default `mednafen`, and `0977d2c` (2026-05-26) made `libretro` the default. At HEAD `1382b85` the option
registers `libretro`/`mednafen`, default `libretro` (`libretro_core_options.h:826-837`); in libretro mode the core hands
backup RAM to the frontend as save RAM (`libretro.c:1891-1893`, `:1907-1909`) and skips the `.bkr` write
(`mednafen/ss/ss.c:2168-2169`); the stems still swap, `MDFNMKF_SAV` under `shared_int` (`libretro.c:2201-2205`) and
`MDFNMKF_CART` under `shared_ext` (`:2207-2211`), and the `.bcr` is written through the latter (`mednafen/ss/ss.c:2382`;
it is read at `:2302-2305`). sigil's row cites `mednafen/ss/ss.c` (sigil's `README.md:317`), a path of the later tree:
at `ccba526` the file is `mednafen/ss/ss.cpp`. So whether the per-game save is `.srm` or `.bkr` is different builds
(T43; T42, O33, O36 and O37 are `sigil only` for the same reason). At HEAD the per-game `.bkr` needs the Mednafen method
_and_ `shared_int` off (T45), the per-game `.bcr` `shared_ext` off (T47) and the per-game `.smpc` `shared_int` off (T49)
— second conditions sigil's one-key members do not state.

**FinalBurn Neo (T71).** **[V]** At `libretro/FBNeo` `01e29d5` (`atlas/data/core_oddities.json:910`), the EEPROM device
reads `<EEPROM path><driver>.nv` when it starts (`src/burn/devices/eeprom.cpp:97`) and writes it when it exits (`:120`),
and the EEPROM path is `<save dir>/fbneo/` (`src/burner/libretro/libretro.cpp:1968`). A board with an EEPROM writes
`fbneo/<driver>.nv`; atlas's card names `.fs` and `.memcard` and no `.nv`. The same at HEAD `aceeebe` (`eeprom.cpp:97`,
`:120`; `libretro.cpp:1923`). sigil's cell cites `retro_common.cpp` and `eeprom.cpp` (sigil's `README.md:323`); T70, the
name alone, is `sigil only`. **[O]** Which drivers carry the EEPROM device.

**The save root (S2).** **[V]** At `libretro/RetroArch` `a79435a` — the pin of
`docs/research/retrodeck-save-placement.md` — the sorted save directory appends the content directory's name under
`sort_savefiles_by_content_enable` and then `library_name` under `sort_savefiles_enable` (`runloop.c:8826-8840`). HEAD
`01cca3a` does the same (`runloop.c:9682-9696`). sigil's sentence names only the core-named subfolder
(`docs/c.md:95-97`) and not which name it carries (row S3).

**Stems (S10).** **[V]** `libretro/beetle-ngp-libretro` `139fe34` (`atlas/data/core_oddities.json:2571`) copies the
frontend's canonical content name as its base name (`libretro.c:215-223`) and names the `.flash` after it (`:807-809`);
RetroArch `a79435a` sets that name to the archive's base name for archived content (`tasks/task_content.c:585-590`,
`:631`). A zipped game's `.flash` therefore takes the archive's name, where sigil's stem takes the member's (§1.3). At
HEAD: beetle-ngp `a50d5ac` (2026-06-14) `libretro.c:222`, `:807-809`; RetroArch `01cca3a` `tasks/task_content.c:600`,
`:641`. FinalBurn Neo `01e29d5` names `.fs` after the selected driver (`src/burner/libretro/libretro.cpp:2186`), whose
name takes a subsystem prefix from the content's parent directory (`cv_`, `gg_`, `md_` and others, `:2352-2415`) and is
`neocdz` for every Neo Geo CD game (`:2416-2421`); sigil's `{romset}` is the content stem. HEAD `aceeebe` keeps the
prefixes (`:2387-2512`) but names a CD game's `.fs` by `CDInfo_GamePrefix()` instead of the driver (`:2195`), so atlas's
`neocdz` spelling is the shipped build's. **[O]** What `CDInfo_GamePrefix()` returns; and a zipped Neo Geo Pocket game's
`.flash` and a FinalBurn Neo console-subsystem save, observed on a machine.

### atlas's own blind spot

**[V]** atlas decides a card's generation by feature detection (`atlas/installations.py:1623-1648`). For a card selected
by a rule over several options, `_rule_confirmed_choice` retires the card only when one of the options the card names is
not registered (`:1715`), and otherwise confirms it (`:1730-1736`); an option the installed core registers and the card
does not name is not looked at. The Beetle Saturn card names two, `beetle_saturn_shared_int` and
`beetle_saturn_shared_ext` (`atlas/data/core_oddities.json:3105-3108`), and its rule reads those two alone
(`atlas/mode_rules.py:250-280`). With a confirmed card, a version that differs from the audit's record is reported as a
source note, not a caveat (`_verification_notes`, `atlas/installations.py:1789-1797`), and for Beetle Saturn the record
pins no core version (`atlas/data/core_audit.json:380`), so the core's own version is not compared
(`atlas/installations.py:1752`).

**[V]** A run shows it. A scratch fixture — §2.2's machine for `mednafen_saturn`, the core also registering
`beetle_saturn_save_method` with the values and default of `1382b85` — was answered six times: the core stamped with
`ccba526` or `1382b85`, and `retrodeck.json` stating no version, the audited `0.10.9b`, or another. Each time atlas
answered `declared`, mode `per-game`, the files `<stem>.bcr`, `<stem>.bkr` and `<stem>.smpc`, with no caveat about the
core. The core's stamp changed nothing. The signals were a source note "feature-detected: core registers
beetle_saturn_shared_int, beetle_saturn_shared_ext", a second source note on the version record where `retrodeck.json`
stated no version or another one, and, for another version, the caveat `arrangement-version-drifted` — which is about
the RetroDECK installation as a whole (`atlas/evidence.py:163`), not about the core.

**[D]** Such a build writes `<stem>.srm` through the frontend by default and no `.bkr` (Beetle Saturn §4 above), so
atlas's answer for it names a `.bkr` that never appears and leaves out the `.srm`. **[O]** Not observed on a machine.

## 5. Identification: sigil's ids in atlas's `<save_id>` holes

Where an emulator keys a save on an id it reads from the content and atlas states the name, atlas leaves that id as a
`<save_id>` hole in the declared name, and a client fills it; where atlas refuses to state the entry names (I9–I12) it
declares no hole. sigil reads such ids from the ROM. **[V]** The holes atlas declares (search: `<save_id>` in
`atlas/data/*.json`, `TEMPLATE_SAVE_ID` joined into a path or name in `atlas/installations.py`): the Flycast and
SwanStation cards; the directories atlas builds for PCSX2's texture replacements (`atlas/installations.py:5523`), Cemu
(`:7480`) and Azahar (`:7693`), each with its `physical_dir` twin (`:5590`, `:7482`, `:7695`); and DuckStation's
per-game card names, by serial (`:7954`) and by title (`:7958`). Two of these are not a save id read from the content:
the title-keyed card (I4) and the texture directory, which holds no save (I7).

- **I1** Dreamcast: the id in Flycast's per-game VMU name — different: sigil trims, cuts at a NUL and replaces nothing;
  Flycast's steps differ by build (below). Sources: atlas's `atlas/data/core_oddities.json:968`,
  `atlas/data/core_oddities.json:1095`; sigil's `src/dreamcast.c:48-70`, `src/sigil.c:351-361`.
- **I2** PlayStation: the serial in SwanStation's per-game card name — sigil only: atlas states the hole, not the
  serial's spelling; SwanStation turns the executable name `SCES_123.45` into `SCES-12345` (libretro/swanstation 4d309c0
  `src/core/system.cpp:233-248`), the spelling of sigil's `title_id`. Sources: atlas's
  `atlas/data/core_oddities.json:3721`; sigil's `src/cnf_parser.c:101-103`.
- **I3** PlayStation: the serial in DuckStation's per-game card name — sigil only: atlas states the hole, not the
  serial's spelling; DuckStation takes its database's serial for a disc the database knows and the executable name,
  rewritten as SwanStation rewrites it, otherwise (stenzek/duckstation 64655818e `src/core/system.cpp:872-887`,
  `:4032-4041`). Sources: atlas's `atlas/installations.py:7955`; sigil's `src/cnf_parser.c:101-103`.
- **I4** PlayStation: DuckStation's title-keyed card name — atlas only: atlas spells this hole `<save_id>` too, and no
  sigil field fills it. Sources: atlas's `atlas/installations.py:7974`; sigil: none (`include/sigil.h:115-136`: the
  result carries ids, no title).
- **I5** Wii U: the title id in Cemu's save path — different: atlas's hole is both words (`00050000/1010ec00`); sigil's
  `save_id` is the low word, its `raw_serial` all sixteen digits (below). Sources: atlas's
  `atlas/installations.py:7495`; sigil's `src/wiiu_wua.c:14-21`, `src/wiiu_wua.c:102`.
- **I6** 3DS: the title id in Azahar's save path — same: both `<high>/<low>`, lowercase, `usage` folder-split. Sources:
  atlas's `atlas/installations.py:7710`; sigil's `src/threeds.c:274`.
- **I7** PlayStation 2: the serial in PCSX2's texture directory — different: atlas spells the hole `<save_id>` but fills
  it with the serial — sigil's `title_id` on PS2, not its `save_id` (`BA…`); a texture directory, not a save. Sources:
  atlas's `atlas/installations.py:5580`; sigil's `src/ps2.c:20-29`.
- **I8** PlayStation 2 on LRPS2: a per-game id on the host — different: sigil's PS2 `save_id` names a folder inside a
  card image; the card atlas states is the host file (the guide's layering trap). Sources: atlas's
  `atlas/data/core_oddities.json:2446`; sigil's `README.md:26`.
- **I9** PSP: the per-game directory under PPSSPP's SAVEDATA — sigil only: atlas refuses the entry names; sigil's prefix
  is how a client finds them. Sources: atlas's `atlas/installations.py:7066`; sigil's `README.md:152`.
- **I10** PS3 and Vita: the per-title directory under savedata — sigil only: atlas refuses the entry names; sigil
  supplies them. Sources: atlas's `atlas/installations.py:9410`; sigil's `README.md:114`, `README.md:113`.
- **I11** Wii: the title directory in Dolphin's NAND — sigil only: atlas refuses the entry names; sigil's `save_id` is
  the lowercase hex Dolphin writes. Sources: atlas's `atlas/installations.py:6928`; sigil's `README.md:46`.
- **I12** GameCube: the `.gci` names in Dolphin's folder card — sigil only: atlas refuses the names; sigil's game id is
  the `-<gameId>-` a client matches. Sources: atlas's `atlas/installations.py:6702`; sigil's `README.md:154`.
- **I13** Xbox: the title directory inside the hard-disk image — same: both: no host path, the id names a directory
  inside the image. Sources: atlas's `atlas/installations.py:7238`; sigil's `README.md:194`.

**Flycast (I1).** **[V]** Flycast derives the game id from the ten-byte product number. At `flyinghead/flycast`
`1dac369` (`atlas/data/core_audit.json:217`) it trims trailing bytes of the set space and NUL (`core/emulator.cpp:841`;
`trim_trailing_ws`, `core/stdclass.h:145-153`, over `" \0"`, `core/stdclass.cpp:25`) and does nothing more. Commit
`97442c0` (2025-12-12, "terminate game id at first null character") then also cuts the id at its first NUL; at HEAD
`869038f` that is `core/emulator.cpp:859-860`, after the same trim at `:858` (`core/stdclass.cpp:26`). At both revisions
the per-game VMU name replaces a space and each of `/\:*?|<>` in the id with `_`, and an empty id falls back to the
content's name (`shell/libretro/oslib.cpp:44-62` at `1dac369`, `:46-64` at HEAD). sigil trims trailing bytes of a
different set, space and `0x09`–`0x0D` (`src/dreamcast.c:26-28`, `:50`), then cuts at the first NUL (`:51-56`), and
returns no id when what remains is empty, holds a byte outside `0x20`–`0x7E`, or has no upper-case letter or digit
(`:57-65`); it replaces nothing. Its README calls the result "the name flycast gives the per-game VMU file" (sigil's
`README.md:570-571`); atlas's card states the trim and the replacement (`atlas/data/core_oddities.json:1095`), as the
build atlas reads does them.

**[D]** Where the two meet, per case:

- A product number without a NUL: both drop trailing spaces. Where the trailing run of spaces and control whitespace
  holds a byte in `0x09`–`0x0D`, sigil drops the whole run and Flycast keeps the id up to and including the last such
  byte, at both revisions (`AB`, a tab, a space: sigil's id is `AB`, Flycast's `AB` and the tab).
- A product number with a NUL: Flycast's trim runs over spaces _and_ NULs from the end, sigil's over spaces and control
  whitespace only, so at both revisions they differ where a space stands before a NUL that only NULs and spaces follow
  (`AB \0\0`: Flycast's id is `AB`, sigil's keeps the space after `AB`). At HEAD both then cut at the first NUL; at
  `1dac369` Flycast does not cut, and an inner NUL followed by other bytes stays in its id.
- A product number sigil refuses: sigil gives no id, while Flycast still names the file after what its steps leave, or
  after the content where that is empty.

In the remaining cases sigil's id is Flycast's id, and a client fills the hole with it after the replacement. **[O]**
Which name `1dac369` writes for an id that keeps an inner NUL, which depends on how the path reaches the file system;
and whether any retail product number falls into one of the cases above or carries a replaced character.

**Cemu (I5).** **[V]** Cemu `v2.6` (`cemu-project/Cemu` `a6fb0a4`), the release atlas's record names
(`atlas/data/standalone_saves.json:88`), builds a save path from both words of the title id, `usr/save/%08x/%08x/…`
(`src/Cafe/OS/libs/nn_save/nn_save.cpp:133-146`); HEAD `5e09ec7` (2026-09-23) the same (`:132-145`). sigil's `save_id`
is the low word in lower case (`src/wiiu_wua.c:33`, `src/filename.c:189`, sigil's `README.md:46-49`). Its `raw_serial`
holds all sixteen digits, in upper case, on two routes: the `.wua` reader, the one binary Wii U reader
(`src/sigil.c:134`, `:308`; `src/wiiu_wua.c:31-32`, `:99-102`), and a sixteen-digit bracket starting `0005` in the file
name (`src/filename.c:183-189`, upper-cased by `match_hex_run`, `:50-56`). From an eight-digit tag in brackets or
parentheses, `raw_serial` is the low word alone, like `title_id` (`:193-207`). sigil's README says the last eight digits
"are what the save system keys on" (sigil's `README.md:502`) and shows Cemu's path with both words (`:46-49`). A client
fills the hole from `raw_serial` only when it holds sixteen digits: the first and the last eight, in lower case, joined
by `/`. From an eight-digit tag sigil gives no high word, and the hole stays the client's to fill.

**DuckStation (I3).** **[O]** The spelling DuckStation's game database gives the serial of a disc it knows, against
sigil's `LLLL-DDDDD`; for a disc it does not know, the rewritten executable name is sigil's spelling.

**PCSX2's texture directory (I7).** A client fills it with sigil's PS2 `title_id`, not its `save_id`. **[O]** Whether
PCSX2 spells the serial as sigil's `title_id` does — `GSTextureReplacements.cpp` at PCSX2 `v2.6.3`.

**LRPS2 (I8).** Nothing to fill on the host: sigil's PS2 `save_id` names a folder inside the memory-card image atlas
states, the layering the guide warns about (`docs/how-to-use.md`, "The layering trap").

## Open items

Each **[O]** above, with what would close it:

1. A Sega CD session under the shipped Genesis Plus GX, the save directory listed afterwards: no `<stem>.srm` (T15).
2. A session of Beetle PSX with sharing on, and one with the second card disabled, the save directory listed (T31–T37);
   the memory size of the device an unemulated port holds (the [D] about slot 1).
3. Which FinalBurn Neo drivers carry the EEPROM device (T71) — `libretro/FBNeo` at `01e29d5`.
4. What `CDInfo_GamePrefix()` returns at FinalBurn Neo `aceeebe`, and, on a machine, a zipped Neo Geo Pocket game's
   `.flash` and a FinalBurn Neo console-subsystem save (S10).
5. Which files Handy, SAME CDi and Nestopia write themselves, beyond any frontend file (T3, T4, T6–T8): an audit of each
   core by atlas's method, with sigil's rows naming the candidates.
6. The name Flycast `1dac369` writes for an id with an inner NUL, and whether any retail product number carries one or a
   replaced character (I1).
7. The serial spelling of DuckStation's game database for a known disc (I3).
8. The spelling of the serial PCSX2 names a texture directory by — `GSTextureReplacements.cpp` at `v2.6.3` (I7).
9. A Beetle Saturn build that registers `beetle_saturn_save_method`, run and its save directory listed, against atlas's
   answer for it (atlas's own blind spot).
