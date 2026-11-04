# How the decoder works

This page explains the weighted Union-Find decoder in `cpp/`: what it
computes, how the fast version computes the same thing with far less work,
how that equivalence is tested, and how the sliding-window decoder turns it
into a streaming decoder.

## 1. The decoding problem

Stim turns a noisy circuit into a detector error model. With
`decompose_errors=True`, every error mechanism becomes one or more edges of a
graph: nodes are detectors (plus one boundary node), and edge `e` carries a
probability `p_e` and the set of logical observables it flips (a bit mask).
`src/backlog/dem.py` builds that graph; parallel edges that flip the same
observables are merged as independent errors.

A shot gives the set of fired detectors, the defects. The decoder must choose
a set of edges, the correction, such that every defect touches an odd number
of chosen edges and every other detector an even number (the boundary absorbs
any parity). Its prediction is the XOR of the observable masks of the chosen
edges. Minimum-weight perfect matching (PyMatching) picks the most likely such
set. Union-Find gives up a little accuracy for a much simpler, local algorithm.

## 2. Union-Find in one paragraph

Every defect starts a cluster. Clusters with an odd number of defects that
do not touch the boundary are odd, and odd clusters grow along their edges.
When an edge is fully grown, the clusters at its two ends merge. Merging two
odd clusters gives an even one, which stops growing; touching the boundary
also stops a cluster. When no odd cluster is left, every cluster can be
explained internally. Peeling then extracts a correction from each cluster:
take a spanning forest, remove leaves one by one, and when a removed leaf is
a defect, put its edge in the correction and move the defect to the other end
(Delfosse and Nickerson 2021, Delfosse and Zemor 2020).

## 3. Weighted growth

Classic Union-Find grows every edge at the same speed, in half-edge steps.
Under circuit-level noise, edges are not equally likely, so it ends up
matching through unlikely errors. Here, each edge gets an integer length
proportional to its log-likelihood ratio:

```
W_e = max(1, round(R * ln((1 - p_e) / p_e) / max_f ln((1 - p_f) / p_f)))
```

with resolution `R = 1024` by default. The least likely edge needs `R`
growth units; a likely edge fills sooner. `weighted=False` sets every
`W_e = 2`, which is the classic half-edge decoder.

**The specification** is `ReferenceDecoder` (`cpp/src/reference.cpp`, 150
lines, written to be obviously right rather than fast). It runs synchronous
unit rounds over every edge:

- an edge whose two ends are in different clusters grows by
  `odd(a) + odd(b)`, so by 0, 1 or 2 per round;
- an edge whose growth reaches `W_e` is full; all edges that become full in a
  round are merged at the end of that round;
- it stops when no odd cluster is left.

On the surface code at d = 13 and p = 0.3%, edge lengths range from 476 to
1024, so a cluster needs hundreds of rounds to cross one edge. Running the
specification as written costs O(edges x rounds) per shot, far too slow.

**What weighting buys**, on the same circuits (see the README): the d = 7 / 9
crossing moves from 0.66 ± 0.04% to 0.77 ± 0.03% (PyMatching: 0.75 ± 0.02%),
and the logical error rate at d = 9 drops from 1.8 to 3.7 times PyMatching's
to 1.06 to 1.20 times. Huang, Newman and Brown (2020) saw the same pattern on
the toric code: weighting helps Union-Find far more than it helps matching.
Accuracy saturates around `R = 64` to `256`; the default 1024 costs nothing
measurable in speed.

## 4. Same result, less work: from rounds to events

The fast `UnionFindDecoder` (`cpp/src/decoder.cpp`) computes exactly what the
reference computes, but only does work when something changes.

### Radii instead of per-edge counters

Take a node `u` in a cluster. An edge from `u` to a node outside every
cluster has grown by exactly the number of rounds `u`'s cluster has been odd
since `u` joined it. Call that `u`'s radius `rho_u`. An edge between two
clusters has grown `rho_u + rho_v`. So no edge needs a counter: node radii are
enough.

