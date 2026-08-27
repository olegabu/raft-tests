import csv, math, os, sys

# Cross-FLEET comparison charts, as opposed to mkcharts.py's per-product
# knee charts. Different question, so a different script: mkcharts.py
# asks "how does this product behave as load rises on one fleet",
# whereas these ask "how does the same product behave on different
# hardware", which needs one series per fleet rather than per product.
#
#   python3 sweep/mkcharts-fleets.py [output-dir]
#
# Inputs are the per-fleet CSVs under sequencer/fleets/, in the same
# 10-column shape every sweep here writes (see sweep/README.md).
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FLEETS = os.path.join(ROOT, "sequencer", "fleets")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "sequencer")

SURF, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
BAND = "#f0efec"
# Same first three as mkcharts.py, plus the fourth that cleared its
# all-pairs colour check there; see that file's palette comment.
COLS = ["#2a78d6", "#eb6834", "#1baf7a", "#b5179e"]
FONT = 'system-ui,-apple-system,"Segoe UI",sans-serif'


def load(path, product):
    """rate -> (p50, achieved), averaging repeats like mkcharts.py does."""
    acc = {}
    if not os.path.exists(path):
        return {}
    for row in csv.reader(open(path)):
        if len(row) < 10 or row[0] != product:
            continue
        acc.setdefault(int(row[1]), []).append((int(row[3]), int(row[2])))
    return {k: (sum(a for a, _ in v) // len(v), sum(b for _, b in v) // len(v))
            for k, v in acc.items()}


def head(W, H, title, sub):
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
         f"font-family='{FONT}' role=\"img\" aria-label=\"{title}\">",
         f'<rect width="{W}" height="{H}" fill="{SURF}"/>',
         f'<text x="84" y="30" font-size="16" font-weight="600" fill="{INK}">{title}</text>']
    for i, line in enumerate(sub):
        o.append(f'<text x="84" y="{50 + i * 16}" font-size="12" fill="{INK2}">{line}</text>')
    return o


def chart(path, title, sub, series, xmax, xstep, yrange, yticks, fmt, band_at=None, marker=None,
          band_label=None):
    W, H, L, R = 940, 496, 84, 44
    T = 50 + len(sub) * 16 + 14
    # Deep enough for the x-axis caption plus up to two legend rows.
    B = 104
    PW, PH = W - L - R, H - T - B
    ylo, yhi = math.log10(yrange[0]), math.log10(yrange[1])
    xm = lambda v: L + PW * (v / xmax)
    # No clamping: a post-knee point is meant to leave the top edge
    # (the clipPath below cuts it), not to be pinned to the ceiling —
    # pinning drew a flat line that reads as a plateau the data does
    # not have.
    ym = lambda v: T + PH * (1 - (math.log10(max(v, 1)) - ylo) / (yhi - ylo))
    o = head(W, H, title, sub)
    if band_at:
        yb = ym(band_at)
        o.append(f'<rect x="{L}" y="{yb:.1f}" width="{PW}" height="{T+PH-yb:.1f}" fill="{BAND}"/>')
        o.append(f'<text x="{L+8}" y="{yb+15:.1f}" font-size="11" fill="{MUTED}">at or below {band_label or fmt(band_at)}</text>')
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
    o.append(f'<text x="{L+PW/2:.0f}" y="{T+PH+44}" font-size="11" fill="{INK2}" text-anchor="middle">'
             f'offered rate (requests/sec)</text>')
    if marker:
        mx, mlabel = marker
        kx = xm(mx)
        o.append(f'<line x1="{kx:.1f}" y1="{T}" x2="{kx:.1f}" y2="{T+PH}" stroke="{MUTED}" '
                 f'stroke-width="1" stroke-dasharray="4 4"/>')
        o.append(f'<text x="{kx+6:.1f}" y="{T+16}" font-size="11" font-weight="600" fill="{INK2}">{mlabel}</text>')
    # Clip so a post-knee point leaves the top edge instead of dragging
    # a line across the title (same reasoning as mkcharts.py's).
    o.append(f'<defs><clipPath id="p"><rect x="{L-6}" y="{T-6}" width="{PW+12}" height="{PH+12}"/></clipPath></defs>')
    o.append('<g clip-path="url(#p)">')
    for (label, data), col in zip(series, COLS):
        pts = sorted(k for k in data if k <= xmax)
        if not pts:
            continue
        d = " ".join(("M" if i == 0 else "L") + f"{xm(k):.1f},{ym(data[k][0]):.1f}" for i, k in enumerate(pts))
        o.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="2" stroke-linejoin="round"/>')
        for k in pts:
            p50, ach = data[k]
            sat = ach < 0.99 * k
            o.append(f'<circle cx="{xm(k):.1f}" cy="{ym(p50):.1f}" r="4.5" '
                     f'fill="{SURF if sat else col}" stroke="{col if sat else SURF}" stroke-width="2"/>')
    o.append('</g>')
    # Wraps rather than running off the canvas: these labels carry the
    # instance type AND its silicon, so they are long by necessity.
    lx, ly = L, H - 34
    for (label, _), col in zip(series, COLS):
        w = 28 + len(label) * 6.3
        if lx + w > W - 10:
            lx, ly = L, ly + 17
        o.append(f'<circle cx="{lx+5}" cy="{ly}" r="4.5" fill="{col}"/>')
        o.append(f'<text x="{lx+16}" y="{ly+4}" font-size="11" fill="{INK2}">{label}</text>')
        lx += w
    o.append('</svg>')
    open(path, "w").write("\n".join(o))


