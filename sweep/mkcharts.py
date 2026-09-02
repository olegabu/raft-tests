import csv, math, os, sys

# Renders the knee charts in the repo root from one or more sweep CSVs.
#   python3 sweep/mkcharts.py [sweep.csv ...] [output-dir]
#
# Any number of CSVs may be passed: every row carries its own
# `product` column, so several sweeps' files merge cleanly into one
# dataset. That is what the cross-round-trip comparison chart needs —
# sequencer's five round trips are written by three different sweep
# scripts into three different CSVs (phase 1, phase 3, phase 4's three
# flavors), and only an invocation that sees all of them at once can
# draw them on shared axes. Arguments ending in .csv are inputs; a
# non-.csv argument is the output directory, so the original
# `mkcharts.py <csv> <dir>` form keeps working unchanged.
HERE = os.path.dirname(os.path.abspath(__file__))
_args = sys.argv[1:]
CSVS = [a for a in _args if a.endswith(".csv")] or [os.path.join(HERE, "knee-sweep.csv")]
_dirs = [a for a in _args if not a.endswith(".csv")]
OUT  = _dirs[0] if _dirs else os.path.dirname(HERE)
os.makedirs(OUT, exist_ok=True)

SURF,INK,INK2,MUTED,GRID,AXIS = "#fcfcfb","#0b0b0b","#52514e","#898781","#e1e0d9","#c3c2b7"
BAND = "#f0efec"
C1,C2,C3 = "#2a78d6","#eb6834","#1baf7a"
# Two more categorical slots, for the five-round-trip comparison only.
# Checked as a five-colour set against *every* pair, not just adjacent
# ones — a five-series overlay puts all of them side by side, so
# adjacent-only is the wrong test. The first attempt here paired an
# orange (#c2410c) with C2 and a violet (#8b5cf6) with C1: both failed,
# the oranges at ΔE 11.8 for normal vision (below the 15 floor —
# genuinely hard to tell apart with full colour vision, not just a CVD
# concern) and the violet/blue at ΔE 4.8 deutan. These two clear all
# pairs in all three CVD models.
C4,C5 = "#b5179e","#6d28d9"
FONT = 'system-ui,-apple-system,"Segoe UI",sans-serif'

rows=[r for path in CSVS for r in csv.DictReader(open(path))]
# A rate may appear more than once: repeats are how run-to-run spread gets
# measured near the knee, where a single run says very little. Average them.
_acc={}
for r in rows:
    _acc.setdefault((r["product"], int(r["rate"])), []).append(
        {k: int(v) for k, v in r.items() if k != "product"})
D={}
for (prod, _rate), reps in _acc.items():
    D.setdefault(prod, []).append(
        {k: round(sum(x[k] for x in reps) / len(reps)) for k in reps[0]})
for p in D: D[p].sort(key=lambda d:d["rate"])

# braft's burst-1 arm, averaged over repeats. Plotted on the braft chart because
# it is what explains that chart's left-hand p99 rise: the rise belongs to the
# burst-10 arrival shape, not to braft.
def sat(d): return d["achieved"] < 0.99*d["rate"]

def head(W,H,title,sub):
    o=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
       f"font-family='{FONT}' role=\"img\" aria-label=\"{title}\">",
       f'<rect width="{W}" height="{H}" fill="{SURF}"/>',
       f'<text x="78" y="28" font-size="16" font-weight="600" fill="{INK}">{title}</text>']
    for i,l in enumerate(sub):
        o.append(f'<text x="78" y="{46+i*16}" font-size="12" fill="{INK2}">{l}</text>')
    return o

