// Dependency-free test suite for the decoders.
//
// The core of it is a differential fuzzer: thousands of random graphs, random
// integer weights and random error patterns, decoded by the fast decoder and
// by the reference decoder. They must agree exactly: same observable flips and
// the same correction, edge for edge, in the same order. The same fast decoder
// object is reused across shots, which also exercises its lazy state reset.
#include <algorithm>
#include <cstdio>
#include <random>
#include <set>
#include <stdexcept>
#include <utility>
#include <vector>

#include "backlog/decoder.hpp"

using namespace backlog;

namespace {

int g_checks = 0;
int g_failures = 0;

#define CHECK(cond)                                                          \
    do {                                                                     \
        ++g_checks;                                                          \
        if (!(cond)) {                                                       \
            ++g_failures;                                                    \
            if (g_failures <= 20)                                            \
                std::fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, \
                             #cond);                                         \
        }                                                                    \
    } while (0)

Edge edge(int32_t a, int32_t b, uint64_t obs = 0, double p = 0.01) {
    return Edge{a, b, obs, p};
}

// B - D0 - D1 - D2 - B. Edge 0 is D0-B (obs 0), edge 3 is D2-B (obs 1).
std::vector<Edge> chain_edges() {
    return {edge(0, 3, 0), edge(0, 1), edge(1, 2), edge(2, 3, 1)};
}

void test_basics() {
    UnionFindDecoder dec(DecodingGraph(3, 1, chain_edges(), Weighting::Uniform));

    DecodeResult r = dec.decode_detailed({});
    CHECK(r.observables == 0);
    CHECK(r.correction.empty());
    CHECK(r.syndrome_ok);

    r = dec.decode_detailed({1, 1});  // a detector listed twice cancels
    CHECK(r.correction.empty());
    CHECK(r.syndrome_ok);

    r = dec.decode_detailed({0, 1});  // adjacent pair: the edge between them
    CHECK(r.correction == std::vector<int32_t>{1});
    CHECK(r.observables == 0);
    CHECK(r.syndrome_ok);

    bool threw = false;
    try {
        dec.decode(std::vector<int32_t>{7});
    } catch (const std::out_of_range&) {
        threw = true;
    }
    CHECK(threw);
}

void test_weights_change_the_decision() {
    // One defect on D0. Unweighted: the left boundary is one edge away, take
    // it. Weighted: that edge is unlikely (weight 10) while the path to the
    // right boundary is three likely edges (weight 1 each), so go right.
    UnionFindDecoder uniform(DecodingGraph(3, 1, chain_edges(), Weighting::Uniform));
    UnionFindDecoder weighted(
        DecodingGraph(3, 1, chain_edges(), std::vector<int32_t>{10, 1, 1, 1}));

    const DecodeResult ru = uniform.decode_detailed({0});
    CHECK(ru.correction == std::vector<int32_t>{0});
    CHECK(ru.observables == 0);
    CHECK(ru.syndrome_ok);

    const DecodeResult rw = weighted.decode_detailed({0});
    CHECK((rw.correction == std::vector<int32_t>{1, 2, 3}));
    CHECK(rw.observables == 1);
    CHECK(rw.syndrome_ok);
}

void test_log_likelihood_weights() {
    // Likelier edges must get smaller weights, and the heaviest edge maps to
    // the resolution.
    std::vector<Edge> edges = {edge(0, 1, 0, 0.1), edge(1, 2, 0, 0.01),
                               edge(0, 3, 0, 0.001)};
    const std::vector<int32_t> w =
        compute_weights(edges, Weighting::LogLikelihood, 1000);
    CHECK(w[0] < w[1]);
    CHECK(w[1] < w[2]);
    CHECK(w[2] == 1000);
    const std::vector<int32_t> u = compute_weights(edges, Weighting::Uniform, 1000);
    CHECK((u == std::vector<int32_t>{2, 2, 2}));
}

void test_unexplainable_syndrome() {
    // Two detectors, one edge, no boundary: a single defect cannot be
    // explained. Both decoders must say so, and agree on what they tried.
    DecodingGraph g(2, 1, {edge(0, 1, 1)}, Weighting::Uniform);
    UnionFindDecoder fast(g);
    ReferenceDecoder ref(g);
    const DecodeResult rf = fast.decode_detailed({0});
    const DecodeResult rr = ref.decode_detailed({0});
    CHECK(!rf.syndrome_ok);
    CHECK(!rr.syndrome_ok);
    CHECK(rf.correction == rr.correction);
}

void test_batch_matches_single_shots() {
    UnionFindDecoder dec(DecodingGraph(3, 1, chain_edges(), Weighting::Uniform));
    // shots: {}, {0}, {0,1}, {2}
    const std::vector<std::vector<int32_t>> shots = {{}, {0}, {0, 1}, {2}};
    std::vector<uint8_t> packed(shots.size(), 0);
    for (std::size_t s = 0; s < shots.size(); ++s) {
        for (int32_t d : shots[s]) packed[s] |= static_cast<uint8_t>(1u << d);
    }
    std::vector<uint8_t> out(shots.size(), 0xFF);
    dec.decode_batch_bit_packed(packed.data(), shots.size(), 1, out.data(), 1);
    for (std::size_t s = 0; s < shots.size(); ++s) {
        CHECK(out[s] == static_cast<uint8_t>(dec.decode(shots[s])));
    }
    const std::vector<double> lat = dec.latency_ns(packed.data(), shots.size(), 1);
    CHECK(lat.size() == shots.size());
}

DecodingGraph random_graph(std::mt19937_64& rng, bool uniform) {
    const int32_t n = 1 + static_cast<int32_t>(rng() % 40);
    const int32_t m = 1 + static_cast<int32_t>(rng() % 3);
    const std::size_t target = static_cast<std::size_t>(n) + rng() % (2 * n + 1);
    std::set<std::pair<int32_t, int32_t>> used;
    std::vector<Edge> edges;
    for (std::size_t attempt = 0; attempt < 8 * target && edges.size() < target; ++attempt) {
        const int32_t a = static_cast<int32_t>(rng() % n);
        const int32_t b = (rng() % 4 == 0) ? n : static_cast<int32_t>(rng() % n);
        if (a == b) continue;
        const auto key = std::minmax(a, b);
        if (!used.insert(key).second) continue;
        edges.push_back(edge(a, b, rng() & ((1ull << m) - 1)));
    }
    std::vector<int32_t> weights(edges.size(), 2);
    if (!uniform) {
        for (int32_t& w : weights) w = 1 + static_cast<int32_t>(rng() % 9);
    }
    return DecodingGraph(n, m, std::move(edges), std::move(weights));
}

void test_fuzz_fast_equals_reference(int num_graphs, int shots_per_graph) {
    std::mt19937_64 rng(20261104);
    long long cases = 0, mismatches = 0;
    for (int gi = 0; gi < num_graphs; ++gi) {
        const bool uniform = (gi % 3 == 0);
        DecodingGraph g = random_graph(rng, uniform);
        UnionFindDecoder fast(g);  // reused across all shots below
        ReferenceDecoder ref(g);
        std::uniform_real_distribution<double> unit(0.0, 1.0);
        const double q = 0.02 + 0.3 * unit(rng);
        for (int s = 0; s < shots_per_graph; ++s) {
            // Syndrome from a random error pattern, so a valid correction
            // always exists.
            std::vector<uint8_t> flip(g.num_nodes(), 0);
            for (const Edge& e : g.edges()) {
                if (unit(rng) < q) {
                    flip[e.a] ^= 1;
                    flip[e.b] ^= 1;
                }
            }
            std::vector<int32_t> fired;
            for (int32_t v = 0; v < g.num_detectors(); ++v) {
                if (flip[v]) fired.push_back(v);
            }
            std::shuffle(fired.begin(), fired.end(), rng);

            const DecodeResult rf = fast.decode_detailed(fired);
            const DecodeResult rr = ref.decode_detailed(fired);
            ++cases;
            const bool same = rf.observables == rr.observables &&
                              rf.correction == rr.correction;
            if (!same) ++mismatches;
            CHECK(same);
            CHECK(rf.syndrome_ok);
            CHECK(rr.syndrome_ok);
            CHECK(fast.decode(fired) == rf.observables);  // fast path agrees
        }
    }
    std::printf("fuzz: %lld random cases, %lld mismatches between fast and reference\n",
                cases, mismatches);
}

}  // namespace

int main() {
    test_basics();
    test_weights_change_the_decision();
    test_log_likelihood_weights();
    test_unexplainable_syndrome();
    test_batch_matches_single_shots();
    test_fuzz_fast_equals_reference(3000, 25);

    std::printf("%d checks, %d failures\n", g_checks, g_failures);
    if (g_failures) {
        std::printf("FAILED\n");
        return 1;
    }
    std::printf("ALL C++ TESTS PASSED\n");
    return 0;
}
