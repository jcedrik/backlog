// Decoding graph with integer growth weights.
//
// Nodes are detectors 0..n-1 plus a single boundary node n. Every edge is one
// graphlike error mechanism from a Stim detector error model: it flips one or
// two detectors and a set of logical observables, with some probability.
//
// Growth weights are integers. An edge is fully grown once its growth counter
// reaches its weight. Two weightings are provided:
//   Uniform        every edge has weight 2 (two half-edges): the classic
//                  unweighted Union-Find decoder.
//   LogLikelihood  weight proportional to ln((1-p)/p), quantized to
//                  `resolution` levels: likely errors grow faster, which is
//                  the weighted Union-Find decoder.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace backlog {

struct Edge {
    int32_t a;            // detector id
    int32_t b;            // detector id, or the boundary node (== num_detectors)
    uint64_t obs;         // bitmask of logical observables flipped by this edge
    double probability;   // probability of this error mechanism
};

enum class Weighting { Uniform, LogLikelihood };

std::vector<int32_t> compute_weights(const std::vector<Edge>& edges,
                                     Weighting weighting, int32_t resolution);

class DecodingGraph {
public:
    DecodingGraph(int32_t num_detectors, int32_t num_observables,
                  std::vector<Edge> edges, Weighting weighting,
                  int32_t resolution = 1024);

    // Explicit integer weights (each >= 1). Used by tests and experiments.
    DecodingGraph(int32_t num_detectors, int32_t num_observables,
                  std::vector<Edge> edges, std::vector<int32_t> weights);

    // Text format: "N M" header, then one edge per line: "a b obs [p]".
    // If any edge has no probability, Uniform weighting is forced.
    static DecodingGraph from_file(const std::string& path, Weighting weighting,
                                   int32_t resolution = 1024);

    int32_t num_detectors() const { return n_; }
    int32_t num_observables() const { return m_; }
    int32_t boundary() const { return n_; }
    int32_t num_nodes() const { return n_ + 1; }
    int32_t num_edges() const { return static_cast<int32_t>(edges_.size()); }

    const Edge& edge(int32_t e) const { return edges_[e]; }
    const std::vector<Edge>& edges() const { return edges_; }
    int32_t weight(int32_t e) const { return weights_[e]; }
    const std::vector<int32_t>& weights() const { return weights_; }

    // Incident edges of node v, as a contiguous range (CSR layout).
    const int32_t* adj_begin(int32_t v) const { return adj_.data() + offset_[v]; }
    const int32_t* adj_end(int32_t v) const { return adj_.data() + offset_[v + 1]; }

private:
    void validate_and_index();

    int32_t n_;
    int32_t m_;
    std::vector<Edge> edges_;
    std::vector<int32_t> weights_;
    std::vector<int32_t> offset_;
    std::vector<int32_t> adj_;
};

}  // namespace backlog
