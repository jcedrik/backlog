// Union-Find decoders.
//
// UnionFindDecoder is the fast one. It never simulates growth round by round.
// Each cluster has a radius that increases by one per round while the cluster
// is odd; each node's radius (rounds its cluster spent growing since the node
// joined) is derived from it through a union-find with potentials, so merging
// two clusters is O(1). An edge's growth is the sum of its endpoints' radii.
// Edges toward untouched nodes all grow at the same rate from one side, so a
// node only needs one pending event: its lightest such edge (adjacency is
// pre-sorted by weight). Only edges between two different clusters get their
// own events. When a cluster changes parity, only its boundary nodes and those
// shared edges are rescheduled. A bucket queue jumps from one fill round to
// the next. This is exactly equivalent to the unit-round algorithm, so fine
// weight resolution costs nothing in iteration count. Work buffers are
// allocated once; decoding a shot allocates nothing and only touches the part
// of the graph its clusters reach.
//
// ReferenceDecoder is the specification: synchronous unit rounds over the whole
// graph, written to be obviously correct rather than fast. The test suite
// checks that both produce the same observable prediction and the same
// correction, edge for edge.
//
// Both decoders share these semantics:
//   1. Growth: every odd cluster (odd number of defects, not touching the
//      boundary) grows each edge on its boundary by one unit per round. An edge
//      between two odd clusters grows by two. An edge is fully grown when its
//      growth reaches its weight; its endpoints' clusters then merge.
//   2. Peeling: a spanning forest of the fully grown edges is built in
//      (weight, edge id) order, so likelier edges are preferred, then peeled
//      from the leaves toward the boundary (Delfosse-Zemor).
#pragma once

#include <cstddef>
#include <cstdint>
#include <utility>
#include <vector>

#include "backlog/graph.hpp"

namespace backlog {

struct DecodeResult {
    uint64_t observables = 0;          // predicted logical flips
    std::vector<int32_t> correction;   // edge ids, in peeling order
    bool syndrome_ok = false;          // correction reproduces the syndrome
};

// Cumulative work counters, to see where decoding time goes.
struct DecoderStats {
    uint64_t decodes = 0;
    uint64_t defects = 0;            // unpaired defects at the start
    uint64_t growth_steps = 0;       // rounds in which at least one edge filled
    uint64_t full_edges = 0;         // edges fully grown
    uint64_t edge_reschedules = 0;   // growth-rate updates of single edges
    uint64_t events_popped = 0;      // queue pops, stale ones included
};

// True iff flipping every edge in `correction` produces exactly `fired`.
bool correction_explains(const DecodingGraph& graph,
                         const std::vector<int32_t>& fired,
                         const std::vector<int32_t>& correction);

// Stim bit-packed shot (little-endian bits) -> list of fired detector ids.
void unpack_shot(const uint8_t* bytes, std::size_t num_bytes,
                 int32_t num_detectors, std::vector<int32_t>& out);

class UnionFindDecoder {
public:
    explicit UnionFindDecoder(DecodingGraph graph);

    const DecodingGraph& graph() const { return g_; }

    // fired: detector ids, any order; a detector listed twice cancels out.
    uint64_t decode(const int32_t* fired, std::size_t count);
    uint64_t decode(const std::vector<int32_t>& fired) {
        return decode(fired.data(), fired.size());
    }
    DecodeResult decode_detailed(const std::vector<int32_t>& fired);

    // Batch decoding on Stim's bit-packed layout. det_stride and obs_stride
    // are row sizes in bytes.
    void decode_batch_bit_packed(const uint8_t* dets, std::size_t shots,
                                 std::size_t det_stride, uint8_t* obs_out,
                                 std::size_t obs_stride);

    // Per-shot latency of the decode call, in nanoseconds.
    std::vector<double> latency_ns(const uint8_t* dets, std::size_t shots,
                                   std::size_t det_stride);

    // Growth steps (fill events) used by the last decode.
    int32_t last_growth_steps() const { return steps_; }
    const DecoderStats& stats() const { return stats_; }
    void reset_stats() { stats_ = DecoderStats{}; }

private:
    uint64_t run(const int32_t* fired, std::size_t count,
                 std::vector<int32_t>* correction);
    void grow();
    uint64_t peel(std::vector<int32_t>* correction);

    // Hot per-node state, one cache-friendly record per node. Reset lazily:
    // a record whose epoch differs from the decoder's is treated as fresh.
    struct NodeState {
        int64_t pot = 0;   // offset to parent: radius(v) = R(root) - pot path sum
        int64_t r0 = 0;    // root only: cluster radius at round t0
        int64_t t0 = 0;    // root only
        uint32_t epoch = 0;
        int32_t parent = 0;
        int32_t fparent = 0;    // spanning-forest union-find
        int32_t fdeg = 0;       // spanning-forest degree
        int32_t next = 0;       // next candidate in weight-sorted adjacency
        uint32_t nver = 0;      // node-event version, never reset
        uint32_t seen = 0;      // scratch marks
        uint32_t nmark = 0;
        uint8_t rank = 0;
        uint8_t parity = 0;     // root only: defects in the cluster, mod 2
        uint8_t at_boundary = 0;
        uint8_t growing = 0;    // root only: odd, so its radius increases
        uint8_t incorporated = 0;
        uint8_t defect = 0;
    };

