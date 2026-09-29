"""Build Research Tree's icon set from geometry, check it, and write icons.tsx.

Every icon is drawn on a 16-unit grid from primitives whose coordinates come
from keylines, mirrored halves, the canvas's S-shaped edges and arcs of known
radius, so the shapes can be read and checked rather than eyeballed. Before
anything is written, each icon must:

- keep every stroke edge inside the live area, a 1-unit margin;
- be symmetric where it claims to be;
- put straight strokes that are off the centre line on .25 or .75, where a
  1.5-unit stroke has its edges on whole device pixels at 16px on a 2x screen.

Usage, from the repository root:

    python3 apps/web/scripts/icons.py                  # rewrite icons.tsx
    python3 apps/web/scripts/icons.py --sheet out.html # and a review sheet

The sheet shows each icon enlarged over its grid, at the sizes the app uses,
and rasterized at 1x and 2x so soft edges are visible.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

GRID = 16
STROKE = 1.5
HALF = STROKE / 2
LIVE_MIN, LIVE_MAX = 1.0, 15.0

# Keylines: circles and rounded rectangles are drawn to shared sizes so icons
# read as one weight next to each other.
CIRCLE_R = 5.75
RADIUS_LARGE = 2.0
RADIUS_SMALL = 1.0

ICONS_TSX = Path(__file__).resolve().parents[1] / "src" / "components" / "ui" / "icons.tsx"

Point = tuple[float, float]


def fmt(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def pt(x: float, y: float) -> str:
    return f"{fmt(x)} {fmt(y)}"


# ------------------------------------------------------------- primitives ---


@dataclass
class Element:
    tag: str
    attrs: dict[str, str]
    # Points on the centreline, dense enough to bound the element.
    samples: list[Point] = field(default_factory=list)
    filled: bool = False
    # Straight horizontal and vertical strokes: ("x", 6.25) is a vertical one.
    axis_lines: list[tuple[str, float]] = field(default_factory=list)

    def extent(self) -> tuple[float, float, float, float]:
        grow = 0.0 if self.filled else HALF
        xs = [x for x, _ in self.samples]
        ys = [y for _, y in self.samples]
        return min(xs) - grow, min(ys) - grow, max(xs) + grow, max(ys) + grow


def circle(cx: float, cy: float, r: float, *, filled: bool = False) -> Element:
    samples = [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in _angles(64)]
    attrs = {"cx": fmt(cx), "cy": fmt(cy), "r": fmt(r)}
    if filled:
        attrs.update({"fill": "currentColor", "stroke": "none"})
    return Element("circle", attrs, samples, filled)


def rect(x: float, y: float, w: float, h: float, r: float) -> Element:
    assert r <= min(w, h) / 2, "corner radius larger than the rect allows"
    return Element(
        "rect",
        {"x": fmt(x), "y": fmt(y), "width": fmt(w), "height": fmt(h), "rx": fmt(r)},
        [(x, y), (x + w, y), (x, y + h), (x + w, y + h)],
        axis_lines=[("x", x), ("x", x + w), ("y", y), ("y", y + h)],
    )


class PathBuilder:
    """An SVG path that records the geometry of what it draws."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.samples: list[Point] = []
        self.axis_lines: list[tuple[str, float]] = []
        self.pos: Point = (0.0, 0.0)
        self.start: Point = (0.0, 0.0)

    def M(self, x: float, y: float) -> PathBuilder:
        self.parts.append(f"M{pt(x, y)}")
        self.pos = self.start = (x, y)
        self.samples.append(self.pos)
        return self

    def L(self, x: float, y: float) -> PathBuilder:
        assert math.dist(self.pos, (x, y)) > 0.05, "degenerate segment"
        if abs(x - self.pos[0]) < 1e-9:
            self.axis_lines.append(("x", x))
        elif abs(y - self.pos[1]) < 1e-9:
            self.axis_lines.append(("y", y))
        self.parts.append(f"L{pt(x, y)}")
        self.pos = (x, y)
        self.samples.append(self.pos)
        return self

    def C(self, c1: Point, c2: Point, end: Point) -> PathBuilder:
        start = self.pos
        self.parts.append(f"C{pt(*c1)} {pt(*c2)} {pt(*end)}")
        self.samples.extend(_bezier(start, c1, c2, end, i / 32) for i in range(1, 33))
        self.pos = end
        return self

    def A(self, r: float, end: Point, *, sweep: bool = True, center: Point | None = None) -> PathBuilder:
        """A circular arc; with its centre known, the checks sample it exactly."""
        self.parts.append(f"A{fmt(r)} {fmt(r)} 0 0 {int(sweep)} {pt(*end)}")
        if center is not None:
            a0 = math.atan2(self.pos[1] - center[1], self.pos[0] - center[0])
            a1 = math.atan2(end[1] - center[1], end[0] - center[0])
            if sweep and a1 < a0:
                a1 += 2 * math.pi
            if not sweep and a1 > a0:
                a1 -= 2 * math.pi
            for i in range(1, 33):
                a = a0 + (a1 - a0) * i / 32
                self.samples.append((center[0] + r * math.cos(a), center[1] + r * math.sin(a)))
        else:
            self.samples.append(end)
        self.pos = end
        return self

    def Z(self) -> PathBuilder:
        self.parts.append("Z")
        self.pos = self.start
        return self

    def element(self) -> Element:
        return Element("path", {"d": "".join(self.parts)}, self.samples, axis_lines=list(self.axis_lines))


