"""Plot the threshold graph from results/threshold_data.csv.

One line per code distance: logical error rate (log scale) vs physical error
rate. The crossing point is the threshold. A clean version of this plot is the
single most useful thing to have at the top of the README, because it says in
one picture "the decoder works and here is where it stops helping".
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RESULTS = Path(__file__).resolve().parent.parent / "results"

# Colorblind-safe, high-contrast in light and dark. One hue per distance.
COLORS = {3: "#4C72B0", 5: "#C44E52", 7: "#55A868", 9: "#8172B3", 11: "#CCB974"}
MARKERS = {3: "o", 5: "s", 7: "^", 9: "D", 11: "v"}


def load(path):
    by_d = defaultdict(list)
    with open(path) as f:
        for row in csv.DictReader(f):
            by_d[int(row["d"])].append(
                (float(row["p"]), float(row["logical_error_rate"]),
                 float(row["ler_stderr"]))
            )
    for d in by_d:
        by_d[d].sort()
    return by_d


def estimate_threshold(by_d):
    """Rough threshold: the p where the two largest-distance curves cross.

    Linear interpolation in log space between the two highest distances. This
    is a visual estimate for the annotation, not a finite-size-scaling fit
    (that collapse fit is a documented next step, not needed for the picture).
    """
    ds = sorted(by_d)
    if len(ds) < 2:
        return None
    import math
    d_hi, d_lo = ds[-1], ds[-2]
    hi = by_d[d_hi]
    lo = by_d[d_lo]
    ps = sorted({p for p, _, _ in hi} & {p for p, _, _ in lo})
    hi_m = {p: v for p, v, _ in hi}
    lo_m = {p: v for p, v, _ in lo}
    prev = None
    for p in ps:
        if hi_m[p] <= 0 or lo_m[p] <= 0:
            prev = None
            continue
        diff = math.log(hi_m[p]) - math.log(lo_m[p])  # <0 below threshold
        if prev is not None and prev[1] <= 0 <= diff:
            p0, d0 = prev
            t = (0 - d0) / (diff - d0)
            return p0 + t * (p - p0)
        prev = (p, diff)
    return None


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", default=str(RESULTS))
    args = ap.parse_args()
    results = Path(args.results_dir)

    data = results / "threshold_data.csv"
    if not data.exists():
        raise SystemExit(f"{data} not found. Run baseline_threshold.py first.")

    by_d = load(data)
    fig, ax = plt.subplots(figsize=(7, 5))

    for d in sorted(by_d):
        pts = by_d[d]
        xs = [p for p, _, _ in pts]
        ys = [v for _, v, _ in pts]
        es = [e for _, _, e in pts]
        ax.errorbar(xs, ys, yerr=es, label=f"d = {d}",
                    color=COLORS.get(d, "#444"), marker=MARKERS.get(d, "o"),
                    markersize=5, capsize=2, linewidth=1.6)

    # Log scale must be set before placing the annotation, otherwise the
    # linear-scale y-limits (which can be negative) push the label off-plot.
    ax.set_yscale("log")

    thr = estimate_threshold(by_d)
    if thr:
        ax.axvline(thr, color="#888", linestyle="--", linewidth=1)
        # x in data coords, y in axes fraction: robust to any y scale.
        ax.annotate(f"threshold ≈ {thr*100:.2f}%",
                    xy=(thr, 0.04), xycoords=("data", "axes fraction"),
                    xytext=(6, 0), textcoords="offset points",
                    color="#444", fontsize=9)

    ax.set_xlabel("physical error rate  p")
    ax.set_ylabel("logical error rate per shot")
    ax.set_title("Rotated surface code: Stim + PyMatching baseline")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(title="code distance", frameon=False)
    fig.tight_layout()

    out = results / "threshold.png"
    fig.savefig(out, dpi=150)
    fig.savefig(results / "threshold.svg")
    print(f"Wrote {out}")
    if thr:
        print(f"Estimated threshold: {thr*100:.3f}%")


if __name__ == "__main__":
    main()
