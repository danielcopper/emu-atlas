# Packaged data

## `core_oddities.json` — the rule cards

One card per libretro core whose save behaviour deviates from RetroArch's standard rule. A card says _which live config
governs the core_ and what each of its values means; the value itself is always read off the machine. The audit verdict
behind every card lives in `core_audit.json`, and a test fails if a card has no entry there.

**A card states only what no read of the machine recovers, and what it states is machine-checked wherever a machine can
check it.** Three consequences run through the format:

- The card key is the core's canonical short name — the `.so` basename without `_libretro.so` — and the `.so` name is
  **derived** from it rather than restated. A second spelling could only ever be a way for the two to disagree about
  which core a card describes, so the loader refuses a card that still carries `identifiers.so`. What the key does not
  give is the display name the binary reports (`Flycast`, `LRPS2`); `identifiers.library_name` carries that, and lookup
  works from either side.
- `governing_option.default` is recorded **only for a core that registers none**, and a test fails the redundant copy
  rather than keeping it correct by hand. Where a core registers its options during `retro_set_environment`,
  `query_core` reads the default off the shipped binary; a card's copy would be a second, ageing one. LRPS2 registers
  later than that, which is why its card keeps a default and says so in its provenance. The consequence is stated, not
  papered over: on a machine where such a core's registration cannot be read **and** no options file names the value,
  nothing establishes which mode is in force, so the card steps aside and the answer carries
  `core-option-value-unestablished`.
- Every file name and subdir fragment a card records is pinned to the binary it was read from — see _Anchors_ below.

A mode — one value of the governing option — states the root it anchors at, an optional `subdir`, its `granularity`, and
the files the save consists of. Three of those fields are closed vocabularies — all three the placement's own, imported
by the loader rather than respelled here, so a card cannot select a value the contract cannot carry — and one is a type
rule:

| field         | accepted values                                                   |
| ------------- | ----------------------------------------------------------------- |
| `root`        | `savefile_directory`, `system_directory`, `content_directory`     |
| `also_under`  | the same three, and not the mode's own `root`                     |
| `granularity` | `shared-card`, `per-game-file`, `per-game-files`                  |
| `complete`    | a JSON boolean, `true` or `false` — never a string, never coerced |

`granularity` reaches the caller as the contractual `Granularity.value`, so a misspelling would be stated as this
machine's actual grouping; `complete` is a claim about the save, and `bool("false")` is `True` in Python, so a quoted
boolean fails the load instead of silently asserting completeness.

```json
"All VMUs": {
  "root": "savefile_directory",
  "subdir": null,
  "files": ["<save_id>.A1.bin", "<save_id>.B1.bin"],
  "files_without_save_id": ["<rom_stem>.A1.bin", "<rom_stem>.B1.bin"],
  "granularity": "per-game-files"
}
```

### File names are templates in the placement's own hole vocabulary

A declared name may carry exactly two tokens, and they are not local to this file: they are the holes
`SavefilePlacement.needs` speaks (`atlas/placement.py`, which the loader imports — one definition, not a second spelling
here).

- `<rom_stem>` — the resolver fills it from the content path. Not a hole in an answer: either it is substituted or the
  file set is honestly unknown.
- `<save_id>` — the content's platform-native id (Flycast names a per-game VMU after the disc's product number). atlas
  never fills it, because reading an id out of a ROM is identification, not location. It stays in the stated name and
  `save_id` joins `needs`, so a caller sees a template rather than a resolved-looking name.

The loader rejects any other token in a declared name, and the check is **subtractive**: it removes the known templates
and refuses whatever still contains `<` or `>`. Scanning for well-formed `<…>` would pass `<rom_stem.A1.bin` (bracket
never closed) and `<<rom_stem>>.A1.bin` (nested) — both of which atlas would then state verbatim as a filename. That is
the point of keeping one vocabulary: a card is data, and without the check a typo travels silently into a name atlas
states as fact — the failure mode the "never guess" rule exists to prevent. A card that needs a new hole adds it to the
placement vocabulary first. An empty list, an empty name and a literal angle bracket are refused for the same reason.