def line(*points: Point) -> Element:
    path = PathBuilder().M(*points[0])
    for point in points[1:]:
        path.L(*point)
    return path.element()


def join(*paths: PathBuilder) -> Element:
    """Several subpaths drawn as one path element."""
    joined = PathBuilder()
    for path in paths:
        joined.parts.extend(path.parts)
        joined.samples.extend(path.samples)
        joined.axis_lines.extend(path.axis_lines)
    return joined.element()


def rounded_polygon(points: list[Point], r: float) -> Element:
    """A closed polygon whose every corner is a circular arc of radius r."""

    corners = []
    for i, cur in enumerate(points):
        prev, nxt = points[i - 1], points[(i + 1) % len(points)]
        v1 = _unit((prev[0] - cur[0], prev[1] - cur[1]))
        v2 = _unit((nxt[0] - cur[0], nxt[1] - cur[1]))
        angle = math.acos(max(-1.0, min(1.0, v1[0] * v2[0] + v1[1] * v2[1])))
        tangent = r / math.tan(angle / 2)
        bisector = _unit((v1[0] + v2[0], v1[1] + v2[1]))
        distance = r / math.sin(angle / 2)
        corners.append((
            (cur[0] + v1[0] * tangent, cur[1] + v1[1] * tangent),
            (cur[0] + v2[0] * tangent, cur[1] + v2[1] * tangent),
            (cur[0] + bisector[0] * distance, cur[1] + bisector[1] * distance),
            v1[0] * v2[1] - v1[1] * v2[0] < 0,
        ))
    path = PathBuilder().M(*corners[0][1])
    for i in range(1, len(corners) + 1):
        a, b, centre, sweep = corners[i % len(corners)]
        path.L(*a).A(r, b, sweep=sweep, center=centre)
    return path.Z().element()


def edge(start: Point, end: Point) -> PathBuilder:
    """A tree edge as the canvas draws one: it leaves and enters horizontally."""
    mid = (start[0] + end[0]) / 2
    return PathBuilder().M(*start).C((mid, start[1]), (mid, end[1]), end)


def _angles(count: int) -> list[float]:
    return [2 * math.pi * i / count for i in range(count)]


def _unit(v: Point) -> Point:
    length = math.hypot(*v)
    return v[0] / length, v[1] / length


def _bezier(p0: Point, p1: Point, p2: Point, p3: Point, t: float) -> Point:
    mt = 1 - t
    return (
        mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0],
        mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1],
    )


def P() -> PathBuilder:
    return PathBuilder()


# ------------------------------------------------------------------ icons ---


@dataclass
class Icon:
    name: str
    elements: list[Element]
    # "x": mirror-symmetric about x=8, "y": about y=8.
    symmetry: str = ""
    doc: str | None = None
    # Drawn with fill on the root svg rather than strokes.
    filled: bool = False


