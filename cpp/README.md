# C++ Union-Find decoder

A from-scratch Union-Find decoder (Delfosse-Nickerson growth + peeling) for
any graphlike decoding problem. No dependencies: it reads a decoding graph and
detection events as plain text, exported from a Stim detector error model by
`python/export_decoding_graph.py`.

## Build and run

```bash
cmake -S . -B build && cmake --build build -j
python3 ../python/export_decoding_graph.py --code repetition --distance 5 --rounds 5 --p 0.02
./build/backlog_decode testdata/graph.txt testdata/dets.txt testdata/preds_cpp.txt
python3 ../python/compare_decoders.py
```

The binary prints the syndrome check and per-shot latency, and exits non-zero
if any correction fails to reproduce its syndrome.

## Results (from this repo, 20,000 shots each)

**Hard invariant.** Every correction reproduces its syndrome: 100% on the
repetition code and on the rotated surface code at d = 3, 5 and 7.

**Accuracy vs PyMatching** (circuit-level noise, rounds = d), from
`python/uf_vs_pymatching.py`, 50,000 shots per point:

| p | d=3 UF / MWPM | d=5 UF / MWPM | d=7 UF / MWPM |
|---|---|---|---|
| 0.2% | 1.21x | 1.96x | 4.00x |
| 0.4% | 1.20x | 1.82x | 2.42x |
| 0.6% | 1.18x | 1.78x | 2.21x |

Unweighted UF still suppresses errors with distance below its own threshold
(at p = 0.1%: 1.0e-3, 2.6e-4, 4.0e-5 for d = 3, 5, 7), but its threshold sits
around 0.5 to 0.6% versus about 0.73% for PyMatching, and the gap grows with
d. That is the expected cost of ignoring edge weights under circuit-level
noise, and it is the next milestone. At d = 7 and p = 0.1% there are only one
or two errors in 50,000 shots, so that point is not statistically meaningful.

**Latency** (one CPU core, per shot including all d rounds):

| code | mean | p50 | p99 |
|---|---|---|---|
| repetition d=5 | 1.7 us | 1.3 us | 6.2 us |
| surface d=3 | 2.0 us | 1.5 us | 6.3 us |
| surface d=5 | 12.5 us | 11.4 us | 40.9 us |
| surface d=7 | 35.9 us | 32.3 us | 96.4 us |

Per round, that is about 0.7 us at d=3 but about 5 us at d=7, so this naive
implementation already falls behind a 1 us superconducting cycle at d >= 5.
That is the backlog problem this project is named after.

## Known limitations (and what fixes them)

- **Unweighted growth.** Every edge grows at the same speed. Fix: weighted
  growth using the log-likelihood weights from the detector error model.
- **O(E) scan per growth round.** Each round touches every edge. Fix: keep a
  boundary list per cluster and only grow those edges (the almost-linear
  version from the original paper).
- **Single boundary node.** Fine for memory experiments; logical-aware
  boundaries come later.
