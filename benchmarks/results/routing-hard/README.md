# Routing: everything forced to hard (skills off)

Router: basal at http://172.20.0.3:8000. Forced tier: hard.

Real LLM spend this run: $0.1366 (cost columns: first-run cost of the calls, cached or not). Scoring: tools/llm_score.py vs expected.json.

## Per set

| set | mode | ok | recall | precision | type | required | validators | section rec | tokens | cost $ | list $ | list $/form | llm s | tiers | esc |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | off | 9/12 | 0.308 | 0.536 | 0.846 | 0.747 | 0.702 | 0.311 | 674813 | 0.0000 | 0.3712 | 0.03093 | 1893.0 | {'hard': 12} | 0 |
| gate | off | 5/6 | 0.357 | 0.524 | 0.819 | 0.778 | 0.708 | 0.400 | 459009 | 0.0000 | 0.2470 | 0.04117 | 1098.0 | {'hard': 6} | 0 |
| held-out | off | 4/6 | 0.246 | 0.552 | 0.879 | 0.709 | 0.693 | 0.199 | 215804 | 0.0000 | 0.1242 | 0.02069 | 795.0 | {'hard': 6} | 0 |

## Per validator (label-matched fields; tp/fp/fn, P/R)

| set/mode | REQUIRED | PESEL | NIP | REGON | ZIP_CODE | EMAIL |
|---|---|---|---|---|---|---|
| all/off | 125/1/63 P0.992 R0.665 | 5/0/0 P1.000 R1.000 | 2/0/0 P1.000 R1.000 | 1/0/1 P1.000 R0.500 | 12/8/0 P0.600 R1.000 | 5/0/0 P1.000 R1.000 |
| gate/off | 75/0/33 P1.000 R0.694 | 3/0/0 P1.000 R1.000 | 1/0/0 P1.000 R1.000 | 0/0/1 P– R0.000 | 6/6/0 P0.500 R1.000 | 1/0/0 P1.000 R1.000 |
| held-out/off | 50/1/30 P0.980 R0.625 | 2/0/0 P1.000 R1.000 | 1/0/0 P1.000 R1.000 | 1/0/0 P1.000 R1.000 | 6/2/0 P0.750 R1.000 | 4/0/0 P1.000 R1.000 |

## Per form

| form | set | mode | ok | tier | recall | precision | type | required | validators | calls (cached) | llm s | cost $ | skills fired |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| oplata-za-odpady | gate | off | ✓ | hard | 0.401 | 0.582 | 0.860 | 0.632 | 0.614 | 26 (26) | 726.9 | 0.0000 |  |
| psy-ras-agresywnych | gate | off | ✓ | [simple] hard | 0.340 | 0.409 | 0.778 | 0.778 | 0.722 | 15 (15) | 134.0 | 0.0000 |  |
| wydanie-zaswiadczenia | gate | off | ✓ | [simple] hard | 0.346 | 0.429 | 0.778 | 0.889 | 0.778 | 11 (0) | 36.3 | 0.0000 |  |
| wynajecie-swietlicy-wiejskiej | gate | off | ✓ | [simple] hard | 0.273 | 0.429 | 0.778 | 0.889 | 0.778 | 8 (0) | 34.3 | 0.0000 |  |
| zajecie-pasa-drogowego | gate | off | ✗ | [simple] hard | – | – | – | – | – | 19 (0) | 114.3 | 0.0000 |  |
| zwrot-oplaty-skarbowej | gate | off | ✓ | [simple] hard | 0.426 | 0.769 | 0.900 | 0.700 | 0.650 | 14 (0) | 52.2 | 0.0000 |  |
| ho-wniosek-o-dzierzawe-najem-uzyczenie-nieruchomosci | held-out | off | ✗ | [simple] hard | – | – | – | – | – | 16 (0) | 211.8 | 0.0000 |  |
| ho-wniosek-o-pozwolenie-na-uzytkowanie-pb-17 | held-out | off | ✓ | [medium] hard | 0.341 | 0.566 | 0.907 | 0.512 | 0.512 | 18 (18) | 280.1 | 0.0000 |  |
| ho-wniosek-o-rejestracje-pojazdu | held-out | off | ✓ | [medium] hard | 0.228 | 0.439 | 0.889 | 0.722 | 0.722 | 12 (0) | 51.5 | 0.0000 |  |
| ho-wniosek-o-udostepnienie-informacji-publicznej | held-out | off | ✗ | [simple] hard | – | – | – | – | – | 12 (0) | 150.6 | 0.0000 |  |
| nadplata-podatku | held-out | off | ✓ | [simple] hard | 0.167 | 0.440 | 0.909 | 0.727 | 0.727 | 16 (0) | 59.5 | 0.0000 |  |
| numer-porzadkowy | held-out | off | ✓ | [simple] hard | 0.250 | 0.762 | 0.812 | 0.875 | 0.812 | 9 (9) | 41.5 | 0.0000 |  |

## Skills fired (ON, uses = prompt injections + rule applications)

