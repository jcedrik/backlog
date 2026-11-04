#!/usr/bin/env bash
# Test suite for the C++ Union-Find decoder.
#
# Gate 1 (all codes): every correction must reproduce its syndrome.
#   backlog_decode exits non-zero otherwise, which stops this script.
# Gate 2 (repetition code only): UF logical error rate within 2x of PyMatching.
#   On the circuit-level surface code, unweighted UF is expected to be worse
#   than MWPM (see cpp/README.md), so accuracy there is reported, not gated.
set -euo pipefail
cd "$(dirname "$0")/.."

BIN=./cpp/build/backlog_decode
[ -x "$BIN" ] || { echo "build first: make cpp"; exit 1; }

echo "== repetition code d=5: invariant + accuracy gate"
python3 python/export_decoding_graph.py --code repetition --distance 5 \
        --rounds 5 --p 0.02 --shots 20000 --out cpp/testdata > /dev/null
"$BIN" cpp/testdata/graph.txt cpp/testdata/dets.txt cpp/testdata/preds_cpp.txt
python3 python/compare_decoders.py cpp/testdata

for d in 3 5 7; do
  echo "== surface code d=$d: invariant gate"
  dir="cpp/testdata_surface_d$d"
  python3 python/export_decoding_graph.py --code surface --distance "$d" \
          --rounds "$d" --p 0.005 --shots 5000 --out "$dir" > /dev/null
  "$BIN" "$dir/graph.txt" "$dir/dets.txt" "$dir/preds_cpp.txt"
done

echo "ALL TESTS PASSED"
