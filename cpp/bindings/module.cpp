// Python bindings for the backlog decoders (module: backlog._core).
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <memory>
#include <stdexcept>
#include <vector>

#include "backlog/decoder.hpp"
#include "backlog/window.hpp"

namespace py = pybind11;
using backlog::DecodeResult;
using backlog::DecodingGraph;
using backlog::Edge;
using backlog::ReferenceDecoder;
using backlog::ReweightRules;
using backlog::ParallelWindowDecoder;
using backlog::SlidingWindowDecoder;
using backlog::UnionFindDecoder;
using backlog::Weighting;

namespace {

using I32 = py::array_t<int32_t, py::array::c_style | py::array::forcecast>;
using U64 = py::array_t<uint64_t, py::array::c_style | py::array::forcecast>;
using F64 = py::array_t<double, py::array::c_style | py::array::forcecast>;
using U8 = py::array_t<uint8_t, py::array::c_style | py::array::forcecast>;

DecodingGraph make_graph(int32_t n, int32_t m, const I32& a, const I32& b,
                         const U64& obs, const F64& p, bool weighted,
                         int32_t resolution) {
    const py::ssize_t E = a.size();
    if (b.size() != E || obs.size() != E || p.size() != E) {
        throw std::invalid_argument("edge arrays must have the same length");
    }
    std::vector<Edge> edges(static_cast<std::size_t>(E));
    const int32_t* pa = a.data();
    const int32_t* pb = b.data();
    const uint64_t* po = obs.data();
    const double* pp = p.data();
    for (py::ssize_t i = 0; i < E; ++i) {
        edges[i] = Edge{pa[i], pb[i], po[i], pp[i]};
    }
    return DecodingGraph(n, m, std::move(edges),
                         weighted ? Weighting::LogLikelihood : Weighting::Uniform,
                         resolution);
}

// Correlation table (CSR over edges: offset, target, P(target | edge)) ->
// reweighting rules on this graph's weight scale. No table: no rules.
ReweightRules make_rules(const DecodingGraph& g, const py::object& offset,
                         const py::object& target, const py::object& conditional,
                         double alpha) {
    if (offset.is_none() && target.is_none() && conditional.is_none()) return {};
    if (offset.is_none() || target.is_none() || conditional.is_none()) {
        throw std::invalid_argument("corr_offset, corr_target and corr_conditional go together");
    }
    const I32 o = offset.cast<I32>();
    const I32 t = target.cast<I32>();
    const F64 c = conditional.cast<F64>();
    return backlog::make_reweight_rules(
        g, std::vector<int32_t>(o.data(), o.data() + o.size()),
        std::vector<int32_t>(t.data(), t.data() + t.size()),
        std::vector<double>(c.data(), c.data() + c.size()), alpha);
}

py::tuple rules_tuple(const ReweightRules& r) {
    return py::make_tuple(r.offset, r.target, r.weight);
}

py::tuple to_tuple(const DecodeResult& r) {
    return py::make_tuple(r.observables, r.correction, r.syndrome_ok);
}

void check_packed(const U8& dets, int32_t num_detectors) {
    if (dets.ndim() != 2) throw std::invalid_argument("expected a 2D uint8 array");
    const py::ssize_t need = (num_detectors + 7) / 8;
    if (dets.shape(1) < need) {
        throw std::invalid_argument("bit-packed rows are too short for this graph");
    }
}

}  // namespace

