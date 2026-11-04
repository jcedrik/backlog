"""Python front end for the C++ Union-Find decoders."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import stim

from ._core import ReferenceDecoder as _CoreReference
from ._core import UnionFindDecoder as _CoreDecoder
from .dem import GraphEdges, dem_to_edges


def _mask_to_bits(mask: int, num_observables: int) -> np.ndarray:
    return np.array([(mask >> k) & 1 for k in range(num_observables)], dtype=bool)


def _fired_ids(syndrome) -> list[int]:
    return np.flatnonzero(np.asarray(syndrome)).astype(np.int32).tolist()


@dataclass(frozen=True)
class DecodeResult:
    observables: np.ndarray    # predicted logical flips, bool per observable
    correction: np.ndarray     # edge ids chosen by the decoder
    syndrome_ok: bool          # the correction reproduces the syndrome


class UnionFindDecoder:
    """Weighted Union-Find decoder for graphlike detector error models.

    weighted=True grows edges in proportion to ln((1-p)/p), so likely errors
    are matched first. weighted=False is the classic unweighted decoder.
    """

    def __init__(self, edges: GraphEdges, *, weighted: bool = True,
                 resolution: int = 1024):
        self.edges = edges
        self.weighted = weighted
        self.resolution = resolution
        self._core = _CoreDecoder(edges.num_detectors, edges.num_observables,
                                  edges.a, edges.b, edges.obs, edges.p,
                                  weighted, resolution)

    @classmethod
    def from_dem(cls, dem: stim.DetectorErrorModel, **kwargs) -> "UnionFindDecoder":
        return cls(dem_to_edges(dem), **kwargs)

    @classmethod
    def from_circuit(cls, circuit: stim.Circuit, **kwargs) -> "UnionFindDecoder":
        return cls.from_dem(circuit.detector_error_model(decompose_errors=True), **kwargs)

    @property
    def num_detectors(self) -> int:
        return self._core.num_detectors

    @property
    def num_observables(self) -> int:
        return self._core.num_observables

    @property
    def num_edges(self) -> int:
        return self._core.num_edges

    @property
    def weights(self) -> np.ndarray:
        return np.asarray(self._core.weights, dtype=np.int32)

    def decode(self, syndrome) -> np.ndarray:
        """Bool array over detectors -> bool array over observables."""
        return _mask_to_bits(self._core.decode_ids(_fired_ids(syndrome)),
                             self.num_observables)

    def decode_detailed(self, syndrome) -> DecodeResult:
        mask, correction, ok = self._core.decode_detailed_ids(_fired_ids(syndrome))
        return DecodeResult(_mask_to_bits(mask, self.num_observables),
                            np.asarray(correction, dtype=np.int32), bool(ok))

    def decode_batch(self, syndromes) -> np.ndarray:
        """(shots, num_detectors) bool -> (shots, num_observables) bool."""
        packed = np.packbits(np.asarray(syndromes, dtype=np.uint8), axis=1,
                             bitorder="little")
        out = self._core.decode_batch_bit_packed(packed)
        return np.unpackbits(out, axis=1, count=self.num_observables,
                             bitorder="little").astype(bool)

    def decode_batch_bit_packed(self, bit_packed_detection_events) -> np.ndarray:
        """Stim bit-packed detection events -> bit-packed predictions."""
        return self._core.decode_batch_bit_packed(bit_packed_detection_events)

    def latency_ns(self, bit_packed_detection_events) -> np.ndarray:
        """Per-shot decode latency in nanoseconds, measured in C++."""
        return np.asarray(self._core.latency_ns(bit_packed_detection_events))

    def stats(self) -> dict:
        """Work counters accumulated since construction or reset_stats()."""
        return dict(self._core.stats())

    def reset_stats(self) -> None:
        self._core.reset_stats()


class ReferenceDecoder:
    """The slow, obviously correct specification used to test the fast decoder."""

    def __init__(self, edges: GraphEdges, *, weighted: bool = True,
                 resolution: int = 1024):
        self.edges = edges
        self._core = _CoreReference(edges.num_detectors, edges.num_observables,
                                    edges.a, edges.b, edges.obs, edges.p,
                                    weighted, resolution)

    @classmethod
    def from_dem(cls, dem: stim.DetectorErrorModel, **kwargs) -> "ReferenceDecoder":
        return cls(dem_to_edges(dem), **kwargs)

    def decode_detailed(self, syndrome) -> DecodeResult:
        mask, correction, ok = self._core.decode_detailed_ids(_fired_ids(syndrome))
        return DecodeResult(_mask_to_bits(mask, self.edges.num_observables),
                            np.asarray(correction, dtype=np.int32), bool(ok))
