# Roadmap

Each phase ships something measurable. A phase is done when its numbers are
in the README, reproducible with one `make` target, and covered by CI.

## Phase 1: baseline (done)

- Stim circuit-level noise model, rotated surface code memory, d = 3, 5, 7
- PyMatching threshold sweep with sinter: `make baseline`, `make plot`
- Result: threshold about 0.73% (crossing of d = 5 and d = 7)

## Phase 2: C++ Union-Find decoder (v0.4 done)

Done:
- [x] Union-Find growth + peeling, dependency free, CLI on plain-text graphs
- [x] Hard invariant (correction reproduces syndrome), checked on every decode
- [x] Weighted growth from the detector error model: the d = 7 / 9 crossing
      goes from 0.66% to 0.77% (PyMatching 0.75%), logical error rate within
      0 to 20% of PyMatching up to d = 9
- [x] Event-driven growth (node radii, bucket queue, per-cluster lists)
      instead of scanning every edge each round: speed parity with
      PyMatching 2 on one core, d = 3 to 13
- [x] Reference decoder + fuzzing: fast and reference corrections identical
      on 75,000 random cases, parallel edges included
- [x] Mutation testing: 18 injected bugs, all caught (`make mutants`)
- [x] Python package (pybind11, `pip install .`) and sinter custom decoders,
      sinter command line included
- [x] Streaming: sliding windows (commit, buffer), windows with the same
      structure share one decoder, per-window latency measured in C++
- [x] Windowed accuracy matches whole-shot decoding once the buffer is d
      rounds (at most 2.3% more logical errors, same shots)
- [x] Backlog measured: one core keeps up with 1 µs rounds to d = 9 at
      p = 0.1% and to d = 5 at p = 0.3%, and falls behind beyond
- [x] Parallel windows (Skoric et al. 2023, Tan et al. 2023): core windows,
      then seams, on a thread pool; same answer for any thread count, clean
      under ThreadSanitizer, accuracy within 1.3% of whole-shot decoding
- [x] Real speedup 1.54 to 1.79x on 2 cores; a scheduling model checked
      against it gives the cores needed: 4 at p = 0.1% and 15 at p = 0.3%
      for d = 17, with 15% headroom
- [x] Check the model beyond 2 cores on a real machine: on a 4-core laptop
      (i7-8665U) 2x was measured at 4 threads against 3x predicted, and
      hyperthreads made it slower, so the model gives lower bounds

v0.5 done:
- [x] Correlated (two-pass) decoding in the whole-shot and window decoders,
      exact against the reference on the reweighted graph (75,000 fuzzed
      cases): 7 to 67% fewer logical errors than plain Union-Find (d = 3 to
      7), within a few percent of PyMatching's correlated matching, faster
      than it from d = 13
- [x] The regularized rule of arXiv:2610.26785 (alpha), with alpha*(p)
- [x] Exhaustive fault enumeration (every pair at d = 5): standard
      correlated decoding loses a unit of distance, regularized does not
- [x] Basis decomposition (X part, Z part) for correlated decoding: Stim's
      own decomposition makes it worse than plain decoding at d = 3
- [x] Parallel windows: seams start as soon as their two cores are done (no
      barrier), PAUSE in spin loops, no spinning on hyperthreads, worker
      exceptions carried to the caller; `make scaling` reports work
      inflation and idle time
- [x] 26 mutants, all killed

Next:
- [ ] Rerun `make scaling` on the 4-core laptop and on a server with pinned
      physical cores, and decide from work inflation vs idle time whether
      sharing the read-only window graphs between threads is worth it
- [ ] Correlated decoding at d = 7 and 9: enumerate triples of faults (local
      triples exhaustively), fit alpha*(p) per distance
- [ ] Other noise models: SI1000, and a model fitted to hardware data
- [ ] Logical clock cost: report how long a feed-forward decision waits for
      its window (the "streaming tail latency" NVIDIA reports), and shrink it
- [ ] Faster than PyMatching 2 at p = 0.3%: profile shows no single hotspot,
      so this needs an algorithmic change, not tuning

## Phase 3: hardware (1D done)

Done:
- Golden model, VHDL RTL, self-checking testbench, bit-exact at N = 8, 16, 32
- Synthesis (ghdl + yosys) and place and route (nextpnr, iCE40 HX8K)
- Measured bottleneck: ripple prefix/suffix chains, Fmax drops with N

Next:
- [ ] Parallel segmented prefix scan, O(log N) depth per round
- [ ] Weighted growth in hardware (integer lengths, as in the C++ decoder)
- [ ] 2D surface code core (d = 3, then 5) with the time dimension, vectors
      from Stim through the C++ decoder as golden model
- [ ] Synthesize in Vivado (free edition) for an Artix-7 to get 7-series timing
- [ ] Optional: run on a real board (Basys 3 or Arty A7) with a UART harness

## Phase 4: showcase

- [ ] Web demo: inject errors on a surface code, watch clusters grow
      (C++ compiled to WebAssembly)
- [ ] Write-up: what building a real-time decoder from scratch taught me,
      with the honest limitations
- [ ] One upstream contribution (Stim, PyMatching or Deltakit), even small:
      a basis-aware decomposition option for correlated matching is a
      natural candidate
- [ ] First PyPI release (`pip install backlog-qec`), wheels already built
      by `.github/workflows/wheels.yml`
- [x] Quickstart notebook, citation file, contributing notes

## Definition of done for the portfolio

- [x] One graph at the top of the README: logical vs physical error rate
- [x] A latency table (CPU, and ns per cycle after place and route)
- [x] Tests that prove correctness, not just that code runs
- [x] An honest limitations section
- [x] The CV line: "Real-time QEC decoder (weighted Union-Find, C++17,
      multithreaded, pip and sinter): threshold matches PyMatching within error
      bars; parallel sliding windows within 1.3% of whole-shot accuracy, 1.6 to
      1.85x speedup on 2 cores; fuzzed against a reference, race-free under
      ThreadSanitizer, 18/18 mutants caught; 1D VHDL core placed and routed at
      94.5 MHz"
