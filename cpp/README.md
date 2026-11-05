# C++ core

The weighted Union-Find decoder and its streaming versions (sliding windows
on one core, parallel windows on a thread pool), with no dependencies (C++17
standard library and threads only). The same code backs three
front ends:

- the Python package `backlog` (pybind11 bindings in `bindings/module.cpp`,
  built by `pip install .` from the repo root);
- `backlog_decode`, a command-line decoder that reads plain-text files, so it
  runs without Python or Stim;
- `backlog_tests`, the C++ test suite.

How the algorithm works and why the fast version equals the reference:
[../docs/decoder.md](../docs/decoder.md).

## Files

```
include/backlog/graph.hpp     DecodingGraph: edges, probabilities, integer weights
include/backlog/decoder.hpp   UnionFindDecoder (fast), ReferenceDecoder (spec)
include/backlog/window.hpp    SlidingWindowDecoder, ParallelWindowDecoder (streaming)
src/graph.cpp                 weights from log-likelihoods, validation, file loader
src/decoder.cpp               event-driven weighted growth, peeling
src/reference.cpp             synchronous unit rounds, the specification
src/window.cpp                window plan, commits, thread pool, both window decoders
src/main.cpp                  command-line decoder with latency statistics
tests/test_decoder.cpp        unit tests + fuzzing: fast == reference, both window decoders
bindings/module.cpp           Python module backlog._core
```

## Build and test

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
cd build && ctest --output-on-failure
```

Expected output of `./build/backlog_tests`:

```
fuzz: 75000 random cases, 0 mismatches between fast and reference
windows: 75000 random cases, 0 invalid corrections, 0 inconsistent, 0 differences with whole-shot decoding
parallel windows: 75000 random cases, 0 invalid corrections, 0 inconsistent, 0 depend on the thread count, 0 differences with whole-shot decoding
306190 checks, 0 failures
ALL C++ TESTS PASSED
```

From the repo root, `make mutants` checks that the suite fails on each of 18
injected bugs, and `make tsan` runs the suite under ThreadSanitizer
(`-DBACKLOG_TSAN=ON`).

## Command-line decoder

```bash
python3 ../python/export_decoding_graph.py --code surface --distance 5 --rounds 5 --p 0.005
./build/backlog_decode testdata/graph.txt testdata/dets.txt testdata/preds_cpp.txt
python3 ../python/compare_decoders.py testdata
```

`backlog_decode [--unweighted] [--resolution R] graph dets preds` decodes
every shot, prints the syndrome check and per-shot latency, and exits with
code 3 if any correction fails to reproduce its syndrome. File formats:

- `graph.txt`: a header `num_detectors num_observables`, then one edge per
  line, `a b obs_mask p`. The boundary node is `num_detectors`. Without a
  probability column, all weights are equal (unweighted decoding).
- `dets.txt`: one shot per line, the ids of the fired detectors.
- `preds.txt` (output): one shot per line, the predicted observable flips.

## Results

All results live in the main [README](../README.md): accuracy and threshold
against PyMatching on 12.8 million shots, streaming accuracy and backlog,
decoding time from d = 3 to 13.
