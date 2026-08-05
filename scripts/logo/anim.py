#!/usr/bin/env python3
"""Two motions: a needle settling, and a globe turning.

The compass is displaced once and swings back with the amplitude decaying, the
way a needle finds north again -- it settles onto the bead, which stays put on
the bezel. The globe makes one revolution per loop, and only what really moves
on a turning globe moves: the parallels stand still while the meridians sweep.

    anim.py --list                the motion names
    anim.py --plot                the schedule, frame by frame
    anim.py --frame 18            one frame's SVG
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass

import gen


@dataclass(frozen=True)
class Motion:
    """One loop. Frame 0 is the resting pose, and so is the last frame's
    successor -- both motions are periodic, so the loop closes without a seam."""

    frames: int = 60
    fps: int = 20  # 60 @ 20 = 3.0s

    # Needle: displaced over `throw` frames, then settling over `settle`.
    rest: int = 12
    throw: int = 7
    settle: int = 34
    swing_deg: float = 52.0
    decay: float = 3.6
    wobbles: float = 1.75

    def needle_at(self, i: int) -> float:
        i %= self.frames
        thrown = self.rest + self.throw
        if i < self.rest:
            return 0.0
        if i < thrown:
            return self.swing_deg * _smooth(_seg(i, self.rest, thrown))
        if i < thrown + self.settle:
            u = _seg(i, thrown, thrown + self.settle)
            return self.swing_deg * math.exp(-self.decay * u) * math.cos(2.0 * math.pi * self.wobbles * u)
        return 0.0

    def longitude_at(self, i: int) -> float:
        """One full revolution per loop."""
        return 2.0 * math.pi * (i % self.frames) / self.frames

    def turning_points(self) -> list[int]:
        """The frames worth looking at when reviewing a change."""
        thrown = self.rest + self.throw
        step = max(1, self.frames // 10)
        return sorted({0, self.rest, thrown, *range(thrown, self.frames, step), self.frames - 1})


def _smooth(u: float) -> float:
    return u * u * (3.0 - 2.0 * u)


def _seg(i: int, lo: int, hi: int) -> float:
    return (i - lo) / (hi - lo)


DEFAULT_MOTION = Motion()


def frame(
    i: int,
    name: str,
    pal: gen.Palette,
    motion: Motion = DEFAULT_MOTION,
    g: gen.Geometry = gen.DEFAULT_GEOMETRY,
    size: int = 256,
) -> str:
    if name.startswith("globe"):
        return gen.standalone(name, pal, g, size=size, lon=motion.longitude_at(i))
    return gen.standalone(name, pal, g, size=size, swing=motion.needle_at(i))


def plot(motion: Motion) -> str:
    rows = ["frame  needle   longitude"]
    for i in range(motion.frames):
        rows.append(f"{i:5d}  {motion.needle_at(i):6.2f}  {math.degrees(motion.longitude_at(i)):9.1f}")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--mark", default=gen.CHOSEN_MARK, help=f"default: {gen.CHOSEN_MARK}")
    ap.add_argument("--palette", default=gen.CHOSEN_PALETTE, help=f"default: {gen.CHOSEN_PALETTE}")
    ap.add_argument("--plot", action="store_true", help="print the schedule frame by frame")
    ap.add_argument("--frame", type=int, help="print one frame's SVG")
    args = ap.parse_args(argv)

    if args.list:
        print("marks:    " + ", ".join(gen.MARKS))
        print("palettes: " + ", ".join(p.name for p in gen.PALETTES))
        return 0
    if args.mark not in gen.MARKS or args.palette not in gen.BY_PALETTE:
        print("unknown mark or palette; try --list", file=sys.stderr)
        return 2

    if args.plot:
        print(plot(DEFAULT_MOTION))
    elif args.frame is not None:
        print(frame(args.frame, args.mark, gen.BY_PALETTE[args.palette]))
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