def tree_icon() -> Icon:
    # The canvas at glyph size: a root on the left, a little larger than the
    # two branches on the right, joined by the canvas's S-shaped edges. Nodes
    # are rounded squares, the canvas's cards.
    root_size, branch = 4.5, 4.0
    root = (1.75, 8 - root_size / 2)
    top = (10.25, 1.75)
    bottom = (10.25, GRID - 1.75 - branch)
    return Icon("TreeIcon", [
        rect(*root, root_size, root_size, RADIUS_SMALL),
        rect(*top, branch, branch, RADIUS_SMALL),
        rect(*bottom, branch, branch, RADIUS_SMALL),
        join(
            edge((root[0] + root_size, 8), (top[0], top[1] + branch / 2)),
            edge((root[0] + root_size, 8), (bottom[0], bottom[1] + branch / 2)),
        ),
    ], "y", doc="A root and two branches joined as the canvas joins them.")


def paper_icon() -> Icon:
    left, right, top, bottom, fold = 2.75, 13.25, 1.75, 14.25, 3.5
    r = RADIUS_LARGE - 0.25
    body = (
        P().M(right - fold, top).L(left + r, top)
        .A(r, (left, top + r), sweep=False, center=(left + r, top + r))
        .L(left, bottom - r)
        .A(r, (left + r, bottom), sweep=False, center=(left + r, bottom - r))
        .L(right - r, bottom)
        .A(r, (right, bottom - r), sweep=False, center=(right - r, bottom - r))
        .L(right, top + fold).Z()
    )
    crease = (
        P().M(right - fold, top).L(right - fold, top + fold - 1)
        .A(1, (right - fold + 1, top + fold), sweep=False, center=(right - fold + 1, top + fold - 1))
        .L(right, top + fold)
    )
    lines = join(P().M(5.5, 8.25).L(10.5, 8.25), P().M(5.5, 11.25).L(8.75, 11.25))
    return Icon("PaperIcon", [body.element(), crease.element(), lines])


def chat_icon() -> Icon:
    left, right, top, bottom, r = 2.25, 13.75, 2.75, 11.25, RADIUS_LARGE - 0.5
    bubble = (
        P().M(right, bottom - r)
        .A(r, (right - r, bottom), center=(right - r, bottom - r))
        .L(6, bottom).L(left, 13.75).L(left, top + r)
        .A(r, (left + r, top), center=(left + r, top + r))
        .L(right - r, top)
        .A(r, (right, top + r), center=(right - r, top + r))
        .Z()
    )
    return Icon("ChatIcon", [bubble.element()])


def search_icon() -> Icon:
    c, r = (7.25, 7.25), 4.5
    d = _unit((1, 1))
    handle = (c[0] + d[0] * (r + 0.25), c[1] + d[1] * (r + 0.25))
    return Icon("SearchIcon", [circle(*c, r), line(handle, (13.25, 13.25))])


def warning_icon() -> Icon:
    triangle = rounded_polygon([(8, 1.75), (14.5, 13.25), (1.5, 13.25)], 1.25)
    return Icon("WarningIcon", [triangle, line((8, 6), (8, 8.75)), circle(8, 11, 0.8, filled=True)], "x")


def book_icon() -> Icon:
    def page(sign: int) -> PathBuilder:
        def x(value: float) -> float:
            return 8 + sign * (value - 8)
        return (
            P().M(8, 4.5).C((x(6.75), 3.4), (x(5), 3), (x(2.25), 3))
            .L(x(2.25), 12.5).C((x(5), 12.5), (x(6.75), 12.9), (8, 14))
        )
    return Icon("BookIcon", [join(page(1), page(-1)), line((8, 4.5), (8, 14))], "x")


def trash_icon() -> Icon:
    lid = P().M(2.75, 4.25).L(13.25, 4.25)
    handle = (
        P().M(6.25, 4.25).L(6.25, 3).A(0.75, (7, 2.25), center=(7, 3)).L(9, 2.25)
        .A(0.75, (9.75, 3), center=(9, 3)).L(9.75, 4.25)
    )
    can = (
        P().M(4, 4.25).L(4.62, 12.65).A(1.25, (5.87, 13.75), sweep=False).L(10.13, 13.75)
        .A(1.25, (11.38, 12.65), sweep=False).L(12, 4.25)
    )
    slats = join(P().M(6.75, 7).L(6.75, 10.75), P().M(9.25, 7).L(9.25, 10.75))
    return Icon("TrashIcon", [join(lid, handle, can), slats], "x")


