# Firmware system assignment — the standing grind list

Which declared firmware files still _inherit_ their system from the core's own `systemname` instead of carrying a
per-file source. The vocabulary switch (map version 2) made every filing an ES-DE id or a declared own spelling; what it
deliberately did not do is invent per-file knowledge. A file on this list is filed correctly _as far as the machine
says_ — and the answer marks it (`system-assignment-derived`, or `core-without-systemname` where nothing says anything)
until an entry here is worked off.

Snapshot: the `.info` set RetroDECK 0.10.9b deploys (`components/retroarch/rd_extras/cores/`: 292 `.info` files, minus
the two shipped template stems `00_example_libretro` and `puzzlescript_libretro` = 290 read), read 2026-08-09 — **173
inherited declarations across 42 uncertain cores** (multiple builds of one emulator counted separately, as the info set
ships them). Regenerate by re-running the derivation over that directory (`atlas.firmware._declarations_in` +
`CoreDeclarations.serves_several_systems`); nothing here comes from any user data.

**What retires an entry.** One of:

1. a `FIRMWARE_SYSTEM_OVERRIDE` row with per-entry evidence (the file's own `firmwareN_desc`, upstream core source, or
   the deployed catalogue launching the core under the file's system) — the Flycast boards and `BIOS.col` went this way;
2. establishing that the core's systems collapse for firmware purposes (every declared file belongs to the mapped id
   anyway), recorded as a note on the core below — the caveat still fires until a per-file rule exists, because the
   generous multi-system reading is deliberate;
3. for `_unknown` cores, any on-machine source for a systemname at all.

An entry whose file belongs to a system **without a catalogue id** (Videoton TVC below) cannot move to an id — it needs
a `SYSTEMS_WITHOUT_CATALOGUE_ID` ruling first, like `bk`/`ti83`/`ep128`.

## No systemname at all (`_unknown`, `core-without-systemname`)

