# Design

Status: **settled** — rewritten after the RetroDECK/RetroArch research (`docs/research/retrodeck-save-placement.md`).
Supersedes the phase-1 draft, which framed atlas as a knowledge library; the research showed that framing reproduces
exactly the trap the README names. Nothing here is API-frozen; the _decisions_ are settled, signatures are not.

## What atlas is

**A resolver, not a lookup.** Atlas answers questions about the running machine by reading the running machine — the
same files, in the same order, with the same fallbacks the emulator itself uses. It never answers a question from a
shipped table when the answer exists on the machine.

The boundary rule that decides every "table or live?" question:

> **What is on the running machine is read — always. What is written nowhere on the machine is world knowledge, and
> world knowledge is marked, versioned, and source-cited.**

Why: user configuration drifts daily (a settings toggle, an override file, a moved folder); emulator _behaviour_ drifts
over years. A shipped path table is stale on the first user click. A reading procedure is stale only when the emulator
changes its logic — and that is version-pinnable. The research produced the receipts: a stock RetroDECK ships a per-core
override that flips the save layout for PPSSPP; a real installation showed user drift that survived updates; Flycast
changes its save _root_ based on a core option. Every one of these is invisible to a lookup and trivial for a reader.

What legitimately lives _in_ atlas:

1. **Reading procedures** — where installations are found, which configs govern them, in what order they override each
   other, which fallbacks apply. Pinned to the emulator versions they were read from.
2. **Oddity rules** — per-emulator behaviour written nowhere on disk (Flycast roots its VMUs in `system_directory`;
   memory-card granularity semantics). Small, named, individually testable.
3. **The BIOS registry** — which firmware files each platform/core wants, hashes and sizes. This is world knowledge by
   nature: no config on the machine lists it. Core scope, not optional. (Whether a platform needs an _installer step_
   rather than file placement — e.g. RPCS3 firmware — is bonus knowledge; nice to have, safe to omit.)

Everything else — paths, layouts, active settings, deviations from defaults, which files a save actually consists of —
is read or observed, never stored.

## The two entry points

```python
import atlas

installations = atlas.detect(home="/home/deck")
# -> every arrangement present on the machine, each as its own handle.
#    Never a silently chosen winner: ambiguity is a truthful result.

inst = installations[0]              # e.g. RetroDeck(root=..., health=...)
entries = inst.emulators_for("n64")  # the catalogue: (system, emulator) pairs in launch-priority order
emu = entries[0]                     # one emulator, as it is configured right now

emu.save_location(content_path="/.../roms/n64/Paper Mario (USA).zip")
emu.bios_location()
emu.save_granularity                 # per-game file / per-game folder / shared card — with the config that selects it
```

- **Installations are handles.** Every question is asked _of an installation_, never of a global "the system". A machine
  can carry RetroDECK, EmuDeck and a bare RetroArch side by side; each answers for itself. No cross-installation
  fall-through: a RetroDECK handle never borrows a coexisting install's config.
- **Detection labels markers, it does not partition.** EmuDeck _is_ a configured `org.libretro.RetroArch` — both
  descriptions of the same RetroArch are true at once, so marker checks are ordered (EmuDeck before "bare standalone")
  and a handle may carry more than one description.
- **Detection reports health.** Present-and-complete, config-readable-but-root-missing (unmounted SD card), config
  unreadable. A syntactically correct path into an absent mount is the classic silent failure; health makes it a stated
  one. The config is the truth, never the existence of a folder — a stale secondary root must not win.
- **The emulator handle means "as currently configured".** Granularity, roots, and modes are config readings with
  provenance, not static facts. Where an alternative mode exists (Flycast per-game VMUs), the handle names the config
  that selects it.
- **The catalogue can be absent.** RetroDECK always ships ES-DE; EmuDeck may use ES-DE, Pegasus, or Steam Rom Manager;
  bare RetroArch has no catalogue at all. Three different questions, three different sources: _choice_ (frontend launch
  entries, first = default), _capability_ (core `.info` `systemid` — which cores can run a platform), _fact_ (RetroArch
  playlists — which core actually launched a ROM). When no catalogue exists, the caller names the core; atlas does not
  invent a default.

## The machine seam

All machine access goes through one injected seam. It abstracts **the machine**, not "text files":

```python
class Machine(Protocol):
    def read_text(self, path: str) -> str | None: ...
    def glob(self, pattern: str) -> list[str]: ...
    def exists(self, path: str) -> bool: ...
    def readlink(self, path: str) -> str | None: ...      # symlink target, or None if not a link
    def query_core(self, so_path: str) -> CoreInfo | None: ...  # retro_get_system_info, or None if unloadable
```

- `readlink` exists because RetroDECK's whole standalone save architecture is symlinks (`dir_prep`): the emulator-side
  path and the real path are two truthful answers to different questions, and a dead link (`exists` → false, link
  present) is a real state the resolver must be able to see.
- `query_core` exists because `library_name` — the value that names sort-by-core directories _and_ the override
  directory — lives only in the core binary. Loading the core and asking it is the same read RetroArch performs; it is a
  live read, not a table. The production implementation is process-isolated (a crashing core costs one answer, not the
  host process) and may cache per `.so` mtime/size — a memoized live read, never shipped data. `.info` files are **not**
  a substitute: `corename` disagrees with `library_name` for 56 of 210 installed cores.
