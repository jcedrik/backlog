"""How much work a predecoder takes off Union-Find, and what it costs in accuracy.

The predecoder settles every isolated defect (no other defect within two
edges) that pairs with an adjacent defect or sits next to the boundary.
"lazy" (Delfosse's lazy decoder, arXiv:2001.11427) settles a shot only if
every defect is settled, and leaves any other shot to Union-Find unchanged:
in hardware, the shots it settles never reach the main decoder. "partial"
settles the isolated defects of every shot and runs Union-Find on the rest.

For each distance and error rate this records, on the same shots: the shots
lazy settles whole, the defects partial settles, the logical errors of each
(paired against Union-Find alone: shots fixed and shots broken), and the mean
and tail decoding time.

    python python/predecoder_benchmark.py          # writes results/predecoder.csv
    python python/predecoder_benchmark.py --plot   # draws results/predecoder.png
"""
from __future__ import annotations

import argparse
import csv
import os
import time

import numpy as np
import stim

import backlog

RESULTS = os.path.join(os.path.dirname(__file__), "..", "results")
CSV = os.path.join(RESULTS, "predecoder.csv")
MODES = (None, "lazy", "partial")
NAMES = {None: "uf", "lazy": "lazy", "partial": "partial"}
FIELDS = (["distance", "p", "shots", "empty_shots", "defects", "lazy_shots", "partial_defects"]
          + [f"{NAMES[m]}_errors" for m in MODES]
          + [f"{x}_{k}" for x in ("lazy", "partial") for k in ("fixed", "broken")]
          + [f"{NAMES[m]}_mean_ns" for m in MODES] + [f"{NAMES[m]}_p99_ns" for m in MODES])


def circuit(d: int, p: float) -> stim.Circuit:
    return stim.Circuit.generated(
        "surface_code:rotated_memory_z", distance=d, rounds=d,
        after_clifford_depolarization=p, before_measure_flip_probability=p,
        after_reset_flip_probability=p, before_round_data_depolarization=p)


def point(d: int, p: float, shots: int, seed: int, repeats: int) -> dict:
    c = circuit(d, p)
    dem = c.detector_error_model(decompose_errors=True)
    dec = {m: backlog.UnionFindDecoder.from_dem(dem, predecode=m) for m in MODES}
    det, obs = c.compile_detector_sampler(seed=seed).sample(
        shots, separate_observables=True, bit_packed=True)
    truth = obs[:, 0] & 1
    wrong = {m: (dec[m].decode_batch_bit_packed(det)[:, 0] & 1) != truth for m in MODES}
    st = {m: dec[m].stats() for m in MODES}
    row = dict(distance=d, p=p, shots=shots, empty_shots=int((~det.any(axis=1)).sum()),
               defects=int(np.unpackbits(det, axis=1).sum()),
               lazy_shots=st["lazy"]["predecoded"],
               partial_defects=st["partial"]["predecoded_defects"])
    for m in MODES:
        row[f"{NAMES[m]}_errors"] = int(wrong[m].sum())
    for m in ("lazy", "partial"):
        row[f"{m}_fixed"] = int((wrong[None] & ~wrong[m]).sum())
        row[f"{m}_broken"] = int((wrong[m] & ~wrong[None]).sum())
    # Timing: the same shots, decoders in turn, the best mean of a few passes.
    times = {m: [] for m in MODES}
    for _ in range(repeats):
        for m in MODES:
            times[m].append(dec[m].latency_ns(det))
    for m in MODES:
        best = min(times[m], key=np.mean)
        row[f"{NAMES[m]}_mean_ns"] = float(np.mean(best))
        row[f"{NAMES[m]}_p99_ns"] = float(np.percentile(best, 99))
    return row


