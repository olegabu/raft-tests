import csv, math, os, sys

# The multi-gateway charts: one input gateway against five, on one fleet.
# Separate from mkcharts.py (per-product knees) and mkcharts-fleets.py
# (same product, different hardware) because the varying quantity here is
# neither the product nor the machine — it is how many client boxes carry
# the offered load, which is a deployment-shape question.
#
#   python3 sweep/mkcharts-multi.py [output-dir]
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SEQ = os.path.join(ROOT, "sequencer")
OUT = sys.argv[1] if len(sys.argv) > 1 else SEQ

SURF, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
BAND = "#f0efec"
# First two of mkcharts.py's palette, which cleared its all-pairs check.
COLS = ["#2a78d6", "#eb6834"]
FONT = 'system-ui,-apple-system,"Segoe UI",sans-serif'


def load(path, product, col):
    """rate -> (value, achieved) for one percentile column."""
    acc = {}
    if not os.path.exists(path):
        return {}
    for row in csv.DictReader(open(path)):
        if row["product"] != product:
            continue
        # A run where every request came back failed leaves the percentile
        # columns empty — there is no latency to plot, and plotting a zero
        # would draw a dive toward the floor that reads as "very fast".
        # Skip the point; the surrounding text says what happened there.
        if not row[col]:
            continue
        acc.setdefault(int(row["rate"]), []).append((int(row[col]), int(row["achieved"])))
    # Average repeats of the same rate, the convention mkcharts.py already
    # uses: 300k was measured twice (once before the journal-index crash
    # described in the README, once after the clean restart) and both are
    # honest measurements of the same point.
    return {k: (sum(a for a, _ in v) // len(v), sum(b for _, b in v) // len(v))
            for k, v in acc.items()}


def chart(path, title, sub, series, xmax, xstep, yrange, yticks, fmt,
          band_at=None, band_label=None, markers=()):
    W, H, L, R = 940, 500, 84, 44
    T = 50 + len(sub) * 16 + 14
    B = 96
    PW, PH = W - L - R, H - T - B
    ylo, yhi = math.log10(yrange[0]), math.log10(yrange[1])
    xm = lambda v: L + PW * (v / xmax)
    ym = lambda v: T + PH * (1 - (math.log10(max(v, 1)) - ylo) / (yhi - ylo))
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
         f"font-family='{FONT}' role=\"img\" aria-label=\"{title}\">",
         f'<rect width="{W}" height="{H}" fill="{SURF}"/>',
         f'<text x="84" y="30" font-size="16" font-weight="600" fill="{INK}">{title}</text>']
    for i, line in enumerate(sub):
        o.append(f'<text x="84" y="{50 + i * 16}" font-size="12" fill="{INK2}">{line}</text>')
    if band_at:
        yb = ym(band_at)
        o.append(f'<rect x="{L}" y="{yb:.1f}" width="{PW}" height="{T+PH-yb:.1f}" fill="{BAND}"/>')
        o.append(f'<text x="{L+8}" y="{yb+15:.1f}" font-size="11" fill="{MUTED}">'
                 f'at or below {band_label or fmt(band_at)}</text>')
    for v in yticks:
        yy = ym(v)
        o.append(f'<line x1="{L}" y1="{yy:.1f}" x2="{L+PW}" y2="{yy:.1f}" stroke="{GRID}"/>')
        o.append(f'<text x="{L-10}" y="{yy+4:.1f}" font-size="11" fill="{MUTED}" text-anchor="end">{fmt(v)}</text>')
    o.append(f'<line x1="{L}" y1="{T+PH}" x2="{L+PW}" y2="{T+PH}" stroke="{AXIS}"/>')
    v = 0
    while v <= xmax:
        xx = xm(v)
        o.append(f'<line x1="{xx:.1f}" y1="{T+PH}" x2="{xx:.1f}" y2="{T+PH+5}" stroke="{AXIS}"/>')
        o.append(f'<text x="{xx:.1f}" y="{T+PH+20}" font-size="11" fill="{MUTED}" text-anchor="middle">{v//1000}k</text>')
        v += xstep
    o.append(f'<text x="{L+PW/2:.0f}" y="{T+PH+42}" font-size="11" fill="{INK2}" text-anchor="middle">'
             f'offered rate across the whole fleet (requests/sec)</text>')
    for mx, mlabel, dy in markers:
        kx = xm(mx)
        o.append(f'<line x1="{kx:.1f}" y1="{T}" x2="{kx:.1f}" y2="{T+PH}" stroke="{MUTED}" '
                 f'stroke-width="1" stroke-dasharray="4 4"/>')
        o.append(f'<text x="{kx+6:.1f}" y="{T+dy}" font-size="11" font-weight="600" fill="{INK2}">{mlabel}</text>')
    o.append(f'<defs><clipPath id="p"><rect x="{L-6}" y="{T-6}" width="{PW+12}" height="{PH+12}"/></clipPath></defs>')
    o.append('<g clip-path="url(#p)">')
    for (label, data), col in zip(series, COLS):
        pts = sorted(k for k in data if k <= xmax)
        if not pts:
            continue
        d = " ".join(("M" if i == 0 else "L") + f"{xm(k):.1f},{ym(data[k][0]):.1f}"
                     for i, k in enumerate(pts))
        o.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="2" stroke-linejoin="round"/>')
        for k in pts:
            val, ach = data[k]
            sat = ach < 0.99 * k
            o.append(f'<circle cx="{xm(k):.1f}" cy="{ym(val):.1f}" r="4.5" '
                     f'fill="{SURF if sat else col}" stroke="{col if sat else SURF}" stroke-width="2"/>')
    o.append('</g>')
    lx, ly = L, H - 30
    for (label, _), col in zip(series, COLS):
        o.append(f'<circle cx="{lx+5}" cy="{ly}" r="4.5" fill="{col}"/>')
        o.append(f'<text x="{lx+16}" y="{ly+4}" font-size="11" fill="{INK2}">{label}</text>')
        lx += 28 + len(label) * 6.3
    o.append('</svg>')
    open(path, "w").write("\n".join(o))


