// Window decoders: see window.hpp for the algorithms.
#include "backlog/window.hpp"

#include <algorithm>
#include <chrono>
#include <map>
#include <stdexcept>
#include <utility>

namespace backlog {
namespace detail {

// ---------------------------------------------------------------------------
// WindowPlan: the global graph, its time layers, and every window's local
// decoding problem. Windows with equal local problems share one shape.

class WindowPlan {
public:
    enum class Side { Open, Closed };

    struct Shape {
        DecodingGraph graph;          // local graph, kept to build decoders
        std::vector<uint8_t> commit;  // local edge -> committed if chosen
    };

    WindowPlan(DecodingGraph graph, std::vector<int32_t> layer);

    // Window decoding layers [first, end) and committing [commit_begin, commit_end).
    void add(Window::Kind kind, int32_t first, int32_t end, int32_t commit_begin,
             int32_t commit_end, Side past, Side future);
    void finish() { signatures_.clear(); }

    // Largest number of layers between the two detectors of one edge.
    int32_t max_span() const { return max_span_; }

    DecodingGraph g;
    std::vector<int32_t> layer;
    std::vector<int32_t> layer_begin;  // first detector of each layer, size L + 1
    int32_t num_layers = 0;
    std::vector<Window> windows;
    std::vector<Shape> shapes;

private:
    std::vector<int32_t> low_, high_;      // per edge: lowest detector, other end
    std::vector<int32_t> by_low_;          // edges sorted by low_
    std::vector<int32_t> by_high_;         // detector-detector edges sorted by high_
    std::map<std::vector<int64_t>, int32_t> signatures_;
    int32_t max_span_ = 0;
};

WindowPlan::WindowPlan(DecodingGraph graph, std::vector<int32_t> layers)
    : g(std::move(graph)), layer(std::move(layers)) {
    const int32_t n = g.num_detectors();
    const int32_t B = g.boundary();
    if (static_cast<int32_t>(layer.size()) != n) {
        throw std::invalid_argument("need one layer per detector");
    }
    for (int32_t v = 0; v < n; ++v) {
        if (layer[v] < 0) throw std::invalid_argument("layers must be >= 0");
        if (v > 0 && layer[v] < layer[v - 1]) {
            throw std::invalid_argument(
                "detectors must be numbered in time order (layer non-decreasing with id)");
        }
    }
    num_layers = n ? layer.back() + 1 : 0;
    layer_begin.assign(static_cast<std::size_t>(num_layers) + 1, n);
    for (int32_t t = 0; t <= num_layers; ++t) {
        layer_begin[t] = static_cast<int32_t>(
            std::lower_bound(layer.begin(), layer.end(), t) - layer.begin());
    }
    const int32_t E = g.num_edges();
    low_.resize(static_cast<std::size_t>(E));
    high_.resize(static_cast<std::size_t>(E));
    for (int32_t e = 0; e < E; ++e) {
        int32_t a = g.edge(e).a, b = g.edge(e).b;
        if (a == B) std::swap(a, b);
        if (b != B && b < a) std::swap(a, b);
        low_[e] = a;
        high_[e] = b;  // a detector above `a`, or the boundary
        if (b != B) max_span_ = std::max(max_span_, layer[b] - layer[a]);
    }
    for (int32_t e = 0; e < E; ++e) {
        by_low_.push_back(e);
        if (high_[e] != B) by_high_.push_back(e);
    }
    std::stable_sort(by_low_.begin(), by_low_.end(),
                     [this](int32_t x, int32_t y) { return low_[x] < low_[y]; });
    std::stable_sort(by_high_.begin(), by_high_.end(),
                     [this](int32_t x, int32_t y) { return high_[x] < high_[y]; });
}

void WindowPlan::add(Window::Kind kind, int32_t first, int32_t end, int32_t commit_begin,
                     int32_t commit_end, Side past, Side future) {
    const int32_t B = g.boundary();
    Window w;
    w.kind = kind;
    w.first_layer = first;
    w.end_layer = end;
    w.commit_begin = commit_begin;
    w.commit_end = commit_end;
    w.lo = layer_begin[first];
    w.hi = layer_begin[end];
    w.commit_lo = layer_begin[commit_begin];
    w.commit_hi = layer_begin[commit_end];
    const int32_t lo = w.lo, hi = w.hi;
    const int32_t num_local = hi - lo;
    if (num_local == 0) {
        windows.push_back(std::move(w));
        return;
    }

    // Every edge with at least one detector in [lo, hi).
    std::vector<int32_t> owned;
    auto by = [](const std::vector<int32_t>& key) {
        return [&key](int32_t e, int32_t v) { return key[e] < v; };
    };
    owned.assign(std::lower_bound(by_low_.begin(), by_low_.end(), lo, by(low_)),
                 std::lower_bound(by_low_.begin(), by_low_.end(), hi, by(low_)));
    if (past == Side::Open) {
        auto first_e = std::lower_bound(by_high_.begin(), by_high_.end(), lo, by(high_));
        auto last_e = std::lower_bound(by_high_.begin(), by_high_.end(), hi, by(high_));
        for (auto it = first_e; it != last_e; ++it) {
            if (low_[*it] < lo) owned.push_back(*it);
        }
    }
    std::sort(owned.begin(), owned.end());  // keep the global edge order

    const int32_t local_boundary = num_local;
    std::vector<Edge> edges;
    std::vector<int32_t> weights;
    std::vector<uint8_t> commit;
    auto inside = [lo, hi](int32_t v) { return v >= lo && v < hi; };
    auto committed = [&w, B](int32_t v) {
        return v != B && v >= w.commit_lo && v < w.commit_hi;
    };
    for (int32_t e : owned) {
        int32_t a = low_[e], b = high_[e];
        if (!inside(a)) std::swap(a, b);  // a is now in the window
        // b: inside, the boundary, before the window (open past) or after it.
        if (b != B && !inside(b) && b >= hi && future == Side::Closed) continue;
        if (b != B && !inside(b) && b < lo && past == Side::Closed) continue;
        const int32_t lb = (b != B && inside(b)) ? b - lo : local_boundary;
        edges.push_back(Edge{a - lo, lb, g.edge(e).obs, g.edge(e).probability});
        weights.push_back(g.weight(e));
        commit.push_back(committed(a) || committed(b) ? 1 : 0);
        w.edge_map.push_back(e);
    }

    std::vector<int64_t> sig;
    sig.reserve(1 + 5 * edges.size());
    sig.push_back(num_local);
    for (std::size_t i = 0; i < edges.size(); ++i) {
        sig.push_back(edges[i].a);
        sig.push_back(edges[i].b);
        sig.push_back(static_cast<int64_t>(edges[i].obs));
        sig.push_back(weights[i]);
        sig.push_back(commit[i]);
    }
    auto found = signatures_.find(sig);
    if (found != signatures_.end()) {
        w.shape = found->second;
    } else {
        w.shape = static_cast<int32_t>(shapes.size());
        shapes.push_back(Shape{DecodingGraph(num_local, g.num_observables(), std::move(edges),
                                             std::move(weights)),
                               std::move(commit)});
        signatures_.emplace(std::move(sig), w.shape);
    }
    windows.push_back(std::move(w));
}

// Decodes window w from its local defects (local ids, sorted). Commits the
// chosen edges that touch the commit region: returns their observables,
// appends their global ids to `correction`, and appends to `flips` each of
// their detectors outside the commit region.
uint64_t solve_window(const WindowPlan& plan, const Window& w, UnionFindDecoder& decoder,
                      const std::vector<int32_t>& local, std::vector<int32_t>& local_correction,
                      std::vector<int32_t>& flips, std::vector<int32_t>* correction) {
    const int32_t B = plan.g.boundary();
    decoder.decode(local.data(), local.size(), local_correction);
    const std::vector<uint8_t>& commit = plan.shapes[w.shape].commit;
    uint64_t obs = 0;
    for (int32_t e : local_correction) {
        if (!commit[e]) continue;
        const int32_t ge = w.edge_map[e];
        const Edge& edge = plan.g.edge(ge);
        obs ^= edge.obs;
        if (correction) correction->push_back(ge);
        for (int32_t u : {edge.a, edge.b}) {
            if (u != B && (u < w.commit_lo || u >= w.commit_hi)) flips.push_back(u);
        }
    }
    return obs;
}

// Sorts `v` and cancels pairs: a detector listed twice is not a defect.
void canonicalize(std::vector<int32_t>& v) {
    std::sort(v.begin(), v.end());
    std::size_t out = 0;
    for (std::size_t i = 0; i < v.size();) {
        std::size_t j = i;
        while (j < v.size() && v[j] == v[i]) ++j;
        if ((j - i) % 2 == 1) v[out++] = v[i];
        i = j;
    }
    v.resize(out);
}

void check_ids(const std::vector<int32_t>& v, int32_t n) {
    for (int32_t x : v) {
        if (x < 0 || x >= n) throw std::out_of_range("detector id out of range");
    }
}

}  // namespace detail

using detail::WindowPlan;
using Clock = std::chrono::steady_clock;

static double elapsed_ns(Clock::time_point t0) {
    return std::chrono::duration<double, std::nano>(Clock::now() - t0).count();
}

// ---------------------------------------------------------------------------
// SlidingWindowDecoder

SlidingWindowDecoder::SlidingWindowDecoder(DecodingGraph graph, std::vector<int32_t> layer,
                                           int32_t commit_layers, int32_t buffer_layers)
    : plan_(std::make_unique<WindowPlan>(std::move(graph), std::move(layer))) {
    if (commit_layers < 1) throw std::invalid_argument("commit_layers must be >= 1");
    if (buffer_layers < 0) throw std::invalid_argument("buffer_layers must be >= 0");
    WindowPlan& p = *plan_;
    const int32_t L = p.num_layers;
    for (int32_t s = 0; s < L; s += commit_layers) {
        const int32_t end = std::min(s + commit_layers + buffer_layers, L);
        const int32_t commit_end = (end == L) ? L : s + commit_layers;
        p.add(Window::Sliding, s, end, s, commit_end, WindowPlan::Side::Closed,
              WindowPlan::Side::Open);
        if (end == L) break;
    }
    p.finish();
    for (const WindowPlan::Shape& shape : p.shapes) {
        decoders_.push_back(std::make_unique<UnionFindDecoder>(shape.graph));
    }
    const std::size_t n = static_cast<std::size_t>(p.g.num_detectors());
    residual_.assign(n, 0);
    mark_.assign(n, 0);
}

SlidingWindowDecoder::~SlidingWindowDecoder() = default;

const DecodingGraph& SlidingWindowDecoder::graph() const { return plan_->g; }
int32_t SlidingWindowDecoder::num_layers() const { return plan_->num_layers; }
int32_t SlidingWindowDecoder::num_windows() const {
    return static_cast<int32_t>(plan_->windows.size());
}
int32_t SlidingWindowDecoder::num_shapes() const {
    return static_cast<int32_t>(plan_->shapes.size());
}
const std::vector<Window>& SlidingWindowDecoder::windows() const { return plan_->windows; }

uint64_t SlidingWindowDecoder::run(const int32_t* fired, std::size_t count,
                                   std::vector<int32_t>* correction, double* window_ns) {
    const WindowPlan& p = *plan_;
    uint64_t obs = 0;
    if (correction) correction->clear();
    pending_.clear();
    touched_.clear();
    for (std::size_t i = 0; i < count; ++i) {
        residual_[fired[i]] = 1;
        touched_.push_back(fired[i]);
    }

    std::size_t start = 0;  // first fired id not below the current window
    for (std::size_t k = 0; k < p.windows.size(); ++k) {
        const Window& w = p.windows[k];
        const Clock::time_point t0 = window_ns ? Clock::now() : Clock::time_point{};
        if (w.shape >= 0) {
            if (++mark_epoch_ == 0) {
                std::fill(mark_.begin(), mark_.end(), 0);
                mark_epoch_ = 1;
            }
            const uint32_t mark = mark_epoch_;
            local_.clear();
            while (start < count && fired[start] < w.lo) ++start;
            for (std::size_t i = start; i < count && fired[i] < w.hi; ++i) {
                const int32_t v = fired[i];
                if (residual_[v] && mark_[v] != mark) {
                    mark_[v] = mark;
                    local_.push_back(v - w.lo);
                }
            }
            bool merged = false;
            std::size_t keep = 0;
            for (int32_t v : pending_) {
                if (v < w.lo) continue;  // already behind us
                pending_[keep++] = v;
                if (v < w.hi && residual_[v] && mark_[v] != mark) {
                    mark_[v] = mark;
                    local_.push_back(v - w.lo);
                    merged = true;
                }
            }
            pending_.resize(keep);
            if (merged) std::sort(local_.begin(), local_.end());

            flips_.clear();
            obs ^= detail::solve_window(p, w, *decoders_[w.shape], local_, local_correction_,
                                        flips_, correction);
            for (int32_t u : flips_) {
                residual_[u] ^= 1;
                pending_.push_back(u);
                touched_.push_back(u);
            }
        }
        if (window_ns) window_ns[k] = elapsed_ns(t0);
    }
    for (int32_t v : touched_) residual_[v] = 0;
    return obs;
}

uint64_t SlidingWindowDecoder::decode(const int32_t* fired, std::size_t count) {
    sorted_.assign(fired, fired + count);
    detail::check_ids(sorted_, plan_->g.num_detectors());
    detail::canonicalize(sorted_);
    return run(sorted_.data(), sorted_.size(), nullptr, nullptr);
}

DecodeResult SlidingWindowDecoder::decode_detailed(const std::vector<int32_t>& fired) {
    DecodeResult r;
    sorted_ = fired;
    detail::check_ids(sorted_, plan_->g.num_detectors());
    detail::canonicalize(sorted_);
    r.observables = run(sorted_.data(), sorted_.size(), &r.correction, nullptr);
    r.syndrome_ok = correction_explains(plan_->g, fired, r.correction);
    return r;
}

namespace {

void check_rows(const DecodingGraph& g, std::size_t det_stride, std::size_t obs_stride) {
    const std::size_t det_bytes = (static_cast<std::size_t>(g.num_detectors()) + 7) / 8;
    const std::size_t obs_bytes = (static_cast<std::size_t>(g.num_observables()) + 7) / 8;
    if (det_stride < det_bytes) throw std::invalid_argument("detector rows too short");
    if (obs_stride < obs_bytes) throw std::invalid_argument("observable rows too short");
}

template <class Decode>
void batch(const DecodingGraph& g, const uint8_t* dets, std::size_t shots,
           std::size_t det_stride, uint8_t* obs_out, std::size_t obs_stride,
           std::vector<int32_t>& scratch, Decode&& decode_sorted) {
    check_rows(g, det_stride, obs_stride);
    const std::size_t det_bytes = (static_cast<std::size_t>(g.num_detectors()) + 7) / 8;
    const std::size_t obs_bytes = (static_cast<std::size_t>(g.num_observables()) + 7) / 8;
    for (std::size_t s = 0; s < shots; ++s) {
        unpack_shot(dets + s * det_stride, det_bytes, g.num_detectors(), scratch);
        const uint64_t o = decode_sorted();
        uint8_t* dst = obs_out + s * obs_stride;
        for (std::size_t j = 0; j < obs_bytes; ++j) dst[j] = static_cast<uint8_t>(o >> (8 * j));
    }
}

}  // namespace

void SlidingWindowDecoder::decode_batch_bit_packed(const uint8_t* dets, std::size_t shots,
                                                   std::size_t det_stride, uint8_t* obs_out,
                                                   std::size_t obs_stride) {
    batch(plan_->g, dets, shots, det_stride, obs_out, obs_stride, sorted_, [this] {
        return run(sorted_.data(), sorted_.size(), nullptr, nullptr);
    });
}

std::vector<double> SlidingWindowDecoder::window_latency_ns(const uint8_t* dets,
                                                            std::size_t shots,
                                                            std::size_t det_stride) {
    const DecodingGraph& g = plan_->g;
    check_rows(g, det_stride, (static_cast<std::size_t>(g.num_observables()) + 7) / 8);
    const std::size_t det_bytes = (static_cast<std::size_t>(g.num_detectors()) + 7) / 8;
    const std::size_t W = plan_->windows.size();
    std::vector<double> out(shots * W);
    volatile uint64_t sink = 0;
    for (std::size_t s = 0; s < shots; ++s) {
        unpack_shot(dets + s * det_stride, det_bytes, g.num_detectors(), sorted_);
        sink = sink ^ run(sorted_.data(), sorted_.size(), nullptr, out.data() + s * W);
    }
    (void)sink;
    return out;
}

}  // namespace backlog
