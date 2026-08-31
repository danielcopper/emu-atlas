# Save detection — open tasks

What is still missing before "atlas answers save locations correctly for everything RetroDECK can launch". Ordered
roughly by severity: wrong-and-unmarked answers first, then missing coverage, then polish. References point into
`docs/research/retrodeck-save-placement.md` (§) and the GitHub issues.

## Wrong today, unmarked

> **Done:** tasks 1 and 2 shipped as rule cards (`atlas/data/core_oddities.json` + `atlas/oddities.py`): Flycast
> resolves to `system_directory/dc` with observed VMUs, granularity (shared vs. per-game) is read live from the
> governing core option, and the answer names the options file to switch it. The per-game VMU filename scheme is settled
> too (live run 2026-08-05): `<save_id>.<port>.bin` in the content-sorted save directory, `save_id` being the disc's
> product number — stated as the template it is, with `save_id` in `needs`, and the ROM-named branch for content
> carrying no id handed over in `filenames-content-conditional` (research doc §8). Remaining inside task 1: which ports
> a per-content mode covers is content-dependent ([O], needs a live Naomi run), `VMU A1` therefore states no file set at
> all (`file-set-spans-roots`), and cards for further system-directory cores as they are found. Task 6 is now partially
> covered — granularity is stated wherever a rule card exists, `None` elsewhere.

