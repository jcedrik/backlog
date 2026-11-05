# Changelog

## Unreleased

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
