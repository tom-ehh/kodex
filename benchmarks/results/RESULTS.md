# Benchmark results (unattended run)

## Steps

```
skills DONE 2026-11-22T06:25:54+01:00
routing-routed DONE 2026-11-22T06:28:54+01:00
routing-hard DONE 2026-11-22T06:39:09+01:00
morpheus-wake DONE 2026-11-22T06:47:10+01:00
morpheus-sleep DONE 2026-11-22T06:57:57+01:00
```

## 1. Routing: basal-routed vs everything on hard (skills off)

| run | ok | recall | precision | type | required | validators | list $/form | LLM s | tiers | heuristic routes |
|---|---|---|---|---|---|---|---|---|---|---|
| routed / all | 12/12 | 0.282 | 0.610 | 0.866 | 0.755 | 0.725 | 0.01557 | 1388.4 | {'simple': 9, 'hard': 1, 'medium': 2} | 0 |
| routed / gate | 6/6 | 0.345 | 0.600 | 0.866 | 0.752 | 0.719 | 0.02698 | 869.3 | {'simple': 5, 'hard': 1} | 0 |
| routed / held-out | 6/6 | 0.218 | 0.620 | 0.865 | 0.757 | 0.730 | 0.00415 | 519.1 | {'simple': 4, 'medium': 2} | 0 |
| forced hard / all | 9/12 | 0.308 | 0.536 | 0.846 | 0.747 | 0.702 | 0.03093 | 1893.0 | {'hard': 12} | 0 |
| forced hard / gate | 5/6 | 0.357 | 0.524 | 0.819 | 0.778 | 0.708 | 0.04117 | 1098.0 | {'hard': 6} | 0 |
| forced hard / held-out | 4/6 | 0.246 | 0.552 | 0.879 | 0.709 | 0.693 | 0.02069 | 795.0 | {'hard': 6} | 0 |

**Forced hard costs 2.0× the routed cost per form and 1.4× the LLM time; validators 0.725 routed vs 0.702 hard.** Costs use EdenAI list prices × tokens (PRICES in tools/bench_morpheus.py).

## 2. Seed skills v2: OFF vs ON (heuristic router)

| run | ok | recall | precision | type | required | validators |
|---|---|---|---|---|---|---|
| all / off | 12/12 | 0.291 | 0.556 | 0.833 | 0.725 | 0.683 |
| all / on | 12/12 | 0.285 | 0.555 | 0.820 | 0.784 | 0.743 |
| gate / off | 6/6 | 0.370 | 0.551 | 0.786 | 0.740 | 0.698 |
| gate / on | 6/6 | 0.357 | 0.546 | 0.776 | 0.802 | 0.769 |
| held-out / off | 6/6 | 0.213 | 0.561 | 0.879 | 0.709 | 0.669 |
| held-out / on | 6/6 | 0.213 | 0.564 | 0.865 | 0.767 | 0.717 |

Earlier baseline (seeds v1, after the ZIP fix): docs/notes/morpheus-hooks/bench-zipfix/README.md.

## 3. Morpheus: seeds → wake → sleep → approved

| run | ok | recall | precision | type | required | validators | skills |
|---|---|---|---|---|---|---|---|
| all / seeds | 12/12 | 0.287 | 0.554 | 0.816 | 0.778 | 0.748 | – |
| all / wake | 12/12 | 0.287 | 0.542 | 0.822 | 0.793 | 0.769 | 22 |
| all / sleep | 12/12 | 0.287 | 0.542 | 0.822 | 0.793 | 0.769 | 17 |
| all / approved | 12/12 | 0.290 | 0.544 | 0.817 | 0.786 | 0.757 | 16 |
| gate / seeds | 6/6 | 0.357 | 0.546 | 0.776 | 0.802 | 0.769 | – |
| gate / wake | 6/6 | 0.357 | 0.546 | 0.776 | 0.809 | 0.774 | 22 |
| gate / sleep | 6/6 | 0.357 | 0.546 | 0.776 | 0.809 | 0.774 | 17 |
| gate / approved | 6/6 | 0.358 | 0.547 | 0.776 | 0.804 | 0.772 | 16 |
| held-out / seeds | 6/6 | 0.218 | 0.561 | 0.856 | 0.753 | 0.727 | – |
| held-out / wake | 6/6 | 0.217 | 0.538 | 0.869 | 0.776 | 0.765 | 22 |
| held-out / sleep | 6/6 | 0.217 | 0.538 | 0.869 | 0.776 | 0.765 | 17 |
| held-out / approved | 6/6 | 0.222 | 0.542 | 0.858 | 0.769 | 0.743 | 16 |

Clusters found 3 exact; general skills: ['gen-kod-pocztowy-format-z-owdtou', 'gen-pesel-cyfr-bez-separatorow-icvzlo']; review queue: 1.

Per-run detail: */README.md next to this file. Log: run.log.
