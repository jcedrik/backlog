"""Decode Google's surface code experiments with backlog.

The data is Google Quantum AI's, for "Quantum error correction below the
surface code threshold" (Nature 638, 920, 2025): Zenodo record
10.5281/zenodo.13273331, archive google_105Q_surface_code_d3_d5_d7.zip
(5.7 GB, md5 21fa6ad35b395d838ebcdbc92e364a12, CC-BY-4.0). Unzipped, every
experiment is a folder <root>/<patch>/<basis>/r<rounds>/ with the executed
circuit (circuit_ideal.stim), Google's noise model of it
(circuit_noisy_si1000_p1.stim), the recorded detection events
(detection_events.b8) and the measured logical flips (obs_flips_actual.b8).
Patches are named d<distance>_at_q<row>_<col>: nine distance-3 codes, four
distance-5 codes and the distance-7 code, 10 to 250 rounds each.

Every experiment is decoded with PyMatching and with backlog, plain and
correlated, on two decoding graphs:

  si1000  Google's noise model of the circuit, as shipped;
  pij     the same graph with every edge probability measured from the
          detection events themselves (the p_ij method of Google's 2021
          repetition code paper, Supplementary Information): the two-point
          correlation of each pair of detectors gives its edge, and each
          detector's marginal gives its boundary edge. Correlated decoding
          keeps the si1000 model's conditional probabilities on those edges.

For each experiment and decoder this records the logical errors, the logical
error per round eps = (1 - (1 - 2 P_L)^(1/rounds)) / 2 and the decoding time
per shot. --plot fits eps per distance across rounds and patches, prints the
error suppression factor Lambda = eps_d / eps_(d+2), and draws the figure.

    python python/google_data.py data/google       # writes results/google/google_data.csv
    python python/google_data.py data/google --resume --patches d7_at_q6_7   # add what is missing
    python python/google_data.py --plot            # results/google/google_data.png and the table

The CSV is written after every experiment, so an interrupted run keeps what
it did and --resume carries on from there (a d = 7 experiment takes minutes,
the whole dataset hours).

Google's own decoding results (decoding_results/ in the dataset), when
present, are scored on the same shots and listed with dem = google.
"""
from __future__ import annotations

import argparse
import csv
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pymatching
import stim

import backlog
from backlog.dem import GraphEdges, dem_correlations, dem_to_edges, detector_basis

RESULTS = Path(__file__).resolve().parent.parent / "results" / "google"
CSV = RESULTS / "google_data.csv"
PNG = RESULTS / "google_data.png"
FIELDS = ["patch", "distance", "basis", "rounds", "shots", "dem", "decoder", "errors",
          "p_logical", "eps_round", "eps_err", "ns_per_shot", "edges", "skipped_hyperedges",
          "split"]
DECODERS = ["pymatching", "pymatching-corr", "uf", "uf-corr", "uf-corr-reg"]
ALPHA_REG = 0.5   # alpha*(p) of arXiv:2610.26785 at p = 0.1%, about Google's error rates
P_FLOOR = 1e-6    # an edge the data never shows still exists
GOOGLE_EPS7 = 0.00143  # Google's decoder at d = 7, 0.143% per round (Nature 638, 920, 2025)


@dataclass(frozen=True)
class Experiment:
    patch: str
    distance: int
    basis: str
    rounds: int
    path: Path


def discover(root: Path, patches=None, bases=None, rounds=None) -> list[Experiment]:
    """Every folder r<rounds>/ under root that holds detection events."""
    out = []
    for events in sorted(root.rglob("detection_events.b8")):
        r_dir = events.parent
        m = re.fullmatch(r"r(\d+)", r_dir.name)
        if not m:
            continue
        basis_dir = r_dir.parent
        basis = basis_dir.name if re.fullmatch(r"[XZ]", basis_dir.name) else "?"
        patch_dir = basis_dir.parent if basis != "?" else basis_dir
        patch = str(patch_dir.relative_to(root)) if patch_dir != root else patch_dir.name
        d = re.search(r"d(\d+)", patch)
        if not d:
            continue
        exp = Experiment(patch, int(d.group(1)), basis, int(m.group(1)), r_dir)
        if patches and exp.patch not in patches:
            continue
        if bases and exp.basis not in bases:
            continue
        if rounds and exp.rounds not in rounds:
            continue
        out.append(exp)
    return out


