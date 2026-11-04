"""Benchmark the C++ Union-Find decoder against PyMatching on the surface code.

For each (distance, p) this samples the same shots once, decodes them with
both PyMatching (weighted MWPM, the baseline) and our C++ Union-Find binary,
and records both logical error rates. The plot shows where our decoder stands
relative to the baseline, which is the honest number to put in a README.

Requires the C++ binary: cmake -S cpp -B cpp/build && cmake --build cpp/build
"""
from __future__ import annotations

import csv
import subprocess
import tempfile
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pymatching

from export_decoding_graph import dem_to_graph, write_graph, write_rows_of_indices
from generate_circuits import surface_code_circuit

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "cpp" / "build" / "backlog_decode"
RESULTS = ROOT / "results"

COLORS = {3: "#4C72B0", 5: "#C44E52", 7: "#55A868"}
MARKERS = {3: "o", 5: "s", 7: "^"}


def read_preds(path: Path, m: int) -> np.ndarray:
    rows = []
    with path.open() as f:
        for line in f:
            vals = [int(x) for x in line.split()]
            rows.append(vals if vals else [0] * m)
    return np.array(rows, dtype=bool)


def run_point(d: int, p: float, shots: int, seed: int):
    circuit = surface_code_circuit(distance=d, rounds=d, p=p)
    dem = circuit.detector_error_model(decompose_errors=True)
    n, m, edges, _ = dem_to_graph(dem)

    sampler = circuit.compile_detector_sampler(seed=seed)
    dets, obs = sampler.sample(shots, separate_observables=True)
    pm_pred = pymatching.Matching.from_detector_error_model(dem).decode_batch(dets)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        write_graph(tmp / "graph.txt", n, m, edges)
        write_rows_of_indices(tmp / "dets.txt", dets)
        res = subprocess.run(
            [str(BINARY), str(tmp / "graph.txt"), str(tmp / "dets.txt"),
             str(tmp / "preds.txt")],
            capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"decoder failed (exit {res.returncode}):\n"
                               f"{res.stdout}\n{res.stderr}")
        uf_pred = read_preds(tmp / "preds.txt", m)
        latency_line = next((l for l in res.stdout.splitlines()
                             if l.startswith("latency")), "")

    pm_ler = float(np.mean(np.any(pm_pred.astype(bool) != obs, axis=1)))
    uf_ler = float(np.mean(np.any(uf_pred != obs, axis=1)))
    return pm_ler, uf_ler, latency_line


def main():
    if not BINARY.exists():
        raise SystemExit(f"{BINARY} not found. Build the C++ decoder first.")

    distances = [3, 5, 7]
    ps = [0.001, 0.002, 0.003, 0.004, 0.005, 0.006]
    shots = 50_000

    RESULTS.mkdir(exist_ok=True)
    rows = []
    for d in distances:
        for i, p in enumerate(ps):
            pm_ler, uf_ler, lat = run_point(d, p, shots, seed=1000 * d + i)
            rows.append((d, p, shots, pm_ler, uf_ler))
            print(f"d={d} p={p:<6} PyMatching={pm_ler:.3e}  UF={uf_ler:.3e}  "
                  f"ratio={uf_ler / pm_ler if pm_ler else float('nan'):.2f}")

    with (RESULTS / "uf_vs_pymatching.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["d", "p", "shots", "pymatching_ler", "union_find_ler"])
        w.writerows(rows)

    fig, ax = plt.subplots(figsize=(7, 5))
    for d in distances:
        pts = [(p, pm, uf) for dd, p, _, pm, uf in rows if dd == d]
        xs = [p for p, _, _ in pts]
        ax.plot(xs, [pm for _, pm, _ in pts], color=COLORS[d],
                marker=MARKERS[d], linewidth=1.6, label=f"d = {d}  PyMatching")
        ax.plot(xs, [uf for _, _, uf in pts], color=COLORS[d],
                marker=MARKERS[d], linewidth=1.6, linestyle="--",
                markerfacecolor="white", label=f"d = {d}  Union-Find (C++)")
    ax.set_yscale("log")
    ax.set_xlabel("physical error rate  p")
    ax.set_ylabel("logical error rate per shot")
    ax.set_title("Unweighted Union-Find (C++) vs PyMatching, circuit-level noise")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(frameon=False, fontsize=8, ncol=2)
    fig.tight_layout()
    out = RESULTS / "uf_vs_pymatching.png"
    fig.savefig(out, dpi=150)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
