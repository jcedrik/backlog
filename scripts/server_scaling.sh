#!/usr/bin/env bash
# Measure the parallel window decoder on a machine with many cores.
#
# On a fresh Ubuntu 22.04 or 24.04 machine (a cloud VM is fine), this installs
# the build tools, clones backlog, builds it, records the CPU layout, runs
# `make scaling` (speedup with 1, 2, 4, ... threads up to every core, and where
# the rest goes: work inflation and idle time) and packs everything into one
# file in the home folder. About 10 minutes on 16 to 32 cores.
#
#   curl -fsSL https://raw.githubusercontent.com/jcedrik/backlog/main/scripts/server_scaling.sh | bash
#
# For threads = physical cores, turn simultaneous multithreading off when you
# create the machine (one thread per core); the script records which it was.
set -euo pipefail

REPO=${BACKLOG_REPO:-https://github.com/jcedrik/backlog.git}
WORK=${BACKLOG_DIR:-$HOME/backlog}

SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO=sudo

echo "== build tools"
$SUDO apt-get update -qq
$SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    git cmake g++ make python3-venv python3-dev > /dev/null

echo "== backlog"
if [ ! -d "$WORK/.git" ]; then
  git clone --quiet --depth 1 "$REPO" "$WORK"
fi
cd "$WORK"
python3 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt
python -m pip install -q .

echo "== machine"
mkdir -p results/scaling
{
  echo "commit: $(git rev-parse --short HEAD)"
  echo "date: $(date -u +%Y-%m-%dT%H:%MZ)"
  echo "kernel: $(uname -r)"
  echo "smt: $(cat /sys/devices/system/cpu/smt/control 2>/dev/null || echo unknown)"
  echo
  lscpu
} > results/scaling/lscpu.txt
grep -E "^(Model name|CPU\(s\)|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\))" \
    results/scaling/lscpu.txt

echo "== make scaling"
make scaling PY=python

out="$HOME/backlog-scaling-$(hostname)-$(date -u +%Y%m%d-%H%M).tar.gz"
tar czf "$out" -C results scaling
echo
echo "Done. Results: $out"
echo "Download that file, then delete the machine so it stops costing money."
