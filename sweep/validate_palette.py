#!/usr/bin/env python3
"""Colour-vision check for the chart palette, in Python.

Why this exists: the palette comments in mkcharts.py quote figures --
"deltaE 11.8 for normal vision", "4.8 deutan" -- from a Node validator
that is not installed on the machine that draws these charts, so the
seventh series colour went in UNCHECKED with a comment admitting it.
The rule that matters is "the colour part is computable, so compute
it"; which language computes it is not the point. This lives next to
mkcharts.py so the tool that draws the charts can check its own
palette.

Thresholds:
  normal-vision deltaE >= 15   hard floor: below this, readers with
                               full colour vision cannot separate the
                               pair, and no secondary encoding excuses
                               it.
  CVD deltaE      >= 8         target. 6-8 is a floor that is legal
                               ONLY alongside a secondary encoding
                               (these charts direct-label every series,
                               which is one).
Distances are Euclidean in OKLab, x100.

CVD simulation uses the Vienot-Brettel-Mollon (1999) dichromat
matrices, applied in LINEAR light -- the standard construction, and
deterministic, so a result here is reproducible rather than a judgement
call.

Usage:
  validate_palette.py                    check mkcharts.py's own palette
  validate_palette.py '#aabbcc,#ddeeff'  check an explicit set
"""

import itertools
import sys

NORMAL_FLOOR = 15.0
CVD_TARGET = 8.0
CVD_FLOOR = 6.0


def hex_to_linear(h):
    h = h.lstrip("#")
    srgb = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    return [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb]


def linear_to_oklab(rgb):
    r, g, b = rgb
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = [max(v, 0.0) ** (1 / 3) for v in (l, m, s)]
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


# Vienot-Brettel-Mollon dichromat matrices, in linear RGB.
CVD = {
    "protan": ((0.11238, 0.88762, 0.0), (0.11238, 0.88762, 0.0), (0.00401, -0.00401, 1.0)),
    "deutan": ((0.29275, 0.70725, 0.0), (0.29275, 0.70725, 0.0), (-0.02234, 0.02234, 1.0)),
    "tritan": ((1.0, 0.14461, -0.14461), (0.0, 1.0, 0.0), (0.0, 0.15594, 0.84406)),
}


def simulate(rgb, kind):
    m = CVD[kind]
    return [sum(m[i][j] * rgb[j] for j in range(3)) for i in range(3)]


def delta_e(a, b):
    la, lb = linear_to_oklab(a), linear_to_oklab(b)
    return 100.0 * sum((x - y) ** 2 for x, y in zip(la, lb)) ** 0.5


def relative_luminance(rgb):
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def contrast(a, b):
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def check(colours, surface="#fcfcfb"):
    lin = {name: hex_to_linear(h) for name, h in colours}
    worst_fail = 0
    print(f"{len(colours)} colours, all {len(colours)*(len(colours)-1)//2} pairs\n")
    print(f"{'pair':<44}{'normal':>8}{'protan':>8}{'deutan':>8}{'tritan':>8}  verdict")
    for (na, _), (nb, _) in itertools.combinations(colours, 2):
        a, b = lin[na], lin[nb]
        normal = delta_e(a, b)
        sims = {k: delta_e(simulate(a, k), simulate(b, k)) for k in CVD}
        worst_cvd = min(sims.values())
        if normal < NORMAL_FLOOR:
            verdict, sev = "FAIL normal-vision floor", 2
        elif worst_cvd < CVD_FLOOR:
            verdict, sev = "FAIL cvd", 2
        elif worst_cvd < CVD_TARGET:
            verdict, sev = "warn: needs labels", 1
        else:
            verdict, sev = "pass", 0
        worst_fail = max(worst_fail, sev)
        print(f"{na + ' / ' + nb:<44}{normal:>8.1f}{sims['protan']:>8.1f}"
              f"{sims['deutan']:>8.1f}{sims['tritan']:>8.1f}  {verdict}")

    print(f"\ncontrast against surface {surface}")
    surf = hex_to_linear(surface)
    for name, h in colours:
        c = contrast(lin[name], surf)
        flag = "" if c >= 3.0 else "  WARN: needs a visible label"
        print(f"  {name:<28}{h}  {c:>5.2f}:1{flag}")
    return worst_fail


if __name__ == "__main__":
    if len(sys.argv) > 1:
        entries = [(h.strip(), h.strip()) for h in sys.argv[1].split(",")]
    else:
        entries = [
            ("C1 ack/fix-journal", "#2a78d6"),
            ("C2 relay/fix-inline", "#eb6834"),
            ("C3 output-brpc", "#1baf7a"),
            ("C4 direct/output-grpc", "#b5179e"),
            ("C5 output-websocket", "#6d28d9"),
            ("quickfix (7th)", "#a16207"),
            ("INK2 braft floor", "#52514e"),
        ]
    sys.exit(check(entries))
