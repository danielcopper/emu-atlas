# How a core locates firmware — the question a name cannot answer

Sibling to [`system-firmware.md`](system-firmware.md), which asks whether a system starts at all without an image, and
to [`core-audit.md`](core-audit.md), which is about **save behaviour**. This one asks a third question about the same
declaration: when a core goes looking for the firmware it boots, what does it actually open? The three share only the
discipline — unfiltered reads, source or observation behind every claim, and a verdict that says what it rests on.

Every claim carries an evidence level:

- **[V]** verified — read from source, extracted from a binary, or observed on disk
- **[D]** derived — follows from verified facts by reading, but not directly observed
- **[O]** open — not yet established

Sources are cited as `file:line`. SwanStation refers to upstream at `4d309c05f` and Beetle PSX to `d6383bff` — each the
revision the deployed build stamps itself with — and RetroArch to `a79435a`, the pin this repository already uses for
RetroArch facts (see the header of [`retrodeck-save-placement.md`](retrodeck-save-placement.md)).

## The defect this exists for

A `.info` is a list of names. `atlas.firmware` reproduces it faithfully, and a consumer reads it as an instruction:
place a file called this, here. For most cores that is exactly right. For SwanStation it is not, and nothing in the
answer said so — the core opens a name from its own options and, where that fails, ignores every name it declared and
recognises whatever the directory holds by hashing it. Two consumers can therefore be wrong in opposite directions over
one core: one telling a user to fetch `scph5501.bin` for a machine that is already booting from a file under another
name, another reporting a complete BIOS set for a directory whose files the core will refuse.

