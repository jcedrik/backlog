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
the error suppression factor Λ. The CSV is rewritten after every experiment,
so a run stopped with Ctrl-C keeps what it did, and `--resume` decodes only
the experiments the CSV does not hold yet:

```bash
python python/google_data.py data/google --resume --patches d7_at_q6_7   # the distance-7 code, hours
```

The distance-3 and distance-5 experiments take about two hours on a laptop;
the distance-7 code, with 48 detectors per round and up to 250 rounds, takes
several more.

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
scored on the same shots and listed with `dem = google`;
`--rescore-google` does only that step, in minutes.

Telling X checks from Z checks needs no coordinates: `backlog.detector_basis`
reads it off the model (the graphlike pieces of a decomposed model never join
the two types), which is what lets `decompose_by_basis` work on Google's
circuits as it does on Stim's.

## Results

All 420 experiments, 21 million shots, decoded on one laptop core
(`results/google/google_data.csv`, `results/google/google_data.png`).
Logical error per round, fitted per code and basis across durations, then
averaged per distance:

| Decoding graph | Decoder | d = 3 | d = 5 | d = 7 | Λ (fit) |
|---|---|---|---|---|---|
| SI1000 | PyMatching | 0.976% | 0.655% | 0.411% | 1.54 |
| SI1000 | PyMatching, correlated | 0.835% | 0.439% | 0.228% | 1.91 |
| SI1000 | backlog Union-Find | 0.979% | 0.682% | 0.445% | 1.48 |
| SI1000 | backlog, correlated, α = 1 | 0.838% | 0.459% | 0.248% | 1.84 |
| SI1000 | backlog, correlated, α = 0.5 | 0.822% | 0.452% | 0.247% | 1.82 |
| p_ij | PyMatching | 0.932% | 0.619% | 0.393% | 1.54 |
| p_ij | backlog Union-Find | 0.955% | 0.654% | 0.433% | 1.49 |
| p_ij | backlog, correlated, α = 1 | 0.836% | 0.454% | 0.255% | 1.81 |
| p_ij | backlog, correlated, α = 0.5 | 0.826% | 0.467% | 0.264% | 1.77 |

Google ships the predictions of five of its decoders with the distance-7
experiments (`decoding_results/`), scored here on the same shots:

| Google's decoder (shipped predictions) | d = 7 |
|---|---|
| correlated matching, SI1000 prior | 0.224% |
| correlated matching, RL-optimized prior | 0.206% |
| Harmony (ensemble), SI1000 prior | 0.207% |
| Harmony, RL-optimized prior | 0.197% |
| Libra (ensembled matching synthesis), RL-optimized prior | 0.169% |

Three checks that the pipeline measures what it should:

- PyMatching with correlations on the shipped SI1000 model is within 2% of
  Google's own correlated matching decoder with the same prior (0.228% vs
  0.224%): the graph, the X/Z split and the fit agree with Google's.
- The shipped Libra predictions give ε₇ = 0.169% with this fit; the paper
  reports 0.171% ± 0.003% for that decoder (Λ = 2.04). The paper's headline,
  ε₇ = 0.143% and Λ = 2.14, is its neural network decoder, not shipped.
- The X/Z split (`detector_basis`) worked on every one of the 420 circuits,
  with no hyperedge left undecomposed.

What it says about backlog: Union-Find is equal to matching at d = 3, 4%
behind at d = 5 and 8% behind at d = 7, plain and correlated alike;
correlated decoding removes 16%, 34% and 45% of the logical errors at
d = 3, 5, 7, and α = 0.5 is as good as or better than α = 1 throughout. On
the d = 7 experiments of 110 rounds and more, backlog decodes in 3.2 µs per
shot per round against PyMatching's 4.0 (7.6 against 11.1 with correlations),
batch decoding on one laptop core, so not a real-time latency. Measured edge
probabilities help the plain decoders by 3 to 4% but not the correlated ones,
whose conditional probabilities still come from the SI1000 model.

## Reading the numbers

The logical error per round is ε = (1 − (1 − 2 P_L)^(1/rounds)) / 2 for one
experiment; the table fits ε for each patch across its durations
(1 − 2 P_L(t) = (1 − 2ε)^t), averages the patches of each distance as the
paper does, and reports Λ = ε_d / ε_(d+2) from a fit over d = 3, 5, 7.

One thing to keep in mind when comparing with the paper: Google's best
numbers come from priors tuned on the data (reinforcement learning on the
matching graph weights) and from ensembles or a neural network. Everything
here uses the shipped noise model or plain pairwise statistics. The fair
comparison is backlog against PyMatching on the same graph, and against
Google's shipped correlated matching with the same prior, which is what the
figure shows.
