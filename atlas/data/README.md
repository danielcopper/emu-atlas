# Packaged data

## `core_oddities.json` — the rule cards

One card per libretro core whose save behaviour deviates from RetroArch's standard rule. A card says _which live config
governs the core_ and what each of its values means; the value itself is always read off the machine. The audit verdict
behind every card lives in `core_audit.json`, and a test fails if a card has no entry there.

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
`SavePlacement.needs` speaks (`atlas/placement.py`, which the loader imports — one definition, not a second spelling
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

### Provenance

`provenance.source` is the evidence prose the answer carries in its `sources`, `verified_against` and `date` pin what it
was established on, and `provenance.status` states it **per mode**: every mode has an entry, and the flycast card opens
each with the project's evidence marker (`[V-live]`, `[D]`, `[O]`) so an observed mode and a derived one cannot be read
as the same claim. Tests enforce the per-mode coverage and that marker split.

## `core_audit.json`

The machine-readable core of `docs/research/core-audit.md`: per core, the verdict, the evidence `note`, whether per-game
saves are a proven capability, and — per arrangement — the versions the knowledge was verified against. The file's own
`spec` field carries the rules; the research doc carries the method.

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
