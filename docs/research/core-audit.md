# libretro core audit — save behaviour, core by core

The systematic pass behind "atlas answers libretro completely": every core RetroDECK's matrix references gets a verdict,
sourced from an **unfiltered** options scan of the shipped binary, upstream source where behaviour needs proof, and live
observation where available. Filtered/truncated string scans are banned — one produced a wrong LRPS2 card (caught by
review; the option had been grepped away).

Verdicts:

- **standard** — roots at the RetroArch save directory under the standard rule; no card needed for placement. File-set
  nuances, if any, are noted.
- **standard-dir** — the directory follows the rule, but the file set is core-owned: the core writes its own
  content-keyed files (`.bcr`/`.bkr`/`.smpc`, `.flash`, `.eep`) instead of RetroArch's `.srm`.
- **card** — deviates; covered by a rule card in `atlas/data/core_oddities.json`.
- **multi-option** — directory is standard, but the file set / granularity depends on several interacting options; the
  single-option card schema cannot express it. Candidate for the code-rule-plus-card route (see the schema's own spec).
  Placement answers are correct today; granularity is honestly unstated.
- **suspect** — evidence of deviation exists but is unproven; needs a live run.
- **unaudited** — not yet examined.

| core (`.so` short name) | library_name     | verdict          | evidence                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ----------------------- | ---------------- | ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| flycast                 | Flycast          | **card**         | flycast@1dac369: `libretro.cpp:790-807` maps the live option to `per_content_vmus`; `oslib.cpp:38-67` builds the VMU path, called from `maple_devs.cpp:409-428`. Both placements live-verified: shared `bios/dc` VMUs, and `All VMUs` writing `<save_id>.<port>.bin` into the redirected save directory (`libretro.cpp:2142-2148`), the id being the disc's product number (`emulator.cpp:838-841`). Content without an id is named after the ROM ([V-source] `oslib.cpp:62`) — both spellings are stated, the condition with them. `VMU A1` is [D] and covers port A1 alone, so it states no file set. [O] which ports a mode covers is content-dependent (Naomi: B1/C1, `maple_cfg.cpp:246-253`)                                                                                                                                                                                                                                                                     |
| pcsx2                   | LRPS2            | **card**         | libretro/ps2 source: `pcsx2_shared_memory_cards` puts the cards either under `system_directory/pcsx2/memcards`, shared by every game, or into the save dir as `<rom_stem>.ps2` (`main.cpp:2154`, `Pcsx2Config.cpp:997`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| mednafen_psx            | Beetle PSX       | **multi-option** | beetle-psx-libretro@d6383bf: every memory-card file lands in `retro_save_directory` (`libretro.cpp:5234-5249`). `beetle_psx_use_mednafen_memcard0_method` decides whether slot 0 reaches RetroArch through the libretro SRAM interface as an `.srm` (`libretro.cpp:5129-5145`) or is written by the core as `<stem>.<idx>.mcr` (`:2145-2163`); `beetle_psx_enable_memcard1` adds the slot-1 card, written once a game dirties it (`:1972-1973`, `:2459-2485`, `mednafen/psx/frontio.cpp:991-1011`); `beetle_psx_shared_memory_cards` swaps the content stem for `mednafen_psx_libretro_shared` (`libretro.cpp:5247`). Option definitions: `libretro_core_options.h:642-655`, `:656-669`, `:670-683`                                                                                                                                                                                                                                                                    |
| mednafen_psx_hw         | Beetle PSX HW    | **multi-option** | same implementation at the same revision, `beetle_psx_hw_` option prefix                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| pcsx_rearmed            | PCSX ReARMed     | **multi-option** | pcsx_rearmed@228c14e: slot 1 is the frontend's — the core hands `Mcd1Data` to the libretro SRAM interface (`frontend/libretro.c:2050-2068`) and points its own `Config.Mcd1` at nothing (`:3483-3496`), so RetroArch writes the standard `.srm`. `pcsx_rearmed_memcard2` (`frontend/libretro_core_options.h:152-165`) is the only save option the core registers: switched on, slot 2 becomes `pcsx-card2.mcd` in the save directory — one card shared by every game (`frontend/libretro.c:3500-3525`). No serial-keyed mode and no slot-1 option exist                                                                                                                                                                                                                                                                                                                                                                                                                |
| mupen64plus_next        | Mupen64Plus-Next | **standard**     | live-verified: combined fixed-size `.srm` (296960 B) in the standard dir, two games, two chip types (research doc §12)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| mgba                    | mGBA             | **standard**     | live-observed `.srm` in standard dir (+ sort-by-core dir `saves/gba/mGBA` from an earlier layout)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| mednafen_saturn         | Beetle Saturn    | **standard-dir** | live-observed: core-written `.bcr`/`.bkr`/`.smpc` in the standard dir — file set is core-owned, directory follows the rule                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| mednafen_ngp            | Beetle NeoPop    | **standard-dir** | live-observed content-keyed `SNK Gals' Fighters (USA, Europe).flash` in the standard dir                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| pokemini                | PokeMini         | **standard-dir** | live-observed content-keyed `Pokemon Party Mini (USA).eep` in the standard dir                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| swanstation             | SwanStation      | **multi-option** | swanstation@4d309c0: dir always `GetSaveDirectory()` (`src/libretro/libretro_host_interface.cpp:396-403`). `MemoryCards_Card1Type` (`src/libretro/libretro_core_options.h:785-803`) selects the slot-1 card and `MemoryCards_Card2Type` (`:804-817`) the slot-2 one: `Libretro` (slot 1 only) is pure standard (`.srm`); `Shared` → `duckstation_shared_card_<n>.mcd` (`libretro_host_interface.cpp:405-409`); `PerGame` → `<game_code>_<n>.mcd` (serial-keyed, a `<save_id>` hole); `PerGameTitle` → `<title>_<n>.mcd`, sanitized (both `:411-415`, selected in `src/core/system.cpp:1457-1486`); `None` is no card at all. `UsePlaylistTitle` (`libretro_core_options.h:818-830`) folds the discs of an m3u onto the playlist's own title (`src/core/system.cpp:1645-1656`)                                                                                                                                                                                          |
| dolphin                 | dolphin-emu      | **suspect**      | RetroDECK dir_preps under `<retroarch config dir>/saves/dolphin-emu/…` imply a fourth root kind; zero core-written data observed; needs one live run                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| azahar                  | Azahar           | **suspect**      | same pattern (`saves/Citra/…` targets)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ppsspp                  | PPSSPP           | **suspect**      | same pattern (`saves/PPSSPP/PSP/…` targets); also carries the shipped sort-flip override (research doc §6)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| opera                   | Opera            | **card**         | opera-libretro@67a29e6: `opera_nvram_storage` writes the NVRAM either under `<save_dir>/opera/per_game/<stem>.<version>.srm` (`opera_lr_nvram.c:130-152`) or under `<save_dir>/opera/shared/nvram.<version>.srm` (`:154-175`), `<version>` being `opera_nvram_version`; `RETRO_MEMORY_SAVE_RAM` is NULL (`libretro.c:430-444`) — the nvram files are the only persistence. Not yet live-observed                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| genesis_plus_gx         | Genesis Plus GX  | **multi-option** | Genesis-Plus-GX@v1.7.4 46a5521: cart SRAM via the libretro memory interface (`libretro/libretro.c:3746-3757`) = standard `.srm`. Sega-CD BRAM is core-written into the save dir with names governed by three options (`libretro/libretro.c:1383-1503`): `system_bram` keys the internal BRAM per BIOS region — shared `scd_E.brm`/`scd_U.brm`/`scd_J.brm` — or per game, `<stem>.brm` (`:1383-1404`); `cart_bram` keys the cart BRAM per cart — shared `<size>_cart.brm` — or per game, `<stem>_<size>_cart.brm` (`:1428-1503`), where `<size>` is how the file names spell `cart_size`'s value: `128k`/`256k`/`512k` → `128Kbit`/`256Kbit`/`512Kbit`, `1meg`/`2meg`/`4meg` → `1Mbit`/`2Mbit`/`4Mbit` (`:1406-1426` maps value to size, `:1433-1500` size to name). [O] `cart_size`'s seventh value, `disabled`, maps to a size no name branch matches (`:1411-1412`) — what the core then does with the cart file is untraced                                         |
| neocd                   | NeoCD            | **multi-option** | neocd@5eca2c8: core writes backup RAM itself (`src/path.cpp:137-168`) — `neocd_per_content_saves` is registered as `Off\|On` and so defaults to **`Off`**, the shared `<system_dir>/neocd/neocd.srm` (`src/path.cpp:7-9`, `:11-26`); the value `On`, and only that spelling, switches it to `<save_dir>/<stem>.srm`, the content file's name without its extension (`src/libretro_variables.cpp:111-114`, `src/path.cpp:62-79`). **This default is written down because nothing on a machine states it**: the core registers its variables at load (`src/libretro_variables.cpp:45`), so a probe of the shipped binary reads no options, and NeoCD has no rule card to hold one structurally. The core **also** exposes the same RAM via `RETRO_MEMORY_SAVE_RAM` (`src/libretro.cpp:214-215`) — RetroArch persists a standard `.srm` in parallel. Which copy wins on load is unestablished [O]; a card stating one location would guess — deferred until live-verified |
| _remaining ~142 of 159_ |                  | **unaudited**    | queue below (2026-07-24 triage scan)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |

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
| `standard-dir` | none                    | nothing is withheld either; see below                                         |
| `multi-option` | `core-multi-option`     | granularity depends on options atlas does not interpret — deliberately unsaid |
| `suspect`      | `core-suspect`          | a deviation is suspected and unproven                                         |
| _no entry_     | `core-unaudited`        | the standard rule is assumed, not verified                                    |

`core-multi-option` carries `core`, `verdict`, and `options` — the governing option keys as a comma-separated string,
read from the audit entry's `save_options`. Naming them is the point: "granularity unknown" leaves a consumer with
nothing to do, while "granularity depends on `swanstation_MemoryCards_Card1Type`, …" lets it name the setting, look the
value up, or say which switch decides. `multi-option` without `save_options` fails the loader — the verdict is exactly
the claim that those options decide the answer, so an entry that cannot name them has not earned it.

**`standard-dir` needs no caveat of its own** — checked, not assumed:

- **No option governs it.** Unfiltered option dumps of the shipped cores show no save-related option registered at all
  (`mednafen_ngp` registers 1 option, `mednafen_saturn` 25, `pokemini` 13; none touches saves). There is no "depends on
  something nobody read" to state — the multi-option risk does not exist here.
- **The granularity is per-game.** All three are live-observed content-keyed (`SNK Gals' Fighters (USA, Europe).flash`,
  `Sega Rally Championship (USA).bcr`/`.bkr`/`.smpc`, `Pokemon Party Mini (USA).eep`), so a per-game consumer reading
  `granularity=None` the way it reads a `standard` core is correct, not misled.
- **The file names were never claimed.** atlas states no file set for any core without a card: the answer is the literal
  `<rom_stem>.*` observation, or `unknown` with "never guessed" — the fixed `<rom_stem>.srm` is gone
  (`atlas/placement.py`). `standard` sits in exactly the same position, so there is no asymmetry between the two to
  surface. What `standard-dir` adds is that a save may be _several_ files, and the observation already reports all of
  them.

## Audit queue — 2026-07-24 triage scan

Unfiltered `strings` dumps of all 211 shipped `.so` files, searched for save-related option keys and path-format
strings. **A triage hit is a queue position, not a verdict** — every entry stays _unaudited_ until the full method ran.
One method lesson from this pass: pattern-based triage produces false negatives — `genesis_plus_gx` (Sega-CD BRAM,
`.brm` files) matched no pattern because its path strings start mid-word (`_128Kbit_cart.brm`); suspicious systems get a
manual dump check even when the scan is silent.

Card-suspect first (own path construction, or a granularity/root option), then likely-internal hits, then the scanless
rest:

1. ~~`genesis_plus_gx`~~ — audited 2026-07-24 (multi-option, see table)
2. ~~`opera`~~ — audited 2026-07-24 (card, see table)
3. ~~`neocd`~~ — audited 2026-07-24 (multi-option, double persistence, see table)
4. `fbneo` — own subtree `%s%cfbneo%c%s.fs` / `.memcard`, plus `%s%s.nv(ram)`
5. `kronos` — own subtree `%s%ckronos%csaturn%c%s(-ext*).ram`, `%s%ckronos%cstv%c%s.ram`
6. `scummvm` — own save scheme (`pegasus-%s.sav`; ScummVM savepath semantics)
7. `dosbox_pure` — `.pure.zip` saves, "Save Difference Per Content" (manual check; scan hit only weakly)
8. `puae` / `puae2021` — `puae_shared_nvram`, `cd32nvram`
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
   citing the reachable memory switch without tracing which systems reach it. What remains is the card: the CD save is
   the core's own BRAM tree under **three** interacting options (`genesis_plus_gx_system_bram`, `_cart_bram`,
   `_cart_size` — shared region-keyed `scd_E/U/J.brm` or per-game `<rom>.brm`, plus the RAM cart's `<size>Kbit_cart.brm`
   spellings; libretro.c:1383-1500, written by bram_load/bram_save at :3675/:3730), which is the multi-option shape the
   single-option card schema cannot express — the same gap swanstation sits on.
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
6. ~~**`cap32`** and **`hatari`**~~ — read 2026-08-17, one carded, one blocked. `cap32`: the binary's `%s%s%s.sav` is
   the compile-time join of `"%s%s%s." EXT_DIFF_DSK` (retro_disk_control.c:90 at a5d96c5) — a track-level differencing
   file per drive-A floppy, `<save dir>/<image name>.sav` with the extension kept, written only when the disk was
   altered (dsk_diff, slots.c:558), at eject, swap and teardown; carded. `hatari`: the `auto.sav`/`hatari.sav` hits are
   memory-snapshot (savestate) defaults reachable only through the core's own GUI — but the read found the real story:
   write-back into the content itself (floppy.c:599-634 at 7008194, governed by `hatari_writeprotect_floppy`, default
   off), hard-disk content written in place, and Falcon/TT `hatari.nvram` under `$HOME/.hatari` — a root the card format
   cannot state. Audited **multi-option**; the card waits on the mode form that names no file of the core's own.
7. ~~**`pokemini`**~~ — read 2026-08-17, carded: `%s%c%s.eep` joins the save directory and the content's stem
   (libretro.c:556-561), read back at load, written at unload only when the cartridge EEPROM was touched (:1343-1351);
   EEPROM sharing is compiled off. The audit's 07-23 live observation of a content-keyed `.eep` was this chain — the
   standard-dir verdict became the card that states the file.
8. ~~**`scummvm`**, **`dosbox_pure`**, **`desmume`**~~ — the three big reads, done 2026-08-17; **`easyrpg`**,
   **`mednafen_saturn`** (`BSC.MCR`) remain, new since 07-24, and `handy` / `mesen` (`.eeprom*`) stay confirmed where
   the old tiers put them. `dosbox_pure`: carded — the emulated C: drive is a union of read-only content and one
   writable overlay, and the overlay is the save, `<save dir>/<rom_stem>.pure.zip` (DBP_GetSaveFile,
   dosbox_pure_libretro.cpp:826-844 at ed5e809), created on first write, rewritten on a five-second schedule; a
   `.SAVENAME` redirect shares saves between contents, a legacy `.sav` keeps being used unless strict mode forbids it,
   Boot-OS setups add `-CDRIVE.sav` and hash-keyed disks. `desmume`: carded — its backup device writes
   `<save dir>/<rom_stem>.dsv` (mc.cpp:232-235 at 7f05a8d) with the battery path defaulting to the save directory this
   generation fills (path.cpp:196-222); no `.dsv.bak` here, the copy is gated on a Windows-only setting — the 2015
   card's pair shrinks to one file. `scummvm`: audited **standard-dir**, not carded — saves are target-keyed slot files
   in ScummVM's own `savepath` setting (default: the save directory, flat; truth in `<system dir>/scummvm.ini`,
   libretro-os-utils.cpp:64-69, :212-221 at 686cdd1) — a card needs the ini-reading code rule plus #170's mode form.
9. **`melondsds`** — the one flagged core that fills `save_ram`: its `.sav` hits are DSi-NAND-internal paths
   (`0:/title/%08x/%08x/data/…`), and the open question is whether host files appear beside the frontend's `.srm`.

From the card side, `bsnes_hd_beta` joins: its provenance leans on "unchanged in this respect" toward bsnes rather than
tracing its own chain — the thinnest citation among the shipped cards.

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