Each cluster root keeps a clock `R(t) = r0 + g * (t - t0)`, where `g` is 1
while the cluster is odd and 0 otherwise. A node stores an offset, and its
radius is `rho_v = R(root) - pot(v)`, where `pot(v)` sums the offsets along
`v`'s path to the root of the union-find tree. When cluster `b` merges into
cluster `a` at round `t`, setting `pot(b) = R_a(t) - R_b(t)` keeps every
radius in `b` unchanged, in O(1); then `a`'s clock restarts from its current
value. Path compression in `find` accumulates the offsets along the way
(union-find with potentials).

### Events

- **Node events.** For a cluster node `u` that still has neighbors outside
  every cluster: the round when its lightest such edge fills, `t + (W - rho_u)`.
  Adjacency lists are sorted by (length, id) once, so a cursor only moves
  forward. When the event fires, every outside edge of `u` with `W <= rho_u`
  fills at once. One event per node, not one per edge.
- **Shared-edge events.** An edge between two clusters grows at rate
  `g_a + g_b`, 1 or 2, and fills after `(W - rho_a - rho_b) / rate` rounds,
  rounded up.
- **Growth changes.** When a merge flips a cluster between odd and even, only
  that cluster's boundary nodes and shared edges are rescheduled. Version
  counters make stale events harmless. Each cluster keeps those two lists, and
  a merge appends the smaller list to the larger one.

### Data structures

- **Bucket queue (Dial).** Every pending event falls within `max W + 1`
  rounds of now, so a ring of buckets plus a bitmap finds the next round in a
  few word operations. Events live in a preallocated pool: no heap, no
  allocation per shot. A binary heap was tried first and was slower.
- **Lazy reset.** Per-node and per-edge state carries an epoch number, so a
  new shot invalidates everything by incrementing one counter. A shot only
  touches the nodes its clusters reach, not the whole graph (2,184 detectors
  at d = 13).
- **Compact layout.** Adjacency entries store (edge, other endpoint) so growth
  never looks up an edge to find its far end.

At d = 9 and p = 0.5%, node-level events cut the work per shot from 1,318
reschedules and 723 queue pops to 243 and 153. A profile at d = 13 shows no
single hotspot left: growth, rescheduling and peeling each take 5 to 10% of
the time.

## 5. Peeling

Full edges are sorted by (length, id) and a Kruskal pass builds a
minimum-length spanning forest, so inside a cluster the correction prefers
likely errors (the same choice as Huang, Newman and Brown). Leaves are peeled in order of first appearance in the forest.
The boundary node is never peeled: it absorbs whatever parity reaches it.

## 6. Testing that fast equals reference

The fast decoder must return the same correction as the reference, edge for
edge, not just the same logical prediction:

- **Fuzzing** (`cpp/tests/test_decoder.cpp`): 3,000 random graphs with up to
  40 nodes, a quarter of them boundary edges, lengths 1 to 9 (one graph in
  three uses the uniform length 2, one in four has parallel edges, which
  sliding windows create), 25 random syndromes each: 75,000 cases.
- **Real error models** (`tests/test_backlog.py`): surface code detector
  error models at resolution 32, so the reference stays fast enough.
- **Invariant**: every correction must reproduce its syndrome, checked on
  every decode of every test, including the command-line runs.

**Mutation testing** (`make mutants`, `scripts/mutation_test.py`) checks the
tests themselves. It injects one realistic bug at a time and requires the
suite to fail:

| injected bug | caught by |
|---|---|
| shared edge grows at rate 1 when both clusters grow | 26,861 fast/reference mismatches |
| merge forgets the absorbed cluster's radii | 25,105 mismatches |
| two odd clusters merge into an odd cluster | 33,177 mismatches |
| growth state changes are detected backwards | 24,654 mismatches |
| shared edge tracked by only one of its two clusters | 13,420 mismatches |
| spanning forest ignores edge lengths | 27,985 mismatches |
| path compression stores the wrong radius offset | runaway loop, stopped by a memory cap |
| node event fires one round late | 24,023 mismatches |
| node event fires one round early | runaway loop, stopped by a memory cap |
| log-likelihood weights collapse to uniform | 2 failed unit checks |
| window: a committed edge does not carry its flip forward | 25,827 invalid windowed corrections |
| window: flips land in the committed region instead of ahead | 25,827 invalid corrections |
| window: defects created by earlier commits are ignored | 16,365 invalid corrections |
| window: windows do not overlap | 22,773 invalid corrections |
| window: edges into the future are dropped | the window-top unit test (section 7) |
| window: edges committed by their far end | 57,527 invalid corrections |

