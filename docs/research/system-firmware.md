# Which systems need firmware — the question a `.info` cannot answer

Sibling to [`core-audit.md`](core-audit.md), which is about **save behaviour** and stays that way. This one is about a
different question with a different method: not "where does this core write", but "will this system start at all without
a firmware file". The two share only the discipline — unfiltered reads, source or observation behind every claim, and a
verdict that says what it rests on.

## The defect this exists for

A consumer's BIOS page showed "Nothing required" in green over a PlayStation that cannot boot. Nothing was wrong with
the consumer, and nothing was wrong with atlas: `need` comes straight from `firmware<N>_opt`, and every SwanStation
firmware entry in libretro's own catalogue is declared optional. The report was faithful and the picture it produced was
false.

## The two readings, and why neither alone answers the question

**The catalogue reading** — a core's `<core>.info`, enumerated the way RetroArch enumerates it
(`atlas.core_info.enumerate_firmware`, a port of `core_info_resolve_firmware`). It says **what RetroArch reports**:
which files it will look for, and which of them it labels required. That is a real and useful fact, and it is what atlas
reports today.

**Reports, not refuses — and that distinction is the whole case.** It is tempting to write that RetroArch will not
launch without a required file. It does not. At the pinned revision `a79435a`, `core_info_list_update_missing_firmware`
has exactly one caller, `menu/menu_displaylist.c:880`, and that caller builds the core-information page's labels —
`MISSING_OPTIONAL`, `MISSING_REQUIRED`, `PRESENT_OPTIONAL`, `PRESENT_REQUIRED` (`menu/menu_displaylist.c:882-887`).
Nothing on that path stops a load. The setting that would block, `check_firmware_before_loading`, is **off in both
configurations RetroDECK ships** — `rd_config/retroarch.cfg:65` and the live
`~/.var/app/net.retrodeck.retrodeck/config/retroarch/retroarch.cfg:65` each read `"false"` — so on this machine nothing
blocks at all.

That makes the observed refusal _stronger_ evidence, not weaker. The catalogue says optional, the frontend only labels,
the blocking setting is off, and Beetle PSX still stopped and named `scph5501.bin` on screen. **The core refused, not
the frontend.** That is precisely the contradiction this table exists to record.

One provenance note, because the repo's rule is to cite RetroArch at the pin: `check_firmware_before_loading` is **not**
in the source at `a79435a` — a raw byte scan over all 10,832 files finds it nowhere. It is in the deployed build, whose
binary carries the literal and reports version `1.22.2`, and in the two config files above. So the setting is a
`[V-binary]` plus config reading of what is deployed, not a source citation, and the repo's RetroArch pin now predates a
setting the shipped build has.

**A slot is written three ways and read two.** `firmware<N>_opt` may say optional, may say required, or may be
**absent** — and absent means required. The behaviour is in the source, not in a comment: `core_info.c:1584-1585`
`calloc`s the slot array, so `optional` starts false, and `core_info.c:1603-1604` writes the flag **only** when
`config_get_bool` succeeds, leaving that zero in place when the key is absent or unparseable. libretro's own template
says the same thing in words, at `00_example_libretro.info:47`:

```
# Is firmware optional or not, if not defined RetroArch will assume it is required
```

The line is documentation, not a load-bearing citation, and it is nearly gone: of the 292 `.info` files deployed,
**two** still carry it — the template itself and `puzzlescript_libretro.info:45`. `atlas.core_info` ports the source
rule, so an absent flag reads as required everywhere in atlas and `need` says `required`. One deployed core is written
that way: `ecwolf`, whose single `ecwolf.pk3` slot carries no `_opt` at all. Of the 118 cores declaring firmware, 117
state `_opt` on every slot, exactly one states it on none, and none are mixed. The third shape is worth naming because
reading an absent flag as optional is the mistake that would make a hard requirement look like no requirement at all.

What the format cannot say is anything about the machine being emulated. It carries one boolean per file. There is no
way to write "one of these three", and no way to write "this system does not start without one of them". An author who
knows a PlayStation needs one regional BIOS therefore has two moves, and both lose the fact:

| move                      | what the file says                        | what RetroArch then reports                           |
| ------------------------- | ----------------------------------------- | ----------------------------------------------------- |
| mark every image required | Beetle PSX: `scph5500/5501/5502` required | all three as "Missing, Required", though one would do |
| mark every image optional | SwanStation: all five images optional     | all five as "Missing, Optional", though one is needed |

