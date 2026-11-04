# Hardware: Union-Find decoder in VHDL

A synthesizable Union-Find decoder for a 1D chain (the repetition code), with
the full verification flow you would use on a real FPGA project:

```
golden model (Python) -> test vectors -> RTL simulation, bit-exact (ghdl)
                      -> synthesis (ghdl + yosys) -> place and route (nextpnr)
```

Reproduce everything with `./run_hw.sh` (or `./run_hw.sh sim` for simulation only).

## Results

All numbers below come from `./run_hw.sh` in this repo.

**Verification.** For every size, the RTL matches the golden model bit for bit
(correction, logical flip, number of growth rounds), and an independent check
in the testbench confirms every correction reproduces its syndrome.

| N (detectors) | d | vectors | mismatches | invariant failures |
|---|---|---|---|---|
| 8  | 9  | 2018 | 0 | 0 |
| 16 | 17 | 2034 | 0 | 0 |
| 32 | 33 | 2066 | 0 | 0 |

**Implementation.** LUT/FF counts from `yosys synth_xilinx` (7-series). Fmax
from a real place and route with `nextpnr-ice40` on an iCE40 HX8K, which is a
slow, cheap FPGA: treat these as a floor, not a target.

| N | d | LUTs (xc7) | FFs | iCE40 LCs | Fmax (iCE40) | ns / cycle | worst case (N+2 cycles) |
|---|---|---|---|---|---|---|---|
| 8  | 9  | 76  | 45  | 113 | 94.5 MHz | 10.6 | 106 ns |
| 16 | 17 | 158 | 77  | 215 | 54.3 MHz | 18.4 | 331 ns |
| 32 | 33 | 328 | 141 | 417 | 34.8 MHz | 28.8 | 978 ns |

Typical case is much better than worst case: at a physical error rate of 8%,
the golden model needs on average 0.74 growth rounds at N=8 and 1.01 at N=16.

**Read this before comparing to anything.** This is the code-capacity
repetition code: one round of perfect measurements, bit flips only. It is not
the surface code with circuit-level noise, so these numbers are not comparable
to Helios, Riverlane's LCD or any 2D decoder. The point of this stage is the
flow (model, RTL, bit-exact, synthesis, P&R) and finding the scaling bottleneck.

## The bottleneck we measured

Fmax drops as N grows (94.5 -> 54.3 -> 34.8 MHz). The cause is structural:
segment parity and boundary contact are computed with prefix/suffix XOR chains
that ripple across the whole chain, so the critical path is O(N). On top of
that, the worst case needs O(N) growth rounds, so worst-case latency is O(N^2).

Next optimizations, in order:
1. Replace the ripple chains with a parallel segmented prefix scan
   (Kogge-Stone or Brent-Kung style): O(log N) depth per round.
2. Stop computing global info every round: only clusters that changed need
   updates (this is what distributed designs like Helios exploit).
3. Move to 2D (surface code) with the time dimension, driven by Stim data.

## Design

Layout (N detectors, N+1 data qubits, code distance d = N + 1):

```
 [B]--e0--(D0)--e1--(D1)-- ... --(D_{N-1})--eN--[B]
```

Edge k corresponds to data qubit k. Detector D(i) = e(i) xor e(i+1). The
logical observable is read on data qubit 0, so `obs_flip = correction(0)`.

Each clock cycle in `S_GROW` does one synchronous growth round:

1. Combinationally, for every vertex v: segment prefix parity L(v), suffix
   parity R(v), and boundary contact BL(v) / BR(v). Cluster parity is
   L(v) xor R(v) xor defect(v). A cluster is odd if its parity is 1 and it
   touches no boundary.
2. Every non-grown edge gains one half-edge per odd neighbour. Two halves make
   it fully grown (`half_r` and `grown_r` encode growth 0, 1, 2).
3. When no odd cluster remains, the peeling result is registered and `done`
   pulses for one cycle. `rounds` reports how many growth rounds were used.

Latency from the start pulse to `done` is rounds + 2 cycles.

## Why the peeling rule is always valid

Peeling is combinational. For a grown edge k:

- `k = 0` (left boundary edge): `c(0) = R(0)`
- `k = N` (right boundary edge): `c(N) = 0` if the segment also touches the
  left boundary, else `c(N) = L(N-1)`
- internal edge: `c(k) = R(k)` if the segment touches the left boundary,
  else `c(k) = L(k-1)`

Validity means: for every vertex v, the XOR of correction bits on its two
edges equals defect(v). Using L(v) = L(v-1) xor defect(v) and
R(v) = R(v+1) xor defect(v):

- **No boundary contact** (segment a..b, even parity, so L(b) = 0): inner
  vertices see L(v-1) xor L(v) = defect(v); the first sees L(a) = defect(a);
  the last sees L(b-1) = L(b) xor defect(b) = defect(b).
- **Left boundary** (rooted at the left boundary, peeled from the right):
  vertices see R(v) xor R(v+1) = defect(v); the last sees R(b) = defect(b);
  the left boundary absorbs R(0).
- **Right boundary only**: mirror image using L.
- **Both boundaries**: same as left boundary, with the right boundary edge
  dropped to break the cycle.

The testbench checks this property on every vector, independently of the
golden model.

## Files

- `model/uf1d_model.py`: golden model, test-vector generator, selfcheck vs PyMatching
- `rtl/uf_decoder_1d.vhd`: the decoder (VHDL-2008, generic `N`)
- `tb/tb_uf_decoder_1d.vhd`: self-checking testbench
- `run_hw.sh`: the full flow
