"""Parallel window decoding: accuracy, real speedup, and cores needed for real time.

Rotated surface code memory under circuit-level noise, weighted Union-Find,
core windows committing d rounds with d rounds of buffer on each side, seams
of d rounds (Skoric et al. 2023, Tan et al. 2023).

accuracy  Whole-shot vs sliding-window vs parallel-window decoding, on the
          very same shots (memory of 10 d rounds, p = 0.5%).

speedup   Wall time per shot with 1 and 2 threads on this machine, and the
          work time of every window (best of 3 interleaved passes per shot).
          A scheduling model built from the window times predicts the
          2-thread time; checking that prediction against the real 2-thread
          run is what lets the model speak for more cores.

project   The model on a stream: rounds arrive every microsecond, core windows
          start once their data is in, seams once both neighbors committed,
          N cores take ready windows in order. How many cores keep the delay
          bounded (with 15% headroom, since the model was up to 12% optimistic
          on the real 2-core run), and what the backlog looks like with fewer.

    python3 parallel_benchmark.py accuracy
    python3 parallel_benchmark.py speedup       # on an idle machine
    python3 parallel_benchmark.py project       # uses the speedup data
    python3 parallel_benchmark.py plot
    python3 parallel_benchmark.py scaling     # real speedup up to every core here

scaling   Wall time per shot with 1, 2, 4, ... threads up to every core of the
          machine it runs on, against the model's prediction for each count.
          This is the check of the model beyond 2 cores. Results go to
          results/scaling/ (not tracked by git).
"""
from __future__ import annotations

import argparse
import csv
import heapq
import math
import os
import platform
from pathlib import Path

import numpy as np

import backlog
from generate_circuits import surface_code_circuit

RESULTS = Path(__file__).resolve().parent.parent / "results"
SCALING_DIR = RESULTS / "scaling"
ACCURACY_CSV = RESULTS / "parallel_accuracy.csv"
SPEEDUP_CSV = RESULTS / "parallel_speedup.csv"
WINDOWS_NPZ = RESULTS / "parallel_windows.npz"
PROJECTION_CSV = RESULTS / "parallel_projection.csv"
TRACE_CSV = RESULTS / "parallel_backlog.csv"
CYCLE_US = 1.0
REPS = 3
HEADROOM = 0.15  # the model was up to 12% optimistic against the real 2-core run
DISTANCES = (5, 7, 9, 11, 13, 15, 17)
PS = (0.001, 0.003)