### Two fields for what one file list cannot say

- `files_without_save_id` — the same set as the emulator names it when the content carries **no** id. Flycast's
  `getVmuPath` takes the id branch only for console content with a readable disc header and falls back to the ROM's name
  otherwise, so the set is conditional on a fact atlas does not read. The resolver states the id-keyed set and hands
  this one to the caller in `filenames-content-conditional`, filled as far as it can fill it. Only meaningful next to a
  `<save_id>` set, and it may not name an id itself; the loader enforces both.
- `files_established_for` + `files_citation` — the class of content the list was established for, and the source that
  says so. Not every difference between content classes is a spelling: Flycast connects four VMUs on a Dreamcast and two
  on a Naomi board, so for arcade content two of the four declared names can never exist. Both travel into the same
  caveat as data, so a client can tell "this list is scoped" from "this list is universal" without reading prose. The
  scope needs a declared `files` to scope, and the citation needs a scope to cite; the loader enforces both.
- `also_under` — a second root this mode's save data lives under (Flycast's `VMU A1` moves one controller's VMU and
  leaves the rest on the shared card). A card states one root per mode, so such a mode declares **no** `files` at all —
  the loader refuses both together — and the answer carries `file-set-spans-roots` instead of offering the visible part
  as the whole save. What the schema would need to state it properly is task 16 in `docs/tasks/save-detection.md`.

`complete` is the explicit claim that the mode's candidate universe is closed. A template can in principle carry it —
the hole is in the name, not in the membership — but only source-verified provenance earns it.

### Anchors — every recorded name, pinned to the string it was read from

A card names files and subdirectories that reach a caller as fact, and `saves.anchors` records where each of those names
came from. One entry per recorded name — the governing option key, every segment of a `subdir`, and every name in
`files`, `observe` and `files_without_save_id` — carrying exactly one of three kinds:

| kind          | means                                                                                |
| ------------- | ------------------------------------------------------------------------------------ |
| `literal`     | the whole NUL-delimited byte string the auditor read in the shipped `.so`            |
| `unprotected` | no literal spells this name — the core composes it at run time, and the text says so |
| `arrangement` | the name is not the core's word at all: the arrangement builds that path             |

```json
"anchors": {
  "vmu_save_A1.bin": { "literal": "vmu_save_" },
  "dc_nvmem.bin": { "unprotected": "composed at run time from the platform prefix and the flash file's own name — …" }
}
```

A test re-reads every `literal` in the deployed core and fails when it is gone, so a build that renames `vmu_save_` or
`Mcd%03u.ps2` is caught instead of leaving the card describing names the core no longer writes. The check is
whole-string containment between NUL bytes, not a substring scan: flycast really does carry the literal `/dc`, and it is
the texture-dump path, nothing to do with saves.

**A recorded name with none of the three kinds fails the tests** — there is no silent opt-out, because a name that looks
checked and is not is worse than one marked as what it is. What the tripwire cannot catch is the _grammar_ around a
literal (that `%s.ps2` still names the save and not something else) and the names no literal carries; those stay
`unprotected`, and each one's reason states what does stand behind it — live observation and the next re-audit, or
source the byte check simply cannot reach (Flycast's `dc_nvmem.bin` is assembled in `.text` from instruction
immediates).

Mode keys are deliberately not anchored. They are the governing option's own values and the deployed core registers
them, so the tests measure that set against the binary directly — a measurement beats an anchor.

The loader validates the block and then drops it: anchors are audit machinery, and nothing that reaches a caller holds
them.

### Provenance

`provenance.source` is the evidence prose the answer carries in its `sources` — the one field of the block a resolver
reads. `provenance.status` states the evidence **per mode**: every mode has an entry, and the flycast card opens each
with the project's evidence marker (`[V-live]`, `[D]`, `[O]`) so an observed mode and a derived one cannot be read as
the same claim. That is a maintenance invariant enforced by tests over this file, not an input to any answer — no client
receives anything shaped by it.

