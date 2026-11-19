# A remote target

atlas answers for the machine it runs on. A **remote target** is another machine — a handheld beside a build host, say —
prepared once from the build host so that atlas's questions can be asked there: the build host copies the tree in, and a
question or a probe script runs on the target, in a read-only sandbox, with its answer coming back on stdout. The target
needs no checkout and no toolchain; everything it runs is in `/usr/bin`.

```bash
mise run setup:remote-target <name> <destination>           # log in, install, and record the target here
mise run setup:remote-target <name> <destination> --print   # print a script to run on the target instead
mise run ask --target <name> -- <question>                  # then: ask atlas a question there
mise run probe --target <name> <script> [arguments]         # or run a probe script of scripts/probes/ there
```

`<destination>` is anything your own `ssh` reaches: `player@192.0.2.20`, or an alias from your `~/.ssh/config`, which
the setup reads with `ssh -G` and never writes. `<name>` is yours to choose — letters, digits, `.`, `_` and `-`.

## What the setup does

1. **It checks the login before it writes anything**, here or on the target. Where the build host already holds its key
   for targets, it logs in with that key alone first, asking for no password and reading none of your SSH config. It
   logs in through your own `ssh` instead — with whatever access that already has: a password, or a key from your agent
   or your SSH config — when there is no key yet, when the target refuses the key, when the target takes the key but its
   dispatcher runs no install (gone, or one that does not take the setup request), and when a jump host on the way stops
   the key's login, since your own config may pass it.
2. **It creates the build host's key for targets** if there is none: `~/.config/emu-atlas-dev/id_ed25519` (under
   `$XDG_CONFIG_HOME` where that is set). One key serves every target; its comment is `emu-atlas-dev@<build host>`.
3. **It records the target's host key** in this tool's own known-hosts file, `~/.config/emu-atlas-dev/known_hosts`. At
   first contact `ssh` shows the fingerprint and asks, as it always does — even for a machine your own
   `~/.ssh/known_hosts` already knows, because that file is not the one the target's entry names. The record is filed
   under the target's name (`emu-atlas-dev.<name>`) rather than its address, so a target at a new address keeps it.
4. **It checks that the target has what a probe needs**, still before writing anything there: `/usr/bin/python3` at or
   above the package's Python floor (`requires-python` in `pyproject.toml`), and `/usr/bin/bwrap` able to start the
   sandbox every probe runs in.
5. **It installs on the target**, under `~/.local/lib/emu-atlas-dev/`: `dispatch.py`, the dispatcher. The copies of the
   tree arrive later beside it, in `trees/`, with `current` naming the latest; no copy reaches the dispatcher. It
   rebuilds `~/.ssh/authorized_keys` with one line for the key, a comment above it naming the build host and the time,
   and every other line byte for byte; a rebuilt file that came out short, or without the new line last, is not put in
   place. A symlinked `authorized_keys` is written through: the file it names is replaced, and the link stays. The
   install is the script `scripts/dev_target_install.py`, sent with its values and the dispatcher filled in, and run by
   the target's `/usr/bin/python3`. Once both files are in place it closes `~/.ssh` to others (mode 700), as sshd wants
   it.
6. **It writes the target into this tool's own SSH config**, `~/.config/emu-atlas-dev/ssh_config`: the address, port,
   user and jump host or proxy command the destination resolved to, the build host's key and nothing else, and the
   known-hosts file. Your `~/.ssh/config` serves the setup's own login only; the entry is written to be read with this
   file alone, and so with the restricted key alone. A jump host is written out as `user@address:port`, since an alias
   from your config means nothing in this one; a proxy command goes in as `ssh -G` prints it. A comment above the entry
   names the build host and the time.

Run again, it changes what changed: one key, one `authorized_keys` line and one config entry, however often it runs.
That is also how a target is repaired. While the build host's address is the one the line names, the repair needs no
password: the key logs in, and the dispatcher's setup request runs the same install. After the build host's address
changed, the target refuses the key, and the repair needs a login of your own — or, on a target without one, `--print`.

Once set up, the setup names the target's Python version. Below 3.14 the standard library has no zstd codec, so on that
target the answers read out of a zstd AppImage — EmuDeck's ES-DE, whose bundled catalogue, find rules and build stay
sealed (`emulator-catalogue-sealed`, `frontend-build-sealed`) — come back sealed, as they do for any consumer that
brings no codec. Nothing but the tree is copied to a target; RetroDECK's answers are not affected.