us = lambda v: f"{v//1000} ms" if v >= 1000 else f"{v} µs"
B, M = f"{SEQ}/seq-multi-baseline.csv", f"{SEQ}/seq-multi.csv"
SUB = ["Both arms: one fleet, one leader (pinned to the node the clients share an AZ with),",
       "identical rig settings and the same 100 sender threads in total — only the number of",
       "client boxes and their input gateways differs. Percentiles come from merging every",
       "client's raw histogram, not from averaging theirs. Hollow marker = fell behind the rate."]

chart(f"{OUT}/multi-gateway-p50.svg",
      "Five input gateways move the knee from ~137k to ~360k",
      SUB, [("1 input gateway", load(B, "sequencer-multi1", "p50")),
            ("5 input gateways", load(M, "sequencer-multi", "p50"))],
      xmax=400000, xstep=50000, yrange=(500.0, 60000.0),
      yticks=[600, 800, 1000, 2000, 5000, 20000], fmt=us,
      band_at=1000, band_label="1 ms",
      markers=[(137000, "1-gateway knee", 16), (360000, "5-gateway knee", 32)])

chart(f"{OUT}/multi-gateway-p99.svg",
      "The same split, at p99",
      SUB, [("1 input gateway", load(B, "sequencer-multi1", "p99")),
            ("5 input gateways", load(M, "sequencer-multi", "p99"))],
      xmax=400000, xstep=50000, yrange=(600.0, 200000.0),
      yticks=[1000, 2000, 5000, 20000, 100000], fmt=us,
      markers=[(137000, "1-gateway knee", 16), (360000, "5-gateway knee", 32)])

print(f"wrote multi-gateway-p50.svg, multi-gateway-p99.svg to {OUT}")
