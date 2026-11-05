# Save portability — whether one entry reads the save another wrote

The method and evidence behind `installation.save_portability(system, source=…, target=…)` and the packaged records in
`atlas/data/save_formats.json` (issue #577). The save questions already say where each entry keeps a game's save and
which files make it up; this question says whether the target entry reads what the source entry wrote. Nothing on the
machine states that, so every verdict rests on a read of both emulators' sources at a pinned build.

## What is compared

**Parts, not pairs.** A save has parts: a memory card in each slot, the console's backup RAM, a backup cartridge. Each
part is answered on its own (#577 D2), named by the memory it holds (`SAVE_MEMORIES`) and its slot (D6), beside the file
group each entry's save answer states for it. Each group names the slots of the console that read it, so a part is one
slot's group, and a file two slots are set to is a part of each slot. Where an emulator keeps a memory apart per region
— Dolphin's `GC/<region>/Card A` and `MemoryCardA.<region>.raw` — each region is a part of its own, named by its
`region` (`SAVE_REGIONS`), and pairs with the target's memory of the same slot and region (D21): a game runs in one
region on both sides. A rule's `<region>` hole matches a word of `SAVE_REGIONS` alone, and a folder rule matches a
directory by its trailing segments, as many as its name spells. The pair's summary is the most severe part (D9), in the
order `not-readable`, `unestablished`, `convertible`, `same-data`, `same-files`.

**Formats per emulator, not facts per pair.** A record labels the file groups of one emulator's own save answer, mode by
mode, with the format the source pins for each part. Two parts are the same data exactly where their records name the
same format; the few conversions a source states are listed apart. A system with _n_ entries needs _n_ records, never a
table of every pair.

**The save answers as they stand.** Both entries' `savefile_location` is asked with the caller's `content_path`, `cwd`
and `appendconfig`, and the live mode each one read (`granularity.mode`) selects the record's rules. Nothing here reads
an option itself: Kronos's Beetle-saves switch, LRPS2's card mode, SwanStation's twenty slot combinations and
DuckStation's card types all arrive through the save answer. A record finds a file of the answer by the name its card
spells, a hole matching whatever filled it, and a rule bound to a slot claims a group only for a slot the answer says
reads it (D23): the save answer states each group's slots from the path the emulator resolves for each slot, so a card a
setting moves or renames (PCEE2's slots, PCSX2's `Slot1_Filename`, DuckStation's shared `Card1Path`) is its slot's part
under any name. Where two rules match one name for one slot, the one with more literal characters claims it (Kronos's
`<stem>-ext512K.ram` beside `<stem>.ram`). Where a save answer cannot establish which slots read a group (its `slots` is
null: RPCS3's virtual memory cards, PCEE2's card folder under a PCSX2 ini), a part kept per slot in it is
`unestablished`, with `save-part-unestablished` (`reason` `slots-unestablished`) (D24).

**A part the target does not name.** Where the target's live mode keeps no such part, the part is `not-readable` (D8);
where its mode keeps one and its answer names no file for it — a card that lists the file only as an observation
candidate, an observed directory whose file is not written yet — it is `unestablished`, with `save-part-unestablished`
(`reason` `target-file-unnamed`). A part only the target keeps is not listed: the target creates it anew and nothing of
the source's is lost.

**What a record sets aside.** A record's `ignore` names what of an emulator's save answer is no part of a game's save on
the system — a directory (Kronos's ST-V files beside a Saturn disc's) or files by name (Beetle Saturn's `.smpc`). Each
carries its citation, and the answer states it among its sources wherever it set something aside, so nothing is dropped
silently.

**Two formats.** Where the two records name different formats, a conversion a source states makes the part
`convertible`. With none stated, a target that RetroArch loads the file for (a `frontend` rule) reads it `not-readable`:
RetroArch copies the file byte for byte into the core's buffer (`save.c:492-541`), so the target reads the source's
layout as its own. A target that opens the file itself may recognise a layout of another's, so with no conversion stated
its part is `unestablished`, with `save-part-unestablished` (`reason` `no-conversion-stated`). No unestablished part is
left without the caveat that says why.

**The cartridge's kind (D15, D16).** atlas reads no cartridge, and on the Game Boy a part's layout can differ by the
cartridge's kind (`CARTRIDGE_KINDS`, from the header byte 0x147): mGBA packs MBC2 RAM two cells to a byte and appends
MBC6 flash, SameBoy stores MBC7 EEPROM words low byte first, VBA-M keeps HuC3 and TAMA5 clocks in layouts of its own. A
record says so per rule (`kinds`: the format for a kind, or `null` where the emulator runs the cartridge and keeps no
such part), per rule which kinds it runs whose layout no read establishes (`unread`, each with its citation saying why),
and per system which kinds the emulator does not run at all (`not_run`, each with its citation). In a record that tells
kinds apart, every kind has exactly one of these statements, and `tests/test_save_formats.py` fails on one that falls
back to the base format unnamed. A part is judged for every kind either rule names or either side does not run, and for
every other kind together. An unread kind is `unestablished` for that kind, never folded into the others'. A kind the
source does not run is skipped, since no save of it exists there to carry; a kind the target does not run is
`not-readable`, since a verdict that holds for the other games would not hold for that one; either way its citation
rides the sources. A kind the source keeps no such part for holds no game data there. A cartridge clock exists only on
the kinds in `CLOCK_CARTRIDGE_KINDS`, so a clock part holds no game data for every other kind either: SameBoy writes its
clock section for every battery cartridge and TGB Dual and DoubleCherryGB theirs for every game, and Gambatte, Gearboy,
mGBA and VBA-M write theirs for clock kinds alone, and in both cases nothing of a clockless game is lost (D16). Where
every kind that holds game data answers alike, and the files carry over or no kind is empty, that is the part's verdict;
otherwise the part is `unestablished`, and `save-part-unestablished` (`reason` `layout-differs-by-cartridge`) states
what holds per kind, a word of `CARTRIDGE_STATEMENTS` each — a verdict, or `no-game-data` — in `cartridges`, keyed by
every kind whose word differs, and in `other_cartridges` for every other kind. Nothing is stated by leaving a key out.

**A save sized per game (D19).** A DS game card's save is as long as the emulator sizes the card's chip, and each
emulator decides that per game its own way: a melonDS build by the save type its own ROM list gives the game's code,
DeSmuME by how far the game writes. A record names its way per rule (`length`): a word of `SAVE_LENGTH_RULES`
(`rom-list` or `written-extent`), for `rom-list` the revision whose list it is, and its citation. Two lengths size every
game alike only where the rule and the revision agree. Otherwise a game's save may be cut or padded on the way for games
atlas cannot name without reading the game code, so the part is `unestablished`, with `save-part-unestablished`
(`reason` `save-length-differs-by-game`) naming each side's rule in `source_length` and `target_length` and the
revision, where there is one, in `source_length_revision` and `target_length_revision`; the lengths are judged before
the formats, since whether the bytes land at all is what they decide. On a system whose records state a length, every
rule states one, and on every other system none does (`tests/test_save_formats.py`).

**A file RetroArch compressed.** With `save_file_compression` on, RetroArch writes a file it saves for a core as an RZIP
stream (`save.c:644-657`), and the save answer says so (`save-files-compressed`). RetroArch reads its own stream back,
and a plain file too (`save.c:511-518`), so between two parts RetroArch loads — the record's `frontend` rules — nothing
changes. An emulator that opens the file itself reads the compressed bytes, so toward it the part is `convertible`, with
no rule a client applies itself (D4).

**Granularity.** A shared card against a per-game file of the same format is `same-data`, and `moves` names
`granularity` beside `directory` and `name` (D11): a client copying one game's card over a shared one would overwrite
every other game's saves. Two per-game spellings (`per-game-file`, `per-game-files`) are one granularity.

## Confirming the build on the machine (D5, D12)

A record pins the strings the machine names its build by, and a part's verdict holds only where both sides' strings
match exactly; elsewhere it answers `unestablished` with `save-format-build-unconfirmed`, its groups still stated.

- **A libretro core** — the `library_version` the core probe reads (`Machine.read_core`), the same comparison the
  save-memory records make (`unverified-version`). The pins are the builds `tests/data/core_registrations.json`
  captured, or, for a core whose registration was never captured, the `verified_core` `atlas/data/save_memory.json` pins
  for each system the record covers; `tests/test_save_formats.py` holds them equal.
- **A standalone emulator** — the `component_version` RetroDECK's deploy writes beside the binary, which is the
  `release` a save card's pinned build states. EmuDeck and a bare RetroArch deploy no such record, so a standalone
  emulator there is `build-unread`.

A pin that names a release rather than a commit (PCEE2's `v2.9.78`, shared by several of its commits; Mesen-S's `0.4.0`)
or a channel (DuckStation's `preview`) is confirmed by its string alone, and the answer's sources say so. Searching the
deployed binary for its stamp at runtime is a question of its own.

RetroArch's own load of a frontend-written file is cited at a79435a, as every save answer cites it (D7): the file's
bytes are copied into the core's buffer for that memory id, cut to the buffer where the file is longer
(`save.c:492-541`).

## Evidence — PlayStation, PlayStation 2, Saturn

Every line below is read at the pin its record states; the record carries the citation per part.

| emulator                     | pin                   | what the source states                                                                                                                                                                                                                                                                                                                | format                                               |
| ---------------------------- | --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| SwanStation                  | `1.0.0 4d309c0`       | The Libretro card's `SAVE_RAM` is the card's 128 KiB array (`libretro_host_interface.cpp:754-762`, `:778-784`); a `.mcd` is loaded only at exactly 128 KiB and saved whole (`memory_card_image.cpp:36-64`).                                                                                                                           | `psx-card-raw`                                       |
| Beetle PSX, Beetle PSX HW    | `0.9.44.1 d6383bf`    | `SAVE_RAM` is card 0's `card_data`, 1 << 17 bytes (`libretro.cpp:5137-5155`, `memcard.cpp:57`); `.mcr` files are read and written raw (`frontio.cpp:950-1011`). One implementation behind two option prefixes.                                                                                                                        | `psx-card-raw`                                       |
| PCSX-ReARMed                 | `r25 228c14e`         | `SAVE_RAM` is `Mcd1Data`, 128 KiB (`frontend/libretro.c:2052-2063`); card 2 is read raw past a 64- or 3904-byte header only where the file carries one, and created raw for a non-`.gme` name (`sio.c:335-470`).                                                                                                                      | `psx-card-raw`                                       |
| DuckStation                  | `preview` (1f6de046b) | A card file is loaded only at exactly 128 KiB and saved whole (`memory_card_image.cpp:99-136`).                                                                                                                                                                                                                                       | `psx-card-raw`                                       |
| LRPS2                        | `14d19f8`             | A file card is pages of 512 data and 16 ECC bytes, sized by the file, addressed by byte, created at 8 MB (`pcsx2/MemoryCardFile.cpp:274-484`).                                                                                                                                                                                        | `ps2-file-card`                                      |
| PCEE2                        | `v2.9.78` (340ce04)   | The same file card (`pcsx2/SIO/Memcard/MemoryCardFile.cpp:262-508`).                                                                                                                                                                                                                                                                  | `ps2-file-card`                                      |
| PCSX2                        | `v2.8.2` (fd9d310c)   | The same file card; a slot whose path is a directory is a folder card (`MemoryCardFile.cpp:584-604`); the Qt converter turns one into the other (`MemoryCardConvertWorker.cpp:36-167`).                                                                                                                                               | `ps2-file-card`, `ps2-folder-card`                   |
| Beetle Saturn                | `v1.29.0 ccba526`     | Backup RAM holds each odd bus byte at `(A >> 1) & 0x7FFF`, 32 KiB, raw in the `.bkr` (`ss.cpp:98`, `:244-249`, `:1037-1057`); the 4 Mbit cartridge the same way, 512 KiB raw in the `.bcr` (`cart/backup.cpp:25-66`, `ss.cpp:1077-1138`); the `.smpc` is the SMPC's state (`smpc.cpp:357-369`), set aside as the console's own (D13). | `saturn-backup-ram`, `…-cartridge-4mbit`             |
| Kronos                       | `v2.7.0 6709c1d`      | Backup RAM is 0x8000 bytes of the odd bus bytes at `addr >> 1`, loaded and saved byte for byte (`memory.c:507-570`, `:1282-1325`, `memory.h:219-354`); the backup cartridges the same way (`cs0.c`); the Beetle-saves switch changes only the paths (`libretro.c:1299-1316`, `:1709-1712`).                                           | `saturn-backup-ram`, `…-cartridge-4mbit` … `-32mbit` |
| RetroArch (frontend-written) | a79435a               | A `.srm` is copied into the core's buffer for that id, cut to the buffer where longer (`save.c:492-541`).                                                                                                                                                                                                                             | —                                                    |

What follows for the pairs, in each entry's default mode unless named: the four PlayStation cores' first card is the
same `<rom_stem>.srm`; DuckStation keeps the same data as `<save_id>_1.mcd` in its own directory; Beetle PSX's second
card does not carry over to an entry whose mode keeps none. LRPS2's shared cards and PCEE2's are the same files; PCSX2's
own build keeps them in its own memory-card folder; a folder card is convertible to a file card with no rule a client
applies itself. Beetle Saturn's backup RAM is Kronos's under another name and directory, the same files with Kronos's
Beetle-saves switch on. Beetle's `.smpc` is no part: it holds the console's clock and its SMPC memory (the language),
which each emulator keeps its own way, so losing it costs no game progress (D13); the record sets it aside with that
citation, and the answer's sources say so.

## Evidence — Game Boy, Game Boy Color

Every core here hands its save to RetroArch through `RETRO_MEMORY_SAVE_RAM` (`<rom_stem>.srm`) and `RETRO_MEMORY_RTC`
(`<rom_stem>.rtc`), so every rule is a `frontend` one. A Game Boy Color cartridge is laid out as a Game Boy one, so each
record states `gbc` as the same as `gb`. The cartridge RAM is one format, `gb-cartridge-ram`, in every record — each
address one byte at its offset, an MBC2 cell a byte, an MBC7 word high byte first — except where a kind is named; each
core sizes the image its own way (Gambatte and TGB Dual give a 2 KiB cartridge 8 KiB, Gearboy every MBC3 cartridge 32
KiB), and RetroArch's load copies what fits (`save.c:523-533`), so the data a game holds carries over. A TAMA5
cartridge's memory is mGBA's own 32 bytes in its `.srm` (`gb/mbc.c:438-445`, `tama5.c:205-215`); VBA-M keeps it apart
from any memory id (`gbMemory.cpp:1390-1396`), and TGB Dual's and DoubleCherryGB's TAMA5 writes are empty stubs, so
their saves hold none of it.

| emulator       | pin                      | cartridge RAM                                                                                                                                                                                                                                                                                                                  | clock                                                                                                                      | does not run                                    |
| -------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| Gambatte       | `v0.5.0-netlink 6924c76` | `rambankdata`, mapped straight onto the window (`cartridge_libretro.cpp:43-56`, `cartridge.cpp:589-607`)                                                                                                                                                                                                                       | 8-byte base time, MBC3 timer and HuC3 only (`cartridge_libretro.cpp:30-41`, `:58-80`)                                      | MMM01, MBC6, MBC7, TPP1, Pocket Camera, TAMA5   |
| SameBoy        | `1.0.2 be2c352`          | `mbc_ram`, MBC2 mirrored (`mbc.c:207-235`, `memory.c:454`, `:1293`); MBC7 words low byte first (`memory.c:1142`, `:1159-1203`)                                                                                                                                                                                                 | its `rtc` section, every battery cartridge (`libretro.c:1690-1693`, `:1763-1766`, `gb.h:554-559`)                          | MBC6, TAMA5                                     |
| Gearboy        | `3.7.4-8-g37c21f7`       | the rule's RAM banks (`MBC1MemoryRule.cpp:246-249`, `MBC2MemoryRule.cpp:120-140`, `MBC3MemoryRule.cpp:374-377`, `MBC5MemoryRule.cpp:189-192`); a Pocket Camera as an MBC3, its first four banks alone (`Cartridge.cpp:362-365`, `MBC3MemoryRule.cpp:165-176`); MBC3 banks 0-3 only, so no MBC30 (`MBC3MemoryRule.cpp:172-181`) | VBA-M's MBC3 layout with a 32-bit time and a zeroed pad (`MBC3MemoryRule.h:25-39`, `.cpp:40-64`)                           | MMM01, MBC6, MBC7, TPP1, TAMA5, HuC3            |
| TGB Dual       | `v0.8.3 933707c`         | `sram` (`rom.cpp:53-57`, `cpu.cpp:141`, `:184`); MBC7 words high byte first (`mbc.cpp:230-231`)                                                                                                                                                                                                                                | a 4-byte dword, every game (`libretro.cpp:493-496`, `dmy_renderer.h:40`)                                                   | MBC6, TPP1, Pocket Camera                       |
| DoubleCherryGB | `v0.17.0 5dbc8c9`        | TGB Dual's (`cpu.cpp:168`, `:216`, `mbc.cpp:255-256`)                                                                                                                                                                                                                                                                          | TGB Dual's, every game (`libretro.cpp:434-436`, `dmy_renderer.h:237`)                                                      | MBC6, TPP1, Pocket Camera                       |
| Mesen-S        | `0.4.0` (9e4fdeb)        | the cartridge's RAM, MBC2 mirrored (`GameboyHeader.h:29-45`, `GbMbc2.h:34`, `:54`); MBC3 RAM bank n at byte offset n, so banks past the first overlap (`GbMbc3.h:36`, `GbMemoryManager.cpp:107-117`)                                                                                                                           | none                                                                                                                       | every kind but plain, MBC1, MBC2, MBC3 and MBC5 |
| mGBA           | `0.11-dev c758314`       | `sramSize` (`gb/mbc.c:277-304`); MBC2 packed two cells to a byte (`gb/mbc/mbc.c:123-174`), MBC6 flash appended (`gb/mbc.c:414-421`)                                                                                                                                                                                            | VBA-M's MBC3 layout, MBC3 timer only, none for HuC3 and TAMA5 (`libretro.c:2357-2366`, `mbc.h:34-46`)                      | TPP1                                            |
| VBA-M          | `2.2.3 d0787ae`          | `gbRam` (`gbCartData.cpp:446-465`, `gbMemory.cpp:258-267`); MBC7 words high byte first (`gbMemory.cpp:701-702`); no Pocket Camera RAM, which gets no battery (`gbCartData.cpp:380-382`), and no TAMA5 memory                                                                                                                   | MBC3: ten registers and a 64-bit time; TAMA5 and HuC3 in layouts of its own (`libretro.cpp:166-216`, `gbMemory.h:206-208`) | MBC6, TPP1                                      |

What follows for the pairs: the cartridge RAM carries over in place between two of these cores for MBC1, MBC2 and MBC5
cartridges, except toward or from mGBA for MBC2 and between two cores that keep a mirror write differently (D18), for
MBC3 cartridges between the cores that bank them alike (all but Gearboy, which stops at bank 3 where an MBC30 goes to 7,
and Mesen-S, which maps bank n at byte offset n), and for a cartridge with RAM and no mapper where both records read it;
it does not toward or from mGBA for an MBC6 or TAMA5 cartridge, toward or from Gearboy for a Pocket Camera, or between
SameBoy and the cores that store MBC7 words high byte first for an MBC7 one; a kind the source runs and the target does
not is not readable for it; and a kind either record runs without a read of its layout — HuC1 and HuC3 on every core
that runs them, MMM01 on SameBoy, mGBA, VBA-M, TGB Dual and DoubleCherryGB, MBC5 with rumble on Gambatte, Gearboy,
Mesen-S and mGBA, and the rest each record's `unread` names — is unestablished. Gearboy, mGBA and VBA-M share the MBC3
clock; TGB Dual and DoubleCherryGB share theirs; every other pair of clocks does not carry over for a clock cartridge
and holds nothing for the rest (D16). Those parts answer `unestablished` with each kind's word stated, so nearly every
Game Boy pair does until the cartridge's kind is read.

## Evidence — Nintendo DS

Every entry keeps one part, the card's save memory (`cartridge-ram`). melonDS and DeSmuME write their own file; melonDS
DS hands the save to RetroArch through `RETRO_MEMORY_SAVE_RAM`, so its rule is a `frontend` one.

| emulator             | pin                                | what the source states                                                                                                                                                                                                                                                                       | format         | length                 |
| -------------------- | ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------- | ---------------------- |
| DeSmuME              | `git 7f05a8d`                      | `<rom_stem>.dsv`, written in place at each address the game writes, padded with 0xFF to the next size of `saveSizes` at or above the highest address written, then the footer (`mc.cpp:232-235`, `:935-972`, `:1409-1424`); read back at the size the footer states (`:336-347`, `:414-457`) | `nds-dsv`      | `written-extent`       |
| DeSmuME 2015         | `git af397ff`                      | the same file, footer and table (`mc.cpp:236-240`, `:73-86`, `:893-930`, `:318-332`, `:397-434`)                                                                                                                                                                                             | `nds-dsv`      | `written-extent`       |
| melonDS              | `0.9.3 7a3c11f`                    | `<save dir>/<rom_stem>.sav`, raw, read up to the save's length over a 0xFF fill, and every flush the core asks for at the end of a frame writes the whole buffer (`libretro.cpp:769`, `:887-895`, `NDSCart.cpp:438-472`, `NDSCart_SRAMManager.cpp:76-94`, `:153-180`)                        | `nds-card-raw` | `rom-list` at 7a3c11f  |
| melonDS DS           | `1.2.0` (33c4826, melonDS f6692df) | `SAVE_RAM` is the card's save memory, raw and `SramLength()` long, which RetroArch writes as `<rom_stem>.srm` and loads back into the zeroed buffer before the first frame installs it whole (`core.cpp:361-371`, `:937-960`, `sram.cpp:52-55`, `:75-96`, `:118-127`)                        | `nds-card-raw` | `rom-list` at f6692df  |
| melonDS (standalone) | Flathub build of 1.1 (b86390e4)    | `<rom_stem>.sav`, read whole, copied up to the save's length over a 0xFF fill, every flush written whole (`EmuInstance.cpp:1889-1915`, `NDSCart.cpp:402-428`, `SaveManager.cpp:97-122`, `:166-193`)                                                                                          | `nds-card-raw` | `rom-list` at b86390e4 |

**The `.dsv` footer.** Both DeSmuME builds close the file with the same 122 bytes: the 82-byte line
`DESMUME_BACKUP_FOOTER_TXT`, six little-endian 32-bit fields (the size written, the padded size, the save type, the
address size, the memory size, the version 0) and the 16-byte cookie `|-DESMUME SAVE-|` (`mc.cpp:74-75`, `:961-970`,
`GetDSVFooterSize` at `:1702-1705`, `mc.h:55-70` at 7f05a8d; `mc.cpp:70-71`, `:325-326` at af397ff, which sets aside
`strlen(cookie) + strlen(text) + 24`). Cutting it off leaves the raw image a melonDS build reads, padded to DeSmuME's
own size. The strip is a rule a client could apply, and it becomes a conversion rule once a verdict uses it, which waits
for the game code (D19): until then the length decides first.

**How long a save is.** Each melonDS build sizes a game's save by the save type its ROM list gives the game's code — 0,
512 bytes, 8, 64, 128, 256 or 512 KiB, 1, 8, 16 or 64 MiB — and 8 KiB, a 64 kbit EEPROM's, for a code the list lacks
(none for homebrew) (`NDSCart.cpp:446-454`, `:1554-1565` at 7a3c11f; `:402-428`, `:1633-1645` at b86390e4; `:387-413`,
`:1616-1628` at f6692df). The three lists differ: compared by game code, 0.9.3 and 1.1 give two codes another type and
1.1 has one code 0.9.3 lacks (both types are later fixes, 2179ca2 and 9f96ac6), and f6692df, which carries the first fix
and predates the second, differs from 1.1 in one code. A file of another length is cut to the save's length where it is
longer; where it is shorter, melonDS 0.9.3 and 1.1 fill the rest with 0xFF (`NDSCart.cpp:459-466` at 7a3c11f, `:420-427`
at b86390e4), and melonDS DS leaves it 0x00: its buffer is allocated zeroed (`sram.cpp:52-55` at 33c4826), RetroArch
copies a shorter `.srm` into its start and cuts a longer one (`save.c:523-533` at a79435a), and the whole buffer is
installed in the card (`NDSCart.cpp:476-482` at f6692df). DeSmuME sizes the file by how far the game writes, from its
own table of sizes, and reads a `.dsv` back at the length its footer states. It imports a raw `<rom_stem>.sav` beside it
only while no `.dsv` exists, and gives it a footer only where its length is exactly one of its `save_types` sizes, which
name no 128 KiB chip (`mc.cpp:94-109`, `:270-325`, `:641-649` at 7f05a8d); a file of another length is left without one
and its data unused (`:341-347`). The import is DeSmuME's own and is not counted as the target reading melonDS's format
(D19).

What follows for the pairs: DeSmuME and DeSmuME 2015 keep the same `.dsv`, which moves between DeSmuME 2015's working
directory and DeSmuME's save directory. Every other pair sizes a game's save in two ways, so it answers `unestablished`
with `save-length-differs-by-game` until the game code is read. NooDS, its standalone build, DeSmuME's standalone build
and SkyEmu have no record yet, so their pairs say `save-format-unrecorded`.

## Evidence — GameCube, Wii

Dolphin, its libretro core and PrimeHack, a fork of Dolphin, keep the GameCube cards and the Wii NAND alike: the card
code is the same in all three builds but for style and one guard Dolphin adds against a block index past a save's end
(`GCMemcardDirectory.cpp:556-561` at f84df020; `GCMemcard.h`, `GCMemcardDirectory.cpp`, `GCMemcardRaw.cpp` and
`GCIFile.cpp` compared at f84df020, fd1aca3a and 81bfb96), and so is the NAND path. Each record has four card rules — a
GCI folder or a raw card in slot A or B, by the live mode `folder`, `card`, `agp` or `none` per slot — and one Wii rule.
A card is its slot's part by the slots its group names, as above (D23), so a raw card Dolphin.ini moves or renames
(`MemcardAPath`, `MemcardBPath`) is read by the slot whose key names it under any name, also where that name is the
other slot's default: the running disc's region takes the place of the name's region word, or goes before its extension
(`GetMemcardPath`, `MainSettings.cpp:776-836` at f84df020). A raw rule therefore reads only the region word off the name
(`<card>.<region>.<ext>`). The save answer cuts from a configured card's name the region codes its build cuts — `USA`,
`JAP`, `EUR` and `DEV` in Dolphin (`MainSettings.cpp:805-808` at f84df020) and the Flathub PrimeHack (`:807-810` at
53f53e0), `USA`, `JAP` and `EUR` in RetroDECK's PrimeHack (`:664-666` at 81bfb96) and the core (`:697-699` at fd1aca3a),
never `JPN` — so the card it names is the one Dolphin opens.

| emulator               | pin                              | GameCube card                                                                                                                                                                                                                                                                                                         | Wii NAND                                                                                                                                                                                    |
| ---------------------- | -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Dolphin (standalone)   | Flathub build of 2609 (f84df020) | GCI folder: each save a `.gci` of its 0x40-byte directory entry and its 0x2000-byte blocks, read whole at load and written back alike (`GCMemcardDirectory.cpp:64-123`, `:180-259`, `:626-723`, `GCIFile.cpp:13-67`); raw card: read whole at the file's size and flushed whole (`GCMemcardRaw.cpp:38-82`, `:94-162`) | `title/<hi>/<lo>/data` below the NAND root, one host file per NAND file (`NandPaths.cpp:63-72`, `HostBackend/FS.cpp:32-52`); an unlisted file gets read and write for everyone (`:234-278`) |
| Dolphin (libretro)     | `fd1aca3a`                       | the same, below the core's `User` tree (`GCMemcardDirectory.cpp:65-124`, `:181-260`, `:621-718`, `GCMemcardRaw.cpp:38-83`, `:95-163`)                                                                                                                                                                                 | the same (`NandPaths.cpp:63-72`, `HostBackend/FS.cpp:33-53`, `:236-280`)                                                                                                                    |
| PrimeHack (standalone) | `master-230724.27` (81bfb96)     | the same (`GCMemcardDirectory.cpp:65-124`, `:181-260`, `:621-718`, `GCMemcardRaw.cpp:38-83`, `:95-163`)                                                                                                                                                                                                               | the same (`NandPaths.cpp:49-58`, `HostBackend/FS.cpp:32-50`, `:238-283`)                                                                                                                    |

**Raw card and GCI folder.** Dolphin's memory-card manager opens a raw card and exports its saves as `.gci` files
(`GCMemcardManager.cpp:331-350`, `:407-501` at f84df020), and imports `.gci` files into an open raw card (`:503-575`,
`:577-619`). The copy-protected saves (PSO and F-Zero GX's) are re-signed to the card they land on both ways: a GCI
folder re-signs them at load (`GCMemcardDirectory.cpp:110-114`), an import into a raw card at import
(`GCMemcard.cpp:713-784`, `:757-758`). So the conversion holds for every game, and no rule a client applies itself
states it.

What follows for the pairs: between any two of the three, a GCI folder carries over to the other's GCI folder of the
same slot and region, and a raw card to the other's raw card, in its own user tree (`same-data`, `directory`); a raw
card and a GCI folder convert into each other (`convertible`); a slot the target keeps nothing in does not carry over. A
Wii title's data directory carries over to the other's NAND as it lies. Triforce (standalone) has no record, so its
pairs say `save-format-unrecorded`.

**Content with no region (D22).** A `.dol` or `.elf` — the extension compared lowercased — that does not open as a disc
image boots as an executable whose region is Unknown, and Dolphin then runs it in `[Core] FallbackRegion`, by default
the region of the system's locale (`Boot.cpp:243-267`, `ConfigManager.cpp:382-387`, `:489-490`,
`MainSettings.cpp:280-281`, `DefaultLocale.cpp:188-192` at f84df020; the core and PrimeHack alike). atlas reads neither
setting, and two emulators may hold different ones, so where the question is asked with such a content path each card
part is `unestablished`, with `save-part-unestablished` (`reason` `region-not-in-content`) and each side's citation; a
record states it per system (`regionless`). The Wii NAND keeps no region, so its part is unaffected.

## Known limits

- **Kronos's cartridge on a declared answer.** With the Beetle-saves switch on, Kronos names a 4 Mbit cartridge at
  `<stem>.bcr` (`libretro.c:1301-1305`), but the emulator's own game table replaces the cartridge for the discs it lists
  (`DBLookup`, `ctrl/src/yabause.c:392-393`, `utils/src/db.c:14-47` and `:57-79` at 6709c1d), and atlas reads no disc.
  So the card keeps the file an observation candidate: before it exists the cartridge part is `unestablished`, and once
  it is on disk it is judged.
- **PCEE2's folder cards.** PCEE2's build opens a directory at a slot's path as a folder card (`FileMcd_SetType`,
  `pcsx2/SIO/Memcard/MemoryCardFile.cpp:584-604` at 340ce04), and its save answer names that path as a file card, so its
  record has no folder rule; a folder card toward PCEE2 answers by the file card its answer names, `convertible` (D14).
  PCEE2's save answer learning to state folder cards is an issue of its own, and this answer follows it.
- **What a save answer cannot label.** PCSX2's multitap slots and PCEE2's card folder that cannot be listed (or that a
  standalone `PCSX2.ini` governs) state groups no record names, and the pair says `save-format-unrecorded` rather than
  guessing.
- **The kinds no read establishes.** MBC1 and MBC5 cartridges are the plain bank image every record's citation
  describes, at bank n × 0x2000, and no record names them. Every kind of the vocabulary a record's emulator runs has a
  cited statement, and every Game Boy record is held to it; where it is `unread` — HuC1 and HuC3 on every core that runs
  them, MMM01 on SameBoy, mGBA, VBA-M, TGB Dual and DoubleCherryGB (the last two run it for one title the code names), a
  cartridge with RAM and no mapper on Gambatte, mGBA and VBA-M, an MBC5 cartridge with rumble on Gambatte, Gearboy,
  Mesen-S and mGBA (whose bank select can keep the motor bit), MBC3 with and without a clock on Gearboy (no MBC30 banks)
  and Mesen-S (banks past the first overlap), the Pocket Camera on SameBoy and mGBA and TPP1 on SameBoy — the kind is
  `unestablished` until its banking is read and compared.
- **A write through a mirror.** A small cartridge RAM is mirrored over the RAM window, and a game that writes through a
  mirror leaves its byte at a different offset depending on the core: SameBoy and Mesen-S fold it onto the RAM, and mGBA
  for MBC2 only, Gambatte, TGB Dual and DoubleCherryGB keep it at its window offset, Gearboy and VBA-M drop it for MBC2
  (and Gearboy keeps it past a 2 KiB RAM's end). Every MBC2 cartridge is that small, so each record states its `mirrors`
  handling and `mbc2` is `unestablished` between two that differ (D18). A 2 KiB RAM on another mapper is told only by
  the header's RAM size byte (0x149), which the issue that reads the cartridge's kind reads too; until then it is one of
  the two stated exceptions to D15 among these limits: the non-folding cores for it (Gambatte, TGB Dual, DoubleCherryGB,
  Gearboy, VBA-M, and mGBA, which keeps a 2 KiB RAM in an 8 KiB image written at `address & 0x1FFF`, `gb/mbc.c:293-296`
  and `gb/memory.c:386`) and the folding ones are answered alike for it, and no game is known to write through such a
  mirror.
- **RetroArch-written saves are always declared.** D10 reads the parts off the save answer, and for a core RetroArch
  writes the save for, that answer names the files its memory ids give whether or not they are on disk: it states no
  observed set to tell a game with a `.rtc` from one without. So a Game Boy pair always lists the clock part, and states
  for the kinds without a clock that it holds no game data (D16).
- **bsnes.** bsnes runs a Game Boy cartridge through its Super Game Boy and writes `<rom_stem>.srm` itself, the
  cartridge RAM with a 48-byte clock after it for a clock cartridge, by the SameBoy copy it carries. Its pin names the
  release `115` alone, and the revision its card was read at is not the one these lines were read at, so no record
  states it yet and its pairs say `save-format-unrecorded`.
- **The game code.** On the Nintendo DS whether a save survives depends on how each side sizes that game's save, which
  differs between the melonDS builds for a few game codes and is decided another way by DeSmuME than by any melonDS
  build, and atlas reads no game code (D19). Reading it from the launched file, as the issue that reads the Game Boy
  cartridge's kind does for its header, would let a pair whose lengths agree for that game carry its plain verdict.
- **Dolphin's moved GCI folders and its GBA adapter.** A GCI folder Dolphin.ini moves (`GCIFolderAPath`,
  `GCIFolderBPath`) is a directory no rule names, and a GBA cartridge adapter (`agp`) keeps the cartridge's save beside
  the cartridge, which no record states, so those pairs say `save-format-unrecorded`. RetroDECK's shipped Dolphin.ini
  and PrimeHack's leave the cards where the defaults put them. A NAND Dolphin.ini moves (`NANDRootPath`, which
  RetroDECK's sets) is still read, since the Wii rule names the tail `title/<save_id>/data`.
- **Content Dolphin runs in its fallback region, unnamed.** Without a content path atlas cannot tell a `.dol` or `.elf`
  from a disc, so the card parts answer as for a disc with its region in its header (D22). Nor does it read a playlist's
  first entry here, which Dolphin boots by its own extension (`Boot.cpp:217-228` at f84df020), so a `.m3u` whose first
  entry is a `.dol` or `.elf` is answered as a disc too.
- **An extracted disc.** A disc extracted to a directory boots from its `sys/main.dol`, which Dolphin opens as a disc
  (`DirectoryBlobReader`, `Blob.cpp:245`, `DirectoryBlob.cpp:256-259` at f84df020), with the region its header states.
  atlas compares the extension alone, so such a content path answers its card parts `unestablished` with
  `region-not-in-content`: no verdict is wrong, but the reason names a cause that does not hold for it.
- **A card of another size.** A raw card smaller than the default carries its free block count in its name, after the
  region, and the GCI folder's card shrinks with it (`MainSettings.cpp:779-790` at f84df020; `EXI.cpp:117-119` at
  81bfb96); the save answer does not read `MemoryCardSize` yet, so such a card is answered by the default size's name
  (D22). Dolphin's own per-game settings set that size for a few game families with no user change: a
  `Data/Sys/GameSettings` file's `[Core] MemoryCardSize` is a game layer of the configuration (`ConfigManager.cpp:286`,
  `GameConfigLoader.cpp:109`) and is read where the card is mounted (`EXI.cpp:124-126` at f84df020). For a raw card on
  those games this is the other stated exception to D15: the card is named otherwise than the answer says until the save
  answer reads the setting and the game (D22).
- **Coverage.** The records cover psx, ps2, saturn, saturnjp, gb, gbc, nds, gc and wii. Every other pair answers
  `unestablished` with `save-format-unrecorded` until its records land.

Reading Dolphin's fallback region, its card size and the region spelling of a configured GCI folder, so the limits on
content Dolphin runs in its fallback region and on a card of another size become exact and a configured GCI folder can
be recorded, is #777.
