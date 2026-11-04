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
