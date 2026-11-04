// ReferenceDecoder: the specification the fast decoder is tested against.
//
// Synchronous unit rounds over every edge of the graph, every round. Slow on
// purpose: each line maps directly to the algorithm description in
// decoder.hpp, so it is easy to convince yourself it is right.
#include <algorithm>
#include <numeric>
#include <stdexcept>
#include <utility>

#include "backlog/decoder.hpp"

namespace backlog {

ReferenceDecoder::ReferenceDecoder(DecodingGraph graph) : g_(std::move(graph)) {}

DecodeResult ReferenceDecoder::decode_detailed(const std::vector<int32_t>& fired) const {
    const int32_t n = g_.num_detectors();
    const int32_t V = g_.num_nodes();
    const int32_t B = g_.boundary();
    const int32_t E = g_.num_edges();
    const std::vector<int32_t>& weight = g_.weights();

    std::vector<uint8_t> syndrome(V, 0);
    for (int32_t d : fired) {
        if (d < 0 || d >= n) throw std::out_of_range("detector id out of range");
        syndrome[d] ^= 1;
    }

    // Clusters: disjoint sets with parity and boundary contact at the root.
    std::vector<int32_t> parent(V);
    std::iota(parent.begin(), parent.end(), 0);
    std::vector<int32_t> rank(V, 0);
    std::vector<uint8_t> parity(syndrome);
    std::vector<uint8_t> at_boundary(V, 0);
    at_boundary[B] = 1;
    auto find = [&parent](int32_t x) {
        while (parent[x] != x) {
            parent[x] = parent[parent[x]];
            x = parent[x];
        }
        return x;
    };
    auto unite = [&](int32_t x, int32_t y) {
        int32_t rx = find(x), ry = find(y);
        if (rx == ry) return;
        if (rank[rx] < rank[ry]) std::swap(rx, ry);
        parent[ry] = rx;
        if (rank[rx] == rank[ry]) ++rank[rx];
        parity[rx] ^= parity[ry];
        at_boundary[rx] |= at_boundary[ry];
    };
    auto odd = [&](int32_t root) { return parity[root] && !at_boundary[root]; };

    // Growth, one unit round at a time.
    std::vector<int32_t> growth(E, 0);
    std::vector<uint8_t> full(E, 0);
    std::vector<int32_t> to_merge, full_edges;
    while (true) {
        bool any_odd = false;
        for (int32_t v = 0; v < n && !any_odd; ++v) any_odd = odd(find(v));
        if (!any_odd) break;

        to_merge.clear();
        bool grew = false;
        for (int32_t e = 0; e < E; ++e) {
            if (full[e]) continue;
            const int32_t ra = find(g_.edge(e).a), rb = find(g_.edge(e).b);
            if (ra == rb) continue;  // internal edges do not grow
            const int32_t inc = (odd(ra) ? 1 : 0) + (odd(rb) ? 1 : 0);
            if (inc == 0) continue;
            growth[e] += inc;
            grew = true;
            if (growth[e] >= weight[e]) {
                full[e] = 1;
                to_merge.push_back(e);
            }
        }
        for (int32_t e : to_merge) {
            unite(g_.edge(e).a, g_.edge(e).b);
            full_edges.push_back(e);
        }
        if (!grew) break;
    }

    // Spanning forest in (weight, edge id) order.
    std::sort(full_edges.begin(), full_edges.end(), [&weight](int32_t x, int32_t y) {
        return weight[x] != weight[y] ? weight[x] < weight[y] : x < y;
    });
    std::vector<int32_t> fparent(V);
    std::iota(fparent.begin(), fparent.end(), 0);
    auto ffind = [&fparent](int32_t x) {
        while (fparent[x] != x) {
            fparent[x] = fparent[fparent[x]];
            x = fparent[x];
        }
        return x;
    };
    std::vector<std::vector<int32_t>> fadj(V);
    std::vector<int32_t> fdeg(V, 0);
    std::vector<int32_t> forest_nodes;  // in order of first appearance
    for (int32_t e : full_edges) {
        const Edge& ed = g_.edge(e);
        const int32_t ra = ffind(ed.a), rb = ffind(ed.b);
        if (ra == rb) continue;
        fparent[ra] = rb;
        fadj[ed.a].push_back(e);
        fadj[ed.b].push_back(e);
        if (fdeg[ed.a]++ == 0) forest_nodes.push_back(ed.a);
        if (fdeg[ed.b]++ == 0) forest_nodes.push_back(ed.b);
    }

    // Peeling: leaves taken in order of first appearance in the forest, the
    // boundary is never peeled.
    std::vector<uint8_t> defect(syndrome);
    std::vector<uint8_t> removed(E, 0);
    std::vector<int32_t> leaves;
    for (int32_t v : forest_nodes) {
        if (v != B && fdeg[v] == 1) leaves.push_back(v);
    }
    DecodeResult result;
    for (std::size_t head = 0; head < leaves.size(); ++head) {
        const int32_t u = leaves[head];
        if (fdeg[u] != 1) continue;
        int32_t e = -1;
        for (int32_t x : fadj[u]) {
            if (!removed[x]) {
                e = x;
                break;
            }
        }
        if (e < 0) continue;
        const Edge& ed = g_.edge(e);
        const int32_t v = (ed.a == u) ? ed.b : ed.a;
        removed[e] = 1;
        --fdeg[u];
        --fdeg[v];
        if (defect[u]) {
            result.observables ^= ed.obs;
            result.correction.push_back(e);
            defect[u] = 0;
            if (v != B) defect[v] ^= 1;
        }
        if (v != B && fdeg[v] == 1) leaves.push_back(v);
    }
    result.syndrome_ok = correction_explains(g_, fired, result.correction);
    return result;
}

}  // namespace backlog
