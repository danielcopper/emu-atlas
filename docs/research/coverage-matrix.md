# Coverage matrix — GENERATED, do not edit

Regenerate with `python scripts/generate_coverage_matrix.py` (then `deno fmt`). Facts come from
`atlas/data/core_audit.json`; the row set comes from RetroDECK's bundled `es_systems.xml`, so unaudited emulators appear
automatically as the work list. Cells: ✔ verified (with the arrangement version the knowledge was proven against), ✖ not
verified, — not applicable. Verdicts are defined in `docs/research/core-audit.md`.

**Status:** libretro 11/159 audited · standalone 0/22 audited

## libretro cores

| emulator                                    | systems                                             | verdict      | RetroDECK | EmuDeck | RetroArch (bare) |
| ------------------------------------------- | --------------------------------------------------- | ------------ | --------- | ------- | ---------------- |
| `flycast`                                   | arcade, atomiswave, consolearcade, dreamcast, … (8) | card         | ✔ 0.10.9b | ✖       | ✖                |
| `mednafen_ngp`                              | ngp, ngpc                                           | standard-dir | ✔ 0.10.9b | ✖       | ✖                |
| `mednafen_psx`                              | psx                                                 | multi-option | ✔ 0.10.9b | ✖       | ✖                |
| `mednafen_psx_hw`                           | psx                                                 | multi-option | ✔ 0.10.9b | ✖       | ✖                |
| `mednafen_saturn`                           | saturn, saturnjp                                    | standard-dir | ✔ 0.10.9b | ✖       | ✖                |
| `mgba`                                      | gb, gba, gbc, sgb                                   | standard     | ✔ 0.10.9b | ✖       | ✖                |
| `mupen64plus_next`                          | n64, n64dd                                          | standard     | ✔ 0.10.9b | ✖       | ✖                |
| `pcsx2`                                     | ps2                                                 | card         | ✔ 0.10.9b | ✖       | ✖                |
| `pcsx_rearmed`                              | psx                                                 | multi-option | ✔ 0.10.9b | ✖       | ✖                |
| `pokemini`                                  | pokemini                                            | standard-dir | ✔ 0.10.9b | ✖       | ✖                |
| `swanstation`                               | psx                                                 | multi-option | ✔ 0.10.9b | ✖       | ✖                |
| `81`                                        | zx81                                                | unaudited    | ✖         | ✖       | ✖                |
| `DoubleCherryGB`                            | gb, gbc                                             | unaudited    | ✖         | ✖       | ✖                |
| `a5200`                                     | atari5200                                           | unaudited    | ✖         | ✖       | ✖                |
| `amiarcadia`                                | arcadia                                             | unaudited    | ✖         | ✖       | ✖                |
| `ardens`                                    | arduboy                                             | unaudited    | ✖         | ✖       | ✖                |
| `arduous`                                   | arduboy                                             | unaudited    | ✖         | ✖       | ✖                |
| `atari800`                                  | atari5200, atari800, atarixe                        | unaudited    | ✖         | ✖       | ✖                |
| `azahar`                                    | n3ds                                                | unaudited    | ✖         | ✖       | ✖                |
| `b2`                                        | bbcmicro                                            | unaudited    | ✖         | ✖       | ✖                |
| `blastem`                                   | genesis, megadrive, megadrivejp                     | unaudited    | ✖         | ✖       | ✖                |
| `bluemsx`                                   | colecovision, msx, msx1, msx2, … (7)                | unaudited    | ✖         | ✖       | ✖                |
| `boom3`                                     | doom                                                | unaudited    | ✖         | ✖       | ✖                |
| `boom3_xp`                                  | doom                                                | unaudited    | ✖         | ✖       | ✖                |
| `bsnes`                                     | gb, gbc, satellaview, sfc, … (7)                    | unaudited    | ✖         | ✖       | ✖                |
| `bsnes-jg`                                  | satellaview, sfc, snes, snesna, … (5)               | unaudited    | ✖         | ✖       | ✖                |
| `bsnes_hd_beta`                             | satellaview, sfc, snes, snesna, … (5)               | unaudited    | ✖         | ✖       | ✖                |
| `bsnes_mercury_accuracy`                    | satellaview, sfc, snes, snesna, … (5)               | unaudited    | ✖         | ✖       | ✖                |
| `cannonball`                                | ports                                               | unaudited    | ✖         | ✖       | ✖                |
| `cap32`                                     | amstradcpc, gx4000                                  | unaudited    | ✖         | ✖       | ✖                |
| `cdi2015`                                   | cdimono1                                            | unaudited    | ✖         | ✖       | ✖                |
| `chailove`                                  | chailove                                            | unaudited    | ✖         | ✖       | ✖                |
| `citra`                                     | n3ds                                                | unaudited    | ✖         | ✖       | ✖                |
| `citra2018`                                 | n3ds                                                | unaudited    | ✖         | ✖       | ✖                |
| `craft`                                     | ports                                               | unaudited    | ✖         | ✖       | ✖                |
| `crocods`                                   | amstradcpc, gx4000                                  | unaudited    | ✖         | ✖       | ✖                |
| `desmume`                                   | nds                                                 | unaudited    | ✖         | ✖       | ✖                |
| `desmume2015`                               | nds                                                 | unaudited    | ✖         | ✖       | ✖                |
| `dice`                                      | arcade, mame                                        | unaudited    | ✖         | ✖       | ✖                |
| `dirksimple`                                | daphne, laserdisc                                   | unaudited    | ✖         | ✖       | ✖                |
| `dolphin`                                   | gc, wii                                             | unaudited    | ✖         | ✖       | ✖                |
| `dosbox_core`                               | dos, pc                                             | unaudited    | ✖         | ✖       | ✖                |
| `dosbox_pure`                               | dos, pc, windows3x, windows9x                       | unaudited    | ✖         | ✖       | ✖                |
| `dosbox_svn`                                | dos, pc                                             | unaudited    | ✖         | ✖       | ✖                |
| `easyrpg`                                   | easyrpg                                             | unaudited    | ✖         | ✖       | ✖                |
| `ecwolf`                                    | ports                                               | unaudited    | ✖         | ✖       | ✖                |
| `fbalpha2012`                               | arcade, cps, cps1, cps2, … (7)                      | unaudited    | ✖         | ✖       | ✖                |
| `fbalpha2012_cps1`                          | cps, cps1, fba                                      | unaudited    | ✖         | ✖       | ✖                |
| `fbalpha2012_cps2`                          | cps, cps2, fba                                      | unaudited    | ✖         | ✖       | ✖                |
| `fbalpha2012_cps3`                          | cps, cps3, fba                                      | unaudited    | ✖         | ✖       | ✖                |
| `fbalpha2012_neogeo`                        | fba                                                 | unaudited    | ✖         | ✖       | ✖                |
| `fbneo`                                     | arcade, cps, cps1, cps2, … (10)                     | unaudited    | ✖         | ✖       | ✖                |
| `fceumm`                                    | famicom, fds, nes                                   | unaudited    | ✖         | ✖       | ✖                |
| `fmsx`                                      | msx, msx1, msx2                                     | unaudited    | ✖         | ✖       | ✖                |
| `freechaf`                                  | channelf                                            | unaudited    | ✖         | ✖       | ✖                |
| `freeintv`                                  | intellivision                                       | unaudited    | ✖         | ✖       | ✖                |
| `frodo`                                     | c64                                                 | unaudited    | ✖         | ✖       | ✖                |
| `fuse`                                      | zxspectrum                                          | unaudited    | ✖         | ✖       | ✖                |
| `gambatte`                                  | gb, gbc                                             | unaudited    | ✖         | ✖       | ✖                |
| `gearboy`                                   | gb, gbc                                             | unaudited    | ✖         | ✖       | ✖                |
| `gearcoleco`                                | colecovision                                        | unaudited    | ✖         | ✖       | ✖                |
| `geargrafx`                                 | supergrafx                                          | unaudited    | ✖         | ✖       | ✖                |
| `gearsystem`                                | gamegear, mark3, mastersystem, multivision, … (5)   | unaudited    | ✖         | ✖       | ✖                |
| `genesis-plus-gx-expanded-rom-size-paprium` | megadrive, megadrivejp                              | unaudited    | ✖         | ✖       | ✖                |
| `genesis_plus_gx`                           | gamegear, genesis, mark3, mastersystem, … (10)      | unaudited    | ✖         | ✖       | ✖                |
| `genesis_plus_gx_wide`                      | gamegear, genesis, mark3, mastersystem, … (10)      | unaudited    | ✖         | ✖       | ✖                |
| `geolith`                                   | arcade, mame, neogeo                                | unaudited    | ✖         | ✖       | ✖                |
| `gpsp`                                      | gba                                                 | unaudited    | ✖         | ✖       | ✖                |
| `gw`                                        | gameandwatch, lcdgames                              | unaudited    | ✖         | ✖       | ✖                |
| `handy`                                     | atarilynx                                           | unaudited    | ✖         | ✖       | ✖                |
| `hatari`                                    | atarist                                             | unaudited    | ✖         | ✖       | ✖                |
| `holani`                                    | atarilynx                                           | unaudited    | ✖         | ✖       | ✖                |
| `jollycv`                                   | crvision                                            | unaudited    | ✖         | ✖       | ✖                |
| `kronos`                                    | arcade, consolearcade, mame, saturn, … (6)          | unaudited    | ✖         | ✖       | ✖                |
| `lowresnx`                                  | lowresnx                                            | unaudited    | ✖         | ✖       | ✖                |
| `lutro`                                     | lutro                                               | unaudited    | ✖         | ✖       | ✖                |
| `mame`                                      | apple2, apple2gs, arcade, arcadia, … (34)           | unaudited    | ✖         | ✖       | ✖                |
| `mame2000`                                  | arcade, cps, cps1, cps2, … (6)                      | unaudited    | ✖         | ✖       | ✖                |
| `mame2003`                                  | arcade, cps, cps1, cps2, … (6)                      | unaudited    | ✖         | ✖       | ✖                |
| `mame2003_plus`                             | arcade, cps, cps1, cps2, … (6)                      | unaudited    | ✖         | ✖       | ✖                |
| `mame2010`                                  | arcade, cps, cps1, cps2, … (6)                      | unaudited    | ✖         | ✖       | ✖                |
| `mednafen_lynx`                             | atarilynx                                           | unaudited    | ✖         | ✖       | ✖                |
| `mednafen_pce`                              | pcengine, pcenginecd, supergrafx, tg-cd, … (5)      | unaudited    | ✖         | ✖       | ✖                |
| `mednafen_pce_fast`                         | pcengine, pcenginecd, tg-cd, tg16                   | unaudited    | ✖         | ✖       | ✖                |
| `mednafen_pcfx`                             | pcfx                                                | unaudited    | ✖         | ✖       | ✖                |
| `mednafen_supafaust`                        | sfc, snes, snesna                                   | unaudited    | ✖         | ✖       | ✖                |
| `mednafen_supergrafx`                       | supergrafx, tg16                                    | unaudited    | ✖         | ✖       | ✖                |
| `mednafen_vb`                               | virtualboy                                          | unaudited    | ✖         | ✖       | ✖                |
| `mednafen_wswan`                            | wonderswan, wonderswancolor                         | unaudited    | ✖         | ✖       | ✖                |
| `melonds`                                   | nds                                                 | unaudited    | ✖         | ✖       | ✖                |
| `melondsds`                                 | nds                                                 | unaudited    | ✖         | ✖       | ✖                |
| `mesen`                                     | famicom, fds, nes                                   | unaudited    | ✖         | ✖       | ✖                |
| `mesen-s`                                   | gb, gbc, satellaview, sfc, … (7)                    | unaudited    | ✖         | ✖       | ✖                |
| `mess2015`                                  | mess                                                | unaudited    | ✖         | ✖       | ✖                |
| `mojozork`                                  | zmachine                                            | unaudited    | ✖         | ✖       | ✖                |
| `mrboom`                                    | ports                                               | unaudited    | ✖         | ✖       | ✖                |
| `mu`                                        | palm                                                | unaudited    | ✖         | ✖       | ✖                |
| `nekop2`                                    | pc98                                                | unaudited    | ✖         | ✖       | ✖                |
| `neocd`                                     | neogeocd, neogeocdjp                                | unaudited    | ✖         | ✖       | ✖                |
| `nestopia`                                  | famicom, fds, nes                                   | unaudited    | ✖         | ✖       | ✖                |
| `noods`                                     | gba                                                 | unaudited    | ✖         | ✖       | ✖                |
| `np2kai`                                    | pc98                                                | unaudited    | ✖         | ✖       | ✖                |
| `nxengine`                                  | ports                                               | unaudited    | ✖         | ✖       | ✖                |
| `o2em`                                      | odyssey2, videopac                                  | unaudited    | ✖         | ✖       | ✖                |
| `openlara`                                  | ports                                               | unaudited    | ✖         | ✖       | ✖                |
| `opera`                                     | 3do                                                 | unaudited    | ✖         | ✖       | ✖                |
| `panda3ds`                                  | n3ds                                                | unaudited    | ✖         | ✖       | ✖                |
| `parallel_n64`                              | n64, n64dd                                          | unaudited    | ✖         | ✖       | ✖                |
| `picodrive`                                 | gamegear, genesis, mark3, mastersystem, … (12)      | unaudited    | ✖         | ✖       | ✖                |
| `potator`                                   | supervision                                         | unaudited    | ✖         | ✖       | ✖                |
| `ppsspp`                                    | psp                                                 | unaudited    | ✖         | ✖       | ✖                |
| `prboom`                                    | doom                                                | unaudited    | ✖         | ✖       | ✖                |
| `prosystem`                                 | atari7800                                           | unaudited    | ✖         | ✖       | ✖                |
| `puae`                                      | amiga, amiga1200, amiga600, amigacd32, … (5)        | unaudited    | ✖         | ✖       | ✖                |
| `puae2021`                                  | amiga, amiga1200, amiga600, amigacd32, … (5)        | unaudited    | ✖         | ✖       | ✖                |
| `px68k`                                     | x68000                                              | unaudited    | ✖         | ✖       | ✖                |
| `quasi88`                                   | pc88                                                | unaudited    | ✖         | ✖       | ✖                |
| `quicknes`                                  | famicom, nes                                        | unaudited    | ✖         | ✖       | ✖                |
| `race`                                      | ngp, ngpc                                           | unaudited    | ✖         | ✖       | ✖                |
| `retro8`                                    | pico8                                               | unaudited    | ✖         | ✖       | ✖                |
| `same_cdi`                                  | cdimono1                                            | unaudited    | ✖         | ✖       | ✖                |
| `sameboy`                                   | gb, gbc, sgb                                        | unaudited    | ✖         | ✖       | ✖                |
| `sameduck`                                  | megaduck                                            | unaudited    | ✖         | ✖       | ✖                |
| `scummvm`                                   | scummvm                                             | unaudited    | ✖         | ✖       | ✖                |
| `smsplus`                                   | gamegear, mark3, mastersystem                       | unaudited    | ✖         | ✖       | ✖                |
| `snes9x`                                    | satellaview, sfc, snes, snesna, … (5)               | unaudited    | ✖         | ✖       | ✖                |
| `snes9x2005_plus`                           | satellaview, sfc, snes, snesna, … (5)               | unaudited    | ✖         | ✖       | ✖                |
| `snes9x2010`                                | satellaview, sfc, snes, snesna, … (5)               | unaudited    | ✖         | ✖       | ✖                |
| `squirreljme`                               | j2me                                                | unaudited    | ✖         | ✖       | ✖                |
| `stella`                                    | atari2600                                           | unaudited    | ✖         | ✖       | ✖                |
| `stella2014`                                | atari2600                                           | unaudited    | ✖         | ✖       | ✖                |
| `stella2023`                                | atari2600                                           | unaudited    | ✖         | ✖       | ✖                |
| `superbroswar`                              | ports                                               | unaudited    | ✖         | ✖       | ✖                |
| `tgbdual`                                   | gb, gbc                                             | unaudited    | ✖         | ✖       | ✖                |
| `theodore`                                  | moto, to8                                           | unaudited    | ✖         | ✖       | ✖                |
| `tic80`                                     | tic80                                               | unaudited    | ✖         | ✖       | ✖                |
| `tyrquake`                                  | quake                                               | unaudited    | ✖         | ✖       | ✖                |
| `uzem`                                      | uzebox                                              | unaudited    | ✖         | ✖       | ✖                |
| `vba_next`                                  | gba                                                 | unaudited    | ✖         | ✖       | ✖                |
| `vbam`                                      | gb, gba, gbc                                        | unaudited    | ✖         | ✖       | ✖                |
| `vecx`                                      | vectrex                                             | unaudited    | ✖         | ✖       | ✖                |
| `vice_x128`                                 | c64                                                 | unaudited    | ✖         | ✖       | ✖                |
| `vice_x64`                                  | c64                                                 | unaudited    | ✖         | ✖       | ✖                |
| `vice_x64sc`                                | c64                                                 | unaudited    | ✖         | ✖       | ✖                |
| `vice_xplus4`                               | plus4                                               | unaudited    | ✖         | ✖       | ✖                |
| `vice_xscpu64`                              | c64                                                 | unaudited    | ✖         | ✖       | ✖                |
| `vice_xvic`                                 | vic20                                               | unaudited    | ✖         | ✖       | ✖                |
| `vircon32`                                  | vircon32                                            | unaudited    | ✖         | ✖       | ✖                |
| `virtualjaguar`                             | atarijaguar                                         | unaudited    | ✖         | ✖       | ✖                |
| `virtualxt`                                 | dos, pc                                             | unaudited    | ✖         | ✖       | ✖                |
| `vitaquake2`                                | quake                                               | unaudited    | ✖         | ✖       | ✖                |
| `vitaquake2-rogue`                          | quake                                               | unaudited    | ✖         | ✖       | ✖                |
| `vitaquake2-xatrix`                         | quake                                               | unaudited    | ✖         | ✖       | ✖                |
| `vitaquake2-zaero`                          | quake                                               | unaudited    | ✖         | ✖       | ✖                |
| `vitaquake3`                                | quake                                               | unaudited    | ✖         | ✖       | ✖                |
| `wasm4`                                     | wasm4                                               | unaudited    | ✖         | ✖       | ✖                |
| `x1`                                        | x1                                                  | unaudited    | ✖         | ✖       | ✖                |
| `yabasanshiro`                              | saturn, saturnjp                                    | unaudited    | ✖         | ✖       | ✖                |
| `yabause`                                   | saturn, saturnjp                                    | unaudited    | ✖         | ✖       | ✖                |