def pencil_icon() -> Icon:
    body = P().M(10.6, 2.9).A(1.77, (13.1, 5.4)).L(5.9, 12.6).L(2.5, 13.5).L(3.4, 10.1).Z()
    return Icon("PencilIcon", [body.element(), line((9.5, 4), (12, 6.5))])


def gear_icon(teeth: int = 8) -> Icon:
    outer, root_r, hole = 6.25, 4.75, 2.0
    top_half, base_half = math.radians(10), math.radians(15)

    def polar(radius: float, angle: float) -> Point:
        return 8 + radius * math.cos(angle), 8 + radius * math.sin(angle)

    path = P()
    for i in range(teeth):
        a = -math.pi / 2 + 2 * math.pi * i / teeth
        base_start, base_end = polar(root_r, a - base_half), polar(root_r, a + base_half)
        if i == 0:
            path.M(*base_start)
        else:
            path.A(root_r, base_start, center=(8, 8))
        path.L(*polar(outer, a - top_half)).A(outer, polar(outer, a + top_half), center=(8, 8)).L(*base_end)
    path.A(root_r, polar(root_r, -math.pi / 2 - base_half), center=(8, 8)).Z()
    return Icon("GearIcon", [path.element(), circle(8, 8, hole)], "x")


def key_icon() -> Icon:
    bow, bow_r = (5.25, 10.75), 3.0
    along = _unit((1, -1))
    start = (bow[0] + along[0] * bow_r, bow[1] + along[1] * bow_r)
    end = (13.5, 2.5)
    teeth = P()
    across = _unit((1, 1))
    for at, length in ((0.62, 2.0), (0.84, 1.6)):
        base = (start[0] + (end[0] - start[0]) * at, start[1] + (end[1] - start[1]) * at)
        teeth.M(*base).L(base[0] + across[0] * length, base[1] + across[1] * length)
    return Icon("KeyIcon", [circle(*bow, bow_r), join(P().M(*start).L(*end), teeth)])


def help_icon() -> Icon:
    mark = P().M(6.25, 6.3).A(1.8, (9.75, 6.85)).C((9.75, 8.05), (8, 8.4), (8, 9.25))
    return Icon("HelpIcon", [circle(8, 8, CIRCLE_R), mark.element(), circle(8, 11.3, 0.6, filled=True)])


def bug_icon() -> Icon:
    head = P().M(6.25, 5.75).L(6.25, 5).A(1.75, (9.75, 5), center=(8, 5)).L(9.75, 5.75)
    legs = join(
        head,
        P().M(2.25, 9.25).L(5.25, 9.25), P().M(10.75, 9.25).L(13.75, 9.25),
        P().M(2.75, 12.75).L(5.25, 11.75), P().M(13.25, 12.75).L(10.75, 11.75),
        P().M(2.75, 5.25).L(5.25, 6.5), P().M(13.25, 5.25).L(10.75, 6.5),
    )
    return Icon("BugIcon", [rect(5.25, 5.75, 5.5, 8, 2.75), legs], "x")


def sign_out_icon() -> Icon:
    door = P().M(6.25, 13.75).L(3.5, 13.75).A(1.25, (2.25, 12.5)).L(2.25, 3.5).A(1.25, (3.5, 2.25)).L(6.25, 2.25)
    arrow = join(P().M(10.5, 11).L(13.5, 8).L(10.5, 5), P().M(13.5, 8).L(6, 8))
    return Icon("SignOutIcon", [join(door), arrow])


def venue_icon() -> Icon:
    roof = P().M(8, 2.25).L(13.5, 5.25).L(2.5, 5.25).Z()
    # Four columns spaced evenly about the centre, each on the 2x pixel grid.
    columns = join(*[P().M(x, 7.25).L(x, 11.5) for x in (4.25, 6.75, 9.25, 11.75)], P().M(2.25, 13.75).L(13.75, 13.75))
    return Icon("VenueIcon", [roof.element(), columns], "x", doc="Where a paper appeared.")