def axes(o,L,T,PW,PH,xmax,xstep,yticks,ylab,ymap,fmt):
    for v in yticks:
        yy=ymap(v)
        o.append(f'<line x1="{L}" y1="{yy:.1f}" x2="{L+PW}" y2="{yy:.1f}" stroke="{GRID}" stroke-width="1"/>')
        o.append(f'<text x="{L-10}" y="{yy+4:.1f}" font-size="11" fill="{MUTED}" text-anchor="end">{fmt(v)}</text>')
    o.append(f'<text x="20" y="{T+PH/2:.0f}" font-size="11" fill="{INK2}" text-anchor="middle" '
             f'transform="rotate(-90 20 {T+PH/2:.0f})">{ylab}</text>')
    o.append(f'<line x1="{L}" y1="{T+PH}" x2="{L+PW}" y2="{T+PH}" stroke="{AXIS}" stroke-width="1"/>')
    v=0
    while v<=xmax:
        xx=L+PW*(v/xmax)
        o.append(f'<line x1="{xx:.1f}" y1="{T+PH}" x2="{xx:.1f}" y2="{T+PH+5}" stroke="{AXIS}" stroke-width="1"/>')
        o.append(f'<text x="{xx:.1f}" y="{T+PH+20}" font-size="11" fill="{MUTED}" text-anchor="middle">{v//1000}k</text>')
        v+=xstep
    o.append(f'<text x="{L+PW/2:.0f}" y="{T+PH+44}" font-size="11" fill="{INK2}" text-anchor="middle">offered rate (requests/sec)</text>')

def series(o,pts,col,xm,ym,key):
    d=" ".join(("M" if i==0 else "L")+f"{xm(p['rate']):.1f},{ym(p[key]):.1f}" for i,p in enumerate(pts))
    o.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
    for p in pts:
        o.append(f'<circle cx="{xm(p["rate"]):.1f}" cy="{ym(p[key]):.1f}" r="4.5" '
                 f'fill="{SURF if sat(p) else col}" stroke="{col if sat(p) else SURF}" stroke-width="2"/>')

def legend(o,L,H,items):
    lx=L
    for lab,col in items:
        o.append(f'<circle cx="{lx+5}" cy="{H-16}" r="4.5" fill="{col}"/>')
        o.append(f'<text x="{lx+16}" y="{H-12}" font-size="11" fill="{INK2}">{lab}</text>')
        lx+=26+len(lab)*6.6

# ---------- combined ----------
W,H,L,R,T,B=900,500,78,150,78,74
PW,PH=W-L-R,H-T-B
XMAX,YMIN,YMAX=650000,420.0,16000.0
xm=lambda v: L+PW*(v/XMAX)
ym=lambda v: T+PH*(1-(math.log10(v)-math.log10(YMIN))/(math.log10(YMAX)-math.log10(YMIN)))
o=head(W,H,"Flat, then a knee: three raft implementations under open-loop load",
  ["p50 latency vs offered rate, 3-node multi-AZ cluster on EC2. Log latency scale.",
   "Hollow marker = the cluster no longer kept up with the offered rate."])
axes(o,L,T,PW,PH,XMAX,100000,[500,1000,2000,5000,10000],"p50 latency (log scale)",ym,
     lambda v: f"{v//1000} ms" if v>=1000 else f"{v} µs")
# Where each series gets its direct label. aeron and braft label their own
# line-end, to the right, same as always. openraft's line-end now sits inside
# braft's steep climb (braft crosses above it around 145-150k), so a
# right-of-last-point label would float on top of braft's line -- moved to an
# early point on openraft's own curve instead, labelled to its *left*, where
# only openraft is present (braft is a clear ~45-65px below it through this
# whole range) and there is enough clearance from the y-axis to avoid the
# tick labels.
# braft and openraft are labelled at a chosen point rather than their
# line-end: both collapse past their knee to values well above this
# chart's YMAX, so a label at the last point would be drawn off-canvas
# entirely (braft's last point is 85 ms against a 16 ms ceiling).
# aeron's line-end is still on-scale, so it keeps the simple form.
LABEL_AT = {"aeron": ("last", 12, 4), "braft": (250000, 12, -8), "openraft": (85000, -12, 4)}
# Guarded, not D[name] unconditionally: a sweep CSV for a new product
# (raft-tests/sequencer/README.md's own, for one) legitimately has
# none of these three -- this combined chart is specifically the
# three-way comparison, not "every product present," and a KeyError
# here would block that new product's own per-product chart below
# from ever being reached at all.
present = [(name,col) for name,col in (("aeron",C1),("braft",C2),("openraft",C3)) if name in D]
for name,col in present:
    series(o,D[name],col,xm,ym,"p50")
    at,dx,dy = LABEL_AT[name]
    pt = D[name][-1] if at=="last" else next(p for p in D[name] if p["rate"]==at)
    anchor = ' text-anchor="end"' if dx<0 else ""
    o.append(f'<text x="{xm(pt["rate"])+dx:.1f}" y="{ym(pt["p50"])+dy:.1f}" font-size="12"{anchor} '
             f'font-weight="600" fill="{INK}">{name}</text>')
