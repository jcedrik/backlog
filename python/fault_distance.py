"""Exact fault enumeration: which small sets of faults does each decoder get wrong?

A fault is one error mechanism of the circuit's decomposed detector error model.
A distance-d code must correct every set of (d-1)/2 faults: a decoder that gets
one wrong has lost a unit of effective distance, and deep below threshold its
logical error rate scales as p^((d-1)/2) instead of p^((d+1)/2).

Hernando de la Fuente et al. (arXiv:2610.26785) found that standard correlated
decoding (two-pass reweighting) fails such sets, for matching and for Riverlane's
Union-Find-based Local Clustering Decoder, and that scaling every conditional
probability by alpha < 1 restores the distance. This script checks the same on
backlog's correlated Union-Find, with PyMatching (plain and correlated) as a
control, by decoding every single fault and every pair of faults.

For every set it records whether the decoder's prediction differs from the
true logical flip. The summed probability of the failing sets is the leading
order of the logical error rate as p -> 0.

Every decoder gets the same model, re-decomposed by stabilizer type (X part,
Z part), as in the paper; --decomposition stim keeps Stim's own instead.

    python python/fault_distance.py                   # d = 3, 5 at several p
    python python/fault_distance.py --plot
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time

import numpy as np
import pymatching
import stim

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import backlog  # noqa: E402

RESULTS = os.path.join(os.path.dirname(__file__), "..", "results")
CSV = os.path.join(RESULTS, "fault_distance.csv")
FIELDS = ["distance", "p", "size", "decoder", "alpha", "sets", "failing_sets",
          "failing_probability", "only_this_fails"]


def circuit(d: int, p: float) -> stim.Circuit:
    return stim.Circuit.generated(
        "surface_code:rotated_memory_z", distance=d, rounds=d,
        after_clifford_depolarization=p, before_measure_flip_probability=p,
        after_reset_flip_probability=p, before_round_data_depolarization=p)


def mechanisms(dem: stim.DetectorErrorModel):
    """Bit-packed symptom, observable mask and probability of every fault."""
    n = dem.num_detectors
    rows, obs, probs = [], [], []
    for inst in dem.flattened():
        if inst.type != "error":
            continue
        dets: set[int] = set()
        mask = 0
        for t in inst.targets_copy():
            if t.is_relative_detector_id():
                dets ^= {t.val}
            elif t.is_logical_observable_id():
                mask ^= 1 << t.val
        rows.append(sorted(dets))
        obs.append(mask)
        probs.append(inst.args_copy()[0])
    packed = np.zeros((len(rows), (n + 7) // 8), dtype=np.uint8)
    for i, ds in enumerate(rows):
        for v in ds:
            packed[i, v >> 3] |= np.uint8(1 << (v & 7))
    return packed, np.array(obs, dtype=np.uint8), np.array(probs)


def fault_sets(packed, obs, probs, size: int):
    """Every set of `size` distinct faults: symptoms, true flips, probabilities."""
    m = len(probs)
    if size == 1:
        return packed, obs, probs
    i, j = np.triu_indices(m, k=1)
    return packed[i] ^ packed[j], obs[i] ^ obs[j], probs[i] * probs[j]


def decoders(dem: stim.DetectorErrorModel, alphas):
    """(name, alpha, predict) for every decoder compared."""
    edges = backlog.dem_to_edges(dem)
    corr = backlog.dem_correlations(dem, edges)
    out = [("UF", "", backlog.UnionFindDecoder(edges).decode_batch_bit_packed)]
    for a in alphas:
        dec = backlog.UnionFindDecoder(edges, correlations=corr, alpha=a)
        out.append(("UF-corr", a, dec.decode_batch_bit_packed))
    pm = pymatching.Matching.from_detector_error_model(dem)
    pmc = pymatching.Matching.from_detector_error_model(dem, enable_correlations=True)
    out.append(("PM", "", lambda s: pm.decode_batch(
        s, bit_packed_shots=True, bit_packed_predictions=True)))
    out.append(("PM-corr", 1.0, lambda s: pmc.decode_batch(
        s, bit_packed_shots=True, bit_packed_predictions=True, enable_correlations=True)))
    return out


def run(distances, ps, alphas, decomposition):
    os.makedirs(RESULTS, exist_ok=True)
    rows = []
    for d in distances:
        for p in ps:
            dem = circuit(d, p).detector_error_model(decompose_errors=True)
            if decomposition == "basis":
                dem = backlog.decompose_by_basis(dem)
            packed, obs, probs = mechanisms(dem)
            decs = decoders(dem, alphas)
            for size in range(1, (d + 1) // 2 + 1):
                if size > 2:
                    break  # exhaustive triples are out of reach here
                t0 = time.perf_counter()
                syn, truth, weight = fault_sets(packed, obs, probs, size)
                fails = {}
                for name, alpha, predict in decs:
                    pred = np.asarray(predict(syn))[:, 0] & 1
                    fails[(name, alpha)] = pred != (truth & 1)
                uf_fails = fails[("UF", "")]
                pm_fails = fails[("PM", "")]
                for (name, alpha), f in fails.items():
                    base = pm_fails if name.startswith("PM") else uf_fails
                    rows.append(dict(
                        distance=d, p=p, size=size, decoder=name, alpha=alpha,
                        sets=len(weight), failing_sets=int(f.sum()),
                        failing_probability=float(weight[f].sum()),
                        only_this_fails=int((f & ~base).sum())))
                summary = ", ".join(f"{n}{'' if a == '' else f'({a:g})'}: {int(f.sum())}"
                                    for (n, a), f in fails.items())
                print(f"d={d} p={p:g} size {size}: {len(weight)} sets "
                      f"({time.perf_counter() - t0:.1f} s) -> {summary}", flush=True)
    with open(CSV, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {CSV}")


def plot():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    with open(CSV) as fh:
        rows = list(csv.DictReader(fh))

    def pick(d, size, decoder, p):
        return [r for r in rows if int(r["distance"]) == d and int(r["size"]) == size
                and r["decoder"] == decoder and float(r["p"]) == p]

    ps = sorted({float(r["p"]) for r in rows})
    colors = dict(zip(ps, plt.cm.viridis(np.linspace(0.1, 0.85, len(ps)))))
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.3))

    # (a) d = 5: pairs of faults, which a distance-5 code must correct.
    for p in ps:
        pts = sorted((float(r["alpha"]), int(r["failing_sets"])) for r in pick(5, 2, "UF-corr", p))
        if not pts:
            continue
        ax1.plot([a for a, _ in pts], [f + 0.5 for _, f in pts], "o-", color=colors[p],
                 label=f"correlated UF, p = {p:g}")
        pmc = pick(5, 2, "PM-corr", p)
        if pmc:
            ax1.plot([1.0], [int(pmc[0]["failing_sets"]) + 0.5], "s", mfc="none", ms=11,
                     color=colors[p])
    ax1.set_xscale("log")
    ax1.set_yscale("log")
    ax1.set_xlabel("reweighting strength α")
    ax1.set_ylabel("failing pairs of faults (+0.5), out of 1 906 128")
    ax1.set_title("d = 5: pairs a distance-5 code must correct\n"
                  "plain UF and PyMatching fail none; squares: PyMatching correlated",
                  fontsize=10)
    ax1.grid(True, which="both", alpha=0.3)
    ax1.legend(fontsize=8, loc="upper left")

    # (b) d = 3: the leading order of the logical error rate (pairs of faults),
    # relative to plain Union-Find.
    for p in ps:
        base = pick(3, 2, "UF", p)
        pts = sorted((float(r["alpha"]), float(r["failing_probability"])) for r in pick(3, 2, "UF-corr", p))
        if not base or not pts:
            continue
        b = float(base[0]["failing_probability"])
        ax2.plot([a for a, _ in pts], [f / b for _, f in pts], "o-", color=colors[p],
                 label=f"correlated UF, p = {p:g}")
        pm, pmc = pick(3, 2, "PM", p), pick(3, 2, "PM-corr", p)
        if pm and pmc:
            ax2.plot([1.0], [float(pmc[0]["failing_probability"]) / float(pm[0]["failing_probability"])],
                     "s", mfc="none", ms=11, color=colors[p])
    ax2.axhline(1.0, color="k", lw=0.8)
    ax2.set_xscale("log")
    ax2.set_xlabel("reweighting strength α")
    ax2.set_ylabel("probability of failing pairs / plain decoder")
    ax2.set_title("d = 3: every single fault corrected by all decoders;\n"
                  "leading order (pairs) relative to plain UF (squares: PM corr. / PM)",
                  fontsize=10)
    ax2.grid(True, which="both", alpha=0.3)
    ax2.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    out = os.path.join(RESULTS, "fault_distance.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"wrote {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--distances", type=int, nargs="+", default=[3, 5])
    ap.add_argument("--p", type=float, nargs="+", default=[1e-4, 3e-4, 1e-3, 3e-3])
    ap.add_argument("--alphas", type=float, nargs="+",
                    default=[1.0, 0.5, 0.3, 0.2, 0.1, 0.05, 0.02, 0.01])
    ap.add_argument("--decomposition", choices=["basis", "stim"], default="basis")
    ap.add_argument("--plot", action="store_true", help="only redraw the figure")
    args = ap.parse_args()
    if not args.plot:
        run(args.distances, args.p, args.alphas, args.decomposition)
    plot()


if __name__ == "__main__":
    main()