us = lambda v: f"{v//1000} ms" if v >= 1000 else f"{v} µs"

# ---------- sequencer's ack path across instance types ----------
chart(
    os.path.join(OUT, "fleet-instance-types.svg"),
    "sequencer ack path across four instance types",
    ["p50 vs offered rate, 3-node multi-AZ, identical code and sweep throughout. Log scale.",
     "No instance type broke the ~1 ms floor. c7i reaches the highest rate, c6in the lowest,",
     "and 16 vCPU is no better than 8 - at the knee the raft leader measured 99.6% idle.",
     "Hollow marker = the cluster fell behind the offered rate."],
    [("c7a.2xlarge (AMD Genoa, 8 vCPU)", load(f"{FLEETS}/ack-c7a-2xlarge.csv", "sequencer")),
     ("c7a.4xlarge (AMD Genoa, 16 vCPU)", load(f"{FLEETS}/ack-c7a-4xlarge.csv", "sequencer")),
     ("c6in.4xlarge (Intel Ice Lake, network-optimised)", load(f"{FLEETS}/ack-c6in-4xlarge.csv", "sequencer")),
     ("c7i.4xlarge (Intel Sapphire Rapids)", load(f"{FLEETS}/ack-c7i-4xlarge.csv", "sequencer"))],
    xmax=210000, xstep=50000, yrange=(600.0, 300000.0),
    yticks=[1000, 2000, 5000, 20000, 100000], fmt=us, band_at=1000)

# ---------- ack against dissemination, from the same runs ----------
paired = {}
for row in csv.DictReader(open(f"{FLEETS}/paired-c7a-2xlarge-brpc.csv")):
    if row["ack_p50"] and row["diss_p50"]:
        paired[int(row["rate"])] = (int(row["ack_p50"]), int(row["diss_p50"]))
ach = load(f"{FLEETS}/paired-c7a-2xlarge-brpc-dissemination.csv", "sequencer-output-brpc")
ack_s = {k: (v[0], ach.get(k, (0, k))[1]) for k, v in paired.items()}
dis_s = {k: (v[1], ach.get(k, (0, k))[1]) for k, v in paired.items()}
chart(
    os.path.join(OUT, "fleet-ack-vs-dissemination.svg"),
    "Ack vs dissemination, measured in the same runs - they cross over near 85k",
    ["c7a.2xlarge (AMD Genoa, 8 vCPU), brpc output gateway. Leader-to-faster-follower RTT 535 us.",
     "Both p50s come from one load-generator run per rate, so this carries no observer-effect confound.",
     "Below ~85k the ack wins: dissemination pays a journal tail plus a hop, and the gateway is unloaded.",
     "Above it the ack pays the loaded gateway twice, inbound and outbound. Both collapse together at 140k."],
    [("synchronous ack", ack_s), ("dissemination (brpc output gateway)", dis_s)],
    xmax=145000, xstep=25000, yrange=(560.0, 4000.0),
    yticks=[600, 800, 1000, 1500, 2000, 3000], fmt=lambda v: f"{v} us",
    band_at=1000, band_label="1 ms", marker=(87000, "crossover"))

# ---------- aeron: the one product that cares which vendor ----------
chart(
    os.path.join(OUT, "fleet-aeron-vendor.svg"),
    "Aeron regressed on AMD and recovers on Intel",
    ["p50 vs offered rate, same Aeron Cluster build and 3-node multi-AZ layout throughout. Log scale.",
     "AMD Genoa steps up at 290k and never recovers; both Intel fleets stay flat past 400k.",
     "Hollow marker = the cluster fell behind the offered rate."],
    [("c6i.2xlarge (Intel Ice Lake, 8 vCPU)", load(f"{ROOT}/sweep/knee-sweep-c6i.csv", "aeron")),
     ("c7a.2xlarge (AMD Genoa, 8 vCPU)", load(f"{ROOT}/aeron/aeron.csv", "aeron")),
     ("c6in.4xlarge (Intel Ice Lake, network-optimised)", load(f"{FLEETS}/aeron-c6in-4xlarge.csv", "aeron")),
     ("c7i.4xlarge (Intel Sapphire Rapids)", load(f"{FLEETS}/aeron-c7i-4xlarge.csv", "aeron"))],
    xmax=720000, xstep=100000, yrange=(420.0, 6000.0),
    yticks=[500, 700, 1000, 2000, 5000], fmt=lambda v: f"{v//1000} ms" if v >= 1000 else f"{v} us")

print(f"wrote fleet-instance-types.svg, fleet-ack-vs-dissemination.svg, fleet-aeron-vendor.svg to {OUT}")
