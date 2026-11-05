"""backlog: a real-time Union-Find decoder for quantum error correction.

    import backlog, stim
    circuit = stim.Circuit.generated("surface_code:rotated_memory_z",
                                     distance=5, rounds=5,
                                     after_clifford_depolarization=0.005)
    decoder = backlog.UnionFindDecoder.from_circuit(circuit)
    dets, obs = circuit.compile_detector_sampler().sample(
        1000, separate_observables=True)
    predictions = decoder.decode_batch(dets)
"""
from .decoder import (DecodeResult, ParallelWindowDecoder, ReferenceDecoder,
                      SlidingWindowDecoder, UnionFindDecoder)
from .dem import (Correlations, GraphEdges, decompose_by_basis, dem_correlations, dem_to_edges,
                  detector_layers, regularization_strength, surface_code_basis)
from .sinter_decoder import (SinterParallelWindowDecoder, SinterSlidingWindowDecoder,
                             SinterUnionFindDecoder, sinter_decoders)

__version__ = "0.6.0"

__all__ = [
    "Correlations",
    "DecodeResult",
    "GraphEdges",
    "ParallelWindowDecoder",
    "ReferenceDecoder",
    "SinterParallelWindowDecoder",
    "SinterSlidingWindowDecoder",
    "SinterUnionFindDecoder",
    "SlidingWindowDecoder",
    "UnionFindDecoder",
    "decompose_by_basis",
    "dem_correlations",
    "dem_to_edges",
    "detector_layers",
    "regularization_strength",
    "sinter_decoders",
    "surface_code_basis",
]
