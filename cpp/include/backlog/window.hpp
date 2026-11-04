// Sliding-window (streaming) decoding with the weighted Union-Find decoder.
//
// Detectors are grouped in time layers, one per round of syndrome extraction.
// A real-time decoder cannot wait for the end of the experiment: it decodes a
// window of layers, keeps the oldest part of the answer, and slides.
//
//   layers   [ s ................ s+C ................ s+C+B )
//            |   commit region     |    buffer region      |
//
// 1. The window's detectors are decoded together. Edges into already
//    committed layers are dropped. Edges into layers not seen yet become edges
//    to the boundary, so a defect near the top of the window can still be
//    explained by an error whose other half has not been measured yet.
// 2. Only correction edges touching the commit region are kept. Their logical
//    flips are applied, and their endpoints beyond the commit region have
//    their syndrome flipped, so the next window starts from the right state.
// 3. The window slides by C layers. The last window reaches the final layer
//    and commits everything.
//
// Every commit-region detector is explained by the window's correction, and
// whatever the committed edges change further on is carried forward, so the
// union of all committed edges always reproduces the full syndrome. With
// C + B covering every layer there is a single window and the result is
// exactly the whole-shot decoder's.
//
// Windows with the same local structure (the periodic bulk of a memory
// experiment) share one decoder, so decoder memory does not grow with the
// number of rounds.
#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <vector>

#include "backlog/decoder.hpp"
#include "backlog/graph.hpp"

namespace backlog {

class SlidingWindowDecoder {
public:
    // layer[v] is the time layer of detector v. Layers must be non-decreasing
    // with the detector id, which holds for Stim circuits (detectors are
    // declared in time order). commit_layers >= 1, buffer_layers >= 0.
    SlidingWindowDecoder(DecodingGraph graph, std::vector<int32_t> layer,
                         int32_t commit_layers, int32_t buffer_layers);
    ~SlidingWindowDecoder();

    struct Window {
        int32_t first_layer = 0;     // first decoded layer
        int32_t commit_end = 0;      // layers [first_layer, commit_end) are committed
        int32_t end_layer = 0;       // layers [first_layer, end_layer) are decoded
        int32_t first_detector = 0;  // global id of the window's local detector 0
        int32_t num_detectors = 0;
        int32_t shape = -1;          // shared decoder, -1 if the window is empty
        std::vector<int32_t> edge_map;  // local edge -> global edge
    };

    const DecodingGraph& graph() const { return g_; }
    int32_t num_layers() const { return num_layers_; }
    int32_t commit_layers() const { return commit_; }
    int32_t buffer_layers() const { return buffer_; }
    int32_t num_windows() const { return static_cast<int32_t>(windows_.size()); }
    int32_t num_shapes() const { return static_cast<int32_t>(shapes_.size()); }
    const std::vector<Window>& windows() const { return windows_; }

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
    // row-major (shots x windows). Each value covers gathering the window's
    // defects, decoding it and committing.
    std::vector<double> window_latency_ns(const uint8_t* dets, std::size_t shots,
                                          std::size_t det_stride);

private:
    struct Shape;

    // fired must be sorted, without duplicates.
    uint64_t run(const int32_t* fired, std::size_t count,
                 std::vector<int32_t>* correction, double* window_ns);
    void build_windows();
    // Validates the input and leaves it sorted, duplicates cancelled, in sorted_.
    void canonicalize(const int32_t* fired, std::size_t count);
    uint32_t next_mark();

    DecodingGraph g_;
    std::vector<int32_t> layer_;
    int32_t commit_;
    int32_t buffer_;
    int32_t num_layers_ = 0;
    std::vector<int32_t> layer_begin_;  // first detector id of each layer, size L + 1
    std::vector<Window> windows_;
    std::vector<std::unique_ptr<Shape>> shapes_;

    // Per-shot scratch, allocated once.
    std::vector<uint8_t> residual_;     // syndrome still to explain, per detector
    std::vector<uint32_t> mark_;
    uint32_t mark_epoch_ = 0;
    std::vector<int32_t> sorted_;       // canonical form of the input
    std::vector<int32_t> pending_;      // detectors flipped by earlier commits
    std::vector<int32_t> touched_;      // residual entries to reset
    std::vector<int32_t> local_;        // defects of the current window, local ids
    std::vector<int32_t> local_correction_;
};

}  // namespace backlog
