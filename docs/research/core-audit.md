# libretro core audit — save behaviour, core by core

The systematic pass behind "atlas answers libretro completely": every core RetroDECK's matrix references gets a verdict,
sourced from an **unfiltered** options scan of the shipped binary, upstream source where behaviour needs proof, and live
observation where available. Filtered/truncated string scans are banned — one produced a wrong LRPS2 card (caught by
review; the option had been grepped away).

Verdicts:

- **standard** — roots at the RetroArch save directory under the standard rule; no card needed for placement. File-set
  nuances, if any, are noted.
- **card** — deviates; covered by a rule card in `atlas/data/core_oddities.json`.
- **multi-option** — directory is standard, but the file set / granularity depends on several interacting options; the
  single-option card schema cannot express it. Candidate for the code-rule-plus-card route (see the schema's own spec).
  Placement answers are correct today; granularity is honestly unstated.
- **suspect** — evidence of deviation exists but is unproven; needs a live run.
- **unaudited** — not yet examined.

| core (`.so` short name) | library_name     | verdict            | evidence                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ----------------------- | ---------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| flycast                 | Flycast          | **card**           | option strings + live observation (`bios/dc` VMUs); per-game filename scheme still [O]                                                                                                                                                                                                                                                                                                                                          |
| pcsx2                   | LRPS2            | **card**           | libretro/ps2 source: `pcsx2_shared_memory_cards` default enabled → shared under `system_directory/pcsx2/memcards`; disabled → `<rom_stem>.ps2` in save dir (`main.cpp:2154`, `Pcsx2Config.cpp:997`)                                                                                                                                                                                                                             |
| mednafen_psx            | Beetle PSX       | **multi-option**   | beetle-psx-libretro source: dir is always `retro_save_directory` (`libretro.c:6549`). Defaults = pure standard (`.srm`; `use_mednafen_memcard0_method=libretro`, `enable_memcard1=disabled`, `shared_memory_cards=disabled`). Options add `<stem>.<idx>.mcr`, or share as `mednafen_psx_libretro_shared.<idx>.mcr`                                                                                                              |
| mednafen_psx_hw         | Beetle PSX HW    | **multi-option**   | same source, `beetle_psx_hw_` option prefix                                                                                                                                                                                                                                                                                                                                                                                     |
| pcsx_rearmed            | PCSX ReARMed     | **multi-option**   | pcsx_rearmed source (`frontend/libretro.c:3715-3757`): dir always save directory. Slot 1 default `libretro` (`.srm`); slot 2 default **`shared`** → `pcsx-card2.mcd` shared beside per-game saves; `serial` mode → `<dash-serial>_<n>.mcd` (identity-keyed — a `<save_id>` hole). Live file carried `disabled`, not a valid value in the current core — version drift, treated as none                                          |
| mupen64plus_next        | Mupen64Plus-Next | **standard**       | live-verified: combined fixed-size `.srm` (296960 B) in the standard dir, two games, two chip types (research doc §12)                                                                                                                                                                                                                                                                                                          |
| mgba                    | mGBA             | **standard**       | live-observed `.srm` in standard dir (+ sort-by-core dir `saves/gba/mGBA` from an earlier layout)                                                                                                                                                                                                                                                                                                                               |
| mednafen_saturn         | Beetle Saturn    | **standard** (dir) | live-observed: core-written `.bcr`/`.bkr`/`.smpc` in the standard dir — file set is core-owned, directory follows the rule                                                                                                                                                                                                                                                                                                      |
| mednafen_ngp            | Beetle NeoPop    | **standard** (dir) | live-observed `.flash` in standard dir                                                                                                                                                                                                                                                                                                                                                                                          |
| pokemini                | PokeMini         | **standard** (dir) | live-observed `.eep` in standard dir                                                                                                                                                                                                                                                                                                                                                                                            |
| swanstation             | SwanStation      | **multi-option**   | swanstation source: dir always `GetSaveDirectory()` (`libretro_host_interface.cpp:419-431`). `MemoryCards_Card1Type` default **Libretro** (`.srm` — pure standard; `libretro_core_options.h:828`), Card2 default None. `Shared` → `duckstation_shared_card_<n>.mcd`; `PerGame` → `<game_code>_<n>.mcd` (serial-keyed, a `<save_id>` hole); `PerGameTitle` → `<title>_<n>.mcd`; `UsePlaylistTitle` folds m3u discs onto one card |
| dolphin                 | dolphin-emu      | **suspect**        | RetroDECK dir_preps under `<retroarch config dir>/saves/dolphin-emu/…` imply a fourth root kind; zero core-written data observed; needs one live run                                                                                                                                                                                                                                                                            |
| azahar                  | Azahar           | **suspect**        | same pattern (`saves/Citra/…` targets)                                                                                                                                                                                                                                                                                                                                                                                          |
| ppsspp                  | PPSSPP           | **suspect**        | same pattern (`saves/PPSSPP/PSP/…` targets); also carries the shipped sort-flip override (research doc §6)                                                                                                                                                                                                                                                                                                                      |
| _remaining ~145 of 159_ |                  | **unaudited**      | queue below (2026-07-24 triage scan)                                                                                                                                                                                                                                                                                                                                                                                            |

## Audit queue — 2026-07-24 triage scan

Unfiltered `strings` dumps of all 211 shipped `.so` files, searched for save-related option keys and path-format
strings. **A triage hit is a queue position, not a verdict** — every entry stays _unaudited_ until the full method ran.
One method lesson from this pass: pattern-based triage produces false negatives — `genesis_plus_gx` (Sega-CD BRAM,
`.brm` files) matched no pattern because its path strings start mid-word (`_128Kbit_cart.brm`); suspicious systems get a
manual dump check even when the scan is silent.

Card-suspect first (own path construction, or a granularity/root option), then likely-internal hits, then the scanless
rest:

1. `genesis_plus_gx` — CD system BRAM + `_*Kbit_cart.brm` cart files (triage false negative, manual check)
2. `opera` (3DO) — `opera_nvram_storage` with `per_game`, `%s.%u.srm`
3. `neocd` — `neocd_per_content_saves`
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

## The verification matrix is data, and maintenance is enforced

`atlas/data/core_audit.json` holds this table's machine-readable core: per core, the verdict and — per arrangement
(retrodeck / emudeck / bare) — the versions the knowledge was verified against (`null` = never verified there). Two
mechanisms keep it honest instead of hoping someone maintains it:

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
- **Cards will grow variants** dispatched on observable facts — e.g. the LRPS2 generation is identified by _which option
  key the core registers_ (`pcsx2_memcard_slot_1` vs `pcsx2_shared_memory_cards`), not by a version string. The planned
  `query_core` extension captures the option definitions the core registers in `retro_set_environment` — which also
  makes option _defaults_ live-read instead of card-declared.
- **A vector per generation, never deleted.** Each supported generation keeps its fixture machine in the conformance
  vectors — that is the guarantee that understanding an old version survives supporting a new one.
- The version matrix records what was _proven_; the caveat marks everything else as unverified rather than wrong.

Method per core:

1. unfiltered `strings` pass over the shipped `.so` for option keys and save-related strings
2. upstream source for anything the strings imply (option defaults, path construction) — cited as `file:line`
3. live observation on a real machine where save data exists
4. verdict + (if deviant) rule card with provenance and per-mode status

Version drift is real and recorded: LRPS2 changed its option scheme between generations (`pcsx2_memcard_slot_1/2` →
`pcsx2_shared_memory_cards`), PCSX ReARMed's live value `disabled` no longer exists in the current core. Cards state
which shipped version they match.