def write_csv(path: Path, rows):
    RESULTS.mkdir(exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {path}")


def read_csv(path: Path):
    with path.open() as f:
        return [{k: float(v) for k, v in row.items()} for row in csv.DictReader(f)]


def packed_shots(circuit, shots, seed, with_obs=False):
    sampler = circuit.compile_detector_sampler(seed=seed)
    if with_obs:
        dets, obs = sampler.sample(shots, separate_observables=True)
        return (np.packbits(dets.astype(np.uint8), axis=1, bitorder="little"),
                np.packbits(obs.astype(np.uint8), axis=1, bitorder="little"))
    dets = sampler.sample(shots)
    return np.packbits(dets.astype(np.uint8), axis=1, bitorder="little")


# ------------------------------------------------------------------ accuracy

def accuracy(shots: int, p: float):
    rows = []
    for d in (3, 5, 7, 9):
        circuit = surface_code_circuit(distance=d, rounds=10 * d, p=p)
        dem = circuit.detector_error_model(decompose_errors=True)
        packed, truth = packed_shots(circuit, shots, seed=1000 + d, with_obs=True)
        decoders = {
            "whole": backlog.UnionFindDecoder.from_dem(dem),
            "sliding": backlog.SlidingWindowDecoder.from_dem(dem, commit=d, buffer=d),
            "parallel": backlog.ParallelWindowDecoder.from_dem(dem, commit=d, buffer=d,
                                                               seam=d, threads=2),
        }
        wrong = {name: np.any(dec.decode_batch_bit_packed(packed) != truth, axis=1)
                 for name, dec in decoders.items()}
        for name in ("sliding", "parallel"):
            rows.append({
                "d": d, "p": p, "rounds": 10 * d, "shots": shots, "decoder": name,
                "errors_whole": int(wrong["whole"].sum()),
                "errors": int(wrong[name].sum()),
                "only_this": int((wrong[name] & ~wrong["whole"]).sum()),
                "only_whole": int((wrong["whole"] & ~wrong[name]).sum()),
            })
            r = rows[-1]
            print(f"d={d}: {name:8s} {r['errors']:5d} errors vs whole {r['errors_whole']:5d} "
                  f"({r['errors'] / r['errors_whole']:.3f}x)")
    with ACCURACY_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {ACCURACY_CSV}")


# ------------------------------------------------------------ scheduling model

def phase_makespan(durations, workers):
    """Tasks taken in order by whichever worker is free first."""
    free = [0.0] * workers
    for t in durations:
        heapq.heappush(free, heapq.heappop(free) + t)
    return max(free)


def simulate_stream(cores, seams, n_cores, commit, buffer, seam):
    """Delay from 'data complete' to 'committed' for each period of a stream.

    Core window i is ready once its last buffered round has arrived; the seam
    between cores i and i + 1 is ready once both are committed. Event-driven
    list scheduling on n_cores: whenever a core is free, it takes the ready
    window of the oldest period (so periods complete in order, as a real-time
    decoder must), and no core sits idle while a window is ready.
    """
    period = commit + seam
    k = len(cores)
    release = (np.arange(k) * period + commit + buffer) * CYCLE_US
    free = [0.0] * n_cores
    ready, pending = [], []          # ready: (priority, kind, i); pending: (time, ...)
    next_core = 0
    finish_core = np.full(k, np.nan)
    delay = np.empty(k - 1)

    def admit(t):
        nonlocal next_core
        while next_core < k and release[next_core] <= t:
            heapq.heappush(ready, (release[next_core], 0, next_core))
            next_core += 1
        while pending and pending[0][0] <= t:
            _, prio, kind, i = heapq.heappop(pending)
            heapq.heappush(ready, (prio, kind, i))

    for _ in range(2 * k - 1):
        now = heapq.heappop(free)
        admit(now)
        if not ready:  # idle until something becomes ready
            now = min(release[next_core] if next_core < k else math.inf,
                      pending[0][0] if pending else math.inf)
            admit(now)
        _, kind, i = heapq.heappop(ready)
        end = now + (cores[i] if kind == 0 else seams[i])
        heapq.heappush(free, end)
        if kind == 1:
            delay[i] = end - release[i + 1]
            continue
        finish_core[i] = end
        for j in (i - 1, i):  # the seams on either side of core i
            if 0 <= j < k - 1 and not np.isnan(finish_core[j]) and not np.isnan(finish_core[j + 1]):
                heapq.heappush(pending, (max(finish_core[j], finish_core[j + 1]),
                                         release[j + 1], 1, j))
    return release[1:], delay


# -------------------------------------------------------------------- speedup

def speedup(shots: int):
    rows, arrays = [], {}
    for p in PS:
        for d in DISTANCES:
            rounds = 20 * d
            circuit = surface_code_circuit(distance=d, rounds=rounds, p=p)
            dem = circuit.detector_error_model(decompose_errors=True)
            packed = packed_shots(circuit, shots, seed=3000 + d)
            one = backlog.ParallelWindowDecoder.from_dem(dem, commit=d, buffer=d, seam=d, threads=1)
            two = backlog.ParallelWindowDecoder.from_dem(dem, commit=d, buffer=d, seam=d, threads=2)
            one.decode_batch_bit_packed(packed[:10])
            two.decode_batch_bit_packed(packed[:10])
            # Interleaved passes, best of REPS per shot: the same syndromes cost
            # the same work every time, so the minimum only strips machine noise.
            wall1 = np.full(shots, np.inf)
            wall2 = np.full(shots, np.inf)
            work = np.full((shots, one.num_windows), np.inf)  # one thread
            for _ in range(REPS):
                wall1 = np.minimum(wall1, one.shot_latency_ns(packed) / 1000.0)
                wall2 = np.minimum(wall2, two.shot_latency_ns(packed) / 1000.0)
                work = np.minimum(work, one.window_latency_ns(packed) / 1000.0)
            kinds = [w[0] for w in one.windows]
            core_idx = [i for i, k in enumerate(kinds) if k == "core"]
            seam_idx = [i for i, k in enumerate(kinds) if k == "seam"]
            # Model of the 2-thread run: both phases list-scheduled on 2 workers,
            # plus the sequential part measured with one thread.
            serial = np.maximum(wall1 - work.sum(axis=1), 0.0)
            model2 = np.array([phase_makespan(work[s, core_idx], 2) +
                               phase_makespan(work[s, seam_idx], 2) for s in range(shots)])
            model2 += serial
            # Bulk periods only (the first and the last two have other shapes).
            pairs = [(core_idx[i], seam_idx[i]) for i in range(1, len(core_idx) - 2)]
            arrays[f"core_{d}_{p}"] = work[:, [c for c, _ in pairs]].reshape(-1)
            arrays[f"seam_{d}_{p}"] = work[:, [s for _, s in pairs]].reshape(-1)
            row = {
                "d": d, "p": p, "rounds": rounds, "shots": shots,
                "windows_per_shot": len(kinds),
                "wall_1_thread_us": float(np.mean(wall1)),
                "wall_2_threads_us": float(np.mean(wall2)),
                "speedup": float(np.mean(wall1) / np.mean(wall2)),
                "model_2_threads_us": float(np.mean(model2)),
                "model_error": float(np.mean(model2) / np.mean(wall2) - 1.0),
                "work_per_committed_round_us": float(
                    (arrays[f"core_{d}_{p}"].mean() + arrays[f"seam_{d}_{p}"].mean()) / (2 * d)),
            }
            rows.append(row)
            print(f"p={p} d={d:2d}: 1 thread {row['wall_1_thread_us']:8.0f} us/shot, "
                  f"2 threads {row['wall_2_threads_us']:8.0f} us/shot, speedup "
                  f"{row['speedup']:.2f}x, model {row['model_2_threads_us']:8.0f} us "
                  f"({row['model_error'] * 100:+.1f}%), work/round "
                  f"{row['work_per_committed_round_us']:.2f} us")
    write_csv(SPEEDUP_CSV, rows)
    np.savez_compressed(WINDOWS_NPZ, **arrays)
    print(f"wrote {WINDOWS_NPZ}")


# -------------------------------------------------------------------- project

def project():
    data = np.load(WINDOWS_NPZ)
    rows, traces = [], []
    for p in PS:
        for d in DISTANCES:
            cores, seams = data[f"core_{d}_{p}"], data[f"seam_{d}_{p}"]
            work = cores.mean() + seams.mean()            # per period of 2 d rounds
            capacity = 2 * d * CYCLE_US                    # one core, one period
            minimum = max(1, math.ceil(work / capacity * 1.0001))
            needed = max(1, math.ceil(work * (1 + HEADROOM) / capacity))
            _, delay = simulate_stream(cores, seams, needed, d, d, d)
            rows.append({
                "d": d, "p": p, "work_per_committed_round_us": work / (2 * d),
                "cores_minimum": minimum,
                "cores_needed": needed,
                "utilization": work / (needed * capacity),
                "delay_p99_us": float(np.percentile(delay, 99)),
                "delay_max_us": float(delay.max()),
            })
            r = rows[-1]
            print(f"p={p} d={d:2d}: {r['work_per_committed_round_us']:.2f} us of work per round, "
                  f"{needed:2d} cores with headroom (bare minimum {minimum:2d}), busy "
                  f"{r['utilization'] * 100:.0f}%, delay p99 {r['delay_p99_us']:.0f} us, "
                  f"max {r['delay_max_us']:.0f} us")
    # Backlog traces at the largest distance, p = 0.3%.
    d, p = DISTANCES[-1], 0.003
    cores, seams = data[f"core_{d}_{p}"], data[f"seam_{d}_{p}"]
    for n in (1, 2, 4, 8, 16):
        t, delay = simulate_stream(cores, seams, n, d, d, d)
        for b in np.array_split(np.arange(delay.size), 300):
            j = b[np.argmax(delay[b])]
            traces.append({"d": d, "p": p, "cores": n, "stream_ms": t[j] / 1000.0,
                           "delay_us": float(delay[j])})
    write_csv(PROJECTION_CSV, rows)
    write_csv(TRACE_CSV, traces)


# ----------------------------------------------------------------------- plot

def plot():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(13, 9.5))
    blue, red, green, grey = "#4C72B0", "#C44E52", "#55A868", "#555555"

    # (a) accuracy
    ax = axes[0][0]
    acc = []
    with ACCURACY_CSV.open() as f:
        acc = list(csv.DictReader(f))
    for name, color, marker, shift in (("sliding", blue, "o", -0.12), ("parallel", red, "s", 0.12)):
        rs = [r for r in acc if r["decoder"] == name]
        ds = np.array([float(r["d"]) for r in rs])
        ratio = [float(r["errors"]) / float(r["errors_whole"]) for r in rs]
        err = [math.sqrt(float(r["only_this"]) + float(r["only_whole"])) / float(r["errors_whole"])
               for r in rs]
        ax.errorbar(ds + shift, ratio, yerr=err, color=color, marker=marker, linestyle="none",
                    capsize=3, markersize=6, label=f"{name} windows")
    ax.axhline(1.0, color=grey, linewidth=1)
    ax.set_xticks(sorted({int(float(r["d"])) for r in acc}))
    ax.set_xlabel("code distance d (memory of 10 d rounds)")
    ax.set_ylabel("logical errors / whole-shot decoding")
    ax.set_title("Accuracy: windows vs decoding everything at once\n"
                 "same shots, p = 0.5%, commit = buffer = seam = d", fontsize=11)
    ax.grid(True, alpha=0.25)
    ax.legend(frameon=False, fontsize=9)

    # (b) real speedup on this machine vs the model
    ax = axes[0][1]
    sp = read_csv(SPEEDUP_CSV)
    for p, color, marker in ((0.001, blue, "o"), (0.003, red, "s")):
        rs = [r for r in sp if abs(r["p"] - p) < 1e-12]
        ds = [r["d"] for r in rs]
        ax.plot(ds, [r["speedup"] for r in rs], color=color, marker=marker, linewidth=1.6,
                label=f"measured, p = {p * 100:.1f}%")
        ax.plot(ds, [r["wall_1_thread_us"] / r["model_2_threads_us"] for r in rs], color=color,
                marker=marker, markerfacecolor="white", linestyle="--", linewidth=1.2,
                label=f"model (ignores thread sync), p = {p * 100:.1f}%")
    ax.axhline(2.0, color=grey, linewidth=1, linestyle=":")
    ax.text(DISTANCES[0], 2.02, "ideal on 2 cores", fontsize=8, color=grey)
    ax.set_ylim(1.0, 2.15)
    ax.set_xticks(list(DISTANCES))
    ax.set_xlabel("code distance d (memory of 20 d rounds)")
    ax.set_ylabel("speedup, 2 threads vs 1")
    ax.set_title("Real speedup on this 2-core machine,\nand what the scheduling model predicts",
                 fontsize=11)
    ax.grid(True, alpha=0.25)
    ax.legend(frameon=False, fontsize=8, loc="lower right")

    # (c) cores needed
    ax = axes[1][0]
    pr = read_csv(PROJECTION_CSV)
    for p, color, marker in ((0.001, blue, "o"), (0.003, red, "s")):
        rs = [r for r in pr if abs(r["p"] - p) < 1e-12]
        ax.plot([r["d"] for r in rs], [r["cores_needed"] for r in rs], color=color,
                marker=marker, linewidth=1.8, drawstyle="steps-mid",
                label=f"p = {p * 100:.1f}%")
        for r in rs:
            ax.annotate(f"{int(r['cores_needed'])}", (r["d"], r["cores_needed"]),
                        textcoords="offset points", xytext=(0, 6), ha="center", fontsize=8,
                        color=color)
    ax.set_yscale("log", base=2)
    ax.set_ylim(0.8, 24)
    ax.set_yticks([1, 2, 4, 8, 16])
    ax.set_yticklabels(["1", "2", "4", "8", "16"])
    ax.set_xticks(list(DISTANCES))
    ax.set_xlabel("code distance d")
    ax.set_ylabel("cores to keep up with 1 µs rounds")
    ax.set_title("Cores needed for real time, with 15% headroom\n"
                 "(model from measured window times)", fontsize=11)
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(frameon=False, fontsize=9)

    # (d) backlog vs cores
    ax = axes[1][1]
    tr = read_csv(TRACE_CSV)
    d_tr, p_tr = int(tr[0]["d"]), tr[0]["p"]
    cmap = plt.get_cmap("viridis")
    counts = sorted({int(r["cores"]) for r in tr})
    need = next(int(r["cores_needed"]) for r in pr
                if int(r["d"]) == d_tr and abs(r["p"] - p_tr) < 1e-12)
    for i, n in enumerate(counts):
        pts = [r for r in tr if int(r["cores"]) == n]
        ax.plot([r["stream_ms"] for r in pts], [r["delay_us"] for r in pts],
                color=cmap(i / max(1, len(counts) - 1)), linewidth=1.5,
                linestyle="-" if n >= need else "--", label=f"{n} core{'s' if n > 1 else ''}")
    ax.set_yscale("log")
    ax.set_xlabel("time since the stream started (ms)")
    ax.set_ylabel("delay from data complete to commit (µs)")
    ax.set_title(f"The backlog disappears with enough cores\nd = {d_tr}, p = {p_tr * 100:.1f}%, "
                 "1 µs rounds (model)", fontsize=11)
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(frameon=False, fontsize=8, ncol=2)

    fig.suptitle("Parallel window decoding, rotated surface code, circuit-level noise",
                 fontsize=13)
    fig.tight_layout()
    fig.savefig(RESULTS / "parallel.png", dpi=150)
    print(f"wrote {RESULTS / 'parallel.png'}")

