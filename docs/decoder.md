# How the decoder works

This page explains the weighted Union-Find decoder in `cpp/`: what it
computes, how the fast version computes the same thing with far less work,
how that equivalence is tested, and how window decoders (sliding, then
parallel) turn it into a streaming decoder.

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
| window: flips land inside the commit region instead of outside | an internal consistency check stops the run |
| window: defects created by earlier commits are ignored | 16,365 invalid corrections |
| window: sliding windows do not overlap | 22,773 invalid corrections |
| window: edges into the future are dropped | the window-top unit test (section 7) |
| window: edges leaving the commit region are never committed | 58,729 invalid corrections |
| parallel: core windows close their past | the core-past unit test (section 8) |
| parallel: seams ignore the flips from the cores | 31,333 invalid corrections |

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

## 8. Parallel windows: more cores, no backlog

One core decoding windows in order is the limit measured above. The way past
it is to decode several windows at once (Skoric et al. 2023, Tan et al.
2023). `ParallelWindowDecoder` cuts time into core windows and seams:

```
... seam | buffer | core: commit C | buffer | seam | buffer | core: commit C ...
         <--- core window: both sides open --->
```

1. **Core windows** commit C rounds, with B rounds of buffer on each side.
   Both sides are open (edges leaving the window go to the boundary), so a
   core window depends on nothing else: all of them run in parallel.
