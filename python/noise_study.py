"""Is the decomposition effect a property of the noise model or of the gates?

On Stim's CX circuits with uniform circuit noise, correlated matching built on
Stim's decompose_errors=True makes more logical errors than plain matching at
d = 3 (docs/correlated-decoding-decomposition.md). On CZ circuits with SI1000
noise, Hernando de la Fuente et al. (arXiv:2610.26785, Appendix D.1) report no
significant difference between Stim's decomposition and the X/Z split. Those
two setups differ in the gates and in the noise model at once, so this crosses
them:

    gates: Stim's CX circuit, or the paper's CZ circuit with its noise removed
    noise: Stim's uniform model, or SI1000

The same code adds both noise models to the noiseless circuits, and each is
checked first against a reference: uniform noise on the CX circuit must give
exactly the error model of Stim's own noisy circuit, and SI1000 on the CZ
circuit exactly the error model of the paper's circuit file.

For every setting it reports how many errors Stim splits differently from the
X/Z split, how many single faults the correlated decoders get wrong, and the
logical errors of plain and correlated decoding (PyMatching and backlog) under
both decompositions, on the same shots.

Why the gap depends on the noise model: --mass weighs each setting's error
model and reports what share of the total error probability sits in errors that
touch both bases (the ones a decomposition can get wrong) and in the ones Stim
does get wrong. The mechanisms are the same under both noise models; their
weights are not (results/noise_study_mass.csv).

    make noise-study                       # clones the paper's repository into ext/rcm, then runs
    python python/noise_study.py --rcm ext/rcm --check   # only the two reference checks
    python python/noise_study.py --rcm ext/rcm --mass    # the probability-mass table, no sampling
    python python/noise_study.py --plot    # redraws results/noise_study.png
"""
from __future__ import annotations

import argparse
import csv
import os
import time

import numpy as np
import stim

import backlog
from fault_distance import decoders

RESULTS = os.path.join(os.path.dirname(__file__), "..", "results")
CSV = os.path.join(RESULTS, "noise_study.csv")
FIELDS = ["gates", "noise", "distance", "p", "decomposition", "errors", "misdecomposed",
          "misdecomposed_probability", "decoder", "single_faults", "single_fault_failures",
          "shots", "logical_errors", "fixed", "broken", "fixed_vs_stim", "broken_vs_stim"]

ONE_QUBIT = {"H"}
TWO_QUBIT = {"CX", "CZ"}
NOTES = {"DETECTOR", "OBSERVABLE_INCLUDE", "SHIFT_COORDS", "QUBIT_COORDS"}
KNOWN = ONE_QUBIT | TWO_QUBIT | NOTES | {"R", "M", "MR", "TICK"}


# --- circuits -----------------------------------------------------------------

def cx_circuit(d: int, noise: str, p: float) -> stim.Circuit:
    """Stim's rotated surface code memory (Z basis, d rounds), CX gates."""
    bare = stim.Circuit.generated("surface_code:rotated_memory_z", distance=d, rounds=d)
    return add_noise(bare, noise, p)


def cz_file(rcm: str, d: int, p: float) -> str:
    return os.path.join(rcm, "circuits", "si1000_circuits",
                        f"QMEM_rotated_patch-True_d_X-{d}_d_Z-{d}_rounds-{d}_basis-Z_SI1000_p-{p:.6f}.stim")


def cz_circuit(rcm: str, d: int, noise: str, p: float) -> stim.Circuit:
    """The paper's rotated surface code memory (Z basis, d rounds), CZ gates."""
    bare = stim.Circuit.from_file(cz_file(rcm, d, p)).without_noise()
    return add_noise(bare, noise, p)