def read_bits(path: Path, num_bits: int, *, bit_packed: bool) -> np.ndarray:
    fmt = path.suffix.lstrip(".")
    return stim.read_shot_data_file(path=str(path), format=fmt, num_measurements=num_bits,
                                    bit_packed=bit_packed)


def load(exp: Experiment, max_shots: int | None):
    """(ideal circuit, noisy circuit or None, packed detection events, logical flips)."""
    ideal = exp.path / "circuit_ideal.stim"
    noisy_candidates = sorted(exp.path.glob("circuit_noisy*.stim"))
    preferred = [c for c in noisy_candidates if c.name == "circuit_noisy_si1000_p1.stim"]
    noisy_path = (preferred or noisy_candidates or [None])[0]
    circuit = stim.Circuit.from_file(str(ideal if ideal.exists() else noisy_path))
    noisy = stim.Circuit.from_file(str(noisy_path)) if noisy_path else None
    n = circuit.num_detectors
    events = read_bits(exp.path / "detection_events.b8", n, bit_packed=True)
    obs_path = next((exp.path / f"obs_flips_actual.{ext}" for ext in ("b8", "01")
                     if (exp.path / f"obs_flips_actual.{ext}").exists()), None)
    if obs_path is None:
        raise FileNotFoundError(f"{exp.path}: no obs_flips_actual.b8 or .01")
    obs = read_bits(obs_path, 1, bit_packed=False)[:, 0].astype(bool)
    shots = min(len(events), len(obs), max_shots or len(events))
    return circuit, noisy, events[:shots], obs[:shots]


# ---------------------------------------------------------------- decoding graphs

def si1000_dem(noisy: stim.Circuit) -> stim.DetectorErrorModel:
    return noisy.detector_error_model(decompose_errors=True, ignore_decomposition_failures=True,
                                      flatten_loops=True)


def split_by_basis(dem: stim.DetectorErrorModel):
    """The X/Z split of the model, and how it was obtained ("basis" or "as-is")."""
    try:
        return backlog.decompose_by_basis(dem, detector_basis(dem)), "basis"
    except ValueError:
        return dem, "as-is"


def unpack(events: np.ndarray, n: int) -> np.ndarray:
    return np.unpackbits(events, axis=1, count=n, bitorder="little").astype(bool)


def calibrate_pij(edges: GraphEdges, events: np.ndarray, chunk: int = 512) -> GraphEdges:
    """Edge probabilities measured from the detection events, on the model's graph.

    For detectors i and j with detection events x_i, x_j in {0, 1}, an edge
    flipping both has probability
        p_ij = 1/2 - 1/2 sqrt(1 - 4 (<x_i x_j> - <x_i><x_j>) / (1 - 2<x_i> - 2<x_j> + 4<x_i x_j>)),
    and a detector's boundary edge follows from its marginal once its other
    edges are known: 1 - 2<x_i> = (1 - 2 p_i) prod_j (1 - 2 p_ij).
    """
    n = edges.num_detectors
    pairs = edges.b < n
    pa, pb = edges.a[pairs], edges.b[pairs]
    marginal = np.zeros(n)
    joint = np.zeros(pa.shape[0])
    shots = events.shape[0]
    for start in range(0, shots, chunk):
        x = unpack(events[start:start + chunk], n)
        marginal += x.sum(axis=0)
        joint += (x[:, pa] & x[:, pb]).sum(axis=0)
    marginal /= shots
    joint /= shots
    ma, mb = marginal[pa], marginal[pb]
    denom = 1.0 - 2.0 * ma - 2.0 * mb + 4.0 * joint
    with np.errstate(divide="ignore", invalid="ignore"):
        inside = 1.0 - 4.0 * (joint - ma * mb) / denom
        p_pair = 0.5 - 0.5 * np.sqrt(np.clip(inside, 0.0, None))
    p_pair = np.where((denom > 0) & np.isfinite(p_pair), p_pair, P_FLOOR)
    p_pair = np.clip(p_pair, P_FLOOR, 0.5 - P_FLOOR)
    # boundary edges: what the marginal leaves unexplained
    log_rest = np.zeros(n)
    np.add.at(log_rest, pa, np.log1p(-2.0 * p_pair))
    np.add.at(log_rest, pb, np.log1p(-2.0 * p_pair))
    p = np.empty(edges.num_edges)
    p[pairs] = p_pair
    bd = edges.a[~pairs]
    ratio = (1.0 - 2.0 * marginal[bd]) / np.exp(log_rest[bd])
    p[~pairs] = np.clip(0.5 * (1.0 - ratio), P_FLOOR, 0.5 - P_FLOOR)
    return GraphEdges(n, edges.num_observables, edges.a, edges.b, edges.obs, p, 0, 0)


