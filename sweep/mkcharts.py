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
D={}
for r in rows:
    D.setdefault(r["product"],[]).append({k:int(v) for k,v in r.items() if k!="product"})
for p in D: D[p].sort(key=lambda d:d["rate"])

# braft's burst-1 arm, averaged over repeats. Plotted on the braft chart because
# it is what explains that chart's left-hand p99 rise: the rise belongs to the
# burst-10 arrival shape, not to braft.
BURST1 = []
_bp = os.path.join(HERE, "braft-burst.csv")
if os.path.exists(_bp):
    acc = {}
    for r in csv.DictReader(open(_bp)):
        if int(r["burst"]) == 1 and int(r["warmup"]) == 10:
            acc.setdefault(int(r["rate"]), []).append(int(r["p99"]))
    BURST1 = [{"rate": k, "p99": round(sum(v)/len(v)), "achieved": k} for k, v in sorted(acc.items())]
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
DY={"aeron":4,"braft":-6,"openraft":18}
for name,col in (("aeron",C1),("braft",C2),("openraft",C3)):
    series(o,D[name],col,xm,ym,"p50")
    last=D[name][-1]
    o.append(f'<text x="{xm(last["rate"])+12:.1f}" y="{ym(last["p50"])+DY[name]:.1f}" font-size="12" '
             f'font-weight="600" fill="{INK}">{name}</text>')
legend(o,L,H,[("aeron",C1),("braft",C2),("openraft",C3)])
o.append('</svg>')
open(f"{OUT}/knee-curves.svg","w").write("\n".join(o))

# ---------- per product: p50 + p99, linear y, comfort band + knee rule ----------
CFG={
 "braft":    (200000,25000,(600,15000),[700,1000,2000,3000,5000,10000], 70000, 90000, "knee (p50)",
              "braft — the tail breaks first, at 70k",
              "p99 leaves p50 at 70k and is 10x its floor by 100k. Its rise below 35k is the arrival shape, not braft."),
 "openraft": (150000,25000,(800, 7000),[1000,1500,2000,3000,5000], 85000,110000, "knee",
              "openraft — no flat stretch; latency climbs from the first step",
              "Latency never cliffs here; the dashed line is where drops take off instead."),
 "aeron":    (650000,100000,(450, 1500),[500,600,700,800,1000,1200], 400000,460000, "knee",
              "aeron — flat to 400k, then one step up",
              "A 16x change in offered rate, 25k to 400k, moves p50 by 68 µs."),
}
for name,(xmax,xstep,yrange,yticks,comfort,knee,kneelabel,title,line2) in CFG.items():
    W,H,L,R,T,B=820,440,78,120,86,74
    PW,PH=W-L-R,H-T-B
    ylo,yhi=math.log10(yrange[0]),math.log10(yrange[1])
    xm=lambda v,L=L,PW=PW,xmax=xmax: L+PW*(v/xmax)
    ym=lambda v,T=T,PH=PH,ylo=ylo,yhi=yhi: T+PH*(1-(math.log10(v)-ylo)/(yhi-ylo))
    o=head(W,H,title,[line2,
       "p50 and p99 latency, open loop. Hollow marker = the cluster fell behind the offered rate."])
    o.append(f'<rect x="{L}" y="{T}" width="{xm(comfort)-L:.1f}" height="{PH}" fill="{BAND}"/>')
    o.append(f'<text x="{L+8}" y="{T+16}" font-size="11" fill="{MUTED}">comfort zone</text>')
    if name=="braft":
        o.append(f'<text x="{xm(comfort)-8:.1f}" y="{T+16}" font-size="11" fill="{MUTED}" '
                 f'text-anchor="end">tail turns &#8594;</text>')
    kx=xm(knee)
    o.append(f'<line x1="{kx:.1f}" y1="{T}" x2="{kx:.1f}" y2="{T+PH}" stroke="{MUTED}" '
             f'stroke-width="1" stroke-dasharray="4 4"/>')
    o.append(f'<text x="{kx+6:.1f}" y="{T+16}" font-size="11" font-weight="600" fill="{INK2}">{kneelabel}</text>')
    axes(o,L,T,PW,PH,xmax,xstep,yticks,"latency (µs), log scale",ym,lambda v: f"{v:,}")
    if name=="braft" and BURST1:
        series(o,BURST1,C3,xm,ym,"p99")
        last=BURST1[-1]
        o.append(f'<text x="{xm(last["rate"])-6:.1f}" y="{ym(last["p99"])-12:.1f}" font-size="12" '
                 f'font-weight="600" fill="{INK}" text-anchor="end">p99, burst 1</text>')
    for key,col in (("p99",C2),("p50",C1)):
        series(o,D[name],col,xm,ym,key)
        last=D[name][-1]
        o.append(f'<text x="{xm(last["rate"])+12:.1f}" y="{ym(last[key])+4:.1f}" font-size="12" '
                 f'font-weight="600" fill="{INK}">{key}</text>')
    legend(o,L,H,[("p50",C1),("p99",C2)] + ([("p99, uniform arrivals (burst 1)",C3)] if name=="braft" and BURST1 else []))
    o.append('</svg>')
    open(f"{OUT}/knee-{name}.svg","w").write("\n".join(o))
print(f"wrote knee-curves.svg and 3 per-product charts to {OUT}")
