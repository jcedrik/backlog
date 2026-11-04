// backlog_decode: run the Union-Find decoder over a file of shots.
//
// Usage:
//   backlog_decode [--unweighted] [--resolution R] <graph.txt> <dets.txt> <preds_out.txt>
//
// Prints the syndrome-validity check (hard invariant, must be 100%) and the
// per-shot latency of the decode call (mean / p50 / p99 / max).
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "backlog/decoder.hpp"

int main(int argc, char** argv) {
    backlog::Weighting weighting = backlog::Weighting::LogLikelihood;
    int32_t resolution = 1024;
    std::vector<std::string> pos;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--unweighted") {
            weighting = backlog::Weighting::Uniform;
        } else if (arg == "--resolution" && i + 1 < argc) {
            resolution = std::atoi(argv[++i]);
        } else {
            pos.push_back(arg);
        }
    }
    if (pos.size() != 3) {
        std::cerr << "usage: " << argv[0]
                  << " [--unweighted] [--resolution R] <graph.txt> <dets.txt> <preds_out.txt>\n";
        return 2;
    }

    backlog::UnionFindDecoder dec(
        backlog::DecodingGraph::from_file(pos[0], weighting, resolution));
    const backlog::DecodingGraph& g = dec.graph();

    std::ifstream dets_in(pos[1]);
    if (!dets_in) {
        std::cerr << "cannot open " << pos[1] << "\n";
        return 1;
    }
    std::ofstream out(pos[2]);
    if (!out) {
        std::cerr << "cannot write " << pos[2] << "\n";
        return 1;
    }

    const int32_t m = g.num_observables();
    std::vector<double> lat_us;
    long long total = 0, valid = 0;
    std::string line;
    std::vector<int32_t> fired;
    while (std::getline(dets_in, line)) {
        fired.clear();
        std::istringstream ss(line);
        int32_t d;
        while (ss >> d) fired.push_back(d);

        const auto t0 = std::chrono::steady_clock::now();
        const uint64_t pred = dec.decode(fired);
        const auto t1 = std::chrono::steady_clock::now();
        lat_us.push_back(std::chrono::duration<double, std::micro>(t1 - t0).count());

        // Invariant check, outside the timed region.
        const backlog::DecodeResult detailed = dec.decode_detailed(fired);
        ++total;
        if (detailed.syndrome_ok && detailed.observables == pred) ++valid;

        for (int32_t j = 0; j < m; ++j) {
            if (j) out << ' ';
            out << ((pred >> j) & 1ULL);
        }
        out << '\n';
    }
    if (total == 0) {
        std::cerr << "no shots read\n";
        return 1;
    }

    std::vector<double> sorted = lat_us;
    std::sort(sorted.begin(), sorted.end());
    auto pct = [&sorted](double q) {
        return sorted[static_cast<std::size_t>(q * static_cast<double>(sorted.size() - 1))];
    };
    double sum = 0;
    for (double x : lat_us) sum += x;

    std::printf("graph: %d detectors, %d observables, %d edges, %s weights\n",
                g.num_detectors(), m, g.num_edges(),
                weighting == backlog::Weighting::Uniform ? "uniform" : "log-likelihood");
    std::printf("shots decoded:         %lld\n", total);
    std::printf("syndrome check:        %lld/%lld valid (%.2f%%)\n", valid, total,
                100.0 * static_cast<double>(valid) / static_cast<double>(total));
    std::printf("latency per shot (us): mean %.3f | p50 %.3f | p99 %.3f | max %.3f\n",
                sum / static_cast<double>(total), pct(0.50), pct(0.99), sorted.back());
    return valid == total ? 0 : 3;
}
