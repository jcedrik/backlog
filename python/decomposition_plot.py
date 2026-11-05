"""How much correlated matching changes the logical error rate, with Stim's
decomposition and with the X/Z split (PyMatching, same shots for every decoder).

    python python/decomposition_plot.py results/correlated_decomposition.csv results/decomposition.png

The counts come from `make correlated` (python/correlated_benchmark.py
decomposition). Used by docs/correlated-decoding-decomposition.md.
"""
import csv
import math
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASE = "#c3c2b7"
SERIES = {"stim": "#2a78d6", "basis": "#eb6834"}
NAMES = {"stim": "Stim's decompose_errors=True", "basis": "X/Z split (decompose_by_basis)"}

rows = list(csv.DictReader(open(sys.argv[1])))
errors = {(int(r["distance"]), float(r["p"]), r["decomposition"], r["decoder"]): int(r["errors"]) for r in rows}
cases = [(3, 0.001), (3, 0.003), (5, 0.001), (5, 0.003)]

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
fig, ax = plt.subplots(figsize=(8.4, 4.9), dpi=200)
fig.patch.set_facecolor(SURFACE)
ax.set_facecolor(SURFACE)

width = 0.25
gap = 0.02
for k, dec in enumerate(["stim", "basis"]):
    xs, ys, es = [], [], []
    for i, (d, p) in enumerate(cases):
        corr = errors[(d, p, dec, "PM-corr")]
        plain = errors[(d, p, dec, "PM")]
        ratio = corr / plain
        xs.append(i + (k - 0.5) * (width + gap))
        ys.append(100 * (ratio - 1))
        es.append(100 * ratio * math.sqrt(1 / corr + 1 / plain))
    ax.bar(xs, ys, width=width, color=SERIES[dec], label=NAMES[dec], zorder=3)
    ax.errorbar(xs, ys, yerr=es, fmt="none", ecolor=INK2, elinewidth=1, capsize=3, zorder=4)
    for x, y, e in zip(xs, ys, es):
        off = e + 2.2
        ax.text(x, y + (off if y >= 0 else -off), f"{y:+.0f}%".replace("-", "\u2212"), ha="center",
                va="bottom" if y >= 0 else "top", color=INK, fontsize=10.5, zorder=5)

ax.axhline(0, color=BASE, linewidth=1.2, zorder=2)
ax.set_xticks(range(len(cases)))
ax.set_xticklabels([f"d = {d}\np = {p * 100:.1f}%" for d, p in cases], color=INK2)
ax.tick_params(axis="x", length=0, pad=8)
ax.tick_params(axis="y", colors=MUTED, length=0)
ax.set_ylim(-62, 62)
ax.set_yticks(range(-60, 61, 20))
ax.set_yticklabels([(f"{t:+d}%" if t else "0").replace("-", "\u2212") for t in range(-60, 61, 20)])
ax.grid(axis="y", color=GRID, linewidth=0.8, zorder=0)
for side in ("top", "right", "left", "bottom"):
    ax.spines[side].set_visible(False)
ax.set_ylabel("logical errors, correlated vs plain matching", color=INK2)
ax.text(-0.62, 56, "worse", color=MUTED, fontsize=10, va="center")
ax.text(-0.62, -56, "better", color=MUTED, fontsize=10, va="center")
ax.set_xlim(-0.7, len(cases) - 0.3)
ax.legend(loc="upper right", frameon=False, fontsize=10, labelcolor=INK)

fig.text(0.015, 0.965, "Correlated matching on a Stim circuit: the decomposition decides the sign",
         fontsize=13.5, color=INK, weight="bold", ha="left", va="top")
fig.text(0.015, 0.905, "PyMatching 2.4, rotated surface code memory (d rounds), uniform circuit noise, "
         "1,000,000 shots per point,\nthe same shots for every decoder. Error bars: ±1σ, Poisson (conservative).",
         fontsize=9.5, color=INK2, ha="left", va="top")
fig.subplots_adjust(left=0.1, right=0.985, top=0.78, bottom=0.14)
fig.savefig(sys.argv[2], facecolor=SURFACE)
print("saved", sys.argv[2])
