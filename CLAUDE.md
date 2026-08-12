# CLAUDE.md — how to work in emu-atlas

atlas is a **resolver, not a lookup**: it answers questions about the running machine by reading the running machine the
way the emulator does. The one rule that decides every "table or live?" question:

> What is on the running machine is read — always. What is written nowhere on the machine is world knowledge, and world
> knowledge is marked, versioned, and source-cited.

Spec: `DESIGN.md`. Plan: `ROADMAP.md`. Where to pick up core work: `docs/research/coverage-matrix.md` (generated).
Evidence: `docs/research/`. Itemized gaps: `docs/tasks/save-detection.md`. The live verification round's protocol and
baseline are maintainer-side working state, kept outside the tree (`~/Memory/emu-atlas/`): they describe one particular
machine, so this repository does not carry them and no contributor needs them.

## Ground rules

- **Never guess.** A hole (`needs`) is something the caller fills; _unknown_ is something atlas refuses to state.
  Degradations are structured `Caveat`s with stable codes — never free-text-only, never silent.
- **Evidence levels are part of the work**: [V] verified (source read, binary extracted, or observed on disk), [D]
  derived, [O] open. Two hard-won method rules: a code path being real is not the same as it being _reachable_ — trace
  who initializes the state a branch checks before marking [V]; and filtered/truncated `strings`/grep scans over
  binaries are guessing — run the unfiltered pass, then read upstream source with `file:line` citations.
- **Upstream citations**: RetroArch facts cite `file:line` at the pinned revision (see
  `docs/research/retrodeck-save-placement.md` header). RetroDECK's per-emulator knowledge lives in the **Flatpak**
  (`/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components/`), not in its Git repo.
- **Zero runtime dependencies is a contract** (`pyproject.toml`) — it makes vendoring a directory copy. Stdlib
  `xml.etree` is a deliberate, documented choice (local trusted config; see `atlas/esde.py`).
- User-facing behavior changes need vectors. One fixture machine per supported generation of an emulator's behavior —
  vectors for old generations are never deleted.

## Core audit method (rule cards)

Pinned in `docs/research/core-audit.md`: (1) **unfiltered** options/strings scan of the shipped `.so`, (2) upstream
source for anything the scan implies, (3) live observation where data exists, (4) verdict in
`atlas/data/core_audit.json` + card in `atlas/data/core_oddities.json` if deviant. Card keys are the `.so` short name
(`pcsx2`, not a nickname); a test fails if a card lacks an audit entry. After changing audit data, regenerate the
matrix: `python scripts/generate_coverage_matrix.py && deno fmt docs/research/coverage-matrix.md`.

## Commands

```bash
mise run setup      # editable install + dev deps into the local venv
mise run test       # pytest (unit + vector runner)
mise run validate   # vector shape validation
deno fmt --check    # markdown formatting (CI-enforced)
basedpyright atlas tests scripts   # type-check (CI-enforced; pip install basedpyright into the venv)
```

## Pitfalls

- **Worktrees**: fresh worktrees need `mise trust && mise run setup` (per-directory venvs). The harness LSP diagnostics
  resolve against the _main checkout_ while you work in a worktree — trust `mise run test`, not stale diagnostics. The
  shell cwd resets between Bash calls: start commands with an explicit `cd` into the worktree.
- The `machines` vector family (schema 3) models whole machines: `files` (string content, or
  `{"status": "unreadable"|"invalid-text"}` for read failures, optionally with a `size` — the chmod-000 file stats fine
  and only its bytes fail), `dirs` (explicit empty directories), `symlinks` (dead links included), `cores` (`null` =
  present but unloadable), and two ways to be unreadable: `inaccessible` (the `stat` fails — declaring a directory
  declares its whole subtree) and `unlistable` (it _is_ a directory and its contents cannot be read, which is the only
  state a resolver reaches after passing an "is it a directory?" check). Configured save roots must be _directories_ in
  fixtures (`path_is_directory` validation) — list them in `dirs` or place a file inside. Expected blocks are the
  canonical contract serializations (`atlas/contract.py`), asserted with exact equality — prose (sources, messages) is
  non-contractual.
- Live verification against the real RetroDECK installation on this machine is the final check for resolver changes;
  fixtures prove logic, the machine proves reality.