def edges_to_dem(edges: GraphEdges) -> stim.DetectorErrorModel:
    lines = []
    for a, b, obs, p in zip(edges.a, edges.b, edges.obs, edges.p):
        targets = [f"D{a}"] + ([f"D{b}"] if b < edges.num_detectors else [])
        targets += [f"L{k}" for k in range(edges.num_observables) if int(obs) >> k & 1]
        lines.append(f"error({p:.12g}) " + " ".join(targets))
    return stim.DetectorErrorModel("\n".join(lines))


# ---------------------------------------------------------------- decoders

def build_decoders(dem_mode: str, dem_split: stim.DetectorErrorModel | None,
                   edges: GraphEdges, dem_graph: stim.DetectorErrorModel):
    """name -> callable(packed events) -> bool predictions of observable 0."""
    out = {}

    def pm(matching, correlations=False):
        def run(events):
            pred = matching.decode_batch(events, bit_packed_shots=True, bit_packed_predictions=True,
                                         enable_correlations=correlations)
            return (pred[:, 0] & 1).astype(bool)
        return run

    def uf(decoder):
        def run(events):
            return (decoder.decode_batch_bit_packed(events)[:, 0] & 1).astype(bool)
        return run

    out["pymatching"] = pm(pymatching.Matching.from_detector_error_model(dem_graph))
    if dem_mode == "si1000" and dem_split is not None:
        out["pymatching-corr"] = pm(pymatching.Matching.from_detector_error_model(
            dem_split, enable_correlations=True), correlations=True)
    out["uf"] = uf(backlog.UnionFindDecoder(edges))
    if dem_split is not None:
        corr = dem_correlations(dem_split, edges)
        out["uf-corr"] = uf(backlog.UnionFindDecoder(edges, correlations=corr, alpha=1.0))
        out["uf-corr-reg"] = uf(backlog.UnionFindDecoder(edges, correlations=corr, alpha=ALPHA_REG))
    return out


def eps_per_round(p_logical: float, rounds: int) -> float:
    return 0.5 * (1.0 - (1.0 - 2.0 * min(p_logical, 0.5)) ** (1.0 / rounds))


def eps_error(p_logical: float, rounds: int, shots: int) -> float:
    dp = math.sqrt(max(p_logical * (1 - p_logical), 1.0 / shots) / shots)
    return dp * (1.0 - 2.0 * min(p_logical, 0.499)) ** (1.0 / rounds - 1.0) / rounds


def google_results(exp: Experiment, obs: np.ndarray) -> list[tuple[str, int]]:
    """(name, errors) of every prediction file Google shipped with the experiment."""
    out = []
    for f in sorted((exp.path / "decoding_results").rglob("*")):
        if f.suffix not in (".b8", ".01") or "predict" not in f.name:
            continue
        try:
            pred = read_bits(f, 1, bit_packed=False)[:, 0].astype(bool)
        except Exception:  # noqa: BLE001  a file of another shape
            continue
        if len(pred) < len(obs):
            continue
        name = str(f.relative_to(exp.path / "decoding_results").with_suffix(""))
        out.append((name, int(np.count_nonzero(pred[:len(obs)] != obs))))
    return out


