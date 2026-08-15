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
- **Cores that already carry a rule card** (`flycast`, `opera`). The card wins, and a record beside it would be a second
  declaration of one file set.
- **Cores that are not a default anywhere.** A system may offer several; this list picks the one RetroDECK declares
  first. A core nobody leads with is not excluded from the family — it is just outside what this grind is scoped to.
  Note the scope is a rule for picking _cores_, not for what a record then says: once a core is read, its record covers
  **every system the catalogue offers it for**, because a user who picks the second entry for their arcade library is
  asking about the same core and deserves the same answer.

## The list (2 cores)

Tick a core when all of its systems are in and the vector is green. What is left is not reading: one is read and belongs
to the card family, and the other ships no binary to read.

- [ ] **`mess2015`** — `mess` · the catalogue declares it and no `.so` is shipped, so there is nothing to pin. A marker
      for the day it ships.
- [ ] **`swanstation`** — `psx` · read, and a card candidate: a core option decides the file set (#80)

## Done

Systems as the record states them, with the outcome each turned out to be.

- [x] **`81`** — frontend writes nothing on zx81
- [x] **`a5200`** — frontend writes nothing on atari5200
- [x] **`amiarcadia`** — frontend writes nothing on arcadia
- [x] **`arduous`** — fills `save_ram` on arduboy
- [x] **`atari800`** — frontend writes nothing on atari5200, atari800, atarixe
- [x] **`bluemsx`** — frontend writes nothing on colecovision, msx, msx1, msx2, msxturbor, sg-1000, spectravideo
- [x] **`cap32`** — frontend writes nothing on amstradcpc, gx4000
- [x] **`chailove`** — frontend writes nothing on chailove
- [x] **`desmume`** — frontend writes nothing on nds
- [x] **`dosbox_pure`** — frontend writes nothing on dos, pc, windows3x, windows9x
- [x] **`easyrpg`** — frontend writes nothing on easyrpg
- [x] **`ecwolf`** — frontend writes nothing on ports
- [x] **`fbalpha2012`** — frontend writes nothing on arcade, cps, cps1, cps2, cps3, fba, mame
- [x] **`fbneo`** — read, and it turned out to be outcome 2: it keeps its saves in a subtree of its own, so it carries a
      rule card rather than a record (#123)
- [x] **`freechaf`** — frontend writes nothing on channelf
- [x] **`freeintv`** — frontend writes nothing on intellivision
- [x] **`fuse`** — frontend writes nothing on zxspectrum
- [x] **`gambatte`** — fills `rtc`, `save_ram` on gb, gbc
- [x] **`gearsystem`** — fills `save_ram` on gamegear, mark3, mastersystem, multivision, sg-1000
- [x] **`genesis_plus_gx`** — fills `save_ram` on gamegear, genesis, mark3, mastersystem, megacd, megacdjp, megadrive,
      megadrivejp, segacd, sg-1000
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
- [x] **`same_cdi`** — frontend writes nothing on cdimono1
- [x] **`sameduck`** — frontend writes nothing on megaduck
- [x] **`scummvm`** — frontend writes nothing on scummvm
- [x] **`snes9x`** — fills `rtc`, `save_ram` on satellaview, sfc, snes, snesna, sufami
- [x] **`squirreljme`** — frontend writes nothing on j2me
- [x] **`stella`** — frontend writes nothing on atari2600
- [x] **`theodore`** — frontend writes nothing on moto, to8
- [x] **`tic80`** — fills `save_ram` on tic80
- [x] **`tyrquake`** — frontend writes nothing on quake
- [x] **`uzem`** — fills `save_ram` on uzebox
- [x] **`vecx`** — frontend writes nothing on vectrex
- [x] **`vice_x64sc`** — frontend writes nothing on c64
- [x] **`vice_xplus4`** — frontend writes nothing on plus4
- [x] **`vice_xvic`** — frontend writes nothing on vic20
- [x] **`vircon32`** — frontend writes nothing on vircon32
- [x] **`virtualjaguar`** — read, and it turned out to be outcome 2: it writes both its EEPROM files itself, so it
      carries a rule card rather than a record (#121)
- [x] **`wasm4`** — fills `save_ram` on wasm4
- [x] **`x1`** — frontend writes nothing on x1
