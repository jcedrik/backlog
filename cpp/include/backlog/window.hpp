// Window decoders: the weighted Union-Find decoder applied to slices of time.
//
// Detectors are grouped in time layers, one per round of syndrome extraction.
// A real-time decoder cannot wait for the end of the experiment, so the
// decoders here split the layers into windows and decode each window on its
// own. A window decodes its layers, keeps (commits) the part of the
// correction that touches its commit region, and passes the syndrome flips of
// those edges on to whichever window decodes their other end.
//
// Inside a window, an edge leaving the window is either dropped (that side is
// closed: it was already committed) or turned into an edge to the boundary
// (that side is open: not decided yet, so a defect near it may be explained by
// an error whose other half lies outside).
//
// SlidingWindowDecoder (one core, rounds in order):
//
//   layers   [ s ................ s+C ................ s+C+B )
//            |   commit region     |    buffer region      |
//            closed past                               open future
//
//   decode, commit the oldest C layers, slide by C. The last window commits
//   everything.
//
// Every detector is explained by exactly one window's committed
// edges plus the flips passed to it, so the committed correction always
// reproduces the syndrome. With one window covering every layer, the result is
// exactly the whole-shot decoder's.
//
// Windows with the same local structure (the periodic bulk of a memory
// experiment) share one local decoding problem, so memory does not grow with
// the number of rounds.
#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <vector>

#include "backlog/decoder.hpp"
#include "backlog/graph.hpp"

namespace backlog {

struct Window {
    enum Kind : uint8_t { Sliding = 0 };
    Kind kind = Sliding;
    int32_t first_layer = 0;   // layers [first_layer, end_layer) are decoded
    int32_t end_layer = 0;
    int32_t commit_begin = 0;  // layers [commit_begin, commit_end) are committed
    int32_t commit_end = 0;
    int32_t lo = 0;            // detector ids decoded: [lo, hi)
    int32_t hi = 0;
    int32_t commit_lo = 0;     // detector ids committed: [commit_lo, commit_hi)
    int32_t commit_hi = 0;
    int32_t shape = -1;        // shared local problem, -1 if the window is empty
    std::vector<int32_t> edge_map;  // local edge -> global edge
};

namespace detail {
class WindowPlan;
}  // namespace detail

class SlidingWindowDecoder {
public:
    // layer[v] is the time layer of detector v. Layers must be non-decreasing
    // with the detector id, which holds for Stim circuits (detectors are
    // declared in time order). commit_layers >= 1, buffer_layers >= 0.
    SlidingWindowDecoder(DecodingGraph graph, std::vector<int32_t> layer,
                         int32_t commit_layers, int32_t buffer_layers);
    ~SlidingWindowDecoder();
    SlidingWindowDecoder(const SlidingWindowDecoder&) = delete;
    SlidingWindowDecoder& operator=(const SlidingWindowDecoder&) = delete;

    const DecodingGraph& graph() const;
    int32_t num_layers() const;
    int32_t num_windows() const;
    int32_t num_shapes() const;
    const std::vector<Window>& windows() const;

    // fired: detector ids, any order; a detector listed twice cancels out.
    uint64_t decode(const int32_t* fired, std::size_t count);
    uint64_t decode(const std::vector<int32_t>& fired) {
        return decode(fired.data(), fired.size());
    }
    // Committed correction as global edge ids, checked against the syndrome.
    DecodeResult decode_detailed(const std::vector<int32_t>& fired);

    void decode_batch_bit_packed(const uint8_t* dets, std::size_t shots,
                                 std::size_t det_stride, uint8_t* obs_out,
                                 std::size_t obs_stride);

    // Processing time of every window of every shot, in nanoseconds,
    // row-major (shots x windows): gathering defects, decoding, committing.
    std::vector<double> window_latency_ns(const uint8_t* dets, std::size_t shots,
                                          std::size_t det_stride);

private:
    // fired must be sorted, without duplicates.
    uint64_t run(const int32_t* fired, std::size_t count,
                 std::vector<int32_t>* correction, double* window_ns);

    std::unique_ptr<detail::WindowPlan> plan_;
    std::vector<std::unique_ptr<UnionFindDecoder>> decoders_;  // one per shape

    // Per-shot scratch, allocated once.
    std::vector<uint8_t> residual_;     // syndrome still to explain, per detector
    std::vector<uint32_t> mark_;
    uint32_t mark_epoch_ = 0;
    std::vector<int32_t> sorted_;       // canonical form of the input
    std::vector<int32_t> pending_;      // detectors flipped by earlier commits
    std::vector<int32_t> touched_;      // residual entries to reset
    std::vector<int32_t> local_;
    std::vector<int32_t> local_correction_;
    std::vector<int32_t> flips_;
};

}  // namespace backlog
