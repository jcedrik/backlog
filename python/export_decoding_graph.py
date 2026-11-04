"""Export a decoding graph + sampled shots so the C++ decoder can be tested.

The C++ Union-Find decoder does not depend on Stim or PyMatching. Instead, this
script freezes everything it needs into plain text files:

  graph.txt            the decoding graph (nodes, boundary, edges, obs masks)
  dets.txt             detection events, one shot per line (fired detector ids)
  obs_actual.txt       ground-truth observable flips from Stim (the truth)
  preds_pymatching.txt PyMatching's predictions on the same shots (the baseline)

The C++ side reads graph.txt + dets.txt, writes preds_cpp.txt, and we compare.
We default to the repetition code because it is graphlike and unweighted-
friendly, which is the honest first target for a from-scratch UF decoder.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pymatching
import stim

from generate_circuits import repetition_code_circuit, surface_code_circuit


def dem_to_graph(dem: stim.DetectorErrorModel):
    """Flatten a decomposed DEM into graphlike edges.

    Returns (num_detectors, num_observables, edges) where edges maps
    (a, b) -> observable bitmask and b == num_detectors means the boundary.
    Non-graphlike components (>2 detectors) are skipped and counted.
    """
    n = dem.num_detectors
    m = dem.num_observables
    boundary = n
    edges: dict[tuple[int, int], int] = {}
    skipped = 0

    for inst in dem.flattened():
        if inst.type != "error":
            continue
        components = [[[], 0]]  # list of [detector_ids, obs_mask]
        for t in inst.targets_copy():
            if t.is_separator():
                components.append([[], 0])
            elif t.is_relative_detector_id():
                components[-1][0].append(t.val)
            elif t.is_logical_observable_id():
                components[-1][1] |= (1 << t.val)
        for dets, obs in components:
            if len(dets) == 1:
                a, b = dets[0], boundary
            elif len(dets) == 2:
                a, b = dets[0], dets[1]
            else:
                skipped += 1
                continue
            key = (min(a, b), max(a, b))
            edges.setdefault(key, obs)
    return n, m, edges, skipped


def write_graph(path: Path, n: int, m: int, edges: dict):
    with path.open("w") as f:
        f.write(f"{n} {m}\n")
        for (a, b), obs in sorted(edges.items()):
            f.write(f"{a} {b} {obs}\n")


def write_rows_of_indices(path: Path, bool_matrix: np.ndarray):
    with path.open("w") as f:
        for row in bool_matrix:
            idx = np.nonzero(row)[0]
            f.write(" ".join(map(str, idx.tolist())) + "\n")


def write_rows_of_bits(path: Path, bool_matrix: np.ndarray):
    with path.open("w") as f:
        for row in bool_matrix:
            f.write(" ".join("1" if v else "0" for v in row) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="repetition",
                    choices=["repetition", "surface"])
    ap.add_argument("--distance", type=int, default=5)
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--p", type=float, default=0.03)
    ap.add_argument("--shots", type=int, default=20_000)
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
    n, m, edges, skipped = dem_to_graph(dem)
    write_graph(out / "graph.txt", n, m, edges)

    sampler = circuit.compile_detector_sampler()
    dets, obs = sampler.sample(args.shots, separate_observables=True)

    matching = pymatching.Matching.from_detector_error_model(dem)
    preds = matching.decode_batch(dets).astype(bool)

    write_rows_of_indices(out / "dets.txt", dets)
    write_rows_of_bits(out / "obs_actual.txt", obs)
    write_rows_of_bits(out / "preds_pymatching.txt", preds)

    pm_ler = float(np.mean(np.any(preds != obs, axis=1)))
    print(f"code={args.code} d={args.distance} rounds={args.rounds} p={args.p}")
    print(f"  detectors={n} observables={m} edges={len(edges)} "
          f"skipped_hyperedges={skipped}")
    print(f"  shots={args.shots}  PyMatching logical error rate={pm_ler:.4e}")
    print(f"  wrote test data to {out}")


if __name__ == "__main__":
    main()
