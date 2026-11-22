# Routing: basal-routed (skills off)

Router: basal at http://172.20.0.3:8000. Forced tier: none.

Real LLM spend this run: $0.0174 (cost columns: first-run cost of the calls, cached or not). Scoring: tools/llm_score.py vs expected.json.

## Per set

| set | mode | ok | recall | precision | type | required | validators | section rec | tokens | cost $ | list $ | list $/form | llm s | tiers | esc |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | off | 12/12 | 0.282 | 0.610 | 0.866 | 0.755 | 0.725 | 0.203 | 657794 | 0.0357 | 0.1868 | 0.01557 | 1388.4 | {'simple': 9, 'hard': 1, 'medium': 2} | 0 |
| gate | off | 6/6 | 0.345 | 0.600 | 0.866 | 0.752 | 0.719 | 0.265 | 461017 | 0.0116 | 0.1619 | 0.02698 | 869.3 | {'simple': 5, 'hard': 1} | 0 |
| held-out | off | 6/6 | 0.218 | 0.620 | 0.865 | 0.757 | 0.730 | 0.141 | 196777 | 0.0241 | 0.0249 | 0.00415 | 519.1 | {'simple': 4, 'medium': 2} | 0 |

## Per validator (label-matched fields; tp/fp/fn, P/R)

| set/mode | REQUIRED | PESEL | NIP | REGON | ZIP_CODE | EMAIL |
|---|---|---|---|---|---|---|
| all/off | 165/3/72 P0.982 R0.696 | 4/0/0 P1.000 R1.000 | 2/0/0 P1.000 R1.000 | 0/0/2 P– R0.000 | 16/7/0 P0.696 R1.000 | 6/0/2 P1.000 R0.750 |
| gate/off | 89/1/45 P0.989 R0.664 | 3/0/0 P1.000 R1.000 | 1/0/0 P1.000 R1.000 | 0/0/1 P– R0.000 | 8/5/0 P0.615 R1.000 | 2/0/1 P1.000 R0.667 |
| held-out/off | 76/2/27 P0.974 R0.738 | 1/0/0 P1.000 R1.000 | 1/0/0 P1.000 R1.000 | 0/0/1 P– R0.000 | 8/2/0 P0.800 R1.000 | 4/0/1 P1.000 R0.800 |

## Per form

| form | set | mode | ok | tier | recall | precision | type | required | validators | calls (cached) | llm s | cost $ | skills fired |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| oplata-za-odpady | gate | off | ✓ | hard | 0.401 | 0.582 | 0.860 | 0.632 | 0.614 | 26 (26) | 726.9 | 0.0000 |  |
| psy-ras-agresywnych | gate | off | ✓ | simple | 0.226 | 0.600 | 0.917 | 0.833 | 0.833 | 10 (0) | 25.4 | 0.0025 |  |
| wydanie-zaswiadczenia | gate | off | ✓ | simple | 0.346 | 0.562 | 0.889 | 0.889 | 0.889 | 9 (9) | 18.2 | 0.0014 |  |
| wynajecie-swietlicy-wiejskiej | gate | off | ✓ | simple | 0.303 | 0.526 | 0.800 | 0.900 | 0.800 | 10 (10) | 27.9 | 0.0018 |  |
| zajecie-pasa-drogowego | gate | off | ✓ | simple | 0.370 | 0.586 | 0.882 | 0.559 | 0.529 | 13 (0) | 37.9 | 0.0034 |  |
| zwrot-oplaty-skarbowej | gate | off | ✓ | simple | 0.426 | 0.741 | 0.850 | 0.700 | 0.650 | 15 (15) | 33.0 | 0.0025 |  |
| ho-wniosek-o-dzierzawe-najem-uzyczenie-nieruchomosci | held-out | off | ✓ | simple | 0.280 | 0.700 | 0.857 | 0.905 | 0.857 | 10 (10) | 20.6 | 0.0018 |  |
| ho-wniosek-o-pozwolenie-na-uzytkowanie-pb-17 | held-out | off | ✓ | medium | 0.317 | 0.556 | 0.900 | 0.650 | 0.600 | 15 (15) | 299.0 | 0.0120 |  |
| ho-wniosek-o-rejestracje-pojazdu | held-out | off | ✓ | medium | 0.203 | 0.571 | 0.875 | 0.625 | 0.562 | 12 (0) | 129.8 | 0.0053 |  |
| ho-wniosek-o-udostepnienie-informacji-publicznej | held-out | off | ✓ | simple | 0.155 | 0.458 | 0.818 | 0.636 | 0.636 | 12 (0) | 27.5 | 0.0021 |  |
| nadplata-podatku | held-out | off | ✓ | simple | 0.167 | 0.579 | 0.909 | 0.727 | 0.727 | 10 (0) | 26.7 | 0.0015 |  |
| numer-porzadkowy | held-out | off | ✓ | simple | 0.188 | 0.857 | 0.833 | 1.000 | 1.000 | 7 (0) | 15.5 | 0.0014 |  |

## Skills fired (ON, uses = prompt injections + rule applications)

