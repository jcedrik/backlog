# Contributing to backlog

Issues and pull requests are welcome: bug reports, new noise models or codes,
faster decoding, hardware work.

## Set up

```bash
git clone https://github.com/jcedrik/backlog && cd backlog
python3 -m venv .venv && source .venv/bin/activate
make install          # builds the C++ core, installs the package with test extras
make test             # C++ fuzzing against the reference, pytest, CLI checks
```

You need a C++17 compiler, CMake 3.18+ and Python 3.10+. `make help` lists
every target.

## What a change needs to pass

The CI runs all of these on every push; run them locally first.

| Check | Command | What it guards |
|---|---|---|
| C++ tests | `make test-cpp` | the fast decoder equals the reference decoder edge for edge, on 75,000 random cases per fuzzer, including correlated decoding and both window decoders |
| Python tests | `make test-py` | accuracy gates against PyMatching on real Stim circuits, sinter integration, exhaustive fault checks |
| ThreadSanitizer | `make tsan` | no data race in the parallel window decoder |
| Mutation testing | `make mutants` | every injected bug (26 of them) makes the suite fail; a new feature should come with a mutant its tests kill |
| VHDL | `make hw-sim` | the hardware decoder agrees with the C++ model bit for bit |

The decoder has a specification: `ReferenceDecoder` (synchronous rounds,
written to be obviously correct). A change to the fast decoder must keep the
two identical; a change to the semantics changes the reference first.

## Benchmarks

Benchmark scripts write their CSVs and figures to `results/`. If a change
moves a published number, rerun the script that produced it and commit the
new results with the change, with the machine it ran on.

## Releasing (maintainer)

Wheels for Linux, macOS and Windows are built by `.github/workflows/wheels.yml`.
To publish to PyPI without storing a token, once:

1. on pypi.org, Account settings > Publishing > Add a new pending publisher:
   project `backlog-qec`, owner `jcedrik`, repository `backlog`, workflow
   `wheels.yml`, environment `pypi`;
2. on GitHub, Settings > Environments > New environment `pypi`.

Then a release is: bump the version in `pyproject.toml` and
`src/backlog/__init__.py`, commit, `git tag v0.5.0 && git push --tags`.
