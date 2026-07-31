# Memory Index — emu-atlas (project-shared)

- [live-verification-protocol](live-verification-protocol.md) — how a live run on the user's Deck works: one system at a
  time, in-game save, baseline diff (`~/.local/share/emu-atlas/live-baseline/`), matrix flip; launcher pitfall (decky
  forces `-e`, libretro runs go via ES-DE alternative emulator + `ps aux` check)
- [live-round-state](live-round-state.md) — the in-flight verification round ("weiter mit dem Evaluieren der Emus"
  resumes HERE): pending PSP/ppsspp_libretro suspect run, then suspects/wave-1 trio one at a time; update after every
  run
