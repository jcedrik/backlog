// SlidingWindowDecoder: see window.hpp for the algorithm.
#include "backlog/window.hpp"

#include <algorithm>
#include <chrono>
#include <map>
#include <stdexcept>
#include <utility>

namespace backlog {

// A window's local decoding problem. Windows with equal signatures share it.
struct SlidingWindowDecoder::Shape {
    Shape(DecodingGraph graph, std::vector<uint8_t> commit_flags)
        : decoder(std::move(graph)), commit(std::move(commit_flags)) {}
    UnionFindDecoder decoder;
    std::vector<uint8_t> commit;  // local edge -> kept if chosen
};

namespace {

using Signature = std::vector<int64_t>;

Signature make_signature(int32_t num_local, const std::vector<Edge>& edges,
                         const std::vector<int32_t>& weights,
                         const std::vector<uint8_t>& commit) {
    Signature sig;
    sig.reserve(1 + 5 * edges.size());
    sig.push_back(num_local);
    for (std::size_t i = 0; i < edges.size(); ++i) {
        sig.push_back(edges[i].a);
        sig.push_back(edges[i].b);
        sig.push_back(static_cast<int64_t>(edges[i].obs));
        sig.push_back(weights[i]);
        sig.push_back(commit[i]);
    }
    return sig;
}

}  // namespace

SlidingWindowDecoder::SlidingWindowDecoder(DecodingGraph graph,
                                           std::vector<int32_t> layer,
                                           int32_t commit_layers,
                                           int32_t buffer_layers)
    : g_(std::move(graph)),
      layer_(std::move(layer)),
      commit_(commit_layers),
      buffer_(buffer_layers) {
    const int32_t n = g_.num_detectors();
    if (static_cast<int32_t>(layer_.size()) != n) {
        throw std::invalid_argument("need one layer per detector");
    }
    if (commit_ < 1) throw std::invalid_argument("commit_layers must be >= 1");
    if (buffer_ < 0) throw std::invalid_argument("buffer_layers must be >= 0");
    for (int32_t v = 0; v < n; ++v) {
        if (layer_[v] < 0) throw std::invalid_argument("layers must be >= 0");
        if (v > 0 && layer_[v] < layer_[v - 1]) {
            throw std::invalid_argument(
                "detectors must be numbered in time order (layer non-decreasing with id)");
        }
    }
    num_layers_ = n ? layer_.back() + 1 : 0;
    layer_begin_.assign(static_cast<std::size_t>(num_layers_) + 1, n);
    for (int32_t t = 0; t <= num_layers_; ++t) {
        layer_begin_[t] = static_cast<int32_t>(
            std::lower_bound(layer_.begin(), layer_.end(), t) - layer_.begin());
    }
    residual_.assign(static_cast<std::size_t>(n), 0);
    mark_.assign(static_cast<std::size_t>(n), 0);
    build_windows();
}

SlidingWindowDecoder::~SlidingWindowDecoder() = default;

void SlidingWindowDecoder::build_windows() {
    const int32_t n = g_.num_detectors();
    const int32_t B = g_.boundary();
    const int32_t E = g_.num_edges();

    // Each edge hangs off its lowest detector; a window owns the edges whose
    // lowest detector it contains. Edges whose lowest detector is in an
    // earlier window reach into the committed past and are dropped.
    std::vector<int32_t> low(static_cast<std::size_t>(E)), high(static_cast<std::size_t>(E));
    for (int32_t e = 0; e < E; ++e) {
        int32_t a = g_.edge(e).a, b = g_.edge(e).b;
        if (a == B) std::swap(a, b);
        if (b != B && b < a) std::swap(a, b);
        low[e] = a;
        high[e] = b;  // a detector above `a`, or the boundary
    }
    std::vector<int32_t> by_low(static_cast<std::size_t>(E));
    for (int32_t e = 0; e < E; ++e) by_low[e] = e;
    std::stable_sort(by_low.begin(), by_low.end(),
                     [&low](int32_t x, int32_t y) { return low[x] < low[y]; });

    std::map<Signature, int32_t> signatures;  // local structure -> shape id

    std::vector<int32_t> owned;
    std::vector<Edge> edges;
    std::vector<int32_t> weights;
    std::vector<uint8_t> commit;
    for (int32_t s = 0; n > 0; s += commit_) {
        Window w;
        w.first_layer = s;
        w.end_layer = std::min(s + commit_ + buffer_, num_layers_);
        w.commit_end = (w.end_layer == num_layers_) ? num_layers_ : s + commit_;
        const int32_t lo = layer_begin_[s];
        const int32_t hi = layer_begin_[w.end_layer];
        w.first_detector = lo;
        w.num_detectors = hi - lo;

        if (w.num_detectors > 0) {
            auto first = std::lower_bound(by_low.begin(), by_low.end(), lo,
                                          [&low](int32_t e, int32_t v) { return low[e] < v; });
            auto last = std::lower_bound(by_low.begin(), by_low.end(), hi,
                                         [&low](int32_t e, int32_t v) { return low[e] < v; });
            owned.assign(first, last);
            std::sort(owned.begin(), owned.end());  // keep global edge order
            edges.clear();
            weights.clear();
            commit.clear();
            w.edge_map.clear();
            const int32_t local_boundary = w.num_detectors;
            for (int32_t e : owned) {
                const int32_t a = low[e] - lo;
                // The boundary and every not-yet-seen detector act as the boundary.
                const int32_t b = (high[e] == B || high[e] >= hi) ? local_boundary : high[e] - lo;
                edges.push_back(Edge{a, b, g_.edge(e).obs, g_.edge(e).probability});
                weights.push_back(g_.weight(e));
                commit.push_back(layer_[low[e]] < w.commit_end ? 1 : 0);
                w.edge_map.push_back(e);
            }
            Signature sig = make_signature(w.num_detectors, edges, weights, commit);
            auto found = signatures.find(sig);
            if (found != signatures.end()) {
                w.shape = found->second;
            } else {
                w.shape = static_cast<int32_t>(shapes_.size());
                shapes_.push_back(std::make_unique<Shape>(
                    DecodingGraph(w.num_detectors, g_.num_observables(), edges, weights),
                    commit));
                signatures.emplace(std::move(sig), w.shape);
            }
        }
        windows_.push_back(std::move(w));
        if (windows_.back().end_layer == num_layers_) break;
    }
}

uint32_t SlidingWindowDecoder::next_mark() {
    if (++mark_epoch_ == 0) {
        std::fill(mark_.begin(), mark_.end(), 0);
        mark_epoch_ = 1;
    }
    return mark_epoch_;
}

uint64_t SlidingWindowDecoder::run(const int32_t* fired, std::size_t count,
                                   std::vector<int32_t>* correction,
                                   double* window_ns) {
    using Clock = std::chrono::steady_clock;
    const int32_t B = g_.boundary();
    uint64_t obs = 0;
    if (correction) correction->clear();
    pending_.clear();
    touched_.clear();
    for (std::size_t i = 0; i < count; ++i) {
        residual_[fired[i]] = 1;
        touched_.push_back(fired[i]);
    }

    std::size_t start = 0;  // first fired id not below the current window
    for (std::size_t k = 0; k < windows_.size(); ++k) {
        const Window& w = windows_[k];
        const Clock::time_point t0 = window_ns ? Clock::now() : Clock::time_point{};
        if (w.shape >= 0) {
            const int32_t lo = w.first_detector;
            const int32_t hi = lo + w.num_detectors;
            const uint32_t mark = next_mark();
            local_.clear();
            while (start < count && fired[start] < lo) ++start;
            for (std::size_t i = start; i < count && fired[i] < hi; ++i) {
                const int32_t v = fired[i];
                if (residual_[v] && mark_[v] != mark) {
                    mark_[v] = mark;
                    local_.push_back(v - lo);
                }
            }
            bool merged = false;
            std::size_t keep = 0;
            for (int32_t v : pending_) {
                if (v < lo) continue;  // already behind us
                pending_[keep++] = v;
                if (v < hi && residual_[v] && mark_[v] != mark) {
                    mark_[v] = mark;
                    local_.push_back(v - lo);
                    merged = true;
                }
            }
            pending_.resize(keep);
            if (merged) std::sort(local_.begin(), local_.end());

            Shape& shape = *shapes_[w.shape];
            shape.decoder.decode(local_.data(), local_.size(), local_correction_);
            for (int32_t e : local_correction_) {
                if (!shape.commit[e]) continue;
                const int32_t ge = w.edge_map[e];
                const Edge& edge = g_.edge(ge);
                obs ^= edge.obs;
                if (correction) correction->push_back(ge);
                for (int32_t u : {edge.a, edge.b}) {
                    if (u != B && layer_[u] >= w.commit_end) {
                        residual_[u] ^= 1;
                        pending_.push_back(u);
                        touched_.push_back(u);
                    }
                }
            }
        }
        if (window_ns) {
            window_ns[k] = std::chrono::duration<double, std::nano>(Clock::now() - t0).count();
        }
    }
    for (int32_t v : touched_) residual_[v] = 0;
    return obs;
}

void SlidingWindowDecoder::canonicalize(const int32_t* fired, std::size_t count) {
    const int32_t n = g_.num_detectors();
    sorted_.assign(fired, fired + count);
    for (int32_t v : sorted_) {
        if (v < 0 || v >= n) throw std::out_of_range("detector id out of range");
    }
    std::sort(sorted_.begin(), sorted_.end());
    // A detector listed twice cancels out.
    std::size_t out = 0;
    for (std::size_t i = 0; i < sorted_.size();) {
        std::size_t j = i;
        while (j < sorted_.size() && sorted_[j] == sorted_[i]) ++j;
        if ((j - i) % 2 == 1) sorted_[out++] = sorted_[i];
        i = j;
    }
    sorted_.resize(out);
}

uint64_t SlidingWindowDecoder::decode(const int32_t* fired, std::size_t count) {
    canonicalize(fired, count);
    return run(sorted_.data(), sorted_.size(), nullptr, nullptr);
}

DecodeResult SlidingWindowDecoder::decode_detailed(const std::vector<int32_t>& fired) {
    DecodeResult r;
    canonicalize(fired.data(), fired.size());
    r.observables = run(sorted_.data(), sorted_.size(), &r.correction, nullptr);
    r.syndrome_ok = correction_explains(g_, fired, r.correction);
    return r;
}

void SlidingWindowDecoder::decode_batch_bit_packed(const uint8_t* dets,
                                                   std::size_t shots,
                                                   std::size_t det_stride,
                                                   uint8_t* obs_out,
                                                   std::size_t obs_stride) {
    const int32_t n = g_.num_detectors();
    const std::size_t det_bytes = (static_cast<std::size_t>(n) + 7) / 8;
    const std::size_t obs_bytes = (static_cast<std::size_t>(g_.num_observables()) + 7) / 8;
    if (det_stride < det_bytes) throw std::invalid_argument("detector rows too short");
    if (obs_stride < obs_bytes) throw std::invalid_argument("observable rows too short");
    for (std::size_t s = 0; s < shots; ++s) {
        unpack_shot(dets + s * det_stride, det_bytes, n, sorted_);  // sorted, unique
        const uint64_t o = run(sorted_.data(), sorted_.size(), nullptr, nullptr);
        uint8_t* dst = obs_out + s * obs_stride;
        for (std::size_t j = 0; j < obs_bytes; ++j) {
            dst[j] = static_cast<uint8_t>(o >> (8 * j));
        }
    }
}

std::vector<double> SlidingWindowDecoder::window_latency_ns(const uint8_t* dets,
                                                            std::size_t shots,
                                                            std::size_t det_stride) {
    const int32_t n = g_.num_detectors();
    const std::size_t det_bytes = (static_cast<std::size_t>(n) + 7) / 8;
    if (det_stride < det_bytes) throw std::invalid_argument("detector rows too short");
    const std::size_t W = windows_.size();
    std::vector<double> out(shots * W);
    volatile uint64_t sink = 0;
    for (std::size_t s = 0; s < shots; ++s) {
        unpack_shot(dets + s * det_stride, det_bytes, n, sorted_);
        sink = sink ^ run(sorted_.data(), sorted_.size(), nullptr, out.data() + s * W);
    }
    (void)sink;
    return out;
}

}  // namespace backlog
