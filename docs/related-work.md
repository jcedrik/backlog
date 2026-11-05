# Related work: real-time decoding in 2023-2026

Where backlog sits among published real-time surface-code decoders, as of
November 2026. Numbers are the authors' own, on their own noise models and
hardware, so they are context rather than a ranking: latency means different
things in different papers (per round, per window, end of experiment to
correction), and the column says which.

## Published decoders

| Decoder | Algorithm | Platform | Reported performance | Source |
|---|---|---|---|---|
| Google, Willow real-time decoder (2024) | sparse blossom, parallelized and streaming, with correlated reweighting | CPU workstation | d = 5 decoded in real time for up to 10⁶ cycles of 1.1 µs, 63 µs average decoder latency; Λ = 2.0 between d = 3 and 5 when decoding in real time | [1], [2] |
| Sparse Blossom, PyMatching 2 (2023) | exact minimum-weight perfect matching | 1 CPU core | under 1 µs per round for d = 17 at 0.1% circuit noise, both bases | [3] |
| PyMatching 2.3+ correlated matching (2025) | two-pass reweighting on top of sparse blossom | CPU | the baseline correlated decoder; open source | [4] |
| Collision Clustering, Riverlane (2025) | Union-Find variant | FPGA, ASIC | 881-qubit surface code in 810 ns on FPGA (4.5% of the LUTs), 1057 qubits in 240 ns on a 0.06 mm², 8 mW ASIC; threshold 0.78% | [5] |
| Local Clustering Decoder (LCD), Riverlane | adaptive, distributed Union-Find, leakage-aware | FPGA | under 1 µs per round with modest FPGA resources; at d = 17 under 10% of a high-end Xilinx FPGA; adaptivity gives 4x fewer qubits under leakage | [6] |
| Deltaflow 2, Riverlane (April 2026) | LCD inside a full QEC system | FPGA | 16.32 µs mean latency on Google's Willow data (Google reported 63 ± 17 µs) | [7] |
| Micro Blossom (ASPLOS 2025) | exact MWPM, CPU plus one FPGA unit per vertex and edge | Xilinx VMK180 | 0.8 µs average latency at d = 13, p = 0.1%, 62 MHz; at d = 9, 367 ns against 5.1 µs on an M1 Max CPU | [8] |
| Helios (2023) | distributed Union-Find | FPGA | d up to 21 on a Xilinx VCU129, 11.5 ns per measurement round on average (phenomenological noise, p = 0.1%); with O(d³) processing units the time per round falls as d grows | [9] |
| NV-Fusion, NVIDIA cudaq-qec (2026) | multi-threaded MWPM (sparse and fusion blossom) | CPU, 84 threads | p99 streaming tail latency 67 µs to 1.28 ms at d = 5 to 13 over 10,000 rounds, against 2.13 to 28 ms for PyMatching | [10] |
| NVIDIA Ising pre-decoder + PyMatching (2026) | CNN pre-decoder, then matching | GPU | 2.1 µs per round over 104 rounds, 2.5x faster than PyMatching alone | [11] |
| AlphaQubit 2, AQ2-RT (Google DeepMind, 2025) | neural network | commercial accelerators | under 1 µs per cycle for the surface code up to d = 11, more accurate than leading real-time decoders; color code up to d = 9 | [12] |
| FPGA neural decoder on hardware (May 2026) | neural network | FPGA in the control system | real-time d = 3 on a superconducting chip: 124 ns decode, 550 ns closed loop with feedback | [13] |

## Parallel and streaming decoding

- Parallel windows in time: Skoric et al. [14] and Tan et al. [15]. backlog
  implements this scheme (core windows, then seams), checked edge for edge
  against the whole-shot decoder.
- Windows in space too, for lattice surgery on large merged patches: Lin et
  al. [16].
- A neural decoder trained for parallel windows runs surface codes up to
  d = 25 within 1 µs per round on one TPU v6e [17].

## Correlated decoding and its failure below threshold

Decomposed errors (a Y error is an X part and a Z part) show up in the graph as
separate edges. Correlated decoding decodes once, lowers the weights of the
edges that come with the ones it used, and decodes again [4], [18]. Google's
real-time decoder does this [2], and Riverlane calls correlated matching the
current real-time baseline when it discusses NVIDIA's pre-decoder results.

Hernando de la Fuente, Majaniemi, van Nieuwenburg and Frohnert (Leiden and
Riverlane, October 2026) [19] found that the standard rule loses a unit of
effective distance deep below threshold, for matching and for LCD alike: one
first-pass edge can discount several partner edges at once, as if their joint
occurrence were free, so some sets of (d-1)/2 faults are decoded wrongly.
Scaling every conditional probability by α < 1 charges for the missing
faults and restores the distance.

backlog's contribution here (see [decoder.md](decoder.md), section 9, and the
README):

1. An open-source correlated Union-Find, with the regularized rule, in the
   whole-shot, sliding-window and parallel-window decoders.
2. An exact check of the effect on a Union-Find decoder: every pair of faults
   of the d = 5 circuit, 1.9 million sets. Standard correlated decoding gets
   about 400 of them wrong, as does PyMatching's correlated matching; plain
   Union-Find and matching get none wrong. A small enough α fixes them all.
