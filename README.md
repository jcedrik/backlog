# backlog

**A real-time quantum error correction decoder, built from scratch: from
simulation to FPGA.**

[Version française](README.fr.md)

![Threshold of the rotated surface code, Stim + PyMatching baseline](results/threshold.png)

## Why "backlog"

A fault-tolerant quantum computer has to decode errors faster than they
appear. If the decoder is slower than the stream of syndrome data, the backlog
grows and the computation slows down exponentially. On superconducting chips a
QEC cycle lasts about 1 microsecond, so the decoder has a hard real-time
budget. This project builds a decoder for that problem and measures it
honestly at every step.

## Status

| phase | what | status |
|---|---|---|
| 1 | Stim + PyMatching baseline, threshold sweep | done |
| 2 | Union-Find decoder in C++, benchmarked against PyMatching | v0 done |
| 3 | Union-Find decoder in VHDL, verified, synthesized, placed and routed | 1D done |
| 4 | Web demo, write-up, upstream contribution | next |

Details and next steps: [docs/roadmap.md](docs/roadmap.md).

## Key results

All numbers are reproducible from this repo.

- **Baseline.** Rotated surface code, circuit-level noise, d = 3, 5, 7 with
  PyMatching: threshold about **0.73%** (graph above, `make baseline`).
- **C++ Union-Find.** Every correction reproduces its syndrome (100% on
  repetition and surface code d = 3, 5, 7). It still suppresses errors with
  distance below its own threshold, which sits around 0.5 to 0.6%, below
  PyMatching's because growth is unweighted. Weighted growth is next.
  [Details](cpp/README.md).
- **VHDL.** Bit-exact against the golden model on 6,000+ vectors (N = 8, 16,
  32). After place and route on an iCE40 HX8K: 94.5 MHz at N = 8, about
  10.6 ns per growth round. Fmax drops with N, and the cause is identified:
  ripple XOR chains, fixable with a parallel prefix scan.
  [Details](hardware/README.md).

![Union-Find (C++) vs PyMatching](results/uf_vs_pymatching.png)

## Quickstart

```bash
pip install -r requirements.txt     # stim, pymatching, sinter, numpy, matplotlib
make quick       # threshold pipeline smoke test (seconds)
make test        # build the C++ decoder and run its test suite
make hw-sim      # VHDL simulation, bit-exact (needs ghdl)
make baseline    # full threshold sweep (minutes), then: make plot
make bench       # C++ Union-Find vs PyMatching on the surface code
make hw          # VHDL synthesis + place and route (needs yosys, nextpnr-ice40)
```

## Layout

```
python/      Stim circuits, threshold sweep, plots, graph export, benchmarks
cpp/         Union-Find decoder (C++17, no dependencies) + CLI with latency stats
hardware/    golden model, VHDL RTL, self-checking testbench, synth + P&R flow
results/     data and figures produced by the scripts
docs/        roadmap
scripts/     test suite run by CI
```

## How correctness is checked

Two separate questions, two separate checks:

1. **Is every correction valid?** The correction must reproduce the observed
   syndrome. This is a hard invariant: the C++ binary exits non-zero if it
   fails once, and the VHDL testbench checks it on every vector, independently
   of the golden model.
2. **Is it a good decoder?** Logical error rate against PyMatching on the same
   shots. Different algorithms, so they are expected to differ; the gap is
   reported, not hidden.

## Limitations

- The C++ decoder uses unweighted growth, so it loses threshold under
  circuit-level noise.
- The VHDL core is 1D (code-capacity repetition code). Its numbers are not
  comparable to 2D surface code decoders like Helios.
- No real hardware run yet. Fmax comes from place and route, not a board.

## References

- C. Gidney, *Stim: a fast stabilizer circuit simulator*, Quantum 5, 497 (2021)
- O. Higgott and C. Gidney, *Sparse Blossom: correcting a million errors per
  core second with minimum-weight matching*, Quantum 9, 1600 (2025)
- N. Delfosse and N. H. Nickerson, *Almost-linear time decoding algorithm for
  topological codes*, Quantum 5, 595 (2021)
- N. Delfosse and G. Zemor, *Linear-time maximum likelihood decoding of
  surface codes over the quantum erasure channel*, Phys. Rev. Research 2,
  033042 (2020)
- N. Liyanage, Y. Wu, S. Tagare and L. Zhong, *FPGA-based distributed
  Union-Find decoder for surface codes*, IEEE TQE (2024)
- B. M. Terhal, *Quantum error correction for quantum memories*, Rev. Mod.
  Phys. 87, 307 (2015)

## License

MIT
