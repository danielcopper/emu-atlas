# libretro core audit — save behaviour, core by core

The systematic pass behind "atlas answers libretro completely": every core RetroDECK's matrix references gets a verdict,
sourced from an **unfiltered** options scan of the shipped binary, upstream source where behaviour needs proof, and live
observation where available. Filtered/truncated string scans are banned — one produced a wrong LRPS2 card (caught by
review; the option had been grepped away).

Verdicts:

- **standard** — roots at the RetroArch save directory under the standard rule; no card needed for placement. File-set
  nuances, if any, are noted.
- **standard-dir** — the directory follows the rule while the file set is the core's and unstated: the core writes its
  own files instead of RetroArch's `.srm`, and atlas declares none of them. The verdict is still a value the loader
  accepts (`atlas/oddities.py:52`); no shipped entry carries it today — the four entries that have carried it are all
  cards now (`mednafen_ngp`, `mednafen_saturn`, `pokemini`, `scummvm`), see below.
- **card** — deviates; covered by a rule card in `atlas/data/core_oddities.json`.
- **multi-option** — directory is standard, but the file set / granularity depends on several interacting options; the
  single-option card schema cannot express it. Candidate for the code-rule-plus-card route (see the schema's own spec).
  Placement answers are correct today; granularity is honestly unstated.
- **suspect** — evidence of deviation exists but is unproven; needs a live run.
- **unaudited** — not yet examined.

| core (`.so` short name) | library_name     | verdict          | evidence                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ----------------------- | ---------------- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| flycast                 | Flycast          | **card**         | flycast@1dac369: `libretro.cpp:790-807` maps the live option to `per_content_vmus`; `oslib.cpp:38-67` builds the VMU path, called from `maple_devs.cpp:409-428`. Both placements live-verified: shared `bios/dc` VMUs, and `All VMUs` writing `<save_id>.<port>.bin` into the redirected save directory (`libretro.cpp:2142-2148`), the id being the disc's product number (`emulator.cpp:838-841`). Content without an id is named after the ROM ([V-source] `oslib.cpp:62`) — both spellings are stated, the condition with them. `VMU A1` is [D] and covers port A1 alone, so it states no file set. [O] which ports a mode covers is content-dependent (Naomi: B1/C1, `maple_cfg.cpp:246-253`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| pcsx2                   | LRPS2            | **card**         | libretro/ps2 source: `pcsx2_shared_memory_cards` puts the cards either under `system_directory/pcsx2/memcards`, shared by every game, or into the save dir as `<rom_stem>.ps2` (`main.cpp:2154`, `Pcsx2Config.cpp:997`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| mednafen_psx            | Beetle PSX       | **card**         | carded in #175. The card carries the whole option set as its `governing_rule` — `beetle_psx_use_mednafen_memcard0_method`, `beetle_psx_enable_memcard1`, `beetle_psx_shared_memory_cards` and the two card-image index options — and states seven modes over it, each naming its files flat in the save directory: `<rom_stem>.srm` where slot 0 reaches RetroArch through the libretro SRAM interface, `<rom_stem>.0.mcr` / `<rom_stem>.1.mcr` where the core writes the cards itself, and `mednafen_psx_libretro_shared.0.mcr` / `.1.mcr` where sharing swaps the content stem. Its provenance reads beetle-psx-libretro at d6383bf, the revision the shipped binary names in its `library_version`: `MDFN_MakeFName` puts every card in the save directory (`libretro.cpp:5240-5249`), the frontend writes the `.srm` under the default slot-0 method (`:5129-5145`), the core writes `<stem>.<idx>.mcr` otherwise (`:2145-2163`), slot 1 is enabled by default and appears once a game dirties it (`:2459-2485`), and sharing never touches the frontend's `.srm` (`:5240-5247`). The digit is the index option's value (issue #80, `libretro.cpp:2159-2164`), so the recorded names hold for the registered defaults and the rule steps aside for any other selection. Four citations in this cell are the earlier row's own reading rather than the card's, kept because nothing contradicts them: `mednafen/psx/frontio.cpp:991-1011` for slot 1 appearing only once a game dirties it, and the three option definitions at `libretro_core_options.h:642-655`, `:656-669`, `:670-683`. All seven modes are [D]; slots 2-7 for multitap play are written the same way and are outside the recorded set, which is one reason no mode claims completeness |
| mednafen_psx_hw         | Beetle PSX HW    | **card**         | carded in #175 alongside `mednafen_psx`'s — same implementation at the same revision (d6383bf), reached through the `beetle_psx_hw_` option prefix, so the card is the same seven modes and the same file names under the `beetle_psx_hw_` option keys. All seven modes are [D]                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| pcsx_rearmed            | PCSX ReARMed     | **card**         | carded in #176. A plain single-option card: the two modes are `pcsx_rearmed_memcard2`'s own values. `disabled`, the registered default, is `<rom_stem>.srm` alone; `enabled` adds the core-written `pcsx-card2.mcd` beside it, one second-slot card shared by every game. Its provenance reads libretro/pcsx_rearmed at 228c14e, the revision the shipped binary names in its `library_version` (`r25 228c14e`): slot 1 is always the frontend's — the core hands `Mcd1Data` to the libretro SRAM interface (`frontend/libretro.c:2050-2068`) and points its own `Config.Mcd1` at nothing (`:3483-3496`) — and the option, the core's only save option (`frontend/libretro_core_options.h:152-165`), adds the shared second card (`frontend/libretro.c:3500-3525`). No per-game spelling exists for the second slot, so the two modes are the whole space; no serial-keyed mode and no slot-1 option exist. Both modes are [D]                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| mupen64plus_next        | Mupen64Plus-Next | **standard**     | live-verified: combined fixed-size `.srm` (296960 B) in the standard dir, two games, two chip types (research doc §12)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| mgba                    | mGBA             | **standard**     | live-observed `.srm` in standard dir (+ sort-by-core dir `saves/gba/mGBA` from an earlier layout)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| mednafen_saturn         | Beetle Saturn    | **card**         | carded in #174, after the 08-17 read below moved it off standard-dir. The card (`atlas/data/core_oddities.json`) is a `governing_rule` card over `beetle_saturn_shared_int` and `beetle_saturn_shared_ext`, four modes for the four combinations; each names three files in the save directory — the internal backup RAM `<rom_stem>.bkr`, the backup cartridge `<rom_stem>.bcr` and the SMPC's RTC and language `<rom_stem>.smpc` — and the two switches divide them unevenly: `beetle_saturn_shared_int` swaps the `.bkr` and `.smpc` stems to `mednafen_saturn_libretro_shared`, `beetle_saturn_shared_ext` swaps the `.bcr` alone, so each mode is one combination of those two swaps. Its provenance reads beetle-saturn-libretro at ccba526, the revision the shipped binary names in its `library_version`: the core answers system RAM alone (`libretro.cpp:1026-1036`), so every save file is its own, and `MDFN_MakeFName` builds each path from the frontend's save directory and one of the two stems (`libretro.cpp:1045-1073`). The per-game mode is live-observed content-keyed on a RetroDECK arrangement (2026-07-23); the three shared modes are [D]. No mode claims a complete set: the `.bcr` appears only where the emulated machine has a backup cartridge attached                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| mednafen_ngp            | Beetle NeoPop    | **card**         | beetle-ngp-libretro@139fe34, the revision the binary names in its `library_version` (`v1.29.0.0 139fe34`): `retro_get_memory_data` and `retro_get_memory_size` answer no save id beyond system RAM (`libretro.c:780-781`, `:787-788`), every other id falling through to `NULL` and `0` (`:782`, `:789`), so RetroArch writes no `.srm`. The core builds `<save_dir>/<rom_stem>.flash` itself — `MDFN_MakeFName` composes save-directory, slash, stem, dot and the suffix its caller passes (`libretro.c:796-816`), and both callers pass the literal `flash`: the read at `mednafen/ngp/system.c:22` and the write at `:40`. That literal is one whole NUL-delimited string in the shipped `.so`: the unfiltered scan finds four fields containing `flash` case-insensitively, two of them case-sensitively, and the other three are savestate variable names — `FlashLength` and `flashdata` in `FLASH_StateAction` (`system.c:60`, `:65`), `FlashStatusEnable` in `StateAction` (`libretro.c:300`). The sections they sit in are `FINF` and `FLSH` (`system.c:72`, `:92`) and `MAIN` (`libretro.c:317`). Written once, at unload, and only when a flash block was registered (`libretro.c:510-514` → `rom.c:81-87` → `flash.c:261-271`, `:210-218`); read back at load (`rom.c:56-78`, `flash.c:152-179`). Live-observed content-keyed in the standard dir (RetroDECK 0.10.9b, 2026-07-23)                                                                                                                                                                                                                                                                                                                                                                 |
| pokemini                | PokeMini         | **card**         | carded in #169. One mode, one file: `<save dir>/<rom_stem>.eep`, anchored in the shipped `.so` on the whole literal `%s%c%s.eep` (`atlas/data/core_oddities.json`). Its provenance reads the libretro/PokeMini fork — the binary's `library_version` is only `v0.60`, which every build of the fork reports, so the read is bound to the binary by whole strings instead: `retro_get_memory` answers system RAM alone (`libretro/libretro.c:895-909`), so RetroArch writes no save file; the core takes the content's basename, strips the extension and joins it to the frontend's save directory itself (`:194-212`, `:556-561`), reads the file back at load (`:1283-1289`) and writes it at unload only when the cartridge EEPROM was actually written (`:1343-1351`) — an untouched game leaves no file. EEPROM sharing is compiled off with no option to turn it on (`:500-501`), so the file is one per game. A content-keyed `.eep` was live-observed in the standard save directory (2026-07-23) — the observation that stood alone under the old standard-dir verdict                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| swanstation             | SwanStation      | **card**         | carded in #175. The card carries `swanstation_MemoryCards_Card1Type`, `swanstation_MemoryCards_Card2Type` and `swanstation_MemoryCards_UsePlaylistTitle` as its `governing_rule` and states twenty modes, one per pair of slot types, each naming its files in the save directory: `Libretro` (slot 1 only) is the standard `<rom_stem>.srm`; `Shared` is `duckstation_shared_card_1.mcd` / `_2.mcd`; `PerGame` is `<save_id>_1.mcd` / `_2.mcd`, keyed on the disc's game code; `PerGameTitle` is `<rom_stem>_1.mcd` / `_2.mcd`, the title this port takes from the content file's own name; `None` is no card at all. Its provenance reads libretro/swanstation at 4d309c0, the revision the shipped binary names in its `library_version`: the two slot-type options and `UsePlaylistTitle` at `libretro_core_options.h:785-830`, the SRAM route at `libretro_host_interface.cpp:751-786`, the core's own path helpers at `:404-415` selected per slot in `src/core/system.cpp:1450-1508`, and `GetGameInfo` at `libretro_host_interface.cpp:396-410`. `UsePlaylistTitle` on — the default — folds an m3u's discs onto the playlist's own title, so the name still spells the loaded content's stem; off, each disc names its own title-keyed card (`system.cpp:1650-1656`). A game-code card for content that yields no code falls back to the shared card (`:1457-1466`). All twenty modes are [D], never live-observed; the card's alternatives list the one-edit neighbours rather than all twenty combinations                                                                                                                                                                                                                                         |
| dolphin                 | dolphin-emu      | **suspect**      | RetroDECK dir_preps under `<retroarch config dir>/saves/dolphin-emu/…` imply a fourth root kind; zero core-written data observed; needs one live run                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| azahar                  | Azahar           | **suspect**      | same pattern (`saves/Citra/…` targets)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ppsspp                  | PPSSPP           | **suspect**      | same pattern (`saves/PPSSPP/PSP/…` targets); also carries the shipped sort-flip override (research doc §6)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| opera                   | Opera            | **card**         | opera-libretro@67a29e6: the NVRAM file's name carries two switches, so the modes are their product. `opera_nvram_storage` picks the directory and the naming scheme — `<save_dir>/opera/per_game/<stem>.<version>.srm` (`opera_lr_nvram.c:130-152`) or `<save_dir>/opera/shared/nvram.<version>.srm` (`:154-175`), the `opera` segment joined onto the frontend's save directory at `:115-128` — and `opera_nvram_version` supplies `<version>` (`opera_lr_opts.c:346-351`, `:330-337` for the storage). Both are registered value sets, `per game\|shared` defaulting to `per game` and `0`–`9` defaulting to `0` (`libretro_core_options.c:177-187`, `:188-206`), so the twenty (storage, version) pairs are a closed product: the card is a `governing_rule` card with one mode per pair, each naming its one file literally, because the file-name vocabulary has no option-valued hole to leave the version in. The system directory stands in only where the frontend names no save directory (`opera_lr_nvram.c:102-113`), which RetroArch never does — its environment callback hands over `runloop_st->savefile_dir` and breaks (`runloop.c:2001-2005` at `a79435a`), so it returns true (`:3792`) and the pointer is an array member (`runloop.h:298`) and never NULL, failing both halves of the core's guard (`opera_lr_nvram.c:78`). `RETRO_MEMORY_SAVE_RAM` is NULL (`libretro.c:430-444`) — the nvram files are the only persistence, and the legacy `<stem>.srm` and `3DO.nvram` spellings are read at load and never written (`opera_lr_nvram.c:243-265`, `:328-349`), so no mode claims a closed candidate universe. Not yet live-observed                                                                                                  |
| genesis_plus_gx         | Genesis Plus GX  | **card**         | carded in #175. The card dispatches on the content's class and carries `genesis_plus_gx_system_bram`, `genesis_plus_gx_cart_bram` and `genesis_plus_gx_cart_size` as its `governing_rule`. Cartridge content fills the frontend's SRAM interface, so RetroArch writes the standard `<rom_stem>.srm`; CD content never reaches it — `SYSTEM_MCD` runs `scd_init()` instead of `md_cart_init()` (`core/genesis.c:162-175`), the reachability correction of #162 — and its saves are the core's own BRAM files, flat in the save directory. Twenty-six CD modes name them: the internal BRAM keyed per BIOS region, the three `scd_E.brm`/`scd_U.brm`/`scd_J.brm` declared together because which one a game touches is the console region, or per game `<rom_stem>.brm`; beside it the RAM cart, shared `<size>_cart.brm` or per game `<rom_stem>_<size>_cart.brm`, `<size>` spelled `128Kbit`/`256Kbit`/`512Kbit`/`1Mbit`/`2Mbit`/`4Mbit`. Its provenance reads ekeeke/Genesis-Plus-GX at 46a5521, the revision the shipped binary names in its `library_version`: the three options read at first run (`libretro.c:1385-1500`), written by `bram_load`/`bram_save` (`:3675`, `:3730`), the loader's valid extensions at `:3129`. The row's old [O] is closed by the card: `cart_size` disabled is no cart at all. All modes are [D], never live-observed; the card's alternatives list the one-edit neighbours rather than all twenty-six CD combinations                                                                                                                                                                                                                                                                                                     |
| neocd                   | NeoCD            | **multi-option** | neocd@5eca2c8: core writes backup RAM itself (`src/path.cpp:137-168`) — `neocd_per_content_saves` is registered as `Off\|On` and so defaults to **`Off`**, the shared `<system_dir>/neocd/neocd.srm` (`src/path.cpp:7-9`, `:11-26`); the value `On`, and only that spelling, switches it to `<save_dir>/<stem>.srm`, the content file's name without its extension (`src/libretro_variables.cpp:111-114`, `src/path.cpp:62-79`). **This default is written down because nothing on a machine states it**: the core registers its variables at load (`src/libretro_variables.cpp:45`), so a probe of the shipped binary reads no options, and NeoCD has no rule card to hold one structurally. The core **also** exposes the same RAM via `RETRO_MEMORY_SAVE_RAM` (`src/libretro.cpp:214-215`) — RetroArch persists a standard `.srm` in parallel. Which copy wins on load is unestablished [O]; a card stating one location would guess — deferred until live-verified                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| puae                    | PUAE             | **card**         | libretro-uae@0043cf9: `retro_get_memory_size` answers system RAM alone (`libretro/libretro-core.c:8881-8886`), and the save story is the content's class (`dc_get_image_type`, `libretro/libretro-dc.c:825-868`). Floppies write into the loaded image itself, or nowhere where the format cannot take a write (`adz`/`dms` unpacked into memory, `ipf`/`fdi` write-protected at insert — `sources/src/disk.c:1266-1268`, `:1494-1520`) since `disk_setwriteprotect` is called only from the redirect helpers (`libretro-core.c:605`, `:642`); `puae_floppy_write_protection` discards every write and outranks the other switch (`disk.c:865`, `:3248-3251`); `puae_floppy_write_redirect` leaves `<stem>_save.adf.gz` under the save root (`:1300-1306`, `libretro-core.c:612-658`). CDs get `flash_file=<save dir>/<stem>.nvr`, or the shared `puae_libretro(_cdtv).nvr` under `puae_shared_nvram`, for the CDTV/CD32/CD32FR models alone (`:5577-5578`, `:5640-5661`, `:5969-5978`) — appended for the model, so the card's rule reads `puae_model` and `puae_use_boot_hd` too. WHDLoad content mounts `<save dir>/WHDSaves` (`:6767-6785`) or `WHDSaves.hdf` (`:6824-6855`) under `puae_use_whdload`, and WHDLoad names the per-game directory inside it from the slave's `ws_name`; hard-disk content writes in place (`:5709-5728`); an archive is classified by the members the machine seam lists out of it (`:6280-6362`)                                                                                                                                                                                                                                                                                                                           |
| puae2021                | PUAE 2021        | **card**         | same mechanisms at 58527ce with its own line numbers; one difference, and it is when the file appears rather than where: the CD32 EEPROM is created on the first write (`sources/src/akiko.c:128-143`) rather than at reset, while the CDTV's battery RAM is still created at reset (`sources/src/cdtv.c:1636-1655`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| _remaining ~142 of 159_ |                  | **unaudited**    | queue below (2026-07-24 triage scan)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |

**A default is written down only where no read of the machine recovers it.** Where a core registers its options during
`retro_set_environment`, `query_core` captures their defaults and value sets off the shipped binary — so a value copied
into this table would be a second, ageing copy of something the machine answers exactly, and the rows state option
_keys_, what each mode does, and where the files land instead. Three shipped cores in this table register nothing there
— LRPS2, NeoCD and Dolphin — and the two with a default to keep it keep it in the one place that survives: **LRPS2** in
the structured `governing_option.default` of its rule card, and **NeoCD**, which has no card, in its own row above,
cited to the registration in source. **Dolphin** needs neither: no save option of its own has been established, which is
what its `suspect` verdict says. `tests/test_oddities.py` measures a card's option vocabulary against the core installed
on the machine it runs on, in both directions: the mode keys must be that option's registered value set, and a card must
record **no** default for a core that registers one — the redundant copy fails, rather than being kept correct by hand.
The round that produced this rule found five recorded values that had drifted from the binaries they claimed to
describe, one of them invented as a "version drift" that never happened.

**A recorded name is pinned to the string it was read from.** The method's step 1 reads file names and path fragments
out of the shipped binary; `saves.anchors` in each card writes down _which_ literal each recorded name came from, and a
test re-reads it there — whole NUL-delimited, so flycast's `/dc` (its texture-dump path) cannot stand in for the VMU
subdir format `%s%cdc`. What that catches is a **vocabulary rename**: a build that stops spelling `vmu_save_`,
`Mcd%03u.ps2` or `memcards` fails the suite instead of leaving a card describing names the core no longer writes. What
it cannot catch is the **grammar** around a literal — that `%s.ps2` is still the per-game memory card and not some other
file — which stays the job of source reading and live observation. Names no literal carries are marked `unprotected`
with their reason, and flycast's nine run-time-composed names are two different cases under that one mark. The eight
`<save_id>`/`<rom_stem>` port names are genuinely unguarded: they rest on live observation and the next re-audit.
`dc_nvmem.bin` is not unguarded, only **unanchorable**. Source establishes it — the core composes it from
`getRomPrefix()` and `nvmem.bin` at its one load and its one save site (flycast@1dac369
`core/hw/flashrom/nvmem.cpp:35-50`, `:246`, `:305`; `shell/libretro/oslib.cpp:91-93`, `:109-114`) — and the shipped
binary does carry both halves, as `.text` instruction immediates, which is exactly why no NUL-delimited literal can pin
it. A third mark, `arrangement`, is for a path the arrangement builds rather than the core — LRPS2's `pcsx2/memcards`
subdir, whose first segment matches a literal in the binary purely by coincidence (it is PCSX2's own data-root name). A
recorded name with none of the three marks fails the tests, so the opt-out is always a written decision.

**General fact for all core-written saves** [V]: `RETRO_ENVIRONMENT_GET_SAVE_DIRECTORY` returns the _redirected_ save
directory — sorting applied, mkdir-or-revert resolved (`runloop.c:2001` reads `runloop_st->savefile_dir`, which
`runloop_path_set_redirect` sets at `runloop.c:8977`). A core that nests its own subtree (Opera's `opera/…`, Kronos'
`kronos/…`) nests it under the same effective directory the standard rule resolves — so card subdirs compose with the
sorted-dir math instead of bypassing it.

## What a verdict says at answer time

A verdict only exists for a caller if the answer carries it. `SavefilePlacement.granularity` is `None` for every core
without a rule card, so an empty field cannot be the carrier — it reads as _nothing to report_ no matter which verdict
produced it. The separation is a caveat:

| verdict        | caveat on the placement | why                                                                           |
| -------------- | ----------------------- | ----------------------------------------------------------------------------- |
| `standard`     | none                    | nothing is withheld — RetroArch writes the save itself, per game              |
| `standard-dir` | none                    | nothing is withheld either — and no shipped entry carries it; see below       |
| `multi-option` | `core-multi-option`     | granularity depends on options atlas does not interpret — deliberately unsaid |
| `suspect`      | `core-suspect`          | a deviation is suspected and unproven                                         |
| _no entry_     | `core-unaudited`        | the standard rule is assumed, not verified                                    |

`core-multi-option` carries `core`, `verdict`, and `options` — the governing option keys as a comma-separated string,
read from the audit entry's `save_options`. Naming them is the point: "granularity unknown" leaves a consumer with
nothing to do, while "granularity depends on `neocd_per_content_saves`" — `neocd` is the one entry the data still calls
`multi-option`, and that is its one option — lets it name the setting, look the value up, or say which switch decides.
`multi-option` without `save_options` fails the loader — the verdict is exactly the claim that those options decide the
answer, so an entry that cannot name them has not earned it.

**`standard-dir` is a real verdict with no members.** The class exists because the observation and the reading arrive
separately: a core's save can be seen landing in the standard directory under names RetroArch did not write long before
anyone has read which names the core builds. `standard-dir` is that half-knowledge said out loud — the directory is
established, the file set is the core's and unstated.

It is empty because every member's reading eventually reached a form a card could hold. Four entries have carried the
verdict, and each left once the card could state what the read had found — for three of them the file names, for
`scummvm` a directory stated without them: `pokemini` in #169, `mednafen_saturn` in #174 (via `multi-option` in #172,
once the shipped binary was found to register two sharing options after all), `scummvm` in #174 as well, and
`mednafen_ngp` in #388. `scummvm` is the instructive one: its source read was already in the audit note when #171 gave
it the verdict, and what it waited on was machinery — the code rule that reads its `savepath` out of `scummvm.ini`
(`atlas/mode_rules.py`) and the mode form whose groups name no file at all (#170), both shipped in #174. Two of the
notes still record the move: `mednafen_ngp`'s and `pokemini`'s both end "Supersedes the standard-dir triage — the file
set is now stated by the card" (`atlas/data/core_audit.json`). A core audited today would still pass through the verdict
if its directory were settled and its file set were not; nothing about the class was retired, only its membership.

While an entry sat there, a consumer was not misled:

- **The file names were never claimed.** atlas states no file set for any core without a card: the answer is the literal
  `<rom_stem>.*` observation, or `unknown` with "never guessed" — the fixed `<rom_stem>.srm` is gone
  (`atlas/placement.py`). `standard` sits in exactly the same position, so there is no asymmetry between the two to
  surface. What `standard-dir` adds is that a save may be _several_ files, and the observation already reports all of
  them.
- **The granularity read as per-game.** Three of the four were live-observed content-keyed before they were carded, so a
  per-game consumer reading `granularity=None` the way it reads a `standard` core's was correct, not misled. The fourth,
  `scummvm`, was never live-observed; its names are ScummVM's own slot files, keyed on the launcher target rather than
  the content path, which is why the card that replaced the verdict states its directory and refuses its names.

The row above says `standard-dir` carries no caveat, and with the class empty nothing rides on it. It is worth writing
down why the old argument for that row does not survive as stated: it rested on unfiltered option dumps showing no
save-related option registered by any of the three members the class then had, and the 08-17 read of `mednafen_saturn`
found two sharing options that had been missed. A future entry would have to earn the empty caveat cell rather than
inherit it — "depends on something nobody read" is the `multi-option` risk, and a directory-only verdict does not by
itself rule it out.

## Audit queue — 2026-07-24 triage scan

Unfiltered `strings` dumps of all 211 shipped `.so` files, searched for save-related option keys and path-format
strings. **A triage hit is a queue position, not a verdict** — every entry stays _unaudited_ until the full method ran.
One method lesson from this pass: pattern-based triage produces false negatives — `genesis_plus_gx` (Sega-CD BRAM,
`.brm` files) matched no pattern because its path strings start mid-word (`_128Kbit_cart.brm`); suspicious systems get a
manual dump check even when the scan is silent.

Card-suspect first (own path construction, or a granularity/root option), then likely-internal hits, then the scanless
rest:

1. ~~`genesis_plus_gx`~~ — audited 2026-07-24 (multi-option then; carded in #175, see table)
2. ~~`opera`~~ — audited 2026-07-24 (card, see table)
3. ~~`neocd`~~ — audited 2026-07-24 (multi-option, double persistence, see table)
4. `fbneo` — own subtree `%s%cfbneo%c%s.fs` / `.memcard`, plus `%s%s.nv(ram)`
5. `kronos` — own subtree `%s%ckronos%csaturn%c%s(-ext*).ram`, `%s%ckronos%cstv%c%s.ram`
6. `scummvm` — own save scheme (`pegasus-%s.sav`; ScummVM savepath semantics)
7. `dosbox_pure` — `.pure.zip` saves, "Save Difference Per Content" (manual check; scan hit only weakly)
8. ~~`puae` / `puae2021`~~ — read 2026-09-05, both carded. The `cd32nvram` hit is the emulated chip's own config key,
   not a file; the file is `flash_file`, appended by `retro_config_kickstart` as `<save dir>/<stem>.nvr` or, under
   `puae_shared_nvram`, `puae_libretro.nvr` / `puae_libretro_cdtv.nvr` (libretro-core.c:5640-5661 at 0043cf9, the
   revision the binary's `library_version` names) — and only where the model has an extended Kickstart, which is CDTV,
   CD32 and CD32FR alone (:5969-5978). The bigger half was the floppy story the scan never pointed at: UAE's
   `fetch_path` hands every path name the frontend's save directory under `__LIBRETRO__` (misc.c:609-617), so its write
   file is `<save dir>/<stem>_save.adf` — but `disk_setwriteprotect` is the only thing that creates one and its only
   callers are the two libretro redirect helpers (:605, :642), so with both floppy switches off a plain image takes its
   writes in place and a compressed or raw-disk one keeps nothing at all. `puae_floppy_write_redirect` gzips the write
   file at close, `puae_floppy_write_protection` discards every write and outranks it. Extended 2026-09-06 to the
   remaining classes: WHDLoad content mounts `<save dir>/WHDSaves` as a volume whose per-game sub directory WHDLoad
   names from the slave's own `ws_name` (:6767-6785, whdload/WHDLoad.prefs:37), hard-disk content is listed and answers
   by what boots off it — the helper volume being mounted before it (:6712, :6819 before :6873) — and a `.zip` is
   classified by the members the seam reads out of it. Fifteen modes now, and what the rule refuses instead of guessing
   is: a playlist, a `.7z`, an archive it could not list, one mixing classes, one holding two WHDLoad volumes, one whose
   entries are no class at all, a launch pinning a member, a hard-disk image while the helper is mounted beside it, a
   volume that boots nothing, WHDLoad prefs pointing elsewhere or unreadable, and any of these classes on an explicitly
   selected CD model, whose non-volatile memory file would ride beside the class's own story.
9. `geolith` — `geolith_memcard` / `geolith_memcard_wp`
10. `virtualjaguar` — CRC-keyed `%s%08X.srm` names
11. `melonds` / `melondsds` / `desmume` — `.dsv`(+`.bak`) / DSi-NAND title `.sav` / `libretropy_get_save_directory`
12. MAME family (`mame`, `mame2000/2003/2003_plus/2003_midway/2010`, `fbalpha*`) — nvram/diff trees, own block
13. VICE family (`vice_x64` …) — disk/NVRAM writes, own block
14. `same_cdi` / `cdi2015` — CD-i NVRAM
15. `handy` — writes `.eeprom` (`EEPROM SAVE %s`)
16. `mesen` — `.eeprom128` / `.eeprom256`
17. `stella` / `stella2023` — `nvram`
18. likely-internal (symbols only, probably libretro-SRAM): `blastem`, `picodrive` (but: Sega CD — manual check),
    `atari800`, `cap32`, `hatari`, `yabause`, remaining single-hit cores
19. scanless remainder (`gambatte`, `snes9x*`, `nestopia`, `fceumm`, …) — expected standard via the libretro SRAM
    interface; verdict only after per-core source check

### Refreshed 2026-08-17 — `scripts/sweep_save_literals.py`

The triage above, re-measured mechanically after the record and port-core rounds — the sweep script scans every
record-covered `.so` for save-looking literals, so the pass now repeats in one command. Of the card suspects above,
fbneo, geolith, virtualjaguar, melonds, the MAME block and the FB Alpha block are audited and carded. 40 of 87 record
cores carry hits; most are the libretro-common `retro_save_directory` variable name alone (an _ask_, not a write —
superbroswar takes the directory and reads it nowhere) or three-character coincidences, and those stay in the tiers
above. What remains is the open half of the `core-own-writes-unestablished` caveat each of these records honestly
carries, ordered by what is at stake:

1. ~~**`genesis_plus_gx`**~~ — read 2026-08-17, and the hit was real, one step worse than flagged: the CD rows were not
   incomplete but **unreachable**. `SYSTEM_MCD` runs `scd_init()` instead of `md_cart_init()` (core/genesis.c:162-175 at
   46a5521), so `sram_init` never runs and the memory interface answers 0 on CD content; the record's
   megacd/megacdjp/segacd rows were corrected to `memory_types: []` — the audit's first wrong shipped row, produced by
   citing the reachable memory switch without tracing which systems reach it. What remained was the card: the CD save is
   the core's own BRAM tree under **three** interacting options (`genesis_plus_gx_system_bram`, `_cart_bram`,
   `_cart_size` — shared region-keyed `scd_E/U/J.brm` or per-game `<rom>.brm`, plus the RAM cart's `<size>Kbit_cart.brm`
   spellings; libretro.c:1383-1500 as this note records the range and 1385-1500 as the card cites it, written by
   bram_load/bram_save at :3675/:3730) — the multi-option shape the single-option card schema could not express, and the
   same gap swanstation sat on. #175 closed both: the rule-selected form carries all three options here and the pair of
   slot types there, and **both are carded**.
2. ~~**`fbalpha2012_cps1`**~~ — read 2026-08-17 (#158): the boundary really was one build too small, with a shape of its
   own — no `BurnStateSave` and no `.fs` in this build; the non-volatile half is the boards' EEPROM, written as
   `<driver>.nv` at teardown for the sets that have one (cps_run.cpp:161: Q-Sound/CPS1.5, Pang hardware, EEPROM
   bootlegs), beside the family's option-gated `<driver>.hi`. The record was true ("frontend writes nothing") and is
   retired by the fifth family card.
3. ~~**`kronos`**~~ — read 2026-08-17, carded: the tree was real and richer than the hits — `kronos/saturn/<stem>.ram`
   plus option-keyed `-ext*.ram` cartridges, `kronos/stv/<stem>.ram` beside the board eeprom `<romset>.nv`, and a
   `kronos_use_beetle_saves` option that moves the Saturn pair to Beetle Saturn's flat `.bkr`/`.bcr` spellings so the
   two cores can share a library. Both subtrees are created blind at `retro_init`, whichever kind of content ever runs.
4. ~~**`tyrquake`**~~ — read 2026-08-17, carded: the familiar port shape, keyed like vitaquake2 by the content's
   directory. `retro_load_game` joins the frontend's save directory with the content directory's basename
   (libretro.c:990-1007 at dfdae65, the revision the binary's `library_version` names) — before the engine re-roots its
   basedir for id1/hipnotic/rogue/quoth content, so the subdir keeps the content directory's own name. Inside:
   `s0.sav`-`s11.sav` from the menu's twelve slots (the console's `save` takes a free name, `.sav` defaulted) beside a
   written-back `config.cfg` at teardown. `COM_WriteFile`, the one writer into the content tree, has no caller in this
   build. The record was true ("frontend writes nothing") and is retired by the card.
5. ~~**`quasi88`**~~ — read 2026-08-17, carded: the collision shape confirmed, with an option behind it. The core
   answers no save id (its ids are system and video RAM), and floppy writes are routed by `q88_save_to_disk_image`:
   disabled (the registered default) creates `<save dir>/<image stem>.srm` as a _differencing file_ at first open —
   empty at launch, byte differences on write, the image untouched (file-op.c:249-258, :415-445 at 42be798, the revision
   the binary's `library_version` names) — so the file spells exactly what the frontend would write for a `save_ram`
   core, without the frontend writing anything; enabled writes the sectors into the loaded `.d88` itself, and the
   modified content is the save. Each _opened_ image gets its own diff (m3u members, menu picks), which is the scope on
   the declared `<rom_stem>.srm`. The record was true and is retired by the card — the first whose two modes stand on
   different roots.
6. ~~**`cap32`** and **`hatari`**~~ — read 2026-08-17, one carded on the spot, one blocked on a form. `cap32`: the
   binary's `%s%s%s.sav` is the compile-time join of `"%s%s%s." EXT_DIFF_DSK` (retro_disk_control.c:90 at a5d96c5) — a
   track-level differencing file per drive-A floppy, `<save dir>/<image name>.sav` with the extension kept, written only
   when the disk was altered (dsk_diff, slots.c:558), at eject, swap and teardown; carded. `hatari`: the
   `auto.sav`/`hatari.sav` hits are memory-snapshot (savestate) defaults reachable only through the core's own GUI — but
   the read found the real story: write-back into the content itself (floppy.c:599-634 at 7008194, governed by
   `hatari_writeprotect_floppy`, default off), hard-disk content written in place, and Falcon/TT `hatari.nvram` under
   `$HOME/.hatari` — a root the card format cannot state. Audited **multi-option** here, and **carded** in #174, once
   both mode forms that name no file of the core's own existed: #173 brought the one saying the save lives inside the
   content, #174 the one stating the discarded emptiness. The two write-back modes take the first form and the two
   protected ones the second, and the content's class decides which of `hatari_writeprotect_floppy` and
   `hatari_writeprotect_hd` governs.
7. ~~**`pokemini`**~~ — read 2026-08-17, carded: `%s%c%s.eep` joins the save directory and the content's stem
   (libretro.c:556-561), read back at load, written at unload only when the cartridge EEPROM was touched (:1343-1351);
   EEPROM sharing is compiled off. The audit's 07-23 live observation of a content-keyed `.eep` was this chain — the
   standard-dir verdict became the card that states the file.
8. ~~**`scummvm`**, **`dosbox_pure`**, **`desmume`**, **`easyrpg`**, **`mednafen_saturn`**~~ — all read; `handy` /
   `mesen` (`.eeprom*`) stay confirmed where the old tiers put them. `easyrpg` (read 2026-08-17): carded — the libretro
   port names no save path at all, so RPG Maker's fifteen `Save##.lsd` slots (scene_save.cpp:69 at 6a244c1) land beside
   the game's own files, and archived content gets a sibling `<archive name>.save/` directory (filefinder.cpp:88-127) —
   the frontend's save directory never enters the answer. `mednafen_saturn` (read 2026-08-17): the `BSC.MCR` hit was the
   SH-2 bus controller's register name in the debugger's vocabulary, not a file — but the read corrected the audit
   anyway: the shipped binary registers **both** sharing options with their UI labels, refuting the standard-dir
   verdict's "registers none", so Beetle Saturn went **multi-option** here (`beetle_saturn_shared_int`,
   `beetle_saturn_shared_ext` swap the three live-observed files' stems to a shared spelling) and is **carded** since
   #174, whose rule-selected form let one card hold all four combinations. `dosbox_pure`: carded — the emulated C: drive
   is a union of read-only content and one writable overlay, and the overlay is the save,
   `<save dir>/<rom_stem>.pure.zip` (DBP_GetSaveFile, dosbox_pure_libretro.cpp:826-844 at ed5e809), created on first
   write, rewritten on a five-second schedule; a `.SAVENAME` redirect shares saves between contents, a legacy `.sav`
   keeps being used unless strict mode forbids it, Boot-OS setups add `-CDRIVE.sav` and hash-keyed disks. `desmume`:
   carded — its backup device writes `<save dir>/<rom_stem>.dsv` (mc.cpp:232-235 at 7f05a8d) with the battery path
   defaulting to the save directory this generation fills (path.cpp:196-222); no `.dsv.bak` here, the copy is gated on a
   Windows-only setting — the 2015 card's pair shrinks to one file. `scummvm`: audited **standard-dir** here, not yet
   carded — saves are target-keyed slot files in ScummVM's own `savepath` setting (default: the save directory, flat;
   truth in `<system dir>/scummvm.ini`, libretro-os-utils.cpp:64-69, :212-221 at 686cdd1) — a card needed the
   ini-reading code rule plus #170's mode form; both arrived in #174, and the card there states the directory while
   refusing the names.
9. ~~**`melondsds`**~~ — closed 2026-08-17: no host file appears beside the frontend's `.srm` on the catalogue route.
   The `.sav` hits are NAND-internal spellings inside the image kept in the system directory, firmware and Wi-Fi
   settings flush to the system directory (core/tasks.cpp:263-292 at v1.2.0), and the GBA slot writes back to the save
   file the subsystem was handed at its own path (tasks.cpp:239-260) — a route no catalogue entry launches.

~~From the card side, `bsnes_hd_beta` joins~~ — read 2026-08-17 in its own tree at beta_10_6, and the trace found more
than thin provenance: in **both** bsnes and bsnes-hd the `time.rtc` branch lives only in the Game Boy handler
(program.cpp:565-601 mainline, :541-580 hd), reached solely through the Super Game Boy subsystem, and the pickaxe shows
it never lived anywhere else — a Super Famicom cartridge's real-time clock is never persisted. Both cards declared
`<rom_stem>.rtc` and both retire it; bsnes-jg's is real (its generic file-write callback serves any system's rtc,
libretro/libretro.cpp:527-544) and stays. The audit era's tally of corrected shipped claims: genesis_plus_gx's CD rows,
the bsnes pair's `.rtc`, Beetle Saturn's "registers none".

### The reachability pass over the filled records — 2026-08-17

The genesis_plus_gx wrong row defined a class — a **filled** `memory_types` on a system whose hardware never reaches the
claimed id — so the whole record set was swept for its two mechanical tells: a record spanning a cartridge family and a
disc family with the disc rows filled, and one citation string copied verbatim across every system. Four candidates, all
four read to the source:

- `picodrive` (megacd/megacdjp/segacd) — **correct**, and the instructive twin: its memory interface has an explicit
  Sega CD branch (libretro.c:1706-1711 at 046e5ff), so RetroArch's `.srm` _is_ the CD backup RAM. Same hardware as
  genesis_plus_gx, opposite wiring; the copied cartridge-only citations on those rows were strengthened to say so.
- `mednafen_pce` (tg-cd) and `mednafen_pcfx` — **correct**: the backup RAM is console hardware, answered unconditionally
  through the interface (beetle-pce libretro.cpp:2066-2070, beetle-pcfx libretro.cpp:1724-1725), so the disc systems
  reach it exactly as the card systems do.
- `neocd` — its filled rows come from the 07-24 audit itself ("double persistence"); the own-files half of that verdict
  sits in the tiers above, not in this class.

One wrong row set in 87 records, corrected in #162. The tell that found it stays cheap to re-run: mixed hardware
families plus a copied citation is grounds for a per-system reachability trace, and a citation that names reachable code
still has to name who reaches it.

## The verification matrix is data, and maintenance is enforced

`atlas/data/core_audit.json` holds this table's machine-readable core: per core, the verdict, a concise evidence `note`,
whether per-game saves are a proven capability, the `save_options` a `multi-option` verdict rests on, and — per
arrangement (retrodeck / emudeck / bare) — the versions the knowledge was verified against (`null` = never verified
there).

`per_game_capable` is deliberately tri-state: `true` means at least one per-game mode is established by source, shipped
binary, or observation; `false` requires evidence that no such mode exists; `null` means unknown. It is a static
capability, not the mode currently selected on a running machine. `SavefilePlacement.granularity` answers the latter by
reading live configuration where a runtime rule exists; today that is complete for rule-card cores and `None` elsewhere
(`docs/tasks/save-detection.md`) — where the `None` is deliberate rather than incidental, the `core-multi-option` caveat
says so and names the options. A shared default can therefore coexist with `per_game_capable: true`.

Three mechanisms keep the data honest instead of hoping someone maintains it:

- strict loading requires every audit entry to state both `per_game_capable` and `note`
- strict loading requires a `multi-option` entry to list its `save_options`, and rejects them on any other verdict
- a test fails when a rule card lacks an audit entry
- the resolver attaches an `unverified-version` caveat when a card is applied on an arrangement that is `null` in the
  matrix, or whose live-read versions (RetroDECK's `retrodeck.json` `version`, the core's own `library_version` via
  `query_core`) differ from the verified ones

## Understanding old and new versions at once

The versioning model is **feature detection over version comparison** — dispatch on what the machine observably is, not
on parsing version strings (the browser lesson: sniffing user agents loses; probing capabilities wins):

- **Live reads never go stale.** When ES-DE or RetroDECK change config _contents_, the resolver absorbs it — it reads
  live. Only knowledge (cards) and procedures (parsers) can drift.
- **Parsers grow tolerant, not switched.** The gamelist parser already handles both ES-DE's two-root quirk and
  well-formed XML through one code path. A format change extends the parser; both shapes stay supported.
- **Card applicability is feature-detected** (implemented): `query_core` captures the option definitions a core
  registers in `retro_set_environment` (all API formats: `SET_VARIABLES`, `SET_CORE_OPTIONS`/`_INTL`, v2, v2 `_INTL`). A
  card applies when its governing key is observably registered — then version drift is demoted to provenance; a key the
  core does not register retires the card (`core-generation-mismatch`, the standard frame stays with the caveat);
  registered defaults outrank the card's shipped-generation copy, and persisted values are validated against the live
  value set. A core that could not be read at all retires its card too, under its own code
  (`core-generation-unestablished`): the `.so` file name is not evidence of a generation. Not every core is capturable —
  LRPS2 itself registers its options after `retro_set_environment` (probe shows none), so the uncaptured case — the core
  answered, its options did not — falls back to the version comparison. That fallback establishes the generation, not
  the _setting_: with no registration read, the core states no default either, and a card that records none (because its
  core normally registers one) then has nothing to select a mode with. If the machine's own configuration does not state
  the value, the card steps aside under its own code, `core-option-value-unestablished` — one level below the two above,
  and never alongside either, since each of those has already retired the card before an option is read. LRPS2 is the
  case the recorded default exists for, and it keeps answering. Next step when an old generation gets audited:
  per-generation card _variants_ keyed by their option signature.
- **A vector per generation, never deleted.** Each supported generation keeps its fixture machine in the conformance
  vectors — that is the guarantee that understanding an old version survives supporting a new one.
- The version matrix records what was _proven_; the caveat marks everything else as unverified rather than wrong.

Method per core:

1. unfiltered `strings` pass over the shipped `.so` for option keys and save-related strings, plus `query_core` for the
   option definitions the binary itself registers — keys, defaults, value sets
2. upstream source for anything the scan implies (path construction, what each option value does), at the revision the
   shipped binary names — cited as `file:line`
3. live observation on a real machine where save data exists
4. verdict + (if deviant) rule card with provenance, per-mode status, and an anchor for every name the card records

**A string the scan cannot find is not a code path that does not exist** — step 1 bounds what a name scan can prove,
step 2 is what establishes a path. Three ways a shipped build hides what it does, each of which has produced a wrong
verdict here:

- It **compiles out its INFO and DEBUG log format strings** (flycast's
  `"flash/nvmem is missing, will create new
  file..."` and every `DEBUG_LOG` text in its VMU handling are absent from
  the `.so` while the `WARN_LOG` texts beside them are present), so probing for a branch by the text of its info log
  reads absence as non-existence.
- The compiler **folds short literals into instruction immediates**, which is where flycast's `dc_nvmem.bin` lives — a
  `movabs` operand in `.text` at each of its two sites, in no string table.
- The linker **tail-merges a literal that is a suffix of another literal**, and then it has no copy of its own: LRPS2's
  `textures` is stored as the tail of `GL_EXT_protected_textures` (`0x8e6d6a`) and its `Textures` as the tail of
  `glBindTextures` (`0x8e7813`) in the shipped `pcsx2_libretro.so` at 14d19f8. `strings` emits whole NUL-delimited runs,
  so a suffix is invisible to it — and to every encoding sweep, because the encoding was never the problem. This one
  cost a wrong absence: "no texture path literal in any encoding" was recorded for LRPS2 when both components were in
  the binary all along. **Search raw bytes, not tokens** — `data.find(b"textures")` over the whole file, then read back
  the enclosing NUL-delimited run to see what the name is a suffix of.
- The **scan itself** hides things, which is the one failure above that is not the build's doing. A pass that keeps only
  runs of printable ASCII drops every literal carrying a newline or a tab — which is most format strings and all log
  lines, exactly the material step 1 exists to find. Keep `\t`, `\n` and `\r` inside a run and cut only on NUL.

**What to do when a name is too short to be a string at all.** The three mechanisms above say absence proves nothing;
they do not say what proves presence, and knowing only the first half is what makes this mistake repeatable. It was made
twice more after the list was written — for RACE, whose `.ngf` extension is a three-character literal a compiler stores
as one immediate, and where no other literal in `flash.c` is longer, so the whole unit leaves no trace a scan can see.
The answer is not another sweep. **Ask the build**: `Makefile.common` lists its translation units, and a unit named
there is compiled in whatever the strings say. An anchor for such a name records that, and says why no literal backs it
— which is the difference between a gap that is understood and one that is merely unexplained.

**A built path is not a written path.** Found 2026-08-17, twice in one reading round: boom3 and vitaquake3 both take
`GET_SAVE_DIRECTORY`, append a subdirectory, create it with `path_mkdir` — and nothing ever reads the result. boom3's
`Sys_GetPath(PATH_SAVE)` answers `BUILD_DATADIR`, the ROM tree (checked at the shipped revision too); vitaquake3's
`homePath` static is never filled, so `fs_homepath` falls back to the ROM tree. The block reads like the placement, and
the directory even _appears on disk_ — a live observation would "confirm" it — while every byte of it is dead. So step 2
does not end where a path is built: **trace who reads the variable the block fills.** The tell that found both: grep the
unit for the variable and compare assignments against reads — a variable only ever assigned is dead, and an `extern`
array needs that check across every unit the build actually compiles (ask the build, as above). The same round's
openlara mirror — a reading that stopped at "lets its engine write inside it" when the engine names exactly one
establishable file — says it from the other side: the shell/engine boundary is where readings die early. Carry the chain
to the write call, in both directions.

Version drift is real and recorded: LRPS2 changed its option scheme between generations (`pcsx2_memcard_slot_1/2` →
`pcsx2_shared_memory_cards`), observed in a live `retroarch-core-options.cfg`. Cards state which shipped version they
match.

Drift is also easy to invent, and this audit did once: PCSX ReARMed's live `pcsx_rearmed_memcard2 = "disabled"` was
recorded as a value the core no longer knows, when the shipped core registers exactly that value and defaults to it
(`pcsx_rearmed@228c14e frontend/libretro_core_options.h:152-165`). A live value only proves drift once the binary has
been asked whether it still knows it — the probe answers that in one call.
