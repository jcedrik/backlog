// Window decoders: the weighted Union-Find decoder applied to slices of time.
//
// Detectors are grouped in time layers, one per round of syndrome extraction.
// A real-time decoder cannot wait for the end of the experiment, so both
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
// ParallelWindowDecoder (Skoric et al. 2023, Tan et al. 2023):
//
//   ... seam |  buffer | core: commit C | buffer |  seam | buffer | core ...
//            open      <-- core window, both sides open --> open
//
//   1. Core windows (commit C layers, a buffer of B on each side) do not
//      depend on each other and are decoded in parallel.
//   2. Seams (the G layers between two core commit regions) are then decoded
//      in parallel too, with both sides closed: the cores already committed
//      everything around them, and their flips are part of the seam syndrome.
//
// In both cases every detector is explained by exactly one window's committed
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
    enum Kind : uint8_t { Sliding = 0, Core = 1, Seam = 2 };
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
class ThreadPool;
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

class ParallelWindowDecoder {
public:
    // Core windows commit commit_layers each, with buffer_layers of buffer on
    // both sides; seams of seam_layers sit between consecutive core commit
    // regions. seam_layers must cover the longest edge in time (one layer for
    // Stim memory circuits). threads >= 1, including the calling thread.
    ParallelWindowDecoder(DecodingGraph graph, std::vector<int32_t> layer,
                          int32_t commit_layers, int32_t buffer_layers,
                          int32_t seam_layers, int32_t threads);
    ~ParallelWindowDecoder();
    ParallelWindowDecoder(const ParallelWindowDecoder&) = delete;
    ParallelWindowDecoder& operator=(const ParallelWindowDecoder&) = delete;

    const DecodingGraph& graph() const;
    int32_t num_layers() const;
    int32_t num_threads() const;
    int32_t num_windows() const;
    int32_t num_shapes() const;
    // In time order: core, seam, core, seam, ..., possibly ending with a seam.
    const std::vector<Window>& windows() const;

    uint64_t decode(const int32_t* fired, std::size_t count);
    uint64_t decode(const std::vector<int32_t>& fired) {
        return decode(fired.data(), fired.size());
    }
    DecodeResult decode_detailed(const std::vector<int32_t>& fired);

    void decode_batch_bit_packed(const uint8_t* dets, std::size_t shots,
                                 std::size_t det_stride, uint8_t* obs_out,
                                 std::size_t obs_stride);

    // Work time of every window of every shot, in nanoseconds, row-major
    // (shots x windows, windows in time order), measured by the thread that
    // decoded it.
    std::vector<double> window_latency_ns(const uint8_t* dets, std::size_t shots,
                                          std::size_t det_stride);
    // Wall-clock time of each shot with all threads, in nanoseconds.
    std::vector<double> shot_latency_ns(const uint8_t* dets, std::size_t shots,
                                        std::size_t det_stride);

private:
    struct Scratch;
    struct Result;
    uint64_t run(const int32_t* fired, std::size_t count,
                 std::vector<int32_t>* correction, double* window_ns);

    std::unique_ptr<detail::WindowPlan> plan_;
    std::unique_ptr<detail::ThreadPool> pool_;
    std::vector<std::vector<std::unique_ptr<UnionFindDecoder>>> decoders_;  // [thread][shape]
    std::vector<int32_t> cores_;   // window indices of the core windows
    std::vector<int32_t> seams_;   // window indices of the seams
    std::vector<Scratch> scratch_;  // one per thread
    std::vector<Result> results_;   // one per window
    std::vector<std::vector<int32_t>> seam_flips_;  // flips received, per seam
    std::vector<int32_t> sorted_;
};

}  // namespace backlog
