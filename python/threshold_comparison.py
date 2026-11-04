"""Threshold comparison: PyMatching vs backlog Union-Find (weighted, unweighted).

All three decoders see the same circuits through sinter, so the comparison is
apples to apples. Results are stored in sinter's own CSV format, so the file
also works with `sinter plot`.

    python3 threshold_comparison.py collect      # long: run in the background
    python3 threshold_comparison.py plot         # figures from the CSV
"""
from __future__ import annotations

import argparse
import math
import os
from collections import defaultdict
from pathlib import Path

import sinter

RESULTS = Path(__file__).resolve().parent.parent / "results"
CSV = RESULTS / "threshold_comparison.csv"

DECODERS = ["pymatching", "backlog-uf", "backlog-uf-unweighted"]
LABELS = {
    "pymatching": "PyMatching (MWPM)",
    "backlog-uf": "backlog Union-Find, weighted",
    "backlog-uf-unweighted": "backlog Union-Find, unweighted",
}
DISTANCES = [3, 5, 7, 9]
PS = [0.002, 0.003, 0.004, 0.005, 0.006, 0.007, 0.008, 0.010]


def tasks():
    from generate_circuits import surface_code_circuit
    for d in DISTANCES:
        for p in PS:
            yield sinter.Task(
                circuit=surface_code_circuit(distance=d, rounds=d, p=p),
                json_metadata={"d": d, "p": p, "rounds": d},
            )


def collect(workers: int, max_shots: int, max_errors: int):
    import backlog
    RESULTS.mkdir(exist_ok=True)
    sinter.collect(
        num_workers=workers,
        tasks=list(tasks()),
        decoders=DECODERS,
        custom_decoders=backlog.sinter_decoders(),
        max_shots=max_shots,
        max_errors=max_errors,
        save_resume_filepath=str(CSV),
        print_progress=False,
    )
    print(f"wrote {CSV}")


def load():
    stats = sinter.read_stats_from_csv_files(str(CSV))
    table = defaultdict(dict)  # (decoder, d) -> {p: (ler, stderr, shots, errors)}
    for s in stats:
        d, p = s.json_metadata["d"], s.json_metadata["p"]
        if s.shots == 0:
            continue
        ler = s.errors / s.shots
        se = math.sqrt(ler * (1 - ler) / s.shots)
        table[(s.decoder, d)][p] = (ler, se, s.shots, s.errors)
    return table


def crossing(table, decoder, d_lo, d_hi, ler=None):
    """p where the d_hi curve crosses above the d_lo curve (log interpolation).

    ler(decoder, d, p) overrides the measured logical error rate; the bootstrap
    uses it to resample every point within its statistical error.
    """
    lo, hi = table.get((decoder, d_lo), {}), table.get((decoder, d_hi), {})
    ps = sorted(set(lo) & set(hi))
    prev = None
    for p in ps:
        a, b = (ler(decoder, d_lo, p), ler(decoder, d_hi, p)) if ler else (lo[p][0], hi[p][0])
        if a <= 0 or b <= 0:
            prev = None
            continue
        diff = math.log(b) - math.log(a)
        if prev is not None and prev[1] < 0 <= diff:
            p0, d0 = prev
            return p0 + (0 - d0) / (diff - d0) * (p - p0)
        prev = (p, diff)
    return None


def crossing_spread(table, decoder, d_lo, d_hi, resamples=2000, seed=0):
    """Spread of the crossing point when each point moves within its 1-sigma error."""
    import numpy as np
    rng = np.random.default_rng(seed)
    found = []
    for _ in range(resamples):
        draws = {}

        def ler(dec, d, p):
            key = (dec, d, p)
            if key not in draws:
                mean, se = table[(dec, d)][p][:2]
                draws[key] = max(rng.normal(mean, se), 1e-12)
            return draws[key]

        c = crossing(table, decoder, d_lo, d_hi, ler)
        if c is not None:
            found.append(c)
    if len(found) < resamples // 2:
        return None
    return float(np.std(found))


def ratio_and_error(num, den):
    """LER ratio and its 1-sigma error from the two binomial standard errors."""
    r = num[0] / den[0]
    return r, r * math.sqrt((num[1] / num[0]) ** 2 + (den[1] / den[0]) ** 2)