The answer needed a word for the door, not a longer list of names. That word is `cores[].locating`, one of `by-name`,
`by-name-then-content` and `unestablished`; where it comes from and what it means to a client is in
[the guide](../how-to-use.md#how-the-emulator-finds-it--locating).

## What was read, and at which revision

**[V]** Two upstream trees and three deployed binaries. A core reports its own version through `retro_get_system_info`,
and all three put a short commit hash in it, so the binary names the revision its source must be read at rather than the
other way round. Each stamp below was read twice and independently: once from an unfiltered `strings -a` pass over the
shipped file, and once through atlas's own core probe (`atlas.machine.CoreInfo.library_version`). The two agreed for all
three.

| deployed core                 | reported version   | source revision                         |
| ----------------------------- | ------------------ | --------------------------------------- |
| `swanstation_libretro.so`     | `1.0.0 4d309c0`    | `libretro/swanstation@4d309c05f`        |
| `mednafen_psx_hw_libretro.so` | `0.9.44.1 d6383bf` | `libretro/beetle-psx-libretro@d6383bff` |
| `mednafen_psx_libretro.so`    | `0.9.44.1 d6383bf` | `libretro/beetle-psx-libretro@d6383bff` |

The cores sit under RetroDECK's `components/retroarch/rd_extras/cores/`. SwanStation's own version string is composed at
`src/libretro/libretro_host_interface.cpp:72` as `"1.0.0 " GIT_VERSION`, which is what makes the hash readable at all;
its `.info` states `display_version = "v0.1"`, a string that is not the build and can be held against nothing.

The two Beetle builds report the same revision, and it was measured on each rather than assumed from the other: they are
two build targets of one source tree, differing in renderer, and an entry covering a core it was never measured against
would be a guess.

`tests/test_core_firmware_tripwire.py` is what keeps this honest after the fact. Where a core entered in
`atlas/data/core_firmware.json` is deployed, it asks the binary for its version and fails when the entry's pinned
revision is not in the answer. It is machine-bound, so an ordinary CI run skips it and the weekly canary is where an
upgrade surfaces. It is a floor, not a proof: a revision can move without a version string moving, and a core whose
version carries no hash could not be checked this way at all.

## SwanStation — a name first, content as the fallback

Everything below is at `4d309c05f`.

**[V] The directory is never configurable.** The libretro build overrides `GetBIOSDirectory` to return the frontend's
system directory and nothing else (`src/libretro/libretro_host_interface.cpp:1059-1065`,
`RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY`). No core option points it elsewhere: of the **112** keys the build declares,
the only **3** naming a path are the per-region BIOS ones below. So on the `.info` route that directory is
`FirmwareContext.root`.

Counting rule, source side: a regex for `\{"(swanstation_[A-Za-z0-9_]+)"` over `src/libretro/libretro_core_options.h`,
deduplicated, then a substring test for `dir` or `path` on each key. Binary side, which is the second reading this
number rests on: **whole-line** matches of `^swanstation_[A-Za-z0-9_]+$` over the unfiltered `strings -a` dump of the
deployed core, deduplicated — and the two sets are not merely the same size, they are identical, with nothing in either
that the other lacks. The whole-line anchor is the rule and not a convenience: a `strings` line is one NUL-terminated
string, so a declared key stands on a line of its own, while the same regex run as a _substring_ scan returns **114** by
also matching two stems that are not keys — `swanstation_libretro`, from inside the file name `swanstation_libretro.so`,
and `swanstation_Controller`, from the per-port templates `swanstation_Controller%u_…` the core expands at run time,
where the match stops at the `%`.

**[V] One region is read per boot, and it decides which name.** The core option `swanstation_Console_Region` takes
`Auto` (the default), `NTSC-J`, `NTSC-U` and `PAL` (`src/libretro/libretro_core_options.h:80-95`); `Auto` resolves to
the region of what is being booted (`src/core/system.cpp:618-654` — an executable's or a disc's, falling back to NTSC-U
for a disc region it cannot place, `:650`), and a boot with no file at all defaults to NTSC-U (`:660-661`). The boot
then asks for exactly one region's image (`:667`).

**[V] The name comes from a per-region option.** `GetBIOSImage` (`src/core/host_interface.cpp:151-181`) reads
`BIOS/PathNTSCJ`, `BIOS/PathNTSCU` or `BIOS/PathPAL` by region (`:157-168`). The libretro settings interface spells a
`(section, key)` pair as `swanstation_<section>_<key>` (`src/libretro/libretro_settings_interface.cpp:11`) and returns
the frontend's answer or the C++ default where the frontend has none (`:14-15`). The options themselves are defined with
the defaults `scph5500.bin`, `scph5501.bin` and `scph5502.bin`, allowing `psxonpsp660.bin` and `ps1_rom.bin` beside them
(`src/libretro/libretro_core_options.h:96-134`).

**[V] RetroArch answers `GET_VARIABLE` with a core's own declared default wherever its options file holds no entry for
the key.** The chain, at `a79435a`: `runloop_environment_cb` handles `RETRO_ENVIRONMENT_GET_VARIABLE` by looking the key
up and answering with `core_option_manager_get_val` (`runloop.c:1419-1457`, the call at `:1442-1445`); that returns
`option->vals->elems[option->index].data` (`core_option_manager.c:1613-1625`); and `option->index` is set while the
value list is built, to the position of the value equal to the core's own `default_value` (`core_option_manager.c:897`
`core_option_manager_parse_option`, `:1021-1028`). The options file is consulted only **after** that, and moves
`option->index` only where it holds an entry whose value matches one the core declared (`:1031-1053`, the guard at
`:1037`). An absent entry therefore leaves the core's default standing, and that is what the core reads back.

**[D] So the empty-name branch at `src/core/host_interface.cpp:172-173` — which would go straight to the search — is not
the path a libretro build takes**, and the named route is the normal one. Derived rather than observed: it follows from
the three options declaring non-empty defaults and from the RetroArch chain above, not from watching a launch.

**[V] The named open consults no table whatsoever.** `LoadImageFromFile` (`src/core/bios.cpp:81-105`) accepts a size of
exactly `0x80000`, `0x400000` or `0x3E66F0` (`src/core/bios.h:9`) and then reads the first 512 KiB into a buffer sized
`BIOS_SIZE` (`:83`, `:98`). Nothing is hashed and nothing is looked up. **Any file of an accepted size, under the
configured name, boots** — known or not, right region or not.

**[V] The search is the fallback, and it is the content half.** Where the named file is absent or its open fails,
`GetBIOSImage` calls `FindBIOSImageInDirectory` (`:178-179`, and `:172-173` for the empty name).
`FindBIOSImageInDirectory` (`src/core/host_interface.cpp:183-244`):

- lists the directory non-recursively, hidden files included (`:188-189`);
- skips anything whose size is none of the three (`:197-200`);
- loads each survivor and takes an md5 over what was read — which is the first 512 KiB, because `GetHash` digests
  `image.size()` (`src/core/bios.cpp:72-79`) and the image is `BIOS_SIZE` long;
