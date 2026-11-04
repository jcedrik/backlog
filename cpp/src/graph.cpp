#include "backlog/graph.hpp"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace backlog {

std::vector<int32_t> compute_weights(const std::vector<Edge>& edges,
                                     Weighting weighting, int32_t resolution) {
    std::vector<int32_t> w(edges.size(), 2);
    if (weighting == Weighting::Uniform || edges.empty()) return w;
    if (resolution < 2) throw std::invalid_argument("resolution must be >= 2");

    std::vector<double> ll(edges.size());
    double max_ll = 0.0;
    for (std::size_t i = 0; i < edges.size(); ++i) {
        double p = edges[i].probability;
        if (!(p >= 0.0 && p <= 1.0)) {
            throw std::invalid_argument("edge probability must be in [0, 1]");
        }
        // p >= 0.5 carries no information in favour of this edge: weight 1.
        p = std::min(std::max(p, 1e-15), 0.5);
        ll[i] = std::log((1.0 - p) / p);
        max_ll = std::max(max_ll, ll[i]);
    }
    if (max_ll <= 0.0) return w;  // every edge at p = 0.5: nothing to weigh
    for (std::size_t i = 0; i < edges.size(); ++i) {
        const double scaled = static_cast<double>(resolution) * ll[i] / max_ll;
        w[i] = std::max<int32_t>(1, static_cast<int32_t>(std::lround(scaled)));
    }
    return w;
}

DecodingGraph::DecodingGraph(int32_t num_detectors, int32_t num_observables,
                             std::vector<Edge> edges, Weighting weighting,
                             int32_t resolution)
    : n_(num_detectors), m_(num_observables), edges_(std::move(edges)) {
    weights_ = compute_weights(edges_, weighting, resolution);
    validate_and_index();
}

DecodingGraph::DecodingGraph(int32_t num_detectors, int32_t num_observables,
                             std::vector<Edge> edges, std::vector<int32_t> weights)
    : n_(num_detectors), m_(num_observables), edges_(std::move(edges)),
      weights_(std::move(weights)) {
    if (weights_.size() != edges_.size()) {
        throw std::invalid_argument("one weight per edge is required");
    }
    for (int32_t w : weights_) {
        if (w < 1) throw std::invalid_argument("weights must be >= 1");
    }
    validate_and_index();
}

void DecodingGraph::validate_and_index() {
    if (n_ < 0) throw std::invalid_argument("negative detector count");
    if (m_ < 0 || m_ > 64) {
        throw std::invalid_argument("between 0 and 64 observables supported");
    }
    for (const Edge& e : edges_) {
        if (e.a < 0 || e.a > n_ || e.b < 0 || e.b > n_) {
            throw std::invalid_argument("edge endpoint out of range");
        }
        if (e.a == e.b) throw std::invalid_argument("self-loop edge");
        if (m_ < 64 && (e.obs >> m_) != 0) {
            throw std::invalid_argument("edge flips an observable that does not exist");
        }
    }
    // CSR adjacency: incident edge ids per node, in increasing edge id order.
    offset_.assign(static_cast<std::size_t>(n_) + 2, 0);
    for (const Edge& e : edges_) {
        ++offset_[e.a + 1];
        ++offset_[e.b + 1];
    }
    for (std::size_t v = 1; v < offset_.size(); ++v) offset_[v] += offset_[v - 1];
    adj_.assign(static_cast<std::size_t>(offset_.back()), 0);
    std::vector<int32_t> fill(offset_.begin(), offset_.end() - 1);
    for (int32_t e = 0; e < num_edges(); ++e) {
        adj_[fill[edges_[e].a]++] = e;
        adj_[fill[edges_[e].b]++] = e;
    }
}

DecodingGraph DecodingGraph::from_file(const std::string& path,
                                       Weighting weighting, int32_t resolution) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("cannot open graph file: " + path);
    std::string line;
    int32_t n = 0, m = 0;
    if (!std::getline(in, line)) throw std::runtime_error("empty graph file");
    {
        std::istringstream hs(line);
        if (!(hs >> n >> m)) throw std::runtime_error("bad header in " + path);
    }
    std::vector<Edge> edges;
    bool all_have_p = true;
    while (std::getline(in, line)) {
        std::istringstream ls(line);
        long long a, b;
        unsigned long long obs;
        if (!(ls >> a >> b >> obs)) continue;
        double p = 0.0;
        if (!(ls >> p)) {
            all_have_p = false;
            p = 0.01;
        }
        edges.push_back(Edge{static_cast<int32_t>(a), static_cast<int32_t>(b),
                             static_cast<uint64_t>(obs), p});
    }
    if (!all_have_p) weighting = Weighting::Uniform;
    return DecodingGraph(n, m, std::move(edges), weighting, resolution);
}

}  // namespace backlog
