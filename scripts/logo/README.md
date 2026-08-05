# Logo

The mark: a compass rose set in a disc, split along a facet. It animates — the needle is displaced once and settles back
onto the bead on the bezel.

It is built on the same recipe as the marks of its two sibling repos
([decky-romm-sync](https://github.com/danielcopper/decky-romm-sync),
[romm-gavel](https://github.com/danielcopper/romm-gavel)), so the three read as related: a 200-unit disc, a facet
through its centre with the darker tone below-right, flat two-tone ink, two warm accent dots — here two _places_.

A compass rather than a globe, because a globe is a fixed printed picture of a world, which is the thing this library
argues against. A compass is an instrument that answers now, and its reading depends on where it is held — the same
shape as asking every question of an installation rather than of "the system".

Four bodies are kept and all four ship: `compass-rose` (what the README uses), `compass-bezel`, `globe-bands` and
`globe-grid`. The other three were the alternatives the choice was made between; keeping them means a switch is a flag
rather than a rewrite, and the globes stay available for a social preview or wherever a second mark is wanted.

## Regenerating

```sh
python3 build.py --install
```

That renders all four marks and writes each shipped copy from the same render. Needs `rsvg-convert` and `ffmpeg` on
PATH. Without `--install` it writes to `out/` instead, which is the way to look at a change before it lands.
`--mark <name>` narrows it to one.

| File                          | Where it goes                                   |
| ----------------------------- | ----------------------------------------------- |
| `<mark>.svg` (512px)          | `assets/` — the still                           |
| `<mark>-animated.gif` (256px) | `assets/` — the loop; the README shows the rose |

`gen.py` and `anim.py` also stand alone, for looking at one thing:

```sh
python3 gen.py --list                    # the mark and palette names
python3 gen.py --mark globe-grid         # one mark, any body
python3 gen.py --palette moss-teal       # ...in any palette
python3 anim.py --plot                   # the schedule, frame by frame
python3 anim.py --frame 18               # one frame's SVG
```

## Changing things

Three config objects, and nothing else worth editing:

- **`gen.Palette`** — one row per candidate in `PALETTES`; `CHOSEN_PALETTE` names the one that ships. Each carries two
  facet pairs, disc and ink, given as (above-left, below-right), plus the two dot colours. The seven rows vary in two
  directions: how deep the disc sits, and how warm the accents are.
- **`gen.Geometry`** — every position and size, in a 200-unit square: the disc and its facet, the bezel, the needle and
  the rose's cross arm, the sphere, and how wide a graticule cut is.
- **`anim.Motion`** — frame count, rate, and the needle's throw, decay and wobble count.

## Notes

Four things are worth knowing before touching this.

**The facet clip belongs outside the rotation.** `clipPathUnits` defaults to `userSpaceOnUse`, so the polygon is read in
the user space of whatever references it. Put the clip group inside the rotation and the ink's seam turns with the body,
landing 38.36° off the disc's own — which shows up as a shadow inside the mark at a different angle to the one across
the disc.

**The graticule is cut, not drawn.** The globes are solid spheres with their lines masked _out_. A cut lets the disc
through, so every line carries the facet's two tones for free; a line drawn on top would need its own second pass and
would still be one flat colour across the seam.

**A meridian's width is its longitude.** At longitude λ a meridian projects to an ellipse of half-width `R·|sin λ|` —
edge-on toward the viewer it is a straight line, a quarter turn later it lies on the limb. That is the whole rotation:
the parallels never move, because on a turning globe they don't. `STILL_LON` exists because longitude zero is a poor
still — the meridian is edge-on there and reduces to a bare line.

**A place is an ellipse, not a dot.** Its width foreshortens with how squarely it faces the viewer and reaches zero
exactly at the limb, so it narrows away instead of switching off; its height never changes, because a circle at the edge
of a sphere is as tall as it ever was. The two longitudes are picked so both places are on the near side at `STILL_LON`
— two places half a turn apart could never be up together, since each is visible for exactly half the rotation.

The GIF uses one palette for the whole sequence and no dithering. Dithering flat colour only adds grain, and costs about
a fifth of the file because LZW cannot compress noise.