legend(o,L,H,present)
o.append('</svg>')
if present:
    open(f"{OUT}/knee-curves.svg","w").write("\n".join(o))

# ---------- per product: p50 + p99, linear y, comfort band + knee rule ----------
CFG={
 # Re-swept on the c7a fleet (AMD Genoa, ~3.7GHz). The knee moved a long
 # way on faster cores: flat to 250k here, against ~160-165k on the
 # earlier c6i fleet, so the axes had to grow with it. Do not compare
 # these absolute values against this README's older braft prose —
 # different hardware, see sweep/README.md's own fleet-provenance note.
 "braft":    (320000,50000,(400,1000000),[500,700,1000,2000,5000,20000,100000,500000], 250000,
              [(265000,"knee",16)],
              "braft — flat to 250k on c7a, then a cliff",
              "p50 641us at 10k to 1291us at 250k; 280k collapses. Earlier c6i fleet turned at ~160k."),
 # Re-swept on c7a. Same shape as before — a slope, not a plateau with a
 # cliff — but the whole curve dropped: 1159us at 100k here against
 # ~2.7ms on the c6i fleet, and it now reaches 140k still climbing
 # gently rather than capping out around 128k.
 "openraft": (150000,25000,(700, 9000),[800,1000,1500,2000,3000,5000,8000], 100000,
              [(110000,"drops climb",16)],
              "openraft — still no cliff, but a much lower slope on c7a",
              "Latency never cliffs; the dashed line is where the rig's own drops start climbing."),
 # Re-swept on c7a. The comfort-zone latency is essentially unchanged
 # (495-559us, against ~537 on c6i) but the ceiling moved *down*, from
 # ~400k to ~250k — the one product that did not improve on faster
 # cores. See README.md's aeron section for what is and isn't
 # established about why.
 "aeron":    (650000,100000,(450, 4000),[500,600,800,1000,1500,2000,3000], 250000,
              [(270000,"knee",16)],
              "aeron — still the flattest curve, but the ceiling moved down on c7a",
              "A 10x change in offered rate, 25k to 250k, moves p50 by 16 µs; 290k steps to 2.1 ms."),
 # raft-tests/sequencer/README.md's phase 1 (submission to synchronous
 # receipt) — client -> input gateway -> node -> node -> input gateway
 # -> client, one hop longer than bare braft on purpose. Not a gradual
 # turn like braft's own: achieved plateaus hard at ~123-126k regardless
 # of how much more is offered from 130k on, and p50 jumps from 2.5ms at
 # 115k straight past 1 full *second* at 130k -- a real system-level
 # stall, not the rig (dropped-by-rig stays 0 at every point except
 # 145k, where it is 711009 -- the input gateway's own outstanding-call
 # limit finally being hit, downstream of the stall, not the cause of
 # it). Whether this exact knee position also reflects sequencer's
 # default --burst=1 dispatch cost (braft's own README: an earlier
 # revision mistook exactly this for aeron's real knee before a
 # BURST=10 comparison run showed otherwise) is not yet established
 # here -- worth a repeat sweep with BURST set before trusting 115-130k
 # as sequencer's true ceiling rather than partly this rig's own.
 # Re-measured after the input gateway learned to batch proposals
 # (sequencer's gateway/input/README.md): the knee this entry used to
 # describe at ~115k was the gateway's own, not the raft group's.
 "sequencer": (170000,25000,(400,3000000),[500,1000,2000,5000,50000,500000,2000000], 130000,
              [(137000,"knee",16)],
              "sequencer — flat to ~130k once the gateway batches proposals",
              "p50 572us at 10k to 1430us at 130k; 145k is the first rate that breaks."),
 # Three rounds of fixes, all in the relay gateway's own gRPC
 # Subscribe implementation (gateway/relay/src/relay_grpc_service_impl.hpp)
 # -- not this rig, not sequencer's consensus/ack path. (1) One
 # journal record per Write() call, no batching: at counter-record
 # sizes (~40-50 bytes on the wire) a blocking synchronous Write()'s
 # own fixed per-call overhead dominated completely, producing a
 # severe cliff around 25k-40k. Fixed by gathering every record
 # already available (up to --relay_max_batch_records, default 1024)
 # into one RecordBatch per Write(). (2) That fix's own gather loop
 # introduced a second, accidental bug: re-checking
 # context->IsCancelled() on every record instead of once per batch --
 # IsCancelled() plucks gRPC's completion queue under the hood, ~3000x
 # slower per record than per batch. (3) Rewrote Subscribe onto gRPC's
 # callback/reactor API (grpc::ServerWriteReactor) in pursuit of
 # sub-millisecond p50 at 100k: this dropped p50 to 649us-1.3ms
 # through 100k (actually *beating* phase 1's own synchronous-ack p50
 # at the same rates) but did not reach <1ms exactly at 100k, and
 # moved the throughput cliff *down* from 130k to 115k -- a real
 # trade-off, not a strict win, and not (per a --relay_max_batch_records
 # sweep at 128/1024/8192) primarily a batch-size effect: p50 barely
 # moves across that range at 100k. See gateway/relay/README.md's
 # "Batching the gRPC stream" section for the full writeup, including
 # the chained-OnWriteDone variant that was tried and reverted after
 # measuring *worse* p50 at 100k despite removing a cross-thread
 # wake/schedule round trip that seemed like it should only help --
 # the real cause of the 115k ceiling is still open.
 "sequencer-relay": (200000,25000,(500,20000000),
                     [1000,5000,50000,200000,1000000,5000000,20000000], 100000,
                     [(112000,"ceiling",16)],
                     "sequencer-relay — sub-2ms through 100k, but a lower ceiling than the old design",
                     "p50 649us-1.3ms from 10k to 100k, beating phase 1's own ack latency; cliff moved from 130k to 115k."),
 # The control arm for the input gateway's cost: same rig, same rates,
 # but calling a node's ProposeService directly. Not a deployable
 # configuration (specification.md §3.3 has clients submit through a
 # gateway) — it exists to price that hop.
 # No knee inside the swept range — hence no dashed rule and a comfort
 # band running the whole width. Axes sized to the data (p99 tops out
 # near 8.5ms) rather than to a collapse that never happens.
 "sequencer-direct": (260000,50000,(400,12000),
                     [500,700,1000,2000,3000,5000,10000], 250000,
                     [],
                     "sequencer-direct — submitting straight to the node, no input gateway",
                     "p50 508us at 10k to 727us at 100k, still 1.6ms at 250k; the gateway arm knees at ~55-70k."),
 # Phase 4, one entry per transport flavor (sequencer's
 # raft-tests/sequencer/sweep-output.sh writes product=
 # sequencer-output-<flavor>). All three were rebuilt on the
 # per-subscriber BroadcastRing design — see sequencer's own
 # gateway/output/README.md — which is what moved them from ~4.2-4.9ms
 # p50 at 100k to ~890us, i.e. onto the relay's own curve rather than
 # 3.5x above it. Same axes for all three on purpose: the whole point
 # of charting them separately is that they are now hard to tell
 # apart, and shared axes are what makes that visible.
 "sequencer-output-brpc": (260000,50000,(500,200000),
                     [500,1000,2000,5000,10000,50000,200000], 150000,
                     [(245000,"braft plateau",16)],
                     "sequencer-output-brpc — brpc Streaming RPC, sub-1ms to 150k",
                     "Five client boxes, one gateway, per-client topics: fan-out is one. No knee before consensus itself plateaus."),
 "sequencer-output-grpc": (260000,50000,(500,200000),
                     [500,1000,2000,5000,10000,50000,200000], 150000,
                     [(245000,"braft plateau",16)],
                     "sequencer-output-grpc — real gRPC streaming, sub-1ms to 150k",
                     "The synchronous gRPC API's thread-per-Subscribe is the per-subscriber reader here."),
 # FIX carries no knee marker because the sweep never found one: it
 # absorbed every rate to 250k (248,959 of 250,000, p50 1949us) with the
 # rig's own schedule lag still at 1us. Drawing a dashed "knee" here
 # would invent an inflection the data does not contain.
 #
 # Its comfort zone ends far earlier than the output flavors' despite
 # scaling further, and that is the honest shape of the trade: every
 # output reaches a FIX client by the journal (specification.md 8.11),
 # so the median carries a commit-then-read cycle the output gateways'
 # subscribers do not pay.
 "sequencer-fix": (260000,50000,(500,200000),
                     [500,1000,2000,5000,10000,50000,200000], 25000,
                     [(245000,"braft plateau",16)],
                     "sequencer-fix - FIX 4.4 order entry, delivered from the journal",
                     "ONE gateway serving five sessions, doing both directions. Carries 250k at 2.3ms; sub-1ms only to 25k."),
 # The same gateway as sequencer-fix, answering from the propose
 # receipt instead of the journal (--inline_designated_outputs). Charted
 # as its own curve because it is a different round trip, not a tuning
 # of the same one.
 "sequencer-fix-inline": (260000,50000,(500,200000),
                     [500,1000,2000,5000,10000,50000,200000], 75000,
                     [(245000,"braft plateau",16)],
                     "sequencer-fix-inline - FIX 4.4, answered from the propose receipt",
                     "Faster to ~125k (1036us vs 1127us at 100k), slower above it: one send per reply, where the journal path coalesces."),
 "sequencer-output-websocket": (260000,50000,(500,200000),
                     [500,1000,2000,5000,10000,50000,200000], 150000,
                     [(245000,"braft plateau",16)],
                     "sequencer-output-websocket — Boost.Beast WebSocket, sub-1ms to 150k",
                     "Each connection's stream is owned outright by its writer thread, not posted to a shared io thread."),
}
for name,(xmax,xstep,yrange,yticks,comfort,markers,title,line2) in CFG.items():
    if name not in D:
        # CFG lists every product this script *knows how to* chart;
        # a given CSV (raft-tests/sequencer/README.md's own sweeps,
        # for instance) legitimately has only one or two of them.
        continue
    W,H,L,R,T,B=820,440,78,120,86,74
    PW,PH=W-L-R,H-T-B
    ylo,yhi=math.log10(yrange[0]),math.log10(yrange[1])
    xm=lambda v,L=L,PW=PW,xmax=xmax: L+PW*(v/xmax)
    ym=lambda v,T=T,PH=PH,ylo=ylo,yhi=yhi: T+PH*(1-(math.log10(v)-ylo)/(yhi-ylo))
    o=head(W,H,title,[line2,
       "p50 and p99 latency, open loop. Hollow marker = the cluster fell behind the offered rate."])
    o.append(f'<rect x="{L}" y="{T}" width="{xm(comfort)-L:.1f}" height="{PH}" fill="{BAND}"/>')
    o.append(f'<text x="{L+8}" y="{T+16}" font-size="11" fill="{MUTED}">comfort zone</text>')
    # One dashed rule per inflection. braft has two -- the tail turns well before
    # p50 does -- so their labels are staggered vertically to stay apart.
    for mrate,mlabel,mdy in markers:
        kx=xm(mrate)
        o.append(f'<line x1="{kx:.1f}" y1="{T}" x2="{kx:.1f}" y2="{T+PH}" stroke="{MUTED}" '
                 f'stroke-width="1" stroke-dasharray="4 4"/>')
        o.append(f'<text x="{kx+6:.1f}" y="{T+mdy}" font-size="11" font-weight="600" '
                 f'fill="{INK2}">{mlabel}</text>')
    axes(o,L,T,PW,PH,xmax,xstep,yticks,"latency (µs), log scale",ym,lambda v: f"{v:,}")
    # Clip to the plot area. Past a product's knee, and at any rate
    # where a repeated run happened to be rig-limited (repeats are
    # averaged — see sweep/README.md), a point can land far above the
    # range the rest of the curve needs; without clipping it drags a
    # line across the title instead of just leaving the top edge.
    o.append(f'<defs><clipPath id="plot"><rect x="{L-6}" y="{T-6}" width="{PW+12}" height="{PH+12}"/></clipPath></defs>')
    o.append('<g clip-path="url(#plot)">')
    for key,col in (("p99",C2),("p50",C1)):
        series(o,D[name],col,xm,ym,key)
    o.append('</g>')
    for key,col in (("p99",C2),("p50",C1)):
        last=D[name][-1]
        ylab_pos=min(max(ym(last[key]), T), T+PH)
        o.append(f'<text x="{xm(last["rate"])+12:.1f}" y="{ylab_pos+4:.1f}" font-size="12" '
                 f'font-weight="600" fill="{INK}">{key}</text>')
    legend(o,L,H,[("p50",C1),("p99",C2)])
    o.append('</svg>')
    open(f"{OUT}/knee-{name}.svg","w").write("\n".join(o))

