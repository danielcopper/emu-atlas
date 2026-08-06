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
3. **Firmware identities** — the `md5`/`sha1`/`size` triple that says what a correct firmware file's bytes are. This is
   world knowledge by nature: no config on the machine lists it. Core scope, not optional. Which files a core _wants_ is
   **not** in this list: RetroArch ships that declaration in the `.info` file next to every core, so it is read off the
   machine like everything else. (Whether a platform needs an _installer step_ rather than file placement — e.g. RPCS3
   firmware — is bonus knowledge; nice to have, safe to omit.)

Everything else — paths, layouts, active settings, deviations from defaults, which files a save actually consists of —
is read or observed, never stored.

## The two entry points

```python
import atlas

installations = atlas.detect(home="/home/deck")
# -> every arrangement present on the machine, each as its own handle (one Installation
#    protocol: kind, kinds, root, health, save_location, plus the four firmware calls below).
#    Never a silently chosen winner: ambiguity is a truthful result.

inst = installations[0]              # e.g. a RetroDeck handle — live, re-reads its sources per query
inst.health()                        # structured: a tuple of issue caveats with stable codes; ok = no issues

inst.save_location(content_path="/.../roms/n64/Paper Mario (USA).z64",
                   core_so="mupen64plus_next_libretro.so")
# -> the primary route: the caller names the core. It is the only save route on the handles
#    with no frontend catalogue — EmuDeck, the standalone Flatpak, the native install.

entries = inst.emulators_for("n64")  # the catalogue: launch entries in effective priority order
emu = entries[0]                     # RetroDECK handles only today — one emulator, as configured right now
emu.save_location(content_path="/.../roms/n64/Paper Mario (USA).z64")   # the entry carries its own core

# -> SavePlacement (dir, root_kind, needs, file_set, granularity, caveats, fallback_dir, physical_dir),
#    or — on the entry route — Unresolved (a typed domain outcome, e.g. a standalone entry
#    before that block lands).
#    Granularity — per-game file / shared card, with the option that selects it — is part of the placement.
```

```python
inst.firmware_for_core(core_so="mgba_libretro.so")   # does this emulator need firmware, and where does it go?
inst.firmware_for_system(system="gb")                # which emulators run this system, and what does each want?
inst.firmware_inventory(verify=True)                 # everything installed, plus what nobody asks for
inst.identify_firmware(md5="32fbbd84...")            # this content — where does it go, under what name?
# -> the first three: FirmwareAnswer (root, cores, unclaimed, hash_checked, sources, caveats).
#    identify_firmware answers off content, so it has its own shape:
#    FirmwareIdentification (identity, known_as, requirements, sources, caveats).
#    A firmware requirement belongs to an EMULATOR: the core decides the file
#    name and the absolute destination, the packaged identity decides whether
#    what lies there is the right thing. Two axes, never merged:
#      need     required | optional            — what the emulator asks for
#      checked  verified | mismatch | unchecked | unknown  — what the machine says
#    "unchecked" (we did not look) and "unknown" (we looked and cannot tell) are
#    different answers. Having no declaration at all is a caveat on an EMPTY
#    answer, because empty is honest and "nothing missing" would be a lie.
```

- **Installations are handles.** Every question is asked _of an installation_, never of a global "the system". A machine
  can carry RetroDECK, EmuDeck and a bare RetroArch side by side; each answers for itself. No cross-installation
  fall-through: a RetroDECK handle never borrows a coexisting install's config.
- **Detection labels markers, it does not partition.** EmuDeck _is_ a configured `org.libretro.RetroArch` — both
  descriptions of the same RetroArch are true at once, so marker checks are ordered (EmuDeck before "bare standalone")
  and a handle may carry more than one description.
- **Detection reports health, structurally.** Detection triggers on marker _existence_; health separates marker read
  status, parse status, root state, and required-companion state into individual issue caveats with stable codes — a
  present-but-broken installation (unreadable marker, unmounted SD card, stale EmuDeck whose claimed RetroArch config is
  gone) is detected and states its issues, never invisible and never "ok". The config is the truth, never the existence
  of a folder — a stale secondary root must not win.
- **The emulator handle means "as currently configured".** Granularity, roots, and modes are config readings with
  provenance, not static facts. Where an alternative mode exists (Flycast per-game VMUs), the handle names the config
  that selects it.