Read from the deployed catalogue on the reference machine, that is exactly what the PlayStation cores do. All **eight**
entries that declare firmware under `systemname = "PlayStation"`, not a selection of them:

```
duckstation               scph5500.bin! scph5501.bin! scph5502.bin!                              (no .so deployed)
mednafen_psx              scph5500.bin! scph5501.bin! scph5502.bin! psxonpsp660.bin? ps1_rom.bin?
mednafen_psx_hw           scph5500.bin! scph5501.bin! scph5502.bin! psxonpsp660.bin? ps1_rom.bin?
pcsx1                     scph5500.bin? scph5501.bin? scph5502.bin?                              (no .so deployed)
pcsx_rearmed              scph5500.bin? scph5501.bin? scph5502.bin? psxonpsp660.bin?
pcsx_rearmed_interpreter  scph5500.bin? scph5501.bin? scph5502.bin?                              (no .so deployed)
pcsx_rearmed_neon         scph5500.bin? scph5501.bin? scph5502.bin?                              (no .so deployed)
swanstation               psxonpsp660.bin? scph5500.bin? scph5501.bin? scph5502.bin? ps1_rom.bin?
```

(`!` required, `?` optional.) One machine, one requirement, eight entries that disagree about it — three on the required
side, five on the optional side. A ninth entry, `rustation`, carries the same `systemname` and declares no firmware at
all, so it takes no part. Four of the eight have no binary deployed, which matters below.

**The options reading** — the core's own registered options, read unfiltered off the shipped `.so`
(`RealMachine.query_core`). It says **what the core can do**, which is a different question again. Every BIOS-related
option the four loadable PlayStation cores register, listed in full rather than filtered, because a filtered list is how
the first draft of this page came to claim something false:

```
swanstation_BIOS_PathNTSCJ      default scph5500.bin  values scph5500.bin, psxonpsp660.bin, ps1_rom.bin
swanstation_BIOS_PathNTSCU      default scph5501.bin  values scph5501.bin, psxonpsp660.bin, ps1_rom.bin
swanstation_BIOS_PathPAL        default scph5502.bin  values scph5502.bin, psxonpsp660.bin, ps1_rom.bin
swanstation_BIOS_PatchFastBoot  default false         values true, false
pcsx_rearmed_bios               default auto          values auto, HLE
pcsx_rearmed_show_bios_bootlogo default disabled      values disabled, enabled
beetle_psx_override_bios        default disabled      values disabled, psxonpsp, ps1_rom
beetle_psx_skip_bios            default disabled      values disabled, enabled
beetle_psx_hw_override_bios     default disabled      values disabled, psxonpsp, ps1_rom
beetle_psx_hw_skip_bios         default disabled      values disabled, enabled
```

`beetle_psx` and `beetle_psx_hw` there are **option namespaces, not core keys.** The cores that register them ship as
`mednafen_psx_libretro.so` and `mednafen_psx_hw_libretro.so`, and a key in this repo is the `.so` short name rather than
the display nickname — so those two are `mednafen_psx` and `mednafen_psx_hw` everywhere a core is named, including in
the table's own entry.

SwanStation's three _path_ slots offer nothing but BIOS image file names, so on those three there is no way to say "run
without one" — which is what makes its all-optional declaration an understatement. PCSX ReARMed's single `bios` option
carries `HLE`, a value that is not a file at all.

**What this reading does not settle, and a careful reader will ask.** Both Beetle cores register `skip_bios`
(`disabled`/`enabled`), and their `override_bios` values are `disabled`, `psxonpsp` and `ps1_rom` — symbolic names, not
file names, and `disabled` is not an image. What either switch does with no image present has not been observed here,
and this page does not claim it. Nor is the reading complete: only four of the nine PlayStation entries have a binary
deployed, so five were never read at all. The options reading is evidence, not a census.

Neither reading answers "is this file needed" on its own. The catalogue is about the emulator's launch check; the
options are about one core's capabilities. The fact a user actually wants — _this system does not run without firmware_
— is written nowhere on the machine, which by the boundary rule in `CLAUDE.md` makes it world knowledge: marked,
versioned, and source-cited, in `atlas/data/system_firmware.json`.

## The derivation, and what it cannot see

