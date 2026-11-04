#include "union_find_decoder.hpp"

#include <fstream>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace backlog {

namespace {

// Disjoint-set forest with per-cluster parity and boundary flag.
// parity[root]           = number of defects in the cluster, mod 2
// touches_boundary[root] = cluster contains the boundary node
struct ClusterSet {
    std::vector<int> parent, rank;
    std::vector<uint8_t> parity, touches_boundary;

    explicit ClusterSet(int n)
        : parent(n), rank(n, 0), parity(n, 0), touches_boundary(n, 0) {
        for (int i = 0; i < n; ++i) parent[i] = i;
    }

    int find(int x) {
        while (parent[x] != x) {
            parent[x] = parent[parent[x]];  // path halving
            x = parent[x];
        }
        return x;
    }

    void unite(int x, int y) {
        int rx = find(x), ry = find(y);
        if (rx == ry) return;
        if (rank[rx] < rank[ry]) std::swap(rx, ry);
        parent[ry] = rx;
        if (rank[rx] == rank[ry]) ++rank[rx];
        parity[rx] ^= parity[ry];
        touches_boundary[rx] |= touches_boundary[ry];
    }

    // An odd cluster still has an unpaired defect and no boundary to dump it
    // into, so it must keep growing.
    bool is_odd(int x) {
        int r = find(x);
        return parity[r] == 1 && !touches_boundary[r];
    }
};

// Plain union-find used only to extract a spanning forest.
struct Forest {
    std::vector<int> parent;
    explicit Forest(int n) : parent(n) {
        for (int i = 0; i < n; ++i) parent[i] = i;
    }
    int find(int x) {
        while (parent[x] != x) {
            parent[x] = parent[parent[x]];
            x = parent[x];
        }
        return x;
    }
    bool join(int x, int y) {
        int rx = find(x), ry = find(y);
        if (rx == ry) return false;
        parent[rx] = ry;
        return true;
    }
};

}  // namespace

UnionFindDecoder::UnionFindDecoder(int num_detectors, int num_observables,
                                   std::vector<Edge> edges)
    : n_(num_detectors),
      m_(num_observables),
      boundary_(num_detectors),
      num_nodes_(num_detectors + 1),
      edges_(std::move(edges)) {
    if (m_ > 64) {
        throw std::invalid_argument("at most 64 observables supported");
    }
    for (const Edge& e : edges_) {
        if (e.a < 0 || e.a > n_ || e.b < 0 || e.b > n_) {
            throw std::invalid_argument("edge endpoint out of range");
        }
    }
}

uint64_t UnionFindDecoder::decode(const std::vector<int>& syndrome,
                                  bool* syndrome_ok) const {
    const int V = num_nodes_;
    const int B = boundary_;
    const std::size_t E = edges_.size();

    // Input syndrome as a parity vector (duplicated ids cancel, like real XOR).
    std::vector<uint8_t> in_synd(V, 0);
    for (int d : syndrome) {
        if (d >= 0 && d < n_) in_synd[d] ^= 1;
    }

    // ---- Phase 1: grow clusters until every cluster is even ----------------
    ClusterSet cs(V);
    for (int i = 0; i < n_; ++i) cs.parity[i] = in_synd[i];
    cs.touches_boundary[B] = 1;

    // growth[e] counts grown half-edges: 2 means fully grown.
    std::vector<uint8_t> growth(E, 0), grown(E, 0);
    std::vector<std::pair<int, int>> to_merge;

    while (true) {
        bool any_odd = false;
        for (int i = 0; i < n_ && !any_odd; ++i) {
            if (cs.is_odd(i)) any_odd = true;
        }
        if (!any_odd) break;

        // Synchronous round: oddness is read from the state at the start of
        // the round, merges are applied after. Each odd cluster grows every
        // edge on its boundary by one half-edge.
        to_merge.clear();
        bool grew = false;
        for (std::size_t e = 0; e < E; ++e) {
            if (grown[e]) continue;
            const int inc = (cs.is_odd(edges_[e].a) ? 1 : 0) +
                            (cs.is_odd(edges_[e].b) ? 1 : 0);
            if (inc == 0) continue;
            growth[e] = static_cast<uint8_t>(growth[e] + inc);
            grew = true;
            if (growth[e] >= 2) {
                grown[e] = 1;
                to_merge.emplace_back(edges_[e].a, edges_[e].b);
            }
        }
        for (const auto& pr : to_merge) cs.unite(pr.first, pr.second);
        if (!grew) break;  // safety net: odd cluster with no way out
    }

    // ---- Phase 2: spanning forest of the grown region ----------------------
    Forest forest(V);
    std::vector<std::vector<int>> fadj(V);
    std::vector<int> deg(V, 0);
    for (std::size_t e = 0; e < E; ++e) {
        if (!grown[e]) continue;
        const int a = edges_[e].a, b = edges_[e].b;
        if (forest.join(a, b)) {
            fadj[a].push_back(static_cast<int>(e));
            fadj[b].push_back(static_cast<int>(e));
            ++deg[a];
            ++deg[b];
        }
    }

    // ---- Phase 3: peel leaves toward the boundary --------------------------
    // The boundary is never peeled: it is the sink that absorbs leftover
    // parity. Every other leaf that carries a defect pushes it to its parent
    // and puts the connecting edge in the correction.
    std::vector<uint8_t> defect(in_synd);
    std::vector<uint8_t> removed(E, 0);
    std::vector<uint8_t> recon(V, 0);  // syndrome implied by the correction
    std::vector<int> leaves;
    leaves.reserve(V);
    for (int v = 0; v < n_; ++v) {
        if (deg[v] == 1) leaves.push_back(v);
    }

    uint64_t correction = 0;
    for (std::size_t head = 0; head < leaves.size(); ++head) {
        const int u = leaves[head];
        if (deg[u] != 1) continue;  // stale entry
        int e = -1;
        for (int ei : fadj[u]) {
            if (!removed[ei]) { e = ei; break; }
        }
        if (e < 0) continue;
        const int v = (edges_[e].a == u) ? edges_[e].b : edges_[e].a;
        removed[e] = 1;
        --deg[u];
        --deg[v];
        if (defect[u]) {
            correction ^= edges_[e].obs;
            defect[u] = 0;
            if (v != B) defect[v] ^= 1;
            recon[u] ^= 1;
            recon[v] ^= 1;
        }
        if (v != B && deg[v] == 1) leaves.push_back(v);
    }

    if (syndrome_ok) {
        bool ok = true;
        for (int i = 0; i < n_ && ok; ++i) {
            if (recon[i] != in_synd[i]) ok = false;
        }
        *syndrome_ok = ok;
    }
    return correction;
}

UnionFindDecoder UnionFindDecoder::from_graph_file(const std::string& path) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("cannot open graph file: " + path);
    int n = 0, m = 0;
    if (!(in >> n >> m)) throw std::runtime_error("bad graph header in " + path);
    std::vector<Edge> edges;
    long long a, b;
    unsigned long long obs;
    while (in >> a >> b >> obs) {
        edges.push_back(Edge{static_cast<int>(a), static_cast<int>(b),
                             static_cast<uint64_t>(obs)});
    }
    return UnionFindDecoder(n, m, std::move(edges));
}

}  // namespace backlog
