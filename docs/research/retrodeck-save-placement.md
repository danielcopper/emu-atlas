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
**not** in the RetroDECK Git repository; EmuDeck to upstream `dragoonDorise/EmuDeck` at clone time (2026-07-23).

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

**[O]** The exact filename scheme of the per-game VMU files is not in the binary's strings and remains unestablished.

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
against the **standalone** `org.libretro.RetroArch` Flatpak (`functions/EmuScripts/RetroArch_maincfg.sh:3048-3090`,
`functions/EmuScripts/emuDeckRetroArch.sh:228`):

| Key                                | RetroDECK         | EmuDeck                       |
| ---------------------------------- | ----------------- | ----------------------------- |
| `savefile_directory`               | `<rd_home>/saves` | `<savesPath>/retroarch/saves` |
| `savefiles_in_content_dir`         | `false`           | `false`                       |
| `sort_savefiles_by_content_enable` | **`true`**        | **`false`**                   |
| `sort_savefiles_enable`            | `false`           | `false`                       |

EmuDeck's libretro layout is therefore **flat**: `<savesPath>/retroarch/saves/<rom_stem>.srm`. The entire RetroArch
knowledge (directory math, override chain, `library_name`) transfers unchanged; only roots and values differ.

**[V]** EmuDeck's own truth is a **shell settings file**, not JSON: `$emudeckFolder/settings.sh`, `key=value` lines
(`functions/helperFunctions.sh:4`). Defaults: `$HOME/Emulation/{roms,tools,bios,saves,storage}`
(`helperFunctions.sh:337-341`).

**[V]** **Reset behaviour differs.** EmuDeck's `functions/autofix.sh:72` forces `sort_savefiles_by_content_enable` back
to `false` — user drift is corrected on EmuDeck, while RetroDECK leaves it standing (§9). A deviation warning means
different things on the two.

**[O]** The adjacent line `autofix.sh:73` looks broken in upstream source (key and value appear swapped:
`"sort_savefiles_enable" "false = "`). Recorded as an observation about the code, not about behaviour; untested.

**[V]** **The frontend is not guaranteed.** RetroDECK always ships ES-DE; EmuDeck installs ES-DE _or_ Pegasus _or_ Steam
Rom Manager (`functions/ToolScripts/emuDeckESDE.sh`, `emuDeckPegasus.sh`, `emuDeckSRM.sh`). An emulator catalogue may be
absent entirely.

**[V]** **Identity overlap:** EmuDeck configures the standalone RetroArch Flatpak, so "EmuDeck installed" and
"standalone RetroArch installed" are both true on the same machine — the same RetroArch under two descriptions.
Detection must check EmuDeck markers _before_ concluding "bare standalone", and a handle may truthfully carry both
descriptions.

**[V]** An Android tree exists with its own paths (`/storage/emulated/0/Emulation/…`,
`android/configs/.../retroarch.cfg`). Relevant for argosy later.

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
lives there). It is stored in the system's `gamelists/<system>/gamelist.xml` as a top-level
`<alternativeEmulator><label>…</label></alternativeEmulator>` element whose label matches the command's `label`
attribute in `es_systems.xml` — and ES-DE writes that file with **two root elements** (the declaration, then
`<alternativeEmulator>`, then `<gameList>`), i.e. not well-formed XML. Observed live after switching n64 and psx on a
real installation. A parser must tolerate the quirk; a label matching no declared entry falls back to declared order, as
ES-DE itself does.

When no catalogue exists (bare RetroArch, EmuDeck without ES-DE), the caller names the core; a default cannot be read
and must not be invented.

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
