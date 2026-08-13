# Save-memory records — the cores still to read

A checklist for one grind: giving every core RetroDECK launches by default a record in `atlas/data/save_memory.json`, so
a standard core's savefile answer names its files instead of stating an honest `file_set: unknown`.

## What a line here means

A record says which libretro memory ids a core fills for one system — `save_ram` (`.srm`), `rtc` (`.rtc`), or both.
RetroArch composes the names; the core only decides whether there is anything to write, and it decides that per
cartridge, after content load. So a record is read out of the core's own source at the revision the installed binary
names, pinned to it with `verified_core`, and it is an upper bound over the system rather than a claim about any one
game. `atlas/data/README.md` carries the format and the rules.

**A core is done when** every system it is the default for has an entry with a `file:line` citation at the pinned
revision, its loader tests pass, and a vector covers the answer.

## Not on this list, and why

- **Standalone emulators.** 31 of the declared systems launch a full program (Dolphin, PCSX2, PPSSPP, Cemu, Vita3K, …)
  rather than a core. Nothing here applies to them: they write their saves by their own rules, not through RetroArch.
- **Cores with a rule card** (`flycast`, `opera`). A card already declares that core's files, and it wins — two
  declarations of one file set would be a contradiction no client could resolve.
- **Cores that are not a default.** A system may offer several; this list follows the one RetroDECK declares first,
  which is what launches when nobody picked another. A non-default core is not excluded from the family — it is just not
  what this grind is scoped to.

## Order

Alphabetical, with `mame` last: it is the default for 23 systems whose save behaviour has little in common, so it is a
round of its own rather than one line among many. The mednafen cores share a source tree and are quickest read together.

## The list (69 cores)

Tick a core when all of its systems are in and the vector is green.

- [ ] **`81`** — `zx81`
- [ ] **`a5200`** — `atari5200`
- [ ] **`amiarcadia`** — `arcadia`
- [ ] **`arduous`** — `arduboy`
- [ ] **`atari800`** — `atari800`, `atarixe`
- [ ] **`bluemsx`** — `colecovision`, `msx`, `msx1`, `msx2`, `msxturbor`, `spectravideo`
- [ ] **`cap32`** — `amstradcpc`, `gx4000`
- [ ] **`chailove`** — `chailove`
- [ ] **`desmume`** — `nds`
- [ ] **`dosbox_pure`** — `dos`, `pc`, `windows3x`, `windows9x`
- [ ] **`easyrpg`** — `easyrpg`
- [ ] **`ecwolf`** — `ports`
- [ ] **`fbalpha2012`** — `fba`
- [ ] **`fbneo`** — `fbneo`, `neogeo`
- [ ] **`freechaf`** — `channelf`
- [ ] **`freeintv`** — `intellivision`
- [ ] **`fuse`** — `zxspectrum`
- [ ] **`gearsystem`** — `multivision`
- [ ] **`genesis_plus_gx`** — `gamegear`, `genesis`, `mark3`, `mastersystem`, `megacd`, `megacdjp`, `megadrive`,
      `megadrivejp`, `segacd`, `sg-1000`
- [ ] **`handy`** — `atarilynx`
- [ ] **`hatari`** — `atarist`
- [ ] **`jollycv`** — `crvision`
- [ ] **`kronos`** — `stv`
- [ ] **`lowresnx`** — `lowresnx`
- [ ] **`lutro`** — `lutro`
- [ ] **`mednafen_ngp`** — `ngp`, `ngpc`
- [ ] **`mednafen_pce`** — `pcengine`, `pcenginecd`, `tg-cd`, `tg16`
- [ ] **`mednafen_pcfx`** — `pcfx`
- [ ] **`mednafen_saturn`** — `saturn`, `saturnjp`
- [ ] **`mednafen_supergrafx`** — `supergrafx`
- [ ] **`mednafen_vb`** — `virtualboy`
- [ ] **`mednafen_wswan`** — `wonderswan`, `wonderswancolor`
- [ ] **`mesen`** — `famicom`, `fds`, `nes`
- [ ] **`mesen-s`** — `sgb`
- [ ] **`mess2015`** — `mess`
- [ ] **`mojozork`** — `zmachine`
- [ ] **`mu`** — `palm`
- [ ] **`mupen64plus_next`** — `n64`
- [ ] **`neocd`** — `neogeocd`, `neogeocdjp`
- [ ] **`np2kai`** — `pc98`
- [ ] **`o2em`** — `odyssey2`, `videopac`
- [ ] **`parallel_n64`** — `n64dd`
- [ ] **`picodrive`** — `sega32x`, `sega32xjp`, `sega32xna`
- [ ] **`pokemini`** — `pokemini`
- [ ] **`potator`** — `supervision`
- [ ] **`prosystem`** — `atari7800`
- [ ] **`puae`** — `amiga`, `amiga1200`, `amiga600`, `amigacd32`, `cdtv`
- [ ] **`px68k`** — `x68000`
- [ ] **`quasi88`** — `pc88`
- [ ] **`same_cdi`** — `cdimono1`
- [ ] **`sameduck`** — `megaduck`
- [ ] **`scummvm`** — `scummvm`
- [ ] **`snes9x`** — `satellaview`, `sfc`, `snes`, `snesna`, `sufami`
- [ ] **`squirreljme`** — `j2me`
- [ ] **`stella`** — `atari2600`
- [ ] **`swanstation`** — `psx`
- [ ] **`theodore`** — `moto`, `to8`
- [ ] **`tic80`** — `tic80`
- [ ] **`tyrquake`** — `quake`
- [ ] **`uzem`** — `uzebox`
- [ ] **`vecx`** — `vectrex`
- [ ] **`vice_x64sc`** — `c64`
- [ ] **`vice_xplus4`** — `plus4`
- [ ] **`vice_xvic`** — `vic20`
- [ ] **`vircon32`** — `vircon32`
- [ ] **`virtualjaguar`** — `atarijaguar`
- [ ] **`wasm4`** — `wasm4`
- [ ] **`x1`** — `x1`
- [ ] **`mame`** — `apple2`, `apple2gs`, `arcade`, `astrocde`, `consolearcade`, `cps`, `cps1`, `cps2`, `cps3`, `daphne`,
      `fmtowns`, `gamate`, `gameandwatch`, `gamecom`, `gmaster`, `laserdisc`, `lcdgames`, `mame`, `model2`, `pv1000`,
      `scv`, `supracan`, `vsmile`

## Done

Systems as the record states them, which can be more than the ones the core is the default for.

- [x] **`gambatte`** — `gb`, `gbc`
- [x] **`mgba`** — `gb`, `gba`, `gbc`