def quote_icon() -> Icon:
    # An opening double quote: each mark a filled bowl with a stroked tail that
    # leaves it vertically and curls up and to the right.
    elements: list[Element] = []
    for cx in (4.75, 11.25):
        bowl, bowl_r = (cx, 10.25), 2.25
        elements.append(circle(*bowl, bowl_r, filled=True))
        tail = P().M(cx - bowl_r + HALF, bowl[1]).C((cx - bowl_r + HALF, 7.25), (cx - 1.25, 5.25), (cx + 1.25, 4.25))
        elements.append(tail.element())
    return Icon("QuoteIcon", elements, doc="How often a paper is cited.")


def globe_icon() -> Icon:
    meridian = (
        P().M(8, 2.25).C((9.55, 3.85), (10.35, 5.75), (10.35, 8)).C((10.35, 10.25), (9.55, 12.15), (8, 13.75))
        .C((6.45, 12.15), (5.65, 10.25), (5.65, 8)).C((5.65, 5.75), (6.45, 3.85), (8, 2.25)).Z()
    )
    return Icon("GlobeIcon", [circle(8, 8, CIRCLE_R), join(P().M(2.25, 8).L(13.75, 8), meridian)], "x")


def build_icons() -> list[Icon]:
    return [
        Icon("SidebarIcon", [rect(2.25, 2.75, 11.5, 10.5, 2.25), line((6.25, 2.75), (6.25, 13.25))], "y"),
        Icon("PlusIcon", [join(P().M(8, 3.25).L(8, 12.75), P().M(3.25, 8).L(12.75, 8))], "x"),
        search_icon(),
        Icon("ClockIcon", [circle(8, 8, CIRCLE_R), line((8, 4.75), (8, 8), (10.25, 9.5))]),
        chat_icon(),
        tree_icon(),
        paper_icon(),
        Icon("CloseIcon", [join(P().M(4, 4).L(12, 12), P().M(12, 4).L(4, 12))], "x"),
        Icon("CheckIcon", [line((3.25, 8.5), (6.25, 11.5), (12.75, 4.5))]),
        warning_icon(),
        Icon("ChevronDownIcon", [line((4.5, 6.25), (8, 9.75), (11.5, 6.25))], "x"),
        Icon("ChevronUpDownIcon", [join(P().M(5, 6.25).L(8, 3.25).L(11, 6.25), P().M(5, 9.75).L(8, 12.75).L(11, 9.75))], "x"),
        Icon("ArrowLeftIcon", [join(P().M(12.75, 8).L(3.25, 8), P().M(7.25, 4).L(3.25, 8).L(7.25, 12))], "y"),
        Icon("ArrowRightIcon", [join(P().M(3.25, 8).L(12.75, 8), P().M(8.75, 4).L(12.75, 8).L(8.75, 12))], "y"),
        Icon("SendIcon", [join(P().M(8, 13).L(8, 3), P().M(3.75, 7.25).L(8, 3).L(12.25, 7.25))], "x"),
        Icon("ExternalIcon", [join(P().M(4.75, 11.25).L(11.25, 4.75), P().M(6, 4.75).L(11.25, 4.75).L(11.25, 10))],
             doc="Leaves the app: a link that opens in a new tab."),
        Icon("DownloadIcon", [join(
            P().M(8, 2.75).L(8, 10.25), P().M(4.75, 7).L(8, 10.25).L(11.25, 7), P().M(3, 13.25).L(13, 13.25)
        )], "x"),
        book_icon(),
        trash_icon(),
        pencil_icon(),
        Icon("EllipsisIcon", [circle(x, 8, 1.2, filled=True) for x in (3.5, 8, 12.5)], "x", filled=True),
        gear_icon(),
        key_icon(),
        help_icon(),
        bug_icon(),
        sign_out_icon(),
        Icon("CalendarIcon", [rect(2.25, 3.25, 11.5, 10.5, RADIUS_LARGE), join(
            P().M(2.25, 6.75).L(13.75, 6.75), P().M(5.25, 1.75).L(5.25, 4.75), P().M(10.75, 1.75).L(10.75, 4.75)
        )], "x"),
        venue_icon(),
        quote_icon(),
        globe_icon(),
        Icon("StepIcon", [join(P().M(3, 4.25).L(10, 4.25), P().M(3, 8).L(13, 8), P().M(3, 11.75).L(8.5, 11.75))],
             doc="A stage of work: drafting, checking, auditing."),
    ]


# ----------------------------------------------------------------- checks ---


