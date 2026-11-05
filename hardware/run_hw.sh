#!/usr/bin/env bash
# Reproduce the whole hardware flow, for both scans (Kogge-Stone and ripple):
#   golden model -> test vectors -> RTL simulation (bit-exact)
#   -> synthesis (ghdl + yosys, Xilinx 7-series stats)
#   -> place and route on iCE40 HX8K (nextpnr) for a real Fmax, best of 3 seeds.
#
# Needs: python3 (+ numpy, pymatching for the model selfcheck), ghdl >= 4,
#        yosys, nextpnr-ice40. On Ubuntu: apt install ghdl yosys nextpnr-ice40
#
# Usage: ./run_hw.sh            # sim + synth + pnr for N = 8 to 128 (~10 min)
#        ./run_hw.sh sim        # simulation only (what CI runs)
set -euo pipefail
cd "$(dirname "$0")"

SIZES=(8 16 32 64 128)
SEEDS=(1 2 3)
MODE="${1:-all}"
mkdir -p build

echo "== golden model selfcheck"
python3 model/uf1d_model.py selfcheck --n 8 --shots 5000

echo "== RTL simulation (bit-exact vs golden model)"
ghdl -a --std=08 --workdir=build rtl/uf_decoder_1d.vhd rtl/uf_fmax_top.vhd tb/tb_uf_decoder_1d.vhd
for n in "${SIZES[@]}"; do
  python3 model/uf1d_model.py vectors --n "$n" --out "build/vectors_n$n.txt" > /dev/null
  for parallel in true false; do
    ghdl --elab-run --std=08 --workdir=build tb_uf_decoder_1d -gN="$n" \
         -gVECTORS="build/vectors_n$n.txt" -gPARALLEL="$parallel" 2>&1 | grep -E "N=|PASS|FAIL"
  done
done

[ "$MODE" = "sim" ] && exit 0

echo "== synthesis + place and route"
echo "N,scan,luts_xc7,ffs_xc7,ice40_lc,fmax_mhz,logic_ns,routing_ns" > fmax.csv
printf "%-5s %-9s %-6s %-5s %-9s %-11s %-11s %s\n" N scan LUTs FFs iCE40_LC Fmax_iCE40 \
       "worst case" "critical path (ns logic + routing)"
for n in "${SIZES[@]}"; do
  for parallel in true false; do
    scan=$([ "$parallel" = true ] && echo parallel || echo ripple)
    tag="n${n}_$scan"
    # resources of the decoder alone, on Xilinx 7-series
    ghdl --synth --std=08 --workdir=build -gN="$n" -gPARALLEL="$parallel" \
         --out=verilog uf_decoder_1d > "build/uf_$tag.v" 2>/dev/null
    yosys -p "read_verilog build/uf_$tag.v; synth_xilinx -family xc7 -top uf_decoder_1d; tee -o build/stat_$tag.txt stat" \
         > "build/yosys_$tag.log" 2>&1
    luts=$(grep -E "^ +LUT[1-6] " "build/stat_$tag.txt" | awk '{s+=$2} END {print s}')
    ffs=$(grep -E "^ +FD[RS]E " "build/stat_$tag.txt" | awk '{s+=$2} END {print s}')
    # Fmax: the decoder behind serial I/O (rtl/uf_fmax_top.vhd), so every N
    # fits the package; the best of a few placement seeds, on a tight target
    ghdl --synth --std=08 --workdir=build -gN="$n" -gPARALLEL="$parallel" \
         --out=verilog uf_fmax_top > "build/top_$tag.v" 2>/dev/null
    yosys -q -p "read_verilog build/top_$tag.v; synth_ice40 -top uf_fmax_top -json build/top_$tag.json" \
         > /dev/null 2>&1
    fmax=0
    best="build/pnr_${tag}_s${SEEDS[0]}.log"
    for seed in "${SEEDS[@]}"; do
      log="build/pnr_${tag}_s$seed.log"
      nextpnr-ice40 --hx8k --package ct256 --json "build/top_$tag.json" \
           --pcf-allow-unconstrained --freq 150 --timing-allow-fail --seed "$seed" > "$log" 2>&1
      f=$(grep -E "Max frequency for clock" "$log" | tail -1 | sed -E 's/.*: ([0-9.]+) MHz.*/\1/')
      if awk -v a="$f" -v b="$fmax" 'BEGIN {exit !(a > b)}'; then fmax=$f; best=$log; fi
      lc=$(grep -E "ICESTORM_LC: +[0-9]+/" "$log" | tail -1 | sed -E 's/.*ICESTORM_LC: +([0-9]+)\/.*/\1/')
    done
    # the clock's critical path in the best placement: logic delay, routing delay
    split=$(grep -E "ns logic, .* ns routing" "$best" | head -1 \
            | sed -E 's/.* ([0-9.]+) ns logic, ([0-9.]+) ns routing.*/\1 \2/')
    read -r logic routing <<< "$split"
    worst=$(awk -v n="$n" -v f="$fmax" 'BEGIN {printf "%.0f ns", (n + 2) * 1000 / f}')
    printf "%-5s %-9s %-6s %-5s %-9s %-11s %-11s %s\n" "$n" "$scan" "$luts" "$ffs" "$lc" \
           "$fmax MHz" "$worst" "$logic + $routing"
    echo "$n,$scan,$luts,$ffs,$lc,$fmax,$logic,$routing" >> fmax.csv
  done
done
echo "wrote hardware/fmax.csv"
python3 plot_fmax.py fmax.csv fmax.png
