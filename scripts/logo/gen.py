#!/usr/bin/env python3
"""The atlas mark: a compass rose set in a disc that is split along a facet.

Built on the same recipe as its two sibling repos' marks, so the three read as
related: a 200-unit disc, a facet through its centre with the darker tone
below-right, flat two-tone ink, and two warm accent dots -- here two *places*.

Four bodies live here, not one. The rose is what ships; the plain needle and the
two globes are kept because they were the alternatives the choice was made
between, and switching is then a flag rather than a rewrite.

    gen.py --list                 the mark and palette names
    gen.py                        the chosen mark, to stdout
    gen.py --mark globe-grid --palette moss-teal
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass

VIEW = 200.0
CX = CY = VIEW / 2.0


@dataclass(frozen=True)
class Palette:
    """Two facet pairs, each given as (above-left, below-right), plus the two
    warm dots. The dots sit *on* the ink, so the ink has to stay dark enough to
    carry them -- inverting the mark is not an option."""

    name: str
    disc: tuple[str, str]
    ink: tuple[str, str]
    dot_a: str
    dot_b: str


_TAN, _PEACH = "#e8c49c", "#dd9880"
_MOSS_DISC = ("#93cbb0", "#77ab93")
_MOSS_INK = ("#1f4b3b", "#1a2521")

# Two directions of variation: how deep the disc sits, and how warm the accents
# are. All green, because the two siblings hold the blue and the warm sand.
PALETTES: list[Palette] = [
    Palette("moss", _MOSS_DISC, _MOSS_INK, _TAN, _PEACH),
    Palette("moss-deep", ("#7fb99e", "#649a83"), ("#16382c", "#101815"), _TAN, _PEACH),
    Palette("moss-bright", ("#a8dcc3", "#88bda6"), ("#215040", "#1a2521"), _TAN, _PEACH),
    Palette("moss-sage", ("#b4c8ba", "#96ab9c"), ("#2a4132", "#1a211c"), _TAN, _PEACH),
    Palette("moss-teal", ("#8ecbc8", "#71aaa8"), ("#174449", "#141e20"), _TAN, _PEACH),
    Palette("moss-olive", ("#bccb92", "#a0ae78"), ("#33401b", "#1c2210"), _TAN, _PEACH),
    Palette("moss-clay", _MOSS_DISC, _MOSS_INK, "#eab98c", "#d97f5f"),
]

BY_PALETTE: dict[str, Palette] = {p.name: p for p in PALETTES}

# What the shipped assets render from.
CHOSEN_PALETTE = "moss-deep"
CHOSEN_MARK = "compass-rose"


@dataclass(frozen=True)
class Geometry:
    """Everything in a 200-unit square, measured from the disc's centre."""

    disc_r: float = 100.0

    # The facet. Not 45 degrees: it comes from the plugin mark, where it runs
    # parallel to the button bars, which are slanted because the diamond they
    # sit on is wider than it is tall.
    facet_deg: float = 141.64

    bezel_r: float = 66.0
    bezel_w: float = 13.0
    needle_reach: float = 52.0  # the long spike, toward local -x
    needle_back: float = 46.0
    needle_half: float = 15.0
    rose_cross_reach: float = 42.0  # the rose's short spike, across the long one
    rose_cross_half: float = 13.0

    hub_r: float = 13.0
    bead_r: float = 10.5  # the fixed north mark, on the bezel

    sphere_r: float = 66.0
    cut_w: float = 8.0  # width of a graticule gap
    meridian_max: float = 56.0  # kept short of the rim so a cut never eats it
    place_r: float = 12.0
    place_orbit: float = 62.0

    @property
    def rot_deg(self) -> float:
        """The rotation that puts a horizontal shape onto the facet."""
        return self.facet_deg - 180.0

    def facet_polygon(self, reach: float = 800.0) -> str:
        """The darker half-plane, as a polygon far larger than the disc.

        A half-plane has no finite outline, so it is drawn as a rectangle long
        enough that its far edges never enter the disc."""
        a = math.radians(self.facet_deg)
        dx, dy = math.cos(a), math.sin(a)
        nx, ny = dy, -dx  # the seam's normal, pointing into the darker half
        corners = [
            (CX - dx * reach, CY - dy * reach),
            (CX + dx * reach, CY + dy * reach),
            (CX + dx * reach + nx * reach, CY + dy * reach + ny * reach),
            (CX - dx * reach + nx * reach, CY - dy * reach + ny * reach),
        ]
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in corners)


DEFAULT_GEOMETRY = Geometry()


# --------------------------------------------------------------------------- #
# Primitives. Offsets are from the disc's centre, in the rotated frame.
# --------------------------------------------------------------------------- #
def rounded(cx: float, cy: float, w: float, h: float, fill: str) -> str:
    return (
        f'<rect x="{CX + cx - w / 2:.2f}" y="{CY + cy - h / 2:.2f}" '
        f'width="{w:.2f}" height="{h:.2f}" rx="{min(w, h) / 2:.2f}" fill="{fill}"/>'
    )