# -------------------------------------------------------------------- scaling

def cpu_name() -> str:
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown CPU"


def thread_counts():
    n = os.cpu_count() or 1
    counts = [1]
    while counts[-1] * 2 < n:
        counts.append(counts[-1] * 2)
    if n > 1:
        counts.append(n)
    return counts


def scaling(shots: int):
    counts = thread_counts()
    print(f"{cpu_name()}, {os.cpu_count()} hardware threads: measuring {counts}")
    rows = []
    for p in PS:
        for d in (9, 13, 17):
            circuit = surface_code_circuit(distance=d, rounds=20 * d, p=p)
            dem = circuit.detector_error_model(decompose_errors=True)
            packed = packed_shots(circuit, shots, seed=3000 + d)
            dec = {t: backlog.ParallelWindowDecoder.from_dem(dem, commit=d, buffer=d, seam=d,
                                                             threads=t) for t in counts}
            for x in dec.values():
                x.decode_batch_bit_packed(packed[:10])
            wall = {t: np.full(shots, np.inf) for t in counts}
            work = np.full((shots, dec[1].num_windows), np.inf)
            for _ in range(REPS):  # interleaved passes, best of REPS per shot
                for t in counts:
                    wall[t] = np.minimum(wall[t], dec[t].shot_latency_ns(packed) / 1000.0)
                work = np.minimum(work, dec[1].window_latency_ns(packed) / 1000.0)
            kinds = [w[0] for w in dec[1].windows]
            core_idx = [i for i, k in enumerate(kinds) if k == "core"]
            seam_idx = [i for i, k in enumerate(kinds) if k == "seam"]
            serial = np.maximum(wall[1] - work.sum(axis=1), 0.0)
            base = float(np.mean(wall[1]))
            for t in counts:
                model = serial + np.array([phase_makespan(work[s, core_idx], t) +
                                           phase_makespan(work[s, seam_idx], t)
                                           for s in range(shots)])
                rows.append({"d": d, "p": p, "threads": t, "wall_us": float(np.mean(wall[t])),
                             "speedup": base / float(np.mean(wall[t])),
                             "model_speedup": base / float(np.mean(model))})
            line = "  ".join(f"{r['threads']:>2d}t {r['speedup']:5.2f}x (model {r['model_speedup']:5.2f}x)"
                             for r in rows if r["d"] == d and r["p"] == p)
            print(f"p={p} d={d:2d}: {line}")
    SCALING_DIR.mkdir(parents=True, exist_ok=True)
    out = SCALING_DIR / "scaling.csv"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with (SCALING_DIR / "machine.txt").open("w") as f:
        f.write(f"{cpu_name()}\n{os.cpu_count()} hardware threads\n{platform.platform()}\n")
    plot_scaling()
    print(f"wrote {out} and {SCALING_DIR / 'scaling.png'}: paste the lines above to compare")


