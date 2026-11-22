# Benchmarks

Raw results of the runs behind the charts in the main README. The summary is
[results/RESULTS.md](results/RESULTS.md).

| Run | Question | Script |
|-----|----------|--------|
| `results/routing-routed`, `results/routing-hard` | Does the System One router save cost without losing quality, compared with sending every form to `hard`? | `tools/bench_morpheus.py` |
| `results/skills` | Do the seed skills improve extraction (skills off vs on)? | `tools/bench_morpheus.py` |
| `results/morpheus-wake` | What do wake-learned skills change? | `tools/bench_morpheus_wake.py` |
| `results/morpheus-sleep` | Does sleep merge skills without losing accuracy? | `tools/bench_morpheus_sleep.py` |

Each run folder has a `README.md` (scores per form), `results.json` (all metrics) and
`forms/` (every generated Form Definition). `run.log` and `steps.txt` are the log of the
unattended run.

To run again from the repository root, with the router up and `COI_EDENAI_API_KEY` set:

```sh
benchmarks/run.sh            # OUT=... to write elsewhere, BASAL=... for the router URL
```

The scripts score against hand-labelled reference outputs that are kept outside the
repository, so a fresh clone can read the results but not reproduce the scores.
Costs are EdenAI list prices × tokens; spending is capped per step (`--max-usd`).
