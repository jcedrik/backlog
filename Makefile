PY ?= python3

.PHONY: help deps install cpp test test-cpp test-py test-cli mutants quick baseline plot \
        compare plots streaming streaming-plot latency hw hw-sim all clean

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

mutants:  ## mutation testing: every injected bug must make the test suite fail (~2 min)
	$(PY) scripts/mutation_test.py

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

latency:  ## decoding time vs PyMatching, d = 3 to 13 (run on an idle machine)
	cd python && $(PY) latency_benchmark.py

hw:  ## VHDL: bit-exact simulation, synthesis, place and route
	./hardware/run_hw.sh

hw-sim:  ## VHDL: simulation only
	./hardware/run_hw.sh sim

all: test mutants quick hw-sim  ## everything CI runs

clean:  ## remove build outputs
	rm -rf cpp/build hardware/build cpp/testdata* results/quick build _skbuild
