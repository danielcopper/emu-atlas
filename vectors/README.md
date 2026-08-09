# The conformance vectors — what they are and how to read them

These files are the normative artifact: a fixture machine goes in, the answers atlas must give come out. They are what a
**port** is checked against (a reimplementation in another language passes atlas's vectors or it is not a port), and
what a change to the resolver is measured by. The reference implementation runs them in `tests/test_machine_vectors.py`;
the shape rules below are enforced independently by `scripts/validate_vectors.py`, which is stdlib-only so a port author
can run it without importing atlas.

The whole idea in one line: **the expected block is the canonical serialization of a real answer, asserted with exact
equality.** Nothing here is a description of an answer — it is the answer.

## Layout

```
vectors/<family>/*.json
```

One family today, `machines`. Every file carries a header and its vectors:

| key           | meaning                                                   |
| ------------- | --------------------------------------------------------- |
| `family`      | must match the directory (`machines`)                     |
| `schema`      | the generation of the fixture grammar — **3** (see below) |
| `spec`        | which document this file's guarantees come from           |
| `description` | what this file covers                                     |
| `vectors`     | the vectors themselves                                    |

A vector is `{name, rationale?, input, expected}`. `name` is unique within its file; `rationale` is prose for the reader
and part of no contract. Two uniqueness rules matter more than they look:

- **names are unique per file** — a duplicate would make a failure impossible to place;
- **canonical inputs are unique across the whole family** — the same machine asked the same question twice, with two
  expectations, means one of them is never the answer and nothing says which.

### Why the schema number

Schema 3 asks more of a port than 2 did: `glob` reports how much of the walk it could read, and a fixture can state a
directory that exists and cannot be listed. A port built to 2 models neither, so the corpus is not the same promise —
the number says so once, instead of leaving it to be discovered one failing vector at a time.

## `input` — a machine as plain data

`home` and `files` are required; everything else is optional.

```json
{
  "home": "/home/deck",
  "files": {
    "/home/deck/.config/retroarch/retroarch.cfg": "savefile_directory = \"/saves\"\n",
    "/saves/chmod-000.srm": { "status": "unreadable", "size": 8192 },
    "/bios/gba_bios.bin": { "md5": "a860e8c0…", "size": 16384 }
  },
  "dirs": ["/saves/empty"],
  "symlinks": { "/saves/link": "/elsewhere" },
  "cores": { "/cores/mgba_libretro.so": { "library_name": "mGBA" } },
  "unlistable": ["/saves/mode-000"],
  "inaccessible": ["/mnt/unplugged-card"]
}
```

**`files`** — one entry per file, keyed by absolute path. Three spellings, and each state has exactly one:

- **a string** — readable text content.
- **`{"status": "unreadable" | "invalid-text"}`**, optionally with `"size"` — a file that exists and yields that read
  outcome. The `size` belongs with it because the two come from different reads on a real machine: a chmod-000 file
  `stat`s fine and only its bytes fail.
- **`{"md5": …, "sha1": …, "size": …}`** — a binary blob: it exists, reads as `invalid-text`, and answers those values
  for `file_digest` / `file_size`. Any subset of the three.

**`dirs`** — directories that exist with nothing in them. Parents of every path named anywhere are directories already;
this list is how an _empty_ one is stated. A configured save root must be a directory: list it here, or put a file in
it.

**`symlinks`** — link path → target (absolute, or relative to the link's directory). A target that is in no other list
is a **dead** link, which is a state the corpus deliberately covers.

**`cores`** — `.so` path → what loading it answers: `{"library_name": …}`, optionally `library_version` and `options`
(`{key: {"default": str|null, "values": [str, …]}}`). `null` means **present but unloadable**. The distinction between a
core with no `options` key and one whose `options` is `{}` is load-bearing: the first says nothing was captured, the
second is evidence that the core registers none.

**`unlistable` vs `inaccessible`** — two ways to be unreadable, told apart by one question: does the `stat` succeed?

- `unlistable` — it does. The path _is_ a directory and its contents cannot be read. That is what a mode-000 directory
  answers about itself, and the only such state a resolver reaches after passing an "is it a directory?" check.
- `inaccessible` — it does not, so nothing about the path can be told. This is what the paths _below_ a mode-000
  directory answer, and what a mount point answers when the card behind it stops responding. **Declaring a directory
  declares its whole subtree.**

A path in both lists is a contradiction, not a mode-000 shorthand, and is refused.

### The question a vector asks

Each expectation is paired with the input key that asks it. Several may appear in one vector; each pair must be complete
— a query with no expectation asks nothing, an expectation with no query is checked by nobody.

| input key               | expected key               | the question                                          |
| ----------------------- | -------------------------- | ----------------------------------------------------- |
| `savefile_query`        | `savefile_location`        | where does this savefile live                         |
| `entry_savefile_query`  | `entry_savefile_location`  | the same placement, asked _through_ a catalogue entry |
| `savestate_query`       | `savestate_location`       | where does this savestate live                        |
| `entry_savestate_query` | `entry_savestate_location` | the same placement, asked _through_ a catalogue entry |
| `catalogue_query`       | `catalogue`                | which emulators can launch this system                |
| `systems_query`         | `systems`                  | which systems does the frontend declare               |
| `rom_location_query`    | `rom_location`             | where do this system's ROMs live                      |
| `firmware_query`        | `firmware`                 | `kind` is `core`, `system` or `inventory`             |
| `identify_query`        | `identification`           | what is this content, by `md5` / `sha1` / `size`      |
| `aggregate_query`       | `aggregate`                | one question put to **every** detected installation   |

Every single-question family may name `installation` (a handle kind) to choose which detected installation answers;
without it the first one does. `aggregate_query` may not — asking the aggregate to choose is the one thing it does not
do — and names its `question` instead.

## `expected` — the answers, canonically serialized

`installations` is always present: what `detect()` must find, in detection order, each `{kind, kinds, root, health}`.
Every other key is the canonical serialization of that question's answer, from `atlas/contract.py`.

Two rules decide what belongs in a vector:

- **structured fields are contractual** — every one is asserted, including caveat `code` and `data`, granularity
  identity, `fallback_dir` / `physical_dir`, file sets and their completeness, and health findings; `file_set.complete`
  is **reserved**: it is `false` in every vector because no shipped rule card can yet establish which files a core
  writes at all for the active mode, and a port that answers `true` anywhere is answering something atlas does not
  claim;
- **prose is not** — `sources`, caveat `message` and the `*provenance` fields are for humans and change freely, so they
  are not serialized and not asserted.

Health findings ride twice by design: in `installations[].health`, and in the `caveats` of every answer computed on that
installation. They keep their own codes in both places — nothing wraps them.

Caveats are `{code, data}` with `data` a flat object of strings. The codes are a closed vocabulary; so are root kinds,
granularities, file-set states, holes (`needs`), emulator kinds, declaration states, system sources, path kinds and
firmware needs. `scripts/validate_vectors.py` carries all of them, and the reference suite checks that list against
atlas's own exports in both directions.

## Running them

```bash
python scripts/validate_vectors.py     # shape: does every vector state a legal machine and a legal answer
python -m pytest tests/test_machine_vectors.py    # conformance: does the resolver answer exactly this
```

The validator ships beside the corpus and runs on a bare Python — no atlas import, no dependencies, and it finds the
vectors relative to its own location, so it works the same from an unpacked sdist as from the repo. The conformance run
is the reference implementation's, and is where a port substitutes its own.

A port implements the machine seam (read a file, list a directory, stat a path, read a link, load a core, size and
digest a file), feeds each `input` to it, and compares its serialized answers to `expected`. Nothing else is needed —
that is the point of the family.

## When an expected block changes

Changing what an existing input maps to is a **breaking change** to this corpus: the PR title needs the `!` marker or
its body a `BREAKING CHANGE:` footer, because release-please turns that into the major bump. Adding vectors only grows
the contract. Editing a fixture's `input` counts as breaking too — the machine the old guarantee described is gone from
the corpus. The gate that decides this (`scripts/check_vector_breaking_change.py`) lives in the repository rather than
in this package: it diffs the corpus against a git ref, which a release artifact does not carry.

Vectors for old generations of an emulator's behaviour are never deleted: when a new generation lands, it gets its own
fixture machine beside the existing one.
