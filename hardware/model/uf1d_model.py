"""Golden reference model of the hardware Union-Find decoder (1D chain).

This is the cycle-faithful software twin of hardware/rtl/uf_decoder_1d.vhd.
It implements exactly the same growth rounds and the same peeling rule, so the
RTL must match it bit for bit: correction vector, logical flip, and number of
growth rounds (which is the latency in clock cycles).

Code model (code-capacity repetition code, bit flips only):
    N detectors D0..D(N-1), N+1 data qubits (code distance d = N + 1).
    Edge k <-> data qubit k.
      edge 0      : left boundary -- D0
      edge k      : D(k-1) -- D(k)          (1 <= k <= N-1)
      edge N      : D(N-1) -- right boundary
    The logical observable is read on data qubit 0, so obs = correction[0].

Usage:
    python3 uf1d_model.py selfcheck --n 8           # invariant + LER vs PyMatching
    python3 uf1d_model.py vectors --n 8 --out ../tb/vectors_n8.txt
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path


def cluster_info(defect, grown, n):
    """Per-vertex segment info, as computed combinationally in the RTL.

    L[v]  : XOR of defects from the start of v's segment up to v (inclusive)
    R[v]  : XOR of defects from v to the end of v's segment (inclusive)
    BL[v] : v's segment is connected to the left boundary
    BR[v] : v's segment is connected to the right boundary
    odd[v]: v's cluster has odd parity and touches no boundary
    """
    L = [0] * n
    BL = [0] * n
    for v in range(n):
        if v == 0:
            L[v] = defect[v]
            BL[v] = grown[0]
        elif grown[v]:
            L[v] = defect[v] ^ L[v - 1]
            BL[v] = BL[v - 1]
        else:
            L[v] = defect[v]
            BL[v] = 0
    R = [0] * n
    BR = [0] * n
    for v in range(n - 1, -1, -1):
        if v == n - 1:
            R[v] = defect[v]
            BR[v] = grown[n]
        elif grown[v + 1]:
            R[v] = defect[v] ^ R[v + 1]
            BR[v] = BR[v + 1]
        else:
            R[v] = defect[v]
            BR[v] = 0
    odd = [0] * n
    for v in range(n):
        par = L[v] ^ R[v] ^ defect[v]
        odd[v] = 1 if (par and not (BL[v] or BR[v])) else 0
    return L, R, BL, BR, odd


def peel(defect, grown, L, R, BL, n):
    """Combinational peeling rule (see hardware/README.md for the proof)."""
    corr = [0] * (n + 1)
    for k in range(n + 1):
        if not grown[k]:
            continue
        if k == 0:
            corr[0] = R[0]
        elif k == n:
            corr[n] = 0 if BL[n - 1] else L[n - 1]
        else:
            corr[k] = R[k] if BL[k - 1] else L[k - 1]
    return corr


def decode(syndrome, n):
    """Return (correction, obs_flip, rounds)."""
    defect = list(syndrome)
    grown = [0] * (n + 1)
    half = [0] * (n + 1)
    rounds = 0
    while True:
        L, R, BL, BR, odd = cluster_info(defect, grown, n)
        if not any(odd):
            break
        new_grown = grown[:]
        new_half = half[:]
        for k in range(n + 1):
            if grown[k]:
                continue
            inc = (odd[k - 1] if k >= 1 else 0) + (odd[k] if k <= n - 1 else 0)
            g = half[k] + inc
            new_grown[k] = 1 if g >= 2 else 0
            new_half[k] = 1 if g == 1 else 0
        grown, half = new_grown, new_half
        rounds += 1
    L, R, BL, BR, odd = cluster_info(defect, grown, n)
    corr = peel(defect, grown, L, R, BL, n)
    return corr, corr[0], rounds


def syndrome_of(errors, n):
    """Detector D(i) = data error on edge i XOR data error on edge i+1."""
    return [errors[i] ^ errors[i + 1] for i in range(n)]


def selfcheck(n, shots, p, seed):
    import numpy as np
    import pymatching

    rng = random.Random(seed)
    # Parity-check matrix of the code-capacity repetition code, same layout.
    H = np.zeros((n, n + 1), dtype=np.uint8)
    for i in range(n):
        H[i, i] = 1
        H[i, i + 1] = 1
    matching = pymatching.Matching(H)

    invariant_ok = 0
    uf_fail = 0
    pm_fail = 0
    max_rounds = 0
    total_rounds = 0
    for _ in range(shots):
        err = [1 if rng.random() < p else 0 for _ in range(n + 1)]
        synd = syndrome_of(err, n)
        corr, obs, rounds = decode(synd, n)
        if syndrome_of(corr, n) == synd:
            invariant_ok += 1
        residual = [e ^ c for e, c in zip(err, corr)]
        uf_fail += residual[0]
        pm_corr = matching.decode(np.array(synd, dtype=np.uint8))
        pm_fail += (err[0] ^ int(pm_corr[0]))
        max_rounds = max(max_rounds, rounds)
        total_rounds += rounds

    print(f"N={n} (d={n + 1}) p={p} shots={shots}")
    print(f"  invariant (correction reproduces syndrome): "
          f"{invariant_ok}/{shots}")
    print(f"  UF model logical error rate:   {uf_fail / shots:.4e}")
    print(f"  PyMatching logical error rate: {pm_fail / shots:.4e}")
    print(f"  growth rounds: mean {total_rounds / shots:.2f}, max {max_rounds}")
    if invariant_ok != shots:
        raise SystemExit("FAIL: invariant broken")


def bits_msb_first(bits):
    """VHDL (N-1 downto 0) reads the leftmost char as the highest index."""
    return "".join(str(b) for b in reversed(bits))


def write_vectors(n, out: Path, count, seed):
    rng = random.Random(seed)
    syndromes = []
    syndromes.append([0] * n)                      # nothing fired
    syndromes.append([1] * n)                      # everything fired
    syndromes.append([(i % 2) for i in range(n)])  # alternating
    for i in range(n):                             # every single defect
        s = [0] * n
        s[i] = 1
        syndromes.append(s)
    for i in range(n - 1):                         # every adjacent pair
        s = [0] * n
        s[i] = s[i + 1] = 1
        syndromes.append(s)
    for p in (0.05, 0.1, 0.2, 0.35):               # random physical errors
        for _ in range(count // 4):
            err = [1 if rng.random() < p else 0 for _ in range(n + 1)]
            syndromes.append(syndrome_of(err, n))

    out.parent.mkdir(parents=True, exist_ok=True)
    max_rounds = 0
    with out.open("w") as f:
        for s in syndromes:
            corr, obs, rounds = decode(s, n)
            max_rounds = max(max_rounds, rounds)
            f.write(f"{bits_msb_first(s)} {bits_msb_first(corr)} {obs} {rounds}\n")
    print(f"wrote {len(syndromes)} vectors for N={n} to {out} "
          f"(max rounds {max_rounds})")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("selfcheck")
    a.add_argument("--n", type=int, default=8)
    a.add_argument("--shots", type=int, default=20_000)
    a.add_argument("--p", type=float, default=0.08)
    a.add_argument("--seed", type=int, default=7)
    b = sub.add_parser("vectors")
    b.add_argument("--n", type=int, default=8)
    b.add_argument("--count", type=int, default=2000)
    b.add_argument("--seed", type=int, default=11)
    b.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.cmd == "selfcheck":
        selfcheck(args.n, args.shots, args.p, args.seed)
    else:
        write_vectors(args.n, Path(args.out), args.count, args.seed)


if __name__ == "__main__":
    main()