- In production the seam is the real filesystem plus a real core prober. In tests and conformance vectors it is a
  **fixture machine**: files, symlinks, and core answers as plain data describing a whole machine — including broken
  links and unloadable cores. One code path, two data sources; everything the resolver does is vector-testable,
  including the failure states.

## Placements

A placement answers "where does this emulator, configured as it is, keep this save?". Its shape follows four research
findings:

- **Directory and file set are different kinds of knowledge.** The directory follows from one central rule (RetroArch's
  own path math, verified in `runloop.c`) and is answerable for every core at once. The file set is per-core behaviour
  with no metadata source. So the placement's directory is always resolvable; its file set may honestly be _unknown_ —
  and for existing saves atlas can **observe** the set (`glob("<rom_stem>.*")`) instead of knowing it.
- **A hole is not an unknown.** `needs` lists holes the caller fills from the ROM at hand (`<rom_stem>`,
  `<content_dir>`). _Unknown_ means atlas cannot state the value and refuses to guess. These are distinct states and the
  type keeps them distinct.
- **The root varies.** `savefile_directory`, `system_directory` (Flycast VMUs), or the ROM's own directory
  (`savefiles_in_content_dir`, or an unset save dir — RetroArch resolves that itself, it is not a hole).
- **Filesystem state is part of the answer.** RetroArch silently reverts to the unsorted root when the sorted directory
  cannot be created; a placement may therefore carry a state-dependent caveat, checked through the seam.

Every answer carries provenance: which config file produced each governing value, which default applied, which override
won. Where a shipped reference config is readable on the machine (RetroDECK's Flatpak deployment; a distro's
`/etc/retroarch.cfg`), atlas can additionally report deviation from it — read live, not hardcoded. Where no reference
exists, the comparison is honestly omitted; the answer itself is unaffected.

## Answer guarantee

The guarantee is precise, not absolute: **atlas reads the configs the way the emulator reads them, as of the pinned
emulator versions its procedures were extracted from.** If RetroArch changes its resolution logic, atlas must follow —
that is the one drift a resolver cannot remove, only version-pin and cite. Provenance makes every answer checkable
rather than merely asserted.

## Consumption

| Consumer                           | Path                                                                    |
| ---------------------------------- | ----------------------------------------------------------------------- |
| Python clients (decky-romm-sync)   | import directly; vendor by copying (`pip install --target py_modules/`) |
| Non-Python clients (grout, argosy) | implement the resolver natively; prove conformance against the vectors  |
| Non-Python tools on a Python host  | optional `python -m atlas … --json` process call                        |

`dependencies = []` is a contract, not an accident: zero-dependency pure Python is what makes vendoring a directory copy
— no compiled parts, no architecture question, no version conflicts inside a plugin bundle.

## Vectors

The conformance vectors are the portable artifact: fixture machine in (files, symlinks, core answers), expected answers
out. A port that passes them demonstrably reads the machine the way the reference does. They are the contract for
_resolver behaviour_ — not a data set to re-ship.

## Vocabulary

Atlas defines canonical system ids and ships translation tables for the dialects it meets (RomM `slug` / `fs_slug`,
ES-DE `system`, RetroArch core and database names). Public functions accept canonical ids; translators are explicit
(`atlas.systems.from_romm_slug("gba")`), never guessed.

## Settled decisions

- **Resolver, not knowledge base.** "Static lists are the trap" applies to atlas's own data as much as to wiki path
  lists. The plan to ship extracted `library_name` tables as package data is rejected for the same reason it was
  rejected for everyone else: it drifts. Live reads through the seam replace every table whose content exists on the
  machine.

- **Python reference + vectors; no native core.** The original reasoning ("the rules are small") did not survive the
  research — the rules are not small. The decision survives on the other leg, now stronger: atlas is the wrong shape for
  a C ABI (callback FFI across the seam, string ownership, variable-length results), and more rules across an FFI
  boundary make that worse, not better. Ports reimplement the resolver and prove themselves against the vectors; the
  known consumers confirm the model (decky imports Python directly; grout/argosy would have had to build, bind, and
  callback into a C library on ARM/Android anyway). Revisit only on a concrete drop-in request.

- **No cross-installation fall-through.** Unchanged: a RetroDECK install with no own `retroarch.cfg` gets RetroDECK's
  defaults, never a coexisting install's cfg.

- **Never guess.** Distinct states for "caller fills this hole" and "atlas does not know". Degradation is always
  explicit: missing reference config → deviation check omitted; unloadable core → `library_name` unknown; absent
  catalogue → caller names the core. No answer is ever invented to keep a field non-empty.

- **`.info` is never a path source.** `corename` ≠ `library_name` for 27% of installed cores; the bsnes variants would
  split one real save directory into three fictional ones. `.info` serves capability queries only.

- **BIOS registry is core world knowledge; installer-step knowledge is bonus.** Which files, hashes, and locations a
  platform needs is not on the machine and belongs in atlas, versioned and cited. Whether firmware needs an installer
  run instead of file placement is useful but omittable.

## Open questions

- Exact seam signatures (`CoreInfo` shape; error reporting for crashed vs. missing cores).
- Vector-format encoding for symlinks and core answers.
- The catalogue API when multiple frontends coexist on one EmuDeck install.
- Health representation: field on the handle vs. part of provenance.
- Exact canonical system-id set (lean toward ES-DE names).
