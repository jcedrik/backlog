"""Generate rotated surface code memory circuits with a circuit-level noise model.

Thin wrapper around stim.Circuit.generated so the rest of the project has a
single source of truth for how circuits are built. Keeping this in one place
matters: the decoding graph, the Monte-Carlo sampler and the threshold sweep
all have to agree on the exact same noise model, or the numbers are garbage.
"""
from __future__ import annotations

import stim


def surface_code_circuit(distance: int, rounds: int, p: float,
                         basis: str = "z") -> stim.Circuit:
    """Return a rotated-surface-code memory experiment.

    One depolarizing parameter `p` drives every noise channel. This is the
    standard "uniform circuit-level" model used in most threshold papers, so
    the threshold we measure is comparable to published numbers (~0.5-0.7%).

    basis: "z" or "x" memory experiment.
    """
    if basis not in ("z", "x"):
        raise ValueError("basis must be 'z' or 'x'")
    return stim.Circuit.generated(
        f"surface_code:rotated_memory_{basis}",
        rounds=rounds,
        distance=distance,
        after_clifford_depolarization=p,
        after_reset_flip_probability=p,
        before_measure_flip_probability=p,
        before_round_data_depolarization=p,
    )


def repetition_code_circuit(distance: int, rounds: int, p: float) -> stim.Circuit:
    """Return a repetition-code memory experiment.

    The repetition code is graphlike and unweighted-friendly, which makes it
    the clean target for validating the C++ Union-Find decoder before we take
    on the full 2D surface code.
    """
    return stim.Circuit.generated(
        "repetition_code:memory",
        rounds=rounds,
        distance=distance,
        after_clifford_depolarization=p,
        after_reset_flip_probability=p,
        before_measure_flip_probability=p,
        before_round_data_depolarization=p,
    )


if __name__ == "__main__":
    c = surface_code_circuit(distance=3, rounds=3, p=0.005)
    print("rotated surface code, d=3, rounds=3, p=0.5%")
    print(f"  detectors: {c.num_detectors}  observables: {c.num_observables}")
