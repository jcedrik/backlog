"""Streaming decoding: what a sliding window costs, and whether one core keeps up.

Rotated surface code memory under circuit-level noise, weighted Union-Find.

accuracy  Windowed vs whole-shot decoding on the very same shots, as the
          buffer grows. Memory experiments of 10 d rounds, commit = d rounds.
          Paired comparison: only shots where the two disagree matter, so the
          error bars come from those (McNemar).

latency   Time to process each window with commit = buffer = d, from d = 3
          to 13, measured in C++. Then a queue simulation: syndrome rounds
          arrive every microsecond, windows are decoded one after the other on
          one core. If the decoder is fast enough, the delay between a window's
          data being complete and its commit stays bounded; if not, it grows
          without limit. That growth is the backlog problem.

    python3 streaming_benchmark.py accuracy
    python3 streaming_benchmark.py latency      # on an idle machine
    python3 streaming_benchmark.py plot
"""
from __future__ import annotations

import argparse
import csv
import math
import time
from pathlib import Path

import numpy as np

import backlog
from generate_circuits import surface_code_circuit

RESULTS = Path(__file__).resolve().parent.parent / "results"
ACCURACY_CSV = RESULTS / "streaming_accuracy.csv"
LATENCY_CSV = RESULTS / "streaming_latency.csv"
BACKLOG_CSV = RESULTS / "streaming_backlog.csv"
CYCLE_US = 1.0  # one QEC round on a superconducting chip


def best_time(f, reps=3):
    best = math.inf
    out = None
    for _ in range(reps):
        t = time.perf_counter()
        out = f()
        best = min(best, time.perf_counter() - t)
    return best, out


# ------------------------------------------------------------------ accuracy

def accuracy(shots: int, p: float):
    import pymatching
    rows = []
    for d in (3, 5, 7, 9):
        rounds = 10 * d
        circuit = surface_code_circuit(distance=d, rounds=rounds, p=p)
        dem = circuit.detector_error_model(decompose_errors=True)
        dets, obs = circuit.compile_detector_sampler(seed=1000 + d).sample(
            shots, separate_observables=True)
        packed = np.packbits(dets.astype(np.uint8), axis=1, bitorder="little")
        truth = np.packbits(obs.astype(np.uint8), axis=1, bitorder="little")

        whole = backlog.UnionFindDecoder.from_dem(dem)
        t_whole, pred_whole = best_time(lambda: whole.decode_batch_bit_packed(packed), reps=1)
        wrong_whole = np.any(pred_whole != truth, axis=1)
        pm = pymatching.Matching.from_detector_error_model(dem)
        pred_pm = pm.decode_batch(packed, bit_packed_shots=True, bit_packed_predictions=True)
        errors_pm = int(np.any(pred_pm != truth, axis=1).sum())

        for buffer in sorted({b for b in (0, 1, 2, 3, 5, 7, 9) if b <= d} | {d}):
            win = backlog.SlidingWindowDecoder.from_dem(dem, commit=d, buffer=buffer)
            t_win, pred = best_time(lambda: win.decode_batch_bit_packed(packed), reps=1)
            wrong = np.any(pred != truth, axis=1)
            row = {
                "d": d, "p": p, "rounds": rounds, "shots": shots,
                "commit": d, "buffer": buffer, "windows": win.num_windows,
                "errors_whole": int(wrong_whole.sum()),
                "errors_window": int(wrong.sum()),
                "window_only": int((wrong & ~wrong_whole).sum()),
                "whole_only": int((wrong_whole & ~wrong).sum()),
                "errors_pymatching": errors_pm,
                "us_per_shot_whole": t_whole / shots * 1e6,
                "us_per_shot_window": t_win / shots * 1e6,
            }
            rows.append(row)
            ratio = row["errors_window"] / row["errors_whole"]
            print(f"d={d} rounds={rounds} buffer={buffer:2d}: errors {row['errors_window']:5d} "
                  f"vs whole {row['errors_whole']:5d} ({ratio:.3f}x), "
                  f"time {row['us_per_shot_window'] / row['us_per_shot_whole']:.2f}x")
    write_csv(ACCURACY_CSV, rows)


