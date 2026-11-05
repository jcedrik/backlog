"""Builds and runs examples/quickstart.ipynb (outputs included, so GitHub shows them).

    python examples/make_quickstart.py
"""
from pathlib import Path

import nbformat
from nbclient import NotebookClient

HERE = Path(__file__).resolve().parent

cells = [
    ("md", """# backlog in five minutes

A real-time Union-Find decoder for the surface code, with correlated decoding,
streaming window decoders and [sinter](https://github.com/quantumlib/Stim/tree/main/glue/sample)
integration.

```bash
pip install backlog-qec        # or, from a clone: make install
```

Every cell below runs in a few seconds on a laptop."""),
    ("code", """import time
import numpy as np
import pymatching
import stim
import backlog

def memory(d, p, rounds=None):
    \"\"\"Rotated surface code memory, every noise channel at p (Stim's generator).\"\"\"
    return stim.Circuit.generated(
        "surface_code:rotated_memory_z", distance=d, rounds=rounds or d,
        after_clifford_depolarization=p, before_measure_flip_probability=p,
        after_reset_flip_probability=p, before_round_data_depolarization=p)

circuit = memory(d=5, p=0.003)
dets, obs = circuit.compile_detector_sampler(seed=1).sample(200_000, separate_observables=True)
print(f"{dets.shape[0]} shots, {dets.shape[1]} detectors")"""),
    ("md", """## 1. Decode, and compare with PyMatching

`UnionFindDecoder.from_circuit` builds the decoding graph from Stim's detector
error model; edges are weighted by log-likelihood."""),
    ("code", """dem = circuit.detector_error_model(decompose_errors=True)
uf = backlog.UnionFindDecoder.from_dem(dem)
pm = pymatching.Matching.from_detector_error_model(dem)

def logical_errors(predict):
    t0 = time.perf_counter()
    pred = predict(dets)
    seconds = time.perf_counter() - t0
    return int(np.sum(np.any(pred != obs, axis=1))), seconds / len(dets) * 1e6

for name, predict in [("backlog Union-Find", uf.decode_batch), ("PyMatching", pm.decode_batch)]:
    errors, us = logical_errors(predict)
    print(f"{name:20s} {errors:5d} logical errors   {us:5.2f} us per shot")"""),
    ("md", """## 2. Correlated decoding

A Y error shows up in the graph as an X edge and a Z edge. Correlated decoding
decodes once, makes the partners of the edges it used cheaper, and decodes
again (as PyMatching's `enable_correlations=True`). backlog first splits every
error into exactly its X part and its Z part (`decompose_by_basis`), which is
what makes the correlations informative."""),
    ("code", """cuf = backlog.UnionFindDecoder.from_dem(dem, correlated=True)
pmc = pymatching.Matching.from_detector_error_model(backlog.decompose_by_basis(dem),
                                                    enable_correlations=True)
for name, predict in [("backlog correlated UF", cuf.decode_batch),
                      ("PyMatching correlated", lambda s: pmc.decode_batch(s, enable_correlations=True))]:
    errors, us = logical_errors(predict)
    print(f"{name:22s} {errors:5d} logical errors   {us:5.2f} us per shot")"""),
    ("md", """## 3. The distance a correlated decoder loses, and how to get it back

A distance-5 code must correct every pair of faults. Below, every pair of
faults of the circuit (1.9 million of them) is decoded. Plain Union-Find gets
none wrong; standard correlated decoding gets hundreds wrong, which costs a
unit of distance deep below threshold (Hernando de la Fuente et al.,
arXiv:2610.26785). Scaling the conditional probabilities by `alpha` < 1 fixes
them."""),
    ("code", """model = backlog.decompose_by_basis(memory(d=5, p=1e-3).detector_error_model(decompose_errors=True))
n = model.num_detectors
rows, flips = [], []
for inst in model.flattened():
    if inst.type != "error":
        continue
    row, flip = np.zeros(n, dtype=bool), False
    for t in inst.targets_copy():
        if t.is_relative_detector_id():
            row[t.val] ^= True
        elif t.is_logical_observable_id():
            flip ^= True
    rows.append(np.packbits(row, bitorder="little"))
    flips.append(flip)
rows, flips = np.array(rows), np.array(flips)
i, j = np.triu_indices(len(rows), k=1)
pairs, truth = rows[i] ^ rows[j], flips[i] ^ flips[j]
print(f"{len(rows)} faults, {len(pairs)} pairs")
for label, kwargs in [("plain UF", {}), ("correlated, alpha = 1", dict(correlated=True)),
                      ("correlated, alpha = 0.05", dict(correlated=True, alpha=0.05))]:
    dec = backlog.UnionFindDecoder.from_dem(model, **kwargs)
    wrong = int(np.sum((dec.decode_batch_bit_packed(pairs)[:, 0] & 1).astype(bool) != truth))
    print(f"{label:26s} {wrong:4d} pairs decoded wrongly")"""),
    ("md", """## 4. Streaming: windows over a long memory

A real decoder cannot wait for the end of the experiment. The sliding-window
decoder commits `commit` rounds at a time with `buffer` rounds of look-ahead;
the parallel window decoder decodes independent windows on several threads.
Both give the same answer as decoding the whole shot, up to the window
approximation, and work with `correlated=True` too."""),
    ("code", """long = memory(d=5, p=0.003, rounds=100)
long_dem = long.detector_error_model(decompose_errors=True)
s, o = long.compile_detector_sampler(seed=2).sample(20_000, separate_observables=True)
decoders = {
    "whole shot": backlog.UnionFindDecoder.from_dem(long_dem, correlated=True),
    "sliding window": backlog.SlidingWindowDecoder.from_dem(long_dem, commit=5, buffer=5, correlated=True),
    "parallel windows, 2 threads": backlog.ParallelWindowDecoder.from_dem(
        long_dem, commit=5, buffer=5, threads=2, correlated=True),
}
for name, dec in decoders.items():
    print(f"{name:28s} {int(np.sum(np.any(dec.decode_batch(s) != o, axis=1))):4d} logical errors")"""),
    ("md", """## 5. sinter

The decoders plug into sinter, Stim's sampling tool, by name."""),
    ("code", """import sinter
tasks = [sinter.Task(circuit=memory(d, 0.004), json_metadata={"d": d}) for d in (3, 5)]
stats = sinter.collect(num_workers=2, tasks=tasks, max_shots=20_000,
                       decoders=["backlog-uf", "backlog-uf-corr"],
                       custom_decoders=backlog.sinter_decoders())
for st in sorted(stats, key=lambda st: (st.decoder, st.json_metadata["d"])):
    print(f"{st.decoder:16s} d={st.json_metadata['d']}  {st.errors / st.shots:.2e} per shot")"""),
    ("md", """More: [README](../README.md) for the results, [docs/decoder.md](../docs/decoder.md)
for how the decoder works, [docs/related-work.md](../docs/related-work.md) for
the state of the art."""),
]

nb = nbformat.v4.new_notebook()
nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
for kind, text in cells:
    nb.cells.append(nbformat.v4.new_markdown_cell(text) if kind == "md"
                    else nbformat.v4.new_code_cell(text))
NotebookClient(nb, timeout=600, kernel_name="python3",
               resources={"metadata": {"path": str(HERE)}}).execute()
out = HERE / "quickstart.ipynb"
nbformat.write(nb, out)
print(f"wrote {out}")
