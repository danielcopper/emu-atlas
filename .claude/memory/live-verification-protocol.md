---
name: live-verification-protocol
type: project
---

**Live verification runs on the user's Steam Deck are the final evidence step of every core audit — one system at a
time, user plays, agent diffs, matrix flips. "Weiter mit dem Evaluieren der Emus" means THIS loop, not just more source
audits.**

**Why:** Source reading alone has produced wrong rules twice (unreachable `runloop.c` branch; LRPS2 generation
mismatch). Only a live save proves shipped-binary behaviour, resolves double-persistence questions (NeoCD), and flips
`verified` matrix entries from `null` to a version. The user explicitly rejected batch verification: **ein System nach
dem anderen**, and audit waves stay small (2–3 cores per PR).

**How to apply — one run:**

1. Agree on ONE system/core with the user; pick a game where in-game saving is quick (menu-save games beat games with
   distant save points).
2. **Launcher pitfall:** starting via decky-romm-sync forces the emulator through `-e %EMULATOR_...%` — for a libretro
   run the user must launch via ES-DE/RetroDECK directly with the system's alternative emulator set to the RetroArch
   entry. Verify with `ps aux | grep -iE "retroarch|<emu>"` that the intended binary runs (RetroArch with
   `-L <core>.so`, not the standalone) BEFORE the user invests play time.
3. User plays and saves **in-game** (never a savestate), then reports.
4. Diff against the baseline (`find <tree> -type f -printf '%T@ %s %p\n' | sort -k3`, then `comm -13` on the path
   columns). Baseline lives durably in `~/.local/share/emu-atlas/live-baseline/` (`saves.txt`, `bios.txt`,
   `ra-config-saves.txt` covering `saves/`, `bios/`, and the RetroArch config-save tree; taken 2026-07-28 BEFORE any run
   — do not retake over it mid-round; refresh it only after a round's findings are recorded).
5. Record findings: verdict/evidence row in `docs/research/core-audit.md`, flip `atlas/data/core_audit.json`
   `verified.retrodeck` to `{version, core_library_version, date}` (arrangement version from `retrodeck.json`, core
   version via probe), update `note`, regenerate the matrix, vectors if behaviour was newly established. Update
   [[live-round-state]] in the same change.

The real RetroDECK root is `/run/media/deck/Emulation/retrodeck`; RetroArch config tree
`~/.var/app/net.retrodeck.retrodeck/config/retroarch/`.
