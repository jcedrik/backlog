// Dependency-free test suite for the decoders.
//
// The core of it is a differential fuzzer: thousands of random graphs, random
// integer weights and random error patterns, decoded by the fast decoder and
// by the reference decoder. They must agree exactly: same observable flips and
// the same correction, edge for edge, in the same order. The same fast decoder
// object is reused across shots, which also exercises its lazy state reset.
//
// The window decoders get their own fuzzers on random layered graphs: every
// committed correction must reproduce the syndrome, a single window covering
// every layer must give exactly the whole-shot answer, and the parallel
// decoder must give the same answer for any number of threads.
#include <algorithm>
#include <cstdlib>
#include <cstdio>
#include <random>
#include <set>
#include <stdexcept>
#include <utility>
#include <vector>

#include "backlog/decoder.hpp"
#include "backlog/window.hpp"

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

// Parallel edges are allowed on request: sliding windows create them (several
// edges into the future all become edges to the boundary).
DecodingGraph random_graph(std::mt19937_64& rng, bool uniform, bool parallel) {
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
        if (!used.insert(key).second && !parallel) continue;
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
        const bool parallel = (gi % 4 == 1);
        DecodingGraph g = random_graph(rng, uniform, parallel);
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

// Random layered graph: each layer is a tree hooked to the boundary, every
// detector reaches the next layer, and some edges skip a layer. Every window
// of whole layers can explain any syndrome, so the invariant must always hold.
struct LayeredGraph {
    DecodingGraph graph;
    std::vector<int32_t> layer;
};

LayeredGraph random_layered_graph(std::mt19937_64& rng, bool periodic) {
    auto pick = [&rng](int32_t lo, int32_t hi) {  // inclusive
        return lo + static_cast<int32_t>(rng() % static_cast<uint64_t>(hi - lo + 1));
    };
    const int32_t L = periodic ? 20 : pick(1, 8);
    const int32_t m = pick(1, 3);
    const int32_t per_layer = pick(1, 5);
    std::vector<int32_t> layer, begin;
    for (int32_t t = 0; t < L; ++t) {
        begin.push_back(static_cast<int32_t>(layer.size()));
        const int32_t k = periodic ? per_layer : pick(1, 5);
        for (int32_t i = 0; i < k; ++i) layer.push_back(t);
    }
    begin.push_back(static_cast<int32_t>(layer.size()));
    const int32_t n = static_cast<int32_t>(layer.size());

    // In periodic mode every layer reuses the same random choices.
    std::mt19937_64 layer_rng = rng;
    std::vector<Edge> edges;
    std::vector<int32_t> weights;
    for (int32_t t = 0; t < L; ++t) {
        std::mt19937_64& r = periodic ? layer_rng : rng;
        if (periodic) r.seed(12345);
        auto rpick = [&r](int32_t lo, int32_t hi) {
            return lo + static_cast<int32_t>(r() % static_cast<uint64_t>(hi - lo + 1));
        };
        auto add = [&](int32_t a, int32_t b) {
            if (b != n && (r() & 1)) std::swap(a, b);  // either orientation
            edges.push_back(edge(a, b, r() & ((1ull << m) - 1)));
            weights.push_back(rpick(1, 9));
        };
        const int32_t lo = begin[t], hi = begin[t + 1];
        for (int32_t v = lo + 1; v < hi; ++v) add(rpick(lo, v - 1), v);
        add(rpick(lo, hi - 1), n);
        for (int32_t v = lo; v < hi; ++v) {
            if (r() % 2) add(v, n);
            if (t + 1 < L) {
                add(v, rpick(begin[t + 1], begin[t + 2] - 1));
                if (r() % 2) add(v, rpick(begin[t + 1], begin[t + 2] - 1));
            }
            if (t + 2 < L && r() % 7 == 0) add(v, rpick(begin[t + 2], begin[t + 3] - 1));
        }
    }
    return {DecodingGraph(n, m, std::move(edges), std::move(weights)), std::move(layer)};
}

// Two layers, one detector each: D0 (layer 0) and D1 (layer 1).
//   edge 0: D0 - D1  weight 1, obs 1   (a likely error between two rounds)
//   edge 1: D0 - B   weight 9
//   edge 2: D1 - B   weight 9
// With a window of one layer and no buffer, D0 cannot see D1, but its edge to
// the future acts as a boundary edge: the cheap explanation is still found,
// committed, and its far end is carried into the next window.
void test_window_matches_across_the_window_top() {
    const std::vector<Edge> edges = {edge(0, 1, 1), edge(0, 2), edge(1, 2)};
    DecodingGraph g(2, 1, edges, std::vector<int32_t>{1, 9, 9});
    SlidingWindowDecoder win(g, {0, 1}, 1, 0);
    CHECK(win.num_windows() == 2);
    const DecodeResult r = win.decode_detailed({0, 1});
    CHECK(r.correction == std::vector<int32_t>{0});
    CHECK(r.observables == 1);
    CHECK(r.syndrome_ok);
    const DecodeResult only_first = win.decode_detailed({0});  // D1 quiet
    CHECK(only_first.syndrome_ok);
}

void test_window_shapes_are_shared() {
    std::mt19937_64 rng(7);
    LayeredGraph lg = random_layered_graph(rng, /*periodic=*/true);
    SlidingWindowDecoder win(lg.graph, lg.layer, 2, 2);
    // 20 layers, commit 2, buffer 2: windows start at 0, 2, ..., 16.
    CHECK(win.num_layers() == 20);
    CHECK(win.num_windows() == 9);
    CHECK(win.windows().back().end_layer == 20);
    CHECK(win.windows().back().commit_end == 20);
    CHECK(win.windows()[3].commit_end == 8);
    CHECK(win.windows()[3].end_layer == 10);
    CHECK(win.num_shapes() <= 3);  // bulk windows share one decoder

    bool threw = false;
    try {
        SlidingWindowDecoder bad(lg.graph, std::vector<int32_t>(lg.layer.rbegin(), lg.layer.rend()), 2, 2);
    } catch (const std::invalid_argument&) {
        threw = true;
    }
    CHECK(threw);  // layers must follow detector order
}

void test_window_fuzz(int num_graphs, int shots_per_graph) {
    std::mt19937_64 rng(20261105);
    long long cases = 0, invalid = 0, inconsistent = 0, differ = 0;
    for (int gi = 0; gi < num_graphs; ++gi) {
        LayeredGraph lg = random_layered_graph(rng, gi % 10 == 0);
        const DecodingGraph& g = lg.graph;
        const int32_t L = lg.layer.back() + 1;
        const int32_t C = 1 + static_cast<int32_t>(rng() % 3);
        const int32_t B = static_cast<int32_t>(rng() % 4);
        SlidingWindowDecoder win(g, lg.layer, C, B);
        SlidingWindowDecoder single(g, lg.layer, L, static_cast<int32_t>(rng() % 3));
        UnionFindDecoder whole(g);
        CHECK(single.num_windows() == 1);
        std::uniform_real_distribution<double> unit(0.0, 1.0);
        const double q = 0.05 + 0.45 * unit(rng);
        for (int s = 0; s < shots_per_graph; ++s) {
            std::vector<int32_t> fired;
            for (int32_t v = 0; v < g.num_detectors(); ++v) {
                if (unit(rng) < q) fired.push_back(v);
            }
            ++cases;
            const DecodeResult r = win.decode_detailed(fired);
            uint64_t x = 0;
            for (int32_t e : r.correction) x ^= g.edge(e).obs;
            if (!r.syndrome_ok) ++invalid;
            if (x != r.observables || win.decode(fired) != r.observables) ++inconsistent;

            // One window covering everything is exactly the whole-shot decoder.
            const DecodeResult a = single.decode_detailed(fired);
            const DecodeResult b = whole.decode_detailed(fired);
            if (a.correction != b.correction || a.observables != b.observables) ++differ;
        }
    }
    std::printf("windows: %lld random cases, %lld invalid corrections, %lld inconsistent, "
                "%lld differences with whole-shot decoding\n",
                cases, invalid, inconsistent, differ);
    CHECK(invalid == 0);
    CHECK(inconsistent == 0);
    CHECK(differ == 0);
}

void test_window_batch_matches_single_shots() {
    std::mt19937_64 rng(99);
    LayeredGraph lg = random_layered_graph(rng, /*periodic=*/true);
    SlidingWindowDecoder win(lg.graph, lg.layer, 3, 2);
    const int32_t n = lg.graph.num_detectors();
    const std::size_t stride = (static_cast<std::size_t>(n) + 7) / 8;
    const std::size_t shots = 64;
    std::vector<uint8_t> packed(shots * stride, 0);
    std::vector<std::vector<int32_t>> fired(shots);
    for (std::size_t s = 0; s < shots; ++s) {
        for (int32_t v = 0; v < n; ++v) {
            if (rng() % 5 == 0) {
                fired[s].push_back(v);
                packed[s * stride + v / 8] |= static_cast<uint8_t>(1u << (v % 8));
            }
        }
    }
    std::vector<uint8_t> out(shots, 0xFF);
    win.decode_batch_bit_packed(packed.data(), shots, stride, out.data(), 1);
    for (std::size_t s = 0; s < shots; ++s) {
        CHECK(out[s] == static_cast<uint8_t>(win.decode(fired[s])));
    }
    const std::vector<double> lat = win.window_latency_ns(packed.data(), shots, stride);
    CHECK(lat.size() == shots * static_cast<std::size_t>(win.num_windows()));
}

int32_t max_span(const LayeredGraph& lg) {
    int32_t span = 0;
    for (const Edge& e : lg.graph.edges()) {
        if (e.a != lg.graph.boundary() && e.b != lg.graph.boundary()) {
            span = std::max(span, std::abs(lg.layer[e.a] - lg.layer[e.b]));
        }
    }
    return span;
}

// Three layers, one detector each: D0, D1, D2, chained in time.
//   edge 0: D0 - D1  weight 1      edge 2: D0 - B  weight 9
//   edge 1: D1 - D2  weight 1      edge 3: D1 - B  weight 9
//                                  edge 4: D2 - B  weight 9
// Parallel windows with commit 1, no buffer, seam 1: core 0 commits D0, the
// seam is D1, core 1 commits D2. An error on edge 1 fires D1 and D2. Core 1
// must see edge 1 through its open past (as a boundary edge), commit it, and
// hand the flip of D1 to the seam, which then has nothing left to do.
void test_parallel_core_sees_its_past() {
    const std::vector<Edge> edges = {edge(0, 1), edge(1, 2), edge(0, 3), edge(1, 3), edge(2, 3)};
    DecodingGraph g(3, 1, edges, std::vector<int32_t>{1, 1, 9, 9, 9});
    ParallelWindowDecoder par(g, {0, 1, 2}, 1, 0, 1, 2);
    CHECK(par.num_windows() == 3);
    const DecodeResult r = par.decode_detailed({1, 2});
    CHECK(r.correction == std::vector<int32_t>{1});
    CHECK(r.syndrome_ok);
}

void test_parallel_layout() {
    std::mt19937_64 rng(7);
    LayeredGraph lg = random_layered_graph(rng, /*periodic=*/true);
    // 20 layers, commit 2, buffer 2, seam 2: cores commit [0,2), [4,6), ...
    ParallelWindowDecoder par(lg.graph, lg.layer, 2, 2, 2, 2);
    CHECK(par.num_threads() == 2);
    CHECK(par.num_windows() == 10);
    const std::vector<Window>& w = par.windows();
    CHECK(w[0].kind == Window::Core && w[0].first_layer == 0 && w[0].end_layer == 4);
    CHECK(w[1].kind == Window::Seam && w[1].first_layer == 2 && w[1].end_layer == 4);
    CHECK(w[2].kind == Window::Core && w[2].first_layer == 2 && w[2].end_layer == 8);
    CHECK(w[2].commit_begin == 4 && w[2].commit_end == 6);
    CHECK(w.back().kind == Window::Seam && w.back().end_layer == 20);
    CHECK(par.num_shapes() <= 5);  // bulk cores share one, bulk seams another

    bool threw = false;
    try {
        ParallelWindowDecoder bad(lg.graph, lg.layer, 2, 2, 0, 1);
    } catch (const std::invalid_argument&) {
        threw = true;
    }
    CHECK(threw);  // a seam must hold at least one layer
}

void test_parallel_fuzz(int num_graphs, int shots_per_graph) {
    std::mt19937_64 rng(20261106);
    long long cases = 0, invalid = 0, inconsistent = 0, thread_dependent = 0, differ = 0;
    for (int gi = 0; gi < num_graphs; ++gi) {
        LayeredGraph lg = random_layered_graph(rng, gi % 10 == 0);
        const DecodingGraph& g = lg.graph;
        const int32_t L = lg.layer.back() + 1;
        const int32_t C = 1 + static_cast<int32_t>(rng() % 3);
        const int32_t B = static_cast<int32_t>(rng() % 4);
        const int32_t G = std::max<int32_t>(max_span(lg), 1 + static_cast<int32_t>(rng() % 3));
        ParallelWindowDecoder one_thread(g, lg.layer, C, B, G, 1);
        ParallelWindowDecoder three_threads(g, lg.layer, C, B, G, 3);
        ParallelWindowDecoder single(g, lg.layer, L, static_cast<int32_t>(rng() % 3), G, 2);
        UnionFindDecoder whole(g);
        CHECK(single.num_windows() == 1);
        std::uniform_real_distribution<double> unit(0.0, 1.0);
        const double q = 0.05 + 0.45 * unit(rng);
        for (int s = 0; s < shots_per_graph; ++s) {
            std::vector<int32_t> fired;
            for (int32_t v = 0; v < g.num_detectors(); ++v) {
                if (unit(rng) < q) fired.push_back(v);
            }
            ++cases;
            const DecodeResult r = one_thread.decode_detailed(fired);
            uint64_t x = 0;
            for (int32_t e : r.correction) x ^= g.edge(e).obs;
            if (!r.syndrome_ok) ++invalid;
            if (x != r.observables || one_thread.decode(fired) != r.observables) ++inconsistent;

            // Any number of threads gives exactly the same answer.
            const DecodeResult r3 = three_threads.decode_detailed(fired);
            if (r3.correction != r.correction || r3.observables != r.observables) {
                ++thread_dependent;
            }

            // One core window covering everything is the whole-shot decoder.
            const DecodeResult a = single.decode_detailed(fired);
            const DecodeResult b = whole.decode_detailed(fired);
            if (a.correction != b.correction || a.observables != b.observables) ++differ;
        }
    }
    std::printf("parallel windows: %lld random cases, %lld invalid corrections, %lld inconsistent, "
                "%lld depend on the thread count, %lld differences with whole-shot decoding\n",
                cases, invalid, inconsistent, thread_dependent, differ);
    CHECK(invalid == 0);
    CHECK(inconsistent == 0);
    CHECK(thread_dependent == 0);
    CHECK(differ == 0);
}

void test_parallel_batch_matches_single_shots() {
    std::mt19937_64 rng(99);
    LayeredGraph lg = random_layered_graph(rng, /*periodic=*/true);
    ParallelWindowDecoder par(lg.graph, lg.layer, 3, 2, 2, 2);
    const int32_t n = lg.graph.num_detectors();
    const std::size_t stride = (static_cast<std::size_t>(n) + 7) / 8;
    const std::size_t shots = 64;
    std::vector<uint8_t> packed(shots * stride, 0);
    std::vector<std::vector<int32_t>> fired(shots);
    for (std::size_t s = 0; s < shots; ++s) {
        for (int32_t v = 0; v < n; ++v) {
            if (rng() % 5 == 0) {
                fired[s].push_back(v);
                packed[s * stride + v / 8] |= static_cast<uint8_t>(1u << (v % 8));
            }
        }
    }
    std::vector<uint8_t> out(shots, 0xFF);
    par.decode_batch_bit_packed(packed.data(), shots, stride, out.data(), 1);
    for (std::size_t s = 0; s < shots; ++s) {
        CHECK(out[s] == static_cast<uint8_t>(par.decode(fired[s])));
    }
    CHECK(par.window_latency_ns(packed.data(), shots, stride).size() ==
          shots * static_cast<std::size_t>(par.num_windows()));
    CHECK(par.shot_latency_ns(packed.data(), shots, stride).size() == shots);
}

}  // namespace

int main() {
    test_basics();
    test_weights_change_the_decision();
    test_log_likelihood_weights();
    test_unexplainable_syndrome();
    test_batch_matches_single_shots();
    test_fuzz_fast_equals_reference(3000, 25);
    test_window_matches_across_the_window_top();
    test_window_shapes_are_shared();
    test_window_fuzz(3000, 25);
    test_window_batch_matches_single_shots();
    test_parallel_core_sees_its_past();
    test_parallel_layout();
    test_parallel_fuzz(3000, 25);
    test_parallel_batch_matches_single_shots();

    std::printf("%d checks, %d failures\n", g_checks, g_failures);
    if (g_failures) {
        std::printf("FAILED\n");
        return 1;
    }
    std::printf("ALL C++ TESTS PASSED\n");
    return 0;
}