The discriminator is the **system**, not the core. `mgba`, `snes9x` and `gambatte` declare everything optional, and so
does `swanstation` — but SwanStation says it about a machine observed refusing to start without a BIOS. Same
declaration, and what could tell them apart is the system behind it, not the wording. (Whether those three are _right_
is not this page's to say: `gambatte` sits under `Game Boy/Game Boy Color` and `mgba` under Game Boy Advance, and this
table records both systems as `open`. Naming them here as correct would be the exact unmarked judgement the table
refuses to make.)

Where one core of a system declares a file required and another declares every file optional, the machine is already
carrying the answer, in the other core's entry. `tests/test_system_firmware_tripwire.py` recomputes those collisions
from the `.info` files RetroDECK deploys and fails when it finds a system `system_firmware.json` records no verdict for.
Measured against the deployed catalogue at the time of writing:

| measurement                                               | count |
| --------------------------------------------------------- | ----- |
| `*_libretro.info` deployed                                | 291   |
| of those, declaring firmware at all                       | 118   |
| of those, declaring every file optional                   | 81    |
| named systems whose catalogue declares firmware           | 65    |
| systems whose cores disagree                              | 7     |
| systems where every declaring core says all-optional      | 36    |
| systems where every declaring core states a required file | 22    |

The seven are PlayStation, Saturn, Sega Dreamcast, Neo Geo, Lynx, Game Boy Advance, and Game Boy/Game Boy Color.

**Each number carries its counting rule, because two reasonable rules give two different answers.** "Declaring firmware"
above means _the slots RetroArch's own enumeration returns_ — `atlas.core_info.enumerate_firmware` over the parsed file
— which is 118. A line-loose text scan for `firmware_count = <non-zero>` returns **119**: the extra file is
`00_example_libretro.info`, libretro's template, whose line 40 reads `# firmware_count = 7` behind a comment marker. It
is not a core and declares nothing, and the RetroArch grammar the parser ports drops the line. Two files carry a
commented `firmware_count` (`00_example`, `b2`); only the template's is non-zero, which is why the scan is off by
exactly one. Counts here come from the parser, never from grep.

Every number above comes from the tripwire's own reading of the deployed `.info` files, and it is dated: the canary
deploys a newer RetroDECK every week, so recount rather than trusting the table.

```python
from tests.test_system_firmware_tripwire import DEPLOYED_CORES, read_catalogue, systems_whose_cores_disagree

catalogue = read_catalogue(DEPLOYED_CORES)
print("named systems:", len(catalogue))
print("disagreeing:", sorted(systems_whose_cores_disagree(catalogue)))
print("all-optional throughout:", sorted(s for s, c in catalogue.items() if all(c.values())))
```

`read_catalogue` drops the cores stating no `systemname`, so it accounts for 116 of the 118 declaring entries; the two
it leaves out are `galaksija` and `skyemu`, which name no system between them.

Four things about the derivation are worth stating rather than leaving to be discovered:

- **It groups by the raw `systemname`.** That is the unit the catalogue itself groups firmware by. Translating into
  atlas's ES-DE vocabulary is not free — `Game Boy/Game Boy Color` is one `systemname` over two catalogue systems — and
  it is a decision for the cut that makes an answer carry this table, not for the cut that records it.
- **It skips a core stating no `systemname`.** An empty string names no system, and lumping such cores together would
  invent one and could manufacture a disagreement between two unrelated emulators. `atlas.firmware.system_decision`
  answers `_unknown` for the same input for the same reason.
- **A core stating no `_opt` at all counts as declaring required firmware**, by RetroArch's default above, so it
  contradicts an all-optional sibling instead of falling between the two readings. `ecwolf` is the one core written that
  way today and it sits alone under its `systemname`, so nothing turns on it yet — but the day a core of that shape
  appears beside an all-optional one, the tripwire fires rather than going quiet, and a test pins that rather than
  leaving it to the port.
- **It reads every deployed `.info`, with or without a `.so` beside it.** The two sets differ: 291 catalogue entries
  against 211 binaries on the reference machine. The declarations are what the derivation is about, so filtering to the
  cores this deployment happens to be able to load would make the evidence an accident of one installation — 96 of the
  118 declaring entries here have a binary, and holding the derivation to those loses three of the seven disagreements.

**And what it cannot see: a system where every core understates.** There is no disagreement to find, so the derivation
is silent. **36** systems declare firmware with every declaring core saying all-optional, and the derivation says
nothing about any of them.

Three of those 36 are worth naming, and precisely what is claimed about them matters:

