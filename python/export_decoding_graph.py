"""Export a decoding graph + sampled shots for the C++ command-line decoder.

The C++ CLI does not depend on Stim. This script freezes what it needs into
plain text files, using the same DEM conversion as the Python package:

  graph.txt            "N M" header, then one edge per line: "a b obs p"
  dets.txt             detection events, one shot per line (fired detector ids)
  obs_actual.txt       ground-truth observable flips from Stim
  preds_pymatching.txt PyMatching's predictions on the same shots
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pymatching

import backlog
from generate_circuits import repetition_code_circuit, surface_code_circuit


def write_graph(path: Path, edges: backlog.GraphEdges):
    with path.open("w") as f:
        f.write(f"{edges.num_detectors} {edges.num_observables}\n")
        for a, b, o, p in zip(edges.a, edges.b, edges.obs, edges.p):
            f.write(f"{a} {b} {o} {p:.17g}\n")


def write_rows_of_indices(path: Path, bool_matrix: np.ndarray):
    with path.open("w") as f:
        for row in bool_matrix:
            f.write(" ".join(map(str, np.flatnonzero(row).tolist())) + "\n")


def write_rows_of_bits(path: Path, bool_matrix: np.ndarray):
    with path.open("w") as f:
        for row in bool_matrix:
            f.write(" ".join("1" if v else "0" for v in row) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="repetition", choices=["repetition", "surface"])
    ap.add_argument("--distance", type=int, default=5)
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--p", type=float, default=0.03)
    ap.add_argument("--shots", type=int, default=20_000)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    out = Path(args.out) if args.out else (
        Path(__file__).resolve().parent.parent / "cpp" / "testdata")
    out.mkdir(parents=True, exist_ok=True)

    if args.code == "repetition":
        circuit = repetition_code_circuit(args.distance, args.rounds, args.p)
    else:
        circuit = surface_code_circuit(args.distance, args.rounds, args.p)

    dem = circuit.detector_error_model(decompose_errors=True)
    edges = backlog.dem_to_edges(dem)
    write_graph(out / "graph.txt", edges)

    dets, obs = circuit.compile_detector_sampler(seed=args.seed).sample(
        args.shots, separate_observables=True)
    preds = pymatching.Matching.from_detector_error_model(dem).decode_batch(dets).astype(bool)

    write_rows_of_indices(out / "dets.txt", dets)
    write_rows_of_bits(out / "obs_actual.txt", obs)
    write_rows_of_bits(out / "preds_pymatching.txt", preds)

    pm_ler = float(np.mean(np.any(preds != obs, axis=1)))
    print(f"code={args.code} d={args.distance} rounds={args.rounds} p={args.p}")
    print(f"  detectors={edges.num_detectors} observables={edges.num_observables} "
          f"edges={edges.num_edges} skipped_hyperedges={edges.skipped_hyperedges}")
    print(f"  shots={args.shots}  PyMatching logical error rate={pm_ler:.4e}")
    print(f"  wrote test data to {out}")


if __name__ == "__main__":
    main()
