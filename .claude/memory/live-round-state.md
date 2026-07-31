---
name: live-round-state
type: project
---

**Current state of the live-verification round — update this file whenever a run completes or the plan changes.**

**Why:** The round spans many sessions and the user's play time; without this file a fresh agent resumes source audits
instead of the in-flight run.

**State (2026-07-31):**

- **In flight: PSP → `ppsspp_libretro` (suspect run).** Plan: Ys — The Oath in Felghana (menu-save, fastest), via ES-DE
  with the psp alternative emulator set to "PPSSPP" (the entry WITHOUT "Standalone"). A God-of-War attempt ran the wrong
  emulator (standalone via decky launcher) and was abandoned. Pre-run evidence: the dir_prep skeletons under
  `config/retroarch/saves/{PPSSPP,Citra,dolphin-emu}/` contain only texture/mod dirs (`TEXTURES`, `load/mods`,
  `User/Load`) — the fourth-root hypothesis is wobbling; the save decides. Side finding to bank later: the standalone
  PPSSPP run may have written memstick data (baseline predates it).
- **Then, one at a time:** the remaining suspects (psx → Beetle PSX defaults, psx → PCSX ReARMed shared card2), then the
  wave-1 trio, which needs the user to add content first:
  - 3DO: a game into `roms/3do/` + BIOS `panafz10.bin` (or equivalent) into `bios/`
  - Sega CD: a game into `roms/segacd/` + `bios_CD_E.bin`/`bios_CD_U.bin`/`bios_CD_J.bin` into `bios/`
  - Neo Geo CD: a game into `roms/neogeocd/` + NeoCD BIOS files into `bios/neocd/` — this run also settles the
    load-order question (double persistence) that blocks the NeoCD card.
- Next source-audit wave after the round (small waves, 2–3 cores/PR): `fbneo`, `kronos`, then `scummvm`, `dosbox_pure`
  (queue in `docs/research/core-audit.md`).

**How to apply:** Read [[live-verification-protocol]] for the run mechanics. After each run, rewrite the State block
above — stale state here is worse than none.