## What the key may do on the target

The `authorized_keys` line restricts the key three ways:

```text
from="<address>",command="/usr/bin/python3 -I <home>/.local/lib/emu-atlas-dev/dispatch.py",restrict <key>
```

- **`from=`** — to the build host's address **as the target sees it**: the one `SSH_CLIENT` shows in the setup login,
  which behind a jump host is the jump host's. This is the only bound on the key (see the setup request below): a copy
  of the key used from anywhere else would run code on the target. When the build host's address changes, the target
  refuses the key — run the setup again.
- **`command=`** — to the dispatcher, which runs instead of whatever the key asks for and decides from that request
  alone, split on blanks and never handed to a shell. A home directory whose path holds a `"`, a `\` or a control
  character cannot be written into that option safely, and the setup refuses it rather than escaping it.
- **`restrict`** — no terminal, no forwarding of any kind.

The dispatcher lets four requests through; everything else, a login among it, is refused:

- **`/usr/bin/python3 -I -`** — the setup request: the setup's install script on stdin. It is the command line the setup
  sends to whichever login gets in; through a login of your own it reaches a plain shell and runs unchanged.
- **`copy`** — an uncompressed tar stream of the tree on stdin, unpacked into a new directory under `trees/` with
  `tarfile`'s `data` filter, files and directories only, and made `current` with one rename once it arrived whole; the
  older trees are removed after that. It answers with the new tree's id, `tree-` and letters, digits and `_`, the one
  line it writes on stdout.
- **`ask <tree> <word>`** — one question: `python -m atlas <arguments>` from the tree `<tree>`, in the sandbox.
- **`probe <tree> <name> [<word>]`** — the tree `<tree>`'s `scripts/probes/<name>`, in the sandbox; `<name>` is a bare
  file name, `[A-Za-z0-9_]+\.py`.

`<tree>` is the id a copy answered with: a question runs from the tree its own copy made, held for as long as it runs,
and from no other — never from `current`, which another client's copy may have moved on in between. As its first act
inside the sandbox, the run writes one line on stdout, `emu-atlas-dev: the answer follows`, and its own output comes
after it: a sandbox or an interpreter that fails to start writes none. `<word>` is the argument list as a JSON array of
strings, UTF-8, base64url-encoded: content paths carry blanks, quotes and parentheses, which words split on blanks
cannot.

A target set up before the copy answered with an id, or before the marker line, runs an older dispatcher: run the setup
again, which installs this checkout's dispatcher through the key.

The setup request runs outside the sandbox: through it the key can run any code on the target with the user's rights,
its own `authorized_keys` line among what it can rewrite. So `from=` is the only bound on the key, and the sandbox
guards against a faulty probe script or a core that writes, not against the key's holder.

**The copy.** A copy arrives whole when its last member is the manifest, `tree.json` at the top level, stating the
Python floor the tree needs: `{"requires_python": "3.12"}`. A stream that breaks off before that, a member that is
neither a file nor a directory, or one the `data` filter refuses — a path out of the new directory — leaves the current
tree as it was and removes the partial one; a leading `/` the filter strips, so such a member lands inside the new tree.
Whatever sends a copy has to write the manifest last; the floor it states is `pyproject.toml`'s `requires-python`. An
older tree that cannot be removed is named on stderr, after it has been moved aside where the rename went through; the
copy stays current, and a later copy tries again.

**The sandbox.** Every question and every probe script runs through `/usr/bin/bwrap`
(`--ro-bind / / --dev /dev --proc /proc --tmpfs /tmp --unshare-net --unshare-pid --die-with-parent`): the file system
read-only, except a fresh `/dev` and `/tmp` that are writable and gone with the run; a process view of its own, in which
it sees its own processes and none of the device's; and no network. Without a `/usr/bin/bwrap`, or with one that cannot
start a sandbox — a kernel that forbids unprivileged user namespaces — nothing runs, ever: the dispatcher refuses with
the reason and the repair the setup names for the same failure, rather than run unsandboxed.

**The run's lifetime.** The dispatcher waits for `bwrap` and ends the run when its connection is gone — when nothing
reads its output any more — and the sandboxed program ends with `bwrap`. For as long as a question or a probe runs, the
tree it runs from stays: a copy that lands meanwhile becomes `current`, and a later copy removes the older tree once
nothing runs from it.

**The interpreter.** Inside the sandbox the question runs on the interpreter the dispatcher runs on, `/usr/bin/python3`,
with `-I` (no working directory on `sys.path`, no `PYTHON*` variable) and `-S` (no site-packages), and the tree it names
appended to `sys.path` behind the standard library: `atlas` is imported from the copied tree and nowhere else. The core
probe keeps working inside, since atlas starts it as a child of that same interpreter. A tree whose manifest names a
floor above the interpreter is refused with both versions named.

The dispatcher's own exit statuses are ones atlas does not use (it ends with 0 or 2); a question's or a probe's own
status is passed through unchanged, and a run ended by a signal answers 128 plus the signal's number, as a shell does —
a run the dispatcher ended because its connection was gone among them:

- **3 `REFUSED`** — the request is none of the four above, or its argument word is no JSON array of strings.
- **4 `CANNOT_RUN`** — nothing ran: a tree without a readable manifest, an interpreter below the tree's floor, a probe
  the tree does not hold, no working sandbox, a run that could not be started, a `copy` on a Python whose `tarfile` has
  no `data` filter, an error reading the tree, or an exception of the dispatcher's own, its traceback on stderr.
- **5 `COPY_FAILED`** — the copy could not start, did not arrive whole, or arrived but could not be made current; the
  current tree is as it was.
- **8 `TREE_GONE`** — nothing ran: the tree the request names is not there, removed by a later copy or never copied. Ask
  again; every call copies first.

A probe script that raises — at compile and import time as well — ends with 2, a question it could not put, with its
traceback on stderr: the bootstrap that runs it turns its crash into that, here as on a target.

## A target you cannot log into yet: `--print`

A target with no password login and no key of yours on it cannot be logged into, so `--print` logs into nothing: it
resolves the destination, asks `ip route get` for the address this machine sends from towards it, and prints a script
instead:

```bash
mise run setup:remote-target handheld player@192.0.2.20 --print > setup-handheld.py
```

Copy it to the target by any means and run it there with `/usr/bin/python3 setup-handheld.py`. It checks Python and
`bwrap` as the setup does, does the same install, and shows the target's host key fingerprints. By default it restricts
the key to the address the build host sends from towards the target (`ip route get`), written into the script, and the
script says which before it writes anything; if the target sees the build host at another address — through NAT, say —
give that one instead: `/usr/bin/python3 setup-handheld.py 198.51.100.6`. Then run the setup on the build host again,
without `--print`: it logs in with the key, `ssh` shows the fingerprint for you to compare with the script's, and the
setup installs through the key once more, records the target and finishes. If the key is refused at that point, the
message names the script's default address as the likely cause.

`--print` refuses a destination behind a jump host or a proxy command: the target would see that hop's address, which
the build host cannot know.

## When the setup refuses

Each refusal says what to do next and ends with whether anything was changed. These end it with **nothing changed, here
or on the target**:

| What the setup meets                        | What to do                                                                                                                                                                                      |
| ------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| a name this machine cannot resolve          | give the target's address, or a name the network resolves                                                                                                                                       |
| no SSH server answering                     | check the address, and that the target runs an SSH server                                                                                                                                       |
| no login accepted                           | `--print`, as above — behind a jump host or proxy command, a login of yours on the target                                                                                                       |
| a host key that is not the recorded one     | another machine may answer at that address; if the target was reinstalled, remove its record                                                                                                    |
| the key refused (after `--print`)           | the address the printed script restricted the key to; run it again with the right one                                                                                                           |
| a target lacking `/usr/bin/python3`         | install Python at or above the floor there; the message names the floor, and the version found where there is one                                                                               |
| a target lacking a working `/usr/bin/bwrap` | install bubblewrap there; where there is one that did not start or started no sandbox, the message also names unprivileged user namespaces, and quotes what `bwrap` said where it said anything |

When `ssh` names a jump host on the way as the one that failed — a name this machine cannot resolve, a host key your own
files record differently, a login it refused, no server answering — the message names the hop, and the setup leaves your
files alone; the `ssh-keygen -R` advice is given only for a record in this tool's own known-hosts file. Where `ssh`
names no host, the message names the target or the hop. A host key you decline, or one `ssh` cannot ask about without a
terminal, ends the setup the same way. A login that runs no install at all ends it too, and there the message gives what
`ssh` said; when the target took the build host's key and its forced command failed, the message says so as well.

A failure counts as one before the login only where the install never started and `ssh` ended with its own failure
status (255): the remote shell's login files write to the same stderr as `ssh`, so a `Permission denied` they print is
not taken for a refused key. Once the install has started, a failure is the target's, and this machine is unchanged. The
install builds its files first and moves them into place last. One that refuses to go on or fails with an error takes
back what it can: the directories it created, once empty, and a dispatcher it put where none was, unless
`authorized_keys` is already in place; what it cannot take back, the message names. One that is killed takes back
nothing. It ends one of three ways:

- **It refuses to go on** — no address it can restrict the key to, a home the key's line cannot carry, or a rebuilt
  `authorized_keys` that did not come out whole: the setup gives its reason. Running it again as it stands would not
  change that.
- **An error on the way** — a full disk, a file it may not write: the setup says the setup failed, quotes the error, and
  names what was and was not moved into place (a file moved but whose rename was not confirmed on disk says so), and any
  directory it created that stays. Run the setup again once the cause is fixed.
- **It ended without saying why** — killed, or its connection lost: the setup says it stopped part-way, with what `ssh`
  said. Running it again finishes it.

A failure writing this machine's side after the target is set up is named with its file, and running the setup again
finishes that too. Interrupted with Ctrl-C, the setup says which side may be part-way.

A target whose address changed is set up again with the new one, and needs no password for it: the key still holds,
because `from=` bounds the build host's address, not the target's.

## Asking a target

```bash
mise run ask [--target <name>] -- <question>
mise run probe [--target <name>] <script> [arguments]
mise run ask --target handheld -- save-portability gba --source <a> --target <b>   # the second --target: the question's
```

`<question>` is what `python -m atlas` takes, word for word; `<script>` is a file name in `scripts/probes/`, whose
`README.md` says what a probe script may do. Without `--target` both run here, on this interpreter with this tree's
`atlas`, through the dispatcher's own bootstrap — as they run on a target, without the sandbox — and nothing else
changes. `--target <name>` (or `--target=<name>`) counts only as the first argument: everything after it is the
question, a `--target` of its own among it.

**Every call copies first.** With `--target`, the client reads the target's entry from this tool's SSH config alone
(`ssh -F ~/.config/emu-atlas-dev/ssh_config`) — so with the restricted key alone, and nothing of your own SSH config —
and sends a `copy` of the working tree as it stands, uncommitted edits and all: every file and directory under `atlas/`
and `scripts/probes/`, without `__pycache__` and `*.pyc`, in a stable order, then `tree.json` with `pyproject.toml`'s
floor. A link, or anything else that is no file or directory, is left out and named on stderr. The copy answers with the
id of the tree it made, and the client sends `ask <tree> <word>` or `probe <tree> <name> [<word>]` with that id, so the
question runs from this copy and no other client's. What the run writes after the dispatcher's marker arrives on stdout
and its stderr on stderr, byte for byte, and its exit status is the command's. A note the copy writes on stderr — an
older tree it could not remove — is shown there, and the question is still asked. `ssh` gives up on an address that
takes no connection within 15 seconds.

**The target's shell.** sshd runs the dispatcher through the user's shell, and that shell's startup files may print. On
stderr that passes on with the run's own stderr. On stdout it would land in front of the answer, so nothing on stdout
counts but the tree's id at the copy and what follows the marker at the question. Anything else there — ahead of the id
or glued to it, or ahead of the marker — ends the call without an answer, shows what arrived, each line escaped, and
asks for the startup files to be silent for a shell without a terminal; the copy went through. No id at all, or no
marker, is what a dispatcher older than this client answers: run the setup again.

What comes back is printed here and written nowhere, the tree included.

**The statuses.** atlas ends with 0 or 2; the dispatcher's 3, 4, 5 and 8 are passed on with what it said and a line that
names them as the dispatcher's. The client's failures past a usage error are none of those; a usage error ends with 2,
as atlas's does, before anything is contacted. A probe script ends with 0, 1 or 2, so that its status is never taken for
the dispatcher's, or for a failure of the client's past a usage error:

- **2 `USAGE`** — the call could not be put, as for atlas: no question, no probe script, a script `scripts/probes/` does
  not hold or the dispatcher would not take, or a target name the setup would not accept. Nothing was contacted.
- **6 `NOT_ASKED`** — no answer was taken, and nothing was done on the target but a copy where one went through: no such
  target, an entry without an address or a user, no key for targets here, no `ssh` or one that could not be started, a
  copy that could not be built here, an `ssh` that ended before the login (below), an interpreter the target cannot
  start or a dispatcher that is gone there, the target shell's output or an older dispatcher (the run, if it started,
  ended before its answer was taken), or a crash of the client before it contacted anything or after the copy went
  through. Without `--target`: Python that could not be started here, or a crash of the client, after which whatever ran
  here printed above and its status is lost.
- **7 `BROKE_OFF`** — the request ended without a status of the dispatcher's or the run's: the connection broke, the
  dispatcher or the run's sandbox there failed to start, or `ssh` failed before the login with a message no pattern
  knows, so that whether the request reached the target is not known. After a copy, the target's current tree is that
  copy whole or the one before it. A run that itself ends with 255, `ssh`'s own failure status, ends the call with 7 as
  well: once the run's marker came the two cannot be told apart. So does an answer that could not be written out here
  whole — a full disk, or a reader of the output that stopped early — whose message names what the run ended with, and a
  crash of the client during the copy or the question, whose traceback names where.

An interrupt (Ctrl-C) ends the call with 130 and says where it found it.

Each failure before the login says what to do and ends with **nothing done on the target**, or, after a copy went
through, that the question was not asked:

| What the call meets                     | What to do                                                                                |
| --------------------------------------- | ----------------------------------------------------------------------------------------- |
| no entry for the name                   | set the target up first: `mise run setup:remote-target <name> <destination>`              |
| a name this machine cannot resolve      | run the setup with the target's address, or a name the network resolves                   |
| no SSH server answering                 | the target may be asleep: wake it; or it has a new address: run the setup with that one   |
| a host key that is not the recorded one | another machine may answer there; if the target was reinstalled, remove its record, rerun |
| no record of the target's host key      | run the setup again: it shows the fingerprint and records it                              |
| the build host's key refused            | run the setup again: the address the target sees this machine at may have changed         |
| a hop on the way that fails             | as the setup words it: check that hop with your own `ssh`                                 |

## A device that is also a romm-tender target

romm-tender sets up targets the same way, with a key of its own and its own line in the same `authorized_keys`. This
setup knows its line by its key and by the comment `emu-atlas-dev@<build host>`, and leaves every other line byte for
byte — romm-tender's among them. Each of the two setups rewrites the file whole, though, so **do not run them at the
same time** against one device: the one that finishes second would not carry the other's new line.

## What rests on OpenSSH's documentation alone

Measured on OpenSSH 10.0p2 with `ssh -G -F /dev/null …`, which reads no config and connects nowhere: `--` before the
destination is accepted, also after `-p <port>`; the output is lowercase `key value` lines; and a destination that reads
as an option after `--` is refused by `ssh` itself (255, "hostname contains invalid characters"). The entry the setup
writes, read with `-F` alone and the client's own `-T -o ConnectTimeout=15 --`, resolves to its address, user, key,
known-hosts file and `BatchMode yes` there as well.

Everything else the setup and the client read off `ssh` — the messages on stderr they tell failures apart by, and exit
status 255 for a failure of `ssh`'s own — follows OpenSSH's documentation and source, **not a measurement against a real
server**. The tests run against a stand-in `ssh` (`tests/remote_target.py`) that prints what OpenSSH is documented to
print; what it does is not evidence of what `ssh` does. A message no pattern knows is quoted as `ssh` wrote it. The
patterns steer more than the message, though: whether the setup hands over from the key's login to your own (on
`Permission denied (`) and whether a failure is the target's or a hop's depend on them, so a change in OpenSSH's wording
can change what the setup does, not only what it says. The same holds for the client's status: a failure before the
login it does not recognise ends the call with 7, not 6.

Two things on the target side rest on the same reading. sshd sends a command that runs without a terminal no signal when
its connection drops, which is why the dispatcher watches its own output for the hang-up and ends the run itself; that
sshd closes that output when the connection goes is OpenSSH knowledge too, shown here with pipes, not with sshd.