2. **Seams**, the G rounds between two core commit regions, run as soon as
   the two cores around them have committed, on the thread that finished the
   second of them (its flips are still in that core's cache). There is no
   barrier between cores and seams: a thread that is done with a core takes
   the next core or the seam it just unlocked. Both sides of a seam are
   closed: the flips of the cores' committed edges are part of its syndrome.
   A seam commits everything it decodes.

G must cover the longest edge in time (one round for Stim memory circuits),
so a core's flips always land in a seam, never in another core: a check stops
the run otherwise.

**Same answer for any number of threads.** Each window only reads the
original syndrome and the flips handed to it, and writes its own result, so
the scheduling cannot change the answer. Each core writes its flips into two
inboxes, one per neighboring seam, each with that core as its only writer;
an atomic counter per seam says when both inboxes are full. Each thread owns
its scratch space and its own copy of every window decoder. The thread pool
spins for 50 µs before sleeping, with the x86 PAUSE instruction in the loop,
and only when every thread has a physical core of its own (read from the
CPU topology): a spinning hyperthread steals execution units from the
sibling doing real work. An exception thrown by a window on a worker thread
is carried back to the caller instead of ending the process. The fuzzer checks on 75,000 random layered graphs that
1 and 3 threads give the same correction, edge for edge, that it reproduces
the syndrome, and that one core window covering everything is the whole-shot
decoder; the whole suite also runs under ThreadSanitizer (`make tsan`) with
no data race reported. A unit test pins down the open past: an error just
below a core window must still be matched through it.

**What it costs and what it buys.** With C = B = G = d, every round is
decoded about twice, as with sliding windows, and accuracy is the same: on
the same shots, 0.8 to 1.3% more logical errors than whole-shot decoding
(d = 3 to 9, p = 0.5%). On this 2-core machine, 2 threads are 1.54 to 1.79
times faster than one (d = 5 to 17); before seams stopped waiting for every
core, d = 5 only reached 1.4.

**How many cores for real time?** `python/parallel_benchmark.py` measures the
work of every window, then replays a stream: rounds every microsecond, core
windows ready once their buffer has arrived, seams once both neighbors have
committed, N cores taking the oldest period first. The same model, applied to
2 cores, predicts the real 2-thread time within 1 to 13% (it is optimistic:
it ignores synchronization), so the core counts below keep 15% headroom:

| d | 9 | 11 | 13 | 15 | 17 |
|---|---|---|---|---|---|
| p = 0.1% | 1 | 2 | 2 | 3 | 4 |
| p = 0.3% | 4 | 6 | 9 | 11 | 15 |

With fewer cores the delay grows without bound; with these, it stays under
about a millisecond. The price is latency: the oldest round of a seam waits
for C + B + G rounds of data before its commit can even start, about 3 d µs.

**Checked on a real 4-core laptop** (Intel i7-8665U, 15 W, Ubuntu on WSL2,
`make scaling`, data in `results/scaling-i7-8665U/`), the model does not
hold beyond 2 threads. Measured speedups were 1.56 to 1.69 at 2 threads
(model: 1.80 to 1.85), 1.83 to 2.11 at 4 (model: about 3) and 0.60 to 1.99 at
8, where the model expected over 4. The model assumes each core decodes a
window as fast as one core alone; on this laptop the turbo frequency falls as
cores wake up, the cores share one cache (each thread holds its own copy of
every window graph), hyperthreads share a core with spinning workers, and the
host takes CPUs away from the virtual machine mid-phase. The core counts
above are therefore lower bounds.

`make scaling` now splits the gap between model and measurement in two:
**work inflation** (the total work of the windows with t threads, divided by
the same with one thread: the same windows run slower when more cores are
busy) and the **busy fraction** (window work divided by t times the wall
time: the rest is threads waiting). The speedup is t × busy / inflation.
Rerun on the same laptop with the v0.5 scheduler
(`results/scaling-i7-8665U-v0.5/`):

| threads | speedup | model | work inflation | busy |
|---|---|---|---|---|
| 2 | 1.48 to 1.66 | 1.68 to 1.80 | 1.11 to 1.21 | 88 to 96% |
| 4 | 1.61 to 2.09 | 2.60 to 2.88 | 1.57 to 1.72 | 67 to 84% |
| 8 | 0.57 to 2.18 | 3.50 to 4.07 | 2.17 to 2.56 | 18 to 65% |

At 4 threads, one per physical core, inflation alone caps the speedup at 2.3
to 2.5. It does not grow with the window size (a d = 17 window has 7 times
the detectors of a d = 9 one), which points at the clock of a 15 W chip under
an all-core load rather than at cache pressure: sharing the read-only window
graphs between threads, the planned next step, would not lift it, so it is
dropped. The 16 to 33% of idle time at 4 threads is what the schedule itself
leaves, not overhead: the model, which has no synchronization cost at all,
keeps 4 threads busy only 65 to 72% of the time (its speedup over 4), because
a shot of about ten core windows and their seams does not split evenly over 4
threads. A streaming scheduler that takes the next period's windows as they
arrive would not have that end-of-shot tail. At
8 threads, hyperthreads share a core and inflate every window 2.2 to 2.6
times, and with no spinning past the physical cores the wake-ups cost the
rest: use one thread per physical core.

## 9. Correlated decoding

A Y error on a data qubit is an X error and a Z error at once: in a
decomposed detector error model it appears as an X-type edge and a Z-type
edge, decoded independently, and the decoder forgets that they come
together. Correlated decoding remembers (Fowler 2013; PyMatching since
version 2.3):

1. Decode once, as usual.
2. For every edge e of that correction and every edge f that comes with e in
   some error, lower f's weight to that of probability P(f | e) = P(e and f) /
   P(e), if that is lower than its own.
3. Decode again with those weights; restore them.

`backlog.dem_correlations` computes the conditional probabilities exactly as
PyMatching does (both P(e and f) and P(e) combine the errors that contain
them as independent events), and the two decoders behave the same way: on
the same shots their correlated versions remove the same fraction of errors,
and on the exhaustive fault checks below they fail almost the same sets.

**Exactly the decoder on the reweighted graph.** The event-driven decoder
relies on each node's adjacency sorted by weight. A lowered edge can only
move left in its endpoints' lists, so one insertion step per change keeps
them sorted; the lists and the weights touched are saved before and copied
back after the shot (the boundary node never grows, so its list is never
read). The fuzzer checks on 75,000 random graphs with random rules, reusing
one decoder for every shot, that the second pass equals the reference
decoder run on the explicitly reweighted graph, edge for edge. Windows carry
the rules whose two edges are both inside them, and the window fuzzers check
that one window covering everything equals the whole-shot correlated decoder.

**The decomposition matters.** Stim's decomposer splits an error into any
graphlike pieces that occur elsewhere in the model; at d = 5 it splits 400
errors into several pieces of the same stabilizer type, which gives the
reweighting partners that are not physical. With that decomposition,
correlated decoding at d = 3 and p = 0.1% has 44% *more* logical errors than
plain decoding, for PyMatching and for backlog alike (1 million shots,
`results/correlated_decomposition.csv`), and both correlated decoders get 6
of the 286 single faults wrong, which a distance-3 code must correct. `decompose_by_basis` splits every error into exactly its
X-type part and its Z-type part (the stabilizer type comes from Stim's
detector coordinates), each part keeping the observables it has when it
occurs alone. Correlated decoding then gains at d = 3 too (7% fewer errors
there), and at d = 5 the gain goes from 28% to 46%. backlog's correlated
decoders apply this split by default.

**A unit of distance lost, and won back.** A distance-d code must correct
every set of (d-1)/2 faults (a fault is one error of the model). Hernando de
la Fuente et al. (2026) showed that standard correlated decoding does not:
one first-pass edge can discount several partner edges at once, as if their
joint occurrence came for free, while it really needs extra independent
faults. `python/fault_distance.py` decodes every single fault and every pair
of faults (d = 5: 1,906,128 pairs) with every decoder:

| d = 5, pairs of faults decoded wrongly | p = 10⁻⁴ | 3·10⁻⁴ | 10⁻³ | 3·10⁻³ |
|---|---|---|---|---|
| Union-Find, PyMatching | 0 | 0 | 0 | 0 |
| correlated UF, α = 1 | 425 | 367 | 320 | 284 |
| correlated PyMatching | 440 | 361 | 311 | 281 |
| correlated UF, α = 0.3 | 73 | 34 | 23 | 29 |
| correlated UF, α = 0.1 | 11 | 4 | 2 | 2 |
| correlated UF, α = 0.05 | 2 | 0 | 0 | 0 |

(At d = 3, with the basis decomposition, every single fault is corrected by
every decoder.) The regularized rule multiplies every conditional
probability by α ≤ 1, which charges ln(1/α) more for each discounted edge.
How much α to take is a trade-off: a smaller α corrects more pairs, but also
discounts less where the correlation is real. On the Monte Carlo runs
(`python/correlated_benchmark.py`, same shots for every decoder), the best α
of the grid is 0.3 to 0.5 at p = 0.1% and 1 from p = 0.5%, which the
paper's form α*(p) = α₀ / (1 + (p₀/p)^k) follows with α₀ = 1, p₀ = 10⁻³ and
k = 1 (`backlog.regularization_strength`). At d = 3, where the leading order
of the logical error rate is the pairs of faults, the exact enumeration
gives the same trend: the best α falls from 1 at p = 0.3% to about 0.2 at
p = 10⁻⁴ (`results/fault_distance.png`, right).

**What it buys.** On the same shots, correlated Union-Find makes 7 to 12%
fewer logical errors than plain Union-Find at d = 3, 21 to 38% fewer at
d = 5 and 32 to 67% fewer at d = 7 (p = 0.1% to 0.7%); PyMatching gains the
same from its own correlated mode, and correlated Union-Find makes fewer
errors than plain PyMatching at every point. It raises the error suppression
factor Λ (logical error per round at d, divided by that at d + 2) from 3.3
to 5.2 at p = 0.3% (d = 5 to 7), and from 9.9 to 19 at p = 0.1%. The price
is a second pass and the reweighting: correlated Union-Find takes about 3
times as long as plain Union-Find, as PyMatching's correlated mode does
against plain PyMatching. Against correlated PyMatching it is level at d = 5
and 9, and faster from d = 13: 4.2 against 5.8 µs per round at d = 17 and
p = 0.1% (`results/correlated_speed.csv`, one core).

## 10. Limits

- Graphlike error models only: hyperedges must be decomposed, which Stim
  does with `decompose_errors=True`. When two parallel edges flip different
  observables, the likelier one is kept and the conflict is counted.
- The basis decomposition reads the stabilizer type from the coordinates of
  Stim's rotated surface code circuits; for another layout, pass the types
  (`decompose_by_basis(dem, basis)`), or the decoder falls back to the
  model's own decomposition.
- The regularization strength α is a parameter: the decoder does not know
  the physical error rate. `regularization_strength(p)` gives the value the
  benchmarks support.
- At most 64 logical observables (one machine word per edge).
- The parallel scheduling model is optimistic beyond 2 cores: on a 4-core
  laptop it predicted about 3x at 4 threads where 2x was measured, and over
  4x at 8 threads where hyperthreads made decoding slower. Its core counts
  are lower bounds until `make scaling` is rerun on a machine with more
  physical cores.
- The window decoders need detectors numbered in time order, which holds for
  Stim circuits.

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
- A. G. Fowler, *Optimal complexity correction of correlated errors in the
  surface code*, arXiv:1310.0863 (2013)
- O. Higgott and C. Gidney, *Sparse Blossom: correcting a million errors per
  core second with minimum-weight matching*, Quantum 9, 1600 (2025);
  correlated matching in PyMatching since version 2.3
- C. Hernando de la Fuente, J. Majaniemi, E. van Nieuwenburg and F. Frohnert,
  *Correlated decoding loses the surface code's effective distance below
  threshold*, arXiv:2610.26785 (2026)
