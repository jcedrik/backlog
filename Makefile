PY ?= python3

.PHONY: help deps quick baseline plot cpp test bench hw hw-sim all clean

help:  ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/'

deps:  ## install Python dependencies
	$(PY) -m pip install -r requirements.txt

quick:  ## tiny threshold sweep into results/quick/ (smoke test, ~5 s)
	cd python && $(PY) baseline_threshold.py --quick && $(PY) plot_threshold.py --results-dir ../results/quick

baseline:  ## full Stim + PyMatching threshold sweep (a few minutes)
	cd python && $(PY) baseline_threshold.py --max-shots 300000 --max-errors 1000

plot:  ## redraw results/threshold.png from results/threshold_data.csv
	cd python && $(PY) plot_threshold.py

cpp:  ## build the C++ Union-Find decoder
	cmake -S cpp -B cpp/build -DCMAKE_BUILD_TYPE=Release
	cmake --build cpp/build -j

test: cpp  ## C++ decoder: syndrome invariant + accuracy vs PyMatching
	./scripts/run_tests.sh

bench: cpp  ## C++ UF vs PyMatching on the surface code, writes results/uf_vs_pymatching.*
	cd python && $(PY) uf_vs_pymatching.py

hw:  ## VHDL: bit-exact simulation, synthesis, place and route
	./hardware/run_hw.sh

hw-sim:  ## VHDL: simulation only
	./hardware/run_hw.sh sim

all: quick test hw-sim  ## everything CI runs

clean:  ## remove build outputs
	rm -rf cpp/build hardware/build cpp/testdata* results/quick