    struct EdgeState {
        uint32_t epoch = 0;
        uint32_t version = 0;   // edge-event version, never reset
        uint8_t full = 0;
        uint8_t removed = 0;
        uint8_t shared = 0;     // both ends in clusters: tracked by edge events
    };

    struct EdgeTopo {
        int32_t a;
        int32_t b;
        int32_t weight;
    };

    // id >= 0: edge event for edge id. id < 0: node event for node ~id.
    struct Event {
        int32_t id;
        uint32_t version;
    };

    // Dial's bucket queue. Every event is scheduled at most max_weight rounds
    // ahead, so a ring of buckets indexed by round covers all pending events
    // and a bitmap finds the next non-empty one. Buckets are linked lists in a
    // pool reused across decodes: pushing is O(1) and never allocates once
    // warm. A bucket is exactly one round's batch of events.
    class BucketQueue {
    public:
        void reset(int32_t max_weight);
        void clear();
        bool empty() const { return count_ == 0; }
        void push(int64_t round, int32_t id, uint32_t version);
        // Appends the earliest round's events to `out`; returns that round.
        int64_t pop_round(std::vector<Event>& out);

    private:
        struct Link {
            int32_t id;
            uint32_t version;
            int32_t next;
        };
        std::vector<Link> pool_;
        std::vector<int32_t> head_;
        std::vector<uint64_t> bits_;
        std::size_t mask_ = 0;
        std::size_t count_ = 0;
        int64_t now_ = 0;
    };

    void next_epoch();
    uint32_t next_seen_mark();
    uint32_t next_node_mark();
    NodeState& node(int32_t v);
    EdgeState& edge_state(int32_t e);
    bool is_incorporated(int32_t v) const {
        return nodes_[v].epoch == epoch_ && nodes_[v].incorporated;
    }
    bool is_full(int32_t e) const {
        return edges_[e].epoch == epoch_ && edges_[e].full;
    }
    bool is_odd(int32_t root) const {
        return nodes_[root].parity && !nodes_[root].at_boundary;
    }
    int64_t cluster_radius(int32_t root, int64_t now) const {
        const NodeState& r = nodes_[root];
        return r.r0 + (r.growing ? now - r.t0 : 0);
    }
    int32_t find(int32_t v);
    int32_t ffind(int32_t v);
    int64_t radius(int32_t v, int64_t now);
    void incorporate(int32_t v, int64_t now);
    void union_roots(int32_t ra, int32_t rb, int64_t now);
    void register_shared_edges(int32_t v, int64_t now);
    bool reschedule_node(int32_t v, int64_t now);
    void reschedule_shared_edge(int32_t e, int64_t now);
    void reschedule_cluster(int32_t participant, int64_t now);
    void pop_node_event(int32_t v, int64_t now);

    DecodingGraph g_;
    int32_t boundary_;
    std::vector<EdgeTopo> topo_;
    std::vector<uint64_t> edge_obs_;
    struct AdjEntry {
        int32_t edge;
        int32_t other;   // the edge's other endpoint
    };
    std::vector<int32_t> sadj_offset_;   // adjacency sorted by (weight, id)
    std::vector<AdjEntry> sadj_;

    uint32_t epoch_ = 0;
    uint32_t seen_mark_ = 0;
    uint32_t node_mark_ = 0;
    std::vector<NodeState> nodes_;
    std::vector<EdgeState> edges_;
    std::vector<std::vector<int32_t>> bnodes_;  // per cluster: boundary nodes
    std::vector<std::vector<int32_t>> sedges_;  // per cluster: shared edges
    std::vector<std::vector<int32_t>> fadj_;    // spanning forest

    BucketQueue queue_;
    std::vector<Event> batch_;
    std::vector<std::pair<int32_t, uint8_t>> participants_;  // (root, was odd)
    std::vector<int32_t> new_nodes_;
    std::vector<int32_t> popped_nodes_;
    std::vector<int32_t> path_;

    std::vector<int32_t> fired_;
    std::vector<int32_t> active_;
    std::vector<int32_t> newly_full_;
    std::vector<int32_t> full_edges_;
    std::vector<int32_t> forest_nodes_;
    std::vector<int32_t> leaves_;
    int32_t steps_ = 0;
    DecoderStats stats_;
};

class ReferenceDecoder {
public:
    explicit ReferenceDecoder(DecodingGraph graph);
    const DecodingGraph& graph() const { return g_; }
    DecodeResult decode_detailed(const std::vector<int32_t>& fired) const;

private:
    DecodingGraph g_;
};

}  // namespace backlog