# ------------------------------------------------------------------- latency

def simulate_queue(window_us: np.ndarray, commit: int, window_rounds: int):
    """Decode delay of each window when rounds arrive every CYCLE_US.

    Window k covers rounds [k * commit, k * commit + window_rounds) and can
    start once its last round has arrived, and once window k - 1 is done.
    Returns the delay between "data complete" and "commit done", per window.
    """
    k = np.arange(len(window_us))
    arrival = (k * commit + window_rounds) * CYCLE_US
    finish = np.empty_like(window_us)
    prev = -math.inf
    for i in range(len(window_us)):
        prev = max(arrival[i], prev) + window_us[i]
        finish[i] = prev
    return arrival, finish - arrival


def latency(shots: int):
    rows, traces = [], []
    for p in (0.001, 0.003):
        for d in (3, 5, 7, 9, 11, 13):
            rounds = 12 * d
            circuit = surface_code_circuit(distance=d, rounds=rounds, p=p)
            dem = circuit.detector_error_model(decompose_errors=True)
            dets = circuit.compile_detector_sampler(seed=2000 + d).sample(shots)
            packed = np.packbits(dets.astype(np.uint8), axis=1, bitorder="little")
            win = backlog.SlidingWindowDecoder.from_dem(dem, commit=d, buffer=d)
            win.decode_batch_bit_packed(packed[:100])  # warm up
            lat = win.window_latency_ns(packed) / 1000.0  # (shots, windows), us

            # Bulk windows only: the first one and the ones touching the final
            # layer have a different structure. A long stream is all bulk.
            last = win.num_layers - 1
            bulk = [i for i, (first, _, end) in enumerate(win.windows)
                    if first > 0 and end < last]
            samples = lat[:, bulk].reshape(-1)
            arrival, delay = simulate_queue(samples, d, 2 * d)
            per_round = samples / d
            row = {
                "d": d, "p": p, "commit": d, "buffer": d, "rounds": rounds, "shots": shots,
                "windows_measured": samples.size,
                "window_p50_us": float(np.percentile(samples, 50)),
                "window_p99_us": float(np.percentile(samples, 99)),
                "window_p999_us": float(np.percentile(samples, 99.9)),
                "window_mean_us": float(samples.mean()),
                "per_round_mean_us": float(per_round.mean()),
                "per_round_p99_us": float(np.percentile(per_round, 99)),
                "keeps_up": bool(per_round.mean() < CYCLE_US),
                "delay_p99_us": float(np.percentile(delay, 99)),
                "delay_max_us": float(delay.max()),
                "delay_final_us": float(delay[-1]),
                "stream_ms": float(arrival[-1] / 1000.0),
            }
            rows.append(row)
            print(f"p={p} d={d:2d}: window p50 {row['window_p50_us']:7.2f} us, "
                  f"p99 {row['window_p99_us']:7.2f} us | per committed round mean "
                  f"{row['per_round_mean_us']:.3f} us | delay max {row['delay_max_us']:9.1f} us"
                  f" | {'keeps up' if row['keeps_up'] else 'BACKLOG GROWS'}")
            # Downsampled trace for the figure: the worst delay in each bin.
            bins = np.array_split(np.arange(delay.size), 400)
            for b in bins:
                j = b[np.argmax(delay[b])]
                traces.append({"d": d, "p": p, "stream_ms": arrival[j] / 1000.0,
                               "delay_us": float(delay[j])})
    write_csv(LATENCY_CSV, rows)
    write_csv(BACKLOG_CSV, traces)
    with (RESULTS / "latency_machine.txt").open() as f:
        print("machine:", f.readline().strip())