- **skyemu** — 8 of 10 declarations inherited (`dmg_rom.bin`, `dmg0_rom.bin`, `cgb0_boot.bin`, `cgb_agb_boot.bin`,
  `gba_bios.bin`, `nds7.bin`, `nds9.bin`, `firmware.bin`); the descs name GB/GBC/GBA/NDS, so most are cheap override
  candidates — the generic `firmware.bin` is not (deliberately unclaimed, see the override table's note).
- **galaksija** — `galaksija/CHRGEN.BIN`, `ROM1.BIN`, `ROM2.BIN`. The machine (Galaksija) has no catalogue id either;
  needs the own-spelling ruling before any filing.

## Multi-system cores, one filing per file still to establish

Per core: the systemname everything inherits from, and the declared files (paths as spelled, descs abridged).

- **atari800** (`Atari 8-bit Family`) → `atari800`: `ATARIBAS.ROM`, `ATARIOSA.ROM`, `ATARIOSB.ROM`, `ATARIXL.ROM`,
  `BB01R4_OS.ROM`, `XEGAME.ROM`. The 5200 BIOS already has its override; these six are 400/800/XL/XE/XEGS material —
  candidates for `atari800`/`atarixe` per desc.
- **bk** (`BK-0010/BK-0011(M)`) → `bk` (own spelling): the eight `bk/*.ROM` dumps. Model variants of one machine; a
  per-file split (BK-0010 vs 0011M) only matters if the family ever gets ids.
- **bluemsx** (`MSX/SVI/ColecoVision/SG-1000`) → `msx`: `Databases/msxromdb.xml`, `Machines/Shared Roms/MSX.rom`
  (folder-shaped declarations).
- **desmume**, **desmume2015**, **melonds**, **melondsds**, **noods** (`Nintendo DS`) → `nds`: `firmware.bin`,
  `dsi_sd_card.bin`, `nds_sd_card.bin`, and noods' `gba_bios.bin` — that last one is a GBA BIOS filed under `nds` and
  the clearest override candidate of the group.
- **dolphin** (`GameCube / Wii`) → `gc`: `dolphin-emu/Sys/codehandler.bin` (one file serving both systems).
- **ep128emu_core** (`128`) → `ep128` (own spelling): 22 `ep128emu/roms/*` files. The Enterprise 64/128 rows are home;
  the rest inherit wrongly and their descs say so — `cpc464.rom`/`cpc664.rom`/`cpc6128.rom`/`cpc_amsdos.rom` (Amstrad
  CPC → `amstradcpc`), `zx128.rom`/`zx48.rom` (ZX Spectrum → `zxspectrum`), `tvc22_sys.rom`/`tvc22_ext.rom`/
  `tvcfileio.rom`/`tvc_dos12d.rom` (Videoton TVC → **no catalogue id exists**).
- **fbneo_cps12** [info-only] (`CP System I/II`) → `cps`: `fbneo/hiscore.dat`.
- **fceumm** (`Nintendo Entertainment System`) → `nes`: `nes.pal`, `gamegenie.nes` (multi-system by database: NES + FDS;
  the FDS BIOS override already exists, these two are NES-side).
- **flycast**, **flycast_gles2** (`Sega Dreamcast`) → `dreamcast`: `dc/dc_boot.bin` — the boot ROM itself, correct by
  the systemname; the seven board zips moved to overrides. `dc/dc_flash.bin`-class files would land here if a future
  info declares them.
- **fmsx** (`MSX`) → `msx`: ten `*.ROM` files (MSX/MSX2/MSX2+ BIOS family). Multi-system by database (MSX vs MSX2);
  per-file split into ES-DE's `msx1`/`msx2` is possible from the descs if ever wanted.
- **fuse** (`ZX Spectrum (various)`) → `zxspectrum`: eight `fuse/*.rom` clone-machine ROMs (Pentagon, Scorpion).
- **genesis_plus_gx**, **_wide**, **genesis-plus-gx-expanded-rom-size-paprium** (`Sega 8/16-bit (Various)`) → `genesis`:
  `bios_MD.bin`, `sk.bin`, `sk2chip.bin`, `areplay.bin`, `ggenie.bin` (the SMS/GG/CD dumps already carry overrides).
- **hatari** (`Atari ST/STE/TT/Falcon`) → `atarist`: `tos.img`.
- **higan_sfc**, **higan_sfc_balanced** [info-only] (`Super Nintendo Entertainment System`) → `snes`:
  `SGB1.sfc/program.rom`, `SGB2.sfc/program.rom` — folder-shaped SGB dumps; the flat spellings already have overrides.
- **jollycv** (`ColecoVision/CreatiVision/My Vision`) → `colecovision`: `coleco.rom` (ColecoVision BIOS — desc-backed
  override candidate), `bioscv.rom` (CreatiVision BIOS — CreatiVision is ES-DE `crvision`).
- **kronos** (`Saturn`) → `saturn`: `kronos/saturn_bios.bin`, `kronos/stvbios.zip` (ST-V — ES-DE has `stv`),
  `mpr-18811-mx.ic1`, `mpr-19367-mx.ic1` (cartridge ROMs).
- **mednafen_pce**, **mednafen_pce_fast**, **mednafen_supergrafx** (`PC Engine …`) → `pcenginecd`: `syscard3.pce`,
  `syscard2.pce`, `syscard1.pce`, `gexpress.pce` — the filing the ruling chose _because_ of these files; an override per
  file would only restate the systemname result and can wait.
- **mgba**, **vbam** (`Game Boy/Game Boy Color/Game Boy Advance`) → `gba`: `gba_bios.bin` — desc-backed override
  candidate (`gba`), which would silence the derived caveat on both cores.
- **o2em** (`Magnavox Odyssey2 / Philips Videopac+`) → `odyssey2`: `o2rom.bin`, `c52.bin`, `g7400.bin`, `jopac.bin` (the
  G7400 dumps are Videopac+ — ES-DE has `videopac`).
- **parallel_n64**, **parallel_n64_debug** [info-only] (`Nintendo 64`) → `n64`: `64DD_IPL.bin` (64DD — ES-DE has
  `n64dd`).
- **puae**, **puae2021**, **uae4arm** [info-only] (`Amiga`) → `amiga`: the Kickstart family (`kick*.A500`/`.A600`/
  `.A1200`/`.A4000`/`.CDTV`/`.CD32*`). The suffixes name the models; ES-DE has `amiga600`, `amiga1200`, `cdtv`,
  `amigacd32` — the largest single block of cheap desc-backed overrides on the list.
- **quasi88** (`PC-8000 / PC-8800 series`) → `pc88`: ten `quasi88/*.rom` files.
- **same_cdi** (`CD-i`) → `cdimono1`: `same_cdi/bios/cdimono1.zip`, `cdimono2.zip`, `cdibios.zip` — the build declares
  no `cdimono2` id (guard-tested), so all three stay under `cdimono1`.
- **smsplus** (`Sega 8-bit`) → `mastersystem`: `bios.sms` (its `BIOS.col` moved to overrides).
- **snes9x** (`Super Nintendo Entertainment System`) → `snes`: `STBIOS.bin` (Sufami Turbo — ES-DE has `sufami`).
- **tempgba** [info-only] (`Game Boy Advance`) → `gba`: `gba_bios.bin`.