def plot():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    table = load()
    colors = {3: "#4C72B0", 5: "#C44E52", 7: "#55A868", 9: "#8172B3"}
    markers = {3: "o", 5: "s", 7: "^", 9: "D"}
    thresholds = {dec: crossing(table, dec, DISTANCES[-2], DISTANCES[-1]) for dec in DECODERS}
    spreads = {dec: crossing_spread(table, dec, DISTANCES[-2], DISTANCES[-1]) for dec in DECODERS}

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.8), sharey=True)
    for ax, dec in zip(axes, DECODERS):
        for d in DISTANCES:
            pts = sorted(table.get((dec, d), {}).items())
            if not pts:
                continue
            xs = [p for p, _ in pts]
            ys = [v[0] for _, v in pts]
            es = [v[1] for _, v in pts]
            ax.errorbar(xs, ys, yerr=es, color=colors[d], marker=markers[d],
                        markersize=4, capsize=2, linewidth=1.5, label=f"d = {d}")
        thr = thresholds[dec]
        title = LABELS[dec]
        if thr:
            ax.axvline(thr, color="#888", linestyle="--", linewidth=1)
            spread = spreads[dec]
            pm_str = f" ± {spread * 100:.2f}" if spread else ""
            title += (f"\nd = {DISTANCES[-2]} / {DISTANCES[-1]} crossing ≈ "
                      f"{thr * 100:.2f}{pm_str}%")
        ax.set_title(title, fontsize=11)
        ax.set_yscale("log")
        ax.set_xlabel("physical error rate  p")
        ax.grid(True, which="both", alpha=0.25)
    axes[0].set_ylabel("logical error rate per shot")
    axes[0].legend(frameon=False, title="code distance")
    fig.suptitle("Rotated surface code memory, circuit-level noise, rounds = d", fontsize=12)
    fig.tight_layout()
    fig.savefig(RESULTS / "threshold_comparison.png", dpi=150)

    # Accuracy relative to PyMatching
    fig, ax = plt.subplots(figsize=(7.5, 4.8))
    for dec, style in (("backlog-uf", "-"), ("backlog-uf-unweighted", "--")):
        for d in DISTANCES:
            pm = table.get(("pymatching", d), {})
            uf = table.get((dec, d), {})
            ps = sorted(p for p in set(pm) & set(uf) if pm[p][3] >= 50 and uf[p][3] >= 50)
            if not ps:
                continue
            ratios = [ratio_and_error(uf[p], pm[p]) for p in ps]
            ax.errorbar(ps, [r for r, _ in ratios], yerr=[e for _, e in ratios],
                        linestyle=style, color=colors[d], marker=markers[d], markersize=4,
                        linewidth=1.5, capsize=2, elinewidth=0.8,
                        markerfacecolor="white" if style == "--" else colors[d],
                        label=f"d = {d}, {'weighted' if style == '-' else 'unweighted'}")
    ax.axhline(1.0, color="#444", linewidth=1)
    ax.set_xlabel("physical error rate  p")
    ax.set_ylabel("logical error rate / PyMatching's")
    ax.set_title("Union-Find logical error rate relative to PyMatching\n"
                 "same circuits, 50+ logical errors per point, 1-sigma error bars", fontsize=11)
    ax.grid(True, alpha=0.25)
    ax.legend(frameon=False, fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(RESULTS / "accuracy_vs_pymatching.png", dpi=150)

    print("crossing of d = %d and d = %d (bootstrap spread):" % (DISTANCES[-2], DISTANCES[-1]))
    for dec in DECODERS:
        thr, spread = thresholds[dec], spreads[dec]
        if thr:
            print(f"  {dec:24s} {thr * 100:.3f}% ± {spread * 100:.3f}%" if spread
                  else f"  {dec:24s} {thr * 100:.3f}%")
        else:
            print(f"  {dec:24s} n/a")
    print("LER ratio to PyMatching at p = 0.3% and 0.5%:")
    for d in DISTANCES:
        row = []
        for dec in ("backlog-uf", "backlog-uf-unweighted"):
            for p in (0.003, 0.005):
                pm, uf = table.get(("pymatching", d), {}).get(p), table.get((dec, d), {}).get(p)
                if pm and uf and pm[0] > 0 and uf[0] > 0:
                    r, e = ratio_and_error(uf, pm)
                    row.append(f"{r:.2f} ± {e:.2f}")
                else:
                    row.append("n/a")
        print(f"  d={d}: weighted {row[0]} / {row[1]}   unweighted {row[2]} / {row[3]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["collect", "plot"])
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--max-shots", type=int, default=1_000_000)
    ap.add_argument("--max-errors", type=int, default=500)
    args = ap.parse_args()
    if args.mode == "collect":
        collect(args.workers, args.max_shots, args.max_errors)
    else:
        plot()


if __name__ == "__main__":
    main()