def circle(cx: float, cy: float, r: float, fill: str) -> str:
    return f'<circle cx="{CX + cx:.2f}" cy="{CY + cy:.2f}" r="{r:.2f}" fill="{fill}"/>'


def place(cx: float, cy: float, rx: float, ry: float, fill: str) -> str:
    """A place on the sphere: a circle seen at an angle is an ellipse."""
    return f'<ellipse cx="{CX + cx:.2f}" cy="{CY + cy:.2f}" rx="{rx:.2f}" ry="{ry:.2f}" fill="{fill}"/>'


def ring(r: float, w: float, fill: str) -> str:
    return f'<circle cx="{CX}" cy="{CY}" r="{r}" stroke="{fill}" stroke-width="{w}" fill="none"/>'


def spike(reach: float, half: float, back: float, fill: str) -> str:
    """A pointer along local x: `reach` forward, `back` the other way."""
    return (
        f'<polygon points="{CX - reach:.2f},{CY} {CX},{CY - half:.2f} '
        f'{CX + back:.2f},{CY} {CX},{CY + half:.2f}" fill="{fill}"/>'
    )


def cross_spike(reach: float, half: float, fill: str) -> str:
    """The rose's short arm, across the long one."""
    return (
        f'<polygon points="{CX},{CY - reach:.2f} {CX + half:.2f},{CY} '
        f'{CX},{CY + reach:.2f} {CX - half:.2f},{CY}" fill="{fill}"/>'
    )


def turn(inner: str, g: Geometry, extra: float = 0.0) -> str:
    return f'<g transform="rotate({g.rot_deg + extra:.2f} {CX} {CY})">{inner}</g>'


# --------------------------------------------------------------------------- #
# Bodies. Each returns the ink shapes for one tone, given the needle's angle
# (compasses) or the globe's longitude (globes).
# --------------------------------------------------------------------------- #
def compass_body(fill: str, g: Geometry, rose: bool, swing: float = 0.0) -> str:
    point = spike(g.needle_reach, g.needle_half, g.needle_back, fill)
    if rose:
        point += cross_spike(g.rose_cross_reach, g.rose_cross_half, fill)
    if swing:
        point = f'<g transform="rotate({swing:.2f} {CX} {CY})">{point}</g>'
    return ring(g.bezel_r, g.bezel_w, fill) + point


def compass_dots(pal: Palette, g: Geometry, swing: float = 0.0) -> str:
    """The hub turns with the needle; the bead is the fixed north mark it settles
    back onto."""
    hub = circle(0.0, 0.0, g.hub_r, pal.dot_a)
    if swing:
        hub = f'<g transform="rotate({swing:.2f} {CX} {CY})">{hub}</g>'
    return hub + circle(-g.bezel_r, 0.0, g.bead_r, pal.dot_b)


def globe_mask(uid: str, g: Geometry, bands: bool, lon: float) -> str:
    """The graticule as a *cut*, not a drawn line.

    A cut lets the disc through, so every line carries the facet's two tones for
    free; a line drawn on top would need its own second pass and would still be
    one flat colour across the seam.

    A meridian at longitude `lon` projects to an ellipse of half-width
    R*|sin lon| -- edge-on toward the viewer it is a straight line, a quarter
    turn later it lies on the limb."""
    cuts = _cut_bar(0.0, g) if not bands else _cut_bar(-27.0, g) + _cut_bar(27.0, g)
    cuts += _cut_meridian(g.meridian_max * abs(math.sin(lon)), g)
    if not bands:  # the grid carries a second meridian, a quarter turn along
        cuts += _cut_meridian(g.meridian_max * abs(math.cos(lon)), g)
    return (
        f'<mask id="{uid}" maskUnits="userSpaceOnUse" x="0" y="0" width="{VIEW:.0f}" height="{VIEW:.0f}">'
        f'<circle cx="{CX}" cy="{CY}" r="{g.sphere_r}" fill="#fff"/>{cuts}</mask>'
    )


def _cut_bar(cy: float, g: Geometry) -> str:
    w = g.sphere_r * 2.0 + 20.0
    return (
        f'<rect x="{CX - w / 2:.1f}" y="{CY + cy - g.cut_w / 2:.1f}" '
        f'width="{w:.1f}" height="{g.cut_w:.1f}" fill="#000"/>'
    )


def _cut_meridian(rx: float, g: Geometry) -> str:
    return (
        f'<ellipse cx="{CX}" cy="{CY}" rx="{max(1.0, rx):.2f}" ry="{g.sphere_r}" fill="none" '
        f'stroke="#000" stroke-width="{g.cut_w}"/>'
    )


def globe_body(fill: str, g: Geometry, uid: str) -> str:
    return f'<circle cx="{CX}" cy="{CY}" r="{g.sphere_r}" fill="{fill}" mask="url(#{uid})"/>'


