"""End-to-end tests of the Python package on real Stim circuits."""
from __future__ import annotations

import numpy as np
import pymatching
import pytest
import sinter
import stim

import backlog


def surface(d: int, p: float) -> stim.Circuit:
    return stim.Circuit.generated(
        "surface_code:rotated_memory_z", distance=d, rounds=d,
        after_clifford_depolarization=p, after_reset_flip_probability=p,
        before_measure_flip_probability=p, before_round_data_depolarization=p)


def sample(circuit: stim.Circuit, shots: int, seed: int):
    return circuit.compile_detector_sampler(seed=seed).sample(
        shots, separate_observables=True)


def ler(pred: np.ndarray, obs: np.ndarray) -> float:
    return float(np.mean(np.any(pred != obs, axis=1)))


def test_dem_conversion_is_graphlike():
    edges = backlog.dem_to_edges(surface(5, 0.005).detector_error_model(decompose_errors=True))
    assert edges.num_edges > 0
    assert edges.skipped_hyperedges == 0
    assert edges.conflicting_parallel_edges == 0
    assert np.all(edges.b <= edges.num_detectors)


def test_parallel_edges_merge_as_independent_errors():
    dem = stim.DetectorErrorModel("""
        error(0.1) D0 D1
        error(0.2) D0 D1
        error(0.3) D0 L0
        error(0.05) D0
    """)
    edges = backlog.dem_to_edges(dem)
    pair = (edges.a == 0) & (edges.b == 1)
    assert edges.p[pair][0] == pytest.approx(0.1 * 0.8 + 0.2 * 0.9)
    # Two boundary edges on D0 that disagree on L0: keep the likelier one.
    assert edges.conflicting_parallel_edges == 1
    to_boundary = (edges.a == 0) & (edges.b == edges.num_detectors)
    assert edges.obs[to_boundary][0] == 1


@pytest.mark.parametrize("weighted", [True, False])
@pytest.mark.parametrize("d", [3, 5, 7])
def test_every_correction_reproduces_its_syndrome(d, weighted):
    circuit = surface(d, 0.006)
    decoder = backlog.UnionFindDecoder.from_circuit(circuit, weighted=weighted)
    dets, _ = sample(circuit, 300, seed=d)
    for shot in dets:
        assert decoder.decode_detailed(shot).syndrome_ok


@pytest.mark.parametrize("weighted", [True, False])
def test_fast_decoder_equals_reference_on_surface_code(weighted):
    # Low weight resolution keeps the unit-round reference decoder fast.
    circuit = surface(3, 0.01)
    dem = circuit.detector_error_model(decompose_errors=True)
    fast = backlog.UnionFindDecoder.from_dem(dem, weighted=weighted, resolution=32)
    ref = backlog.ReferenceDecoder.from_dem(dem, weighted=weighted, resolution=32)
    dets, _ = sample(circuit, 500, seed=7)
    for shot in dets:
        a, b = fast.decode_detailed(shot), ref.decode_detailed(shot)
        assert np.array_equal(a.observables, b.observables)
        assert np.array_equal(a.correction, b.correction)


def test_batch_matches_single_shot_decoding():
    circuit = surface(5, 0.005)
    decoder = backlog.UnionFindDecoder.from_circuit(circuit)
    dets, _ = sample(circuit, 200, seed=3)
    batch = decoder.decode_batch(dets)
    single = np.array([decoder.decode(s) for s in dets])
    assert np.array_equal(batch, single)
    packed = np.packbits(dets.astype(np.uint8), axis=1, bitorder="little")
    out = decoder.decode_batch_bit_packed(packed)
    assert out.shape == (200, 1)
    assert decoder.latency_ns(packed).shape == (200,)


def test_weighting_closes_most_of_the_gap_to_pymatching():
    circuit = surface(5, 0.005)
    dem = circuit.detector_error_model(decompose_errors=True)
    dets, obs = sample(circuit, 20_000, seed=11)
    pm = ler(pymatching.Matching.from_detector_error_model(dem).decode_batch(dets), obs)
    weighted = ler(backlog.UnionFindDecoder.from_dem(dem, weighted=True).decode_batch(dets), obs)
    unweighted = ler(backlog.UnionFindDecoder.from_dem(dem, weighted=False).decode_batch(dets), obs)
    assert weighted < unweighted
    assert weighted < 1.35 * pm


def test_sinter_integration():
    task = sinter.Task(circuit=surface(3, 0.005), json_metadata={"d": 3})
    stats = sinter.collect(
        num_workers=1, tasks=[task], decoders=["backlog-uf"],
        custom_decoders=backlog.sinter_decoders(), max_shots=2_000, max_errors=500)
    assert len(stats) == 1
    assert stats[0].shots == 2_000
    assert 0 < stats[0].errors < 200