- **[D]** `3DO` (opera), `SNK Neo Geo CD` (neocd) and `PC-98` (np2kai) are each the **only** core declaring firmware
  under their `systemname`, so there is no second entry that _could_ contradict them. That is derived from the catalogue
  and is checkable.
- **[O]** Whether any of the three actually needs firmware to run is **unestablished**. Nothing here has observed one,
  and this page does not say they understate.
- The other 33 have not been examined either. Naming three is not a claim that the remaining 33 were checked and cleared
  — **how many of the 36 understate is exactly what the derivation cannot say**, and no number here should be read as an
  answer to it.

The tripwire is a **floor on what is recorded**, never a proof that the record is complete, and a green run must not be
read as the second thing.

## The canary tier

The tripwire is a member of the machine-bound tier, which an ordinary CI run skips because no emulator is deployed on a
bare runner. `.github/workflows/canary.yml` deploys the latest RetroDECK from Flathub weekly at exactly those paths,
refuses a deploy that would let the tier silently skip, runs the whole suite, and files or updates a drift issue when it
goes red. So a release that ships a core introducing a new disagreement opens that issue by itself, within the week and
days before any reference machine updates. The failure asks for a verdict, and `open` is a verdict.

## What a verdict must cite

An entry holds a verdict, the evidence level it rests on, and a source saying what that level read.

- **`cannot-run-without-firmware`** — the system does not start with none present.
- **`runs-without-firmware`** — it does. Recorded rather than left out: "checked, and the answer is no" is knowledge,
  and an absent entry is not.
- **`open`** — nobody has established which. A real value, and the one that makes a disagreement _visible_ instead of
  merely unrecorded.

Levels are the repo's own — `[V]` verified, `[D]` derived, `[O]` open — and the loader ties them to the verdict: `open`
carries `[O]` and nothing else does, because a verdict that states something cannot rest on an open question and an open
question cannot claim a verified reading. The `source` prose uses the finer markers the other tables use: `[V-live]` an
observation on a real machine, `[V-binary]` a read of the shipped core, `[V-script]` the arrangement's own scripts.

**A catalogue disagreement is never itself a verdict.** It is what makes the question worth asking. Only an observation
or a source read can close it, which is why six of the seven shipped entries are `open`: the disagreement is derived,
and nothing beyond it has been established.

The one entry that is not `open` rests on three device observations and one binary read. The observations are
SwanStation never starting, Beetle PSX refusing at load and naming `scph5501.bin` on screen, and PCSX ReARMed playing a
game through with no BIOS present; the binary read is SwanStation's three path slots above, whose values are all image
file names. Its `source` also records what it does **not** establish — the two unobserved Beetle switches, and the four
declaring entries whose binaries were never read — because a verdict that hides its own gaps is the kind a reader stops
trusting the moment they find one.

## Exempting a core that is right

A system that needs firmware can still have a core that runs without a file, because the core supplies its own
substitute. Its catalogue entry declares everything optional and is _correct_ to, so it has to be nameable.
`cores_supplying_an_alternative` is that place, on a `cannot-run-without-firmware` entry only, and each core carries its
own reason: a bare exemption excuses by assertion.

**What the field is for, precisely, because it is easy to overstate.** It is recorded for the cut that makes an answer
read this table, where a system needing firmware must not be reported against a core that carries its own substitute. It
is **not** what keeps the tripwire green. The derivation compares whole systems and never consults the field, so
removing it fails no check and changes no verdict — measured by deleting it and re-running, not assumed. The one check
that consumes it today is the staleness one below.

PCSX ReARMed is the case the field was written around, and its entry states its own limits. The core registers a `bios`
option carrying `HLE`, and it was observed playing a game through with no BIOS present. Which of its two values was in
force during that run is not recorded, and nothing establishes what `auto` does when no image is present. It is also not
claimed to be the only PlayStation core that can run without an image: the Beetle cores register an unobserved
`skip_bios` switch, and four of the eight declaring entries have no binary deployed here, so their options were never
read. The exemption rests on the one core that was watched running without firmware.

The list is not a completeness claim. It holds the cores whose substitute has been established; a core absent from it is
one nobody has looked at, never one judged to be understating. The tripwire checks the other direction — a named core
must still be declaring everything optional for that system in the deployed catalogue, or the exemption is stale and
excusing nothing.

## Scope

This document and the table describe knowledge only. No answer carries a verdict today: `need` still comes straight from
`firmware<N>_opt`, no field is added, no caveat is emitted, and no requirement is grouped. Making an answer carry it is
separate work, and it needed the table to exist first.