# ---------- sequencer's five round trips: small multiples, p50 + p99 ----------
# One panel per round trip rather than ten lines on shared axes: five
# products x two percentiles is past the point where a single overlay
# stays readable, and the interesting comparison here is panel-to-panel
# shape, which identical axes make direct. The p50-only overlay below
# is the other half — it answers "do they land on top of each other?",
# which is exactly the question the small multiples make hard to eyeball.
RT = [("sequencer",                   "synchronous ack", C1),
      ("sequencer-direct",            "direct to node",  C4),
      ("sequencer-relay",             "relay (gRPC)",    C2),
      ("sequencer-output-brpc",       "output: brpc",    C3),
      ("sequencer-output-grpc",       "output: gRPC",    C4),
      ("sequencer-output-websocket",  "output: WebSocket", C5),
      # C1, not a new hue: C3 is already output-brpc, and two series
      # sharing a colour in one overlay is a colour-identity error, not
      # a cosmetic one. The palette above has five slots checked
      # all-pairs across three CVD models; a chart carrying all seven
      # round trips at once would need a validated sixth, which is a
      # deliberate exercise (run the palette validator) rather than a
      # guess. C1 is free whenever the ack path is not in the same CSV,
      # which is the case for the gateway-comparison sweep.
      ("sequencer-fix",               "FIX 4.4 (journal)", C1),
      ("sequencer-fix-inline",        "FIX 4.4 (inline)",  C2)]
