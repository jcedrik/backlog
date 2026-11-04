#!/usr/bin/env python3
"""Mutation testing: inject realistic bugs, check the C++ test suite catches each one.

A green test suite only means something if it fails when the code is wrong.
This script copies cpp/ to a temporary directory, applies one bug at a time,
rebuilds the test binary and runs it. A mutant is "killed" when the suite
fails (a check fails, fast and reference decoders disagree, or the run hangs
and hits the timeout). Any surviving mutant is a hole in the tests.

    python3 scripts/mutation_test.py        # about two minutes
"""
from __future__ import annotations

import re
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT_S = 30            # the unmutated suite runs in about 1.5 s
MEMORY_CAP = 1 << 30      # a runaway mutant must not take the machine down


def _cap_memory():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_CAP, MEMORY_CAP))
    except (ImportError, ValueError, OSError):
        pass


@dataclass(frozen=True)
class Mutant:
    name: str
    file: str       # relative to cpp/
    before: str
    after: str


MUTANTS = [
    Mutant("shared edge grows at rate 1 when both clusters grow",
           "src/decoder.cpp",
           "(rate == 1 ? rem : (rem + 1) >> 1)", "rem"),
    Mutant("merge forgets the absorbed cluster's radii",
           "src/decoder.cpp",
           "b.pot = Ra - Rb;", "b.pot = 0;"),
    Mutant("two odd clusters merge into an odd cluster",
           "src/decoder.cpp",
           "a.parity ^= b.parity;", "a.parity |= b.parity;"),
    Mutant("growth state changes are detected backwards",
           "src/decoder.cpp",
           "if (pr.second != nodes_[find(pr.first)].growing) {",
           "if (pr.second == nodes_[find(pr.first)].growing) {"),
    Mutant("shared edge tracked by only one of its two clusters",
           "src/decoder.cpp",
           "        sedges_[rw].push_back(e);\n", ""),
    Mutant("spanning forest ignores edge weights",
           "src/decoder.cpp",
           "return topo[x].weight != topo[y].weight\n"
           "                             ? topo[x].weight < topo[y].weight\n"
           "                             : x < y;",
           "return x < y;"),
    Mutant("path compression stores the wrong radius offset",
           "src/decoder.cpp",
           "        total -= own;\n", ""),
    Mutant("node event fires one round late",
           "src/decoder.cpp",
           "queue_.push(now + wait, ~v, nodes_[v].nver);",
           "queue_.push(now + wait + 1, ~v, nodes_[v].nver);"),
    Mutant("node event fires one round early (runaway loop)",
           "src/decoder.cpp",
           "std::max<int64_t>(topo_[e].weight - rho, 0);",
           "std::max<int64_t>(topo_[e].weight - rho - 1, 0);"),
    Mutant("log-likelihood weights collapse to uniform",
           "src/graph.cpp",
           "ll[i] = std::log((1.0 - p) / p);", "ll[i] = 1.0;"),
    # Sliding windows
    Mutant("window: a committed edge does not carry its flip forward",
           "src/window.cpp",
           "                        residual_[u] ^= 1;\n", ""),
    Mutant("window: flips land in the committed region instead of ahead",
           "src/window.cpp",
           "if (u != B && layer_[u] >= w.commit_end) {",
           "if (u != B && layer_[u] < w.commit_end) {"),
    Mutant("window: defects created by earlier commits are ignored",
           "src/window.cpp",
           "                    local_.push_back(v - lo);\n                    merged = true;",
           "                    merged = true;"),
    Mutant("window: windows do not overlap",
           "src/window.cpp",
           "for (int32_t s = 0; n > 0; s += commit_) {",
           "for (int32_t s = 0; n > 0; s += commit_ + buffer_) {"),
    Mutant("window: edges into the future are dropped",
           "src/window.cpp",
           "            for (int32_t e : owned) {\n",
           "            for (int32_t e : owned) {\n"
           "                if (high[e] != B && high[e] >= hi) continue;\n"),
    Mutant("window: edges committed by their far end",
           "src/window.cpp",
           "commit.push_back(layer_[low[e]] < w.commit_end ? 1 : 0);",
           "commit.push_back(high[e] != B && layer_[high[e]] < w.commit_end ? 1 : 0);"),
]


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def build(build_dir: Path) -> bool:
    r = run(["cmake", "--build", str(build_dir), "--target", "backlog_tests", "-j2"])
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:])
    return r.returncode == 0


def run_suite(binary: Path) -> tuple[bool, str]:
    """(passed, short description of the outcome)."""
    try:
        r = run([str(binary)], timeout=TIMEOUT_S, preexec_fn=_cap_memory)
    except subprocess.TimeoutExpired:
        return False, f"hang, stopped after {TIMEOUT_S} s"
    out = r.stdout
    mism = re.search(r"(\d+) mismatches", out)
    fails = re.search(r"(\d+) checks, (\d+) failures", out)
    if r.returncode == 0:
        return True, "suite passed"
    parts = []
    if mism and int(mism.group(1)):
        parts.append(f"{int(mism.group(1))} fast/reference mismatches")
    for pattern, label in ((r"(\d+) invalid corrections", "invalid windowed corrections"),
                           (r"(\d+) differences with whole-shot", "differences with whole-shot")):
        found = re.search(pattern, out)
        if found and int(found.group(1)):
            parts.append(f"{int(found.group(1))} {label}")
    if not parts and fails and int(fails.group(2)):
        parts.append(f"{int(fails.group(2))} failed checks")
    if not parts and "bad_alloc" in r.stderr:
        parts.append("runaway memory, stopped by the 1 GB cap")
    if not parts and r.returncode < 0:
        parts.append(f"crashed with {signal.Signals(-r.returncode).name}")
    if not parts:
        parts.append(f"exit code {r.returncode}")
    return False, ", ".join(parts)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="backlog-mutants-") as tmp:
        src = Path(tmp) / "cpp"
        shutil.copytree(ROOT / "cpp", src, ignore=shutil.ignore_patterns("build", "testdata*"))
        build_dir = Path(tmp) / "build"
        r = run(["cmake", "-S", str(src), "-B", str(build_dir),
                 "-DCMAKE_BUILD_TYPE=Release", "-DBACKLOG_BUILD_TOOLS=ON"])
        if r.returncode != 0:
            print(r.stdout, r.stderr)
            return 2
        binary = build_dir / "backlog_tests"
        originals = {m.file: (src / m.file).read_text() for m in MUTANTS}

        if not build(build_dir):
            return 2
        ok, how = run_suite(binary)
        if not ok:
            print(f"the unmutated suite must pass first ({how})")
            return 2

        survivors = 0
        print(f"{'mutant':<62} result")
        for m in MUTANTS:
            for f, text in originals.items():
                (src / f).write_text(text)
            text = originals[m.file]
            if text.count(m.before) != 1:
                print(f"{m.name:<62} PATTERN NOT FOUND (update this script)")
                survivors += 1
                continue
            (src / m.file).write_text(text.replace(m.before, m.after))
            if not build(build_dir):
                print(f"{m.name:<62} BUILD FAILED")
                survivors += 1
                continue
            passed, how = run_suite(binary)
            if passed:
                survivors += 1
                print(f"{m.name:<62} SURVIVED")
            else:
                print(f"{m.name:<62} killed: {how}")

        killed = len(MUTANTS) - survivors
        print(f"\n{killed} / {len(MUTANTS)} mutants killed")
        return 0 if survivors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