def decode_experiment(exp: Experiment, dem_modes, max_shots, google: bool, log=print) -> list[dict]:
    circuit, noisy, events, obs = load(exp, max_shots)
    shots = len(obs)
    rows = []
    base = dict(patch=exp.patch, distance=exp.distance, basis=exp.basis, rounds=exp.rounds, shots=shots)
    if noisy is None:
        log(f"  {exp.path}: no circuit_noisy*.stim, skipped")
        return rows
    dem = si1000_dem(noisy)
    if dem.num_detectors != circuit.num_detectors:
        raise ValueError(f"{exp.path}: the noisy circuit has {dem.num_detectors} detectors, "
                         f"the ideal one {circuit.num_detectors}")
    dem_split, split = split_by_basis(dem)
    edges_si = dem_to_edges(dem_split)
    graphs = {}
    if "si1000" in dem_modes:
        graphs["si1000"] = (edges_si, edges_to_dem(edges_si))
    if "pij" in dem_modes:
        edges_pij = calibrate_pij(edges_si, events)
        graphs["pij"] = (edges_pij, edges_to_dem(edges_pij))
    for mode, (edges, dem_graph) in graphs.items():
        for name, run in build_decoders(mode, dem_split, edges, dem_graph).items():
            t0 = time.perf_counter()
            pred = run(events)
            dt = time.perf_counter() - t0
            errors = int(np.count_nonzero(pred != obs))
            p = errors / shots
            rows.append(dict(base, dem=mode, decoder=name, errors=errors, p_logical=p,
                             eps_round=eps_per_round(p, exp.rounds),
                             eps_err=eps_error(p, exp.rounds, shots),
                             ns_per_shot=dt / shots * 1e9, edges=edges.num_edges,
                             skipped_hyperedges=edges_si.skipped_hyperedges, split=split))
            log(f"  {exp.patch} {exp.basis} r{exp.rounds:<3} {mode:6} {name:16} "
                f"{errors:6d}/{shots}  eps/round {rows[-1]['eps_round'] * 100:.3f}%  "
                f"{rows[-1]['ns_per_shot'] / 1e3:.0f} us/shot")
    if google:
        for name, errors in google_results(exp, obs):
            p = errors / shots
            rows.append(dict(base, dem="google", decoder=name, errors=errors, p_logical=p,
                             eps_round=eps_per_round(p, exp.rounds),
                             eps_err=eps_error(p, exp.rounds, shots), ns_per_shot="",
                             edges="", skipped_hyperedges="", split=""))
    return rows


def expected_decoders(dem_modes) -> set[tuple[str, str]]:
    out = set()
    if "si1000" in dem_modes:
        out |= {("si1000", d) for d in DECODERS}
    if "pij" in dem_modes:
        out |= {("pij", d) for d in DECODERS if d != "pymatching-corr"}
    return out


def read_rows(path: Path) -> list[dict]:
    with open(path) as fh:
        return list(csv.DictReader(fh))


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def run(root: Path, out_csv: Path, *, patches=None, bases=None, rounds=None, dem_modes=("si1000", "pij"),
        max_shots=None, google=True, resume=False, log=print) -> list[dict]:
    """Decode the experiments and write the CSV after each one, so an interrupted
    run keeps what it did; with resume, experiments the CSV already holds in
    full are skipped and the rest are added to it. Returns the CSV's rows."""
    exps = discover(root, patches, bases, rounds)
    if not exps:
        raise SystemExit(f"no experiments under {root} (expected <patch>/<basis>/r<rounds>/detection_events.b8)")
    rows: list[dict] = []
    if resume and out_csv.exists():
        done: dict[tuple, set] = {}
        for r in read_rows(out_csv):
            done.setdefault((r["patch"], r["basis"], int(r["rounds"])), set()).add((r["dem"], r["decoder"]))
        complete = {k for k, v in done.items() if expected_decoders(dem_modes) <= v}
        rows = [r for r in read_rows(out_csv) if (r["patch"], r["basis"], int(r["rounds"])) in complete]
        skipped = [e for e in exps if (e.patch, e.basis, e.rounds) in complete]
        exps = [e for e in exps if (e.patch, e.basis, e.rounds) not in complete]
        log(f"{len(skipped)} experiments already in {out_csv}, {len(exps)} to go")
    else:
        log(f"{len(exps)} experiments under {root}")
    for exp in exps:
        rows += decode_experiment(exp, dem_modes, max_shots, google, log)
        write_rows(out_csv, rows)
    write_rows(out_csv, rows)
    log(f"wrote {out_csv}")
    return read_rows(out_csv)


