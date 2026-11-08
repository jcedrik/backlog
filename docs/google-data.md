# Decoding Google's surface code experiments

Everything else in this repository is measured on simulated noise. Google
Quantum AI published the raw detection events of its below-threshold surface
code experiment on the Willow processor ([Nature 638, 920 (2025)](https://doi.org/10.1038/s41586-024-08449-y),
data on [Zenodo](https://doi.org/10.5281/zenodo.13273331), CC-BY-4.0), so
backlog can be run on a real machine's syndromes, next to PyMatching, with
one command. This page is how.

## The data

The Zenodo record holds four archives (112 GB). Only one is needed:

| archive | size | md5 | content |
|---|---|---|---|
| `google_105Q_surface_code_d3_d5_d7.zip` | 5.7 GB | `21fa6ad35b395d838ebcdbc92e364a12` | the 105-qubit processor: nine distance-3 codes, four distance-5 codes, one distance-7 code, 10 to 250 rounds each |

Download it from the record page (the download link is a plain HTTP file, so
`wget -c` resumes), check it, and unzip it into `data/google/` (ignored by
git):

```bash
mkdir -p data/google
md5sum google_105Q_surface_code_d3_d5_d7.zip     # 21fa6ad35b395d838ebcdbc92e364a12
unzip -q google_105Q_surface_code_d3_d5_d7.zip -d data/google
find data/google -name detection_events.b8 | head -3
```

Every experiment is a folder `<patch>/<basis>/r<rounds>/`, for instance
`d5_at_q4_7/Z/r50/`, with the circuit that ran (`circuit_ideal.stim`),
Google's noise model of it (`circuit_noisy_si1000_p1.stim`), the detection
events (`detection_events.b8`, Stim's bit-packed format) and the measured
logical flips (`obs_flips_actual.b8`). The script finds the experiments by
these names, so if the archive unzips into a subfolder, point `DATA` at it.

## Running

```bash
make google-data                          # all patches, all durations, about an hour
make google-data DATA=/path/to/unzipped   # the data elsewhere
python python/google_data.py data/google --patches d7_at_q6_7 --rounds 50 250   # a subset
make google-data-plot                     # redraw the figure, print the table
```

Results go to `results/google/google_data.csv` (one row per experiment,
decoding graph and decoder) and `results/google/google_data.png`. The terminal
gets a running log and, at the end, the table of logical errors per round and
the error suppression factor Λ.

## What is decoded, and how

Two decoding graphs, both with the X/Z split of
[the decomposition write-up](correlated-decoding-decomposition.md):

- **si1000**: Google's noise model of each circuit, as shipped. Every edge
  probability comes from the device's calibrated error rates through the
  SI1000 model.
- **pij**: the same graph, with every edge probability measured from the
  detection events themselves. For detectors *i* and *j*, the correlation of
  their events gives the probability of the edge between them,
  p_ij = ½ − ½ √(1 − 4 (⟨x_i x_j⟩ − ⟨x_i⟩⟨x_j⟩) / (1 − 2⟨x_i⟩ − 2⟨x_j⟩ + 4⟨x_i x_j⟩)),
  and each detector's marginal then fixes its boundary edge (the p_ij method
  of Google's 2021 repetition code paper). Correlated decoding on this graph
  keeps the si1000 model's conditional probabilities between edges, since
  pairwise statistics cannot see them.

Five decoders on each: PyMatching, PyMatching with correlations (si1000 only,
it needs the hyperedges), backlog's Union-Find, correlated Union-Find (α = 1)
and the regularized rule (α = 0.5, the value of α*(p) around Google's error
rates). All of them decode the same shots, bit-packed, and the decoding time
per shot is recorded with the errors.

If an experiment folder carries predictions from Google's own decoders
(a `decoding_results/` folder with `*predicted*.b8` or `.01` files), they are
scored on the same shots and listed with `dem = google`.

Telling X checks from Z checks needs no coordinates: `backlog.detector_basis`
reads it off the model (the graphlike pieces of a decomposed model never join
the two types), which is what lets `decompose_by_basis` work on Google's
circuits as it does on Stim's.

## Reading the numbers

The logical error per round is ε = (1 − (1 − 2 P_L)^(1/rounds)) / 2 for one
experiment; the table fits ε for each patch across its durations
(1 − 2 P_L(t) = (1 − 2ε)^t), averages the patches of each distance as the
paper does, and reports Λ = ε_d / ε_(d+2) from a fit over d = 3, 5, 7.

One thing to keep in mind when comparing with the paper: Google's headline
numbers (ε_7 = 0.143% ± 0.003% per round, Λ = 2.14 ± 0.02) come from its own
decoder with priors tuned on the data (reinforcement learning on the matching
graph weights) and, for the best accuracy, a neural network. Everything here
uses the shipped noise model or plain pairwise statistics. The fair comparison
is backlog against PyMatching on the same graph, which is what the figure
shows; the paper's point is drawn for scale.
