# backlog

**A real-time quantum error correction decoder, built from scratch: from
simulation to FPGA.**

[Version française](README.fr.md)

![PyMatching vs backlog Union-Find, weighted and unweighted, rotated surface code under circuit-level noise](results/threshold_comparison.png)

## Why "backlog"

A fault-tolerant quantum computer has to decode errors faster than they
appear. If the decoder is slower than the stream of syndrome data, the backlog
grows and the computation slows down exponentially. On superconducting chips a
QEC cycle lasts about 1 microsecond, so the decoder has a hard real-time
budget. This project builds a decoder for that problem and measures it
honestly at every step.

## Results

Rotated surface code memory, circuit-level noise from Stim, rounds = d. All
decoders run through sinter on the same circuits: 12.8 million shots, 96 data
points, at least 68 logical errors per point. Error bars are one standard
deviation. Reproduce with `make compare` and `make latency`.

| | PyMatching 2 (MWPM) | backlog UF, weighted | backlog UF, unweighted |
|---|---|---|---|
| crossing of d = 7 and d = 9 | 0.75 ± 0.02% | 0.77 ± 0.03% | 0.66 ± 0.04% |
| logical error rate vs PyMatching, d = 3 and 5 | 1 | 0.96 to 1.11 | 1.16 to 2.08 |
| logical error rate vs PyMatching, d = 7 and 9 | 1 | 1.05 to 1.20 | 1.61 to 3.66 |
| speed vs PyMatching, d = 3 | 1 | 0.82 to 0.87 | |
| speed vs PyMatching, d = 5 to 13, p = 0.1% | 1 | 1.02 to 1.19 | |
| speed vs PyMatching, d = 5 to 13, p = 0.3% | 1 | 0.98 to 1.04 | |

- **Weighting is what makes Union-Find competitive.** Growing each edge in
  proportion to its log-likelihood brings Union-Find from up to 3.7 times
  PyMatching's logical error rate to within 0 to 20%, with the same threshold
  within error bars.
- **Speed: parity with PyMatching 2, not a blowout.** Sparse Blossom is very
  fast. On one core, this decoder is 15 to 19% faster at p = 0.1% from d = 9,
  level at p = 0.3%, and slower at d = 3 (repeat runs agree within about 5%).
  Union-Find's real advantage is in hardware, where its local rules map to
  parallel logic.
- **Same answer as a slow, obviously correct reference.** The fast decoder
  returns the exact same correction as the reference implementation on 75,000
  fuzzed cases, and mutation testing shows the tests catch 10 out of 10
  injected bugs. How it works: [docs/decoder.md](docs/decoder.md).

![Union-Find logical error rate relative to PyMatching](results/accuracy_vs_pymatching.png)

### Decoding time

![Decoding time per round of syndrome data, backlog UF vs PyMatching](results/latency.png)

One thread, Intel Xeon at 2.1 GHz (2-core cloud VM), 20,000 shots per point.
Times are per shot, which holds d rounds of syndrome data:

| d | p | UF p50 | UF p99 | UF mean | PyMatching mean |
|---|---|---|---|---|---|
| 5 | 0.1% | 0.32 µs | 3.1 µs | 0.46 µs | 0.49 µs |
| 9 | 0.1% | 2.3 µs | 11.4 µs | 2.9 µs | 3.5 µs |
| 13 | 0.1% | 8.2 µs | 26.1 µs | 9.5 µs | 11.0 µs |
| 5 | 0.3% | 1.1 µs | 8.1 µs | 1.6 µs | 1.6 µs |
| 9 | 0.3% | 11.3 µs | 41.9 µs | 11.8 µs | 12.2 µs |
| 13 | 0.3% | 38.4 µs | 97.6 µs | 40.0 µs | 40.7 µs |

Divide by d to compare with a 1 µs QEC cycle. At p = 0.1% the mean keeps up
all the way to d = 13, but the p99 tail falls behind from d = 9. At p = 0.3%,
the mean falls behind from d = 9. One CPU core cannot hold real time at
scale, whichever of the two decoders runs on it: that is the backlog problem,
and why the next steps are streaming windows and hardware.

### Hardware (v0: 1D, unweighted)

Union-Find core in VHDL, bit-exact against its golden model on 6,000+ vectors
(N = 8, 16, 32). After place and route on an iCE40 HX8K: 94.5 MHz at N = 8,
about 10.6 ns per growth round. Fmax drops with N, and the cause is
identified: ripple XOR chains, fixable with a parallel prefix scan.
[Details](hardware/README.md).

## Use it

```bash
pip install .     # builds the C++ core: needs a C++17 compiler and CMake
```

```python
import stim
import backlog

p = 0.003
circuit = stim.Circuit.generated(
    "surface_code:rotated_memory_z", distance=5, rounds=5,
    after_clifford_depolarization=p, before_round_data_depolarization=p,
    before_measure_flip_probability=p, after_reset_flip_probability=p)
decoder = backlog.UnionFindDecoder.from_circuit(circuit)  # weighted by default

dets, obs = circuit.compile_detector_sampler().sample(100_000, separate_observables=True)
predictions = decoder.decode_batch(dets)
print("logical error rate:", (predictions != obs).any(axis=1).mean())

result = decoder.decode_detailed(dets[0])  # edges chosen, syndrome check
```