# ---------------------------------------------------------------- analysis

def fit_eps(rows: list[dict]) -> tuple[float, float]:
    """Logical error per round from several durations: 1 - 2 P_L(t) = (1 - 2 eps)^t,
    a weighted least-squares line through the origin in -ln(1 - 2 P_L) vs t."""
    t = np.array([float(r["rounds"]) for r in rows])
    shots = np.array([float(r["shots"]) for r in rows])
    p = np.clip(np.array([float(r["p_logical"]) for r in rows]), 0.0, 0.49)
    y = -np.log1p(-2.0 * p)
    var = np.maximum(4.0 * p * (1 - p) / shots, 4.0 / shots ** 2) / (1 - 2 * p) ** 2
    w = 1.0 / var
    slope = float(np.sum(w * t * y) / np.sum(w * t * t))
    slope_err = float(math.sqrt(1.0 / np.sum(w * t * t)))
    eps = 0.5 * (1.0 - math.exp(-slope))
    return eps, 0.5 * math.exp(-slope) * slope_err


def summarize(rows: list[dict]):
    """{(dem, decoder): {distance: (eps, err)}} with eps averaged over patches."""
    out: dict[tuple[str, str], dict[int, tuple[float, float]]] = {}
    keys = sorted({(r["dem"], r["decoder"]) for r in rows})
    for key in keys:
        per_d: dict[int, list[tuple[float, float]]] = {}
        sel = [r for r in rows if (r["dem"], r["decoder"]) == key]
        for patch in sorted({(r["patch"], r["basis"]) for r in sel}):
            prow = [r for r in sel if (r["patch"], r["basis"]) == patch]
            eps, err = fit_eps(prow)
            per_d.setdefault(int(prow[0]["distance"]), []).append((eps, err))
        out[key] = {d: (float(np.mean([e for e, _ in v])),
                        float(math.sqrt(sum(s ** 2 for _, s in v)) / len(v)))
                    for d, v in sorted(per_d.items())}
    return out


def lambda_table(summary) -> str:
    lines = ["dem      decoder            " + "".join(f"eps_d{d} (%)   " for d in (3, 5, 7)) + "Lambda"]
    for (dem, dec), per_d in summary.items():
        cells = []
        for d in (3, 5, 7):
            cells.append(f"{per_d[d][0] * 100:.3f}+-{per_d[d][1] * 100:.3f}  " if d in per_d else "      -        ")
        ds = sorted(per_d)
        lam = ""
        if len(ds) >= 2:
            x = np.array(ds, dtype=float)
            y = np.log([per_d[d][0] for d in ds])
            lam = f"{math.exp(-2.0 * np.polyfit(x, y, 1)[0]):.2f}"
            if len(ds) == 3:
                lam += f"  ({per_d[3][0] / per_d[5][0]:.2f} from 3 to 5, {per_d[5][0] / per_d[7][0]:.2f} from 5 to 7)"
        lines.append(f"{dem:8} {dec:18} " + "".join(cells) + lam)
    return "\n".join(lines)


