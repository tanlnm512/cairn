# Worked bundle: perf-DS-v1

Reproduce this run with the command recorded in `manifest.json`
(`$HOME`/`$TMPDIR`-templated paths expand before running):

```sh
cairn bench --suite perf --sizes 100,500,1000,5000 --n-files 300 --complexity medium --embed-backend hash --worked benchmarks --threshold 0.15 --repeats 3 --runs 3
```

- `perf.json` -- raw result JSON, the identical payload `--save` writes.
- `manifest.json` -- inputs manifest: suite, dataset version, seed/repeats/runs,
  embed backend, machine stamp, full command line.
- `README.md` -- this companion.