What a card was verified _against_ is not written here. It lives in `core_audit.json` as `verified.<arrangement>` — the
arrangement version, the core's `library_version`, the date — where it is structured, compared against the running
machine, and carries `unverified-version` when the two differ. A prose twin beside the card could only be a second copy
going stale in silence.

## `core_audit.json`

The machine-readable core of `docs/research/core-audit.md`: per core, the verdict, the evidence `note`, whether per-game
saves are a proven capability, and — per arrangement — the versions the knowledge was verified against. The file's own
`spec` field carries the rules; the research doc carries the method.

## `arrangement_evidence.json` — which arrangements have been seen alive

One record per installation kind, saying whether a live installation of that arrangement has ever confirmed atlas's
answers end to end. Read by `atlas.evidence`; an arrangement whose record has no `verified` block attaches
`arrangement-unverified` to every answer it gives.

```json
"emudeck": {
  "label": "an EmuDeck arrangement",
  "verified": null,
  "note": "[D] Read from EmuDeck's shipped configuration upstream …; no EmuDeck machine has been observed."
}
```

- `label` — how the arrangement is named in the caveat's prose.
- `note` — the evidence level (`[V]`/`[D]`/`[O]`, as in `docs/research/`) and what it rests on.
- `verified` — `null`, or `{version, date, reference}`: the arrangement version observed, when, and which installation.
  `version` and `reference` are required in a present record, because a record that pins nothing would read as verified
  everywhere and forever while establishing nothing.

`version` is also live machinery, not only provenance: the resolver compares it against the version the machine states
about itself (RetroDECK writes one into `retrodeck.json`) and attaches `arrangement-version-drifted` when the two
differ. That one comparison guards everything pinned at once — parser grammar, path layout, shipped-build behaviour —
and `docs/re-verification.md` is what retires it. The comparison needs both sides: a machine that states no version is
not compared, and stays silent rather than claiming a comparison nobody made.

Two rules make this file the whole mechanism. **A missing record counts as unverified** — omission is never evidence —
though a handle kind with no record at all fails the test suite, because a fact nobody wrote down should not ship.
**Verifying an arrangement retires its caveat here, not in a resolver**: add or re-pin the `verified` block, and the
answers stop carrying it. No code names an arrangement's evidence state. Re-pinning does move the vector corpus, though
— every fixture stating the old version is a drifted machine afterwards, and the runner enforces it
(`docs/re-verification.md`, step 5).

## `system_ids.json` — the whole system vocabulary

Every id a question about a system can take, read by `atlas.systems`. One list, nothing else in the file.

```json
"systems": ["3do", "gb", "n64", …]
```

The ids are **ES-DE's system names**, because that is what a frontend catalogue declares and what the questions answer
about. Cited to the `es_systems.xml` of a pinned build, so the set is checkable rather than an opinion: a name that file
does not declare is not an id, however plausible. Commented-out blocks are not declarations and are not included — the
guard test parses the file with ElementTree, which drops them, and asserts set-identity with this list.

**No foreign vocabulary belongs here.** Another product's platform identifiers — a library manager's slugs, a metadata
service's ids — are world knowledge atlas cannot check against any machine, and a client holding them owns the mapping
into these ids (`DESIGN.md`, Vocabulary). What ships for them is the target set to validate against, not a table.

**The loader is fail-closed**: an unreadable schema, an empty list, an entry that is not a non-empty string, and a
repeated id each fail the load. A vocabulary whose own account of itself does not add up would answer "not an id" for
names that are, and a caller validating against it would delete the mapping that was right.

## `firmware_hashes.json`

What a correct firmware file's bytes are: the `md5` / `sha1` / `size` triple that identifies it. This is world knowledge
by nature — no config on the machine states it — so it ships inside the `atlas` package and is read by
`atlas.firmware.load_hashes`. At the current release it holds **388 firmware identities**.