def run(distances, ps, shots: int, seed: int, repeats: int) -> None:
    os.makedirs(RESULTS, exist_ok=True)
    rows = []
    for p in ps:
        for d in distances:
            t0 = time.perf_counter()
            r = point(d, p, shots, seed, repeats)
            rows.append(r)
            n = r["shots"]
            print(f"d={d:2d} p={p:g}: lazy settles {100 * r['lazy_shots'] / n:5.1f}% of shots "
                  f"(empty {100 * r['empty_shots'] / n:5.1f}%), partial "
                  f"{100 * r['partial_defects'] / max(1, r['defects']):5.1f}% of defects; errors UF "
                  f"{r['uf_errors']}, lazy {r['lazy_errors']} (+{r['lazy_broken']} -{r['lazy_fixed']}),"
                  f" partial {r['partial_errors']} (+{r['partial_broken']} -{r['partial_fixed']}); "
                  f"mean ns {r['uf_mean_ns']:.0f} / {r['lazy_mean_ns']:.0f} / "
                  f"{r['partial_mean_ns']:.0f} ({time.perf_counter() - t0:.0f} s)", flush=True)
    with open(CSV, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {CSV}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--distances", type=int, nargs="+", default=[3, 5, 7, 9, 11, 13])
    ap.add_argument("--p", type=float, nargs="+", default=[5e-4, 1e-3, 3e-3])
    ap.add_argument("--shots", type=int, default=500_000)
    ap.add_argument("--repeats", type=int, default=2, help="timing passes, best mean kept")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--plot", action="store_true")
    args = ap.parse_args()
    if args.plot:
        plot(os.path.join(RESULTS, "predecoder.png"))
    else:
        run(args.distances, args.p, args.shots, args.seed, args.repeats)


def plot(png: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    surface, ink, ink2, muted, grid, base = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
    with open(CSV) as fh:
        rows = list(csv.DictReader(fh))
    ps = sorted({float(r["p"]) for r in rows})
    colors = dict(zip(ps, ["#86b6ef", "#2a78d6", "#123f7a"][-len(ps):]))
    shots = max(int(r["shots"]) for r in rows)
    ds = sorted({int(r["distance"]) for r in rows})

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 5.1), dpi=200)
    fig.patch.set_facecolor(surface)
    for ax in (ax1, ax2):
        ax.set_facecolor(surface)
        ax.tick_params(axis="both", length=0, colors=muted)
        ax.grid(axis="y", color=grid, linewidth=0.8, zorder=0)
        for side in ("top", "right", "left", "bottom"):
            ax.spines[side].set_visible(False)
        ax.set_xticks(ds)
        ax.set_xlabel("code distance d (d rounds)", color=ink2)
    for p in ps:
        pts = sorted((int(r["distance"]), r) for r in rows if float(r["p"]) == p)
        x = [d for d, _ in pts]
        c = colors[p]
        lazy = [100 * int(r["lazy_shots"]) / int(r["shots"]) for _, r in pts]
        part = [100 * int(r["partial_defects"]) / max(1, int(r["defects"])) for _, r in pts]
        ax1.plot(x, part, "o-", color=c, linewidth=2, markersize=5, zorder=3)
        ax1.plot(x, lazy, "s--", color=c, linewidth=1.5, markersize=4, zorder=3)
        ax1.annotate(f"p = {100 * p:g}%", (x[-1], part[-1]), textcoords="offset points",
                     xytext=(6, 0), va="center", color=ink2, fontsize=9.5)
        for key, style, lw in (("partial", "o-", 2), ("lazy", "s--", 1.5)):
            ratio = [float(r[f"{key}_mean_ns"]) / float(r["uf_mean_ns"]) for _, r in pts]
            ax2.plot(x, ratio, style, color=c, linewidth=lw, markersize=5 if key == "partial" else 4,
                     zorder=3)
    for ax, (solid, dashed) in ((ax1, ("defects settled, partial", "shots settled whole, lazy")),
                                (ax2, ("partial", "lazy"))):
        ax.plot([], [], "o-", color=ink2, linewidth=2, markersize=5, label=solid)
        ax.plot([], [], "s--", color=ink2, linewidth=1.5, markersize=4, label=dashed)
    ax1.legend(loc="upper right", frameon=False, fontsize=9.5, labelcolor=ink)
    ax1.set_ylim(0, 105)
    ax1.set_xlim(ds[0] - 0.6, ds[-1] + 2.4)
    ax1.set_ylabel("%", color=ink2)
    ax1.set_title("Work taken off Union-Find", color=ink, fontsize=11.5, loc="left")
    ax2.axhline(1.0, color=base, linewidth=1.2, zorder=2)
    ax2.set_ylim(0, 1.4)
    ax2.set_xlim(ds[0] - 0.6, ds[-1] + 0.6)
    ax2.set_ylabel("mean time per shot / Union-Find alone", color=ink2)
    ax2.set_title("Decoding time with the predecoder", color=ink, fontsize=11.5, loc="left")
    ax2.legend(loc="lower right", frameon=False, fontsize=9.5, labelcolor=ink)
    total = sum(int(r["shots"]) for r in rows)
    changed = sum(int(r["lazy_fixed"]) + int(r["lazy_broken"]) for r in rows)
    # relative change only where Union-Find alone makes enough errors to say
    worst, at = max((int(r["partial_errors"]) / int(r["uf_errors"]) - 1, float(r["p"]))
                    for r in rows if int(r["uf_errors"]) >= 50)
    fig.text(0.012, 0.97, "A predecoder settles most defects, but they are the cheap ones",
             fontsize=13, color=ink, weight="bold", ha="left", va="top")
    fig.text(0.012, 0.905, f"Rotated surface code memory, uniform circuit noise, {shots:,} shots per "
             "point, the same shots for every decoder; lines are p = "
             + ", ".join(f"{100 * p:g}%" for p in ps) + ", light to dark.\n"
             f"Lazy changed {changed} of {total:,} answers; partial makes up to {100 * worst:.0f}% "
             f"more logical errors (p = {100 * at:g}%). Time: C++, one core, best mean of 2 passes.",
             fontsize=9.5, color=ink2, ha="left", va="top")
    fig.subplots_adjust(left=0.065, right=0.985, top=0.77, bottom=0.12, wspace=0.2)
    fig.savefig(png, facecolor=surface)
    print("saved", png)


if __name__ == "__main__":
    main()