def check(icon: Icon) -> tuple[list[str], tuple[float, float, float, float]]:
    boxes = [element.extent() for element in icon.elements]
    box = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    problems = []
    if box[0] < LIVE_MIN - 1e-6 or box[1] < LIVE_MIN - 1e-6 or box[2] > LIVE_MAX + 1e-6 or box[3] > LIVE_MAX + 1e-6:
        problems.append("leaves the live area")
    samples = [p for element in icon.elements for p in element.samples]
    if "x" in icon.symmetry:
        problems += _asymmetry(samples, lambda p: (GRID - p[0], p[1]), "x")
    if "y" in icon.symmetry:
        problems += _asymmetry(samples, lambda p: (p[0], GRID - p[1]), "y")
    soft = sorted({
        f"{axis}={fmt(value)}"
        for element in icon.elements if not element.filled
        for axis, value in element.axis_lines
        if abs(value - 8) > 1e-9 and abs((value * 4) % 2 - 1) > 1e-9
    })
    if soft:
        problems.append(f"soft at 16px on a 2x screen: {', '.join(soft)}")
    return problems, box


def _asymmetry(samples: list[Point], reflect, axis: str) -> list[str]:
    worst = max(min(math.dist(reflect(p), s) for s in samples) for p in samples)
    return [f"not symmetric about {axis}=8 (off by {worst:.2f})"] if worst > 0.35 else []


# ----------------------------------------------------------------- output ---


def svg_body(icon: Icon) -> str:
    return "\n".join(
        "<{} {} />".format(element.tag, " ".join(f'{key}="{value}"' for key, value in element.attrs.items()))
        for element in icon.elements
    )


HEADER = '''import type { ReactNode } from "react";

/**
 * The icon set: one 16-unit grid, 1.5-unit strokes with round caps and joins,
 * sized by the caller: `size-4` for controls and rows, `size-3` for marks in
 * small indicators, `size-5` in feature tiles. Generated and checked by
 * `apps/web/scripts/icons.py`; change an icon there, not here. The three zoom
 * icons at the end belong to the canvas and keep the canvas's own drawing.
 */
type IconProps = {
  className?: string;
};

function Icon({ className, children }: IconProps & { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      {children}
    </svg>
  );
}

'''

CANVAS_ICONS = '''export function ZoomOutIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true" className={className}>
      <circle cx="6" cy="6" r="4.2" />
      <path d="M9.3 9.3L12.5 12.5M4 6H8" />
    </svg>
  );
}

export function ZoomInIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true" className={className}>
      <circle cx="6" cy="6" r="4.2" />
      <path d="M9.3 9.3L12.5 12.5M6 4V8M4 6H8" />
    </svg>
  );
}

export function FitIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className}>
      <path d="M1.5 4.5V1.5H4.5M9.5 1.5H12.5V4.5M12.5 9.5V12.5H9.5M4.5 12.5H1.5V9.5" />
    </svg>
  );
}
'''


def icons_tsx(icons: list[Icon]) -> str:
    blocks = []
    for icon in icons:
        body = "\n".join("      " + row for row in svg_body(icon).splitlines())
        doc = f"/** {icon.doc} */\n" if icon.doc else ""
        if icon.filled:
            svg_open = '    <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true" className={className}>'
            svg_close = "    </svg>"
        else:
            svg_open, svg_close = "    <Icon className={className}>", "    </Icon>"
        blocks.append(
            f"{doc}export function {icon.name}({{ className }}: IconProps) {{\n  return (\n"
            f"{svg_open}\n{body}\n{svg_close}\n  );\n}}\n"
        )
    return HEADER + "\n".join(blocks) + "\n" + CANVAS_ICONS


