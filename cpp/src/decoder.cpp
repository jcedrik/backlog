#include "backlog/decoder.hpp"

#include <algorithm>
#include <chrono>
#include <stdexcept>
#include <utility>

#if defined(_MSC_VER)
#include <intrin.h>
#endif

namespace backlog {

namespace {

inline int ctz64(uint64_t x) {
#if defined(_MSC_VER)
    unsigned long i;
    _BitScanForward64(&i, x);
    return static_cast<int>(i);
#else
    return __builtin_ctzll(x);
#endif
}

}  // namespace

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

bool correction_explains(const DecodingGraph& graph,
                         const std::vector<int32_t>& fired,
                         const std::vector<int32_t>& correction) {
    std::vector<uint8_t> x(static_cast<std::size_t>(graph.num_nodes()), 0);
    for (int32_t d : fired) {
        if (d < 0 || d >= graph.num_detectors()) return false;
        x[d] ^= 1;
    }
    for (int32_t e : correction) {
        const Edge& ed = graph.edge(e);
        x[ed.a] ^= 1;
        x[ed.b] ^= 1;
    }
    for (int32_t v = 0; v < graph.num_detectors(); ++v) {
        if (x[v]) return false;
    }
    return true;
}

void unpack_shot(const uint8_t* bytes, std::size_t num_bytes,
                 int32_t num_detectors, std::vector<int32_t>& out) {
    out.clear();
    for (std::size_t j = 0; j < num_bytes; ++j) {
        unsigned byte = bytes[j];
        while (byte) {
            const int32_t d = static_cast<int32_t>(8 * j) + ctz64(byte);
            if (d < num_detectors) out.push_back(d);
            byte &= byte - 1;
        }
    }
}

// ---------------------------------------------------------------------------
// Bucket queue
// ---------------------------------------------------------------------------

void UnionFindDecoder::BucketQueue::reset(int32_t max_weight) {
    std::size_t span = 64;
    while (span < static_cast<std::size_t>(max_weight) + 1) span <<= 1;
    head_.assign(span, -1);
    bits_.assign(span / 64, 0);
    pool_.clear();
    mask_ = span - 1;
    count_ = 0;
    now_ = 0;
}

void UnionFindDecoder::BucketQueue::clear() {
    if (count_ != 0) {
        for (std::size_t w = 0; w < bits_.size(); ++w) {
            uint64_t word = bits_[w];
            while (word) {
                head_[w * 64 + static_cast<std::size_t>(ctz64(word))] = -1;
                word &= word - 1;
            }
            bits_[w] = 0;
        }
    }
    pool_.clear();
    count_ = 0;
    now_ = 0;
}

inline void UnionFindDecoder::BucketQueue::push(int64_t round, int32_t id,
                                                uint32_t version) {
    const std::size_t idx = static_cast<std::size_t>(round) & mask_;
    pool_.push_back(Link{id, version, head_[idx]});
    head_[idx] = static_cast<int32_t>(pool_.size() - 1);
    bits_[idx >> 6] |= 1ull << (idx & 63);
    ++count_;
}

int64_t UnionFindDecoder::BucketQueue::pop_round(std::vector<Event>& out) {
    // First occupied bucket at or after now_, wrapping around the ring.
    const std::size_t start = static_cast<std::size_t>(now_) & mask_;
    const std::size_t words = bits_.size();
    std::size_t idx = 0;
    for (std::size_t k = 0; k <= words; ++k) {
        const std::size_t w = ((start >> 6) + k) % words;
        uint64_t word = bits_[w];
        if (k == 0) word &= ~0ull << (start & 63);        // at or after start
        if (k == words) word &= ~(~0ull << (start & 63));  // wrapped: before start
        if (word) {
            idx = w * 64 + static_cast<std::size_t>(ctz64(word));
            break;
        }
    }
    now_ += static_cast<int64_t>((idx - start) & mask_);
    out.clear();
    for (int32_t i = head_[idx]; i >= 0; i = pool_[i].next) {
        out.push_back(Event{pool_[i].id, pool_[i].version});
    }
    head_[idx] = -1;
    bits_[idx >> 6] &= ~(1ull << (idx & 63));
    count_ -= out.size();
    return now_;
}

// ---------------------------------------------------------------------------
// UnionFindDecoder: setup and lazy state
// ---------------------------------------------------------------------------

UnionFindDecoder::UnionFindDecoder(DecodingGraph graph)
    : g_(std::move(graph)), boundary_(g_.boundary()) {
    const int32_t V = g_.num_nodes();
    const int32_t E = g_.num_edges();
    topo_.resize(static_cast<std::size_t>(E));
    edge_obs_.resize(static_cast<std::size_t>(E));
    int32_t max_weight = 2;
    for (int32_t e = 0; e < E; ++e) {
        topo_[e] = EdgeTopo{g_.edge(e).a, g_.edge(e).b, g_.weight(e)};
        edge_obs_[e] = g_.edge(e).obs;
        max_weight = std::max(max_weight, g_.weight(e));
    }
    if (max_weight > (1 << 22)) {
        throw std::invalid_argument("weights above 2^22 are not supported");
    }
    // Adjacency sorted by (weight, edge id): a node's edges toward untouched
    // nodes all grow at the same rate, so they fill in this order.
    sadj_offset_.assign(static_cast<std::size_t>(V) + 1, 0);
    for (int32_t v = 0; v < V; ++v) {
        sadj_offset_[v + 1] = sadj_offset_[v] +
                              static_cast<int32_t>(g_.adj_end(v) - g_.adj_begin(v));
    }
    sadj_.resize(static_cast<std::size_t>(sadj_offset_[V]));
    for (int32_t v = 0; v < V; ++v) {
        AdjEntry* out = sadj_.data() + sadj_offset_[v];
        AdjEntry* o = out;
        for (const int32_t* it = g_.adj_begin(v); it != g_.adj_end(v); ++it, ++o) {
            *o = AdjEntry{*it, topo_[*it].a == v ? topo_[*it].b : topo_[*it].a};
        }
        std::sort(out, o, [this](const AdjEntry& x, const AdjEntry& y) {
            const int32_t wx = topo_[x.edge].weight, wy = topo_[y.edge].weight;
            return wx != wy ? wx < wy : x.edge < y.edge;
        });
    }
    nodes_.assign(static_cast<std::size_t>(V), NodeState{});
    edges_.assign(static_cast<std::size_t>(E), EdgeState{});
    bnodes_.assign(static_cast<std::size_t>(V), {});
    sedges_.assign(static_cast<std::size_t>(V), {});
    fadj_.assign(static_cast<std::size_t>(V), {});
    queue_.reset(max_weight);
}

void UnionFindDecoder::next_epoch() {
    if (++epoch_ == 0) {  // wrapped after 2^32 decodes: start over cleanly
        for (NodeState& n : nodes_) n.epoch = 0;
        for (EdgeState& s : edges_) s.epoch = 0;
        epoch_ = 1;
    }
}

uint32_t UnionFindDecoder::next_seen_mark() {
    if (++seen_mark_ == 0) {
        for (NodeState& n : nodes_) n.seen = 0;
        seen_mark_ = 1;
    }
    return seen_mark_;
}

uint32_t UnionFindDecoder::next_node_mark() {
    if (++node_mark_ == 0) {
        for (NodeState& n : nodes_) n.nmark = 0;
        node_mark_ = 1;
    }
    return node_mark_;
}

inline UnionFindDecoder::NodeState& UnionFindDecoder::node(int32_t v) {
    NodeState& n = nodes_[v];
    if (n.epoch != epoch_) {
        n.epoch = epoch_;
        n.pot = 0;
        n.r0 = 0;
        n.t0 = 0;
        n.parent = v;
        n.fparent = v;
        n.fdeg = 0;
        n.next = 0;
        n.rank = 0;
        n.parity = 0;
        n.at_boundary = (v == boundary_) ? 1 : 0;
        n.growing = 0;
        n.incorporated = 0;
        n.defect = 0;
        bnodes_[v].clear();
        sedges_[v].clear();
        fadj_[v].clear();
    }
    return n;
}

inline UnionFindDecoder::EdgeState& UnionFindDecoder::edge_state(int32_t e) {
    EdgeState& s = edges_[e];
    if (s.epoch != epoch_) {
        s.epoch = epoch_;
        s.full = 0;
        s.removed = 0;
        s.shared = 0;
    }
    return s;
}

// Union-find with potentials: pot is the offset to the parent, and after
// compression it is the offset to the root.
inline int32_t UnionFindDecoder::find(int32_t v) {
    const int32_t p = node(v).parent;
    if (p == v) return v;                       // root
    const int32_t gp = nodes_[p].parent;
    if (gp == p) return p;                      // one hop: pot is already to the root
    // Longer path: total offset first, then point every node at the root.
    int64_t total = nodes_[v].pot + nodes_[p].pot;
    int32_t r = gp;
    while (nodes_[r].parent != r) {
        total += nodes_[r].pot;
        r = nodes_[r].parent;
    }
    for (int32_t x = v; x != r;) {
        const int32_t next = nodes_[x].parent;
        const int64_t own = nodes_[x].pot;
        nodes_[x].parent = r;
        nodes_[x].pot = total;
        total -= own;
        x = next;
    }
    return r;
}

inline int32_t UnionFindDecoder::ffind(int32_t v) {
    while (nodes_[v].fparent != v) {
        nodes_[v].fparent = nodes_[nodes_[v].fparent].fparent;
        v = nodes_[v].fparent;
    }
    return v;
}

// Rounds the node's cluster has spent growing since the node joined it.
inline int64_t UnionFindDecoder::radius(int32_t v, int64_t now) {
    const int32_t r = find(v);
    return cluster_radius(r, now) - (v == r ? 0 : nodes_[v].pot);
}

// A node joins the bookkeeping the first time a cluster reaches it. It starts
// as a singleton cluster of radius 0, with its parity as its growth state.
inline void UnionFindDecoder::incorporate(int32_t v, int64_t now) {
    NodeState& n = node(v);
    n.incorporated = 1;
    n.r0 = 0;
    n.t0 = now;
    n.growing = is_odd(v) ? 1 : 0;
    n.next = 0;
    bnodes_[v].push_back(v);
}

// Merge two cluster roots at round `now`, keeping every node's radius.
inline void UnionFindDecoder::union_roots(int32_t ra, int32_t rb, int64_t now) {
    int64_t Ra = cluster_radius(ra, now);
    int64_t Rb = cluster_radius(rb, now);
    if (nodes_[ra].rank < nodes_[rb].rank) {
        std::swap(ra, rb);
        std::swap(Ra, Rb);
    }
    NodeState& a = nodes_[ra];
    NodeState& b = nodes_[rb];
    b.parent = ra;
    b.pot = Ra - Rb;
    if (a.rank == b.rank) ++a.rank;
    a.parity ^= b.parity;
    a.at_boundary |= b.at_boundary;
    a.r0 = Ra;  // growth state is settled once the round's merges are done
    a.t0 = now;
}

// Edges of a newly incorporated node toward nodes already in another cluster
// grow from both sides: they become shared edges with their own events.
void UnionFindDecoder::register_shared_edges(int32_t v, int64_t now) {
    const int32_t rv = find(v);
    const AdjEntry* end = sadj_.data() + sadj_offset_[v + 1];
    for (const AdjEntry* it = sadj_.data() + sadj_offset_[v]; it != end; ++it) {
        const int32_t w = it->other;
        if (w == boundary_ || !is_incorporated(w)) continue;  // v's node event covers it
        const int32_t e = it->edge;
        if (is_full(e)) continue;
        const int32_t rw = find(w);
        if (rw == rv) continue;  // internal
        EdgeState& s = edge_state(e);
        if (s.shared) continue;
        s.shared = 1;
        sedges_[v].push_back(e);
        sedges_[rw].push_back(e);
        reschedule_shared_edge(e, now);
    }
}

// Node event: the round in which the node's lightest edge toward an untouched
// node fills. Returns false once the node has no such edge left.
bool UnionFindDecoder::reschedule_node(int32_t v, int64_t now) {
    ++stats_.edge_reschedules;
    NodeState& n = nodes_[v];
    const AdjEntry* adj = sadj_.data() + sadj_offset_[v];
    const int32_t deg = sadj_offset_[v + 1] - sadj_offset_[v];
    while (n.next < deg) {
        const AdjEntry& a = adj[n.next];
        if ((a.other != boundary_ && is_incorporated(a.other)) || is_full(a.edge)) {
            ++n.next;  // full, shared or internal: not this node's business
            continue;
        }
        break;
    }
    ++n.nver;
    if (n.next == deg) return false;
    const int32_t r = find(v);
    if (!nodes_[r].growing) return true;
    const int32_t e = adj[n.next].edge;
    const int64_t rho = cluster_radius(r, now) - (v == r ? 0 : nodes_[v].pot);
    const int64_t wait = std::max<int64_t>(topo_[e].weight - rho, 0);
    queue_.push(now + wait, ~v, nodes_[v].nver);
    return true;
}

// Edge event for a shared edge: growth is the sum of both endpoints' radii,
// rate is the number of growing clusters at its ends.
void UnionFindDecoder::reschedule_shared_edge(int32_t e, int64_t now) {
    ++stats_.edge_reschedules;
    EdgeState& s = edge_state(e);
    ++s.version;
    if (s.full) return;
    const int32_t a = topo_[e].a, b = topo_[e].b;
    const int32_t ra = find(a), rb = find(b);
    if (ra == rb) return;
    const int32_t rate = nodes_[ra].growing + nodes_[rb].growing;
    if (rate == 0) return;
    const int64_t growth = cluster_radius(ra, now) - (a == ra ? 0 : nodes_[a].pot) +
                           cluster_radius(rb, now) - (b == rb ? 0 : nodes_[b].pot);
    const int64_t rem = static_cast<int64_t>(topo_[e].weight) - growth;
    const int64_t wait = rem <= 0 ? 0 : (rate == 1 ? rem : (rem + 1) >> 1);
    queue_.push(now + wait, e, s.version);
}

// A cluster whose growth state flipped: reschedule its boundary nodes and its
// shared edges, dropping exhausted nodes and dead edges on the way.
void UnionFindDecoder::reschedule_cluster(int32_t participant, int64_t now) {
    std::vector<int32_t>& bl = bnodes_[participant];
    std::size_t keep = 0;
    for (std::size_t i = 0; i < bl.size(); ++i) {
        const int32_t v = bl[i];
        if (nodes_[v].nmark == node_mark_) {
            bl[keep++] = v;
            continue;
        }
        nodes_[v].nmark = node_mark_;
        if (reschedule_node(v, now)) bl[keep++] = v;
    }
    bl.resize(keep);

    std::vector<int32_t>& sl = sedges_[participant];
    keep = 0;
    for (std::size_t i = 0; i < sl.size(); ++i) {
        const int32_t e = sl[i];
        if (is_full(e)) continue;
        if (find(topo_[e].a) == find(topo_[e].b)) {
            ++edges_[e].version;
            continue;
        }
        sl[keep++] = e;
        reschedule_shared_edge(e, now);
    }
    sl.resize(keep);
}

// A node event fires: every edge toward an untouched node whose weight the
// node's radius has reached fills now (they fill in weight order).
void UnionFindDecoder::pop_node_event(int32_t v, int64_t now) {
    NodeState& n = nodes_[v];
    const int64_t rho = radius(v, now);
    const AdjEntry* adj = sadj_.data() + sadj_offset_[v];
    const int32_t deg = sadj_offset_[v + 1] - sadj_offset_[v];
    while (n.next < deg) {
        const AdjEntry& a = adj[n.next];
        const int32_t e = a.edge;
        if ((a.other != boundary_ && is_incorporated(a.other)) || is_full(e)) {
            ++n.next;
            continue;
        }
        if (topo_[e].weight > rho) break;
        edge_state(e).full = 1;
        newly_full_.push_back(e);
        ++n.next;
    }
    popped_nodes_.push_back(v);
}

// ---------------------------------------------------------------------------
// UnionFindDecoder: decoding
// ---------------------------------------------------------------------------

uint64_t UnionFindDecoder::decode(const int32_t* fired, std::size_t count) {
    return run(fired, count, nullptr);
}

DecodeResult UnionFindDecoder::decode_detailed(const std::vector<int32_t>& fired) {
    DecodeResult r;
    r.observables = run(fired.data(), fired.size(), &r.correction);
    r.syndrome_ok = correction_explains(g_, fired, r.correction);
    return r;
}

uint64_t UnionFindDecoder::run(const int32_t* fired, std::size_t count,
                               std::vector<int32_t>* correction) {
    next_epoch();
    steps_ = 0;
    active_.clear();
    full_edges_.clear();

    const int32_t n = g_.num_detectors();
    for (std::size_t i = 0; i < count; ++i) {
        const int32_t d = fired[i];
        if (d < 0 || d >= n) throw std::out_of_range("detector id out of range");
        NodeState& s = node(d);
        s.defect ^= 1;
        s.parity ^= 1;
    }
    // Every node with an unpaired defect starts as a growing singleton.
    const uint32_t mark = next_seen_mark();
    for (std::size_t i = 0; i < count; ++i) {
        const int32_t d = fired[i];
        NodeState& s = nodes_[d];
        if (s.defect && s.seen != mark) {
            s.seen = mark;
            active_.push_back(d);
        }
    }
    for (int32_t d : active_) incorporate(d, 0);
    ++stats_.decodes;
    stats_.defects += active_.size();

    grow();
    return peel(correction);
}

void UnionFindDecoder::grow() {
    queue_.clear();
    for (int32_t d : active_) register_shared_edges(d, 0);
    next_node_mark();
    for (int32_t d : active_) {
        nodes_[d].nmark = node_mark_;
        if (!reschedule_node(d, 0)) bnodes_[d].clear();
    }

    while (!queue_.empty()) {
        // 1. Events of the earliest pending round, judged on the state at the
        //    start of that round, like the reference decoder.
        const int64_t now = queue_.pop_round(batch_);
        stats_.events_popped += batch_.size();
        newly_full_.clear();
        popped_nodes_.clear();
        for (const Event& ev : batch_) {
            if (ev.id < 0) {
                const int32_t v = ~ev.id;
                if (nodes_[v].nver == ev.version) pop_node_event(v, now);
                continue;
            }
            const int32_t e = ev.id;
            if (edges_[e].version != ev.version || is_full(e)) continue;
            if (find(topo_[e].a) == find(topo_[e].b)) continue;
            edges_[e].full = 1;
            newly_full_.push_back(e);
        }
        if (newly_full_.empty()) {
            // Early node events (their target edge became shared): retarget.
            next_node_mark();
            for (int32_t v : popped_nodes_) reschedule_node(v, now);
            continue;
        }
        ++steps_;
        ++stats_.growth_steps;
        stats_.full_edges += newly_full_.size();

        // 2. Reach untouched nodes, then record the clusters taking part in
        //    this round's merges with their growth state before the merges.
        participants_.clear();
        new_nodes_.clear();
        const uint32_t seen = next_seen_mark();
        for (int32_t e : newly_full_) {
            for (int32_t v : {topo_[e].a, topo_[e].b}) {
                if (v != boundary_ && !is_incorporated(v)) {
                    incorporate(v, now);
                    new_nodes_.push_back(v);
                }
                const int32_t r = find(v);
                if (nodes_[r].seen != seen) {
                    nodes_[r].seen = seen;
                    participants_.emplace_back(r, nodes_[r].growing);
                }
            }
        }
        for (int32_t e : newly_full_) {
            full_edges_.push_back(e);
            const int32_t ra = find(topo_[e].a), rb = find(topo_[e].b);
            if (ra != rb) union_roots(ra, rb, now);
        }
        for (const auto& pr : participants_) {
            const int32_t root = find(pr.first);
            nodes_[root].growing = is_odd(root) ? 1 : 0;
        }

        // 3. Reschedule what this round changed: shared edges of new nodes,
        //    clusters whose growth state flipped, and nodes that just fired.
        for (int32_t v : new_nodes_) register_shared_edges(v, now);
        next_node_mark();
        for (const auto& pr : participants_) {
            if (pr.second != nodes_[find(pr.first)].growing) {
                reschedule_cluster(pr.first, now);
            }
        }
        for (int32_t v : popped_nodes_) {
            if (nodes_[v].nmark != node_mark_) {
                nodes_[v].nmark = node_mark_;
                reschedule_node(v, now);
            }
        }

        // 4. Hand each absorbed cluster's lists to its new root. A cluster
        //    touching the boundary is even forever: its lists are dead.
        for (const auto& pr : participants_) {
            const int32_t r = pr.first;
            const int32_t root = find(r);
            if (nodes_[root].at_boundary) {
                bnodes_[r].clear();
                sedges_[r].clear();
                bnodes_[root].clear();
                sedges_[root].clear();
                continue;
            }
            if (root == r) continue;
            for (auto* lists : {&bnodes_, &sedges_}) {
                std::vector<int32_t>& from = (*lists)[r];
                std::vector<int32_t>& to = (*lists)[root];
                if (to.size() < from.size()) to.swap(from);  // append the smaller
                to.insert(to.end(), from.begin(), from.end());
                from.clear();
            }
        }
    }
}

uint64_t UnionFindDecoder::peel(std::vector<int32_t>* correction) {
    // Spanning forest of the grown region, likeliest edges first (Kruskal).
    const std::vector<EdgeTopo>& topo = topo_;
    std::sort(full_edges_.begin(), full_edges_.end(),
              [&topo](int32_t x, int32_t y) {
                  return topo[x].weight != topo[y].weight
                             ? topo[x].weight < topo[y].weight
                             : x < y;
              });
    forest_nodes_.clear();
    for (int32_t e : full_edges_) {
        const int32_t a = topo_[e].a, b = topo_[e].b;
        node(a);
        node(b);
        const int32_t ra = ffind(a), rb = ffind(b);
        if (ra == rb) continue;
        nodes_[ra].fparent = rb;
        fadj_[a].push_back(e);
        fadj_[b].push_back(e);
        if (nodes_[a].fdeg++ == 0) forest_nodes_.push_back(a);
        if (nodes_[b].fdeg++ == 0) forest_nodes_.push_back(b);
    }

    // Peel from the leaves, taken in order of first appearance in the forest;
    // the boundary is a sink and is never peeled.
    leaves_.clear();
    for (int32_t v : forest_nodes_) {
        if (v != boundary_ && nodes_[v].fdeg == 1) leaves_.push_back(v);
    }
    uint64_t obs = 0;
    for (std::size_t head = 0; head < leaves_.size(); ++head) {
        const int32_t u = leaves_[head];
        if (nodes_[u].fdeg != 1) continue;
        int32_t e = -1;
        for (int32_t x : fadj_[u]) {
            if (!edges_[x].removed) {
                e = x;
                break;
            }
        }
        if (e < 0) continue;
        const int32_t v = (topo_[e].a == u) ? topo_[e].b : topo_[e].a;
        edges_[e].removed = 1;
        --nodes_[u].fdeg;
        --nodes_[v].fdeg;
        if (nodes_[u].defect) {
            obs ^= edge_obs_[e];
            nodes_[u].defect = 0;
            if (v != boundary_) nodes_[v].defect ^= 1;
            if (correction) correction->push_back(e);
        }
        if (v != boundary_ && nodes_[v].fdeg == 1) leaves_.push_back(v);
    }
    return obs;
}

void UnionFindDecoder::decode_batch_bit_packed(const uint8_t* dets,
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
        unpack_shot(dets + s * det_stride, det_bytes, n, fired_);
        const uint64_t o = decode(fired_.data(), fired_.size());
        uint8_t* dst = obs_out + s * obs_stride;
        for (std::size_t j = 0; j < obs_bytes; ++j) {
            dst[j] = static_cast<uint8_t>(o >> (8 * j));
        }
    }
}

std::vector<double> UnionFindDecoder::latency_ns(const uint8_t* dets,
                                                 std::size_t shots,
                                                 std::size_t det_stride) {
    const int32_t n = g_.num_detectors();
    const std::size_t det_bytes = (static_cast<std::size_t>(n) + 7) / 8;
    if (det_stride < det_bytes) throw std::invalid_argument("detector rows too short");
    std::vector<double> out(shots);
    volatile uint64_t sink = 0;
    for (std::size_t s = 0; s < shots; ++s) {
        unpack_shot(dets + s * det_stride, det_bytes, n, fired_);
        const auto t0 = std::chrono::steady_clock::now();
        sink = sink ^ decode(fired_.data(), fired_.size());
        const auto t1 = std::chrono::steady_clock::now();
        out[s] = std::chrono::duration<double, std::nano>(t1 - t0).count();
    }
    (void)sink;
    return out;
}

}  // namespace backlog