def globe_places(pal: Palette, g: Geometry, lon: float, clip_id: str) -> str:
    """Two places, carried around by the turn.

    Width foreshortens with the facing and reaches zero exactly at the limb, so a
    place narrows away instead of switching off. Height is untouched: a circle at
    the edge of a sphere is as tall as it ever was."""
    out = ""
    # The two longitudes are chosen so that at STILL_LON both places sit on the
    # near side, roughly symmetric about the centre. Anything close to half a
    # turn apart cannot manage that: their visibility windows are each half the
    # sphere, so they would never be up together.
    for lat_deg, lon0_deg, fill in ((-30.0, -70.0, pal.dot_a), (28.0, 6.0, pal.dot_b)):
        angle = math.radians(lon0_deg) + lon
        facing = math.cos(angle)  # 1 dead centre, 0 edge-on, negative = far side
        if facing <= 0.0:
            continue
        lat = math.radians(lat_deg)
        x = g.place_orbit * math.cos(lat) * math.sin(angle)
        y = g.place_orbit * math.sin(lat)
        out += place(x, y, g.place_r * facing, g.place_r, fill)
    return f'<g clip-path="url(#{clip_id})">{out}</g>' if out else ""


MARKS: tuple[str, ...] = ("compass-rose", "compass-bezel", "globe-bands", "globe-grid")

# Where a globe stands when it is not turning. Not zero: at longitude zero the
# meridian faces the viewer edge-on and projects to a bare straight line, which
# is a legitimate frame of the turn but a poor still. A third of a quarter turn
# in, both meridians are open and the graticule reads.
STILL_LON = math.radians(30.0)


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def mark(
    uid: str,
    name: str,
    pal: Palette,
    g: Geometry = DEFAULT_GEOMETRY,
    swing: float = 0.0,
    lon: float = STILL_LON,
) -> str:
    """The disc, the body in both ink tones, and the two places.

    The facet clip sits OUTSIDE the rotation on purpose. `clipPathUnits` defaults
    to userSpaceOnUse, so the polygon is read in the user space of whatever
    references it; inside the rotation the ink's seam turns with the body and
    lands 38.36 degrees off the disc's own, which shows up as a shadow inside the
    mark running at a different angle to the one across the disc."""
    if name not in MARKS:
        raise ValueError(f"unknown mark {name!r}; one of {', '.join(MARKS)}")
    facet = g.facet_polygon()
    globe = name.startswith("globe")
    extra_defs = globe_mask(f"g{uid}", g, name == "globe-bands", lon) if globe else ""
    extra_defs += f'<clipPath id="s{uid}"><circle cx="{CX}" cy="{CY}" r="{g.sphere_r}"/></clipPath>' if globe else ""

    def shapes(fill: str) -> str:
        if globe:
            return globe_body(fill, g, f"g{uid}")
        if name == "compass-bezel":
            return compass_body(fill, g, rose=False, swing=swing)
        return compass_body(fill, g, rose=True, swing=swing)

    dots = globe_places(pal, g, lon, f"s{uid}") if globe else compass_dots(pal, g, swing)
    return (
        f'<defs><clipPath id="d{uid}"><circle cx="{CX}" cy="{CY}" r="{g.disc_r}"/></clipPath>'
        f'<clipPath id="f{uid}"><polygon points="{facet}"/></clipPath>{extra_defs}</defs>'
        f'<g clip-path="url(#d{uid})">'
        f'<circle cx="{CX}" cy="{CY}" r="{g.disc_r}" fill="{pal.disc[0]}"/>'
        f'<polygon points="{facet}" fill="{pal.disc[1]}"/>'
        f"{turn(shapes(pal.ink[0]), g)}"
        f'<g clip-path="url(#f{uid})">{turn(shapes(pal.ink[1]), g)}</g>'
        f"{turn(dots, g)}"
        f"</g>"
    )


def standalone(
    name: str,
    pal: Palette,
    g: Geometry = DEFAULT_GEOMETRY,
    size: int = 512,
    swing: float = 0.0,
    lon: float = STILL_LON,
) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {VIEW:.0f} {VIEW:.0f}">{mark("m", name, pal, g, swing, lon)}</svg>'
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="print the mark and palette names and exit")
    ap.add_argument("--mark", default=CHOSEN_MARK, help=f"default: {CHOSEN_MARK}")
    ap.add_argument("--palette", default=CHOSEN_PALETTE, help=f"default: {CHOSEN_PALETTE}")
    ap.add_argument("--size", type=int, default=512)
    args = ap.parse_args(argv)

    if args.list:
        print("marks:    " + ", ".join(MARKS))
        print("palettes: " + ", ".join(p.name for p in PALETTES))
        return 0
    if args.mark not in MARKS or args.palette not in BY_PALETTE:
        print("unknown mark or palette; try --list", file=sys.stderr)
        return 2
    print(standalone(args.mark, BY_PALETTE[args.palette], size=args.size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
