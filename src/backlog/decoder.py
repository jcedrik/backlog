"""Python front end for the C++ decoders."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import stim

from ._core import ParallelWindowDecoder as _CoreParallel
from ._core import ReferenceDecoder as _CoreReference
from ._core import SlidingWindowDecoder as _CoreWindow
from ._core import UnionFindDecoder as _CoreDecoder
from .dem import (Correlations, GraphEdges, decompose_by_basis, dem_correlations, dem_to_edges,
                  detector_layers)


def _mask_to_bits(mask: int, num_observables: int) -> np.ndarray:
    return np.array([(mask >> k) & 1 for k in range(num_observables)], dtype=bool)


def _fired_ids(syndrome) -> list[int]:
    return np.flatnonzero(np.asarray(syndrome)).astype(np.int32).tolist()


def _corr_kwargs(correlations: Correlations | None, alpha: float) -> dict:
    if correlations is None:
        return {}
    return dict(corr_offset=correlations.offset, corr_target=correlations.target,
                corr_conditional=correlations.conditional, alpha=float(alpha))


def _graph_and_correlations(dem: stim.DetectorErrorModel, correlated: bool,
                            decompose: str = "basis"):
    """Edges and, for correlated decoding, the correlation table.

    Correlated decoding re-decomposes the model by stabilizer type first
    (decompose="basis"), which is what makes the correlations informative; it
    falls back to the model as given if that is impossible (another layout).
    decompose="as-is" keeps the model's own decomposition.
    """
    if correlated and decompose == "basis":
        try:
            dem = decompose_by_basis(dem)
        except ValueError:
            pass
    elif decompose not in ("basis", "as-is"):
        raise ValueError('decompose must be "basis" or "as-is"')
    edges = dem_to_edges(dem)
    return edges, (dem_correlations(dem, edges) if correlated else None)


@dataclass(frozen=True)
class DecodeResult:
    observables: np.ndarray    # predicted logical flips, bool per observable
    correction: np.ndarray     # edge ids chosen by the decoder
    syndrome_ok: bool          # the correction reproduces the syndrome


class _Decoder:
    """Methods shared by the whole-shot and the sliding-window decoders."""

    _core = None

    @property
    def num_detectors(self) -> int:
        return self._core.num_detectors

    @property
    def num_observables(self) -> int:
        return self._core.num_observables

    @property
    def correlated(self) -> bool:
        """True if every shot is decoded in two correlated passes."""
        return bool(self._core.correlated)

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


class UnionFindDecoder(_Decoder):
    """Weighted Union-Find decoder for graphlike detector error models.

    weighted=True grows edges in proportion to ln((1-p)/p), so likely errors
    are matched first. weighted=False is the classic unweighted decoder.

    correlations (or correlated=True in from_dem) decodes every shot twice:
    the edges of the first correction make the edges they come with in
    decomposed errors cheaper, at probability min(0.5, alpha * P(edge | first
    edge)), and the second pass decides. alpha = 1 is standard correlated
    decoding; alpha < 1 is the regularized rule (see regularization_strength).
    """

    def __init__(self, edges: GraphEdges, *, weighted: bool = True,
                 resolution: int = 1024, correlations: Correlations | None = None,
                 alpha: float = 1.0):
        self.edges = edges
        self.weighted = weighted
        self.resolution = resolution
        self.alpha = alpha
        self._core = _CoreDecoder(edges.num_detectors, edges.num_observables,
                                  edges.a, edges.b, edges.obs, edges.p,
                                  weighted, resolution, **_corr_kwargs(correlations, alpha))

    @classmethod
    def from_dem(cls, dem: stim.DetectorErrorModel, *, correlated: bool = False,
                 decompose: str = "basis", **kwargs) -> "UnionFindDecoder":
        edges, corr = _graph_and_correlations(dem, correlated, decompose)
        return cls(edges, correlations=corr, **kwargs)

    @classmethod
    def from_circuit(cls, circuit: stim.Circuit, **kwargs) -> "UnionFindDecoder":
        return cls.from_dem(circuit.detector_error_model(decompose_errors=True), **kwargs)

    @property
    def rules(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Reweighting rules (offset, target, weight) on the decoder's weight scale."""
        o, t, w = self._core.rules
        return (np.asarray(o, dtype=np.int32), np.asarray(t, dtype=np.int32),
                np.asarray(w, dtype=np.int32))

    @property
    def last_first_pass(self) -> np.ndarray:
        """Edge ids of the last shot's first pass (correlated decoding)."""
        return np.asarray(self._core.last_first_pass, dtype=np.int32)

    def weight_for_probability(self, p: float) -> int:
        return int(self._core.weight_for_probability(p))

    @property
    def num_edges(self) -> int:
        return self._core.num_edges

    @property
    def weights(self) -> np.ndarray:
        return np.asarray(self._core.weights, dtype=np.int32)

    def latency_ns(self, bit_packed_detection_events) -> np.ndarray:
        """Per-shot decode latency in nanoseconds, measured in C++."""
        return np.asarray(self._core.latency_ns(bit_packed_detection_events))

    def stats(self) -> dict:
        """Work counters accumulated since construction or reset_stats()."""
        return dict(self._core.stats())

    def reset_stats(self) -> None:
        self._core.reset_stats()


