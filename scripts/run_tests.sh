#!/usr/bin/env bash
# End-to-end checks of the C++ command-line decoder (no Python bindings involved).
#
# For each code: export a decoding graph and shots from Stim, decode them with
# ./cpp/build/backlog_decode (weighted Union-Find), then compare with PyMatching.
#
# Gate 1: every correction must reproduce its syndrome. backlog_decode exits
#         non-zero otherwise, which stops this script.
# Gate 2: logical error rate at most 1.5x PyMatching's on the same shots.
#
# The unit tests, the fuzzing against the reference decoder and the Python
# tests live in `make test-cpp` and `make test-py`.
set -euo pipefail
cd "$(dirname "$0")/.."

BIN=./cpp/build/backlog_decode
[ -x "$BIN" ] || { echo "build first: make cpp"; exit 1; }
python3 -c "import backlog" 2>/dev/null || { echo "install first: make install"; exit 1; }

check() {  # name, export arguments...
  local name="$1"; shift
  local dir="cpp/testdata/$name"
  echo "== $name"
  python3 python/export_decoding_graph.py "$@" --shots 20000 --seed 1 --out "$dir" > /dev/null
  "$BIN" "$dir/graph.txt" "$dir/dets.txt" "$dir/preds_cpp.txt"
  python3 python/compare_decoders.py "$dir" --max-ratio 1.5
}

check repetition_d5 --code repetition --distance 5 --rounds 5 --p 0.02
for d in 3 5 7; do
  check "surface_d$d" --code surface --distance "$d" --rounds "$d" --p 0.005
done

echo "ALL CLI TESTS PASSED"
