#!/usr/bin/env bash
# Reproduce the whole hardware flow:
#   golden model -> test vectors -> RTL simulation (bit-exact)
#   -> synthesis (ghdl + yosys, Xilinx 7-series stats)
#   -> place and route on iCE40 HX8K (nextpnr) for a real Fmax.
#
# Needs: python3 (+ numpy, pymatching for the model selfcheck), ghdl >= 4,
#        yosys, nextpnr-ice40. On Ubuntu: apt install ghdl yosys nextpnr-ice40
#
# Usage: ./run_hw.sh            # sim + synth + pnr for N = 8 16 32
#        ./run_hw.sh sim        # simulation only (what CI runs)
set -euo pipefail
cd "$(dirname "$0")"

SIZES=(8 16 32)
MODE="${1:-all}"
mkdir -p build

echo "== golden model selfcheck"
python3 model/uf1d_model.py selfcheck --n 8 --shots 5000

echo "== RTL simulation (bit-exact vs golden model)"
ghdl -a --std=08 --workdir=build rtl/uf_decoder_1d.vhd tb/tb_uf_decoder_1d.vhd
for n in "${SIZES[@]}"; do
  python3 model/uf1d_model.py vectors --n "$n" --out "tb/vectors_n$n.txt" > /dev/null
  ghdl --elab-run --std=08 --workdir=build tb_uf_decoder_1d \
       -gN="$n" -gVECTORS="tb/vectors_n$n.txt" 2>&1 | grep -E "N=|PASS|FAIL"
done

[ "$MODE" = "sim" ] && exit 0

echo "== synthesis + place and route"
printf "%-6s %-6s %-8s %-8s %-10s %-12s\n" N d LUTs FFs iCE40_LC Fmax_iCE40
for n in "${SIZES[@]}"; do
  ghdl --synth --std=08 --workdir=build -gN="$n" --out=verilog uf_decoder_1d \
       > "build/uf_n$n.v" 2>/dev/null
  yosys -p "read_verilog build/uf_n$n.v; synth_xilinx -family xc7 -top uf_decoder_1d; tee -o build/stat_n$n.txt stat" \
       > "build/yosys_n$n.log" 2>&1
  luts=$(grep -E "^ +LUT[1-6] " "build/stat_n$n.txt" | awk '{s+=$2} END {print s}')
  ffs=$(grep -E "^ +FD[RS]E " "build/stat_n$n.txt" | awk '{s+=$2} END {print s}')
  yosys -q -p "read_verilog build/uf_n$n.v; synth_ice40 -top uf_decoder_1d -json build/uf_ice40_n$n.json" \
       > /dev/null 2>&1
  nextpnr-ice40 --hx8k --package ct256 --json "build/uf_ice40_n$n.json" \
       --pcf-allow-unconstrained --freq 50 --timing-allow-fail --seed 1 \
       > "build/pnr_n$n.log" 2>&1
  lc=$(grep -E "ICESTORM_LC: +[0-9]+/" "build/pnr_n$n.log" | tail -1 \
       | sed -E 's/.*ICESTORM_LC: +([0-9]+)\/.*/\1/')
  fmax=$(grep -E "Max frequency for clock" "build/pnr_n$n.log" | tail -1 \
         | sed -E 's/.*: ([0-9.]+) MHz.*/\1/')
  printf "%-6s %-6s %-8s %-8s %-10s %-12s\n" "$n" "$((n + 1))" "$luts" "$ffs" "$lc" "${fmax} MHz"
done
