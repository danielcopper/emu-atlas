# Save-memory records — the cores still to read

A checklist for one grind: reading every core on the machine, so a standard core's savefile answer states its files
instead of an honest `file_set: unknown`. It started with the cores RetroDECK launches by default and did not stop there
— what a frontend leads with is a menu position, not a property of the core, and a user who picks the second entry is
asking the same question.

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
Outcome 2 is a round of its own and belongs to the card family, so a core that turns out to be one stays open on this
list, marked as the card candidate it is, and gets its issue over there.

## What a record has to carry

Which ids a core fills for one system, read out of its source at the revision the installed binary names, pinned with
`verified_core`, with a `file:line` citation. It is an upper bound over the system, never a claim about one game —
whether _this_ cartridge has a battery is a fact about the game. `atlas/data/README.md` carries the format.

**A core is done when** every system the catalogue offers it for has an entry, its loader tests pass, and a vector
covers the answer.

## Not on this list, and why

- **Standalone emulators.** 31 of the declared systems launch a full program (Dolphin, PCSX2, PPSSPP, Cemu, Vita3K, …)
  rather than a core. Nothing here applies to them: they write their saves by their own rules, not through RetroArch.
- **Cores that already carry a rule card** (`flycast`, `opera`, `fbneo`, `mame`, `pcsx2`, `virtualjaguar`). The card
  wins, and a record beside it would be a second declaration of one file set — a test enforces that no core carries
  both.

Once a core is read, its record covers **every system the catalogue offers it for**, not only the ones it leads: leading
is a menu position, and a user who picks the second entry is asking about the same core.

## The list

### Cores RetroDECK launches by default — done but for two

Neither is reading. One is read and belongs to the card family; the other ships no binary to read.

- [ ] **`mess2015`** — `mess` · the catalogue declares it and no `.so` is shipped, so there is nothing to pin. A marker
      for the day it ships.
- [ ] **`swanstation`** — `psx` · read, and a card candidate: two options, one per memory card slot, decide the file
      set, which a card's single governing option cannot express (#80)

### Cores the catalogue offers but never leads with

The round that follows, and the reason it is worth it: a frontend lists several emulators per system and the user picks.
Reading these is the same work as reading a default core, and each one answers for every system the catalogue offers it
for. Measured on the reference machine, 87 catalogued cores had no record and no card when this round started; the order
is by what a person is likely to choose, not alphabetical.

Some of this tier is read and waiting on the card family rather than on a reading. The arcade round turned out to be
mostly this: nine of its twelve cores write past the frontend, which is the opposite ratio to every round before it and
follows from what the hardware is — an arcade board's memory is NVRAM the machine owns, not a cartridge save the
frontend can name.

- [ ] **`noods`** — `gba` · fills no id the frontend writes and keeps its own `<save_dir>/<rom_stem>.sav`
- [ ] **`mame2000`** — trees of its own under `<save_dir>/mame2000/` (`nvram`, `hi`, `cfg`, `memcard`, `snap`)
- [ ] **`mame2003`** — the same shape, its layout built from the frontend's save path
- [ ] **`mame2003_plus`** — the same shape
- [ ] **`mame2010`** — trees of its own under `<save_dir>/<core name>/` (`nvram`, `hi`, `cfg`, `memcard`, `diff`, …)
- [ ] **`geolith`** — writes four files of its own per game (`.nv`, `.srm`, `.mcr`, `.brm`), one of which collides with
      the name RetroArch would give a save-RAM file
- [ ] **`fbalpha2012_cps2`** — writes `<save_dir>/<driver>.fs` at unload, keyed by the driver rather than the ROM file
- [ ] **`fbalpha2012_cps3`** — the same
- [ ] **`fbalpha2012_neogeo`** — the same
- [ ] **`fbalpha2012`** — already carries a record, and it is not wrong: the frontend really does write nothing, and the
      record says so with `core-own-writes-unestablished` beside it. That open half is now answered — the core writes
      `<save_dir>/<driver>.fs` itself — so the record is due to be replaced by a card.

### Cores shipped but never catalogued