def plot(csv_path: Path, png: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    with open(csv_path) as fh:
        rows = list(csv.DictReader(fh))
    summary = summarize(rows)
    print(lambda_table(summary))
    surface, ink, ink2, muted, grid = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
    colors = {"pymatching": "#898781", "pymatching-corr": "#3d3c39",
              "uf": "#86b6ef", "uf-corr": "#2a78d6", "uf-corr-reg": "#123f7a"}
    labels = {"pymatching": "PyMatching", "pymatching-corr": "PyMatching, correlated",
              "uf": "backlog Union-Find", "uf-corr": "backlog, correlated",
              "uf-corr-reg": f"backlog, correlated, α = {ALPHA_REG}"}
    modes = [m for m in ("si1000", "pij") if any(k[0] == m for k in summary)]
    titles = {"si1000": "Google's SI1000 noise model", "pij": "edge probabilities measured from the data"}
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    fig, axes = plt.subplots(1, len(modes), figsize=(5.6 * len(modes), 5.2), dpi=200, squeeze=False)
    fig.patch.set_facecolor(surface)
    for ax, mode in zip(axes[0], modes):
        ax.set_facecolor(surface)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.tick_params(axis="both", length=0, colors=muted)
        ax.grid(axis="y", color=grid, linewidth=0.8, zorder=0)
        ax.set_yscale("log")
        ax.set_title(titles[mode], color=ink, fontsize=11.5, loc="left")
        ax.set_xlabel("code distance d", color=ink2)
        ends = []
        for name in DECODERS:
            per_d = summary.get((mode, name))
            if not per_d:
                continue
            ds = sorted(per_d)
            y = [per_d[d][0] * 100 for d in ds]
            e = [per_d[d][1] * 100 for d in ds]
            ax.errorbar(ds, y, yerr=e, fmt="o-" if name.startswith("uf") else "s--", color=colors[name],
                        linewidth=2 if name.startswith("uf") else 1.4, markersize=5, capsize=2, zorder=3)
            ends.append((y[-1], ds[-1], labels[name], colors[name]))
        # Google's own decoder on the same experiment, from the paper
        ax.plot([7], [GOOGLE_EPS7 * 100], marker="D", color=ink, markersize=5, markerfacecolor="none", zorder=4)
        ends.append((GOOGLE_EPS7 * 100, 7, "Google's decoder, from the paper", ink))
        # direct labels, pushed apart where lines end together
        ends.sort()
        ys = np.log10([y for y, *_ in ends])
        gap = 0.06
        for i in range(1, len(ys)):
            ys[i] = max(ys[i], ys[i - 1] + gap)
        for (y, x, text, color), ly in zip(ends, 10 ** ys):
            ax.plot([x + 0.08, x + 0.45], [y, ly], color=color, linewidth=0.6, alpha=0.6)
            ax.annotate(text, (x + 0.5, ly), va="center", color=color, fontsize=9)
        ax.set_xticks([3, 5, 7])
        ax.set_xlim(2.6, 10.2)
    from matplotlib.ticker import FuncFormatter
    for ax in axes[0]:
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
        ax.yaxis.set_minor_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    axes[0][0].set_ylabel("logical error per round (%)", color=ink2)
    shots = sum(int(r["shots"]) for r in rows if r["dem"] == modes[0] and r["decoder"] == "uf")
    distances = ", ".join(str(d) for d in sorted({int(r["distance"]) for r in rows}))
    fig.suptitle("Google's Willow surface code memory (Nature 2025), decoded offline: "
                 f"{shots:,} shots, d = {distances}", color=ink, fontsize=12, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, facecolor=surface)
    print(f"wrote {png}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("root", nargs="?", help="unzipped dataset folder (holds the d*_at_q* patches)")
    ap.add_argument("--out", default=str(CSV), help=f"CSV to write (default {CSV})")
    ap.add_argument("--patches", nargs="*", help="only these patches, e.g. d7_at_q6_7")
    ap.add_argument("--bases", nargs="*", help="only these bases, X or Z")
    ap.add_argument("--rounds", nargs="*", type=int, help="only these numbers of rounds")
    ap.add_argument("--dem", default="si1000,pij", help="decoding graphs: si1000, pij or both")
    ap.add_argument("--max-shots", type=int, help="decode only the first shots of each experiment")
    ap.add_argument("--no-google-results", action="store_true", help="skip Google's own predictions")
    ap.add_argument("--resume", action="store_true",
                    help="keep the experiments already in --out and decode only the missing ones")
    ap.add_argument("--plot", action="store_true", help="draw the figure and print the table from --out")
    args = ap.parse_args()
    out = Path(args.out)
    if args.plot:
        plot(out, out.with_suffix(".png"))
        return
    if not args.root:
        ap.error("give the dataset folder, or --plot")
    run(Path(args.root), out, patches=args.patches, bases=args.bases, rounds=args.rounds,
        dem_modes=tuple(args.dem.split(",")), max_shots=args.max_shots,
        google=not args.no_google_results, resume=args.resume)
    plot(out, out.with_suffix(".png"))


if __name__ == "__main__":
    main()