- **The catalogue can be absent.** RetroDECK always ships ES-DE; EmuDeck may use ES-DE, Pegasus, or Steam Rom Manager;
  bare RetroArch has no catalogue at all. Three different questions, three different sources: _choice_ (frontend launch
  entries, first = default), _capability_ (core `.info` `systemid` — which cores can run a platform), _fact_ (RetroArch
  playlists — which core actually launched a ROM). When no catalogue exists, the caller names the core; atlas does not
  invent a default.

## The machine seam

All machine access goes through one injected seam. It abstracts **the machine**, not "text files", and every operation
reports an **explicit outcome** — failure modes are never collapsed:

```python
class Machine(Protocol):
    def read_text(self, path: str) -> ReadResult: ...     # status ok | missing | unreadable | invalid-text, plus text
    def glob(self, pattern: str) -> list[str]: ...        # *, ?, [seq] within one segment; never crosses '/'
    def path_kind(self, path: str) -> PathKind: ...       # file | directory | missing | inaccessible
    def readlink(self, path: str) -> str | None: ...      # symlink target, or None if not a link
    def query_core(self, so_path: str) -> CoreInfo | None: ...  # retro_get_system_info, or None if unloadable
    def file_size(self, path: str) -> int | None: ...     # regular files only; None = cannot tell
    def file_digest(self, path: str, algorithm: str) -> str | None: ...  # md5 | sha1; None = cannot tell
```

- The outcomes exist because the emulators branch on them — RetroArch applies a configured directory only when
  `path_is_directory()` succeeds — and because health must distinguish _missing_ from _unreadable_ from _invalid_:
  collapsing them turns a present-but-broken installation into an absent or healthy one.
- `readlink` exists because RetroDECK's whole standalone save architecture is symlinks (`dir_prep`): the emulator-side
  path and the real path are two truthful answers to different questions, and a dead link (`path_kind` → missing, link
  present) is a real state the resolver must be able to see.
- `query_core` exists because `library_name` — the value that names sort-by-core directories _and_ the override
  directory — lives only in the core binary. Loading the core and asking it is the same read RetroArch performs; it is a
  live read, not a table. The production implementation is process-isolated (a crashing core costs one answer, not the
  host process) and may cache per `.so` mtime/size — a memoized live read, never shipped data. `.info` files are **not**
  a substitute: `corename` disagrees with `library_name` for 56 of the 203 installed cores that load and declare a
  `corename` (reference machine, recounted 2026-08-05).
- `file_size` and `file_digest` exist because firmware identity is checked by content, not by name: a file present under
  the right name may still be the wrong dump. `file_size` is the free pre-filter that settles most mismatches before any
  bytes are hashed; `file_digest` is the paid answer, and the algorithm vocabulary is closed to `md5`/`sha1` so a port's
  conformance is provable.
- The seam reads the host, but the configs it reads were written from inside a sandbox: a Flatpak'd emulator spells its
  own paths `/app/...` and `/var/config/...` (the live RetroDECK cfg puts its override directory there). Every
  cfg-derived path is therefore translated to its host location before it becomes a read, per app id; a sandbox-only
  path with no host location is a `sandbox-path-untranslated` caveat, never a silently missing file. See
  `docs/research/retrodeck-save-placement.md` §6.
- In production the seam is the real filesystem plus a real core prober. In tests and conformance vectors it is a
  **fixture machine**: files (including unreadable and invalid-text ones), explicit empty directories, symlinks,
  inaccessible paths, and core answers as plain data describing a whole machine. One code path, two data sources; parity
  tests run the same cases against the fixture and a real filesystem tree, so everything the resolver does is
  vector-testable — the failure states included.

## Placements

A placement answers "where does this emulator, configured as it is, keep this save?". Its shape follows four research
findings:

- **Directory and file set are different kinds of knowledge.** The directory follows from one central rule (RetroArch's
  own path math, verified in `runloop.c`) and is answerable for every core at once. The file set is per-core behaviour
  with no metadata source. So the placement's directory is always resolvable; its file set may honestly be _unknown_ —
  and for existing saves atlas can **observe** the set (literal, glob-escaped, with RetroArch's own bookkeeping files
  filtered on source citation). _Observed_ is a snapshot of matching files, never a completeness claim; `complete` is a
  separate assertion only a source-verified rule card can make.