3. A practical finding: correlated decoding depends on how the model's
   errors are decomposed. Stim's decomposer splits many errors into several
   pieces of the same stabilizer type. With that decomposition, correlated
   decoding at d = 3 and p = 0.1% is 44% *worse* than plain decoding, for
   PyMatching and for backlog alike. Splitting every error into exactly its
   X part and its Z part (as the Tesseract decoder's coordinate decomposition
   does, and as [19] uses) turns that into a gain, and lifts the gain at d = 5
   from 28% to 46%. `backlog.decompose_by_basis` does this split, and
   backlog's correlated decoders apply it by default.

## Where backlog stands

backlog is a software decoder with an FPGA path, not an FPGA decoder yet. Its
Union-Find matches PyMatching's speed on one core, and its correlated mode
matches PyMatching's correlated matching in accuracy (see the README). It is
open source, tested edge for edge against a reference implementation, and
reproduces a result from October 2026 on its own code. The hardware part
(a 1D chain in VHDL) is far behind Helios, Collision Clustering, LCD or Micro
Blossom; the roadmap says what it would take to close part of that gap.

## References

1. Google Quantum AI and Collaborators, *Quantum error correction below the
   surface code threshold*, Nature 638, 920 (2025),
   [arXiv:2408.13687](https://arxiv.org/abs/2408.13687).
2. *Real-time decoding of surface codes on superconducting qubits below
   threshold with a streaming parallel blossom decoder*, APS March Meeting 2025,
   [abstract](https://meetings-archive.aps.org/smt/2025/mar-b16/4).
3. O. Higgott and C. Gidney, *Sparse Blossom: correcting a million errors per
   core second with minimum-weight matching*, Quantum 9, 1600 (2025),
   [arXiv:2303.15933](https://arxiv.org/abs/2303.15933).
4. PyMatching, correlated matching since version 2.3,
   [pypi.org/project/pymatching](https://pypi.org/project/pymatching/2.4.0/).
5. B. Barber et al., *A real-time, scalable, fast and resource-efficient
   decoder for a quantum computer*, Nature Electronics 8, 84 (2025),
   [arXiv:2309.05558](https://arxiv.org/abs/2309.05558).
6. A. B. Ziad et al., *Local Clustering Decoder: a fast and adaptive hardware
   decoder for the surface code*, [arXiv:2411.10343](https://arxiv.org/abs/2411.10343).
7. Riverlane, *Riverlane's real-time QEC system achieves 10x lower latency than
   Google's reported results*, April 2026,
   [riverlane.com](https://riverlane.com/news/riverlane-s-real-time-qec-system-performance).
8. Y. Wu, N. Liyanage and L. Zhong, *Micro Blossom: accelerated minimum-weight
   perfect matching decoding for quantum error correction*, ASPLOS 2025,
   [arXiv:2502.14787](https://arxiv.org/abs/2502.14787).
9. N. Liyanage, Y. Wu, A. Deters and L. Zhong, *Scalable quantum error
   correction for surface codes using FPGA* (Helios),
   [arXiv:2301.08419](https://arxiv.org/abs/2301.08419).
10. NVIDIA, *Bounding real-time decode latency with NV-Fusion*,
    [cudaq-qec documentation](https://nvidia.github.io/cudaq-qec/performance/nv_fusion_latency_user_guide.html).
11. NVIDIA, *CUDA-Q QEC 0.6 enables real-time QEC with NVQLink*, April 2026,
    [blog](https://nvidia.github.io/cuda-quantum/blogs/blog/2026/04/14/cudaq-qec-0.6/).
12. A. W. Senior et al., *A scalable and real-time neural decoder for
    topological quantum codes*, [arXiv:2512.07737](https://arxiv.org/abs/2512.07737).
13. X. Yang et al., *Real-time surface-code error correction using an
    FPGA-based neural-network decoder*, [arXiv:2605.04892](https://arxiv.org/abs/2605.04892).
14. L. Skoric, D. E. Browne, K. M. Barnes, N. I. Gillespie and E. T. Campbell,
    *Parallel window decoding enables scalable fault tolerant quantum
    computation*, Nature Communications 14, 7040 (2023).
15. X. Tan, F. Zhang, R. Chao, Y. Shi and J. Chen, *Scalable surface-code
    decoders with parallelization in time*, PRX Quantum 4, 040344 (2023).
16. S. F. Lin, E. C. Peterson, K. Sankar and P. Sivarajah, *Spatially parallel
    decoding for multi-qubit lattice surgery*, Quantum Sci. Technol. 10,
    035007 (2025), [arXiv:2403.01353](https://arxiv.org/abs/2403.01353).
17. *Learning to decode in parallel: self-coordinating neural network for
    real-time quantum error correction*, [arXiv:2601.09921](https://arxiv.org/abs/2601.09921).
18. A. G. Fowler, *Optimal complexity correction of correlated errors in the
    surface code*, [arXiv:1310.0863](https://arxiv.org/abs/1310.0863).
19. C. Hernando de la Fuente, J. Majaniemi, E. van Nieuwenburg and F. Frohnert,
    *Correlated decoding loses the surface code's effective distance below
    threshold*, [arXiv:2610.26785](https://arxiv.org/abs/2610.26785); code and
    data at [github.com/cesar-hernando/regularized-correlated-matching](https://github.com/cesar-hernando/regularized-correlated-matching).
