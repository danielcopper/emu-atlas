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
- **Cores that already carry a rule card** (`flycast`, `opera`, `fbneo`, the four MAME builds, the four FB Alpha 2012
  builds that write past the frontend, `cannonball`, `geolith`, `melonds`, `noods`, `nxengine`, `openlara`, `prboom`,
  `race`, `pcsx2`, `virtualjaguar`, the four vitaquake2 builds, `vitaquake3`, the two boom3 builds, `desmume2015`, and
  the three bsnes builds). The card wins, and a record beside it would be a second declaration of one file set — a test
  enforces that no core carries both.

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

Nothing in this tier waits on the card family any more — every read card candidate is carded, the last of them
(`desmume2015`) once the `working_directory` root existed to state it. One core is read and blocked on something else:
`gearlynx` fills `save_ram` and would be a plain record, but no catalogue entry names it, so there is no system to key
the record by — the third tier's whole problem, tracked in #133.

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
- [x] **`boom3`** — outcome 2, and a card: user-named savegames and a written-back `libretro.cfg` in the content's own
      tree; the directory it creates under the save root is never written
- [x] **`boom3_xp`** — outcome 2, and a card: the Resurrection of Evil build of the same tree and layout
- [x] **`bsnes`** — read, and it turned out to be outcome 2: the source says in a comment that it stays out of the
      memory interface on purpose and writes both its files itself, so it carries a rule card
- [x] **`bsnes-jg`** — read, and outcome 2 for a reason of its own: the frontend writes its `.srm` and the core writes
      its `.rtc`, which no record can state together
- [x] **`bsnes_hd_beta`** — read, and outcome 2: the same code as bsnes
- [x] **`bsnes_mercury_accuracy`** — fills `save_ram` on satellaview, sfc, snes, snesna, sufami
- [x] **`cannonball`** — outcome 2, and a card: three score tables under fixed names, shared by everything the core runs
- [x] **`cap32`** — frontend writes nothing on amstradcpc, gx4000
- [x] **`chailove`** — frontend writes nothing on chailove
- [x] **`craft`** — frontend writes nothing on ports
- [x] **`desmume`** — frontend writes nothing on nds
- [x] **`desmume2015`** — outcome 2, and a card: `<rom_stem>.dsv` (with a `.dsv.bak` once overwritten) relative to the
      launching process's working directory — the `working_directory` root's first and only core
- [x] **`dice`** — frontend writes nothing on arcade, mame
- [x] **`dirksimple`** — frontend writes nothing on daphne, laserdisc
- [x] **`dosbox_pure`** — frontend writes nothing on dos, pc, windows3x, windows9x
- [x] **`easyrpg`** — frontend writes nothing on easyrpg
- [x] **`ecwolf`** — frontend writes nothing on ports
- [x] **`fbalpha2012`** — read as outcome 3 first and re-read as outcome 2: the frontend really writes nothing, and the
      core keeps `<driver>.fs` and, behind its hiscore option, `<driver>.hi`. Its record was replaced by a card
- [x] **`fbalpha2012_cps1`** — frontend writes nothing on cps, cps1, fba
- [x] **`fbalpha2012_cps2`** — outcome 2, and a card: `<driver>.fs` plus `<driver>.hi` behind its hiscore option
- [x] **`fbalpha2012_cps3`** — the same card shape under its own option key
- [x] **`fbalpha2012_neogeo`** — outcome 2, and the one build of the family with no high scores: `<driver>.fs` alone
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
- [x] **`geolith`** — outcome 2, and a card: four files of its own per game, one of them under the name RetroArch gives
      a save-RAM file
- [x] **`gpsp`** — fills `save_ram` on gba
- [x] **`handy`** — frontend writes nothing on atarilynx
- [x] **`hatari`** — frontend writes nothing on atarist
- [x] **`holani`** — frontend writes nothing on atarilynx
- [x] **`jollycv`** — fills `save_ram` on crvision
- [x] **`kronos`** — frontend writes nothing on arcade, consolearcade, mame, saturn, saturnjp, stv
- [x] **`lowresnx`** — fills `save_ram` on lowresnx
- [x] **`lutro`** — frontend writes nothing on lutro
- [x] **`mame`** — read, and it turned out to be outcome 2: three trees of its own below the save directory, so it
      carries a rule card rather than a record
- [x] **`mame2000`** — outcome 2, and a card: three trees of its own (`nvram`, `hi`, `cfg`), every file named after the
      driver, nothing refused
- [x] **`mame2003`** — outcome 2, and a card with two modes: an option moves the whole tree under a subfolder or leaves
      it directly in the save directory