## 7. Sliding windows: decoding as the rounds arrive

A real-time decoder cannot wait for the end of the experiment.
`SlidingWindowDecoder` (`cpp/src/window.cpp`) groups detectors by round (the
time coordinate Stim gives them) and works on a window of `commit + buffer`
rounds:

1. **Decode the window.** Edges into rounds already committed are dropped.
   Edges into rounds not seen yet become edges to the boundary: a defect at
   the top of the window can still be explained by an error whose other half
   has not been measured.
2. **Commit the oldest rounds.** Only correction edges touching the commit
   region are kept. Their logical flips are applied, and their endpoints
   beyond the commit region have their syndrome flipped, so the next window
   starts from the right state.
3. **Slide** by `commit` rounds. The last window reaches the final round and
   commits everything.

Every commit-region detector is explained by its window's correction, and
whatever a committed edge changes further on is carried forward, so the
committed edges always reproduce the full syndrome. When one window covers
every round, the result is the whole-shot decoder's, edge for edge. Both are
fuzzed on 75,000 random layered graphs (random commit and buffer sizes, edges
that skip a round, parallel edges), and a unit test pins down the window top:
a likely error between two rounds, cut by the window edge, must still be
matched through the future boundary.

Windows with the same local structure share one decoder. A memory
experiment has four: the first window, the periodic bulk, the window that
sees the final round, and the last one. Decoder memory does not grow with the
number of rounds.

**What it costs.** The buffer is what keeps accuracy: on the same shots,
with `commit = d`, a buffer of d rounds gives at most 2.3% more logical
errors than whole-shot decoding (d = 3 to 9, p = 0.5%), while no buffer gives
1.4 to 2.1 times more. The price is work: every round is decoded twice, once
in a buffer and once in a commit region, so streaming takes about twice the
whole-shot time per round.

**Does one core keep up?** `python/streaming_benchmark.py latency` measures
every window in C++, then replays them as a stream: one round per
microsecond, windows decoded one after another. At p = 0.1% one core keeps up
to d = 9 (0.73 µs of work per committed round); at p = 0.3%, to d = 5. Past
that, the delay grows without bound: that is the backlog. Even below the
limit, bursts of hard windows matter: at d = 9 and p = 0.1% one delay reached
1.3 ms. The replay chains windows measured on independent shots, which is
an approximation of one long experiment.

## 8. Limits

- Graphlike error models only: hyperedges must be decomposed, which Stim
  does with `decompose_errors=True`. When two parallel edges flip different
  observables, the likelier one is kept and the conflict is counted.
- At most 64 logical observables (one machine word per edge).
- Windows are decoded one after another on one core. Decoding independent
  windows in parallel (Skoric et al. 2023, Tan et al. 2023) is the way past
  the single-core limit, and the next milestone.
- The sliding-window decoder needs detectors numbered in time order, which
  holds for Stim circuits.

## References

- N. Delfosse and N. H. Nickerson, *Almost-linear time decoding algorithm for
  topological codes*, Quantum 5, 595 (2021)
- N. Delfosse and G. Zemor, *Linear-time maximum likelihood decoding of
  surface codes over the quantum erasure channel*, Phys. Rev. Research 2,
  033042 (2020)
- S. Huang, M. Newman and K. R. Brown, *Fault-tolerant weighted union-find
  decoding on the toric code*, Phys. Rev. A 102, 012419 (2020)
- R. B. Dial, *Algorithm 360: shortest-path forest with topological
  ordering*, Communications of the ACM 12, 632 (1969)
- E. Dennis, A. Kitaev, A. Landahl and J. Preskill, *Topological quantum
  memory*, J. Math. Phys. 43, 4452 (2002)
- L. Skoric, D. E. Browne, K. M. Barnes, N. I. Gillespie and E. T. Campbell,
  *Parallel window decoding enables scalable fault tolerant quantum
  computation*, Nat. Commun. 14, 7040 (2023)
- X. Tan, F. Zhang, R. Chao, Y. Shi and J. Chen, *Scalable surface-code
  decoders with parallelization in time*, PRX Quantum 4, 040344 (2023)