1. **Flycast / system-directory cores** (§8, issue #12). The resolver returns the RetroArch default directory for
   Dreamcast content with no warning, while the real saves are shared VMUs under `system_directory`
   (`bios/dc/vmu_save_*.bin` + `dc_nvmem.bin`) — verified live on a Dreamcast title. Steps:
   - interim: caveat on known system-directory cores ("answer incomplete — core does not root at `savefile_directory`")
   - full: oddity rule producing `root_kind=system_directory`, the VMU file set, and granularity _shared card_
   - read `reicast_per_content_vmus` from the core-options file — the option flips both granularity (per-game) and root
     (back to `savefile_directory`); the answer must name the governing option (the "recommended configuration" warning
     from #12)
   - distinguish cores that merely write _runtime_ data into `system_directory` (Mupen64Plus-Next ini/shader cache) from
     cores that keep _saves_ there

2. **Core options are not read at all.** `retroarch-core-options.cfg` plus per-core option files govern save behavior
   for at least Flycast; the resolver never opens them. Needed for task 1 and any future oddity rule.

## Missing coverage

3. **Standalone emulators** (§3b, issue #3). The whole `saves/<system>/<emulator>/` family: per-emulator config parsers
   and placement rules — DuckStation (memcards, `PerGameTitle` naming → `<save_id>` hole), PCSX2 (shared
   `Mcd001.ps2`/`Mcd002.ps2` + multitap), Dolphin (GC region cards, Wii NAND), melonDS, PPSSPP-SA, Ryujinx, … Derive the
   target list from `es_systems.xml`, not by hand.

4. **libretro cores with their own save stack** (§3c). `saves/<system>/retroarch-core/<CORE>/` — LRPS2 memcards reached
   via `bios/pcsx2/memcards` symlink. Oddity rules per core.

5. **Emulator catalogue** (`emulators_for`, DESIGN "two entry points"). **Partially done:** RetroDECK reads the bundled
   `es_systems.xml` (Flatpak deployment) plus the `custom_systems` overlay live; `EmulatorEntry.savefile_location`
   carries its core, removing the no-core caveat class from that path; standalone entries answer with a typed
   `Unresolved` domain outcome instead of raising. Still open: the user's saved per-system emulator choice in
   `es_settings.xml` (key format unverified — [O], needs a machine with a switched emulator to observe; the gamelist
   spellings — per-system `alternativeEmulator` and per-game `altemulator` — are both implemented and vectored), `.info`
   `systemid` capability queries, EmuDeck frontends (ES-DE elsewhere / Pegasus / SRM / absent, §13), and structured
   error reporting for a skipped `custom_systems` overlay — the bundled layer states an unread one as
   `emulator-catalogue-unreadable` now, but an overlay that cannot be read is still passed over silently: the read's
   status tells missing from unreadable, and that layer looks only at the text.

6. **Save granularity field** (issue #12): per-game file / per-game folder / shared card on the placement, with the
   config that selects the mode. Flycast, LRPS2, Dolphin-libretro are the first shared-card targets. **Partially done:**
   stated wherever a rule card exists; where it is deliberately unstated, the `core-multi-option` caveat says so and
   names the governing options (issue #23) instead of leaving an empty field that reads as nothing-to-report. Still open
   for the six `multi-option` cores: interpreting those options into a granularity value, which is what task 15's card
   variants are for.

7. ~~**Savestates.**~~ **Done** — `savestate_location` resolves the savestate quartet through the same parameterized
   chain (`docs/research/retrodeck-save-placement.md` §18). Still open here: nothing reads `retrodeck.json`'s
   `states_path`, which is RetroDECK's input to the cfg and not what RetroArch reads, so it would be a second answer
   about one tree — the same reason `roms_path` stopped being one. A `states_root()` accessor beside `saves_root()` is
   the open question, not a resolver gap.

## Honesty improvements

8. **Override enumeration without a core.** The blanket caveat "per-core overrides not checked" is lazier than the
   machine requires: glob the override config dir — no override dirs → the answer holds for every core (no caveat);
   otherwise name the `library_name`s that would change it.

9. **Filter RetroArch companion files from observed file sets.** **Done for `.ldci`** (disk-control index,
   `disk_index_file.c:201-249` + `file_path_special.h:83` — filtered with the citation; observation is also literal now,
   glob metacharacters escaped). Remaining: survey whether further companion extensions land in the save directory
   before filtering more.

10. **Deviation warning** (§9). Compare the live cfg against the shipped reference (readable from the Flatpak
    deployment); report drift alongside the correct answer. On EmuDeck note the different semantics — `autofix.sh`
    reverts drift.

## Versioning

15. **Card variants via feature detection.** Cards currently describe one core generation; the LRPS2 drift
    (`pcsx2_memcard_slot_1/2` → `pcsx2_shared_memory_cards`) shows two generations coexist in the wild. Planned: variant
    dispatch on observable facts (which option keys the core registers), enabled by extending `query_core` to capture
    the option definitions from `retro_set_environment` — which also turns option defaults into live reads. One vector
    per generation, kept forever. Design in `docs/research/core-audit.md`.

16. **A card mode that spans two roots, and a file set scoped to a content class.** Flycast's `VMU A1` moves port A1 and
    leaves B1..D1 plus the console flash on the shared card under `system_directory`; the schema states one root per
    mode, so the mode declares `also_under` and the answer reports `file-set-spans-roots` instead of a file set. The
    same dimension shows in `All VMUs`, whose four names are the console port set: a Naomi board connects two, so the
    list is stated with `files_established_for` + `files_citation` and the caveat carries the scope — honest, but a
    scope is not the same as an answer. The shared `disabled` mode declares the same console-shaped list and has no
    scope on it (unchanged from before this work; its set is normally observed rather than declared). What the model
    would need: a mode whose file set is a list of `(root, subdir, files)` groups rather than one root plus one list,
    each group with its own evidence status and content scope, and a `granularity` able to say "per-game for these
    files, shared for those". Same design pass as task 15's card variants — both are the card schema growing a
    dimension.

    _Surfaced with it_ (pre-existing, deliberately not fixed here): on the standard route an observation wins over the
    declaration, so a file matching `<rom_stem>.*` in the save directory replaces the template and drops `save_id` from
    `needs` — for a card whose names are id-keyed, the observed file may belong to another game in a content-sorted
    directory. The system_directory route does not observe once a name carries a hole. The two routes should treat a
    template the same way.

    _Why the loader's strictness is not an internal matter_: the card file's own `spec` string states the template
    guarantee — "the loader refuses any other token, so a typo cannot travel into a filename atlas states as fact" — and
    that string ships inside the package, in `atlas/data/core_oddities.json`. It is a claim published to consumers, so a
    loophole in the check is a false claim in a released artifact, not a missing internal invariant. Any change to the
    vocabularies belongs in the same commit as the text that promises them.

## Context and docs

11. **`home` guidance for callers.** Document who supplies `home` and why there is no default: the caller knows which
    user it serves; a root-running host (decky's backend) must pass the target user's home, `expanduser("~")` is only
    correct when the process runs as that user. No `/home/*` scanning — that would be guessing.

12. **Subsystem content** (§7). `--subsystem` launches (Neo Geo CD in the matrix) use core-declared save extensions —
    the one path where RetroArch itself writes something other than `.srm`/`.rtc`. Unhandled and unmarked.

13. **Cores unloadable from the host.** `applewin_libretro.so` needs sandbox-only libraries; `query_core` honestly
    fails. Optional: probe inside the sandbox (`flatpak run --command=...`) as a fallback — evaluate cost before
    building.

14. **Research follow-ups.** Paper Mario stage 2 (FlashRAM region location, §12); ParaLLEl N64 same-game comparison;
    arcade content through Flycast (§8 [O]) — the names are settled for both branches, but which ports a per-content
    mode covers is not: Naomi connects B1/C1, so `VMU A1` moves nothing there. Needs a live Naomi run.

Every task lands with vectors; expectation values are adjudicated against emulator source or live observation, never
invention.
