# RetroDECK save placement — research findings

Findings from reading RetroDECK 0.10.9b, RetroArch upstream, EmuDeck upstream, the shipped core binaries, and one real
installation. Collected before fixing the architecture for the RetroDECK/libretro save phase, so the design answers to
evidence rather than the other way round. The architectural consequences are settled in `DESIGN.md`; this document holds
the evidence.

Every claim carries an evidence level:

- **[V]** verified — read from source, extracted from a binary, or observed on disk
- **[D]** derived — follows from verified facts by arithmetic or reading, but not directly observed
- **[O]** open — not yet established

Sources are cited as `file:line`. RetroArch refers to upstream at `a79435a`; RetroDECK to `0.10.9b`, whose components
live in the Flatpak at `/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components/`,
**not** in the RetroDECK Git repository. EmuDeck has two pinned generations: `acc45fc` (clone of 2026-07-23, the pin
§13's original findings were read at) and `863ab69` (default branch `main`, fetched 2026-08-09, the pin behind §13b and
the corrections marked in §13); ES-DE upstream facts cite the `v3.4.1` tag — the current stable, and the same revision
RetroDECK's shipped fork derives from. Flatpak facts cite tag `1.16.6` — the reference machine's own flatpak — at commit
`e761a8885453c217a931281092a641ebbdd0a0c6` (see §15's env-composition subsection).

## 1. The target matrix

**[V]** From the ES-DE variant RetroDECK ships (`components/es-de/share/es-de/resources/systems/linux/es_systems.xml`):

|                                                 |              |
| ----------------------------------------------- | ------------ |
| declared systems                                | 172          |
| emulator entries                                | 509          |
| — of which libretro                             | 391 (76%)    |
| — of which standalone                           | 118          |
| distinct libretro cores                         | 159          |
| distinct standalone runners                     | 23           |
| libretro-only / mixed / standalone-only systems | 95 / 54 / 23 |
| systems with at least one libretro entry        | 149 (86%)    |

**[V]** 211 core `.so` files are installed under `components/retroarch/rd_extras/cores/`.

This is the scope of "atlas covers RetroDECK". Any single installation exercises a small fraction of it, so coverage
cannot be established by playing — see §10 for what can and cannot be reached without ROMs. In the resolver model the
matrix is a **test matrix**, not a work list: one reading procedure covers all 391 libretro entries at once; each
standalone config format is its own procedure.

## 2. Where RetroDECK keeps its truth

**[V]** `$XDG_CONFIG_HOME/retrodeck/retrodeck.json` is the single root of truth for every path, where `XDG_CONFIG_HOME`
is `~/.var/app/net.retrodeck.retrodeck/config`. `paths.saves_path` governs saves.

**[V]** `prepare_component <action> <component>` (`functions/framework.sh:613`) distributes those roots into each
emulator's own config. Actions: `reset`, `postmove`, `startup`.

**[V]** `dir_prep "<real>" "<symlink>"` (`functions/other_functions.sh:245`) creates the real directory under
`saves_path` and places a symlink where the emulator natively writes. The emulator is unaware.

**[V]** The per-emulator knowledge lives in `components/<name>/component_prepare.sh` plus `component_manifest.json`,
with shipped default configs under `components/<name>/rd_config/` using a `RETRODECKHOMEDIR` placeholder. None of this
is in the RetroDECK Git repository — it ships with the Flatpak.

**[V]** **The config is the truth, never the existence of a folder.** The observed machine carries _two_ retrodeck
roots: a stale `~/retrodeck` (backups, bios, cheats, mods, roms — no `saves`) and the real one on the SD card that
`retrodeck.json` points to. A detector probing paths instead of reading the config picks the wrong root.

**[D]** The commonest Steam Deck failure shape follows directly: SD card not mounted → RetroDECK present,
`retrodeck.json` readable, `saves_path` pointing into an absent mount. Detection must report this as a health state, not
hand out the syntactically correct path as if it were usable.

## 3. Three save families

**[V]** RetroDECK does not have one save layout. It has three, following different rules.

**a. RetroArch / libretro — config-driven.** `components/retroarch/component_prepare.sh:27` sets only
`savefile_directory = <saves_path>`. The layout _below_ that root is decided by RetroArch's own sort keys (§4).

**b. Standalone emulators — hard-wired to `saves/<system>/<emulator>/`.** Two mechanisms coexist: symlink (Dolphin
`saves/gc/dolphin/{US,EU,JP}`, `saves/wii/dolphin`; PPSSPP `saves/PSP/PPSSPP-SA`) and config key (PCSX2
`Folders/MemoryCards`, DuckStation `MemoryCards/Directory`, melonDS `SaveFilePath`, Dolphin `GBA/SavesPath`).

**c. libretro cores with their own save stack — `saves/<system>/retroarch-core/<CORE>/`.** Example: LRPS2 memory cards
at `saves/ps2/retroarch-core/LRPS2/memcards`, reached via a symlink from `bios/pcsx2/memcards`
(`components/retroarch/component_prepare.sh:97`).

Consequence: a system can have several save locations at once depending on which emulator launches it. PS2 has three.

## 4. RetroArch's directory math

**[V]** All of it lives in `runloop_path_set_redirect()`, `RetroArch/runloop.c:8752`.

```text
savefiles_in_content_dir      -> the ROM's own directory
savefile_directory unset      -> also the ROM's own directory   (runloop.c:8786)
otherwise                     -> <savefile_directory>
                                 + <content_dir>    if sort_savefiles_by_content_enable
                                 + <library_name>   if sort_savefiles_enable
```

**[V]** Order is content directory first, then core (`runloop.c:8827` then `:8835`).

**[V]** `content_dir` is the **parent directory name of the ROM**, via `fill_pathname_parent_dir_name`
(`runloop.c:8781`).

**[V]** An unset `savefile_directory` is not an unknown — RetroArch resolves it to the ROM's directory, identically to
`savefiles_in_content_dir` (`runloop.c:8786`). Irrelevant under RetroDECK, which always sets it; relevant for standalone
RetroArch.

**[V]** **The sorted directory is not guaranteed.** If it does not exist and cannot be created, RetroArch silently
reverts to the unsorted root (`runloop.c:8844`). The answer therefore depends on filesystem state, not on configuration
alone — read-only media or missing permissions change where a save lands.

### How the content is named — `runloop_path_set_basename`

**[V]** Every value above is derived from one string, `runtime_content_path_basename`, built once per load in
`runloop_path_set_basename` (`runloop.c:8673-8713`): the content directory is `fill_pathname_basedir` of it
(`runloop.c:8789`), the sort-by-content component `fill_pathname_parent_dir_name` of it (`runloop.c:8781`), and the save
file is its last component with `.srm` appended. Naming the content wrongly therefore moves the directory _and_ the file
name at once.

**[V]** The save file is named **twice**, and the second naming governs. `runloop_path_set_names` first builds
`fill_pathname(basename, ".srm")` (`runloop.c:8720`), which truncates a _second_ extension off the stem — `Game.v1.1`
would become `Game.v1.srm`. `runloop_path_set_redirect` then overwrites it whenever the resolved save path is a
directory (which is every case atlas answers) with `fill_pathname_dir(name.savefile, basename, ".srm")`
(`runloop.c:8929-8936`), and `fill_pathname_dir` appends the basename's last component unchanged
(`file_path.c:436-443`). So the save of `…/Game.v1.1.n64` is `Game.v1.1.srm`: the stem is cut once, by
`runloop_path_set_basename`, and never again.

**[V]** **Content inside an archive is named after the entry, not the archive.** Under `HAVE_COMPRESSION` the basename
is rebuilt from two archive-aware calls before any extension is cut: `path_basedir_wrapper` truncates at the archive
delimiter and keeps the directory (`file_path.c:1322-1341`), `path_basename` returns everything _after_ the delimiter
(`file_path.c:692-700`). So `/roms/n64/pack.zip#Game.n64` becomes `/roms/n64/Game` — save `Game.srm` in `roms/n64` — and
`/roms/n64/pack.7z#disc/Game.n64` becomes `/roms/n64/disc/Game`, an in-archive folder that lands in the path.

**[V]** The delimiter is not simply "the first `#`": `path_get_archive_delim` (`file_path.c:172-220`) accepts only a `#`
directly preceded by `.7z`, `.zip`, `.zst` or `.apk` (letters case-insensitive, at least one character before the dot),
so `Game #2.gb` is one ordinary file name.

**[V]** `HAVE_COMPRESSION` is set unconditionally by the build system (`Makefile.common:1988` — "the ZIP archive backend
decodes DEFLATE through the built-in inflate when zlib is not present"), and the RetroArch shipped in RetroDECK 0.10.9b
reports `7zip extraction support: yes` / `zip extraction support: yes` under `--features`. The compressed branch is the
one that runs.

**[V]** **The extension is truncated on the whole path, not on the basename.** The cut is
`strrchr(runtime_content_path_basename, '.')` guarded by `dst - <start of the path> > 0` (`runloop.c:8710-8711`) — the
comment says "not when the path is relative and begins with a dot", and that is all the guard does: it protects index 0.
For an extensionless ROM under a directory whose name carries a dot the last dot _is_ in the directory name, so
`/roms/My.Games/rom` is named `/roms/My` and its save is written to `/roms/My.srm`, one level up from the ROM. A
trailing slash falls out of the same math: `path_basename` returns nothing for `/roms/psx/Game.cue/` and the dot is cut
anyway, so it names the same ROM as the path without the slash.

**[D]** A path whose last component is empty _and_ carries no dot (`/roms/psx/Game/`) leaves the basename ending in a
slash: `fill_pathname` finds no dot in an empty basename and concatenates, so RetroArch would write
`/roms/psx/Game/.srm` (`file_path.c:345-358`). atlas states that the path names no file instead of observing a
directory's dotfiles.

### RetroDECK's shipped defaults

**[V]** From `components/retroarch/rd_config/retroarch.cfg`:

```text
savefiles_in_content_dir          = "false"
sort_savefiles_by_content_enable  = "true"
sort_savefiles_enable             = "false"
```

So the RetroDECK default layout is `saves/<content_dir>/<rom_stem>.srm`.

**[V]** Observed on a real installation, both branches of the same rule:

- `roms/gba/Mario Kart - Super Circuit (USA).zip` → `saves/gba/Mario Kart - Super Circuit (USA).srm`
- `roms/psx/Final Fantasy VII (USA).m3u/` (a _directory_, the multi-disc convention) →
  `saves/Final Fantasy VII (USA).m3u/Final Fantasy VII (USA).srm`

Note that `saves/gba/` is **not** a per-system directory. It carries the system name only because the ROM's parent
folder happens to be named that. Any consumer assuming `saves/<system>` is structurally wrong for RetroArch.

## 5. `library_name` — one value, four paths

**[V]** `sort_savefiles_enable` appends `sysinfo->library_name` (`runloop.c:8839`) — the core's display name from
`retro_get_system_info`, not the `.so` filename.

**[V]** `config_load_override()` names the override directory by the same value:
`core_name = sys_info->info.library_name` (`configuration.c:7104`).

```text
saves/<content_dir>/<library_name>/          sort-by-core
config/<library_name>/<library_name>.cfg     core override
config/<library_name>/<content_dir>.cfg      content-dir override
config/<library_name>/<rom_name>.cfg         game override
```

**[V]** Extracted from 210 of 211 installed cores by `dlopen` + `retro_get_system_info` — the same read RetroArch itself
performs. The one failure is `applewin_libretro.so`, which needs `libslirp.so.0` — present inside the Flatpak sandbox,
absent on the host; a host artifact, not a core defect. Crash isolation matters: each core was probed in a forked child.

**[V]** **Only 27 of 210 cores have a `library_name` equal to the `.so` basename.** Deriving the path component from the
filename is wrong for 87% of the matrix.

```text
mgba             -> mGBA                 pcsx2       -> LRPS2
mednafen_saturn  -> Beetle Saturn        dolphin     -> dolphin-emu
mednafen_ngp     -> Beetle NeoPop        swanstation -> SwanStation
mupen64plus_next -> Mupen64Plus-Next     melonds     -> melonDS
```

**[V]** Independently cross-checked against a real installation: `saves/gba/mGBA/`, `saves/ps2/retroarch-core/LRPS2/`
and `bios/dolphin-emu/` all exist on disk carrying exactly the extracted names.

**[V]** **The `.info` `corename` is not the same value.** Compared across all installed cores: 147 match, **56
disagree**, 7 have no usable `.info`. The bsnes family shows why this is dangerous: `bsnes2014_accuracy`, `_balanced`
and `_performance` all report `library_name = "bsnes2014"` — one shared save directory and one override directory —
while their `.info` files carry three distinct `corename`s. A resolver using `.info` would invent three directories
where RetroArch uses one. **`.info` must never serve as a path source**; it remains useful for capability queries (§15).

## 6. The override chain

**[V]** `configuration.c:7095` `config_load_override()`. Files stack in this order, each appended after the previous
with a `|` separator; later files win:

1. `retroarch.cfg` (global)
2. `config/<library_name>/<library_name>.cfg` — core (`configuration.c:7161`)
3. `config/<library_name>/<content_dir>.cfg` — content directory (`configuration.c:7186`)
4. `config/<library_name>/<rom_name>.cfg` — game (`configuration.c:7210`)

**[V]** RetroDECK sets `quick_menu_show_save_content_dir_overrides = "true"`, so layers 3 and 4 are reachable from the
in-game menu. These are not hypothetical.

**[V]** **RetroDECK ships an override that changes the layout.**
`components/retroarch/rd_config/core-overrides/PPSSPP/PPSSPP.cfg` contains exactly one line:

```text
sort_savefiles_by_content_enable = "false"
```

It is deployed to `config/retroarch/config/PPSSPP/PPSSPP.cfg` on reset (`components/retroarch/component_prepare.sh:25`).
Any resolver that reads only the global config answers wrong for PPSSPP-libretro **out of the box**, on a stock
installation.

**[V]** When overrides are found, RetroArch unsets the command-line save/state path flags before reloading
(`configuration.c:7240`), so an override file can set `savefile_directory` even when `--save` was passed.

### One merge, two reads — what "later files win" does not mean

**[V]** The chain is not loaded layer by layer. `config_load_override()` collects the override files that exist into a
single `|`-joined list and makes **one** reload of the global config (`configuration.c:7243`); inside it,
`config_append_file` merges every listed file into one `config_file_t` where "the key-value pairs of the new config file
takes priority over the old" (`config_file.c:768-805`, appended at `configuration.c:6355-6392`). A getter therefore sees
exactly **one entry per key** — the last file that sets it. A layer between the global cfg and that last file is
shadowed: its value never reaches a getter at all.

**[V]** That reload runs **without** `config_set_defaults` (`configuration.c:7243`; the boot path calls it first,
`:5907`, and it is what seeds the platform default saves dir, `:5743-5744`). So whatever the reload refuses leaves
standing what the boot load left — the global cfg alone, read the same way. Two reads, then, not four:

| what the merged config holds    | effective value                               |
| ------------------------------- | --------------------------------------------- |
| a value the getter accepts      | that value                                    |
| a value the getter refuses      | the global cfg's own value, validated at boot |
| nothing (key absent everywhere) | the compile-time default (`config.def.h`)     |

**[V]** For `savefile_directory` the "getter accepts" test is `path_is_directory` (`configuration.c:6914-6933`), run per
load. Three branches, and only the first two set anything:

- the literal `default` → the platform default, **unvalidated** (`:6918`)
- an existing directory → that directory (`:6920-6922`)
- anything else → `RARCH_WARN`, nothing set (`:6931-6932`)

The empty string falls in the third branch, not the first: `config_get_path` returns true for an entry whose value is
empty and hands it on unchanged (`config_file.c:1202-1216`), and `path_is_directory("")` fails. So
`savefile_directory = ""` in an override **keeps the global cfg's root**, where `savefile_directory = "default"` drops
it. A resolver that treats blank and `default` as one spelling answers the same for a lone global cfg and differently
for every override.

**[V]** The two-read model is also what separates the two option gates, which otherwise look interchangeable:

| setting                 | read at                                                          | can an override change it? |
| ----------------------- | ---------------------------------------------------------------- | -------------------------- |
| `auto_overrides_enable` | copied into a local at `runloop.c:4941`, used at `:5002-5003`    | no — read before the merge |
| `game_specific_options` | `runloop.c:1529`, reached via `retro_set_environment` at `:5037` | yes — read after the merge |
| `global_core_options`   | `runloop.c:1530`, same call site                                 | yes                        |

`config_load_override` runs at `runloop.c:5003`; the core's own `retro_set_environment` at `:5037` drives
`runloop_init_core_options`, which is where the two option gates are read out of `settings->bools`. A per-core override
that says `game_specific_options = "false"` therefore really does keep RetroArch out of the game and folder `.opt`
files.

### Flatpak sandbox spellings

**[V]** RetroDECK's RetroArch runs inside the Flatpak, so the paths it writes into its own cfg are the paths it sees
from in there. Three namespaces appear in five lines of the live cfg:

```text
libretro_directory    = "/app/retrodeck/components/retroarch/rd_extras/cores"
libretro_info_path    = "/app/retrodeck/components/retroarch/rd_extras/cores"
rgui_config_directory = "/var/config/retroarch/config"
savefile_directory    = "/run/media/deck/Emulation/retrodeck/saves"
system_directory      = "/run/media/deck/Emulation/retrodeck/bios"
```

`/app` is the deployment, `/var/config` the app's private config directory, `/run/media` the SD card exactly as the host
sees it. 13 values in that cfg carry the `/var/config` spelling — including the override directory, which is the one
that decides whether §6's chain is read at all.

**[V]** Flatpak binds the app's per-app XDG directories into the sandbox under `/var`. Observed inside the RetroDECK
0.10.9b sandbox via `flatpak run --command=sh net.retrodeck.retrodeck -c '…'`:

```text
XDG_CONFIG_HOME=/home/deck/.var/app/net.retrodeck.retrodeck/config
XDG_DATA_HOME=/home/deck/.var/app/net.retrodeck.retrodeck/data

66312:3671164  /var/config
66312:3671164  /home/deck/.var/app/net.retrodeck.retrodeck/config
66312:3544555  /var/data
66312:3544555  /home/deck/.var/app/net.retrodeck.retrodeck/data
66312:3544556  /var/cache
66312:3544556  /home/deck/.var/app/net.retrodeck.retrodeck/cache
```

Same device and inode on both sides: one directory under two names, per app id. They are bind mounts, not symlinks —
`readlink -f /var/config` answers `/var/config`, so nothing resolves them for a reader on the host.

**[V]** The rest of `/var` inside the sandbox is the runtime's own filesystem, not the host's: device `239:21` against
`66310:2` for the host `/var`, holding only `cache config data db mnt run tmp`. `/run/user/1000` differs as well
(`71:3657` inside, `71:1` outside) — the sandbox gets its own runtime directory. `/run/media` is the same directory on
both sides (`24:5255`), which is why the SD-card paths above need no translation at all.

**[D]** A cfg-derived absolute path therefore falls into three classes, and the resolver treats them accordingly
(`_Sandbox`, `atlas/installations.py`): `/app/...` and `/var/{config,data,cache}/...` translate to host locations;
anything else under `/var/` or under `/run/user/` exists only inside the sandbox and is reported as
`sandbox-path-untranslated` rather than resolved against a host path that means something else; everything else passes
through untouched. Only a handle that carries an app id translates — a native install writes its cfg outside any
sandbox, where `/var/config` is a real (if unusual) host path.

**[V]** One exception sits inside `/var` and is not a sandbox path: on an **ostree host** — Fedora Silverblue and
Bazzite, both of which ship RetroDECK — `/home` is a symlink to `/var/home`, so real home directories live under `/var`
and are shared with the sandbox like any other home. A machine whose home is `/var/home/deck` would otherwise have its
own configured save, BIOS and core directories read as sandbox-internal, losing the whole firmware answer. Home paths
are therefore classified host-side before the sandbox-only prefixes, by the machine's own `home` and by the literal
`/var/home/` (RetroArch resolves the symlink when it writes the cfg; the caller may still pass the `/home/...`
spelling). atlas is not scoped to SteamOS — `home` is whatever the caller passes.

**[O]** The standalone `org.libretro.RetroArch` Flatpak that EmuDeck configures is not installed on the reference
machine, so its cfg has not been observed. The binds are a per-app-id Flatpak mechanism and apply unchanged, but the
observation is owed.

## 7. Which files are written, and by whom

**[V]** RetroArch itself writes exactly two files per content, core-independently (`save.c:710`):

```text
<stem>.srm    RETRO_MEMORY_SAVE_RAM
<stem>.rtc    RETRO_MEMORY_RTC
```

**[V]** Everything else is written by the core, directly, with its own naming. Observed on one installation: `.bcr`,
`.bkr`, `.smpc` (Beetle Saturn — three files for one game), `.flash` (Beetle NeoPop), `.eep` (PokeMini).

**[V]** Only subsystem content (`--subsystem`, used in the RetroDECK matrix e.g. for Neo Geo CD) makes RetroArch use
core-declared extensions from `retro_subsystem_memory_info`.

**[V]** There is no shipped metadata source for the per-core file set. Core `.info` files carry `supported_extensions`
(ROM extensions) and `libretro_saves`, and the latter does not discriminate: `mgba` (plain `.srm`) and `mednafen_saturn`
(three files) both declare `true`, and 101 of 295 `.info` files omit the key entirely.

**This splits the project.** Directory knowledge is one rule, centrally implemented in RetroArch, correct for all 159
cores at once. File-set knowledge is per core, sourced only from each core's own code, and never "finished". They are
different kinds of knowledge with different acquisition costs and different completeness. In the resolver model the
file-set question largely dissolves: for existing saves the resolver **observes** the set (`glob("<rom_stem>.*")`), and
a server-supplied save brings its own filenames — only the directory must be resolved.

**[V]** The observation is honest about one blind spot: with `savefiles_in_content_dir` it runs in the ROM's own
directory, where the content shares the ROM's name (`Game.bin` next to `Game.cue`, cover art, the archive itself). No
shipped source says which extensions are content — `supported_extensions` is per core, not per content file, and it
would not cover a frontend's box art — so the set is stated in full with a `content-dir-observation` caveat rather than
filtered against an invented list.

## 8. Cores that ignore the save directory

**[V]** Flycast does not use `savefile_directory` at all in its default configuration. Observed on disk under
`system_directory` (RetroDECK's `bios_path`):

```text
bios/dc/vmu_save_A1.bin   131072   modified — port A was used
bios/dc/vmu_save_B1.bin   131072   untouched since creation
bios/dc/vmu_save_C1.bin   131072   untouched since creation
bios/dc/vmu_save_D1.bin   131072   untouched since creation
bios/dc/dc_nvmem.bin      131072   the console's own flash
```

**[V]** This is consistent modelling rather than an oversight: a VMU is hardware attached to a controller, not to a
game. Flycast models the console — four ports by two slots, plus the console's own NVRAM for language, clock and system
settings.

**[V]** It is configurable via the core option `reicast_per_content_vmus`. Values and descriptions extracted from
`flycast_libretro.so`:

| Value                | Behaviour                                                                                     |
| -------------------- | --------------------------------------------------------------------------------------------- |
| `disabled` (default) | shared VMUs in the **system directory**                                                       |
| `VMU A1`             | _"creates a unique VMU 'A1' file in RetroArch's save folder for each game that is launched"_  |
| `All VMUs`           | _"creates up to 8 unique VMU files (A1/A2/B1/B2/C1/C2/D1/D2) for each game that is launched"_ |

The option changes not only granularity but the **root directory**. One core, two entirely different placements.

**[V]** RetroDECK does not set this option. Its shipped `retroarch-core-options.cfg` contains no Flycast entries; live
values are the core's own defaults, written on first run. The behaviour is inherited from upstream Flycast.

**[V]** The same "core writes into the system directory" pattern appears for Mupen64Plus-Next, which writes
`bios/Mupen64plus/mupen64plus.ini` and a GLideN64 shader cache there. It is a pattern, not a Flycast quirk.

**Design hazard worth surfacing:** the default places live save data inside the BIOS folder — a directory users and
tooling treat as static and replaceable. A backup that skips `bios/` loses Dreamcast saves; a "reset BIOS" destroys
them. This is a candidate for an atlas warning with a recommended configuration, not an upstream bug report.

**[V-live]** The per-game filename scheme, settled by a live run on RetroDECK 0.10.9b (2026-08-05, `All VMUs`, a played
and saved four-disc title): the four connected ports produced `<game id>.<port>.bin` of 131072 bytes each — observed
form `MK-5105950.A1.bin` … `.D1.bin` — in the **content-sorted save directory**, not in a subdirectory of the core's
own. The name is not the ROM's. It is `settings.content.gameId`, which for console content is the product number read
from the disc header (`core/emulator.cpp:838-841` at flycast@1dac369), with every character of `" /\:*?|<>"` replaced by
`_`; `hostfs::getVmuPath` composes it as `<vmu_dir>/<id>.<port>.bin` (`shell/libretro/oslib.cpp:38-67`). A legacy
`<content name>.<port>.bin` is read when it exists and the id-named file does not (`oslib.cpp:55-60`) — it did not
engage here. The directory is whatever `RETRO_ENVIRONMENT_GET_SAVE_DIRECTORY` returned (`libretro.cpp:2142-2148`), i.e.
the redirected one, so the sorting stages apply before the core ever sees it.

**[V-live]** The switch is a **move, not an addition**: `<system_directory>/dc/vmu_save_A1.bin` was byte-identical
before and after the run, and `dc_nvmem.bin` — the console's own flash, which no mode moves — was written in place.
Every port a mode does not cover keeps using the shared card, which in `VMU A1` mode means B1…D1 stay shared while A1
alone becomes per-content (`oslib.cpp:40-41`). A per-game answer is therefore partial by construction; atlas states it
with `complete: false`.

**[V-source]** The id branch is conditional, and the condition is not a config: `getVmuPath` takes it only for
`settings.platform.isConsole()` **and** a non-empty id; otherwise the name is `<content name>.<port>.bin`
(`oslib.cpp:62`), the ROM's own stem. Both conjuncts fail in practice — `maple_cfg.cpp:246-253` connects VMUs on ports
B1/C1 for Naomi games that need neither keyboard nor RFID reader, RetroDECK offers Flycast for seven arcade systems next
to `dreamcast`, and a disc whose header carries no product number falls to the same branch. atlas cannot decide it: it
reads no disc headers, by design. So the answer states the id-keyed set and hands the ROM-named alternative to the
caller in `filenames-content-conditional` — whoever can supply `save_id` is the same party that knows whether an id
exists at all, and when none does, the alternative is a set atlas has already filled.

**[O]** Which ports a per-content mode covers is itself content-dependent. `VMU A1` moves port A1 only, and Naomi's VMUs
are B1 and C1 — so for arcade content in that mode nothing moves at all, while for Dreamcast content three of four ports
stay shared. atlas states no file set there (`file-set-spans-roots`), which is honest but coarse: the card model
describes one root per mode and cannot say "these ports here, those there". Closing it needs a live Naomi run and a card
that can express a per-root, per-port split.

### Which directory "the system directory" is

**[V]** A core rooted there never reads `system_directory`; it asks `RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY` and is
answered by `runloop.c:1958-1999`, which returns three different things:

| Situation                                             | What the core receives                                            |
| ----------------------------------------------------- | ----------------------------------------------------------------- |
| `systemfiles_in_content_dir` set, or nothing standing | the **content's** directory (`:1963-1985`)                        |
| …and no content path loaded                           | the standing value, whatever it is — the fallback at `:1986-1987` |
| otherwise                                             | `settings->paths.directory_system` (`:1992-1997`)                 |

The fallback row is not "the empty string": it hands back `dir_system` unchanged, which is empty only when it was
_nothing standing_ that led into the branch. With a configured directory **and** the flag set, a contentless run
receives that configured directory. Either way the row is not an answer about a save — a save presupposes content — so
atlas answers a contentless query with the content template instead.

The content directory is `fill_pathname_basedir` of the **raw** content path (`:1977`, `file_path.c:475-480`) with the
trailing slash removed unless the result is the root (`:1979-1981`) — not the basedir of
`runtime_content_path_basename`, so for content inside an archive subdirectory the two spellings differ. On the launch
shape RetroDECK/ES-DE uses, `RARCH_PATH_CONTENT` is set from argv before the core exists (`retroarch.c:8159` →
`runloop_path_set_basename`, `runloop.c:8678`), so a core querying at `retro_init` already sees it.

**[V]** "Nothing standing" is not the same as "the key is absent", and the difference is the opposite of
`savefile_directory`'s:

- **Absent** → the platform default is already in place from `config_set_defaults` (`configuration.c:5746-5749`), i.e.
  `system` under the config tree on desktop Linux (`platform_unix.c:2137-2143` — the same block that seeds the saves
  default, `:2133-2134`). An unset key therefore _resolves_.
- **Blank, or the literal `default`** → the setting is cleared. `system_directory` passes `handle_setting = true`
  (`configuration.c:1691`), so the generic path loop writes whatever the merged config holds without a directory test
  (`:6532-6538`), `config_get_path` copies an empty value straight through (`config_file.c:1202-1216`), and `default` is
  emptied at `:6834-6835`. Where a blank `savefile_directory` keeps the standing root, a blank `system_directory` hands
  the core the content directory instead.
- **`LIBRETRO_SYSTEM_DIRECTORY`** in the environment overrides the config value outright (`configuration.c:6580-6584`).
  That is a property of the launching process, not of anything on disk, so no config read can see it.

**[V]** RetroArch's own "is the firmware there?" check follows the same flag but not the same fallback: it substitutes
the content directory only when `systemfiles_in_content_dir` is set **and** content is inited, and never for an empty
`system_directory` (`menu/menu_displaylist.c:854-878`). So with the flag on, a core looks for its BIOS next to the
content while the menu's check looks wherever content-inited-ness decides.

## 9. Does RetroDECK overwrite user settings?

**[V]** No, except on an explicit reset.

| Trigger                        | Effect on the sort keys                                                                             |
| ------------------------------ | --------------------------------------------------------------------------------------------------- |
| normal startup                 | none — `prepare_component` does not run                                                             |
| Configurator → reset RetroArch | **all lost** — `cp -fv rd_config/retroarch.cfg` replaces the whole file (`component_prepare.sh:23`) |
| folder moved (`postmove`)      | none — only the `*_directory` keys are rewritten                                                    |
| RetroDECK update               | none — no version block in `component_update.sh` touches them                                       |

**[V]** Confirmed empirically on a real installation: `sort_savestates_by_content_enable` is `false` there while the
shipped default is `true`. The drift survived at least one update.

This makes a deviation warning both possible and well-founded: the shipped configs are the reference, and the reference
is itself **readable live from the machine** (the Flatpak deployment) — no hardcoded values needed. Where an
installation kind has no readable reference (a native RetroArch without a distro default cfg), the comparison is
honestly omitted.

## 10. Launch commands

**[V]** Of 391 RetroArch launch commands in `es_systems.xml`, **none** uses `--save`, `--config` or `--appendconfig`.
For RetroDECK the configuration chain is therefore the whole truth.

**[V]** The mechanism exists and would defeat the chain: `--save` sets `RARCH_OVERRIDE_SETTING_SAVE_PATH`, and while
set, the configured directory is not applied (`runloop.c:8718`, `configuration.c:9062`). RetroDECK does not use it.

## 11. Save writing cadence

**[V]** `autosave_interval = "10"` in RetroDECK's shipped configuration: RetroArch flushes SRAM to disk every ten
seconds. The autosave thread skips writing when the buffer is unchanged (`save.c:109`, dirty-flag fast path with memcmp
fallback).

**[V]** A save file is nonetheless written on content close even without any in-game save (observed: Donkey Kong 64's
`.srm` re-written on exit after a session with no save). **[O]** Whether exit writes unconditionally or because the game
touched its save memory in the background is unestablished.

## 12. Mupen64Plus-Next — combined fixed-size `.srm`, confirmed

**[V]** Two games with different save chips, same core, produce byte-count-identical files:

```text
saves/n64/Donkey Kong 64 (USA).srm   296960 bytes   (EEPROM title, real progress)
saves/n64/Paper Mario (USA).srm      296960 bytes   (FlashRAM title, created WITHOUT any in-game save)
```

The hypothesis — one combined `.srm` of fixed size regardless of the game's actual save chip — is **confirmed** for this
core. No `.fla`, `.eep` or other per-chip files appear. 296960 decomposes as 32768 (SRAM) + 131072 (FlashRAM) + 2048
(EEPROM) + 4×32768 (Controller Pak); the region _order_ inside the file is only partially established, see below.

**[V]** The file is created on first launch, before the game ever saves. Byte comparison of the two files: they are
**identical except for the first 2048 bytes** (`0x000–0x800`). In the fresh, never-saved file that region is uniformly
`0xFF` (erased EEPROM); in the played file it carries structured data (44 distinct byte values). Recorded as a placement
fact: file existence does not imply an in-game save has occurred.

**[V]** `0x000–0x800` is the EEPROM region (DK64 is an EEPROM title and its data lives only there). **[O]** Where SRAM,
FlashRAM and the Controller Pak regions sit in the remainder is unestablished — a FlashRAM in-game save (Paper Mario
stage 2) would locate the FlashRAM region by diff.

**[O]** ParaLLEl N64 is a second libretro core for the same system. Whether it writes the same file is untested, and
worth establishing separately — with the same game, so only one variable changes.

## 13. EmuDeck — same rules, different inputs

**[V]** EmuDeck (upstream `dragoonDorise/EmuDeck`) sets **exactly the same four RetroArch keys**, with different values,
against the **standalone** `org.libretro.RetroArch` Flatpak. At `863ab69` the live writer is `RetroArch_setupSaves`
(`functions/EmuScripts/emuDeckRetroArch.sh:222-230`), which patches the global cfg the Flatpak reads
(`emuDeckRetroArch.sh:8`) after seeding it from a shipped 3303-line template
(`configs/org.libretro.RetroArch/config/retroarch/retroarch.cfg`, rsynced by `configEmuFP`); the template carries all
four sort flags `false` (`:3176-3179`):

| Key                                | RetroDECK         | EmuDeck                       |
| ---------------------------------- | ----------------- | ----------------------------- |
| `savefile_directory`               | `<rd_home>/saves` | `<savesPath>/retroarch/saves` |
| `savefiles_in_content_dir`         | `false`           | `false`                       |
| `sort_savefiles_by_content_enable` | **`true`**        | **`false`**                   |
| `sort_savefiles_enable`            | `false`           | `false`                       |

EmuDeck's libretro layout is therefore **flat**: `<savesPath>/retroarch/saves/<rom_stem>.srm`. The entire RetroArch
knowledge (directory math, override chain, `library_name`) transfers unchanged; only roots and values differ.

**[V]** **The cfg names a symlink.** Before writing the keys, `RetroArch_setupSaves` links
`<savesPath>/retroarch/{saves,states}` → `~/.var/app/org.libretro.RetroArch/config/retroarch/{saves,states}`
(`emuDeckRetroArch.sh:224-225`, `linkToSaveFolder` at `helperFunctions.sh:449`): the configured directory is the
`savesPath` spelling, the bytes live in the Flatpak tree — the placement's `dir`/`physical_dir` pair, on EmuDeck's own
stock layout.

**[V]** **Correction to the original citation** (which read `RetroArch_maincfg.sh:3048-3090` as a co-writer, at
`acc45fc`): at `863ab69` `RetroArch_maincfg()` has **no caller anywhere in the Linux shell tree**, and what it writes
goes through `RetroArch_setOverride` (`emuDeckRetroArch.sh:394-409`) into `config/retroarch/retroarch.cfg` under the
override directory — a file no launch path reads (the launcher `tools/launchers/retroarch.sh:4` passes no
`--appendconfig`, and no core is named "retroarch"). The values it would write are history, not configuration; the
code-real-vs-reachable rule applies.

**[V]** EmuDeck's own truth is a **shell settings file**, not JSON: `$emudeckFolder/settings.sh`, `key=value` lines
(`functions/helperFunctions.sh:4`, `emudeckFolder="$HOME/.config/EmuDeck"` at `vars.sh:2`). Defaults:
`$HOME/Emulation/{roms,tools,bios,saves,storage}` (`helperFunctions.sh:336-341`, re-confirmed at `863ab69`).

**[V→O]** **Reset behaviour downgraded to reachability-unverified.** The enforcement exists — `autofix_raSavesFolders()`
(`functions/autofix.sh:47`) forces all four sort flags back to `false` (`:72-75`) and even flattens already-sorted save
subdirectories (rsync-up-and-delete, `:55-70`) — but at `863ab69` **no Linux shell file calls any autofix function**.
The plausible dispatcher is the Electron app calling backend functions by name (`RunFunc.sh` is a
`source all.sh &&
"@a"` template), which no shell reading can confirm: a code path being real is not the same as it
being reachable, so "user drift is corrected on EmuDeck" is not established. RetroDECK leaves drift standing either way
(§9).

**[O]** `autofix.sh:73` remains broken at `863ab69` (key and value swapped: `"sort_savefiles_enable" "false = "`).
Recorded as an observation about the code, not about behaviour; untested.

**[V]** **The frontend is not guaranteed, but it is recorded.** RetroDECK always ships ES-DE; EmuDeck installs ES-DE
_or_ Pegasus _or_ Steam Rom Manager (`functions/ToolScripts/emuDeckESDE.sh`, `emuDeckPegasus.sh`, `emuDeckSRM.sh`) — and
`settings.sh` records the choice: `jsonToBashVars.sh:69-74` writes `doInstallESDE`, `doInstallPegasus`, `doInstallSRM`
(and more) as ordinary marker keys. The record is install-time state, not the disk; §13b's presence test is the disk.

**[V]** **Identity overlap:** EmuDeck configures the standalone RetroArch Flatpak, so "EmuDeck installed" and
"standalone RetroArch installed" are both true on the same machine — the same RetroArch under two descriptions.
Detection must check EmuDeck markers _before_ concluding "bare standalone", and a handle may truthfully carry both
descriptions.

**[V]** An Android tree exists with its own paths (`/storage/emulated/0/Emulation/…`,
`android/configs/.../retroarch.cfg`). Relevant for argosy later.

## 13b. EmuDeck's ES-DE — the catalogue side (item 21b)

All at `dragoonDorise/EmuDeck` @ `863ab69` and ES-DE `v3.4.1` unless marked.

**[V]** **Build and location.** EmuDeck installs upstream ES-DE — no fork — as the stable `LinuxSteamDeckAppImage`
package from ES-DE's own `latest_release.json` (`emuDeckESDE.sh:15,23-29,91-98`), saved as
`~/Applications/ES-DE.AppImage` (`vars.sh:4-6` `esdeFolder="$HOME/Applications"`, `emuDeckESDE.sh:9-10`; legacy
`EmulationStation-DE*` names become symlinks to it, `:44-55`). AppImage is the only Linux install mode
(`ESDE_toolType="AppImage"`, `:6`; no ES-DE Flatpak reference in the Linux tree). EmuDeck's own installed-test is the
AppImage stat (`ESDE_IsInstalled`, `:488-494`). The version floats with upstream stable — EmuDeck pins nothing, and the
backend itself is rolling (`emulatorInit` runs `git reset --hard && git pull` in `~/.config/EmuDeck/backend` on every
launch, `helperFunctions.sh:1046-1048`).

**[V]** **Config home.** Plain `~/ES-DE`: the launcher runs the AppImage with no `--home` and no `ESDE_APPDATA_DIR`
(`tools/launchers/es-de/es-de.sh:5`), and upstream resolution is `ESDE_APPDATA_DIR` env → else `<home>/ES-DE`
(`FileSystemUtil.cpp:259-285`). A `portable.txt` in the executable directory **may** relocate the home
(`main.cpp:149-192`): ES-DE validates the resolved target and keeps the default when it is missing or a regular file
(`:174-192`), so presence alone does not establish the relocation; EmuDeck writes none. Contrast RetroDECK:
`--home "${XDG_CONFIG_HOME}"` puts the appdata under the Flatpak config tree.

**[V]** **The bundled catalogue is sealed.** "If you're using the AppImage release of ES-DE then the bundled
es_systems.xml file is embedded in the AppImage together with the rest of the resources" (ES-DE `INSTALL.md`
v3.4.1:1470) — not on-disk readable, and the AppImage squashfs is zstd (outside a zero-dependency reader). The one
on-disk exception is ES-DE's per-file resource override (`INSTALL.md`:1125):
`~/ES-DE/resources/systems/linux/
es_systems.xml` shadows the embedded file for ES-DE itself. Reading the sealed layer
is tracked as issue #65.

**[V]** **The overlays EmuDeck writes.** `~/ES-DE/custom_systems/es_systems.xml` (`emuDeckESDE.sh:18`, deployed
`:127/:175/:217`, path-rewritten `sed s|/run/media/mmcblk0p1/Emulation|${emulationPath}|` at `:144-145`) — 8 real
systems at the pin (atarijaguar, atarijaguarcd, model2, switch, wiiu, xbox360, ps4, n3ds), mixing standalone launcher
commands and libretro ones, plus `xmlstarlet` command edits (`:255-398`). `~/ES-DE/custom_systems/es_find_rules.xml`
(`:20`) — deployed from the **chimeraOS** variant on every platform (`:125/:174/:519`); complements the bundled rules
(`INSTALL.md`:1208). Custom es_systems merge semantics: a same-name system **replaces** the bundled one entirely
(`INSTALL.md`:1466), which is what makes an overlay-declared system's answer complete even while the bundled layer is
sealed.

**[V]** **Settings and gamelists.** `~/ES-DE/settings/es_settings.xml` (`emuDeckESDE.sh:19`; upstream
`INSTALL.md`:1010-1012): `ROMDirectory` is written by EmuDeck with `${romsPath}` verbatim (`ESDE_setDefaultSettings`,
`:407-409`), `MediaDirectory` with `${ESDEscrapData}` (`:412-423`, default `$HOME/Emulation/tools/downloaded_media`,
`helperFunctions.sh:342`). Unset `ROMDirectory` falls back on `<home>/ROMs` (`FileData.cpp::getROMDirectory()`,
`:271-305`, the empty-setting branch at `:283-284`) — here against the user's real home, so `~/ROMs`. Gamelists:
`~/ES-DE/gamelists/<system>/gamelist.xml` (`INSTALL.md`:2145), where EmuDeck seeds per-system `<alternativeEmulator>`
defaults for 11 systems (`ESDE_setDefaultEmulators`, `emuDeckESDE.sh:427-441` — standalone-heavy: Dolphin, PPSSPP,
PCSX2, DuckStation, Ryujinx, ScummVM, Azahar standalone; melonDS DS; Beetle Lynx/Saturn), and symlinks the tree into the
saves folder for cloud sync (`ESDE_symlinkGamelists`, `:496-498` → `<savesPath>/es-de/gamelists`).

**[V]** **ES-DE stores no version on disk in settings** (no `ApplicationVersion` in `Settings.cpp` at v3.4.1); the
startup log `~/ES-DE/logs/es_log.txt` carries `ES-DE <version>` (`main.cpp:721,733`, `Log.cpp:28`). Reading it as a
version anchor is **deferred** (decided 2026-08-09) — every EmuDeck answer keeps `arrangement-unverified`
unconditionally until a live EmuDeck machine is observed.

**[O]** `all.sh:24-30` falls back to `$HOME/emudeck/settings.sh` when `~/.config/EmuDeck/settings.sh` is missing **or a
symlink** — whether any live Linux installation keeps its only real marker there is unestablished; atlas's marker stays
`~/.config/EmuDeck/settings.sh` (a live symlink resolves through `stat` either way; only a dead one reads as missing,
which is then the truthful state of a removed installation).

## 14. The machine is not just its config files

**[V]** RetroDECK's standalone save architecture is **symlinks** (`dir_prep`), observed live:

```text
config/ppsspp/PSP/SAVEDATA           -> saves/PSP/PPSSPP-SA
data/dolphin-emu/Wii                 -> saves/wii/dolphin
config/Ryujinx/bis/user/save         -> saves/switch/ryubing/user
bios/pcsx2/memcards                  -> saves/ps2/retroarch-core/LRPS2/memcards
config/mame/hiscore                  -> saves/mame-sa/hiscore
```

Consequences:

- **Two truthful answers.** "Where does PPSSPP save?" is emulator-side `config/ppsspp/PSP/SAVEDATA` and real
  `saves/PSP/PPSSPP-SA`. Different questions; a byte-only seam cannot even see the difference.
- **`exists` misleads on dead links.** `config/retroarch/cores` links to `/app/...` — valid inside the Flatpak sandbox,
  dead from the host. An `exists`-based scan reported **0 installed cores** on a machine carrying 211. A seam must be
  able to report "link present, target absent".
- **Fixture trees of `path → contents` cannot encode links at all** — the standalone family (118 entries) is untestable
  with a byte-only seam. This finding motivated extending the machine seam (`readlink`, `query_core`); the settled form
  is in `DESIGN.md`.

## 15. Emulator catalogue sources

Three live sources answer three different questions:

| Source                          | Question                                                    | Notes                                                               |
| ------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------- |
| frontend launch entries (ES-DE) | **choice** — which emulator would launch, in priority order | first entry = default; may be absent (§13)                          |
| core `.info` `systemid`         | **capability** — which cores can run a platform             | 249 of 295 `.info` files carry `systemid`; never a path source (§5) |
| RetroArch playlists (`*.lpl`)   | **fact** — which core actually launched a ROM               | empty on the observed machine (everything launches via ES-DE)       |

**[V]** The user's saved per-system emulator choice is **not** in `es_settings.xml` (only `AlternativeEmulatorPerGame`
lives there). It is stored in the system's `gamelists/<system>/gamelist.xml` as an
`<alternativeEmulator><label>…</label></alternativeEmulator>` element whose label matches the command's `label`
attribute in `es_systems.xml`. The element occurs in **two locations**, and both are live:

- **Beside `<gameList>`** — ES-DE writes it there with `doc.prepend_child` (`es-app/src/GamelistFileParser.cpp:420` when
  updating an existing gamelist, `:444` when creating one), which makes the file two root elements after the
  declaration, i.e. not well-formed XML. Observed live after switching an emulator on a real installation.
- **Inside `<gameList>`** — the standards-compliant location. ES-DE reads it in either place, document level first
  (`GamelistFileParser.cpp:190-192`, upstream `9207fc77`, 2026-07-29, "Added forward compatibility for reading the
  alternativeEmulator element from the gameList root element"); the commit's own comment states the plan to write it
  there in a future release. A gamelist on the reference installation carries this shape today.

**[V]** RetroDECK's launcher reads the same element itself for launches outside the ES-DE UI — `libexec/run_game.sh`
lines 125-135, an `awk` range over `<alternativeEmulator>`…`</alternativeEmulator>` — so it finds either location,
independent of the ES-DE build. The ES-DE build that 0.10.9b ships (release `retrodeck-main-20260414-105925`) predates
`9207fc77` and therefore only reads the element beside `<gameList>`: on that build a nested element steers a launcher
run but not an ES-DE-UI run.

A parser must therefore accept both locations, in ES-DE's order; a label matching no declared entry falls back to
declared order, as ES-DE itself does. Deeper nesting is not a location either reader is documented to write — a `<game>`
carries its own choice as `<altemulator>` — so the lookup stays depth-bounded.

When no catalogue exists (bare RetroArch, EmuDeck without ES-DE), the caller names the core; a default cannot be read
and must not be invented.

### Where a system's ROMs live — `%ROMPATH%` and the home behind it

**[V]** The same `<system>` element carries `<path>` and `<extension>`. `<path>` is written with ES-DE's own token
(`%ROMPATH%/n64`), and `SystemData::loadConfig()` substitutes it —
`path = Utils::String::replace(path, "%ROMPATH%", rompath)` followed by an **unconditional**
`path = Utils::String::replace(path, "//", "/")` (`es-app/src/SystemData.cpp` ~L859-861, ES-DE 3.4.1; `rompath` is
`FileData::getROMDirectory()`, bound at ~L781). Line numbers read from the tagged upstream source over the web, not from
a local checkout — the shipped component is a binary. Two details a reimplementation gets wrong by default:

- `Utils::String::replace` **loops** until the pattern is gone (`es-core/src/utils/StringUtil.cpp`), so `a///b` reaches
  `a/b`. Python's single-pass `str.replace` leaves `a//b`.
- The collapse runs on paths that carried no token too, because it is not inside the substitution branch.

**[V]** `FileData::getROMDirectory()` (`es-app/src/FileData.cpp:271-305`, ES-DE 3.4.1; the empty-setting branch at
`:283-284`, the separator append at `:291-297`) returns `<home>/ROMs/` when the `ROMDirectory` setting is empty, and
otherwise the configured value with **one** trailing separator appended where it is missing. So the directory ES-DE
substitutes always ends in a separator, which is what the `//` collapse then absorbs — a configured `…/roms/` must not
spell the answer `…/roms//n64`.

**[V]** The non-empty branch expands `~` before the separator append:
`romDirPath =
Utils::FileSystem::expandHomePath(romDirPath)` (`FileData.cpp:289`). `expandHomePath`
(`es-core/src/utils/FileSystemUtil.cpp:663-675`; the `systemHome` parameter defaults `false`, `FileSystemUtil.h:55`, and
the call site passes one argument) is `Utils::String::replace(path, "~", getHomePath())` — plain text substitution of
**every** `~`, not a shell's tilde grammar: a bare `~` becomes the home, `~user` looks no user up and becomes the home
with `user` glued on, a mid-path `~` is replaced the same. One pass in effect, unlike the `//` collapse: after a pass no
`~` from the input remains, and a home itself carrying one hits the endless-loop break (`StringUtil.cpp:293-294`) — so
Python's single-pass `str.replace` mirrors it exactly. The home substituted is `getHomePath()`
(`FileSystemUtil.cpp:183-229`): the `--home` a launcher passed — RetroDECK's `${XDG_CONFIG_HOME}` — else `$HOME`, else,
with both absent, the process's current working directory (`FileSystemUtil.cpp:224-226`). The third case is unreachable
for atlas: RetroDECK's launcher always passes `--home`, and the EmuDeck handle expands against the home the caller
established for the machine — atlas never models an ES-DE process without one, so no answer rests on the CWD fallback.
So a `~`-carrying `ROMDirectory` is not an unresolvable value: it resolves against the same per-arrangement home the
empty-setting default derives from. On RetroDECK that home cannot be moved at all — the `--home` is the pinned
`XDG_CONFIG_HOME` (the env-composition subsection below) — while on EmuDeck a `portable.txt` may move it, and stops
default and expansion alike there. What stays unresolvable is what ES-DE resolves against bases atlas has not
established: relative values (the process's working directory) and `%ESPATH%` (the binary directory,
`FileData.cpp:300-302`).

**[V]** The token is _required_ only in `createSystemDirectories()` (~L1214), whose guard at ~L1366 skips any system
whose `<path>` does not start with it, with a warning. That is the placeholder-generation path. `loadConfig()` has no
such guard, so a `<path>` without the token is a literal directory ES-DE loads normally.

**[V]** The setting is `ROMDirectory` in `es_settings.xml`, **not** `roms_path` in `retrodeck.json`, and the two are
wired one way only. RetroDECK writes its `roms_path` into ES-DE's setting, from three call sites that differ only in
which variable holds the target file:

```bash
components/es-de/component_prepare.sh:17  set_setting_value "$es_de_config" "ROMDirectory" "$roms_path" "es_settings"
components/es-de/component_prepare.sh:35  set_setting_value "$es_de_config" "ROMDirectory" "$roms_path" "es_settings"
components/es-de/component_update.sh:19   set_setting_value "$es_settings"  "ROMDirectory" "$roms_path" "es_settings"
```

The `es_settings` branch of that setter is a `sed -i` over `name" value="…"` — `libexec/framework.sh:130-132`, which is
rooted at the Flatpak's `files/` directory (`…/current/active/files/libexec/`), a **sibling** of the `files/retrodeck/`
tree every other path in this document hangs off, not a child of it. Nothing anywhere in the deployment reads
`ROMDirectory` back out (unfiltered grep of the whole `files/` tree, 0.10.9b, 2026-08-08). A user who edits either file
alone has moved one and not the other, and only `ROMDirectory` is what the frontend substitutes.

**[V]** The shipped template holds the setting **empty**: `<string name="ROMDirectory" value="" />`
(`components/es-de/rd_config/es_settings.xml:158`). The empty-setting branch is therefore the state a fresh installation
is in before the first `component_prepare.sh` run, not a theoretical one.

**[V]** The home that branch falls back on is not the user's. RetroDECK's only path to the frontend is
`components/es-de/component_launcher.sh:10`:

```bash
exec "$component_path/bin/es-de" --home "${XDG_CONFIG_HOME}" "$@"
```

An explicit `--home` outranks both `portable.txt` and `$HOME`, and under Flatpak `XDG_CONFIG_HOME` is the per-app config
directory — the same tree `es_settings.xml` was read out of. So one path answers both questions, and the empty-setting
default is a reading rather than an assumption.

**[D]** RetroDECK ships the `RetroDECK/ES-DE` fork; `getROMDirectory` is taken as unmodified at the pinned build. The
component is a binary here, so this is not verified against fork source.

**[V]** Nothing can move that tree — the finding that retired atlas's relocation guard. Four Flatpak overrides files can
redefine environment variables for the app; each was observed under `strace` (flatpak 1.16.6, reference machine
2026-08-08); one `flatpak override --show` invocation opens exactly **one** file, so the set below is four separate
runs, one per flag combination — reproduce it that way, not with a single command:

| invocation                                | file opened                                 | scope                          |
| ----------------------------------------- | ------------------------------------------- | ------------------------------ |
| `flatpak override --show --user <app id>` | `~/.local/share/flatpak/overrides/<app id>` | user installation, this app    |
| `flatpak override --show <app id>`        | `/var/lib/flatpak/overrides/<app id>`       | system installation, this app  |
| `flatpak override --show --user`          | `~/.local/share/flatpak/overrides/global`   | user installation, every app   |
| `flatpak override --show`                 | `/var/lib/flatpak/overrides/global`         | system installation, every app |

None of the four exists on the reference machine (every run returned `ENOENT`). What they can and cannot change is the
next subsection's finding: an `XDG_CONFIG_HOME` entry in any of them is **inert** — flatpak force-pins the XDG variables
after applying every override — so the per-app config tree the launcher's `--home` names is the tree in force on every
machine, override files or no.

### Flatpak environment composition — what an override can and cannot move

All flatpak facts below are read at tag `1.16.6` (commit `e761a8885453c217a931281092a641ebbdd0a0c6`) — the flatpak the
reference machine runs — and man-page citations are that tag's own `doc/` sources.

**[V]** **Which files apply, and in which order.** Flatpak loads the system installation's overrides only for an app the
system installation deploys, and the user installation's always (`flatpak_dir_load_deployed`,
`flatpak-dir.c:3053-3083`); when both installations deploy the app, the user one runs — the deploy search puts the user
dir first and the first hit wins (`flatpak_find_deploy_for_ref`, `flatpak-dir-utils.c:294-317`, `:278-285`). The
applicable files merge in one order: system-global → system-app → user-global → user-app
(`flatpak_deploy_get_overrides`, `flatpak-dir.c:1518-1567`), and the environment merge is a plain per-key hash insert
(`flatpak-context.c:1077-1079`) — the last applicable file that names a key wins, a later set overwriting an earlier
unset and vice versa.

**[V]** **Values are literal GKeyFile strings.** `[Environment]` entries are read with `g_key_file_get_string`
(`flatpak-context.c:1944`) and applied verbatim — **no `$VAR` is ever expanded**, by flatpak or by anything before the
app. GKeyFile semantics apply: leading whitespace of a value is skipped and trailing kept (GLib 2.84.4,
`g_key_file_parse_key_value_pair`), the escapes `\s` `\n` `\t` `\r` `\\` decode, and a value whose escape does not
decode comes back `NULL` — which flatpak then applies as an **unset** (`flatpak-context.c:1944-1946` reads with a `NULL`
`GError`; a `NULL` value unsets, `flatpak-run.c:752-755`). `unset-environment` in `[Context]` beats `[Environment]`
within the same file, deliberately (`flatpak-context.c:1950-1972`). A file GKeyFile cannot load at all fails the deploy
load and `flatpak run` with it (`flatpak-dir.c:2917-2940` propagates everything but `ENOENT`) — the app never launches,
so that state is out of every resolver question's reach.

**[V]** **The XDG pin.** The sandbox environment is assembled in this order inside `flatpak_run_app`: the host
environment (`flatpak_bwrap_new(NULL)` → `g_get_environ()`, `flatpak-run.c:3055`), the static defaults
(`flatpak_run_apply_env_default`, `:3351`; the `default_exports` table at `:542` touches `PATH`, `XDG_CONFIG_DIRS`, the
`LD_*`/`TMP*` unsets — not `HOME`), the merged context environment — metadata `[Environment]`, all override files,
`--env` — (`flatpak_run_apply_env_vars`, `:3352`), and then, **after all of that and with overwrite**, the per-app XDG
variables: `flatpak-run.c:3574` → `:505` → `flatpak_context_apply_env_appid` (`flatpak-context.c:3286`, `:3158-3187`),
which forces `XDG_DATA_HOME`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME` and `XDG_STATE_HOME` to the **host-spelled** per-app
paths (`flatpak_get_data_dir`, `flatpak-context.c:3074-3081`; `flatpak_bwrap_set_env` overwrites,
`flatpak-bwrap.c:87-93`). The block runs on every normal app run — `app_id_dir` is unset only for
`flatpak-spawn --sandbox` (`flatpak-run.c:3290-3291`). So an override that sets **or unsets** any `XDG_*_HOME` never
reaches the app. Documented, intended behavior, not an accident of ordering: flatpak-run(1) — "Flatpak also overrides
the XDG environment variables to point sandboxed applications at their writable filesystem locations below
`~/.var/app/$APPID/`" (`doc/flatpak-run.xml:138-147`) — and the request to make them overridable,
[flatpak/flatpak#4529](https://github.com/flatpak/flatpak/issues/4529), is closed as not planned.
[#2413](https://github.com/flatpak/flatpak/issues/2413) shows the same behavior reported in 2018.

**[V]** **`HOME` is the key that does reach the app.** It enters the sandbox environment from the host
(`flatpak-run.c:3055`), the static defaults leave it alone, an `[Environment]` override lands on top of it at `:3352` —
and nothing reapplies it afterwards. So a `HOME` override (or unset) is effective in the sandbox.

**[V]** **What an effective `HOME` decides among atlas's reads: the cfg `~` base, nothing else.** Every file the
RetroDECK handle reads is keyed off the pinned `XDG_CONFIG_HOME` by RetroDECK's own scripts: the marker
(`rd_conf="$XDG_CONFIG_HOME/retrodeck/retrodeck.json"`, `libexec/all_vars.sh:4`), RetroArch's cfg
(`retroarch_config="$XDG_CONFIG_HOME/retroarch/retroarch.cfg"`, `components/retroarch/component_functions.sh:3`), and
ES-DE's whole tree via the launcher's `--home "${XDG_CONFIG_HOME}"` (`components/es-de/component_launcher.sh:10`). What
resolves against `HOME` is RetroArch's own `~` substitution in cfg values: `fill_pathname_expand_special`
(`file_path.c:1066-1101`, RetroArch `a79435a`) calls `fill_pathname_home_dir` (`:1457-1468`), which reads
`getenv("HOME")`. With `HOME` unset — or set to the empty string — the buffer stays empty, the substitution block is
skipped (`:1081`), and the value passes through **verbatim** (`:1100`): a `~/`-value becomes a relative path RetroArch's
own `path_is_directory` test then refuses (`configuration.c:6914-6960`). A home ending in a slash is not doubled
(`:1088-1094`).

**[D]** **Version range.** The pin is source-verified at 1.16.6 (this machine's flatpak) and documented in
flatpak-run(1); #2413 dates the behavior to 2018. It is taken as the behavior of every flatpak a supported RetroDECK
runs under — world knowledge, versioned and cited per the ground rule, since no file on a machine states its flatpak's
env assembly.

**Consequence for atlas.** The relocation guard — a refusal of the home-derived ROM resolutions plus a
`config-home-relocated` rider on every answer whenever any override file named `XDG_CONFIG_HOME` or `HOME` — stated
doubt the pinned source refutes, and retired. The override files remain read by exactly the queries they can still
affect: the cfg-reading ones (save, savestate, firmware), which compose the applicable files per the merge order above
and take the effective `HOME` as the `~`-expansion base — followed when it is a literal absolute path, and otherwise
handed to the ordinary value-shape machinery (RetroArch's directory test refuses a non-absolute expansion; the sandbox
translation states a `/var/...` one). `config-home-relocated` lives on solely as EmuDeck's `portable.txt` statement
(§13b), an on-disk switch that really may move ES-DE's tree.

**Consequence for every ROM-directory answer, not just the ROM question.** The dependency being one-way settles which
source each of them reads: `roms_path` is RetroDECK's _input_ to the sed and nothing reads it back, so from atlas's view
it is bookkeeping about a tree, never a statement of where the frontend looks. Two answers used to take it anyway and
now do not:

- The **per-game anchor**. ES-DE resolves a gamelist's `./Game.m3u` against the `startPath` it built in `loadConfig()`,
  which is the system's `<path>` with `ROMDirectory` substituted. Anchoring on `roms_path` instead matches overrides
  against a directory the frontend does not launch from — on a machine whose two paths have drifted apart, the override
  is handed to a file ES-DE never launches, or missed on the one it does.
- **`roms_dir()`**, which reported `roms_path` and so became the less authoritative of two public answers about one
  tree.

Both resolve through the one chain now.

**[V]** A corollary worth stating because it bit the fixtures: a machine carrying a ROM tree and **no** `ROMDirectory`
is not a machine RetroDECK produces. The setting is written in two of `component_prepare.sh`'s three guarded blocks —
`reset` (`:17`) and `postmove` (`:35`), not `startup` — and the `reset` block goes on to **generate the ROM tree
itself** ten lines later, `start_esde --create-system-dirs` (`:27`). The tree and the setting are born together, in that
order, from the same block. So a fixture with system directories and no `ROMDirectory` models an installation that
cannot exist, and the honest reading of an empty setting stays the frontend's own `<home>/ROMs` default.

## 16. What this implies for the design

Recorded as consequences of the findings; the decisions themselves are settled in `DESIGN.md`:

1. The `<core>` hole must be filled with `library_name`, resolved **live** from the core binary (`query_core` — the same
   read RetroArch performs). A shipped table would itself be the static list the README warns about; at most a cache
   invalidated by `.so` mtime/size.
2. A resolver must read the four-layer override chain, or it is wrong on a stock installation (§6).
3. Directory and file set are separate concerns with separate completeness. "Directory only" is a natural seam, not a
   compromise — and the file set is observable for existing saves (§7).
4. Not every core is rooted at `savefile_directory`; some are rooted at `system_directory` (§8). A placement model that
   assumes one root cannot express Flycast.
5. "Never guess" needs a way to say _"the directory is known, the filename is not"_ — distinct from a hole the caller
   fills. Today `needs` conflates the two.
6. Placement can depend on filesystem state, not only configuration (§4, silent revert).
7. The machine seam must express symlinks and core answers, or the standalone family is untestable and `library_name`
   unreachable (§14, §5).
8. Detection needs marker ordering (EmuDeck before bare standalone, §13) and a health state (stale roots, unmounted SD,
   §2).
9. The default-deviation reference is itself readable live where it exists; where it does not, the comparison is
   omitted, never faked (§9).

## 17. Reproduction

- The probe scripts used during this research live in the session scratchpad, not in the repository. The `library_name`
  extraction (210 cores) is a research artifact validating the `query_core` approach — the _procedure_ ships (as the
  seam), the extracted table does not.
- A filesystem baseline (`saves/`, `states/`, `bios/`; size and mtime per file) taken before an experiment and diffed
  after is sufficient to attribute newly written files to a run.
- Core probing requires crash isolation (fork per core) and tolerates missing host libraries as "unknown", never as a
  guess.

## 18. Savestates — the same math, a different quartet

Collected for the savestate placement question. Same pinned revisions as the rest of this document.

**[V]** Four keys govern savestates, mirroring the savefile four: `savestate_directory`, `savestates_in_content_dir`,
`sort_savestates_by_content_enable`, `sort_savestates_enable` (`runloop.c:8765-8768`).

**[V]** **The directory math is one function, not two.** `runloop_path_set_redirect` (`runloop.c:8752-8979`) resolves
both families side by side in a single pass, and the savestate half is the savefile half line for line:

```text
savestates_in_content_dir, or savestate_directory empty -> the ROM's own directory  (runloop.c:8798-8808)
otherwise                                               -> <savestate_directory>
                                                           + <content_dir>    if sort_savestates_by_content_enable  (:8861-8866)
                                                           + <library_name>   if sort_savestates_enable             (:8869-8874)
```

**[V]** Order is content directory first, then core — the same order and the same `content_dir_name` string, which is
computed once for whichever family asked for it (`runloop.c:8779-8783`).

**[V]** The sorted directory is not guaranteed here either: if it does not exist and cannot be created, RetroArch
silently reverts to the unsorted root (`runloop.c:8878-8887`), the twin of `:8844`. The directory is created at
**content load**, not at save time — which is why a machine that has never written a state can still carry the whole
sorted tree (observed, see below).

**[V]** The platform default is `states` under the RetroArch config tree (`platform_unix.c:2135-2136`), seeded before
any config is read (`configuration.c:5740-5741`) exactly as the `saves` default is. Unlike the system directory, no
environment variable overrides it, so the seeded value is what an absent key resolves to.

**[V]** Upstream compile-time defaults are identical to the savefile quartet's: `DEFAULT_SORT_SAVESTATES_ENABLE` true
(`config.def.h:983`), `DEFAULT_SORT_SAVESTATES_BY_CONTENT_ENABLE` false (`:985`), `DEFAULT_SAVESTATES_IN_CONTENT_DIR`
false (`:988`). A bare install therefore sorts states by core.

### Blank versus absent — no asymmetry, and the row had to be read to know it

**[V]** `savestate_directory` passes `handle_setting = false` in the settings table (`configuration.c:1710`), the same
as `savefile_directory` (`:1709`) and unlike `rgui_config_directory` (`:1736`). So the generic path loop skips it
(`:6534-6535`) and the only thing that sets it is the validated block at `:6935-6960` — a line-for-line twin of the
savefile block at `:6914-6933`: the literal `default` resets to the platform default, an existing directory is applied,
anything else warns and sets nothing. **Blank keeps the standing root for savestates too.**

**[V]** One divergence inside that block, and it is the guard rather than the logic: the savestate branch tests
`RARCH_OVERRIDE_SETTING_STATE_PATH` (the `--savestate`/`-S` flag) where the savefile branch tests `..._SAVE_PATH`.
**[V]** Neither flag appears anywhere in the shipped `es_systems.xml` (RetroDECK 0.10.9b) — 893 `<command>` occurrences
in the file text, being the 509 live elements §1 counts plus 384 inside commented-out blocks. So on this arrangement the
config chain is the whole truth for states exactly as §10 established for saves.

**[V]** `config_save_overrides` treats the two directories specially (`configuration.c:9053-9089`, `:9195-9218`) because
they alias one runtime buffer. That is the menu's _write_ path and has no effect on resolution.

### What a savestate is called

**[V]** The base name is the content's stem with `.state` appended — `FILE_PATH_STATE_EXTENSION`
(`file_path_special.h:44`), applied by `fill_pathname_dir` at `runloop.c:8942-8949`, so the same "stem cut once, last
component appended unchanged" math §4 describes for `.srm`.

**[V]** Slots come off that base in `runloop_get_savestate_path` (`runloop.c:8185-8207`): slot 0 is `<stem>.state`, slot
N above zero is `<stem>.stateN`, and a negative slot is `<stem>.state.auto`.

**[V]** With `savestate_thumbnail_enable`, RetroArch writes a `.png` beside each state — `<state path>.png`
(`gfx_thumbnail.c:3409-3425` builds it; `task_save.c:1226-1230` → `task_screenshot.c:476-485` writes it). The reference
machine has the setting off.

**[V]** Input-movie replays share the directory but are not savestates: `name.replay` is set from the same resolved
directory (`runloop.c:8923`, `:8950`).

### Why no core can deviate — and what a core still gets to say

**[V]** The libretro API exposes **no savestate directory to a core**. The environment callbacks that hand out
directories are `GET_SYSTEM_DIRECTORY` (9), `GET_CONTENT_DIRECTORY` / `GET_CORE_ASSETS_DIRECTORY` (30),
`GET_SAVE_DIRECTORY` (31), `GET_PLAYLIST_DIRECTORY` (79) and `GET_FILE_BROWSER_START_DIRECTORY` (80); there is none for
states. `GET_SAVESTATE_CONTEXT` (72 | EXPERIMENTAL) tells a core _why_ it is being serialized, not where the result
goes. RetroArch serializes the state and writes the file itself.

**Consequence for the design.** The savefile question needs per-core rule cards because a core writes its own save data
and can root it anywhere (§8). No such card can exist for savestates, and none ever will while the API stays as it is.
That is why the savestate answer carries no `granularity`: the field is a card's word about how a core groups what it
writes, and its domain here is empty rather than merely unestablished.

**[V]** What a core does declare is whether it can be serialized at all. `savestate = "false"` in a `.info` sets
`CORE_INFO_SAVESTATE_DISABLED` (`core_info.c:1841-1860`), and RetroArch checks that level before offering a state
(`core_info.c:2899-2937`). Of the 292 `.info` files a stock RetroDECK 0.10.9b ships, **68 declare
`savestate = "false"`** (159 true, 1 the non-boolean `"serialized"`); `savestate_features` reads `deterministic` 55,
`serialized` 78, `basic` 24, and the unrecognized literal `null` 33.

**[V]** The declaration does not bind absolutely, and both escapes are in that same function:
`core_info_savestate_bypass` waves the check through (`:2904-2905`), and at the BASIC level a running core reporting a
nonzero `retro_serialize_size()` overrides stale metadata (`:2926-2929`). The second is a fact about a running core, so
no config read can see it.

**[V]** Two parse details follow from `config_get_bool`'s vocabulary: `savestate = "serialized"` is not a boolean, so
the block never runs and the DETERMINISTIC default stands; and `savestate_features` recognizes only the literals `basic`
and `serialized`, so `null` leaves the default standing too.

### RetroDECK's shipped defaults, and the drift on the reference machine

**[V]** `components/retroarch/rd_config/retroarch.cfg` ships `savestate_directory = "RETRODECKHOMEDIR/states"`,
`savestates_in_content_dir = "false"`, `sort_savestates_by_content_enable = "true"`, `sort_savestates_enable = "false"`.
`component_prepare.sh:28` (and `:209`) writes `states_path` into the first of them; `retrodeck.json` carries
`paths.states_path` beside `saves_path`.

**[V-live]** The reference installation reads `savestate_directory = "/run/media/deck/Emulation/retrodeck/states"` with
**`sort_savestates_by_content_enable = "false"`** — the user drift §9 already recorded, still standing. So the expected
layout there is flat: `states/<rom_stem>.state`.

**[V-live]** The drift left a footprint that confirms the whole rule. `states/` carries content-sorted directories from
the period when the flag was true — system-named directories alongside several per-game directories, including
multi-disc `.m3u` spellings — and **not one `.state` file anywhere under the RetroDECK home**. Empty sorted directories
with no states in them are exactly what `path_mkdir` at content load produces (`runloop.c:8878-8879`); a save-time
creation could not have made them. The remaining entries under `states/` (`dolphin`, `mame-sa`, `primehack`,
`ps2/pcsx2`, `ps3/rpcs3`, `PSP/PPSSPP-SA`, `psx/duckstation`, `nds/melonds`, `xroar/*`) are RetroDECK's standalone
`dir_prep` homes, not RetroArch's.

**[V]** The only override file on the machine, `config/PPSSPP/PPSSPP.cfg`, sets `sort_savefiles_by_content_enable` alone
— it does **not** touch the savestate sort. So on a stock installation PPSSPP's states stay content-sorted while its
saves go flat: the shipped override splits the two families apart.

### EmuDeck

**[V]** EmuDeck sets all four state keys. At `863ab69` **both** directories derive from `savesPath`:
`RetroArch_setupSaves` writes `savestate_directory = "$savesPath/retroarch/states"` next to the savefile key
(`emuDeckRetroArch.sh:227-228`), each the `savesPath` spelling of a symlink into the Flatpak's own config tree
(`:224-225`); the shipped cfg template seeds both `in_content_dir` keys and all four sort flags `false` (template
`:3134-3179`). A **prior generation** (`acc45fc`) recorded an asymmetry — `savestate_directory` hardcoded to
`~/.var/app/org.libretro.RetroArch/config/retroarch/states` via `RetroArch_maincfg.sh:3053`, `:3057`, `:3091-3092` —
whose writer at `863ab69` has no Linux caller and targets a file no launch path reads (§13's correction). The
`autofix.sh:74-75` state-flag resets are written correctly, unlike the savefile line at `:73`, but carry §13's
reachability downgrade all the same.