def review_sheet(icons: list[Icon], reports: dict[str, tuple[list[str], tuple]]) -> str:
    grid = "".join(f'<line x1="{i}" y1="0" x2="{i}" y2="16"/><line x1="0" y1="{i}" x2="16" y2="{i}"/>' for i in range(17))
    cells = []
    for icon in icons:
        problems, box = reports[icon.name]
        paint = 'fill="currentColor"' if icon.filled else (
            'fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"'
        )
        svg = f'<svg viewBox="0 0 16 16" {paint}>{svg_body(icon)}</svg>'
        sizes = "".join(f'<span style="width:{s}px;height:{s}px">{svg}</span>' for s in (12, 16, 20))
        cells.append(f"""
      <figure>
        <div class="stage">
          <svg class="grid" viewBox="0 0 16 16"><g>{grid}</g>
            <rect class="live" x="{LIVE_MIN}" y="{LIVE_MIN}" width="{LIVE_MAX - LIVE_MIN}" height="{LIVE_MAX - LIVE_MIN}"/>
            <rect class="box" x="{box[0]}" y="{box[1]}" width="{box[2] - box[0]}" height="{box[3] - box[1]}"/></svg>
          {svg.replace("<svg ", '<svg class="big" ', 1)}
        </div>
        <div class="sizes">{sizes}</div>
        <figcaption>{icon.name}<small>{'; '.join(problems) or 'ok'}</small></figcaption>
      </figure>""")
    return f"""<!doctype html><meta charset="utf-8"><title>Icon review</title>
<style>
body {{ font: 12px Inter, system-ui, sans-serif; margin: 16px; color: #1f2328; background: #fff; }}
main {{ display: grid; grid-template-columns: repeat(auto-fill, 150px); gap: 14px; }}
figure {{ margin: 0; }}
.stage {{ position: relative; width: 128px; height: 128px; }}
.stage svg {{ position: absolute; inset: 0; width: 128px; height: 128px; }}
.grid g line {{ stroke: #e6e9ec; stroke-width: 0.03; }}
.grid .live {{ fill: none; stroke: #f3b5b5; stroke-width: 0.04; stroke-dasharray: 0.2 0.2; }}
.grid .box {{ fill: none; stroke: #7cb7ff; stroke-width: 0.03; }}
.big {{ color: rgb(31 35 40 / 80%); }}
.sizes {{ display: flex; gap: 10px; align-items: center; margin-top: 6px; }}
.sizes span {{ display: inline-block; }}
.sizes svg {{ display: block; width: 100%; height: 100%; }}
figcaption {{ margin-top: 4px; font-weight: 600; }}
figcaption small {{ display: block; font-weight: 400; color: #6b7280; }}
#pixels {{ display: grid; grid-template-columns: repeat(auto-fill, 360px); gap: 12px 18px; }}
</style>
<main>{''.join(cells)}</main>
<h2 style="font-size:13px;margin:28px 0 8px">Rasterized at 16px, 1x and 2x, enlarged</h2>
<section id="pixels"></section>
<script>
for (const figure of document.querySelectorAll("main figure")) {{
  const svg = figure.querySelector(".sizes span:nth-child(2) svg");
  let markup = new XMLSerializer().serializeToString(svg).replaceAll("currentColor", "#1f2328");
  if (!markup.includes("xmlns=")) markup = markup.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ');
  const row = document.createElement("div");
  for (const dpr of [1, 2]) {{
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 16 * dpr;
    canvas.style.cssText = "width:96px;height:96px;margin-right:8px;image-rendering:pixelated;border:1px solid #eef0f2";
    const image = new Image();
    image.onload = () => canvas.getContext("2d").drawImage(image, 0, 0, 16 * dpr, 16 * dpr);
    image.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(markup);
    row.appendChild(canvas);
  }}
  const label = document.createElement("div");
  label.textContent = figure.querySelector("figcaption").firstChild.textContent;
  row.appendChild(label);
  document.getElementById("pixels").appendChild(row);
}}
</script>
"""


def main(argv: list[str]) -> None:
    icons = build_icons()
    assert len({icon.name for icon in icons}) == len(icons), "duplicate icon names"
    reports = {icon.name: check(icon) for icon in icons}
    for icon in icons:
        problems, box = reports[icon.name]
        status = "; ".join(problems) or "ok"
        print(f"{icon.name:18} {box[0]:5.2f},{box[1]:5.2f} → {box[2]:5.2f},{box[3]:5.2f}  {status}")
    if "--sheet" in argv:
        Path(argv[argv.index("--sheet") + 1]).write_text(review_sheet(icons, reports))
    failures = [name for name, (problems, _) in reports.items() if problems]
    if failures:
        raise SystemExit(f"not written: {', '.join(failures)} failed their checks")
    ICONS_TSX.write_text(icons_tsx(icons))


if __name__ == "__main__":
    main(sys.argv[1:])
