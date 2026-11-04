// Python bindings for the backlog decoders (module: backlog._core).
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <memory>
#include <stdexcept>
#include <vector>

#include "backlog/decoder.hpp"

namespace py = pybind11;
using backlog::DecodeResult;
using backlog::DecodingGraph;
using backlog::Edge;
using backlog::ReferenceDecoder;
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
                         int32_t resolution) {
                 return std::make_unique<UnionFindDecoder>(
                     make_graph(n, m, a, b, obs, p, weighted, resolution));
             }),
             py::arg("num_detectors"), py::arg("num_observables"), py::arg("a"),
             py::arg("b"), py::arg("obs"), py::arg("p"), py::arg("weighted") = true,
             py::arg("resolution") = 1024)
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
