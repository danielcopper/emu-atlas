# Save portability — whether one entry reads the save another wrote

The method and evidence behind `installation.save_portability(system, source=…, target=…)` and the packaged records in
`atlas/data/save_formats.json` (issue #577). The save questions already say where each entry keeps a game's save and
which files make it up; this question says whether the target entry reads what the source entry wrote. Nothing on the
machine states that, so every verdict rests on a read of both emulators' sources at a pinned build.

## What is compared

**Parts, not pairs.** A save has parts: a memory card in each slot, the console's backup RAM, a backup cartridge. Each
part is answered on its own (#577 D2), named by the memory it holds (`SAVE_MEMORIES`) and its slot (D6), beside the file
group each entry's save answer states for it — narrowed to the files that are this part, so a group that holds two
slots' cards reads as two parts. The pair's summary is the most severe part (D9), in the order `not-readable`,
`unestablished`, `convertible`, `same-data`, `same-files`.

**Formats per emulator, not facts per pair.** A record labels the file groups of one emulator's own save answer, mode by
mode, with the format the source pins for each part. Two parts are the same data exactly where their records name the
same format; the few conversions a source states are listed apart. A system with _n_ entries needs _n_ records, never a
table of every pair.

**The save answers as they stand.** Both entries' `savefile_location` is asked with the caller's `content_path`, `cwd`
and `appendconfig`, and the live mode each one read (`granularity.mode`) selects the record's rules. Nothing here reads
an option itself: Kronos's Beetle-saves switch, LRPS2's card mode, SwanStation's twenty slot combinations and
DuckStation's card types all arrive through the save answer. A record finds a file of the answer by the name its card
spells, a hole matching whatever filled it — or, where a slot's file is an option's value (PCEE2's slots, PCSX2's
`Slot1_Filename`, DuckStation's shared `Card1Path`), by that option's reading in the answer. Where two rules match one
name, the one with more literal characters claims it (Kronos's `<stem>-ext512K.ram` beside `<stem>.ram`); where two
slots open one card file, the file is both slots' part.

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
`convertible`; with none stated the part is `unestablished`, with `save-part-unestablished` (`reason`
`no-conversion-stated`). No unestablished part is left without the caveat that says why.

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
  captured, and `tests/test_save_formats.py` holds them equal.
- **A standalone emulator** — the `component_version` RetroDECK's deploy writes beside the binary, which is the
  `release` a save card's pinned build states. EmuDeck and a bare RetroArch deploy no such record, so a standalone
  emulator there is `build-unread`.

A pin that names a release rather than a commit (PCEE2's `v2.9.78`, shared by several of its commits) or a channel
(DuckStation's `preview`) is confirmed by its string alone, and the answer's sources say so. Searching the deployed
binary for its stamp at runtime is a question of its own.

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

## Known limits

- **Kronos's cartridge on a declared answer.** With the Beetle-saves switch on, Kronos always puts a 4 Mbit cartridge in
  at `<stem>.bcr` (`libretro.c:1301-1305`), and its card lists that file only as an observation candidate. Before the
  file exists the part is `unestablished`; once it is on disk it is judged.
- **PCEE2's folder cards.** PCEE2's build opens a directory at a slot's path as a folder card (`FileMcd_SetType`,
  `pcsx2/SIO/Memcard/MemoryCardFile.cpp:584-604` at 340ce04), and its save answer names that path as a file card, so its
  record has no folder rule; a folder card toward PCEE2 answers by the file card its answer names, `convertible` (D14).
  PCEE2's save answer learning to state folder cards is an issue of its own, and this answer follows it.
- **What a save answer cannot label.** PCSX2's multitap slots and PCEE2's card folder that cannot be listed (or that a
  standalone `PCSX2.ini` governs) state groups no record names, and the pair says `save-format-unrecorded` rather than
  guessing.
- **Coverage.** The records cover psx, ps2, saturn and saturnjp. Every other pair answers `unestablished` with
  `save-format-unrecorded` until its records land: Game Boy next, then the Nintendo DS, GameCube and Wii.