def moments(circuit: stim.Circuit):
    """The flattened circuit as its moments: the instructions between TICKs.

    An instruction on a qubit that the current moment already used starts a new
    moment, as if a TICK were missing (the paper's d >= 5 circuits have an H and
    a reset on the same qubits with no TICK between them, and its noise treats
    them as two moments).
    """
    out, busy = [[]], set()
    for inst in circuit.flattened():
        if inst.name not in KNOWN:
            raise ValueError(f"unexpected instruction {inst.name}: give a noiseless circuit")
        if inst.name == "TICK":
            out.append([])
            busy = set()
            continue
        if inst.name not in NOTES:
            qs = set(qubits_of(inst))
            if qs & busy:
                out.append([])
                busy = set()
            busy |= qs
        out[-1].append(inst)
    return out


def qubits_of(inst) -> list[int]:
    return [t.value for t in inst.targets_copy()]


def add_noise(circuit: stim.Circuit, noise: str, p: float) -> stim.Circuit:
    return {"uniform": uniform, "si1000": si1000}[noise](circuit, p)


def uniform(circuit: stim.Circuit, p: float) -> stim.Circuit:
    """Stim's uniform circuit noise: what stim.Circuit.generated adds when
    after_clifford_depolarization, before_round_data_depolarization,
    before_measure_flip_probability and after_reset_flip_probability are all p.

    Every gate is followed by depolarization (1 or 2 qubits), every reset by an
    X error and every measurement preceded by one, and the data qubits are
    depolarized once at the start of every round, which begins with the first
    gate after a reset.
    """
    measured: dict[int, int] = {}
    for inst in circuit.flattened():
        if inst.name in ("M", "MR"):
            for q in qubits_of(inst):
                measured[q] = measured.get(q, 0) + 1
    data = sorted(q for q, n in measured.items() if n == 1)  # read out once, at the end

    out = stim.Circuit()
    after_reset = False
    for k, moment in enumerate(moments(circuit)):
        if k:
            out.append("TICK")
        if after_reset and any(i.name in ONE_QUBIT | TWO_QUBIT for i in moment):
            out.append("DEPOLARIZE1", data, p)
            after_reset = False
        for inst in moment:
            if inst.name in ("M", "MR"):
                out.append("X_ERROR", qubits_of(inst), p)
            out.append(inst)
            if inst.name in ("R", "MR"):
                out.append("X_ERROR", qubits_of(inst), p)
                after_reset = True
            elif inst.name in ONE_QUBIT:
                out.append("DEPOLARIZE1", qubits_of(inst), p)
            elif inst.name in TWO_QUBIT:
                out.append("DEPOLARIZE2", qubits_of(inst), p)
    return out


def si1000(circuit: stim.Circuit, p: float) -> stim.Circuit:
    """SI1000, superconducting-inspired noise (Gidney, Newman, Fowler and
    Broughton, arXiv:2108.10457): 2-qubit gates DEPOLARIZE2(p), 1-qubit gates
    DEPOLARIZE1(p/10), resets X_ERROR(2p), measurement results flipped with
    probability 5p, every idle qubit DEPOLARIZE1(p/10) per moment, and
    DEPOLARIZE1(2p) more on the qubits left idle while others are measured or
    reset.
    """
    used = sorted({q for inst in circuit.flattened() if inst.name not in NOTES | {"TICK"}
                   for q in qubits_of(inst)})
    out = stim.Circuit()
    for k, moment in enumerate(moments(circuit)):
        if k:
            out.append("TICK")
        busy: set[int] = set()
        readout = False
        for inst in moment:
            name = inst.name
            if name in NOTES:
                out.append(inst)
                continue
            qs = qubits_of(inst)
            busy.update(qs)
            if name in ("M", "MR"):
                out.append(name, qs, 5 * p)
            else:
                out.append(inst)
            if name in ("R", "MR"):
                out.append("X_ERROR", qs, 2 * p)
            elif name in ONE_QUBIT:
                out.append("DEPOLARIZE1", qs, p / 10)
            elif name in TWO_QUBIT:
                out.append("DEPOLARIZE2", qs, p)
            readout |= name in ("R", "M", "MR")
        idle = [q for q in used if q not in busy]
        if busy and idle:
            out.append("DEPOLARIZE1", idle, p / 10)
            if readout:
                out.append("DEPOLARIZE1", idle, 2 * p)
    return out


# --- error models ---------------------------------------------------------------

