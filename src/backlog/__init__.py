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
from .decoder import DecodeResult, ReferenceDecoder, SlidingWindowDecoder, UnionFindDecoder
from .dem import GraphEdges, dem_to_edges, detector_layers
from .sinter_decoder import SinterSlidingWindowDecoder, SinterUnionFindDecoder, sinter_decoders

__version__ = "0.3.0"

__all__ = [
    "DecodeResult",
    "GraphEdges",
    "ReferenceDecoder",
    "SinterSlidingWindowDecoder",
    "SinterUnionFindDecoder",
    "SlidingWindowDecoder",
    "UnionFindDecoder",
    "dem_to_edges",
    "detector_layers",
    "sinter_decoders",
]