def plot_scaling(folder: Path = SCALING_DIR):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = read_csv(folder / "scaling.csv")
    counts = sorted({int(r["threads"]) for r in rows})
    colors = {9: "#4C72B0", 13: "#C44E52", 17: "#55A868"}
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), sharey=True)
    for ax, p in zip(axes, PS):
        for d, color in colors.items():
            rs = sorted((r for r in rows if int(r["d"]) == d and abs(r["p"] - p) < 1e-12),
                        key=lambda r: r["threads"])
            if not rs:
                continue
            t = [r["threads"] for r in rs]
            ax.plot(t, [r["speedup"] for r in rs], color=color, marker="o", linewidth=1.8,
                    label=f"d = {d}, measured")
            ax.plot(t, [r["model_speedup"] for r in rs], color=color, marker="o",
                    markerfacecolor="white", linestyle="--", linewidth=1.2,
                    label=f"d = {d}, model")
        ax.plot(counts, counts, color="#555555", linestyle=":", linewidth=1, label="ideal")
        ax.set_xscale("log", base=2)
        ax.set_yscale("log", base=2)
        ax.set_xticks(counts)
        ax.set_xticklabels([str(c) for c in counts])
        low = min(r["speedup"] for r in rows)
        yticks = ([0.5] if low < 1 else []) + counts
        ax.set_yticks(yticks)
        ax.set_yticklabels([f"{y:g}" for y in yticks])
        ax.set_xlabel("threads")
        ax.set_title(f"p = {p * 100:.1f}%", fontsize=11)
        ax.grid(True, which="both", alpha=0.25)
    axes[0].set_ylabel("speedup vs 1 thread")
    axes[0].legend(frameon=False, fontsize=8)
    machine = (folder / "machine.txt").read_text().splitlines()
    fig.suptitle(f"Parallel windows, real speedup vs threads: {machine[0]}, {machine[1]}",
                 fontsize=11)
    fig.tight_layout()
    fig.savefig(folder / "scaling.png", dpi=150)



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["accuracy", "speedup", "project", "plot", "scaling",
                                     "scaling-plot"])
    ap.add_argument("--shots", type=int, default=None)
    ap.add_argument("--dir", default=str(SCALING_DIR), help="scaling-plot: results folder")
    args = ap.parse_args()
    if args.mode == "accuracy":
        accuracy(args.shots or 20_000, 0.005)
    elif args.mode == "speedup":
        speedup(args.shots or 300)
    elif args.mode == "project":
        project()
    elif args.mode == "scaling":
        scaling(args.shots or 300)
    elif args.mode == "scaling-plot":
        plot_scaling(Path(args.dir))
    else:
        plot()


if __name__ == "__main__":
    main()
