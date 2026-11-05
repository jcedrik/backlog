"""The browser demo (docs/demo) runs the same decoder as the C++ reference.

docs/demo/uf.js is a JavaScript port of the reference decoder (every edge
weight 2). On random errors at d = 3 to 11, it must give exactly the C++
reference's correction, edge for edge and in the same order, and both must
correct every set of at most (d - 1) / 2 errors at d = 3, 5 and 7.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

import backlog
from backlog.dem import GraphEdges

NODE = shutil.which("node")
UF_JS = Path(__file__).resolve().parent.parent / "docs" / "demo" / "uf.js"

SCRIPT = r"""
const UF = require(process.argv[2]);
let seed = 12345;
const rand = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
const out = [];
for (const d of [3, 5, 7, 9, 11]) {
  const code = UF.surfaceCode(d);
  const cases = [];
  for (let s = 0; s < 400; s++) {
    const p = 0.02 + 0.2 * rand();
    const errors = [];
    for (let q = 0; q < d * d; q++) if (rand() < p) errors.push(q);
    const fired = UF.syndrome(code, errors);
    const r = UF.decode(code, fired);
    cases.push({ fired, correction: r.correction, flip: r.flip });
  }
  // every set of at most (d - 1) / 2 errors, at d = 3, 5, 7
  let small = 0, wrong = 0;
  if (d <= 7) {
    const t = (d - 1) / 2, Q = d * d;
    const visit = (start, chosen) => {
      if (chosen.length > 0) {
        small++;
        const r = UF.decode(code, UF.syndrome(code, chosen));
        if (UF.logicalFlip(code, chosen, r.correction)) wrong++;
      }
      if (chosen.length === t) return;
      for (let q = start; q < Q; q++) visit(q + 1, [...chosen, q]);
    };
    visit(0, []);
  }
  out.push({ d, n: code.n, edges: code.edges.map((e) => [e.a, e.b, e.obs]), cases, small, wrong });
}
process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_demo_decoder_is_the_reference_decoder(tmp_path):
    script = tmp_path / "check.js"
    script.write_text(SCRIPT)
    runs = json.loads(subprocess.check_output([NODE, str(script), str(UF_JS)], timeout=600))
    for run in runs:
        d, n = run["d"], run["n"]
        a, b, obs = (np.array([e[k] for e in run["edges"]]) for k in range(3))
        edges = GraphEdges(num_detectors=n, num_observables=1, a=a.astype(np.int32),
                           b=b.astype(np.int32), obs=obs.astype(np.uint64),
                           p=np.full(len(a), 0.1), skipped_hyperedges=0,
                           conflicting_parallel_edges=0)
        ref = backlog.ReferenceDecoder(edges, weighted=False)
        for case in run["cases"]:
            syndrome = np.zeros(n, dtype=bool)
            syndrome[case["fired"]] = True
            r = ref.decode_detailed(syndrome)
            assert r.syndrome_ok
            assert list(r.correction) == case["correction"], f"d={d} fired={case['fired']}"
            assert int(r.observables[0]) == case["flip"]
        if d <= 7:
            assert run["small"] > 0 and run["wrong"] == 0, f"d={d}: {run['wrong']} small errors missed"