- looks that hash up in a 27-row table compiled into the binary (`src/core/bios.cpp:27-70`);
- returns the **first** file whose row's region is the console's or `Auto` (`:214-217`, with `IsValidHashForRegion` at
  `src/core/bios.cpp:118-125`);
- otherwise keeps one fallback, and a known image is never displaced by an unknown one (`:220-226`);
- with no candidate at all, reports `No BIOS image found for %s region` and returns nothing (`:229-234`);
- **with only a fallback no row knows, returns nothing as well** (`:237-238`) — an unrecognised file is refused on the
  search route, though the same file boots under a configured name;
- with a known image of the wrong region, boots it and logs `Falling back to possibly-incompatible image` (`:240-243`).

Nothing returned means the launch stops: `Failed to load %s BIOS.` and `Shutdown()` (`src/core/system.cpp:667-673`).

**[V] The table is SwanStation's own and cannot be borrowed.** Of the 27 rows, 24 carry an md5 that
`atlas/data/duckstation_bios.json` (104 rows at `stenzek/duckstation@64655818e`) also carries under the same region, and
3 do not: the `Auto` rows `c53ca590…` (PSP), `c02a6fbb…` and `81bbe60b…` (PS3). Counting rule: a regex over the
`s_image_infos` initialiser pairing each `ConsoleRegion::<R>` with its `MakeHashFromString("<md5>")`, matched against
`images[].md5` of the packaged DuckStation table; the array's own declared length (`s_image_infos[27]`) agrees with the
27 rows the regex finds, and no row matched an md5 under a different region. The three it does not share are consistent
with the hash scopes differing — SwanStation digests the first 512 KiB, while the DuckStation table is recorded as being
over the whole file (`atlas/data/README.md`, `duckstation_bios.json`) — so recognising a SwanStation search needs a
table of SwanStation's own, generated at this revision with this scope.

**[V] That table is packaged.** `atlas/data/swanstation_bios.json`, generated from `src/core/bios.cpp` and
`src/core/bios.h` at this revision by `scripts/generate_bios_table.py --emulator swanstation`: 27 rows of
`{name, region, md5}`, the three accepted sizes, `hash_scope: 524288` and `unknown: "refused"`. The row shape is what
the recognition reads and nothing else — upstream's fourth column is `patch_compatible` (`src/core/bios.h:26`), which
gates patching an image the core has already loaded rather than recognising one, and there is no priority column here at
all, so every row ranks alike and the directory's own order is the tie-break the core applies. The scope and the refusal
are read off the code rather than off the table: the core allocates an image of `BIOS_SIZE` and reads that many bytes
into it (`src/core/bios.cpp:83`, `:98`), and its search returns nothing for a candidate no row holds (`:237-238` of
`host_interface.cpp`). Both are stated a second time in the core's knowledge entry with those citations, and the
resolver holds the two against each other — a table regenerated from a build that moved either one stops the answer
rather than being read under a citation that no longer describes it.

## Beetle PSX — names, and only names

Both deployed builds, at `d6383bff`. One source file carries the whole route: `firmware_is_present`
(`libretro.cpp:176-335`).

**[V]** Per console region the function fills a list of spellings and tries them in order: three for Japan (`:252-254`),
nine for North America (`:260-268`) and six for Europe (`:274-279`) — 18 in total, counting rule being a regex over the
`bios_name_list[<n>] = "<name>"` assignments between `:250` and `:283`. Each is joined under `retro_base_directory`
(`:290`), which is the frontend's system directory (`:3049`), and tested with `filestream_exists` (`:298`). The first
that exists wins (`:298-302`); a region with no hit reports the firmware missing and returns false (`:305-318`). Ahead
of the region list, the two override options are tried under the same rule with four spellings of their own
(`:188-247`).

There is no size gate and no directory listing anywhere in it. A file the function was not given a spelling for is never
looked at.

**[V] One call per load, for one region.** `firmware_is_present(region)` is called at `:2110` and nowhere else (counting
rule: occurrences of `firmware_is_present(` in the file — two, the definition and that call), so what a launch needs is
one image of its own region rather than one of each. The region is the disc's: `region = CalcDiscSCEx()` (`:1980`).