As a sinter decoder, from Python:

```python
stats = sinter.collect(num_workers=4, tasks=tasks, decoders=["backlog-uf"],
                       custom_decoders=backlog.sinter_decoders(), max_shots=10**6)
```

or from the command line:

```bash
sinter collect --circuits circuit.stim --decoders backlog-uf pymatching \
    --custom_decoders_module_function "backlog:sinter_decoders" \
    --max_shots 100000 --processes 4 --save_resume_filepath stats.csv
```

`backlog-uf-unweighted` is also available, for comparison.

## Reproduce

```bash
pip install -r requirements.txt && make install   # in a virtualenv
make test        # C++ tests and fuzzing, pytest, command-line checks
make mutants     # mutation testing, about a minute
make compare     # PyMatching vs Union-Find sweep with sinter (long, resumable)
make latency     # decoding time benchmark, on an idle machine
make hw-sim      # VHDL simulation, bit-exact (needs ghdl)
make hw          # VHDL synthesis, place and route (needs yosys, nextpnr-ice40)
make help        # everything else
```

## How correctness is checked

1. **Every correction is valid.** It must reproduce the observed syndrome.
   This is checked on every decode in every test, and the command-line
   decoder exits non-zero if it fails once.
2. **Fast equals reference.** `ReferenceDecoder` is a short, synchronous,
   obviously correct version of the algorithm. The fast event-driven decoder
   must return the same correction, edge for edge: 75,000 fuzzed cases on
   random graphs, plus real surface code error models.
3. **The tests catch bugs.** `make mutants` injects 10 realistic bugs one at
   a time (wrong growth rate, lost radius on merge, forest ignoring weights,
   off-by-one events...) and requires the suite to fail on each.
4. **Accuracy is gated.** pytest fails if weighted Union-Find exceeds 1.35
   times PyMatching's logical error rate at d = 5, p = 0.5% (20,000 shots);
   the command-line checks gate at 1.5 times on the repetition code and on
   the surface code at d = 3, 5, 7.
5. **Hardware.** The VHDL testbench compares every output to the golden model
   bit for bit, and checks the syndrome invariant independently.

All of it runs in CI on every push.

## Layout

```
src/backlog/   Python package: error model conversion, decoder API, sinter plugin
cpp/           C++17 core (no dependencies), reference decoder, tests, CLI, bindings
tests/         pytest: accuracy gates, sinter integration, fast equals reference
python/        experiments: circuits, sweeps, benchmarks, figures
hardware/      VHDL 1D core: golden model, RTL, testbench, synthesis, place and route
scripts/       command-line checks, mutation testing
results/       data and figures produced by the scripts
docs/          how the decoder works, roadmap
```

## Limitations

- **Throughput, not streaming latency.** Every number above decodes a whole
  memory experiment at once. A real-time decoder works on a sliding window of
  rounds as they arrive. That is the next milestone.
- **Speed parity, not a win.** At p = 0.3% this decoder only matches
  PyMatching 2 on one core.
- **Accuracy.** Weighted Union-Find still makes 5 to 20% more logical errors
  than matching at d = 7 and 9.
- **Shared machine.** Timings come from a 2-core cloud VM. Repeat runs agree
  within about 5%, but absolute times will differ on your machine.
- **Scope.** Graphlike error models only (Stim decomposes hyperedges), up to
  64 logical observables.
- **Hardware is v0.** The VHDL core is 1D, unweighted, code capacity. Its
  numbers are not comparable to 2D surface code decoders like Helios, and
  there is no board run yet: Fmax comes from place and route.

## Roadmap

Done: Stim + PyMatching baseline, weighted event-driven Union-Find with
sinter integration, 1D VHDL core. Next: sliding-window streaming decoding with
tail latency per round, a parallel prefix scan in the VHDL core, then a 2D
surface code core. Details in [docs/roadmap.md](docs/roadmap.md).

## References

- C. Gidney, *Stim: a fast stabilizer circuit simulator*, Quantum 5, 497 (2021)
- O. Higgott and C. Gidney, *Sparse Blossom: correcting a million errors per
  core second with minimum-weight matching*, Quantum 9, 1600 (2025)
- N. Delfosse and N. H. Nickerson, *Almost-linear time decoding algorithm for
  topological codes*, Quantum 5, 595 (2021)
- N. Delfosse and G. Zemor, *Linear-time maximum likelihood decoding of
  surface codes over the quantum erasure channel*, Phys. Rev. Research 2,
  033042 (2020)
- S. Huang, M. Newman and K. R. Brown, *Fault-tolerant weighted union-find
  decoding on the toric code*, Phys. Rev. A 102, 012419 (2020)
- N. Liyanage, Y. Wu, S. Tagare and L. Zhong, *FPGA-based distributed
  Union-Find decoder for surface codes*, IEEE TQE (2024)
- B. M. Terhal, *Quantum error correction for quantum memories*, Rev. Mod.
  Phys. 87, 307 (2015)

## License

MIT
