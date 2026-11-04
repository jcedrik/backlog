"""Compare the C++ Union-Find decoder against PyMatching on the same shots.

Two things matter here, and they are different questions:

1. Logical error rate. UF (unweighted) is expected to be slightly worse than
   or equal to PyMatching (weighted MWPM). We just want them in the same ball-
   park, confirming the C++ decoder is actually decoding and not guessing.

2. Agreement rate. How often the two decoders predict the same observable.
   They are different algorithms, so <100% is normal and fine.

The hard correctness guarantee (every correction reproduces its syndrome) is
checked inside the C++ binary itself and printed there.
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
    import sys
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else TESTDATA
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
    print(f"UF / MWPM ratio:           {ratio:.3f}  (>=1 expected)")

    # Sanity gate for CI: UF should not be wildly worse than MWPM.
    if pm_ler > 0 and ratio > 2.0:
        raise SystemExit("FAIL: UF logical error rate is more than 2x MWPM.")
    print("OK")


if __name__ == "__main__":
    main()