What is dead there is the **override**, not the setting behind it. `:1982-1983` replaces the detected region with
`psx.region_default` only where `psx.region_autodetect` is false, and that one is hard-wired to 1
(`mednafen/settings.c:98-99`), so that branch is unreachable. The same `psx.region_default` is read all the same, one
level down: it is the value `CalcDiscSCEx` seeds `ret_region` with (`:1563`) before a disc's own `SYSTEM.CNF` overwrites
it (`:1572` into `:1410`), and `MDFN_GetSettingI` answers 1 for it — `REGION_NA` (`mednafen/settings.c:53-56`). So a
disc stating no region is read as North America. No core option names a region either: the only occurrence of the word
in `libretro_core_options.h` is the override option's own description.

**[V] The override option, and what selects it.** `libretro_core_options.h:1094-1108` declares three values — `disabled`
(the default), `psxonpsp` and `ps1_rom` — and `libretro.cpp:3265-3280` maps them to 0, 1 and 2, leaving the previous
value standing for anything else. The key carries the renderer: `BEETLE_OPT` spells it `beetle_psx_hw_` plus the option
name where `HAVE_HW` is defined and `beetle_psx_` without it (`libretro_options.h:27-31`), and an unfiltered
`strings -a` pass over each deployed binary finds exactly one spelling of it per build (counting rule: lines matching
`override_bios`, case-insensitively, over the whole dump — one in each). A selected image that exists returns before any
region list is filled in (`:227-244`), so it serves whichever region the disc turns out to be.

**[V] The existence test is an open, not a stat.** `filestream_exists` succeeds exactly where `filestream_open` does
(`libretro-common/streams/file_stream.c:104-121`), and the hint it passes — `RETRO_VFS_FILE_ACCESS_HINT_NONE` — leaves
the unbuffered flag unset (`libretro-common/vfs/vfs_implementation.c:372-377`, tested at `:427`), so the call is
`fopen(path, "rb")` at `:445` with the mode string set at `:382`. The `open(path, O_RDONLY)` at `:499` is the other
branch, reached only under the frequent-access hint.

**[V-measured] Both of those succeed on a directory.** Measured against glibc, not watched in a launch: `fopen` on a
directory returns a stream, the seek-to-end that follows answers a size of 0, and the first read fails with `EISDIR`;
`open(path, O_RDONLY)` on one returns a descriptor. So a directory under one of these names ends the walk there and the
core then reads nothing out of it (`:2112-2114`, `:2139`), while a dead symlink fails the open and the next spelling is
tried.

**[V] After a miss the core opens no name it has not already tried.** Where `firmware_is_present` returns false the
loader falls back to `psx.bios_jp` / `psx.bios_na` / `psx.bios_eu` (`:2116-2135`), and those settings answer with the
lowercase first spelling of the same region list (`mednafen/settings.c:118-123`) under the same directory — a second
look at a path that was just tried. What the user sees is the error screen `firmware_is_present` raised on its way out
(`:314-315` with `:4895-4905`).

**[V] The SHA1 beside each list identifies nothing.** A mismatch logs
`Unsupported firmware may cause emulation glitches.` and **still returns true** (`:322-329`), so it warns and gates
nothing — which is why the word is `by-name` and not something conditional on content.

**[V] Why the word is pinned to the revision rather than to the emulator.** Upstream has since grown a second door: a
SHA1 directory search, `search_firmware`, added by `c21c2df` and present at `82d8e05` (`libretro.c:388`, called at
`:510` and `:611`). A build carrying it would be a `by-name-then-content` core described by a `by-name` entry. It is not
in the deployed builds, measured two ways. In the source, `search_firmware` occurs in **0** of the 768 files tracked at
`d6383bff` (counting rule: `git grep -a` for the identifier over that tree). In the binaries, the log string that
function opens with — `Searching for firmware checksum: %s` — occurs in 0 lines of the unfiltered `strings -a` dump of
either shipped core, while `Checking if required firmware is present...`, which `firmware_is_present` logs on entry at
`:182`, occurs in both; a negative reading needs the positive control beside it or it is only a failed grep. The
tripwire above is what turns a future upgrade into a red test rather than a wrong entry.

## What a verdict here must cite

An entry in `atlas/data/core_firmware.json` carries three fields always — `locating`, `build` and `provenance` — and two
more where its word names two doors. The loader refuses an entry with any of the three missing, with a field beside them
that nothing here reads, or with any part of one left blank (`atlas/core_firmware.py`, `tests/test_core_firmware.py`):