- [x] **`mame2003_plus`** — outcome 2, and the same card shape under its own option key
- [x] **`mame2010`** — outcome 2, and a card: six groups, two of which state a directory without its file names
- [x] **`mednafen_lynx`** — frontend writes nothing on atarilynx
- [x] **`mednafen_ngp`** — frontend writes nothing on ngp, ngpc
- [x] **`mednafen_pce`** — fills `save_ram` on pcengine, pcenginecd, supergrafx, tg-cd, tg16
- [x] **`mednafen_pcfx`** — fills `save_ram` on pcfx
- [x] **`mednafen_saturn`** — frontend writes nothing on saturn, saturnjp
- [x] **`mednafen_supergrafx`** — fills `save_ram` on supergrafx, tg16
- [x] **`mednafen_vb`** — fills `save_ram` on virtualboy
- [x] **`mednafen_wswan`** — fills `save_ram` on wonderswan, wonderswancolor
- [x] **`melonds`** — outcome 2, and a card: it builds `<save_dir>/<rom_stem>.sav` itself and hands the path to the
      emulator
- [x] **`melondsds`** — fills `save_ram` on nds
- [x] **`mesen`** — fills `save_ram` on famicom, fds, nes
- [x] **`mesen-s`** — fills `save_ram` on gb, gbc, satellaview, sfc, sgb, snes, snesna
- [x] **`mgba`** — fills `rtc`, `save_ram` on gb, gbc, sgb; fills `save_ram` on gba
- [x] **`mojozork`** — frontend writes nothing on zmachine
- [x] **`mrboom`** — frontend writes nothing on ports
- [x] **`mu`** — frontend writes nothing on palm
- [x] **`mupen64plus_next`** — fills `save_ram` on n64, n64dd
- [x] **`neocd`** — fills `save_ram` on neogeocd, neogeocdjp
- [x] **`nestopia`** — fills `save_ram` on famicom, fds, nes
- [x] **`noods`** — outcome 2, and a card: `<save_dir>/<rom_stem>.sav`, opened as a descriptor the emulator writes into
- [x] **`np2kai`** — frontend writes nothing on pc98
- [x] **`nxengine`** — outcome 2, and a card: five Cave Story profiles under fixed names in the save directory
- [x] **`o2em`** — frontend writes nothing on odyssey2, videopac
- [x] **`openlara`** — outcome 2, and a card: one shared `savegame.dat` under its own `openlara/` in the save directory
- [x] **`parallel_n64`** — fills `save_ram` on n64, n64dd
- [x] **`picodrive`** — fills `save_ram` on gamegear, genesis, mark3, mastersystem, megacd, megacdjp, megadrive,
      megadrivejp, sega32x, sega32xjp, sega32xna, segacd
- [x] **`pokemini`** — frontend writes nothing on pokemini
- [x] **`potator`** — frontend writes nothing on supervision
- [x] **`prboom`** — outcome 2, and a card: `<save_dir>/<rom_stem>/` with eight `prbmsav<slot>.dsg` and a written-back
      `prboom.cfg`, all nameable — the `<rom_stem>` subdir template's first core
- [x] **`prosystem`** — frontend writes nothing on atari7800
- [x] **`puae`** — frontend writes nothing on amiga, amiga1200, amiga600, amigacd32, cdtv
- [x] **`px68k`** — frontend writes nothing on x68000
- [x] **`quasi88`** — frontend writes nothing on pc88
- [x] **`quicknes`** — fills `save_ram` on famicom, nes
- [x] **`race`** — outcome 2, and a card: it writes the Neo Geo Pocket's flash memory as `<save_dir>/<rom_stem>.ngf`,
      naming it by replacing the content file's extension
- [x] **`same_cdi`** — frontend writes nothing on cdimono1
- [x] **`sameboy`** — fills `rtc`, `save_ram` on gb, gbc, sgb
- [x] **`sameduck`** — frontend writes nothing on megaduck
- [x] **`scummvm`** — frontend writes nothing on scummvm
- [x] **`snes9x`** — fills `rtc`, `save_ram` on satellaview, sfc, snes, snesna, sufami
- [x] **`snes9x2005_plus`** — fills `rtc`, `save_ram` on satellaview, sfc, snes, snesna, sufami
- [x] **`snes9x2010`** — fills `rtc`, `save_ram` on satellaview, sfc, snes, snesna, sufami
- [x] **`squirreljme`** — frontend writes nothing on j2me
- [x] **`stella`** — frontend writes nothing on atari2600
- [x] **`superbroswar`** — frontend writes nothing on ports
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
- [x] **`vitaquake2`** — outcome 2, and a card: `<save_dir>/<content_dir_name>/` with a named `config.cfg` beside the
      unnameable `save/` slot tree — the `<content_dir_name>` subdir template's family
- [x] **`vitaquake2-rogue`** — outcome 2, and a card: the Ground Zero build of the same source and layout
- [x] **`vitaquake2-xatrix`** — outcome 2, and a card: the Reckoning build of the same source and layout
- [x] **`vitaquake2-zaero`** — outcome 2, and a card: the Zaero build of the same source and layout
- [x] **`vitaquake3`** — outcome 2, and a card: one `q3config.cfg` in the content's own tree, carrying the arena
      progress as archived cvars; the directory it creates under the save root is never written
- [x] **`wasm4`** — fills `save_ram` on wasm4
- [x] **`x1`** — frontend writes nothing on x1
