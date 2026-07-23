# libretro core audit — save behaviour, core by core

The systematic pass behind "atlas answers libretro completely": every core RetroDECK's matrix references gets a
verdict, sourced from an **unfiltered** options scan of the shipped binary, upstream source where behaviour needs
proof, and live observation where available. Filtered/truncated string scans are banned — one produced a wrong LRPS2
card (caught by review; the option had been grepped away).

Verdicts:

- **standard** — roots at the RetroArch save directory under the standard rule; no card needed for placement. File-set
  nuances, if any, are noted.
- **card** — deviates; covered by a rule card in `atlas/data/core_oddities.json`.
- **multi-option** — directory is standard, but the file set / granularity depends on several interacting options; the
  single-option card schema cannot express it. Candidate for the code-rule-plus-card route (see the schema's own
  spec). Placement answers are correct today; granularity is honestly unstated.
- **suspect** — evidence of deviation exists but is unproven; needs a live run.
- **unaudited** — not yet examined.

| core (`.so` short name)  | library_name     | verdict          | evidence |
| ------------------------ | ---------------- | ---------------- | -------- |
| flycast                  | Flycast          | **card**         | option strings + live observation (`bios/dc` VMUs); per-game filename scheme still [O] |
| pcsx2                    | LRPS2            | **card**         | libretro/ps2 source: `pcsx2_shared_memory_cards` default enabled → shared under `system_directory/pcsx2/memcards`; disabled → `<rom_stem>.ps2` in save dir (`main.cpp:2154`, `Pcsx2Config.cpp:997`) |
| mednafen_psx             | Beetle PSX       | **multi-option** | beetle-psx-libretro source: dir is always `retro_save_directory` (`libretro.c:6549`). Defaults = pure standard (`.srm`; `use_mednafen_memcard0_method=libretro`, `enable_memcard1=disabled`, `shared_memory_cards=disabled`). Options add `<stem>.<idx>.mcr`, or share as `mednafen_psx_libretro_shared.<idx>.mcr` |
| mednafen_psx_hw          | Beetle PSX HW    | **multi-option** | same source, `beetle_psx_hw_` option prefix |
| pcsx_rearmed             | PCSX ReARMed     | **multi-option** | pcsx_rearmed source (`frontend/libretro.c:3715-3757`): dir always save directory. Slot 1 default `libretro` (`.srm`); slot 2 default **`shared`** → `pcsx-card2.mcd` shared beside per-game saves; `serial` mode → `<dash-serial>_<n>.mcd` (identity-keyed — a `<save_id>` hole). Live file carried `disabled`, not a valid value in the current core — version drift, treated as none |
| mupen64plus_next         | Mupen64Plus-Next | **standard**     | live-verified: combined fixed-size `.srm` (296960 B) in the standard dir, two games, two chip types (research doc §12) |
| mgba                     | mGBA             | **standard**     | live-observed `.srm` in standard dir (+ sort-by-core dir `saves/gba/mGBA` from an earlier layout) |
| mednafen_saturn          | Beetle Saturn    | **standard** (dir) | live-observed: core-written `.bcr`/`.bkr`/`.smpc` in the standard dir — file set is core-owned, directory follows the rule |
| mednafen_ngp             | Beetle NeoPop    | **standard** (dir) | live-observed `.flash` in standard dir |
| pokemini                 | PokeMini         | **standard** (dir) | live-observed `.eep` in standard dir |
| swanstation              | SwanStation      | **unaudited**    | next in queue (psx alternative) |
| dolphin                  | dolphin-emu      | **suspect**      | RetroDECK dir_preps under `<retroarch config dir>/saves/dolphin-emu/…` imply a fourth root kind; zero core-written data observed; needs one live run |
| azahar                   | Azahar           | **suspect**      | same pattern (`saves/Citra/…` targets) |
| ppsspp                   | PPSSPP           | **suspect**      | same pattern (`saves/PPSSPP/PSP/…` targets); also carries the shipped sort-flip override (research doc §6) |
| _remaining ~145 of 159_  |                  | **unaudited**    | queue: cores with `memcard`/`save`-related options first (options scan), then the rest |

Method per core:

1. unfiltered `strings` pass over the shipped `.so` for option keys and save-related strings
2. upstream source for anything the strings imply (option defaults, path construction) — cited as `file:line`
3. live observation on a real machine where save data exists
4. verdict + (if deviant) rule card with provenance and per-mode status

Version drift is real and recorded: LRPS2 changed its option scheme between generations
(`pcsx2_memcard_slot_1/2` → `pcsx2_shared_memory_cards`), PCSX ReARMed's live value `disabled` no longer exists in the
current core. Cards state which shipped version they match.