- `locating.mode` — one word from the closed vocabulary, and **never** `unestablished`. That value is what a core with
  no entry already answers; an entry stating it would be a citation for having read nothing.
- `locating.citation` — the `file:line` readings the word rests on, at a revision the entry names. Not a summary: the
  branch that picks the name, the branch that opens it, and the branch that does anything else.
- `name_route` — the options or the names a launch opens, in **one of two shapes**, and the word decides which. Neither
  is ever guessed at from what happens to parse: an entry states the key of the shape it means, and one stating both or
  neither is refused.
  - On `by-name-then-content` it is **required**, because a word naming two doors and describing neither leaves the
    resolver guessing what this file exists to state. It carries `region_option` (the key that pins the console region,
    its declared default, and every value it takes mapped to a region token — `null` for a value that pins none) and
    `region_keys` (the key that names the image per region, with the default the core declares). Every region the option
    can pin needs a key, or a launch it pins is one the route says nothing about.
  - On `by-name` it is **optional**, and it is the other shape: `override_option` (the key tried ahead of everything
    else, its declared default, and every value it takes mapped to the list that value selects — `null` for a value that
    selects none) and `regions` (per console region the ordered `spellings` that region's launch tries and the `sha1`
    the core expects behind them). A `by-name` entry without it is the ordinary case — a core that opens the names it
    was declared with. Each list is held to its shape: a spelling is a bare file name, because the core composes the
    directory itself; a list never repeats a name, because the list is an order; the digest is forty lowercase hex
    digits, so two readings of one image compare equal; one name is never stated by two lists, or which list a file
    belongs to would depend on which one a reader walked first; and the option has to select a list under some value, or
    the block describes a door that never opens.
  - Either way its citation covers the branch that decides the name and the branch that opens it, and marks as `[D]`
    anything resting on what the FRONTEND answers rather than on the core's own code.
- `content_route` — required and refused the same way. It names the packaged table the search recognises a directory by
  (a bare file name beside the others, never a path), the number of leading bytes the core hashes, and what it does with
  an image no row holds. Its citation reads those out of the core's source; the table states the last two as the
  generator read them, and the resolver holds the two against each other, so a table regenerated from another build
  stops the answer rather than being read under a citation that no longer describes it.
- `build.revision` and `build.citation` — the short hash the deployed binary reports and how that was read, so the
  tripwire has something to compare and a later reader can repeat the measurement.
- `provenance.source` — which tree at which commit, and what was done to reach the citations.

Every string is refused blank as well as empty: a citation of spaces reads as filled in to anything that only checks for
emptiness.

## Scope

Three cores are entered. Every other libretro core answers `unestablished`, and that is the honest state — not a backlog
with a deadline, and not a claim that the others are `by-name`. The standalone emulators atlas carries a firmware card
for answer from the shape of that card instead, with no second table: a card that states a search describes a directory
read with per-region keys in front of it, which is `by-name-then-content`, and a card that states `files` or
`config_files` names every path the emulator opens, which is `by-name`.

**The vocabulary carries only the words a producer exists for.** Three of them, and each is reachable: two rules put a
word on an answer — the packaged entry a `.so` reaches, and the shape of a standalone card — and `unestablished` is what
a core neither of them describes takes. Over the 215 `cores[]` blocks the vector corpus holds, the words come out 137
`unestablished`, 43 `by-name` and 35 `by-name-then-content` (counting rule: the serialized `locating` of every core
block in every `expected` tree under `vectors/machines/`, tallied by value).

A value nothing produces would be a claim with no mechanism behind it, and a branch a consumer could write and never
enter. So the list grows with the readings rather than ahead of them, which costs nothing: adding a value is a
compatible change, and removing one is not.
`tests/test_core_firmware.py::TestTheVocabularyIsTotal::test_every_published_value_has_something_that_produces_it` holds
that by comparing the union of what the two rules answer over the cards packaged today, plus the default, against the
published tuple.

**A pure content search would be the next word**, added the day a core is read that does one — an emulator that names no
file whatsoever and decides entirely on what a directory holds. DuckStation is the nearest thing here and it is not that
case: its three region keys ship empty, but they are read first and that branch is in the binary, so the word for its
**code** is `by-name-then-content`. Whether any deployed emulator searches without naming anything is **[O]** — nobody
has read one that does.
