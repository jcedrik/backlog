"""Fmax vs N on an iCE40 HX8K, Kogge-Stone scan against ripple chains.

    python3 plot_fmax.py fmax.csv fmax.png     (run_hw.sh calls it)
"""
import csv
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
SERIES = {"parallel": ("#2a78d6", "Kogge-Stone scan, O(log N) levels"),
          "ripple": ("#eb6834", "ripple chains, O(N) levels")}

rows = list(csv.DictReader(open(sys.argv[1])))
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
fig, ax = plt.subplots(figsize=(8.4, 4.9), dpi=200)
fig.patch.set_facecolor(SURFACE)
ax.set_facecolor(SURFACE)
sizes = sorted({int(r["N"]) for r in rows})
for scan, (color, name) in SERIES.items():
    pts = sorted((int(r["N"]), float(r["fmax_mhz"])) for r in rows if r["scan"] == scan)
    xs, ys = [n for n, _ in pts], [f for _, f in pts]
    ax.plot(xs, ys, "-", color=color, linewidth=2, zorder=3, label=name)
    ax.plot(xs, ys, "o", color=color, markersize=8, markeredgecolor=SURFACE,
            markeredgewidth=2, zorder=4)
    above = scan == "parallel"   # values above the upper line, below the lower one
    for x, y in pts:
        ax.annotate(f"{y:.0f}", (x, y), textcoords="offset points",
                    xytext=(0, 9 if above else -10), ha="center",
                    va="bottom" if above else "top", color=INK, fontsize=9.5)
ax.legend(loc="upper right", frameon=False, fontsize=10, labelcolor=INK)
ax.set_xscale("log", base=2)
ax.set_xticks(sizes)
ax.set_xticklabels([str(n) for n in sizes], color=INK2)
ax.set_xlabel("N, detectors in the chain (code distance N + 1)", color=INK2)
ax.set_ylabel("Fmax after place and route (MHz)", color=INK2)
ax.set_ylim(0, 115)
ax.tick_params(axis="both", length=0, colors=MUTED)
ax.grid(axis="y", color=GRID, linewidth=0.8, zorder=0)
for side in ("top", "right", "left", "bottom"):
    ax.spines[side].set_visible(False)
fig.text(0.015, 0.965, "A parallel scan keeps the clock up as the chain grows",
         fontsize=13.5, color=INK, weight="bold", ha="left", va="top")
big = {r["scan"]: r for r in rows if int(r["N"]) == sizes[-1]}
luts = int(big["parallel"]["luts_xc7"]) / int(big["ripple"]["luts_xc7"])
speed = float(big["parallel"]["fmax_mhz"]) / float(big["ripple"]["fmax_mhz"])
fig.text(0.015, 0.905, "1D Union-Find core in VHDL, iCE40 HX8K, nextpnr, best of 3 placement seeds. "
         f"Both versions give the same\noutputs bit for bit; at N = {sizes[-1]} the scan costs "
         f"{luts:.1f}x the LUTs and runs {speed:.1f}x faster.",
         fontsize=9.5, color=INK2, ha="left", va="top")
fig.subplots_adjust(left=0.1, right=0.985, top=0.79, bottom=0.13)
fig.savefig(sys.argv[2], facecolor=SURFACE)
print("saved", sys.argv[2])
