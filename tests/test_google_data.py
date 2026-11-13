"""python/google_data.py on a dataset laid out like Google's, sampled from Stim.

The real data (Zenodo 10.5281/zenodo.13273331) is 5.7 GB; this builds the
same folders and files from Stim's circuits, with the detector coordinates
stripped the way Google's circuits differ from Stim's, and runs the whole
pipeline: discovery, both decoding graphs, every decoder, Google-style
prediction files, the CSV, the fit and the figure.
"""
import csv
import importlib.util
import re
import sys
from pathlib import Path

import numpy as np
import pymatching
import pytest
import stim

import backlog

spec = importlib.util.spec_from_file_location(
    "google_data", Path(__file__).resolve().parent.parent / "python" / "google_data.py")
google_data = importlib.util.module_from_spec(spec)
sys.modules["google_data"] = google_data  # dataclasses look the module up by name
spec.loader.exec_module(google_data)

P = 0.003


def strip_coordinates(circuit: stim.Circuit) -> stim.Circuit:
    """Google's circuits do not carry Stim's checkerboard coordinates."""
    lines = []
    for line in str(circuit).splitlines():
        if line.strip().startswith("QUBIT_COORDS"):
            continue
        lines.append(re.sub(r"^(\s*DETECTOR)\([^)]*\)", r"\1", line))
    return stim.Circuit("\n".join(lines))


def make_dataset(root: Path, distances=(3, 5), rounds=(3, 6), shots=4000, seed=7) -> None:
    for d in distances:
        for i, basis in enumerate("Z"):
            for r in rounds:
                r_dir = root / f"d{d}_at_q{i}_{d}" / basis / f"r{r:02d}"
                r_dir.mkdir(parents=True)
                noisy = strip_coordinates(stim.Circuit.generated(
                    f"surface_code:rotated_memory_{basis.lower()}", distance=d, rounds=r,
                    after_clifford_depolarization=P, before_round_data_depolarization=P,
                    before_measure_flip_probability=P, after_reset_flip_probability=P))
                noisy.without_noise().to_file(r_dir / "circuit_ideal.stim")
                noisy.to_file(r_dir / "circuit_noisy_si1000_p1.stim")
                noisy.compile_detector_sampler(seed=seed + 10 * d + r).sample_write(
                    shots, filepath=str(r_dir / "detection_events.b8"), format="b8",
                    obs_out_filepath=str(r_dir / "obs_flips_actual.b8"), obs_out_format="b8")
                # a prediction file the way Google ships its decoders' output
                events = stim.read_shot_data_file(path=str(r_dir / "detection_events.b8"), format="b8",
                                                  num_detectors=noisy.num_detectors)
                pred = pymatching.Matching.from_detector_error_model(
                    noisy.detector_error_model(decompose_errors=True)).decode_batch(events)
                out = r_dir / "decoding_results" / "pymatching"
                out.mkdir(parents=True)
                stim.write_shot_data_file(data=pred.astype(bool), path=str(out / "obs_flips_predicted.b8"),
                                          format="b8", num_observables=1)


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    root = tmp_path_factory.mktemp("google")
    make_dataset(root)
    return root


def test_discovery(dataset):
    exps = google_data.discover(dataset)
    assert [(e.patch, e.basis, e.distance, e.rounds) for e in exps] == [
        ("d3_at_q0_3", "Z", 3, 3), ("d3_at_q0_3", "Z", 3, 6),
        ("d5_at_q0_5", "Z", 5, 3), ("d5_at_q0_5", "Z", 5, 6)]
    assert [e.patch for e in google_data.discover(dataset, patches=["d5_at_q0_5"], rounds=[6])] == ["d5_at_q0_5"]


def test_basis_without_coordinates(dataset):
    """Google's circuits have no Stim coordinates; the basis comes from the model itself."""
    exp = google_data.discover(dataset, patches=["d5_at_q0_5"], rounds=[6])[0]
    dem = stim.Circuit.from_file(str(exp.path / "circuit_noisy_si1000_p1.stim")).detector_error_model(
        decompose_errors=True)
    with pytest.raises(ValueError):
        backlog.surface_code_basis(dem)
    split, how = google_data.split_by_basis(dem)
    assert how == "basis"
    reference = stim.Circuit.generated("surface_code:rotated_memory_z", distance=5, rounds=6,
                                       after_clifford_depolarization=P, before_round_data_depolarization=P,
                                       before_measure_flip_probability=P, after_reset_flip_probability=P)
    want = backlog.decompose_by_basis(reference.detector_error_model(decompose_errors=True),
                                      backlog.surface_code_basis(reference.detector_error_model()))
    assert split.num_errors == want.num_errors
    assert {str(i) for i in split if i.type == "error"} == {str(i) for i in want if i.type == "error"}


def test_pij_recovers_the_model(dataset):
    """Edge probabilities measured from shots of a known model land near the model's."""
    exp = google_data.discover(dataset, patches=["d3_at_q0_3"], rounds=[6])[0]
    circuit, noisy, events, obs = google_data.load(exp, None)
    dem_split, _ = google_data.split_by_basis(google_data.si1000_dem(noisy))
    edges = backlog.dem_to_edges(dem_split)
    pij = google_data.calibrate_pij(edges, events)
    assert pij.num_edges == edges.num_edges
    big = edges.p > 2e-3  # the edges 4000 shots can measure
    assert big.sum() > 20
    ratio = pij.p[big] / edges.p[big]
    assert np.median(ratio) == pytest.approx(1.0, abs=0.25)
    assert np.all(pij.p >= google_data.P_FLOOR) and np.all(pij.p < 0.5)


