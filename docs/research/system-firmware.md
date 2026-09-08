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
(`atlas.core_info.enumerate_firmware`, a port of `core_info_resolve_firmware`). It says **what RetroArch will do**:
which files it will look for, and which of them it will refuse to launch without. That is a real and useful fact, and it
is what atlas reports today.

What it cannot say is anything about the machine being emulated. The format carries one boolean per file. There is no
way to write "one of these three", and no way to write "this system does not start without one of them". An author who
knows a PlayStation needs one regional BIOS therefore has two moves, and both lose the fact:

| move                      | what the file says                        | what RetroArch then does                  |
| ------------------------- | ----------------------------------------- | ----------------------------------------- |
| mark every image required | Beetle PSX: `scph5500/5501/5502` required | demands all three, though one would do    |
| mark every image optional | SwanStation: all five images optional     | demands none, though the system needs one |

Read from the deployed catalogue on the reference machine, that is exactly what the PlayStation cores do:

```
swanstation      psxonpsp660.bin? scph5500.bin? scph5501.bin? scph5502.bin? ps1_rom.bin?
pcsx_rearmed     scph5500.bin? scph5501.bin? scph5502.bin? psxonpsp660.bin?
mednafen_psx     scph5500.bin! scph5501.bin! scph5502.bin! psxonpsp660.bin? ps1_rom.bin?
mednafen_psx_hw  scph5500.bin! scph5501.bin! scph5502.bin! psxonpsp660.bin? ps1_rom.bin?
duckstation      scph5500.bin! scph5501.bin! scph5502.bin!
```

(`!` required, `?` optional.) One machine, one requirement, five entries that disagree about it.

**The options reading** — the core's own registered options, read unfiltered off the shipped `.so`
(`RealMachine.query_core`). It says **what the core can do**, which is a different question again, and it is often the
half that settles the case. SwanStation registers three region BIOS _path_ slots and every value they offer is a file
name:

```
swanstation_BIOS_PathNTSCJ  default scph5500.bin  values scph5500.bin, psxonpsp660.bin, ps1_rom.bin
swanstation_BIOS_PathNTSCU  default scph5501.bin  values scph5501.bin, psxonpsp660.bin, ps1_rom.bin
swanstation_BIOS_PathPAL    default scph5502.bin  values scph5502.bin, psxonpsp660.bin, ps1_rom.bin
pcsx_rearmed_bios           default auto          values auto, HLE
```

There is no value in SwanStation's three that stands for "no file", so its all-optional declaration is an
understatement. PCSX ReARMed's single option has one, which is why the same declaration from that core is not.

Neither reading answers "is this file needed" on its own. The catalogue is about the emulator's launch check; the
options are about one core's capabilities. The fact a user actually wants — _this system does not run without firmware_
— is written nowhere on the machine, which by the boundary rule in `CLAUDE.md` makes it world knowledge: marked,
versioned, and source-cited, in `atlas/data/system_firmware.json`.

## The derivation, and what it cannot see

The discriminator is the **system**, not the core. `mgba`, `snes9x` and `gambatte` declare everything optional and are
simply right. `swanstation` declares everything optional about a machine that needs a BIOS. Same declaration; what tells
them apart is the system behind it.

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

Every number above comes from the tripwire's own reading of the deployed `.info` files, never from a text scan, and it
is dated: the canary deploys a newer RetroDECK every week, so recount rather than trusting the table.

```python
from tests.test_system_firmware_tripwire import DEPLOYED_CORES, read_catalogue, systems_whose_cores_disagree

catalogue = read_catalogue(DEPLOYED_CORES)
print("named systems:", len(catalogue))
print("disagreeing:", sorted(systems_whose_cores_disagree(catalogue)))
print("all-optional throughout:", sorted(s for s, c in catalogue.items() if all(c.values())))
```

`read_catalogue` drops the cores stating no `systemname`, so it accounts for 116 of the 118 declaring entries; the two
it leaves out are `galaksija` and `skyemu`, which name no system between them.

Three things about the derivation are worth stating rather than leaving to be discovered:

- **It groups by the raw `systemname`.** That is the unit the catalogue itself groups firmware by. Translating into
  atlas's ES-DE vocabulary is not free — `Game Boy/Game Boy Color` is one `systemname` over two catalogue systems — and
  it is a decision for the cut that makes an answer carry this table, not for the cut that records it.
- **It skips a core stating no `systemname`.** An empty string names no system, and lumping such cores together would
  invent one and could manufacture a disagreement between two unrelated emulators. `atlas.firmware.system_decision`
  answers `_unknown` for the same input for the same reason.
- **It reads every deployed `.info`, with or without a `.so` beside it.** The two sets differ: 291 catalogue entries
  against 211 binaries on the reference machine. The declarations are what the derivation is about, so filtering to the
  cores this deployment happens to be able to load would make the evidence an accident of one installation — 96 of the
  118 declaring entries here have a binary, and holding the derivation to those loses three of the seven disagreements.

**And what it cannot see: a system where every core understates.** There is no disagreement to find, so the derivation
is silent. Three systems are in exactly that position in the deployed catalogue — `3DO` (opera), `SNK Neo Geo CD`
(neocd) and `PC-98` (np2kai), each declaring firmware and each declaring all of it optional, with no second core to
contradict them. The tripwire is a **floor on what is recorded**, never a proof that the record is complete, and a green
run must not be read as the second thing.

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

The one entry that is not `open` cites three device observations and one binary read: SwanStation never starting, Beetle
PSX refusing at load and naming `scph5501.bin` on screen, PCSX ReARMed playing a game through with no BIOS present, and
the option tables above.

## Exempting a core that is right

A system that needs firmware can still have a core that runs without a file, because the core supplies its own
substitute. Its catalogue entry declares everything optional and is _correct_ to, so it has to be nameable — otherwise
the tripwire reads a right entry as an understatement. `cores_supplying_an_alternative` is that place, on a
`cannot-run-without-firmware` entry only, and each core carries its own reason: a bare exemption excuses by assertion.

PCSX ReARMed is the case the field was written around, and its entry states its own limit. The core offers an `HLE`
value where no other deployed PlayStation core offers anything but a choice between files, and it was observed playing a
game through with no BIOS present. Which of its two values was in force during that run is not recorded, and nothing
establishes what `auto` does when no image is present. The exemption rests on the observation that the core ran, not on
a reading of what either value means.

The list is not a completeness claim. It holds the cores whose substitute has been established; a core absent from it is
one nobody has looked at, never one judged to be understating. The tripwire checks the other direction — a named core
must still be declaring everything optional for that system in the deployed catalogue, or the exemption is stale and
excusing nothing.

## Scope

This document and the table describe knowledge only. No answer carries a verdict today: `need` still comes straight from
`firmware<N>_opt`, no field is added, no caveat is emitted, and no requirement is grouped. Making an answer carry it is
separate work, and it needed the table to exist first.
