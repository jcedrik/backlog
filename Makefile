PY ?= python3

.PHONY: help deps install cpp test test-cpp test-py test-cli mutants tsan quick baseline plot \
        compare plots streaming streaming-plot parallel parallel-plot scaling latency correlated \
        correlated-speed correlated-plot faults faults-plot quickstart hw hw-sim all clean

help:  ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/'

deps:  ## install the Python dependencies of the experiments
	$(PY) -m pip install -r requirements.txt

install:  ## build the C++ core and install the backlog Python package (use a virtualenv)
	$(PY) -m pip install ".[test]"

cpp:  ## build the C++ command-line decoder and the C++ test suite
	cmake -S cpp -B cpp/build -DCMAKE_BUILD_TYPE=Release
	cmake --build cpp/build -j

test: test-cpp test-py test-cli  ## all tests except the slow sweeps (run make install first)

test-cpp: cpp  ## C++ unit tests + 75,000 fuzzed cases, fast decoder == reference decoder
	cd cpp/build && ctest --output-on-failure

test-py:  ## pytest: accuracy gates against PyMatching, sinter integration
	$(PY) -m pytest -q

test-cli: cpp  ## command-line decoder on graphs exported from Stim, gated against PyMatching
	./scripts/run_tests.sh

mutants:  ## mutation testing: each of 26 injected bugs must make the test suite fail (~10 min)
	$(PY) scripts/mutation_test.py

tsan:  ## C++ test suite under ThreadSanitizer: any data race fails the run (~5 min)
	cmake -S cpp -B cpp/build-tsan -DCMAKE_BUILD_TYPE=Debug -DBACKLOG_TSAN=ON
	cmake --build cpp/build-tsan -j --target backlog_tests
	TSAN_OPTIONS=halt_on_error=1 ./cpp/build-tsan/backlog_tests

quick:  ## tiny PyMatching threshold sweep into results/quick/ (smoke test, seconds)
	cd python && $(PY) baseline_threshold.py --quick && $(PY) plot_threshold.py --results-dir ../results/quick

baseline:  ## PyMatching-only threshold sweep (a few minutes), see results/threshold.png
	cd python && $(PY) baseline_threshold.py --max-shots 300000 --max-errors 1000

plot:  ## redraw results/threshold.png from results/threshold_data.csv
	cd python && $(PY) plot_threshold.py

compare:  ## sinter sweep, PyMatching vs weighted and unweighted UF (long, resumable)
	cd python && $(PY) threshold_comparison.py collect && $(PY) threshold_comparison.py plot

plots:  ## redraw the comparison figures from results/threshold_comparison.csv
	cd python && $(PY) threshold_comparison.py plot

streaming:  ## sliding windows: accuracy vs buffer, per-window latency, backlog (minutes, idle machine)
	cd python && $(PY) streaming_benchmark.py accuracy && $(PY) streaming_benchmark.py latency \
	  && $(PY) streaming_benchmark.py plot

streaming-plot:  ## redraw results/streaming.png from the CSVs
	cd python && $(PY) streaming_benchmark.py plot

parallel:  ## parallel windows: accuracy, 1 vs 2 thread speedup, cores needed (minutes, idle machine)
	cd python && $(PY) parallel_benchmark.py accuracy && $(PY) parallel_benchmark.py speedup \
	  && $(PY) parallel_benchmark.py project && $(PY) parallel_benchmark.py plot

parallel-plot:  ## redraw results/parallel.png from the saved data
	cd python && $(PY) parallel_benchmark.py plot

scaling:  ## real speedup with 1, 2, 4, ... threads up to every core here, into results/scaling/
	cd python && $(PY) parallel_benchmark.py scaling

correlated:  ## correlated decoding vs plain and vs PyMatching, same shots (~15 min on 2 cores)
	$(PY) python/correlated_benchmark.py accuracy && $(PY) python/correlated_benchmark.py decomposition

correlated-speed:  ## decoding time of plain and correlated decoders (idle machine)
	$(PY) python/correlated_benchmark.py speed

correlated-plot:  ## redraw results/correlated.png and print the Lambda table
	$(PY) python/correlated_benchmark.py plot

decomposition-plot:  ## redraw results/decomposition.png: correlated vs plain matching, both decompositions
	$(PY) python/decomposition_plot.py results/correlated_decomposition.csv results/decomposition.png

faults:  ## exhaustive fault enumeration, d = 3 and 5: which pairs each decoder gets wrong (~6 min)
	$(PY) python/fault_distance.py

faults-plot:  ## redraw results/fault_distance.png
	$(PY) python/fault_distance.py --plot

quickstart:  ## rebuild and run examples/quickstart.ipynb (needs nbformat, nbclient, ipykernel)
	$(PY) examples/make_quickstart.py

latency:  ## decoding time vs PyMatching, d = 3 to 13 (run on an idle machine)
	cd python && $(PY) latency_benchmark.py

hw:  ## VHDL: bit-exact simulation, synthesis, place and route
	./hardware/run_hw.sh

hw-sim:  ## VHDL: simulation only
	./hardware/run_hw.sh sim

all: test tsan mutants quick hw-sim  ## everything CI runs

clean:  ## remove build outputs
	rm -rf cpp/build cpp/build-tsan hardware/build cpp/testdata* results/quick build _skbuild
