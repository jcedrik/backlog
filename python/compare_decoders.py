"""Compare the C++ command-line decoder against PyMatching on the same shots.

Reads the files written by export_decoding_graph.py (graph, shots, PyMatching's
predictions) and by backlog_decode (preds_cpp.txt), then reports:

1. Logical error rate of both decoders. Weighted Union-Find is expected close
   to PyMatching (MWPM); the check fails above --max-ratio times PyMatching.
2. Agreement rate: how often both predict the same observables. Different
   algorithms, so less than 100% is normal.

The hard guarantee (every correction reproduces its syndrome) is checked
inside the C++ binary itself, which exits non-zero if it ever fails.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

TESTDATA = Path(__file__).resolve().parent.parent / "cpp" / "testdata"


def load_bits(path: Path) -> np.ndarray:
    rows = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            rows.append([int(x) for x in line.split()] if line else [])
    width = max((len(r) for r in rows), default=0)
    out = np.zeros((len(rows), width), dtype=bool)
    for i, r in enumerate(rows):
        for j, v in enumerate(r):
            out[i, j] = bool(v)
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("testdata", nargs="?", default=str(TESTDATA))
    ap.add_argument("--max-ratio", type=float, default=1.5,
                    help="fail if UF's logical error rate exceeds this times PyMatching's")
    args = ap.parse_args()
    base = Path(args.testdata)
    actual = load_bits(base / "obs_actual.txt")
    pm = load_bits(base / "preds_pymatching.txt")
    cpp_path = base / "preds_cpp.txt"
    if not cpp_path.exists():
        raise SystemExit("preds_cpp.txt missing. Build and run the C++ decoder "
                         "first (see cpp/README.md).")
    cpp = load_bits(cpp_path)

    n = min(len(actual), len(pm), len(cpp))
    actual, pm, cpp = actual[:n], pm[:n], cpp[:n]

    pm_ler = float(np.mean(np.any(pm != actual, axis=1)))
    cpp_ler = float(np.mean(np.any(cpp != actual, axis=1)))
    agree = float(np.mean(np.all(pm == cpp, axis=1)))

    print(f"shots compared:            {n}")
    print(f"PyMatching logical err:    {pm_ler:.4e}")
    print(f"C++ Union-Find logical err:{cpp_ler:.4e}")
    print(f"decoder agreement:         {agree*100:.2f}%")
    ratio = cpp_ler / pm_ler if pm_ler else float("inf")
    print(f"UF / MWPM ratio:           {ratio:.3f}  (gate: {args.max_ratio})")

    if pm_ler > 0 and ratio > args.max_ratio:
        raise SystemExit(f"FAIL: UF logical error rate is more than {args.max_ratio}x MWPM.")
    print("OK")


if __name__ == "__main__":
    main()