def model_table(dem: stim.DetectorErrorModel) -> dict:
    """{(detectors, observables): probability} of an undecomposed error model."""
    table: dict = {}
    for inst in dem.flattened():
        if inst.type != "error":
            continue
        dets: set[int] = set()
        obs: set[int] = set()
        for t in inst.targets_copy():
            if t.is_relative_detector_id():
                dets ^= {t.val}
            elif t.is_logical_observable_id():
                obs ^= {t.val}
        key = (frozenset(dets), frozenset(obs))
        p, q = inst.args_copy()[0], table.get(key, 0.0)
        table[key] = p + q - 2 * p * q
    return table


def same_model(a: stim.Circuit, b: stim.Circuit) -> tuple[bool, str]:
    ta = model_table(a.detector_error_model())
    tb = model_table(b.detector_error_model())
    if ta.keys() != tb.keys():
        return False, f"{len(ta.keys() ^ tb.keys())} mechanisms in only one model"
    worst = max(abs(ta[k] - tb[k]) / tb[k] for k in ta)
    return worst < 1e-9, f"{len(ta)} mechanisms, largest relative difference {worst:.1e}"


def check(rcm: str, distances, p: float) -> None:
    """The two injectors against their references; raises if either differs."""
    for d in distances:
        ref = stim.Circuit.generated(
            "surface_code:rotated_memory_z", distance=d, rounds=d,
            after_clifford_depolarization=p, before_measure_flip_probability=p,
            after_reset_flip_probability=p, before_round_data_depolarization=p)
        ok, msg = same_model(cx_circuit(d, "uniform", p), ref)
        print(f"d={d} uniform on CX vs stim.Circuit.generated: {'same' if ok else 'DIFFERENT'} ({msg})")
        if not ok:
            raise SystemExit(1)
        ref = stim.Circuit.from_file(cz_file(rcm, d, p))
        ok, msg = same_model(cz_circuit(rcm, d, "si1000", p), ref)
        print(f"d={d} SI1000 on CZ vs the paper's circuit:    {'same' if ok else 'DIFFERENT'} ({msg})")
        if not ok:
            raise SystemExit(1)


# --- the study ------------------------------------------------------------------

def basis_of(circuit: stim.Circuit, dem: stim.DetectorErrorModel) -> np.ndarray:
    """Detector types from the coordinates, checked against the paper's tags."""
    basis = backlog.surface_code_basis(dem)
    tags = [i.tag for i in circuit.flattened() if i.name == "DETECTOR"]
    if any(tags):
        assert list(basis) == [0 if t == "stabiliser=X" else 1 for t in tags]
    return basis


def misdecomposed(dem: stim.DetectorErrorModel, basis: np.ndarray) -> tuple[int, int, float]:
    """Errors of a decomposed model whose pieces are not its X part and its Z part:
    (count, total, share of the total probability)."""
    bad = total = 0
    p_bad = p_all = 0.0
    for inst in dem.flattened():
        if inst.type != "error":
            continue
        pieces, cur = [], set()
        for t in inst.targets_copy():
            if t.is_separator():
                pieces.append(frozenset(cur))
                cur = set()
            elif t.is_relative_detector_id():
                cur ^= {t.val}
        pieces.append(frozenset(cur))
        symptom: set[int] = set()
        for piece in pieces:
            symptom ^= piece
        split = {frozenset(x for x in symptom if basis[x] == b) for b in (0, 1)}
        p = inst.args_copy()[0]
        total += 1
        p_all += p
        if {x for x in pieces if x} != {x for x in split if x}:
            bad += 1
            p_bad += p
    return bad, total, p_bad / p_all


def two_basis_share(dem: stim.DetectorErrorModel, basis: np.ndarray) -> tuple[float, float, float]:
    """Shares of the total error probability in errors that flip detectors of
    both types (the ones a decomposition has to split), of one type only, and
    of neither (an observable flip alone)."""
    both = one = none = 0.0
    for inst in dem.flattened():
        if inst.type != "error":
            continue
        dets: set[int] = set()
        for t in inst.targets_copy():
            if t.is_relative_detector_id():
                dets ^= {t.val}
        kinds = len({int(basis[x]) for x in dets})
        p = inst.args_copy()[0]
        if kinds == 2:
            both += p
        elif kinds == 1:
            one += p
        else:
            none += p
    total = both + one + none
    return both / total, one / total, none / total