class SlidingWindowDecoder(_Decoder):
    """Streaming decoder: weighted Union-Find on a sliding window of rounds.

    Decodes `commit + buffer` time layers at a time, keeps the correction of
    the oldest `commit` layers, carries its effect forward, and slides by
    `commit` layers. The last window commits everything. Every committed
    correction reproduces the syndrome; with a buffer of about d rounds the
    accuracy matches whole-shot decoding.

    layers[v] is the time layer of detector v (from_dem reads it from the
    detector coordinates). Windows with the same local structure share one
    C++ decoder, so memory does not grow with the number of rounds.
    """

    def __init__(self, edges: GraphEdges, layers, *, commit: int, buffer: int,
                 weighted: bool = True, resolution: int = 1024,
                 correlations: Correlations | None = None, alpha: float = 1.0):
        self.edges = edges
        self.commit = commit
        self.buffer = buffer
        self.weighted = weighted
        self.resolution = resolution
        self.alpha = alpha
        self._core = _CoreWindow(edges.num_detectors, edges.num_observables,
                                 edges.a, edges.b, edges.obs, edges.p,
                                 np.ascontiguousarray(layers, dtype=np.int32),
                                 commit, buffer, weighted, resolution,
                                 **_corr_kwargs(correlations, alpha))

    @classmethod
    def from_dem(cls, dem: stim.DetectorErrorModel, *, commit: int, buffer: int,
                 correlated: bool = False, decompose: str = "basis",
                 **kwargs) -> "SlidingWindowDecoder":
        edges, corr = _graph_and_correlations(dem, correlated, decompose)
        return cls(edges, detector_layers(dem), commit=commit, buffer=buffer,
                   correlations=corr, **kwargs)

    @classmethod
    def from_circuit(cls, circuit: stim.Circuit, *, commit: int, buffer: int,
                     **kwargs) -> "SlidingWindowDecoder":
        return cls.from_dem(circuit.detector_error_model(decompose_errors=True),
                            commit=commit, buffer=buffer, **kwargs)

    @property
    def num_layers(self) -> int:
        return self._core.num_layers

    @property
    def num_windows(self) -> int:
        return self._core.num_windows

    @property
    def num_shapes(self) -> int:
        """Distinct window structures, each with its own C++ decoder."""
        return self._core.num_shapes

    @property
    def windows(self) -> list[tuple[int, int, int]]:
        """(first layer, end of commit region, end layer) of every window."""
        return list(self._core.windows)

    def window_latency_ns(self, bit_packed_detection_events) -> np.ndarray:
        """(shots, windows) processing time of each window, in nanoseconds.

        Each value covers gathering the window's defects, decoding them and
        committing, measured in C++.
        """
        return np.asarray(self._core.window_latency_ns(bit_packed_detection_events))


class ParallelWindowDecoder(_Decoder):
    """Streaming decoder that decodes windows on several cores at once.

    The parallel window scheme of Skoric et al. (2023) and Tan et al. (2023):

    1. core windows commit `commit` layers each, with `buffer` layers of
       buffer on both sides; they do not depend on each other and run in
       parallel;
    2. seams, the `seam` layers between two core commit regions, run next,
       also in parallel, once the cores around them have committed.

    The answer is the same for any number of threads. `seam` must cover the
    longest edge in time (1 for Stim memory circuits).
    """

    def __init__(self, edges: GraphEdges, layers, *, commit: int, buffer: int,
                 seam: int | None = None, threads: int = 1, weighted: bool = True,
                 resolution: int = 1024, correlations: Correlations | None = None,
                 alpha: float = 1.0):
        self.edges = edges
        self.commit = commit
        self.buffer = buffer
        self.seam = buffer if seam is None else seam
        self.weighted = weighted
        self.resolution = resolution
        self.alpha = alpha
        self._core = _CoreParallel(edges.num_detectors, edges.num_observables,
                                   edges.a, edges.b, edges.obs, edges.p,
                                   np.ascontiguousarray(layers, dtype=np.int32),
                                   commit, buffer, max(1, self.seam), threads,
                                   weighted, resolution, **_corr_kwargs(correlations, alpha))

    @classmethod
    def from_dem(cls, dem: stim.DetectorErrorModel, *, commit: int, buffer: int,
                 correlated: bool = False, decompose: str = "basis",
                 **kwargs) -> "ParallelWindowDecoder":
        edges, corr = _graph_and_correlations(dem, correlated, decompose)
        return cls(edges, detector_layers(dem), commit=commit, buffer=buffer,
                   correlations=corr, **kwargs)

    @classmethod
    def from_circuit(cls, circuit: stim.Circuit, *, commit: int, buffer: int,
                     **kwargs) -> "ParallelWindowDecoder":
        return cls.from_dem(circuit.detector_error_model(decompose_errors=True),
                            commit=commit, buffer=buffer, **kwargs)

    @property
    def num_layers(self) -> int:
        return self._core.num_layers

    @property
    def num_threads(self) -> int:
        return self._core.num_threads

    @property
    def num_windows(self) -> int:
        return self._core.num_windows

    @property
    def num_shapes(self) -> int:
        return self._core.num_shapes

    @property
    def windows(self) -> list[tuple[str, int, int, int, int]]:
        """(kind, first layer, commit begin, commit end, end layer), in time order."""
        return list(self._core.windows)

    def window_latency_ns(self, bit_packed_detection_events) -> np.ndarray:
        """(shots, windows) work time of each window, in nanoseconds."""
        return np.asarray(self._core.window_latency_ns(bit_packed_detection_events))

    def shot_latency_ns(self, bit_packed_detection_events) -> np.ndarray:
        """Wall-clock time of each shot with all threads, in nanoseconds."""
        return np.asarray(self._core.shot_latency_ns(bit_packed_detection_events))


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
