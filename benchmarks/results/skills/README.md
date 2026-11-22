# Seed skills v2: OFF vs ON

Router: heuristic (basal off). Forced tier: none.

Real LLM spend this run: $0.0970 (cost columns: first-run cost of the calls, cached or not). Scoring: tools/llm_score.py vs expected.json.

## Per set

| set | mode | ok | recall | precision | type | required | validators | section rec | tokens | cost $ | list $ | list $/form | llm s | tiers | esc |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | off | 12/12 | 0.291 | 0.556 | 0.833 | 0.725 | 0.683 | 0.216 | 641674 | 0.0443 | 0.2255 | 0.01879 | 2075.0 | {'simple': 4, 'medium': 6, 'hard': 2} | 0 |
| all | on | 12/12 | 0.285 | 0.555 | 0.820 | 0.784 | 0.743 | 0.216 | 690810 | 0.0485 | 0.2399 | 0.01999 | 1970.4 | {'simple': 4, 'medium': 6, 'hard': 2} | 0 |
| gate | off | 6/6 | 0.370 | 0.551 | 0.786 | 0.740 | 0.698 | 0.309 | 469299 | 0.0142 | 0.1955 | 0.03257 | 1313.1 | {'simple': 3, 'medium': 1, 'hard': 2} | 0 |
| gate | on | 6/6 | 0.357 | 0.546 | 0.776 | 0.802 | 0.769 | 0.309 | 500394 | 0.0144 | 0.2058 | 0.03430 | 1127.2 | {'simple': 3, 'medium': 1, 'hard': 2} | 0 |
| held-out | off | 6/6 | 0.213 | 0.561 | 0.879 | 0.709 | 0.669 | 0.122 | 172375 | 0.0301 | 0.0301 | 0.00501 | 761.9 | {'medium': 5, 'simple': 1} | 0 |
| held-out | on | 6/6 | 0.213 | 0.564 | 0.865 | 0.767 | 0.717 | 0.122 | 190416 | 0.0341 | 0.0341 | 0.00568 | 843.2 | {'medium': 5, 'simple': 1} | 0 |

## Per validator (label-matched fields; tp/fp/fn, P/R)

| set/mode | REQUIRED | PESEL | NIP | REGON | ZIP_CODE | EMAIL |
|---|---|---|---|---|---|---|
| all/off | 162/3/81 P0.982 R0.667 | 4/0/0 P1.000 R1.000 | 2/0/0 P1.000 R1.000 | 0/0/2 P– R0.000 | 14/9/1 P0.609 R0.933 | 7/0/2 P1.000 R0.778 |
| all/on | 181/3/59 P0.984 R0.754 | 4/0/0 P1.000 R1.000 | 2/1/0 P0.667 R1.000 | 1/0/1 P1.000 R0.500 | 14/9/1 P0.609 R0.933 | 7/0/2 P1.000 R0.778 |
| gate/off | 94/1/49 P0.989 R0.657 | 3/0/0 P1.000 R1.000 | 1/0/0 P1.000 R1.000 | 0/0/1 P– R0.000 | 8/6/0 P0.571 R1.000 | 2/0/1 P1.000 R0.667 |
| gate/on | 105/1/34 P0.991 R0.755 | 3/0/0 P1.000 R1.000 | 1/1/0 P0.500 R1.000 | 1/0/0 P1.000 R1.000 | 8/5/0 P0.615 R1.000 | 2/0/1 P1.000 R0.667 |
| held-out/off | 68/2/32 P0.971 R0.680 | 1/0/0 P1.000 R1.000 | 1/0/0 P1.000 R1.000 | 0/0/1 P– R0.000 | 6/3/1 P0.667 R0.857 | 5/0/1 P1.000 R0.833 |
| held-out/on | 76/2/25 P0.974 R0.752 | 1/0/0 P1.000 R1.000 | 1/0/0 P1.000 R1.000 | 0/0/1 P– R0.000 | 6/4/1 P0.600 R0.857 | 5/0/1 P1.000 R0.833 |

## Per form

