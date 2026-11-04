// Union-Find decoder for graphlike quantum error-correcting codes.
//
// This is a from-scratch implementation of the Delfosse-Nickerson Union-Find
// decoder followed by Delfosse-Zemor peeling. It is deliberately dependency
// free: it reads a decoding graph and detection events as plain text, so it can
// be driven from Stim/PyMatching in Python, from a test harness, or eventually
// from an FPGA co-simulation.
//
// Scope note: growth here is unweighted (every edge grows at the same rate),
// which is exact for the repetition code and phenomenological surface code and
// a good approximation for circuit-level noise. Weighted growth is a
// documented later milestone (see docs/roadmap.md).
#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace backlog {

struct Edge {
    int a;          // detector node id
    int b;          // detector node id, or the boundary node (== num_detectors)
    uint64_t obs;   // bitmask of logical observables this edge flips
};

class UnionFindDecoder {
public:
    UnionFindDecoder(int num_detectors, int num_observables,
                     std::vector<Edge> edges);

    // Decode one shot.
    //   syndrome    : ids of detectors that fired.
    //   syndrome_ok : optional out-param, set true iff the chosen correction
    //                 reproduces `syndrome` exactly (the hard invariant).
    // Returns the predicted observable-flip bitmask.
    uint64_t decode(const std::vector<int>& syndrome,
                    bool* syndrome_ok = nullptr) const;

    int num_detectors() const { return n_; }
    int num_observables() const { return m_; }
    std::size_t num_edges() const { return edges_.size(); }

    static UnionFindDecoder from_graph_file(const std::string& path);

private:
    int n_;              // number of detectors
    int m_;              // number of observables
    int boundary_;       // boundary node id == n_
    int num_nodes_;      // n_ + 1
    std::vector<Edge> edges_;
};

}  // namespace backlog