- **A hole is not an unknown.** `needs` lists holes someone else fills: `content_dir` from the content at hand,
  `library_name` when the core would not load, and — on the rule-card route — `system_directory` when the configs state
  none. _Unknown_ means atlas cannot state the value and refuses to guess. These are distinct states and the type keeps
  them distinct — and an empty field is never one of them: where atlas deliberately does not state a value
  (`granularity` for a core whose file set depends on options it does not interpret), a caveat says so and names what it
  depends on, because nothing separates a blank field from nothing-to-report.
- **The root varies.** `savefile_directory`, `system_directory` (Flycast VMUs), or the ROM's own directory
  (`savefiles_in_content_dir`, or an unset save dir — RetroArch resolves that itself, it is not a hole).
- **Filesystem state is part of the answer.** A sorted directory that does not exist yet is a _conditional_ result:
  RetroArch creates it on first save and silently reverts to the unsorted root when creation fails — the placement
  carries that root structurally (`fallback_dir`), and when a file blocks the creation the fallback _is_ the answer. A
  directory reached through symlinks reports the fully resolved backing path (`physical_dir`); a dead link is a stated
  caveat.

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
| Non-Python tools on a Python host  | planned: a `python -m atlas … --json` process call — no CLI ships today |

`dependencies = []` is a contract, not an accident: zero-dependency pure Python is what makes vendoring a directory copy
— no compiled parts, no architecture question, no version conflicts inside a plugin bundle.

## Vectors

The conformance vectors are the portable artifact: fixture machine in (files, dirs, symlinks, core answers — failure
states included), expected answers out. Vector files are schema-versioned; expectations are the canonical contract
serializations (`atlas/contract.py`) asserted with **exact equality** over every stable field — directories, root kinds,
holes, file sets with completeness, granularity identity, caveat codes _and_ data, health issue codes. Structured fields
are contractual; prose (sources, messages) is explicitly not. A port that passes them demonstrably reads the machine the
way the reference does. They are the contract for _resolver behaviour_ — not a data set to re-ship — and each release
publishes them as a versioned artifact.

## Vocabulary

_Planned; nothing of this is built._ Atlas is to define canonical system ids and ship translation tables for the
dialects it meets (RomM `slug` / `fs_slug`, ES-DE `system`, RetroArch core and database names), with public functions
accepting canonical ids and explicit translators (`from_romm_slug("gba")`) — never guessing. Today there is no
translation layer at all: a query takes the vocabulary of whichever source enumerates it — the frontend's system name
where a catalogue exists, an atlas slug where none does — and `firmware_for_system` states that switch as a caveat
instead of translating. The canonical id set is itself still open (see Open questions); the ROADMAP names the
translation table as the real fix.

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

- **`.info` is never a path source.** `corename` ≠ `library_name` for 27% of the installed cores that load and declare a
  `corename`; the bsnes variants would split one real save directory into three fictional ones. `.info` serves
  capability and firmware-declaration queries only — never a save path.

- **Firmware splits at the boundary rule; installer-step knowledge is bonus.** Which files a core wants _is_ on the
  machine (`.info` `firmwareN_path`), so it is read live and never shipped. What a correct file's bytes are is not on
  the machine, so the identity table belongs in atlas, versioned and cited. Whether firmware needs an installer run
  instead of file placement is useful but omittable.

## Settled since the rewrite

- **Seam signatures**: explicit operation outcomes (`ReadResult`, `PathKind`) — see "The machine seam".
- **Health representation**: a structured value on the handle (issue caveats with stable codes), mirrored into placement
  caveats.
- **Vector encoding**: schema-versioned files; whole machines including read-failure states; exact-equality contract
  serializations.
- **Consistency model**: handles are live; within one query every governing source is read exactly once and all
  decisions derive from that snapshot.
- **Firmware is emulator-centric.** A requirement is one `(core, declared file)` pair: the core decides the expected
  name and the absolute destination, the packaged identity decides whether what lies there is right. `need` (`required`
  / `optional`) and `checked` (`verified` / `mismatch` / `unchecked` / `unknown`) are independent axes, and neither
  `unchecked` vs `unknown` nor "core needs nothing" vs "core unknown" may collapse — the second pair is told apart by
  `installed` plus a caveat, never by list length. A file nobody declares is not a requirement at all; it is an
  `UnclaimedFile`, identified by **content**, and save data the rule cards claim never appears there. Hash checking is
  opt-in (`verify=`): policy and caching belong to the caller, not the library.
