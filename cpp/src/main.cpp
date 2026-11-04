// backlog_decode: run the Union-Find decoder over a file of shots.
//
// Usage:
//   backlog_decode <graph.txt> <dets.txt> <preds_out.txt>
//
// Prints:
//   - the syndrome-validity check (hard invariant: must be 100%)
//   - per-shot decode latency (mean / p50 / p99 / max), in microseconds
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "union_find_decoder.hpp"

int main(int argc, char** argv) {
    if (argc != 4) {
        std::cerr << "usage: " << argv[0]
                  << " <graph.txt> <dets.txt> <preds_out.txt>\n";
        return 2;
    }

    backlog::UnionFindDecoder dec =
        backlog::UnionFindDecoder::from_graph_file(argv[1]);

    std::ifstream dets_in(argv[2]);
    if (!dets_in) {
        std::cerr << "cannot open " << argv[2] << "\n";
        return 1;
    }
    std::ofstream out(argv[3]);
    if (!out) {
        std::cerr << "cannot write " << argv[3] << "\n";
        return 1;
    }

    const int m = dec.num_observables();
    std::vector<double> lat_us;
    long long total = 0, valid = 0;
    std::string line;
    std::vector<int> synd;

    while (std::getline(dets_in, line)) {
        synd.clear();
        std::istringstream ss(line);
        int d;
        while (ss >> d) synd.push_back(d);

        bool ok = false;
        auto t0 = std::chrono::steady_clock::now();
        uint64_t pred = dec.decode(synd, &ok);
        auto t1 = std::chrono::steady_clock::now();
        lat_us.push_back(
            std::chrono::duration<double, std::micro>(t1 - t0).count());

        ++total;
        if (ok) ++valid;
        for (int j = 0; j < m; ++j) {
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
    auto pct = [&](double q) {
        std::size_t i = static_cast<std::size_t>(q * (sorted.size() - 1));
        return sorted[i];
    };
    double sum = 0;
    for (double x : lat_us) sum += x;

    std::printf("graph: %d detectors, %d observables, %zu edges\n",
                dec.num_detectors(), m, dec.num_edges());
    std::printf("shots decoded:         %lld\n", total);
    std::printf("syndrome check:        %lld/%lld valid (%.2f%%)\n", valid,
                total, 100.0 * valid / total);
    std::printf("latency per shot (us): mean %.3f | p50 %.3f | p99 %.3f | "
                "max %.3f\n",
                sum / total, pct(0.50), pct(0.99), sorted.back());

    return valid == total ? 0 : 3;  // non-zero exit if the invariant breaks
}