Which files a core _wants_ is deliberately not in here. Those declarations sit in the `.info` files RetroArch ships next
to its cores, so atlas reads them off the running machine (`atlas.firmware.read_core_declarations`) instead of shipping
a snapshot that drifts against the cores an installation actually has. The filename is where that split shows.

Shape:

```json
{
  "_meta": { "generated_from": "...", "version": "5.0.0", "generated_at": "2026-02-27" },
  "files": {
    "scph5501.bin": { "md5": "...", "sha1": "...", "size": 524288 }
  }
}
```

- `_meta.generated_from` — the upstream source the file was built from.
- `_meta.version` — the table's schema/data version (bumped deliberately, independent of the package version).
- `_meta.generated_at` — the UTC date of the last regeneration (`YYYY-MM-DD`).
- Keys are whatever `System.dat` uses, verbatim: usually a bare file name (`scph5501.bin`), sometimes a relative path
  (`dc/dc_boot.bin`, `pcsx2/bios/…` — 91 of the 388 entries). `FirmwareHashes.for_path` therefore matches a declared
  path first and its base name second, which stays unambiguous only while no base name is claimed by two entries — a
  test pins that. Every entry carries all three of `md5`, `sha1`, and `size`.
- **One content, several names.** 18 of the 369 distinct contents are keyed under more than one name — `dmg_boot.bin` ≡
  `gb_bios.bin`, `dc/boot.bin` ≡ `dc/dc_boot.bin`, `bios.sms` ≡ `bios_E.sms` ≡ `bios_U.sms`.
  `FirmwareHashes.for_content` indexes that direction, which is what makes "these bytes belong at these three
  destinations" answerable. Note that a shared identity never satisfies a requirement under another name: SameBoy opens
  `dmg_boot.bin` and nothing else.
- The table covers only what `System.dat` covers. A declared file with no entry here is a **normal** state, not a gap in
  the data — `atlas.firmware` reports it as present with `checked` = `unknown`, never as verified.
- **Not every identity is a whole-file dump.** 21 entries are archives or data packs (MAME-style romset zips such as
  `neogeo.zip` and `dc/naomi.zip`, plus `scummvm.zip`, `Dinothawr.zip`, `ecwolf.pk3`, `prboom.wad`). A romset zip hashes
  differently per romset version and merge mode, and a data pack tracks its core's version, so a `mismatch` on one of
  these says less than it appears to. The table does not yet distinguish those entries from real dumps; doing so needs
  per-entry provenance, not a guess from the file extension (ROADMAP, block 6).

## Upstream source

The table is derived, offline, from [libretro-database](https://github.com/libretro/libretro-database) —
`dat/System.dat`, the hashes and sizes for known firmware files.

## Regenerating

Generation is a dev-time, offline step. The generator takes a local git checkout of the upstream repo as an argument and
touches the network for nothing:

```sh
git clone https://github.com/libretro/libretro-database ~/src/libretro-database

python scripts/generate_firmware_hashes.py --database ~/src/libretro-database
```

With no `-o`, the output defaults to `atlas/data/firmware_hashes.json` (this file's sibling), resolved relative to the
repo root so the command works from any working directory. Pass `-o <path>` to write elsewhere.

## Update discipline

- **A regeneration lands as a reviewable data diff in its own PR.** The point of committing generated data is that the
  diff is auditable: a reviewer can see exactly which entries were added, removed, or changed. Regenerate against a
  fresh upstream checkout, commit the resulting JSON, and let the diff speak.
- **A changed hash is a behavior change for consumers — say so in the PR.** An identity changing, or an entry appearing
  or disappearing, is not cosmetic: a file that verified before may report `mismatch` after, and one that reported
  `present` may start verifying. Call these out explicitly in the PR description so the change is not merged as a silent
  data bump.
- **The vector breaking-change gate does not watch `data/`.** `scripts/check_vector_breaking_change.py` guards only the
  conformance vectors under `vectors/`; it never inspects this table. The table is versioned by releases instead —
  consumers pin a release and get a stable snapshot — so a data change that matters to consumers relies on the PR
  description and the release notes to surface it, not on an automated gate.
