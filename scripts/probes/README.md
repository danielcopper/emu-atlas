# Probe scripts

A probe script asks atlas several questions in one run and compares the answers, where `mise run ask` asks one:

```bash
mise run probe <script> [arguments]                   # here
mise run probe --target <name> <script> [arguments]   # on a remote target (docs/remote-target.md)
```

`<script>` is a file name here, letters, digits and `_` then `.py`; the dispatcher on a target takes no other.

What a probe script may do:

- **Read, never write.** On a target it runs in a sandbox with the whole file system read-only and no network, so a
  write fails there; here nothing stops it, which is why a probe must not try.
- **Use only the standard library and `atlas`.** On a target the copy is the installation: `atlas/` and this directory,
  run on the target's `/usr/bin/python3` with no site-packages. Ask atlas in the script's own process — `atlas.cli.run`
  or the library — since there `atlas` is importable in that process only, not in a child it starts.
- **Report shapes, not values.** Its output describes one machine: say which question and which installation kind, and
  where answers part, rather than printing paths, titles or names off the device.
- **End with 0, 1 or 2**: 0 for what it checks holding, 1 for not, 2 for a question it could not put. The dispatcher
  ends with 3, 4 and 5 and the client with 6 and 7, so a probe's own status stays told apart from theirs.

What a probe returns is printed and never written into the tree, a test fixture, a commit, a pull request or an issue.
