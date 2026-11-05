# Measuring the parallel decoder on a many-core server

The parallel window decoder has only been measured on 2 cores (a cloud VM)
and 4 cores (a laptop, where hyperthreads made it slower). Its scheduling
model predicts how many cores a given distance and error rate need, but
beyond 4 cores that is still a prediction. This page is how to replace it
with a measurement, on any Linux machine with 16 to 32 cores, in about half
an hour.

## What to run

On a fresh Ubuntu 22.04 or 24.04 machine:

```bash
curl -fsSL https://raw.githubusercontent.com/jcedrik/backlog/main/scripts/server_scaling.sh | bash
```

[`scripts/server_scaling.sh`](../scripts/server_scaling.sh) installs the
build tools, clones and builds backlog, records the CPU layout (`lscpu`, and
whether simultaneous multithreading is on), runs `make scaling`, and packs
`results/scaling/` into one `backlog-scaling-<machine>-<date>.tar.gz` in the
home folder. `make scaling` decodes d = 9, 13 and 17 at p = 0.1% and 0.3%
with 1, 2, 4, ... threads up to every core, and splits the speedup into work
inflation (the same windows running slower with more cores busy) and idle
time (threads waiting at the end of a shot).

## Which machine

- **Physical cores, not hyperthreads.** Turn simultaneous multithreading off
  when you create the machine, so that threads = cores. On Google Cloud that
  is *Advanced configurations > vCPUs to core ratio > 1 vCPU per core*: a
  `c2d-standard-32` then shows 16 cores, one thread each (it is billed as 32
  vCPUs). AMD instances without SMT, such as AWS `c7a`, work as they are.
- **Dedicated, not shared.** Burstable or shared-core machine types throttle
  under load and make the numbers meaningless.
- **Nothing else running.** The script needs no network once it has
  installed and built everything.

On Google Cloud, a `c2d-standard-32` costs about 1.5 USD an hour, so one run
is under a dollar; free-trial accounts are limited to 8 vCPUs until they are
upgraded to a full account (the remaining credit carries over).

## Afterwards

Delete the machine. `python python/parallel_benchmark.py scaling-plot --dir
<folder>` redraws the figure from an unpacked result folder, and the CSV goes
next to the existing laptop results (`results/scaling-i7-8665U-v0.5/`).
