#!/usr/bin/env python3
"""Render the marks and write every shipped copy from the same render.

    build.py --install            write assets/ (what ships)
    build.py                      write out/ instead, to look at a change first
    build.py --mark globe-grid --palette moss-teal --install

Needs `rsvg-convert` and `ffmpeg` on PATH.

All four marks are written, not just the chosen one: the README uses
`compass-rose`, the rest stay available for the social preview and wherever a
second mark is wanted. `--mark` narrows it to one.
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

import anim
import gen

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Flat colours, no dithering: dithering flat colour only adds grain, and costs
# about a fifth of the file because LZW cannot compress noise.
GIF_FILTER = (
    "[0:v]split[a][b];"
    "[a]palettegen=max_colors=32:stats_mode=full:reserve_transparent=1[p];"
    "[b][p]paletteuse=dither=none:diff_mode=rectangle"
)


def require(*tools: str) -> None:
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        raise SystemExit(f"not on PATH: {', '.join(missing)}")


def rasterise(svg: str, png: pathlib.Path, size: int) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False) as fh:
        fh.write(svg)
        tmp = pathlib.Path(fh.name)
    try:
        subprocess.run(["rsvg-convert", "-w", str(size), "-o", str(png), str(tmp)], check=True)
    finally:
        tmp.unlink(missing_ok=True)


def build_gif(out: pathlib.Path, name: str, pal: gen.Palette, motion: anim.Motion, size: int) -> None:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="atlas-logo"))
    try:
        for i in range(motion.frames):
            rasterise(anim.frame(i, name, pal, motion, size=size), tmp / f"f{i:03d}.png", size)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-framerate", str(motion.fps), "-i", str(tmp / "f%03d.png"),
             "-filter_complex", GIF_FILTER, "-loop", "0", str(out)],
            check=True,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build(out: pathlib.Path, names: tuple[str, ...], pal: gen.Palette, size: int, gif_size: int) -> None:
    require("rsvg-convert", "ffmpeg")
    out.mkdir(parents=True, exist_ok=True)
    motion = anim.DEFAULT_MOTION

    for name in names:
        svg = out / f"{name}.svg"
        svg.write_text(gen.standalone(name, pal, size=size))
        gif = out / f"{name}-animated.gif"
        build_gif(gif, name, pal, motion, gif_size)
        for path in (svg, gif):
            print(f"  {path.relative_to(ROOT)}  ({path.stat().st_size:,}b)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", action="store_true", help="write assets/ instead of out/")
    ap.add_argument("--mark", help="build one mark instead of all four")
    ap.add_argument("--palette", default=gen.CHOSEN_PALETTE, help=f"default: {gen.CHOSEN_PALETTE}")
    ap.add_argument("--size", type=int, default=512, help="the stills, in px")
    ap.add_argument("--gif-size", type=int, default=256, help="the animations, in px")
    args = ap.parse_args(argv)

    if args.palette not in gen.BY_PALETTE:
        print(f"unknown palette {args.palette!r}; gen.py --list", file=sys.stderr)
        return 2
    if args.mark is not None and args.mark not in gen.MARKS:
        print(f"unknown mark {args.mark!r}; gen.py --list", file=sys.stderr)
        return 2

    names = (args.mark,) if args.mark else gen.MARKS
    out = ROOT / "assets" if args.install else pathlib.Path(__file__).parent / "out"
    build(out, names, gen.BY_PALETTE[args.palette], args.size, args.gif_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
