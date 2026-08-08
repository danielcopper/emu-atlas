# Re-verification — what to do when an arrangement updates

atlas's world knowledge is verified against a pinned version of an arrangement (`atlas/data/arrangement_evidence.json`;
today RetroDECK 0.10.9b). When the machine states a different one, every answer carries `arrangement-version-drifted`
and keeps carrying it until this runs. The caveat is the reminder, this page is the work — for a typical point release,
under an hour.

The order is deliberate: each step narrows what the next one has to look at, and the citations atlas already carries
_are_ the checklist. Nothing here is a re-audit from scratch.

## 1. What moved at all

Diff the arrangement's build manifest between the pinned release and the new one — for RetroDECK, the Flatpak manifest
in its repository at the two tags. This is a release-to-release list of component bumps: RetroArch, ES-DE, the cores,
the emulator components. A component that did not move cannot have changed behaviour, and everything atlas knows about
it still holds.

## 2. Diff only what atlas cites

For each moved component, diff the old pin against what the new release ships — **only over the files atlas cites**.
Every resolver rule carries `file:line` citations at a pinned revision (see the header of
`docs/research/retrodeck-save-placement.md` and the evidence column of `docs/research/coverage-matrix.md`); that set is
small, and it is the whole surface world knowledge rests on.

- Empty diff over the cited spots → re-pin and move on.
- Non-empty → read that spot. Same behaviour, moved lines → update the citation.
- Changed behaviour → a **new generation**: new vectors for it, and the vectors for the old generation stay (one fixture
  machine per supported generation, never deleted).

**If ES-DE moved, `atlas/data/system_ids.json` is one of the cited spots.** That list _is_ the `es_systems.xml` of the
pinned build — atlas's whole system vocabulary, cited to that file — so an ES-DE bump means re-deriving it from the new
deployment. Re-derive, do not patch by hand: a system the build renames and one it drops look the same from inside the
list, and both turn a name clients validate against into one no question can answer.

The signal is a test: `tests/test_systems.py` parses the deployed file and asserts set-identity, so it fails on this
machine the moment the two disagree. It **skips** where RetroDECK is not installed, so a green CI run is not the check —
this step is.

Note what does _not_ signal it. `known_systems` and `from_esde_system` are pure lookups over that list, with no answer
to hang a caveat on, so nothing warns a caller that the vocabulary they validated against came from a build this machine
no longer runs. The drift signal lives on the **answers** instead (`arrangement-version-drifted`, step 5), which is
where a stale vocabulary surfaces as a system that resolves to nothing. Clients that validate their own platform maps
against `known_systems()` (`docs/how-to-use.md`) re-run that check when they bump atlas — the id set moving is exactly
what their check is for.

## 3. Cores: only carded, only moved

Rule cards (`atlas/data/core_oddities.json`) and audit entries (`atlas/data/core_audit.json`) pin core versions of their
own. Re-check the intersection — cards whose core actually moved — following the method in `docs/research/core-audit.md`
(unfiltered scan, then upstream source, then live observation).

Everything else is guarded at runtime and needs no pass: a card whose governing option a core no longer registers steps
aside by feature detection (`card-generation-mismatch`), and a card pinned to versions this machine does not run says so
per answer (`unverified-version`).

## 4. One full-surface live run

Run the whole answer surface against the updated reference machine and byte-diff the canonical contract serializations
against the baseline from the previous round (detect → every question → every catalogue system). Fixtures prove the
logic; only the machine proves the reality, and a diff of zero is what "verified" means here.

## 5. Re-pin — and until then, the caveat stands

Update the `verified` block in `atlas/data/arrangement_evidence.json` (version, date, reference) and the pinned revision
in the research headers. That one file retires the caveat, and **no resolver changes** — but the corpus does move with
it, so expect the next step rather than a green suite.

Every fixture machine that states the old version is a drifted machine the moment the pin moves, and the vector runner
says so: those vectors fail until their expected blocks match. That is the tripwire working on the corpus, not a
regression. For each one, decide which machine it is meant to be — bump its marker version to the new pin where the
vector is about something else (most of them: the flycast, LRPS2 and opera cards, the firmware cases), or take the drift
caveat into its expected block where the point _is_ a machine running an older build. Vectors for old generations are
never deleted, so the second option is a real one.

Until all of this is done, the drift caveat is the honest state of things: the answers still stand — the configs are
read the way upstream reads them either way — but nobody has confirmed the wiring end to end on the version running
here.
