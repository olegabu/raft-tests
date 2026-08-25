import csv, math, os, sys

# Renders the four knee charts in the repo root from a sweep CSV.
#   python3 sweep/mkcharts.py [sweep/knee-sweep.csv] [output-dir]
HERE = os.path.dirname(os.path.abspath(__file__))
CSV  = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "knee-sweep.csv")
OUT  = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(HERE)
os.makedirs(OUT, exist_ok=True)

SURF,INK,INK2,MUTED,GRID,AXIS = "#fcfcfb","#0b0b0b","#52514e","#898781","#e1e0d9","#c3c2b7"
BAND = "#f0efec"
C1,C2,C3 = "#2a78d6","#eb6834","#1baf7a"
FONT = 'system-ui,-apple-system,"Segoe UI",sans-serif'

rows=[r for r in csv.DictReader(open(CSV))]
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
LABEL_AT = {"aeron": ("last", 12, 4), "braft": ("last", 12, -6), "openraft": (85000, -12, 4)}
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
 "braft":    (210000,25000,(400,80000),[500,700,1000,2000,3000,5000,10000,20000,50000], 160000,
              [(165000,"knee",16)],
              "braft — flat to 160k, then both percentiles go together",
              "p99 holds within 1.2-2.4x of p50 from 10k to 150k, then both turn together."),
 "openraft": (150000,25000,(800, 7000),[1000,1500,2000,3000,5000], 85000,
              [(110000,"knee",16)],
              "openraft — no flat stretch; latency climbs from the first step",
              "Latency never cliffs here; the dashed line is where drops take off instead."),
 "aeron":    (650000,100000,(450, 1500),[500,600,700,800,1000,1200], 400000,
              [(460000,"knee",16)],
              "aeron — flat to 400k, then one step up",
              "A 16x change in offered rate, 25k to 400k, moves p50 by 68 µs."),
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
 "sequencer": (200000,25000,(400,2000000),[500,1000,2000,5000,10000,50000,200000,1000000], 115000,
              [(122000,"knee",16)],
              "sequencer — flat to ~115k, then a severe stall",
              "achieved plateaus at ~123-126k past the knee regardless of offered rate; p50 crosses 1s by 130k."),
 # CAUTION, read before trusting this one: relay_dropped_races stayed
 # at 1 throughout the sweep this came from (RelayObserver's own
 # wait-for-a-race mechanism basically never fired), yet p90 explodes
 # 150x between 25k and 40k while p50 barely moves -- the signature of
 # a single consumer thread's own raw processing throughput falling
 # behind a stream, backlog compounding over the run (later records
 # look worse than earlier ones), not genuine per-record dissemination
 # lag. Above 70k RelayObserver correlated *zero* records at all for
 # the whole run -- not "instant," no data, silently truncated out of
 # this chart's own CSV (raft-tests/sequencer/ has both the full sweep
 # and the truncated, chartable rows — see its README). Read this as
 # "RelayObserver's own current single-threaded design tops out
 # somewhere under 40k," not "sequencer's relay gateway does" -- those
 # are different claims and only the sweep above ~85k (where the core
 # pipeline, not this observer, is what's saturating) can tell the two
 # apart. Worth fixing (move correlation off the gRPC read loop, onto
 # its own worker) and re-sweeping before reporting this further.
 "sequencer-relay": (80000,10000,(2000,40000000),
                     [5000,10000,50000,200000,1000000,5000000,20000000], 25000,
                     [(30000,"observer's own ceiling",16)],
                     "sequencer-relay — likely RelayObserver's own limit, not sequencer's",
                     "p90 explodes 150x from 25k to 40k while p50 barely moves; above 70k, zero correlated records at all."),
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
    for key,col in (("p99",C2),("p50",C1)):
        series(o,D[name],col,xm,ym,key)
        last=D[name][-1]
        o.append(f'<text x="{xm(last["rate"])+12:.1f}" y="{ym(last[key])+4:.1f}" font-size="12" '
                 f'font-weight="600" fill="{INK}">{key}</text>')
    legend(o,L,H,[("p50",C1),("p99",C2)])
    o.append('</svg>')
    open(f"{OUT}/knee-{name}.svg","w").write("\n".join(o))
_written = [n for n in CFG if n in D]
_combined = "knee-curves.svg and " if present else ""
print(f"wrote {_combined}{len(_written)} per-product chart(s) ({', '.join(_written)}) to {OUT}")
