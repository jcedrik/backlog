# Roadmap

Each phase ships something measurable. A phase is done when its numbers are
in the README, reproducible with one `make` target, and covered by CI.

## Phase 1: baseline (done)

- Stim circuit-level noise model, rotated surface code memory, d = 3, 5, 7
- PyMatching threshold sweep with sinter: `make baseline`, `make plot`
- Result: threshold about 0.73% (crossing of d = 5 and d = 7)

## Phase 2: C++ Union-Find decoder (v0 done)

Done:
- Union-Find growth + peeling, dependency free, reads graphs exported from Stim
- Hard invariant (correction reproduces syndrome): 100% on repetition and
  surface code d = 3, 5, 7
- Accuracy and latency benchmarked against PyMatching: `make bench`

Next:
- [ ] Weighted growth from the detector error model weights (closes most of
      the gap to MWPM under circuit-level noise)
- [ ] Per-cluster boundary lists instead of scanning every edge each round
- [ ] Streaming mode: decode a window of rounds, commit, slide; report
      p50/p99 tail latency, the metric NVIDIA uses for real-time decoders
- [ ] Plug into sinter as a custom decoder to get UF threshold curves directly
- [ ] Property-based fuzzing: random graphs + random syndromes, invariant and
      differential checks against PyMatching

## Phase 3: hardware (1D done)

Done:
- Golden model, VHDL RTL, self-checking testbench, bit-exact at N = 8, 16, 32
- Synthesis (ghdl + yosys) and place and route (nextpnr, iCE40 HX8K)
- Measured bottleneck: ripple prefix/suffix chains, Fmax drops with N

Next:
- [ ] Parallel segmented prefix scan, O(log N) depth per round
- [ ] 2D surface code core (d = 3, then 5) with the time dimension, vectors
      from Stim through the C++ decoder as golden model
- [ ] Synthesize in Vivado (free edition) for an Artix-7 to get 7-series timing
- [ ] Optional: run on a real board (Basys 3 or Arty A7) with a UART harness

## Phase 4: showcase

- [ ] Web demo: inject errors on a surface code, watch clusters grow
      (C++ compiled to WebAssembly, React front end)
- [ ] Write-up: what building a real-time decoder from scratch taught me,
      with the honest limitations
- [ ] One upstream contribution (Stim, PyMatching or Deltakit), even small

## Definition of done for the portfolio

- [x] One graph at the top of the README: logical vs physical error rate
- [x] A latency table (CPU, and ns per cycle after place and route)
- [x] Tests that prove correctness, not just that code runs
- [x] An honest limitations section
- [ ] The CV line: "Real-time QEC decoder (C++ and VHDL), N ns per round at
      d = 5, validated against PyMatching on Stim circuit-level noise"