- **A derived system assignment is stated, not hidden.** Only the per-file override table knows which machine a firmware
  dump belongs to, and it is `[D]`, deliberately incomplete, and not to be completed by hand — boot-ROM variants arrive
  with every core release. Everything else files a file by what its whole _core_ is called, which holds exactly while
  the core covers one system, and the `.info` says when it does not: `database` names every system the core serves. When
  a declaration falls back on a multi-system core, the answer carries `system-assignment-derived` naming the exact
  files; a core with no `systemname` at all is a different state with its own code. `database` is read as that signal
  only — it is a different vocabulary (`Sinclair - ZX 81` where `systemname` says `ZX81`), so assigning from it would
  mean maintaining a second table of the same size.
- **An empty answer says which kind of empty it is.** `system-unknown` means nothing here covers that identifier — a
  consumer that failed to translate its own vocabulary lands there. Where the subject _is_ covered, an empty requirement
  list has three further reasons and each carries its own code, because each is a different instruction:
  `no-firmware-declaration` — every declaration source was read and none names a file, so the absence is established;
  `no-firmware-requirement` — firmware is declared here and none of it became a requirement (refused, or outside the
  enumeration its own core performs), so the per-emulator entries say which file went where; and
  `firmware-declaration-unknown` — what is declared could not be established at all, because the enumeration did not
  happen or nothing in it could be read. One code for several of these would read as the mildest of them: "nothing
  needed" where the right instruction is "you asked in the wrong vocabulary", or worse, where the truth is that atlas
  never got to look. That last one is the rule behind the split: a claim about the machine requires that the enumeration
  was actually read, so a failed catalogue read, an unresolvable `.info` directory, or a core the catalogue names and
  the installation does not ship may never be turned into "nothing here declares that". The answer-level line exists for
  what the per-emulator entries cannot say: where every listed emulator was read and simply declares nothing, each entry
  already answers that with `declaration="read"` and an empty list — exactly as the per-core route does — and the answer
  adds nothing.
- **`requirements_met` is the number a client renders, so atlas states it.** It is `true` only when every required file
  is there _and_ nothing established contradicts it — a present file with the wrong bytes makes it `false`, and one
  whose identity could not be established makes it `null`. `satisfied` per requirement and `requirements_met` per core
  are both in the contract for one reason: a consumer deriving them from `need` and `present` gets the mismatch case
  wrong, which is exactly how a verified-broken BIOS reads as all-clear.
- **Every path outcome stays distinct.** A file, a missing file, a directory in the way (nothing can be placed there),
  and a path that could not be looked at are four answers, not two. `present` is therefore `true`/`false`/`null`, and
  the seam's own rule holds: a present-but-broken state is never reported as absent or healthy. Declared paths are
  bounded to the firmware root — `firmwareN_path` comes out of an editable config and drives every read that follows, so
  one that climbs out is refused and stated rather than followed. The scan for unclaimed files is bounded to the same
  subtree and, by the seam's glob rule, does not see leading-dot names: dot-files in a firmware tree are tooling
  residue, and leaving them out is a decision, recorded here so it is not mistaken for a gap.

## Open questions

- The catalogue API when multiple frontends coexist on one EmuDeck install.
- Exact canonical system-id set (lean toward ES-DE names). It bites today: `firmware_for_system` speaks the frontend's
  system name where a catalogue exists and an atlas slug where none does (`dreamcast` vs `dc`), and states which via a
  caveat rather than translating.
- Whether `checked` needs a fifth value for artifacts whose whole-file hash is not a meaningful identity — MAME-style
  romset zips hash differently per romset version and merge mode. 21 of the 388 packaged identities are archives or data
  packs; deciding this needs per-entry provenance in the table, not an extension heuristic.
- Distinct probe-failure reporting for `query_core` (crashed vs. missing vs. sandbox-only) — revisit with the
  feature-detection extension (ROADMAP: card variants), which reworks the probe anyway.
