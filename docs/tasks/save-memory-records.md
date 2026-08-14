# Save-memory records — the cores still to read

A checklist for one grind: reading every core RetroDECK launches by default, so a standard core's savefile answer states
its files instead of an honest `file_set: unknown`.

## Three outcomes, not one

Reading a core settles which of three shapes it has, and the first batch made clear that the middle one is the exception
rather than the rule — nine of the first ten cores fell into the third.

1. **It fills the interface.** One or both of `save_ram` (`.srm`) and `rtc` (`.rtc`), so RetroArch writes the file and
   names it after the content. A record in `atlas/data/save_memory.json` with the ids listed. _mgba, gambatte, arduous._
2. **It writes its own files.** The core ignores the interface and writes past the frontend, with its own names and
   sometimes its own directory. That is a rule card in `core_oddities.json` — binary anchors, live option reads, the
   expensive kind of work. _flycast, opera, LRPS2._
3. **The frontend writes nothing.** The core fills no id, so no `.srm` and no `.rtc` exist. A record with an empty
   `memory_types`, which the answer states as a declared set of no files plus `core-own-writes-unestablished` — because
   whether the core writes its own is a separate question that outcome does not settle. _desmume, dosbox_pure, bluemsx,
   …_

Outcomes 1 and 3 are cheap: one function in the core's source decides it, and both are recorded in the same file.
Outcome 2 is a round of its own and belongs to the card family, so a core that turns out to be one is ticked here and
opened there.

## What a record has to carry

Which ids a core fills for one system, read out of its source at the revision the installed binary names, pinned with
`verified_core`, with a `file:line` citation. It is an upper bound over the system, never a claim about one game —
whether _this_ cartridge has a battery is a fact about the game. `atlas/data/README.md` carries the format.

**A core is done when** every system it is the default for has an entry, its loader tests pass, and a vector covers the
answer.

## Not on this list, and why

- **Standalone emulators.** 31 of the declared systems launch a full program (Dolphin, PCSX2, PPSSPP, Cemu, Vita3K, …)
  rather than a core. Nothing here applies to them: they write their saves by their own rules, not through RetroArch.
- **Cores that already carry a rule card** (`flycast`, `opera`). The card wins, and a record beside it would be a second
  declaration of one file set.
- **Cores that are not a default.** A system may offer several; this list follows the one RetroDECK declares first. A
  non-default core is not excluded from the family — it is just outside what this grind is scoped to.

## Order

Alphabetical, with `mame` last: it is the default for 23 systems whose save behaviour has little in common, so it is a
round of its own rather than one line among many. The mednafen cores share a source tree and are quickest read together.

## The list (30 cores)

Tick a core when all of its systems are in and the vector is green.

- [ ] **`mednafen_vb`** — `virtualboy`
- [ ] **`mednafen_wswan`** — `wonderswan`, `wonderswancolor`
- [ ] **`mess2015`** — `mess`
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

Systems as the record states them, with the outcome each turned out to be.

- [x] **`81`** — frontend writes nothing on zx81
- [x] **`a5200`** — frontend writes nothing on atari5200
- [x] **`amiarcadia`** — frontend writes nothing on arcadia
- [x] **`arduous`** — fills `save_ram` on arduboy
- [x] **`atari800`** — frontend writes nothing on atari800, atarixe
- [x] **`bluemsx`** — frontend writes nothing on colecovision, msx, msx1, msx2, msxturbor, spectravideo
- [x] **`cap32`** — frontend writes nothing on amstradcpc, gx4000
- [x] **`chailove`** — frontend writes nothing on chailove
- [x] **`desmume`** — frontend writes nothing on nds
- [x] **`dosbox_pure`** — frontend writes nothing on dos, pc, windows3x, windows9x
- [x] **`easyrpg`** — frontend writes nothing on easyrpg
- [x] **`ecwolf`** — frontend writes nothing on ports
- [x] **`fbalpha2012`** — frontend writes nothing on fba
- [x] **`fbneo`** — frontend writes nothing on fbneo, neogeo
- [x] **`freechaf`** — frontend writes nothing on channelf
- [x] **`freeintv`** — frontend writes nothing on intellivision
- [x] **`fuse`** — frontend writes nothing on zxspectrum
- [x] **`gambatte`** — fills `rtc`, `save_ram` on gb, gbc
- [x] **`gearsystem`** — fills `save_ram` on multivision
- [x] **`genesis_plus_gx`** — fills `save_ram` on gamegear, genesis, mark3, mastersystem, megacd, megacdjp, megadrive,
      megadrivejp, segacd, sg-1000
- [x] **`handy`** — frontend writes nothing on atarilynx
- [x] **`hatari`** — frontend writes nothing on atarist
- [x] **`jollycv`** — fills `save_ram` on crvision
- [x] **`kronos`** — frontend writes nothing on stv
- [x] **`lowresnx`** — fills `save_ram` on lowresnx
- [x] **`lutro`** — frontend writes nothing on lutro
- [x] **`mednafen_ngp`** — frontend writes nothing on ngp, ngpc
- [x] **`mednafen_pce`** — fills `save_ram` on pcengine, pcenginecd, tg-cd, tg16
- [x] **`mednafen_pcfx`** — fills `save_ram` on pcfx
- [x] **`mednafen_saturn`** — frontend writes nothing on saturn, saturnjp
- [x] **`mednafen_supergrafx`** — fills `save_ram` on supergrafx
- [x] **`mesen`** — fills `save_ram` on famicom, fds, nes
- [x] **`mesen-s`** — fills `save_ram` on sgb
- [x] **`mgba`** — fills `rtc`, `save_ram` on gb, gba, gbc
- [x] **`mojozork`** — frontend writes nothing on zmachine
- [x] **`mu`** — frontend writes nothing on palm
- [x] **`mupen64plus_next`** — fills `save_ram` on n64
- [x] **`neocd`** — fills `save_ram` on neogeocd, neogeocdjp
- [x] **`np2kai`** — frontend writes nothing on pc98
- [x] **`o2em`** — frontend writes nothing on odyssey2, videopac
- [x] **`parallel_n64`** — fills `save_ram` on n64dd