## standalone emulators

| emulator      | systems                                    | verdict   | RetroDECK | EmuDeck | RetroArch (bare) |
| ------------- | ------------------------------------------ | --------- | --------- | ------- | ---------------- |
| `azahar`      | n3ds                                       | unaudited | ✖         | ✖       | —                |
| `cemu`        | wiiu                                       | unaudited | ✖         | ✖       | —                |
| `dolphin`     | gc, triforce, wii                          | unaudited | ✖         | ✖       | —                |
| `duckstation` | psx                                        | unaudited | ✖         | ✖       | —                |
| `gzdoom`      | doom                                       | unaudited | ✖         | ✖       | —                |
| `ironwail`    | quake                                      | unaudited | ✖         | ✖       | —                |
| `mame`        | adam, amstradcpc, apple2, apple2gs, … (54) | unaudited | ✖         | ✖       | —                |
| `melonds`     | nds                                        | unaudited | ✖         | ✖       | —                |
| `openbor`     | openbor                                    | unaudited | ✖         | ✖       | —                |
| `os-shell`    | consolearcade, desktop, mugen, ps3         | unaudited | ✖         | ✖       | —                |
| `pcsx2`       | ps2                                        | unaudited | ✖         | ✖       | —                |
| `pico-8`      | pico8                                      | unaudited | ✖         | ✖       | —                |
| `portmaster`  | portmaster                                 | unaudited | ✖         | ✖       | —                |
| `ppsspp`      | psp                                        | unaudited | ✖         | ✖       | —                |
| `primehack`   | gc, primehack, wii                         | unaudited | ✖         | ✖       | —                |
| `rpcs3`       | consolearcade, ps3                         | unaudited | ✖         | ✖       | —                |
| `ruffle`      | flash                                      | unaudited | ✖         | ✖       | —                |
| `ryubing`     | switch                                     | unaudited | ✖         | ✖       | —                |
| `solarus`     | solarus                                    | unaudited | ✖         | ✖       | —                |
| `vita3k`      | psvita                                     | unaudited | ✖         | ✖       | —                |
| `xemu`        | consolearcade, xbox                        | unaudited | ✖         | ✖       | —                |
| `xroar`       | coco, dragon32, tanodragon                 | unaudited | ✖         | ✖       | —                |