Roughly 50 more `.so` files ship that no ES-DE catalogue entry names. They are **not** out of scope: a bare RetroArch is
a frontend of its own, atlas answers for it, and a user there loads any shipped core by hand. Same reading, no catalogue
line to hang it off — so they come last, not never.

## Done

Systems as the record states them, with the outcome each turned out to be.

- [x] **`81`** — frontend writes nothing on zx81
- [x] **`DoubleCherryGB`** — fills `rtc`, `save_ram` on gb, gbc
- [x] **`a5200`** — frontend writes nothing on atari5200
- [x] **`amiarcadia`** — frontend writes nothing on arcadia
- [x] **`arduous`** — fills `save_ram` on arduboy
- [x] **`atari800`** — frontend writes nothing on atari5200, atari800, atarixe
- [x] **`bluemsx`** — frontend writes nothing on colecovision, msx, msx1, msx2, msxturbor, sg-1000, spectravideo
- [x] **`bsnes`** — read, and it turned out to be outcome 2: the source says in a comment that it stays out of the
      memory interface on purpose and writes both its files itself, so it carries a rule card
- [x] **`bsnes-jg`** — read, and outcome 2 for a reason of its own: the frontend writes its `.srm` and the core writes
      its `.rtc`, which no record can state together
- [x] **`bsnes_hd_beta`** — read, and outcome 2: the same code as bsnes
- [x] **`bsnes_mercury_accuracy`** — fills `save_ram` on satellaview, sfc, snes, snesna, sufami
- [x] **`cap32`** — frontend writes nothing on amstradcpc, gx4000
- [x] **`chailove`** — frontend writes nothing on chailove
- [x] **`desmume`** — frontend writes nothing on nds
- [x] **`dice`** — frontend writes nothing on arcade, mame
- [x] **`dirksimple`** — frontend writes nothing on daphne, laserdisc
- [x] **`dosbox_pure`** — frontend writes nothing on dos, pc, windows3x, windows9x
- [x] **`easyrpg`** — frontend writes nothing on easyrpg
- [x] **`ecwolf`** — frontend writes nothing on ports
- [x] **`fbalpha2012`** — frontend writes nothing on arcade, cps, cps1, cps2, cps3, fba, mame
- [x] **`fbalpha2012_cps1`** — frontend writes nothing on cps, cps1, fba
- [x] **`fbneo`** — read, and it turned out to be outcome 2: it keeps its saves in a subtree of its own, so it carries a
      rule card rather than a record (#123)
- [x] **`fceumm`** — fills `save_ram` on famicom, fds, nes
- [x] **`freechaf`** — frontend writes nothing on channelf
- [x] **`freeintv`** — frontend writes nothing on intellivision
- [x] **`fuse`** — frontend writes nothing on zxspectrum
- [x] **`gambatte`** — fills `rtc`, `save_ram` on gb, gbc
- [x] **`gearboy`** — fills `rtc`, `save_ram` on gb, gbc
- [x] **`gearsystem`** — fills `save_ram` on gamegear, mark3, mastersystem, multivision, sg-1000
- [x] **`genesis_plus_gx`** — fills `save_ram` on gamegear, genesis, mark3, mastersystem, megacd, megacdjp, megadrive,
      megadrivejp, segacd, sg-1000
- [x] **`gpsp`** — fills `save_ram` on gba
- [x] **`handy`** — frontend writes nothing on atarilynx
- [x] **`hatari`** — frontend writes nothing on atarist
- [x] **`jollycv`** — fills `save_ram` on crvision
- [x] **`kronos`** — frontend writes nothing on arcade, consolearcade, mame, saturn, saturnjp, stv
- [x] **`lowresnx`** — fills `save_ram` on lowresnx
- [x] **`lutro`** — frontend writes nothing on lutro
- [x] **`mame`** — read, and it turned out to be outcome 2: three trees of its own below the save directory, so it
      carries a rule card rather than a record
- [x] **`mednafen_ngp`** — frontend writes nothing on ngp, ngpc
- [x] **`mednafen_pce`** — fills `save_ram` on pcengine, pcenginecd, supergrafx, tg-cd, tg16
- [x] **`mednafen_pcfx`** — fills `save_ram` on pcfx
- [x] **`mednafen_saturn`** — frontend writes nothing on saturn, saturnjp
- [x] **`mednafen_supergrafx`** — fills `save_ram` on supergrafx, tg16
- [x] **`mednafen_vb`** — fills `save_ram` on virtualboy
- [x] **`mednafen_wswan`** — fills `save_ram` on wonderswan, wonderswancolor
- [x] **`mesen`** — fills `save_ram` on famicom, fds, nes
- [x] **`mesen-s`** — fills `save_ram` on gb, gbc, satellaview, sfc, sgb, snes, snesna
- [x] **`mgba`** — fills `rtc`, `save_ram` on gb, gbc, sgb; fills `save_ram` on gba
- [x] **`mojozork`** — frontend writes nothing on zmachine
- [x] **`mu`** — frontend writes nothing on palm
- [x] **`mupen64plus_next`** — fills `save_ram` on n64, n64dd
- [x] **`neocd`** — fills `save_ram` on neogeocd, neogeocdjp
- [x] **`nestopia`** — fills `save_ram` on famicom, fds, nes
- [x] **`np2kai`** — frontend writes nothing on pc98
- [x] **`o2em`** — frontend writes nothing on odyssey2, videopac
- [x] **`parallel_n64`** — fills `save_ram` on n64, n64dd
- [x] **`picodrive`** — fills `save_ram` on gamegear, genesis, mark3, mastersystem, megacd, megacdjp, megadrive,
      megadrivejp, sega32x, sega32xjp, sega32xna, segacd
- [x] **`pokemini`** — frontend writes nothing on pokemini
- [x] **`potator`** — frontend writes nothing on supervision
- [x] **`prosystem`** — frontend writes nothing on atari7800
- [x] **`puae`** — frontend writes nothing on amiga, amiga1200, amiga600, amigacd32, cdtv
- [x] **`px68k`** — frontend writes nothing on x68000
- [x] **`quasi88`** — frontend writes nothing on pc88
- [x] **`quicknes`** — fills `save_ram` on famicom, nes
- [x] **`same_cdi`** — frontend writes nothing on cdimono1
- [x] **`sameboy`** — fills `rtc`, `save_ram` on gb, gbc, sgb
- [x] **`sameduck`** — frontend writes nothing on megaduck
- [x] **`scummvm`** — frontend writes nothing on scummvm
- [x] **`snes9x`** — fills `rtc`, `save_ram` on satellaview, sfc, snes, snesna, sufami
- [x] **`snes9x2005_plus`** — fills `rtc`, `save_ram` on satellaview, sfc, snes, snesna, sufami
- [x] **`snes9x2010`** — fills `rtc`, `save_ram` on satellaview, sfc, snes, snesna, sufami
- [x] **`squirreljme`** — frontend writes nothing on j2me
- [x] **`stella`** — frontend writes nothing on atari2600
- [x] **`tgbdual`** — fills `rtc`, `save_ram` on gb, gbc
- [x] **`theodore`** — frontend writes nothing on moto, to8
- [x] **`tic80`** — fills `save_ram` on tic80
- [x] **`tyrquake`** — frontend writes nothing on quake
- [x] **`uzem`** — fills `save_ram` on uzebox
- [x] **`vba_next`** — fills `save_ram` on gba
- [x] **`vbam`** — fills `rtc`, `save_ram` on gb, gbc; fills `save_ram` on gba
- [x] **`vecx`** — frontend writes nothing on vectrex
- [x] **`vice_x64sc`** — frontend writes nothing on c64
- [x] **`vice_xplus4`** — frontend writes nothing on plus4
- [x] **`vice_xvic`** — frontend writes nothing on vic20
- [x] **`vircon32`** — frontend writes nothing on vircon32
- [x] **`virtualjaguar`** — read, and it turned out to be outcome 2: it writes both its EEPROM files itself, so it
      carries a rule card rather than a record (#121)
- [x] **`wasm4`** — fills `save_ram` on wasm4
- [x] **`x1`** — frontend writes nothing on x1