rt_present = [(n,lab,c) for n,lab,c in RT if n in D]
if len(rt_present) >= 2:
    # Rows follow from how many round trips the CSVs actually carry.
    # This was 3x2 for the six the repo had; a seventh (FIX) would have
    # drawn off the bottom of the canvas.
    COLS = 3
    ROWS = max(1, -(-len(rt_present) // COLS))
    PANW,PANH = 268,196
    GX,GY = 26,58
    ML,MT = 62,124  # MT leaves room for a four-line subtitle above the first panel row
    W = ML + COLS*PANW + (COLS-1)*GX + 24
    # Bottom margin has to clear the last row's tick labels before the
    # x-axis caption; with 5 panels in a 3x2 grid the caption would
    # otherwise land inside panel 5 rather than under the grid.
    H = MT + ROWS*PANH + (ROWS-1)*GY + 86
    # Follows the data. Hardcoding 130k cut every curve off at exactly
    # the rate the comparison exists to show: with FIX absorbing to 250k
    # while the output flavors collapse at 145k, a fixed 130k axis hid
    # the entire difference between them.
    _maxrate = max((q["rate"] for _n,_l,_c in rt_present for q in D[_n]), default=130000)
    XMAX = int(math.ceil(_maxrate / 25000.0) * 25000)
    XSTEP = 50000 if XMAX > 150000 else 25000
    YR = (500.0, 200000.0)
    ylo,yhi = math.log10(YR[0]), math.log10(YR[1])
    _n = {2:"two",3:"three",4:"four",5:"five",6:"six"}.get(len(rt_present), str(len(rt_present)))
    o = head(W,H,f"sequencer: {_n} round trips, p50 and p99",
      ["Every hop this repo measures, same fleet and same sweep. Identical axes across panels; log latency scale.",
       "Hollow marker = the cluster fell behind the offered rate. Shaded band = at or below 1 ms.",
       "Past the knee, latency runs to whole seconds; those points clip at the panel top.",
       # Only true when that arm is actually in the CSVs being charted.
       *(["The direct-to-node arm was swept further than these shared axes show \u2014 see its own chart."]
         if "sequencer-direct" in D else [])])
    # Each panel clips its own series: past the knee p50 reaches
    # ~1-2 seconds, and stretching the axis to contain that would
    # squeeze the 500us-2ms range this chart exists to show into a
    # few pixels. Clipping keeps the resolution where the story is;
    # the near-vertical climb into the clip is itself the signal.
    o.append('<defs>')
    for i in range(len(rt_present)):
        r,c = divmod(i, COLS)
        L = ML + c*(PANW+GX)
        T = MT + r*(PANH+GY)
        o.append(f'<clipPath id="pan{i}"><rect x="{L-6}" y="{T-6}" width="{PANW+12}" height="{PANH+12}"/></clipPath>')
    o.append('</defs>')
    for i,(name,label,_col) in enumerate(rt_present):
        r,c = divmod(i, COLS)
        L = ML + c*(PANW+GX)
        T = MT + r*(PANH+GY)
        xm = lambda v,L=L: L+PANW*(v/XMAX)
        ym = lambda v,T=T: T+PANH*(1-(math.log10(v)-ylo)/(yhi-ylo))
        # The sub-millisecond band is the whole point of this round of
        # work, so it is drawn rather than left to the reader's eye.
        y1ms = ym(1000)
        o.append(f'<rect x="{L}" y="{y1ms:.1f}" width="{PANW}" height="{T+PANH-y1ms:.1f}" fill="{BAND}"/>')
        for v in [1000,10000,100000]:
            yy=ym(v)
            o.append(f'<line x1="{L}" y1="{yy:.1f}" x2="{L+PANW}" y2="{yy:.1f}" stroke="{GRID}" stroke-width="1"/>')
            if c==0:
                lab = f"{v//1000} ms" if v>=1000 else f"{v} µs"
                o.append(f'<text x="{L-8}" y="{yy+4:.1f}" font-size="10" fill="{MUTED}" text-anchor="end">{lab}</text>')
        o.append(f'<line x1="{L}" y1="{T+PANH}" x2="{L+PANW}" y2="{T+PANH}" stroke="{AXIS}" stroke-width="1"/>')
        v=0
        while v<=XMAX:
            xx=xm(v)
            o.append(f'<line x1="{xx:.1f}" y1="{T+PANH}" x2="{xx:.1f}" y2="{T+PANH+4}" stroke="{AXIS}" stroke-width="1"/>')
            o.append(f'<text x="{xx:.1f}" y="{T+PANH+17}" font-size="10" fill="{MUTED}" text-anchor="middle">{v//1000}k</text>')
            v+=XSTEP
        o.append(f'<text x="{L}" y="{T-8}" font-size="12" font-weight="600" fill="{INK}">{label}</text>')
        o.append(f'<g clip-path="url(#pan{i})">')
        for key,col in (("p99",C2),("p50",C1)):
            pts=[p for p in D[name] if p[key]>0]
            if pts:
                series(o,pts,col,xm,ym,key)
        o.append('</g>')
    _ymid = MT + (ROWS*PANH + (ROWS-1)*GY)/2
    o.append(f'<text x="20" y="{_ymid:.0f}" font-size="11" fill="{INK2}" text-anchor="middle" '
             f'transform="rotate(-90 20 {_ymid:.0f})">latency, log scale</text>')
    o.append(f'<text x="{ML+ (COLS*PANW+(COLS-1)*GX)/2:.0f}" y="{H-46}" font-size="11" fill="{INK2}" '
             f'text-anchor="middle">offered rate (requests/sec)</text>')
    legend(o,ML,H,[("p50",C1),("p99",C2)])
    o.append('</svg>')
    open(f"{OUT}/round-trips.svg","w").write("\n".join(o))

    # ---------- the same five, p50 only, overlaid ----------
    W,H,L,R,T,B = 900,470,78,40,96,74
    PW,PH = W-L-R,H-T-B
    # Same reason as the panels above: this axis was pinned at 130k, so
    # every curve stopped exactly where the comparison gets interesting.
    XMAX = int(math.ceil(_maxrate / 25000.0) * 25000)
    ylo,yhi = math.log10(600.0), math.log10(200000.0)
    xm = lambda v: L+PW*(v/XMAX)
    ym = lambda v: T+PH*(1-(math.log10(v)-ylo)/(yhi-ylo))
    # Built from the data, not typed in. This line used to be a literal
    # carried from an older sweep, so it kept asserting relay/ack
    # numbers even when neither product was in the CSVs being charted.
    _at100k = []
    for _n,_lab,_c in rt_present:
        _pt = next((q for q in D[_n] if q["rate"] == 100000 and q["p50"] > 0), None)
        if _pt:
            _at100k.append(f"{_lab} {_pt['p50']:,} µs")
    _sub = ("At 100k: " + "; ".join(_at100k) + "." if _at100k
            else "No 100k point in this dataset.")
    o = head(W,H,"sequencer: gateway round trips, p50 vs offered rate",
      ["p50 latency vs offered rate, same fleet and sweep as the panels above. Log latency scale.",
       _sub,
       "Hollow marker = the cluster fell behind the offered rate. Shaded band = at or below 1 ms."])
    y1ms = ym(1000)
    o.append(f'<rect x="{L}" y="{y1ms:.1f}" width="{PW}" height="{T+PH-y1ms:.1f}" fill="{BAND}"/>')
    o.append(f'<text x="{L+8}" y="{y1ms+15:.1f}" font-size="11" fill="{MUTED}">at or below 1 ms</text>')
    axes(o,L,T,PW,PH,XMAX,XSTEP,[1000,2000,5000,10000,50000,200000],"p50 latency, log scale",ym,
         lambda v: f"{v//1000} ms" if v>=1000 else f"{v} µs")
    # Legend only, no direct line-end labels: every series collapses
    # past its knee, so all five line-ends pile into the top-right
    # corner where labels would overlap each other rather than
    # identify anything. (The small-multiples chart above is where
    # per-series identity is unambiguous by construction.)
    for _i,(name,_label,col) in enumerate(rt_present):
        pts=[p for p in D[name] if p["p50"]>0]
        if not pts: continue
        series(o,pts,col,xm,ym,"p50")
    legend(o,L,H,[(lab,col) for _n,lab,col in rt_present])
    o.append('</svg>')
    open(f"{OUT}/round-trips-p50.svg","w").write("\n".join(o))

_written = [n for n in CFG if n in D]
_combined = "knee-curves.svg and " if present else ""
_rt = f", round-trips.svg + round-trips-p50.svg ({len(rt_present)} round trips)" if len(rt_present)>=2 else ""
print(f"wrote {_combined}{len(_written)} per-product chart(s) ({', '.join(_written)}){_rt} to {OUT}")