MASS_FIELDS = ["gates", "noise", "distance", "p", "errors", "two_basis_errors", "misdecomposed",
               "two_basis_probability", "misdecomposed_probability"]


def mass(rcm: str, distances, p: float) -> list[dict]:
    """Weigh the error model of every setting; no sampling, a few seconds."""
    rows = []
    for gates in ("CX", "CZ"):
        for noise in ("uniform", "si1000"):
            for d in distances:
                circuit = cx_circuit(d, noise, p) if gates == "CX" else cz_circuit(rcm, d, noise, p)
                dem = circuit.detector_error_model(decompose_errors=True)
                basis = basis_of(circuit, dem)
                bad, total, bad_share = misdecomposed(dem, basis)
                both, _, _ = two_basis_share(dem, basis)
                two = sum(1 for inst in dem.flattened() if inst.type == "error" and len({
                    int(basis[t.val]) for t in inst.targets_copy() if t.is_relative_detector_id()}) == 2)
                rows.append(dict(gates=gates, noise=noise, distance=d, p=p, errors=total, two_basis_errors=two,
                                 misdecomposed=bad, two_basis_probability=both, misdecomposed_probability=bad_share))
    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, "noise_study_mass.csv")
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=MASS_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("gates  noise    d   errors  two-basis  misdecomposed   P(two-basis)  P(misdecomposed)")
    for r in rows:
        print(f"{r['gates']:5}  {r['noise']:8} {r['distance']}  {r['errors']:6}  {r['two_basis_errors']:9}  "
              f"{r['misdecomposed']:13}   {100 * r['two_basis_probability']:10.1f}%  "
              f"{100 * r['misdecomposed_probability']:14.1f}%")
    print(f"wrote {path}")
    return rows


