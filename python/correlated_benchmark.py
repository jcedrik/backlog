"""Correlated decoding: accuracy, error suppression and speed against PyMatching.

Every decoder sees exactly the same shots, so ratios between decoders come with
tight paired error bars (only the shots where exactly one of the two fails
carry information).

Decoders:
  UF          backlog's weighted Union-Find
  UF-corr a   two-pass correlated Union-Find, conditional probabilities times a
              (a = 1: standard correlated decoding; a < 1: regularized,
              Hernando de la Fuente et al., arXiv:2610.26785)
  PM          PyMatching (sparse blossom, exact minimum-weight matching)
  PM-corr     PyMatching's correlated matching (enable_correlations=True)

Every decoder decodes the same model, re-decomposed by stabilizer type
(backlog.decompose_by_basis), which correlated decoding needs; the
`decomposition` mode shows what Stim's own decomposition costs instead.

Modes:
  accuracy       logical error rates on rotated surface code memories, d rounds
  decomposition  correlated decoding with Stim's decomposition vs by basis
  speed          decoding time per shot and per round
  plot           results/correlated.png from the CSVs

    python python/correlated_benchmark.py accuracy
    python python/correlated_benchmark.py speed
    python python/correlated_benchmark.py plot
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pymatching
import stim

import backlog

RESULTS = os.path.join(os.path.dirname(__file__), "..", "results")
ACCURACY_CSV = os.path.join(RESULTS, "correlated_accuracy.csv")
DECOMPOSITION_CSV = os.path.join(RESULTS, "correlated_decomposition.csv")
SPEED_CSV = os.path.join(RESULTS, "correlated_speed.csv")
ALPHAS = (1.0, 0.5, 0.3, 0.2, 0.1)


def circuit(d: int, p: float, rounds: int | None = None) -> stim.Circuit:
    return stim.Circuit.generated(
        "surface_code:rotated_memory_z", distance=d, rounds=rounds or d,
        after_clifford_depolarization=p, before_measure_flip_probability=p,
        after_reset_flip_probability=p, before_round_data_depolarization=p)


def model(c: stim.Circuit, decomposition: str = "basis") -> stim.DetectorErrorModel:
    dem = c.detector_error_model(decompose_errors=True)
    return backlog.decompose_by_basis(dem) if decomposition == "basis" else dem


def build(dem: stim.DetectorErrorModel, alphas=ALPHAS):
    """name -> (alpha, decode(bit-packed shots) -> bool predictions of L0)."""
    edges = backlog.dem_to_edges(dem)
    corr = backlog.dem_correlations(dem, edges)

    def ours(dec):
        return lambda s: (dec.decode_batch_bit_packed(s)[:, 0] & 1).astype(bool)

    out = {"UF": ("", ours(backlog.UnionFindDecoder(edges)))}
    for a in alphas:
        out[f"UF-corr {a:g}"] = (a, ours(backlog.UnionFindDecoder(
            edges, correlations=corr, alpha=a)))
    pm = pymatching.Matching.from_detector_error_model(dem)
    pmc = pymatching.Matching.from_detector_error_model(dem, enable_correlations=True)
    out["PM"] = ("", lambda s: (pm.decode_batch(
        s, bit_packed_shots=True, bit_packed_predictions=True)[:, 0] & 1).astype(bool))
    out["PM-corr"] = (1.0, lambda s: (pmc.decode_batch(
        s, bit_packed_shots=True, bit_packed_predictions=True,
        enable_correlations=True)[:, 0] & 1).astype(bool))
    return out


def accuracy_task(args):
    d, p, target, max_shots, seed = args
    c = circuit(d, p)
    decs = build(model(c))
    sampler = c.compile_detector_sampler(seed=seed)
    names = list(decs)
    errors = dict.fromkeys(names, 0)
    seconds = dict.fromkeys(names, 0.0)
    vs = {(x, ref): [0, 0] for x in names for ref in ("UF", "PM")}
    shots = 0
    batch = 50_000
    while shots < max_shots and errors["UF"] < target:
        n = min(batch, max_shots - shots)
        dets, obs = sampler.sample(n, separate_observables=True, bit_packed=True)
        truth = (obs[:, 0] & 1).astype(bool)
        fails = {}
        for name in names:
            t0 = time.perf_counter()
            pred = decs[name][1](dets)
            seconds[name] += time.perf_counter() - t0
            fails[name] = pred != truth
            errors[name] += int(fails[name].sum())
        for (x, ref), counts in vs.items():
            counts[0] += int((fails[x] & ~fails[ref]).sum())
            counts[1] += int((fails[ref] & ~fails[x]).sum())
        shots += n
        batch = min(batch * 2, 1_000_000)
    rows = []
    for name in names:
        rows.append(dict(
            distance=d, rounds=d, p=p, decoder=name.split(" ")[0],
            alpha=decs[name][0], shots=shots, errors=errors[name],
            only_this_vs_uf=vs[(name, "UF")][0], only_uf_vs_this=vs[(name, "UF")][1],
            only_this_vs_pm=vs[(name, "PM")][0], only_pm_vs_this=vs[(name, "PM")][1],
            seconds=round(seconds[name], 3)))
    print(f"d={d} p={p:g}: {shots} shots, errors " +
          ", ".join(f"{k} {v}" for k, v in errors.items()), flush=True)
    return rows


def accuracy(distances, ps, target, max_shots, workers):
    os.makedirs(RESULTS, exist_ok=True)
    tasks = [(d, p, target, max_shots, 1000 * d + i)
             for d in distances for i, p in enumerate(ps)]
    # Largest tasks first, so the pool finishes together.
    tasks.sort(key=lambda t: (t[0], -t[1]), reverse=True)
    rows = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for r in pool.map(accuracy_task, tasks):
            rows.extend(r)
    rows.sort(key=lambda r: (r["distance"], r["p"], r["decoder"], str(r["alpha"])))
    with open(ACCURACY_CSV, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {ACCURACY_CSV}")


def speed(distances, ps, shots):
    os.makedirs(RESULTS, exist_ok=True)
    rows = []
    for d in distances:
        for p in ps:
            c = circuit(d, p)
            decs = build(model(c), alphas=(1.0, 0.3))
            dets, _ = c.compile_detector_sampler(seed=7).sample(
                shots, separate_observables=True, bit_packed=True)
            for name, (alpha, predict) in decs.items():
                predict(dets[: min(2000, shots)])  # warm up
                best = math.inf
                for _ in range(3):
                    t0 = time.perf_counter()
                    predict(dets)
                    best = min(best, time.perf_counter() - t0)
                us = best / shots * 1e6
                rows.append(dict(distance=d, rounds=d, p=p, decoder=name.split(" ")[0],
                                 alpha=alpha, us_per_shot=round(us, 3),
                                 us_per_round=round(us / d, 4)))
                print(f"d={d} p={p:g} {name}: {us:.2f} us/shot, {us / d:.3f} us/round",
                      flush=True)
    with open(SPEED_CSV, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {SPEED_CSV}")


def decomposition(distances, ps, shots):
    """Correlated decoding on Stim's decomposition and on the basis one, same shots."""
    os.makedirs(RESULTS, exist_ok=True)
    rows = []
    for d in distances:
        for p in ps:
            c = circuit(d, p)
            dets, obs = c.compile_detector_sampler(seed=31 * d).sample(
                shots, separate_observables=True, bit_packed=True)
            truth = (obs[:, 0] & 1).astype(bool)
            for dec_name in ("stim", "basis"):
                decs = build(model(c, dec_name), alphas=(1.0,))
                for name, (alpha, predict) in decs.items():
                    errors = int(np.sum(predict(dets) != truth))
                    rows.append(dict(distance=d, rounds=d, p=p, decomposition=dec_name,
                                     decoder=name.split(" ")[0], alpha=alpha, shots=shots,
                                     errors=errors))
                print(f"d={d} p={p:g} {dec_name}: " + ", ".join(
                    f"{r['decoder']} {r['errors']}" for r in rows[-len(decs):]), flush=True)
    with open(DECOMPOSITION_CSV, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {DECOMPOSITION_CSV}")


def per_round(p_shot: float, rounds: int) -> float:
    """Logical error per round from the error of a memory of `rounds` rounds."""
    return 0.5 * (1 - (1 - 2 * p_shot) ** (1 / rounds))


def paired_ratio(n_x: int, n_ref: int, only_x: int, only_ref: int):
    """Ratio of error counts on the same shots, with a 1-sigma log-normal band."""
    if n_x == 0 or n_ref == 0:
        return math.nan, math.nan, math.nan
    r = n_x / n_ref
    s = math.sqrt((only_x + only_ref) / (n_x * n_ref))
    return r, r * math.exp(-s), r * math.exp(s)


def load_accuracy():
    with open(ACCURACY_CSV) as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for k in ("distance", "rounds", "shots", "errors", "only_this_vs_uf",
                  "only_uf_vs_this", "only_this_vs_pm", "only_pm_vs_this"):
            r[k] = int(r[k])
        r["p"] = float(r["p"])
        r["label"] = r["decoder"] if r["alpha"] in ("", "1.0") and r["decoder"] != "UF-corr" \
            else f"{r['decoder']} {float(r['alpha']):g}"
    return rows


def best_alpha(rows, d, p):
    """The alpha with the fewest errors on the shared shots."""
    cands = [r for r in rows if r["distance"] == d and r["p"] == p and r["decoder"] == "UF-corr"]
    return min(cands, key=lambda r: (r["errors"], -float(r["alpha"])))


def summary(rows):
    """Lambda (error suppression per +2 distance) and ratios, as printed tables."""
    ds = sorted({r["distance"] for r in rows})
    ps = sorted({r["p"] for r in rows})
    out = []
    for p in ps:
        for name in ("UF", "UF-corr 1", "PM", "PM-corr"):
            eps = {}
            for d in ds:
                sel = [r for r in rows if r["distance"] == d and r["p"] == p and r["label"] == name]
                if sel and sel[0]["errors"] > 0:
                    eps[d] = per_round(sel[0]["errors"] / sel[0]["shots"], d)
            lam = [eps[a] / eps[b] for a, b in zip(ds, ds[1:]) if a in eps and b in eps]
            out.append((p, name, eps, lam))
    return out


def plot():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = load_accuracy()
    ds = sorted({r["distance"] for r in rows})
    colors = dict(zip(ds, plt.cm.viridis(np.linspace(0.05, 0.8, len(ds)))))
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.5, 4.6))

    for d in ds:
        ps = sorted({r["p"] for r in rows if r["distance"] == d})
        for name, style, label in (("UF-corr 1", "o--", "correlated UF (α = 1)"),
                                   ("best", "o-", "regularized correlated UF (best α)"),
                                   ("PM-corr", "s:", "correlated PyMatching / PyMatching")):
            xs, ys, lo, hi = [], [], [], []
            for p in ps:
                at = [r for r in rows if r["distance"] == d and r["p"] == p]
                if name == "best":
                    x = best_alpha(rows, d, p)
                else:
                    x = next(r for r in at if r["label"] == name)
                if name == "PM-corr":
                    ref = next(r for r in at if r["label"] == "PM")
                    rr = paired_ratio(x["errors"], ref["errors"], x["only_this_vs_pm"],
                                      x["only_pm_vs_this"])
                else:
                    ref = next(r for r in at if r["label"] == "UF")
                    rr = paired_ratio(x["errors"], ref["errors"], x["only_this_vs_uf"],
                                      x["only_uf_vs_this"])
                if not math.isnan(rr[0]):
                    xs.append(p)
                    ys.append(rr[0])
                    lo.append(rr[0] - rr[1])
                    hi.append(rr[2] - rr[0])
            ax1.errorbar(xs, ys, yerr=[lo, hi], fmt=style, color=colors[d], ms=4, capsize=2,
                         label=f"d = {d}, {label}")
    ax1.axhline(1.0, color="k", lw=0.8)
    ax1.set_xscale("log")
    ax1.set_xlabel("physical error rate p (uniform circuit noise)")
    ax1.set_ylabel("logical errors relative to the uncorrelated decoder")
    ax1.set_title("Correlated decoding, same shots for every decoder")
    ax1.grid(True, which="both", alpha=0.3)
    ax1.legend(fontsize=6.5, ncol=1, loc="upper left")

    for d in ds:
        ps = sorted({r["p"] for r in rows if r["distance"] == d})
        for name, style, label in (("UF", "o-", "backlog UF"), ("best", "D-", "backlog correlated UF"),
                                   ("PM", "s--", "PyMatching"), ("PM-corr", "^--",
                                                                 "PyMatching correlated")):
            xs, ys = [], []
            for p in ps:
                at = [r for r in rows if r["distance"] == d and r["p"] == p]
                x = best_alpha(rows, d, p) if name == "best" else \
                    next(r for r in at if r["label"] == name)
                if x["errors"] > 0:
                    xs.append(p)
                    ys.append(per_round(x["errors"] / x["shots"], d))
            ax2.plot(xs, ys, style, color=colors[d], ms=4, lw=1.1,
                     label=label if d == ds[-1] else None)
        ax2.text(min(ps) * 0.92, ys[0] if ys else 1e-6, f"d={d}", fontsize=8,
                 ha="right", va="center", color=colors[d])
    ax2.set_xscale("log")
    ax2.set_yscale("log")
    ax2.set_xlabel("physical error rate p")
    ax2.set_ylabel("logical error per round")
    ax2.set_title("Logical error per round (memory of d rounds)")
    ax2.grid(True, which="both", alpha=0.3)
    ax2.legend(fontsize=8)
    fig.tight_layout()
    out = os.path.join(RESULTS, "correlated.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"wrote {out}")
    for p, name, eps, lam in summary(rows):
        print(f"p={p:g} {name:10s} eps/round " +
              " ".join(f"d{d}={e:.2e}" for d, e in eps.items()) +
              "  Lambda " + " ".join(f"{x:.2f}" for x in lam))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["accuracy", "decomposition", "speed", "plot"])
    ap.add_argument("--distances", type=int, nargs="+", default=None)
    ap.add_argument("--p", type=float, nargs="+", default=None)
    ap.add_argument("--target", type=int, default=300, help="errors wanted for plain UF")
    ap.add_argument("--max-shots", type=int, default=6_000_000)
    ap.add_argument("--shots", type=int, default=None,
                    help="speed mode (default 20000) and decomposition mode (default 10^6)")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    args = ap.parse_args()
    if args.mode == "accuracy":
        accuracy(args.distances or [3, 5, 7], args.p or [1e-3, 2e-3, 3e-3, 5e-3, 7e-3],
                 args.target, args.max_shots, args.workers)
        plot()
    elif args.mode == "decomposition":
        decomposition(args.distances or [3, 5], args.p or [1e-3, 3e-3], args.shots or 1_000_000)
    elif args.mode == "speed":
        speed(args.distances or [5, 9, 13, 17], args.p or [1e-3, 3e-3], args.shots or 20_000)
    else:
        plot()


if __name__ == "__main__":
    main()
