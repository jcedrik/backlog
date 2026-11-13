# Changelog

## Unreleased

- `python/google_data.py` and `make google-data`: decode Google's Willow
  surface code experiments (Nature 2025, Zenodo 13273331) with PyMatching and
  backlog, plain and correlated, on Google's SI1000 model and on edge
  probabilities measured from the detection events (the p_ij method); logical
  error per round, Λ and decoding time per shot
  ([docs/google-data.md](docs/google-data.md)). The CSV is written after
  every experiment and `--resume` carries an interrupted run on;
  `--rescore-google` scores the predictions Google shipped with the data in
  minutes. The repository ships the results for all 420 experiments
  (21 million shots): Union-Find is 8% behind matching at d = 7 and 10%
  behind Google's own correlated matching on the same model, 20 to 30% faster
  per shot, and the pipeline reproduces Google's fits within 2%. Both READMEs
  have the section.
- `python/noise_study.py --mass`: why the decomposition gap depends on the
  noise model. The same errors are mis-split under uniform noise and SI1000;
  what differs is the probability they carry (6.1% vs 3.0% at d = 3 with CX
  gates), because SI1000 puts its weight on measurement and reset errors,
  which touch one basis. The write-up has the table.
- `backlog.detector_basis`: X or Z type of every detector from the decomposed
  model alone, so `decompose_by_basis` (and correlated decoding) works on
  circuits without Stim's coordinates, Google's included, and on the
  unrotated surface code.

## 0.6.0 (2026-11-05)

- Browser demo (`docs/demo`, GitHub Pages): place bit flips on a surface code
  and watch the decoder grow clusters, merge them and peel out a correction,
  round by round, at d = 3 to 11. It runs the reference decoder ported to
  JavaScript; `tests/test_demo.py` checks it edge for edge against the C++
  and on every set of up to (d - 1) / 2 flips at d = 3, 5 and 7.
- `scripts/server_scaling.sh`: one command that builds backlog on a fresh
  Ubuntu machine, runs `make scaling` and packs the results, with a guide to
  measuring on 16 to 32 physical cores (`docs/server-scaling.md`).
- Predecoder in front of Union-Find: `predecode="lazy"` settles a shot whose
  defects are all isolated pairs or lone defects next to the boundary, as
  Delfosse's lazy decoder, and `predecode="partial"` settles the isolated
  defects of every shot and decodes the rest. Lazy never changed an answer on
  9,000,000 shots and is up to 1.9 times faster at small distance; partial is
  faster at p = 0.1% and below but costs accuracy at p = 0.3%. Sinter decoder
  `backlog-uf-pre`, `make predecoder`, C++ fuzzing of both modes, and a check
  on every pair of faults at d = 5.
- VHDL core: cluster parity and boundary contact from a Kogge-Stone parallel
  prefix scan instead of ripple chains (`PARALLEL => true`, the default). Same
  outputs bit for bit, now checked from N = 8 to 128; on the iCE40, Fmax at
  N = 128 goes from 9.8 to 41 MHz for twice the LUTs. `run_hw.sh` measures
  both versions behind a serial I/O harness, best of 3 placement seeds, and
  writes `hardware/fmax.csv` and `hardware/fmax.png`.
- `make noise-study`: Stim's decomposition against the X/Z split with CX or CZ
  gates, under uniform or SI1000 noise. The X/Z split wins in all eight
  settings; the gap is smallest with CZ gates and SI1000.
- `make faults` and `make correlated` work again after a plain `make install`.

## 0.5.0 (2026-11-05)

- Correlated (two-pass) decoding in the whole-shot, sliding-window and
  parallel-window decoders, exact against the reference decoder on the
  reweighted graph. `correlated=True`, `alpha=`, `dem_correlations`,
  `regularization_strength`, sinter decoder `backlog-uf-corr`.
- The regularized reweighting rule of Hernando de la Fuente et al.
  (arXiv:2610.26785), and an exhaustive check of what it buys: every pair of
  faults at d = 5 decoded by every decoder.
- `decompose_by_basis`: every error split into its X part and its Z part.
  Correlated decoding needs it: on Stim's own decomposition it is worse than
  plain decoding at d = 3, for PyMatching too (reported upstream as
  [PyMatching#193](https://github.com/oscarhiggott/PyMatching/issues/193)).
- Parallel windows: a seam starts as soon as its two cores are done (no
  barrier), PAUSE in spin loops, no spinning on hyperthreads, exceptions on
  worker threads carried back to the caller. `make scaling` splits the
  speedup into work inflation and idle time.
- Benchmarks: correlated accuracy and speed against PyMatching (same shots),
  error suppression factor, fault enumeration, laptop scaling analysis.
- Quickstart notebook, wheels workflow for PyPI, citation file, contributing
  notes, related-work survey, 26 mutants.
- Builds with Apple Clang and MSVC as well as GCC, and CI runs the C++ and
  Python test suites on macOS and Windows on every push.

## 0.4.0 (2026-11-04)

- Parallel window decoding (core windows, then seams) on a thread pool, same
  answer for any number of threads, clean under ThreadSanitizer.
- Scheduling model and projection of the cores needed for real time; real
  speedup measured on 2 cores, then up to 8 threads (`make scaling`).

## 0.3.0 (2026-11-04)

- Sliding-window decoder for streaming, windows with the same structure
  sharing one decoder; the backlog measured with 1 µs rounds.

## 0.2.0 (2026-11-04)

- Weighted, event-driven Union-Find (exact equivalent of synchronous rounds),
  a reference decoder and differential fuzzing between them.
- Python package (pybind11), Stim error model conversion, sinter decoders.
- Accuracy gates and benchmarks against PyMatching, mutation testing.

## 0.1.0 (2026-11-04)

- Stim + PyMatching baseline and threshold plot, a first C++ Union-Find
  decoder with a command-line tool, a 1D Union-Find core in VHDL checked bit
  for bit, CI.