def faults(dem: stim.DetectorErrorModel):
    """Every distinct single fault: bit-packed symptom and observable flips."""
    table = model_table(dem)
    n = dem.num_detectors
    packed = np.zeros((len(table), (n + 7) // 8), dtype=np.uint8)
    obs = np.zeros(len(table), dtype=np.uint8)
    for i, (dets, flips) in enumerate(table):
        for v in dets:
            packed[i, v >> 3] |= np.uint8(1 << (v & 7))
        obs[i] = sum(1 << o for o in flips)
    return packed, obs


def cell(gates: str, noise: str, d: int, p: float, shots: int, rcm: str, seed: int):
    circuit = cx_circuit(d, noise, p) if gates == "CX" else cz_circuit(rcm, d, noise, p)
    stim_dem = circuit.detector_error_model(decompose_errors=True)
    basis = basis_of(circuit, stim_dem)
    bad, total, share = misdecomposed(stim_dem, basis)
    single, single_obs = faults(circuit.detector_error_model())
    det, obs = circuit.compile_detector_sampler(seed=seed).sample(
        shots, separate_observables=True, bit_packed=True)
    truth = obs[:, 0] & 1
    rows, wrong = [], {}
    for name, dem in (("stim", stim_dem), ("basis", backlog.decompose_by_basis(stim_dem, basis))):
        for dec, _, predict in decoders(dem, [1.0]):
            f_single = (np.asarray(predict(single))[:, 0] & 1) != (single_obs & 1)
            w = wrong[name, dec] = (np.asarray(predict(det))[:, 0] & 1) != truth
            row = dict(gates=gates, noise=noise, distance=d, p=p, decomposition=name,
                       errors=total, misdecomposed=bad, misdecomposed_probability=share,
                       decoder=dec, single_faults=len(single_obs),
                       single_fault_failures=int(f_single.sum()), shots=shots,
                       logical_errors=int(w.sum()), fixed="", broken="",
                       fixed_vs_stim="", broken_vs_stim="")
            # paired counts: shots this decoder gets right and the other wrong (fixed)
            # or the reverse (broken), against plain decoding and against Stim's split
            if dec.endswith("-corr"):
                row["fixed"], row["broken"] = paired(w, wrong[name, dec[:-5]])
            if name == "basis":
                row["fixed_vs_stim"], row["broken_vs_stim"] = paired(w, wrong["stim", dec])
            rows.append(row)
    return rows


def paired(wrong, other):
    return int((other & ~wrong).sum()), int((wrong & ~other).sum())


def run(rcm: str, distances, p: float, shots: dict, seed: int) -> None:
    check(rcm, distances, p)
    rows = []
    for d in distances:
        for gates in ("CX", "CZ"):
            for noise in ("uniform", "si1000"):
                t0 = time.perf_counter()
                new = cell(gates, noise, d, p, shots[d], rcm, seed)
                rows += new
                by = {(r["decomposition"], r["decoder"]): r for r in new}
                r0 = new[0]
                print(f"\nd={d} {gates} {noise} ({time.perf_counter() - t0:.0f} s): Stim splits "
                      f"{r0['misdecomposed']} of its {r0['errors']} errors differently from X/Z, "
                      f"{100 * r0['misdecomposed_probability']:.1f}% of the probability")
                print("              plain     corr   corr/plain        single faults wrong (corr)")
                for fam in ("PM", "UF"):
                    for dec in ("stim", "basis"):
                        a, b = by[dec, fam], by[dec, fam + "-corr"]
                        print(f"  {fam} {dec:5s} {a['logical_errors']:9d} {b['logical_errors']:8d}   "
                              f"{b['logical_errors'] / max(1, a['logical_errors']):.2f} "
                              f"(z {z(b['fixed'], b['broken']):+5.1f})   "
                              f"{b['single_fault_failures']} of {b['single_faults']}")
                    b = by["basis", fam + "-corr"]
                    print(f"  {fam} corr, X/Z vs Stim: {b['logical_errors']} vs "
                          f"{by['stim', fam + '-corr']['logical_errors']} "
                          f"(z {z(b['fixed_vs_stim'], b['broken_vs_stim']):+5.1f})", flush=True)
    os.makedirs(RESULTS, exist_ok=True)
    with open(CSV, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {CSV}")


def z(fixed: int, broken: int) -> float:
    """Paired (McNemar) z score of a change: below -2, clearly fewer errors."""
    return (broken - fixed) / max(1, fixed + broken) ** 0.5


def plot(png: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    surface, ink, ink2, muted, grid, base = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
    series = {"stim": "#2a78d6", "basis": "#eb6834"}
    names = {"stim": "Stim's decompose_errors=True", "basis": "X/Z split (decompose_by_basis)"}
    with open(CSV) as fh:
        rows = {(r["gates"], r["noise"], int(r["distance"]), r["decomposition"], r["decoder"]): r
                for r in csv.DictReader(fh)}
    cells = [("CX", "uniform"), ("CX", "si1000"), ("CZ", "uniform"), ("CZ", "si1000")]
    distances = sorted({k[2] for k in rows})
    shots = {int(r["shots"]) for r in rows.values()}

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    fig, axes = plt.subplots(1, len(distances), figsize=(11, 5.2), dpi=200, sharey=True)
    fig.patch.set_facecolor(surface)
    width, gap = 0.36, 0.03
    for ax, d in zip(np.atleast_1d(axes), distances):
        ax.set_facecolor(surface)
        for k, dec in enumerate(("stim", "basis")):
            xs, ys, es = [], [], []
            for i, (g, n) in enumerate(cells):
                plain = int(rows[g, n, d, dec, "PM"]["logical_errors"])
                r = rows[g, n, d, dec, "PM-corr"]
                xs.append(i + (k - 0.5) * (width + gap))
                ys.append(100 * (int(r["logical_errors"]) / plain - 1))
                es.append(100 * (int(r["fixed"]) + int(r["broken"])) ** 0.5 / plain)
            ax.bar(xs, ys, width=width, color=series[dec], label=names[dec], zorder=3)
            ax.errorbar(xs, ys, yerr=es, fmt="none", ecolor=ink2, elinewidth=1, capsize=3, zorder=4)
            for x, y, e in zip(xs, ys, es):
                off = e + 1.5
                ax.text(x, y + (off if y >= 0 else -off), f"{y:+.0f}%".replace("-", "\u2212"),
                        ha="center", va="bottom" if y >= 0 else "top", color=ink, fontsize=10, zorder=5)
        ax.axhline(0, color=base, linewidth=1.2, zorder=2)
        ax.set_xticks(range(len(cells)))
        ax.set_xticklabels([f"{g} gates\n{'SI1000' if n == 'si1000' else n}" for g, n in cells], color=ink2)
        ax.tick_params(axis="x", length=0, pad=8)
        ax.tick_params(axis="y", colors=muted, length=0)
        ax.grid(axis="y", color=grid, linewidth=0.8, zorder=0)
        for side in ("top", "right", "left", "bottom"):
            ax.spines[side].set_visible(False)
        ax.set_title(f"d = {d}", color=ink, fontsize=12, loc="left")
        ax.set_xlim(-0.6, len(cells) - 0.4)
    ax0 = np.atleast_1d(axes)[0]
    ax0.set_ylim(-55, 63)
    ax0.set_yticks(range(-40, 41, 20))
    ax0.set_yticklabels([(f"{t:+d}%" if t else "0").replace("-", "\u2212") for t in range(-40, 41, 20)])
    ax0.set_ylabel("logical errors, correlated vs plain matching", color=ink2)
    ax0.text(-0.55, 59, "worse", color=muted, fontsize=10, va="center")
    ax0.text(-0.55, -51, "better", color=muted, fontsize=10, va="center")
    np.atleast_1d(axes)[-1].legend(loc="upper right", frameon=False, fontsize=10, labelcolor=ink)
    fig.text(0.012, 0.97, "The X/Z split wins with either gates and either noise model; "
             "the gap is smallest with CZ gates and SI1000",
             fontsize=13, color=ink, weight="bold", ha="left", va="top")
    fig.text(0.012, 0.91, f"PyMatching 2.4, rotated surface code memory (d rounds), p = 0.1%, "
             f"{max(shots):,} shots per setting, the same shots for every decoder. Error bars: \u00b11\u03c3, "
             "paired.\nCX gates: Stim's generated circuit. CZ gates: the circuit of arXiv:2610.26785. "
             "Both noise models added to the same noiseless circuits.",
             fontsize=9.5, color=ink2, ha="left", va="top")
    fig.subplots_adjust(left=0.075, right=0.99, top=0.79, bottom=0.13, wspace=0.06)
    fig.savefig(png, facecolor=surface)
    print("saved", png)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rcm", default=os.path.join(os.path.dirname(__file__), "..", "ext", "rcm"),
                    help="clone of github.com/cesar-hernando/regularized-correlated-matching")
    ap.add_argument("--check", action="store_true", help="only check the noise against the references")
    ap.add_argument("--mass", action="store_true",
                    help="weigh the error models: probability in two-basis and misdecomposed errors")
    ap.add_argument("--plot", action="store_true", help=f"redraw results/noise_study.png from {CSV}")
    ap.add_argument("-p", type=float, default=1e-3)
    ap.add_argument("--distances", type=int, nargs="+", default=[3, 5])
    ap.add_argument("--shots", type=int, nargs="+", default=[2_000_000, 2_000_000],
                    help="shots per setting, one value per distance")
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()
    if args.plot:
        plot(os.path.join(RESULTS, "noise_study.png"))
    elif args.check:
        check(args.rcm, args.distances, args.p)
    elif args.mass:
        mass(args.rcm, args.distances, args.p)
    else:
        run(args.rcm, args.distances, args.p, dict(zip(args.distances, args.shots)), args.seed)


if __name__ == "__main__":
    main()
