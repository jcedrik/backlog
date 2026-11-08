"""Turn a Stim detector error model into decoding-graph edges."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import stim


@dataclass(frozen=True)
class GraphEdges:
    """Graphlike edges extracted from a detector error model.

    Node ``num_detectors`` is the boundary. ``obs`` is a bitmask of the logical
    observables each edge flips, ``p`` the probability of the edge.
    """

    num_detectors: int
    num_observables: int
    a: np.ndarray
    b: np.ndarray
    obs: np.ndarray
    p: np.ndarray
    skipped_hyperedges: int
    conflicting_parallel_edges: int

    @property
    def num_edges(self) -> int:
        return int(self.a.shape[0])


def dem_to_edges(dem: stim.DetectorErrorModel) -> GraphEdges:
    """Extract graphlike edges from a (preferably decomposed) DEM.

    Each graphlike component of an error mechanism becomes an edge. Parallel
    edges with the same observables are merged as independent mechanisms
    (p = p1 + p2 - 2 p1 p2). Parallel edges that disagree on the observables
    cannot both be represented: the likelier one is kept and counted in
    ``conflicting_parallel_edges``. Components touching more than two detectors
    are skipped and counted in ``skipped_hyperedges``; decompose the DEM
    (``decompose_errors=True``) to avoid them.
    """
    n = dem.num_detectors
    m = dem.num_observables
    if m > 64:
        raise ValueError("at most 64 observables are supported")
    boundary = n
    merged: dict[tuple[int, int], list] = {}
    skipped = 0
    conflicts = 0

    for inst in dem.flattened():
        if inst.type != "error":
            continue
        p = float(inst.args_copy()[0])
        components: list[list] = [[[], 0]]
        for t in inst.targets_copy():
            if t.is_separator():
                components.append([[], 0])
            elif t.is_relative_detector_id():
                components[-1][0].append(t.val)
            elif t.is_logical_observable_id():
                components[-1][1] ^= 1 << t.val
        for dets, obs in components:
            if len(dets) == 1:
                key = (dets[0], boundary)
            elif len(dets) == 2 and dets[0] != dets[1]:
                key = (min(dets), max(dets))
            elif len(dets) == 0:
                continue  # undetectable component: nothing to decode
            else:
                skipped += 1
                continue
            old = merged.get(key)
            if old is None:
                merged[key] = [obs, p]
            elif old[0] == obs:
                old[1] = old[1] * (1 - p) + p * (1 - old[1])
            else:
                conflicts += 1
                if p > old[1]:
                    merged[key] = [obs, p]

    keys = sorted(merged)
    a = np.array([k[0] for k in keys], dtype=np.int32)
    b = np.array([k[1] for k in keys], dtype=np.int32)
    obs_arr = np.array([merged[k][0] for k in keys], dtype=np.uint64)
    p_arr = np.array([merged[k][1] for k in keys], dtype=np.float64)
    return GraphEdges(n, m, a, b, obs_arr, p_arr, skipped, conflicts)


@dataclass(frozen=True)
class Correlations:
    """What the edges of decomposed errors say about each other.

    A decomposed error such as Y = X.Z flips several graph edges at once. For
    edge e, entries [offset[e], offset[e+1]) list every edge ``target[k]``
    that comes with e in some error, and ``conditional[k]`` = P(target fired |
    e fired) = P(both) / P(e), exactly as PyMatching's correlated matching
    computes them (both probabilities combine their errors as independent).
    """

    offset: np.ndarray        # int32, num_edges + 1
    target: np.ndarray        # int32
    conditional: np.ndarray   # float64

    @property
    def num_rules(self) -> int:
        return int(self.target.shape[0])


def _xor(p: float, q: float) -> float:
    return p * (1 - q) + q * (1 - p)


def dem_correlations(dem: stim.DetectorErrorModel, edges: GraphEdges) -> Correlations:
    """Correlation table of a decomposed DEM, for the edges of ``edges``.

    Components the graph does not have (hyperedges, undetectable parts) are
    ignored, as in ``dem_to_edges``.
    """
    boundary = edges.num_detectors
    index = {(int(a), int(b)): i for i, (a, b) in enumerate(zip(edges.a, edges.b))}
    marginal = np.zeros(edges.num_edges)
    joint: dict[tuple[int, int], float] = {}
    for inst in dem.flattened():
        if inst.type != "error":
            continue
        p = float(inst.args_copy()[0])
        if p <= 0:
            continue
        ids: list[int] = []
        dets: list[int] = []
        for t in inst.targets_copy() + [stim.DemTarget.separator()]:
            if t.is_separator():
                if len(dets) == 1:
                    key = (dets[0], boundary)
                elif len(dets) == 2 and dets[0] != dets[1]:
                    key = (min(dets), max(dets))
                else:
                    key = None
                if key is not None and key in index:
                    ids.append(index[key])
                dets = []
            elif t.is_relative_detector_id():
                dets.append(t.val)
        for e in ids:
            marginal[e] = _xor(marginal[e], p)
        if len(ids) > 1:
            for e in ids:
                for f in ids:
                    if e != f:
                        joint[(e, f)] = _xor(joint.get((e, f), 0.0), p)
    pairs = sorted(joint)
    offset = np.zeros(edges.num_edges + 1, dtype=np.int32)
    target = np.empty(len(pairs), dtype=np.int32)
    conditional = np.empty(len(pairs), dtype=np.float64)
    for k, (e, f) in enumerate(pairs):
        offset[e + 1] += 1
        target[k] = f
        conditional[k] = min(1.0, joint[(e, f)] / marginal[e])
    np.cumsum(offset, out=offset)
    return Correlations(offset, target, conditional)


def surface_code_basis(dem: stim.DetectorErrorModel) -> np.ndarray:
    """Basis of every detector (0 = X-type, 1 = Z-type) in Stim's generated
    rotated surface code circuits, read from the detector coordinates (x, y, t):
    measure qubits of the two types alternate in a checkerboard of 2 x 2 cells.
    """
    coords = dem.get_detector_coordinates()
    basis = np.empty(dem.num_detectors, dtype=np.int8)
    for d in range(dem.num_detectors):
        c = coords.get(d)
        if not c or len(c) < 2:
            raise ValueError(f"detector {d} has no (x, y) coordinates")
        basis[d] = 1 - ((int(c[0]) // 2 + int(c[1]) // 2) % 2)
    return basis


def detector_basis(dem: stim.DetectorErrorModel) -> np.ndarray:
    """Type of every detector, from the model alone: 1 for the checks that
    protect the logical observable (Z-type in a Z-basis memory experiment,
    X-type in an X-basis one), 0 for the others.

    A single X or Z error flips checks of one type only, so the graphlike
    pieces of a decomposed model (one or two detectors each) never join the
    two types: the detectors fall into two connected components, and the
    component whose pieces flip the observable is the one that protects it.
    Which type is called 1 does not matter to ``decompose_by_basis``, which
    only needs the two groups. This works for any
    CSS memory circuit whatever its detector coordinates (Stim's generated
    circuits, Google's experiment circuits). A model with checks of one type
    only (a one-round memory experiment) gets one basis throughout. Raises
    ValueError if the pieces connect the detectors into more than two
    components, or if both components flip the observable.
    """
    dem = dem.flattened()
    n = dem.num_detectors
    parent = np.arange(n)

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = int(parent[x])
        return x

    flips_obs: set[int] = set()
    hyperedges = separators = 0
    for inst in dem:
        if inst.type != "error":
            continue
        dets: list[int] = []
        obs = False
        for t in inst.targets_copy() + [stim.DemTarget.separator()]:
            if t.is_separator():
                separators += 1
                if len(dets) == 2:
                    parent[find(dets[0])] = find(dets[1])
                elif len(dets) > 2:
                    hyperedges += 1
                if dets and obs:
                    flips_obs.update(dets)
                dets, obs = [], False
            elif t.is_relative_detector_id():
                dets.append(t.val)
            elif t.is_logical_observable_id():
                obs = True
        separators -= 1  # the one appended above
    # In an undecomposed model a Y error on a corner data qubit is a
    # two-detector error of mixed type, which would join the two components.
    if hyperedges and not separators:
        raise ValueError("pass a decomposed model "
                         "(circuit.detector_error_model(decompose_errors=True))")
    roots = np.array([find(d) for d in range(n)])
    components = {int(r) for r in roots}
    if len(components) > 2:
        raise ValueError(f"the graphlike pieces connect the detectors into {len(components)} "
                         "components, not two (is this a CSS memory experiment?)")
    protecting = {int(roots[d]) for d in flips_obs}
    if len(protecting) > 1:
        raise ValueError("the logical observable is flipped by pieces of both components: "
                         "X and Z checks cannot be told apart")
    if not protecting:  # no observable: the labels are arbitrary, the split is not
        protecting = {min(components)}
    return (roots == protecting.pop()).astype(np.int8)


def decompose_by_basis(dem: stim.DetectorErrorModel, basis=None) -> stim.DetectorErrorModel:
    """Re-decompose every error into its X-type part and its Z-type part.

    Correlated decoding learns from the pieces of decomposed errors, so which
    pieces they are matters. Stim's decomposer splits an error into any
    graphlike pieces that occur elsewhere; this splits by stabilizer type
    instead, so a Y error becomes exactly its X and Z parts (the convention of
    the Tesseract decoder's coordinate decomposition, used by arXiv:2610.26785).
    Each part keeps the observables that the same detectors carry when they
    fire on their own elsewhere in the model.

    ``dem`` may already be decomposed: an error's pieces are merged back first.
    ``basis[d]`` is the type of detector d; by default it is read from the
    model itself (``detector_basis``), or from Stim's rotated surface code
    coordinates when that fails. Raises ValueError if an error cannot be
    split this way (a part with no counterpart, or more than two types).
    """
    dem = dem.flattened()
    if basis is None:
        try:
            basis = detector_basis(dem)
        except ValueError:
            basis = surface_code_basis(dem)

    def symptom(inst):
        dets: set[int] = set()
        obs = 0
        for t in inst.targets_copy():
            if t.is_relative_detector_id():
                dets ^= {t.val}
            elif t.is_logical_observable_id():
                obs ^= 1 << t.val
        return tuple(sorted(dets)), obs

    errors = []
    alone: dict[tuple[int, ...], set[int]] = {}
    for inst in dem:
        if inst.type != "error":
            continue
        dets, obs = symptom(inst)
        errors.append((inst.args_copy(), dets, obs))
        if len({int(basis[d]) for d in dets}) <= 1:
            alone.setdefault(dets, set()).add(obs)

    out = stim.DetectorErrorModel()
    for inst in dem:
        if inst.type != "error":
            out.append(inst)
    for args, dets, obs in errors:
        groups: dict[int, list[int]] = {}
        for d in dets:
            groups.setdefault(int(basis[d]), []).append(d)
        if len(groups) <= 1:
            parts = [(dets, obs)]
        elif len(groups) == 2:
            a, b = (tuple(g) for g in groups.values())
            split = next(((oa, oa ^ obs) for oa in sorted(alone.get(a, ()))
                          if (oa ^ obs) in alone.get(b, ())), None)
            if split is None:
                raise ValueError(f"error on detectors {dets} has no X/Z split "
                                 "into parts that occur on their own")
            parts = [(a, split[0]), (b, split[1])]
        else:
            raise ValueError("detectors of more than two types")
        targets = []
        for i, (ds, o) in enumerate(parts):
            if i:
                targets.append(stim.target_separator())
            targets += [stim.target_relative_detector_id(d) for d in ds]
            targets += [stim.target_logical_observable_id(k) for k in range(64) if o >> k & 1]
        out.append("error", args, targets)
    return out


def regularization_strength(p: float, *, p0: float = 1e-3, k: float = 1.0,
                            alpha0: float = 1.0) -> float:
    """Reweighting strength alpha*(p) = alpha0 / (1 + (p0/p)^k).

    The error-rate-dependent regularization of Hernando de la Fuente et al.
    (arXiv:2610.26785, Eq. 6): close to alpha0 near threshold, and smaller deep
    below it, where full-strength reweighting costs a unit of distance.
    """
    if p <= 0:
        raise ValueError("p must be > 0")
    return alpha0 / (1.0 + (p0 / p) ** k)


def detector_layers(dem: stim.DetectorErrorModel) -> np.ndarray:
    """Time layer of each detector, from the last coordinate of its DETECTOR.

    Stim's generated circuits give every detector coordinates (x, y, t), where
    t counts rounds. Layers are shifted to start at 0. The sliding-window
    decoder needs detectors numbered in time order, which Stim circuits do.
    """
    coords = dem.get_detector_coordinates()
    n = dem.num_detectors
    layers = np.empty(n, dtype=np.int64)
    for d in range(n):
        c = coords.get(d)
        if not c:
            raise ValueError(f"detector {d} has no coordinates: pass layers explicitly")
        layers[d] = int(round(c[-1]))
    if n:
        layers -= layers.min()
        if np.any(np.diff(layers) < 0):
            raise ValueError("detector layers must be non-decreasing with the detector id")
    return layers.astype(np.int32)