PYBIND11_MODULE(_core, mod) {
    mod.doc() = "C++ core of backlog: Union-Find decoders for quantum error correction";

    py::class_<UnionFindDecoder>(mod, "UnionFindDecoder")
        .def(py::init([](int32_t n, int32_t m, const I32& a, const I32& b,
                         const U64& obs, const F64& p, bool weighted,
                         int32_t resolution, const py::object& corr_offset,
                         const py::object& corr_target, const py::object& corr_conditional,
                         double alpha) {
                 DecodingGraph g = make_graph(n, m, a, b, obs, p, weighted, resolution);
                 ReweightRules rules = make_rules(g, corr_offset, corr_target, corr_conditional, alpha);
                 return std::make_unique<UnionFindDecoder>(std::move(g), std::move(rules));
             }),
             py::arg("num_detectors"), py::arg("num_observables"), py::arg("a"),
             py::arg("b"), py::arg("obs"), py::arg("p"), py::arg("weighted") = true,
             py::arg("resolution") = 1024, py::arg("corr_offset") = py::none(),
             py::arg("corr_target") = py::none(), py::arg("corr_conditional") = py::none(),
             py::arg("alpha") = 1.0)
        .def_property_readonly("correlated", &UnionFindDecoder::correlated)
        .def_property_readonly("rules",
                               [](const UnionFindDecoder& d) { return rules_tuple(d.reweight_rules()); })
        .def_property_readonly("last_first_pass", &UnionFindDecoder::last_first_pass)
        .def("weight_for_probability",
             [](const UnionFindDecoder& d, double p) { return d.graph().weight_for_probability(p); },
             py::arg("p"))
        .def_property_readonly("num_detectors",
                               [](const UnionFindDecoder& d) { return d.graph().num_detectors(); })
        .def_property_readonly("num_observables",
                               [](const UnionFindDecoder& d) { return d.graph().num_observables(); })
        .def_property_readonly("num_edges",
                               [](const UnionFindDecoder& d) { return d.graph().num_edges(); })
        .def_property_readonly("weights",
                               [](const UnionFindDecoder& d) { return d.graph().weights(); })
        .def_property_readonly("last_growth_steps", &UnionFindDecoder::last_growth_steps)
        .def("stats",
             [](const UnionFindDecoder& d) {
                 const backlog::DecoderStats& s = d.stats();
                 py::dict out;
                 out["decodes"] = s.decodes;
                 out["defects"] = s.defects;
                 out["growth_steps"] = s.growth_steps;
                 out["full_edges"] = s.full_edges;
                 out["edge_reschedules"] = s.edge_reschedules;
                 out["events_popped"] = s.events_popped;
                 out["reweighted_edges"] = s.reweighted_edges;
                 return out;
             })
        .def("reset_stats", &UnionFindDecoder::reset_stats)
        .def("decode_ids",
             [](UnionFindDecoder& d, const std::vector<int32_t>& fired) {
                 return d.decode(fired);
             },
             py::arg("fired"))
        .def("decode_detailed_ids",
             [](UnionFindDecoder& d, const std::vector<int32_t>& fired) {
                 return to_tuple(d.decode_detailed(fired));
             },
             py::arg("fired"))
        .def("decode_batch_bit_packed",
             [](UnionFindDecoder& d, const U8& dets) {
                 check_packed(dets, d.graph().num_detectors());
                 const py::ssize_t shots = dets.shape(0);
                 const py::ssize_t obs_bytes = (d.graph().num_observables() + 7) / 8;
                 py::array_t<uint8_t> out({shots, obs_bytes});
                 const uint8_t* in = dets.data();
                 uint8_t* dst = out.mutable_data();
                 const std::size_t stride = static_cast<std::size_t>(dets.shape(1));
                 {
                     py::gil_scoped_release release;
                     d.decode_batch_bit_packed(in, static_cast<std::size_t>(shots), stride,
                                               dst, static_cast<std::size_t>(obs_bytes));
                 }
                 return out;
             },
             py::arg("bit_packed_detection_events"))
        .def("latency_ns",
             [](UnionFindDecoder& d, const U8& dets) {
                 check_packed(dets, d.graph().num_detectors());
                 const uint8_t* in = dets.data();
                 const std::size_t shots = static_cast<std::size_t>(dets.shape(0));
                 const std::size_t stride = static_cast<std::size_t>(dets.shape(1));
                 std::vector<double> lat;
                 {
                     py::gil_scoped_release release;
                     lat = d.latency_ns(in, shots, stride);
                 }
                 return py::array_t<double>(static_cast<py::ssize_t>(lat.size()), lat.data());
             },
             py::arg("bit_packed_detection_events"));

    py::class_<SlidingWindowDecoder>(mod, "SlidingWindowDecoder")
        .def(py::init([](int32_t n, int32_t m, const I32& a, const I32& b,
                         const U64& obs, const F64& p, const I32& layers,
                         int32_t commit, int32_t buffer, bool weighted,
                         int32_t resolution, const py::object& corr_offset,
                         const py::object& corr_target, const py::object& corr_conditional,
                         double alpha) {
                 if (layers.size() != n) {
                     throw std::invalid_argument("need one layer per detector");
                 }
                 std::vector<int32_t> layer(layers.data(), layers.data() + layers.size());
                 DecodingGraph g = make_graph(n, m, a, b, obs, p, weighted, resolution);
                 ReweightRules rules = make_rules(g, corr_offset, corr_target, corr_conditional, alpha);
                 return std::make_unique<SlidingWindowDecoder>(std::move(g), std::move(layer),
                                                               commit, buffer, std::move(rules));
             }),
             py::arg("num_detectors"), py::arg("num_observables"), py::arg("a"),
             py::arg("b"), py::arg("obs"), py::arg("p"), py::arg("layers"),
             py::arg("commit"), py::arg("buffer"), py::arg("weighted") = true,
             py::arg("resolution") = 1024, py::arg("corr_offset") = py::none(),
             py::arg("corr_target") = py::none(), py::arg("corr_conditional") = py::none(),
             py::arg("alpha") = 1.0)
        .def_property_readonly("correlated", &SlidingWindowDecoder::correlated)
        .def_property_readonly("num_detectors",
                               [](const SlidingWindowDecoder& d) { return d.graph().num_detectors(); })
        .def_property_readonly("num_observables",
                               [](const SlidingWindowDecoder& d) { return d.graph().num_observables(); })
        .def_property_readonly("num_layers", &SlidingWindowDecoder::num_layers)
        .def_property_readonly("num_windows", &SlidingWindowDecoder::num_windows)
        .def_property_readonly("num_shapes", &SlidingWindowDecoder::num_shapes)
        .def_property_readonly("windows",
                               [](const SlidingWindowDecoder& d) {
                                   py::list out;
                                   for (const auto& w : d.windows()) {
                                       out.append(py::make_tuple(w.first_layer, w.commit_end,
                                                                 w.end_layer));
                                   }
                                   return out;
                               })
        .def("decode_ids",
             [](SlidingWindowDecoder& d, const std::vector<int32_t>& fired) {
                 return d.decode(fired);
             },
             py::arg("fired"))
        .def("decode_detailed_ids",
             [](SlidingWindowDecoder& d, const std::vector<int32_t>& fired) {
                 return to_tuple(d.decode_detailed(fired));
             },
             py::arg("fired"))
        .def("decode_batch_bit_packed",
             [](SlidingWindowDecoder& d, const U8& dets) {
                 check_packed(dets, d.graph().num_detectors());
                 const py::ssize_t shots = dets.shape(0);
                 const py::ssize_t obs_bytes = (d.graph().num_observables() + 7) / 8;
                 py::array_t<uint8_t> out({shots, obs_bytes});
                 const uint8_t* in = dets.data();
                 uint8_t* dst = out.mutable_data();
                 const std::size_t stride = static_cast<std::size_t>(dets.shape(1));
                 {
                     py::gil_scoped_release release;
                     d.decode_batch_bit_packed(in, static_cast<std::size_t>(shots), stride,
                                               dst, static_cast<std::size_t>(obs_bytes));
                 }
                 return out;
             },
             py::arg("bit_packed_detection_events"))
        .def("window_latency_ns",
             [](SlidingWindowDecoder& d, const U8& dets) {
                 check_packed(dets, d.graph().num_detectors());
                 const uint8_t* in = dets.data();
                 const std::size_t shots = static_cast<std::size_t>(dets.shape(0));
                 const std::size_t stride = static_cast<std::size_t>(dets.shape(1));
                 std::vector<double> lat;
                 {
                     py::gil_scoped_release release;
                     lat = d.window_latency_ns(in, shots, stride);
                 }
                 py::array_t<double> out({static_cast<py::ssize_t>(shots),
                                          static_cast<py::ssize_t>(d.num_windows())});
                 std::copy(lat.begin(), lat.end(), out.mutable_data());
                 return out;
             },
             py::arg("bit_packed_detection_events"));

    py::class_<ParallelWindowDecoder>(mod, "ParallelWindowDecoder")
        .def(py::init([](int32_t n, int32_t m, const I32& a, const I32& b,
                         const U64& obs, const F64& p, const I32& layers,
                         int32_t commit, int32_t buffer, int32_t seam, int32_t threads,
                         bool weighted, int32_t resolution, const py::object& corr_offset,
                         const py::object& corr_target, const py::object& corr_conditional,
                         double alpha) {
                 if (layers.size() != n) {
                     throw std::invalid_argument("need one layer per detector");
                 }
                 std::vector<int32_t> layer(layers.data(), layers.data() + layers.size());
                 DecodingGraph g = make_graph(n, m, a, b, obs, p, weighted, resolution);
                 ReweightRules rules = make_rules(g, corr_offset, corr_target, corr_conditional, alpha);
                 return std::make_unique<ParallelWindowDecoder>(std::move(g), std::move(layer),
                                                                commit, buffer, seam, threads,
                                                                std::move(rules));
             }),
             py::arg("num_detectors"), py::arg("num_observables"), py::arg("a"),
             py::arg("b"), py::arg("obs"), py::arg("p"), py::arg("layers"),
             py::arg("commit"), py::arg("buffer"), py::arg("seam"), py::arg("threads"),
             py::arg("weighted") = true, py::arg("resolution") = 1024,
             py::arg("corr_offset") = py::none(), py::arg("corr_target") = py::none(),
             py::arg("corr_conditional") = py::none(), py::arg("alpha") = 1.0)
        .def_property_readonly("correlated", &ParallelWindowDecoder::correlated)
        .def_property_readonly("num_detectors",
                               [](const ParallelWindowDecoder& d) { return d.graph().num_detectors(); })
        .def_property_readonly("num_observables",
                               [](const ParallelWindowDecoder& d) { return d.graph().num_observables(); })
        .def_property_readonly("num_layers", &ParallelWindowDecoder::num_layers)
        .def_property_readonly("num_threads", &ParallelWindowDecoder::num_threads)
        .def_property_readonly("num_windows", &ParallelWindowDecoder::num_windows)
        .def_property_readonly("num_shapes", &ParallelWindowDecoder::num_shapes)
        .def_property_readonly("windows",
                               [](const ParallelWindowDecoder& d) {
                                   py::list out;
                                   for (const auto& w : d.windows()) {
                                       out.append(py::make_tuple(
                                           w.kind == backlog::Window::Core ? "core" : "seam",
                                           w.first_layer, w.commit_begin, w.commit_end,
                                           w.end_layer));
                                   }
                                   return out;
                               })
        .def("decode_ids",
             [](ParallelWindowDecoder& d, const std::vector<int32_t>& fired) {
                 py::gil_scoped_release release;
                 return d.decode(fired);
             },
             py::arg("fired"))
        .def("decode_detailed_ids",
             [](ParallelWindowDecoder& d, const std::vector<int32_t>& fired) {
                 backlog::DecodeResult r;
                 {
                     py::gil_scoped_release release;
                     r = d.decode_detailed(fired);
                 }
                 return to_tuple(r);
             },
             py::arg("fired"))
        .def("decode_batch_bit_packed",
             [](ParallelWindowDecoder& d, const U8& dets) {
                 check_packed(dets, d.graph().num_detectors());
                 const py::ssize_t shots = dets.shape(0);
                 const py::ssize_t obs_bytes = (d.graph().num_observables() + 7) / 8;
                 py::array_t<uint8_t> out({shots, obs_bytes});
                 const uint8_t* in = dets.data();
                 uint8_t* dst = out.mutable_data();
                 const std::size_t stride = static_cast<std::size_t>(dets.shape(1));
                 {
                     py::gil_scoped_release release;
                     d.decode_batch_bit_packed(in, static_cast<std::size_t>(shots), stride,
                                               dst, static_cast<std::size_t>(obs_bytes));
                 }
                 return out;
             },
             py::arg("bit_packed_detection_events"))
        .def("window_latency_ns",
             [](ParallelWindowDecoder& d, const U8& dets) {
                 check_packed(dets, d.graph().num_detectors());
                 const uint8_t* in = dets.data();
                 const std::size_t shots = static_cast<std::size_t>(dets.shape(0));
                 const std::size_t stride = static_cast<std::size_t>(dets.shape(1));
                 std::vector<double> lat;
                 {
                     py::gil_scoped_release release;
                     lat = d.window_latency_ns(in, shots, stride);
                 }
                 py::array_t<double> out({static_cast<py::ssize_t>(shots),
                                          static_cast<py::ssize_t>(d.num_windows())});
                 std::copy(lat.begin(), lat.end(), out.mutable_data());
                 return out;
             },
             py::arg("bit_packed_detection_events"))
        .def("shot_latency_ns",
             [](ParallelWindowDecoder& d, const U8& dets) {
                 check_packed(dets, d.graph().num_detectors());
                 const uint8_t* in = dets.data();
                 const std::size_t shots = static_cast<std::size_t>(dets.shape(0));
                 const std::size_t stride = static_cast<std::size_t>(dets.shape(1));
                 std::vector<double> lat;
                 {
                     py::gil_scoped_release release;
                     lat = d.shot_latency_ns(in, shots, stride);
                 }
                 return py::array_t<double>(static_cast<py::ssize_t>(lat.size()), lat.data());
             },
             py::arg("bit_packed_detection_events"));

    py::class_<ReferenceDecoder>(mod, "ReferenceDecoder")
        .def(py::init([](int32_t n, int32_t m, const I32& a, const I32& b,
                         const U64& obs, const F64& p, bool weighted,
                         int32_t resolution) {
                 return std::make_unique<ReferenceDecoder>(
                     make_graph(n, m, a, b, obs, p, weighted, resolution));
             }),
             py::arg("num_detectors"), py::arg("num_observables"), py::arg("a"),
             py::arg("b"), py::arg("obs"), py::arg("p"), py::arg("weighted") = true,
             py::arg("resolution") = 1024)
        .def("decode_detailed_ids",
             [](const ReferenceDecoder& d, const std::vector<int32_t>& fired) {
                 return to_tuple(d.decode_detailed(fired));
             },
             py::arg("fired"));
}
