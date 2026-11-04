# Roadmap

Each phase ships something measurable. A phase is done when its numbers are
in the README, reproducible with one `make` target, and covered by CI.

## Phase 1: baseline (done)

- Stim circuit-level noise model, rotated surface code memory, d = 3, 5, 7
- PyMatching threshold sweep with sinter: `make baseline`, `make plot`
- Result: threshold about 0.73% (crossing of d = 5 and d = 7)

## Phase 2: C++ Union-Find decoder (v0.3 done)

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
- [x] Mutation testing: 16 injected bugs, all caught (`make mutants`)
- [x] Python package (pybind11, `pip install .`) and sinter custom decoders,
      sinter command line included
- [x] Streaming: sliding windows (commit, buffer), windows with the same
      structure share one decoder, per-window latency measured in C++
- [x] Windowed accuracy matches whole-shot decoding once the buffer is d
      rounds (at most 2.3% more logical errors, same shots)
- [x] Backlog measured: one core keeps up with 1 µs rounds to d = 9 at
      p = 0.1% and to d = 5 at p = 0.3%, and falls behind beyond

Next:
- [ ] Parallel windows (Skoric et al. 2023, Tan et al. 2023): decode
      independent windows on several cores, then the seams between them.
      Throughput should scale with cores, the way past the single-core limit
- [ ] Report the logical clock cost: how long a feed-forward decision waits
      for its window
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
- [ ] One upstream contribution (Stim, PyMatching or Deltakit), even small

## Definition of done for the portfolio

- [x] One graph at the top of the README: logical vs physical error rate
- [x] A latency table (CPU, and ns per cycle after place and route)
- [x] Tests that prove correctness, not just that code runs
- [x] An honest limitations section
- [x] The CV line: "Streaming QEC decoder (weighted Union-Find, C++, pip,
      sinter): threshold matches PyMatching within error bars, sliding
      windows within 2.3% of whole-shot accuracy, real-time on one core to
      d = 9 at p = 0.1%; bit-exact against a reference on 75,000 fuzzed cases;
      1D VHDL core placed and routed at 94.5 MHz"
