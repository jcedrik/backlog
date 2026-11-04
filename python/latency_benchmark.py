"""Decoding latency of the weighted Union-Find decoder vs PyMatching.

For each code distance d (rounds = d) and physical error rate p, this measures:
  - backlog UF: per-shot latency of the C++ decode call (p50, p99, p99.9),
    timed inside C++ so Python overhead is excluded;
  - mean decode time per shot in batch mode, for both decoders, which is the
    fair head-to-head number.

Dividing by the number of rounds gives decode time per round of syndrome data.
This is a throughput measure on whole memory experiments, not streaming
latency: a real-time decoder works on a sliding window as rounds arrive.

Run on an otherwise idle machine:
    python3 latency_benchmark.py
"""
from __future__ import annotations

import csv
import platform
import time
from pathlib import Path

import numpy as np
import pymatching

import backlog
from generate_circuits import surface_code_circuit

RESULTS = Path(__file__).resolve().parent.parent / "results"
DISTANCES = [3, 5, 7, 9, 11, 13]
PS = [0.001, 0.003]
SHOTS = 20_000


def best_of(f, reps=3):
    times = []
    for _ in range(reps):
        t = time.perf_counter()
        f()
        times.append(time.perf_counter() - t)
    return min(times)


def cpu_name() -> str:
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown CPU"


def main():
    RESULTS.mkdir(exist_ok=True)
    rows = []
    for p in PS:
        for d in DISTANCES:
            circuit = surface_code_circuit(distance=d, rounds=d, p=p)
            dem = circuit.detector_error_model(decompose_errors=True)
            dets, _ = circuit.compile_detector_sampler(seed=100 * d).sample(
                SHOTS, separate_observables=True)
            packed = np.packbits(dets.astype(np.uint8), axis=1, bitorder="little")

            uf = backlog.UnionFindDecoder.from_dem(dem, weighted=True)
            pm = pymatching.Matching.from_detector_error_model(dem)
            uf.decode_batch_bit_packed(packed[:200])  # warm up
            lat = uf.latency_ns(packed) / 1000.0      # microseconds per shot
            uf_mean = best_of(lambda: uf.decode_batch_bit_packed(packed)) / SHOTS * 1e6
            pm.decode_batch(packed[:200], bit_packed_shots=True, bit_packed_predictions=True)
            pm_mean = best_of(lambda: pm.decode_batch(
                packed, bit_packed_shots=True, bit_packed_predictions=True)) / SHOTS * 1e6

            row = {
                "p": p, "d": d, "rounds": d, "shots": SHOTS,
                "uf_p50_us": float(np.percentile(lat, 50)),
                "uf_p99_us": float(np.percentile(lat, 99)),
                "uf_p999_us": float(np.percentile(lat, 99.9)),
                "uf_batch_mean_us": uf_mean,
                "pm_batch_mean_us": pm_mean,
            }
            rows.append(row)
            print(f"p={p:<6} d={d:<3} UF p50 {row['uf_p50_us']:7.2f} us  p99 {row['uf_p99_us']:7.2f} us"
                  f"  | batch mean UF {uf_mean:7.2f} us  PyMatching {pm_mean:7.2f} us"
                  f"  ({pm_mean / uf_mean:.2f}x)")

    out = RESULTS / "latency.csv"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with (RESULTS / "latency_machine.txt").open("w") as f:
        f.write(f"{cpu_name()}\nsingle thread, {SHOTS} shots per point\n")
    plot(rows)
    print(f"wrote {out}")


def plot(rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, len(PS), figsize=(12, 4.6), sharey=True)
    for ax, p in zip(axes, PS):
        sub = [r for r in rows if r["p"] == p]
        ds = [r["d"] for r in sub]
        per_round = lambda key: [r[key] / r["rounds"] for r in sub]
        ax.fill_between(ds, per_round("uf_p50_us"), per_round("uf_p99_us"),
                        color="#4C72B0", alpha=0.18, label="backlog UF, p50 to p99")
        ax.plot(ds, per_round("uf_batch_mean_us"), color="#4C72B0", marker="o",
                linewidth=1.8, label="backlog UF, mean")
        ax.plot(ds, per_round("pm_batch_mean_us"), color="#C44E52", marker="s",
                linewidth=1.8, label="PyMatching, mean")
        ax.axhline(1.0, color="#555", linestyle="--", linewidth=1)
        ax.text(ds[0], 1.08, "1 µs: one superconducting QEC cycle", fontsize=8, color="#555")
        ax.set_yscale("log")
        ax.set_xticks(ds)
        ax.set_xlabel("code distance d (rounds = d)")
        ax.set_title(f"p = {p * 100:.1f}%")
        ax.grid(True, which="both", alpha=0.25)
    axes[0].set_ylabel("decode time per round of syndrome data (µs)")
    axes[0].legend(frameon=False, fontsize=8, loc="upper left")
    fig.suptitle("Single-thread CPU decoding time, rotated surface code, circuit-level noise",
                 fontsize=11)
    fig.tight_layout()
    fig.savefig(RESULTS / "latency.png", dpi=150)


if __name__ == "__main__":
    main()