| form | set | mode | ok | tier | recall | precision | type | required | validators | calls (cached) | llm s | cost $ | skills fired |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| oplata-za-odpady | gate | off | ✓ | hard | 0.401 | 0.582 | 0.860 | 0.632 | 0.614 | 26 (26) | 726.9 | 0.0000 |  |
| oplata-za-odpady | gate | on | ✓ | hard | 0.401 | 0.582 | 0.860 | 0.825 | 0.807 | 29 (16) | 634.6 | 0.0000 | address-required×17, email×2, nip×1, pesel×4, phone-optional×2, regon×1, zip-code×3 |
| psy-ras-agresywnych | gate | off | ✓ | hard | 0.340 | 0.409 | 0.778 | 0.778 | 0.722 | 15 (15) | 134.0 | 0.0000 |  |
| psy-ras-agresywnych | gate | on | ✓ | hard | 0.264 | 0.378 | 0.714 | 0.857 | 0.857 | 15 (11) | 68.9 | 0.0000 | address-required×5, email×1, marked-optional×1, phone-optional×4, zip-code×1 |
| wydanie-zaswiadczenia | gate | off | ✓ | simple | 0.346 | 0.562 | 0.889 | 0.889 | 0.889 | 9 (9) | 18.2 | 0.0014 |  |
| wydanie-zaswiadczenia | gate | on | ✓ | simple | 0.346 | 0.562 | 0.889 | 0.889 | 0.889 | 9 (9) | 18.2 | 0.0014 | address-required×3, pesel×1, phone-optional×1 |
| wynajecie-swietlicy-wiejskiej | gate | off | ✓ | simple | 0.303 | 0.526 | 0.800 | 0.900 | 0.800 | 10 (10) | 27.9 | 0.0018 |  |
| wynajecie-swietlicy-wiejskiej | gate | on | ✓ | simple | 0.303 | 0.526 | 0.800 | 0.900 | 0.800 | 10 (10) | 27.9 | 0.0018 | address-required×2, phone-optional×1 |
| zajecie-pasa-drogowego | gate | off | ✓ | medium | 0.402 | 0.487 | 0.541 | 0.541 | 0.514 | 14 (14) | 373.1 | 0.0085 |  |
| zajecie-pasa-drogowego | gate | on | ✓ | medium | 0.402 | 0.487 | 0.541 | 0.541 | 0.514 | 14 (9) | 343.4 | 0.0085 | address-required×6, email×6, marked-optional×1, phone-optional×7 |
| zwrot-oplaty-skarbowej | gate | off | ✓ | simple | 0.426 | 0.741 | 0.850 | 0.700 | 0.650 | 15 (15) | 33.0 | 0.0025 |  |
| zwrot-oplaty-skarbowej | gate | on | ✓ | simple | 0.426 | 0.741 | 0.850 | 0.800 | 0.750 | 16 (10) | 34.2 | 0.0027 | address-required×10, email×2, marked-optional×1, nip×2, phone-optional×2, zip-code×2 |
| ho-wniosek-o-dzierzawe-najem-uzyczenie-nieruchomosci | held-out | off | ✓ | simple | 0.280 | 0.700 | 0.857 | 0.905 | 0.857 | 10 (10) | 20.6 | 0.0018 |  |
| ho-wniosek-o-dzierzawe-najem-uzyczenie-nieruchomosci | held-out | on | ✓ | simple | 0.267 | 0.690 | 0.850 | 0.900 | 0.850 | 10 (7) | 21.3 | 0.0018 | address-required×7, phone-optional×4 |
| ho-wniosek-o-pozwolenie-na-uzytkowanie-pb-17 | held-out | off | ✓ | medium | 0.317 | 0.556 | 0.900 | 0.650 | 0.600 | 15 (15) | 299.0 | 0.0120 |  |
| ho-wniosek-o-pozwolenie-na-uzytkowanie-pb-17 | held-out | on | ✓ | medium | 0.317 | 0.556 | 0.900 | 0.725 | 0.700 | 18 (9) | 341.9 | 0.0147 | address-required×19, email×6, marked-optional×2, phone-optional×6, zip-code×4 |
| ho-wniosek-o-rejestracje-pojazdu | held-out | off | ✓ | medium | 0.177 | 0.609 | 0.929 | 0.643 | 0.500 | 12 (0) | 134.3 | 0.0049 |  |
| ho-wniosek-o-rejestracje-pojazdu | held-out | on | ✓ | medium | 0.177 | 0.609 | 0.929 | 0.643 | 0.500 | 12 (12) | 134.3 | 0.0049 | address-required×1 |
| ho-wniosek-o-udostepnienie-informacji-publicznej | held-out | off | ✓ | medium | 0.211 | 0.500 | 0.800 | 0.267 | 0.267 | 12 (12) | 127.8 | 0.0046 |  |
| ho-wniosek-o-udostepnienie-informacji-publicznej | held-out | on | ✓ | medium | 0.197 | 0.483 | 0.786 | 0.500 | 0.500 | 12 (10) | 143.8 | 0.0051 | address-required×4, email×4, phone-optional×3 |
| nadplata-podatku | held-out | off | ✓ | medium | 0.152 | 0.500 | 0.900 | 0.900 | 0.900 | 10 (10) | 102.8 | 0.0034 |  |
| nadplata-podatku | held-out | on | ✓ | medium | 0.182 | 0.545 | 0.833 | 0.833 | 0.750 | 11 (7) | 124.6 | 0.0042 | address-required×3, nip×3, pesel×3 |
| numer-porzadkowy | held-out | off | ✓ | medium | 0.141 | 0.500 | 0.889 | 0.889 | 0.889 | 9 (9) | 77.4 | 0.0034 |  |
| numer-porzadkowy | held-out | on | ✓ | medium | 0.141 | 0.500 | 0.889 | 1.000 | 1.000 | 9 (8) | 77.3 | 0.0034 | address-required×2, email×3, marked-optional×1, phone-optional×3 |

## Skills fired (ON, uses = prompt injections + rule applications)

- **gate/on**: seed-address-required 43, seed-phone-optional 17, seed-email 11, seed-zip-code 6, seed-pesel 5, seed-marked-optional 3, seed-nip 3, seed-regon 1
- **held-out/on**: seed-address-required 36, seed-phone-optional 16, seed-email 13, seed-zip-code 4, seed-marked-optional 3, seed-nip 3, seed-pesel 3
- **all/on**: seed-address-required 79, seed-phone-optional 33, seed-email 24, seed-zip-code 10, seed-pesel 8, seed-marked-optional 6, seed-nip 6, seed-regon 1