# ---------------------------------------------------------------------- plot

def read_csv(path: Path):
    with path.open() as f:
        return [{k: float(v) if k not in ("keeps_up",) else v == "True"
                 for k, v in row.items()} for row in csv.DictReader(f)]


def plot():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {3: "#4C72B0", 5: "#C44E52", 7: "#55A868", 9: "#8172B3",
              11: "#CCB974", 13: "#64B5CD"}
    markers = {3: "o", 5: "s", 7: "^", 9: "D", 11: "v", 13: "P"}
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.6))

    acc = read_csv(ACCURACY_CSV)
    ax = axes[0]
    for d in sorted({int(r["d"]) for r in acc}):
        rs = sorted((r for r in acc if int(r["d"]) == d), key=lambda r: r["buffer"])
        x = [r["buffer"] / d for r in rs]
        y = [r["errors_window"] / r["errors_whole"] for r in rs]
        e = [math.sqrt(r["window_only"] + r["whole_only"]) / r["errors_whole"] for r in rs]
        ax.errorbar(x, y, yerr=e, color=colors[d], marker=markers[d], markersize=4,
                    capsize=2, linewidth=1.5, label=f"d = {d}")
    ax.axhline(1.0, color="#444", linewidth=1)
    ax.set_xlabel("buffer size / d  (commit = d rounds)")
    ax.set_ylabel("logical errors, windowed / whole-shot")
    p_acc = acc[0]["p"]
    ax.set_title(f"What the window costs (same shots, p = {p_acc * 100:.1f}%,\n"
                 "memory of 10 d rounds)", fontsize=11)
    ax.grid(True, alpha=0.25)
    ax.legend(frameon=False, fontsize=8, ncol=4, loc="upper center",
              bbox_to_anchor=(0.5, -0.16))

    traces = read_csv(BACKLOG_CSV)
    lat = read_csv(LATENCY_CSV)
    for ax, p in zip(axes[1:], (0.001, 0.003)):
        for d in sorted({int(r["d"]) for r in traces}):
            pts = [r for r in traces if int(r["d"]) == d and abs(r["p"] - p) < 1e-12]
            if not pts:
                continue
            info = next(r for r in lat if int(r["d"]) == d and abs(r["p"] - p) < 1e-12)
            style = "-" if info["keeps_up"] else "--"
            ax.plot([r["stream_ms"] for r in pts], [r["delay_us"] for r in pts],
                    linestyle=style, color=colors[d], linewidth=1.4,
                    label=f"d = {d}: {info['per_round_mean_us']:.2f} µs / round")
        ax.set_yscale("log")
        ax.set_xlabel("time since the stream started (ms)")
        ax.set_title(f"Does one core keep up with 1 µs rounds? p = {p * 100:.1f}%\n"
                     "worst decode delay per stretch of stream", fontsize=11)
        ax.grid(True, which="both", alpha=0.25)
        ax.legend(frameon=False, fontsize=8, title="mean decode time per committed round",
                  title_fontsize=8, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.16))
    axes[1].set_ylabel("delay from data complete to commit (µs)")
    fig.suptitle("Sliding-window decoding, rotated surface code, circuit-level noise, "
                 "commit = buffer = d", fontsize=12)
    fig.tight_layout()
    fig.savefig(RESULTS / "streaming.png", dpi=150, bbox_inches="tight")
    print(f"wrote {RESULTS / 'streaming.png'}")


def write_csv(path: Path, rows):
    RESULTS.mkdir(exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["accuracy", "latency", "plot"])
    ap.add_argument("--shots", type=int, default=None)
    ap.add_argument("--p", type=float, default=0.005, help="accuracy mode only")
    args = ap.parse_args()
    if args.mode == "accuracy":
        accuracy(args.shots or 20_000, args.p)
    elif args.mode == "latency":
        latency(args.shots or 4_000)
    else:
        plot()


if __name__ == "__main__":
    main()
