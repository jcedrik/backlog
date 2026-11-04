"""Monte-Carlo threshold sweep: Stim + PyMatching, parallelised with sinter.

This is the baseline every later decoder (C++ Union-Find, then the VHDL core)
has to be measured against. It sweeps physical error rate across several code
distances and records the logical error rate. Where the distance curves cross
is the threshold: below it, bigger codes help; above it, they hurt.

Run:
    python3 baseline_threshold.py            # default sweep, writes results/threshold_data.csv
    python3 baseline_threshold.py --quick    # tiny sweep for a smoke test
"""
from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

import sinter

from generate_circuits import surface_code_circuit

RESULTS = Path(__file__).resolve().parent.parent / "results"


def build_tasks(distances, ps, basis):
    for d in distances:
        for p in ps:
            circuit = surface_code_circuit(distance=d, rounds=d, p=p, basis=basis)
            yield sinter.Task(
                circuit=circuit,
                json_metadata={"d": d, "p": p, "rounds": d, "basis": basis},
            )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="tiny sweep, for checking the pipeline runs end to end")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--max-shots", type=int, default=50_000)
    ap.add_argument("--max-errors", type=int, default=300)
    ap.add_argument("--basis", default="z", choices=["z", "x"])
    ap.add_argument("--results-dir", default=None,
                    help="default: results/ (or results/quick/ with --quick, "
                         "so a smoke test never overwrites real data)")
    args = ap.parse_args()

    results = Path(args.results_dir) if args.results_dir else (
        RESULTS / "quick" if args.quick else RESULTS)

    if args.quick:
        distances = [3, 5]
        ps = [0.004, 0.006, 0.010]
        args.max_shots = 5_000
        args.max_errors = 100
    else:
        distances = [3, 5, 7]
        ps = [0.002, 0.003, 0.004, 0.005, 0.006, 0.008, 0.010, 0.012]

    tasks = list(build_tasks(distances, ps, args.basis))
    print(f"Collecting {len(tasks)} tasks on {args.workers} workers "
          f"(max_shots={args.max_shots:,}, max_errors={args.max_errors})")

    samples = sinter.collect(
        num_workers=args.workers,
        tasks=tasks,
        decoders=["pymatching"],
        max_shots=args.max_shots,
        max_errors=args.max_errors,
        print_progress=True,
    )

    results.mkdir(parents=True, exist_ok=True)
    out = results / "threshold_data.csv"
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["d", "p", "rounds", "shots", "errors",
                    "logical_error_rate", "ler_stderr"])
        for s in sorted(samples, key=lambda s: (s.json_metadata["d"],
                                                s.json_metadata["p"])):
            d = s.json_metadata["d"]
            p = s.json_metadata["p"]
            ler = s.errors / s.shots if s.shots else float("nan")
            # binomial standard error on the logical error rate
            stderr = (ler * (1 - ler) / s.shots) ** 0.5 if s.shots else float("nan")
            w.writerow([d, p, s.json_metadata["rounds"], s.shots, s.errors,
                        f"{ler:.6g}", f"{stderr:.6g}"])
            print(f"  d={d} p={p:<6} shots={s.shots:>7} errors={s.errors:>5} "
                  f"LER={ler:.4e}")

    print(f"\nWrote {out}")
    print(f"Next: python3 plot_threshold.py --results-dir {results}")


if __name__ == "__main__":
    main()