def test_pipeline(dataset, tmp_path):
    out = tmp_path / "google_data.csv"
    rows = google_data.run(dataset, out, log=lambda *_: None)
    with open(out) as fh:
        assert list(csv.DictReader(fh)) == rows
    by = {(r["patch"], int(r["rounds"]), r["dem"], r["decoder"]): r for r in rows}
    decoders = {k[3] for k in by if k[2] == "si1000"}
    assert decoders == set(google_data.DECODERS)
    assert {k[3] for k in by if k[2] == "pij"} == {"pymatching", "uf", "uf-corr", "uf-corr-reg"}
    assert {k[3] for k in by if k[2] == "google"} == {"pymatching/obs_flips_predicted"}
    for r in rows:
        assert r["shots"] == "4000"
        assert 0 <= int(r["errors"]) <= 4000
        if r["dem"] != "google":
            assert r["split"] == "basis" and r["skipped_hyperedges"] == "0"
    # Google-style prediction file scored like a decoder: PyMatching on the si1000
    # graph decodes the same edges, so the two agree up to ties
    for patch, r in (("d3_at_q0_3", 3), ("d5_at_q0_5", 6)):
        ours = int(by[(patch, r, "si1000", "pymatching")]["errors"])
        theirs = int(by[(patch, r, "google", "pymatching/obs_flips_predicted")]["errors"])
        assert abs(ours - theirs) <= 0.1 * max(ours, theirs) + 5
    # the measured graph decodes about as well as the true one it was sampled from
    for patch in ("d3_at_q0_3", "d5_at_q0_5"):
        si = sum(int(by[(patch, r, "si1000", "uf")]["errors"]) for r in (3, 6))
        pij = sum(int(by[(patch, r, "pij", "uf")]["errors"]) for r in (3, 6))
        assert pij <= 1.3 * si + 10
    # more distance, fewer errors per round
    summary = google_data.summarize(rows)
    for key, per_d in summary.items():
        if key[0] == "google":
            continue
        assert per_d[5][0] < per_d[3][0], key
    table = google_data.lambda_table(summary)
    assert "uf-corr-reg" in table and "Lambda" in table
    pytest.importorskip("matplotlib")
    google_data.plot(out, tmp_path / "google_data.png")
    assert (tmp_path / "google_data.png").stat().st_size > 10_000


def test_resume_keeps_complete_experiments_and_redoes_partial_ones(dataset, tmp_path):
    out = tmp_path / "google_data.csv"
    first = google_data.run(dataset, out, patches=["d3_at_q0_3"], log=lambda *_: None)
    assert {r["rounds"] for r in first} == {"3", "6"}
    # an interrupted run: the last experiment lost its final decoder row
    last = max(i for i, r in enumerate(first) if r["dem"] != "google")
    google_data.write_rows(out, first[:last] + first[last + 1:])
    log = []
    again = google_data.run(dataset, out, resume=True, log=log.append)
    assert log[0].startswith("1 experiments already in")
    assert {(r["patch"], r["rounds"]) for r in again} == {("d3_at_q0_3", "3"), ("d3_at_q0_3", "6"),
                                                          ("d5_at_q0_5", "3"), ("d5_at_q0_5", "6")}
    assert [r for r in again if r["patch"] == "d3_at_q0_3" and r["rounds"] == "3"] == first[:len(first) // 2]
    redone = [r for r in again if r["patch"] == "d3_at_q0_3" and r["rounds"] == "6"]
    assert len(redone) == len(first) // 2  # whole again, decoded afresh
    assert {r["decoder"] for r in redone} == {r["decoder"] for r in first[len(first) // 2:]}


def test_rescore_google_replaces_the_shipped_rows(dataset, tmp_path):
    out = tmp_path / "google_data.csv"
    rows = google_data.run(dataset, out, patches=["d3_at_q0_3"], google=False, log=lambda *_: None)
    assert not [r for r in rows if r["dem"] == "google"]
    log = []
    again = google_data.rescore_google(dataset, out, log=log.append)
    assert log[0].startswith("4 prediction files from Google scored over 4 experiments")
    google = [r for r in again if r["dem"] == "google"]
    assert {(r["patch"], r["rounds"]) for r in google} == {("d3_at_q0_3", "3"), ("d3_at_q0_3", "6"),
                                                           ("d5_at_q0_5", "3"), ("d5_at_q0_5", "6")}
    assert [r for r in again if r["dem"] != "google"] == rows
    # scoring again changes nothing
    assert google_data.rescore_google(dataset, out, log=lambda *_: None) == again


def test_eps_per_round_round_trips():
    eps = 0.004
    for r in (1, 10, 250):
        p = 0.5 * (1 - (1 - 2 * eps) ** r)
        assert google_data.eps_per_round(p, r) == pytest.approx(eps, rel=1e-9)
    rows = [{"rounds": r, "shots": 10 ** 6, "p_logical": 0.5 * (1 - (1 - 2 * eps) ** r)} for r in (10, 50, 250)]
    fit, err = google_data.fit_eps(rows)
    assert fit == pytest.approx(eps, rel=1e-6) and err < 1e-4
