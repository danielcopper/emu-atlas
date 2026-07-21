# Packaged data

## `bios_registry.json`

The BIOS registry: which firmware files each platform and libretro core want, with the hashes and sizes that identify
them. It ships inside the `atlas` package and is read by `atlas.bios.load_registry`; the semantics on top of it (entry
lookup, the per-core required-classification override) live in `atlas/bios.py`. At the current release it holds **548
entries across 54 platforms and 122 cores**.

Shape:

```json
{
  "_meta": { "generated_from": "...", "version": "4.0.0", "generated_at": "2026-02-27" },
  "platforms": {
    "psx": {
      "scph5501.bin": {
        "description": "scph5501.bin (PS1 US BIOS)",
        "required": true,
        "firmware_path": "scph5501.bin",
        "cores": { "swanstation_libretro": { "required": false }, "mednafen_psx_libretro": { "required": true } },
        "md5": "...",
        "sha1": "...",
        "size": 524288
      }
    }
  }
}
```

- `_meta.generated_from` — the upstream sources the file was built from.
- `_meta.version` — the registry schema/data version (bumped deliberately, independent of the package version).
- `_meta.generated_at` — the UTC date of the last regeneration (`YYYY-MM-DD`).
- Each entry always carries `description`, `required` (the top-level flag), and `firmware_path`. `cores` maps each
  libretro core that references the file to its per-core `required` flag (the override that wins over the top-level
  flag). The `md5` / `sha1` / `size` triple is present only for files libretro-database's `System.dat` has a hash for —
  a file no `System.dat` entry covers carries no hashes.

## Upstream sources

The registry is derived, offline, from two libretro repositories:

- [libretro-core-info](https://github.com/libretro/libretro-core-info) — the `.info` files, one per core, that declare
  each core's firmware (`firmwareN_path` / `firmwareN_desc` / `firmwareN_opt`) and its `systemname`.
- [libretro-database](https://github.com/libretro/libretro-database) — `dat/System.dat`, the hashes and sizes for known
  firmware files.

The `SYSTEMNAME_TO_SLUG` table in the generator maps libretro `systemname` strings to atlas platform slugs.

## Regenerating

Generation is a dev-time, offline step. The generator takes local git checkouts of the two upstream repos as arguments
and touches the network for nothing:

```sh
git clone https://github.com/libretro/libretro-core-info ~/src/libretro-core-info
git clone https://github.com/libretro/libretro-database ~/src/libretro-database

python scripts/generate_bios_registry.py \
    --core-info ~/src/libretro-core-info \
    --database ~/src/libretro-database
```

With no `-o`, the output defaults to `atlas/data/bios_registry.json` (this file's sibling), resolved relative to the
repo root so the command works from any working directory. Pass `-o <path>` to write elsewhere.

## Update discipline

- **A regeneration lands as a reviewable data diff in its own PR.** The point of committing generated data is that the
  diff is auditable: a reviewer can see exactly which entries were added, removed, or changed. Regenerate against fresh
  upstream checkouts, commit the resulting JSON, and let the diff speak.
- **Entry-semantics changes are behavior changes for consumers — say so in the PR.** A `required` flag flipping, an
  entry disappearing, or a hash changing is not cosmetic: a consumer classifying firmware or verifying a file will
  decide differently. Call these out explicitly in the PR description so the change is not merged as a silent data bump.
- **The vector breaking-change gate does not watch `data/`.** `scripts/check_vector_breaking_change.py` guards only the
  conformance vectors under `vectors/`; it never inspects this registry. The registry is versioned by releases instead —
  consumers pin a release and get a stable snapshot — so a data change that matters to consumers relies on the PR
  description and the release notes to surface it, not on an automated gate.
