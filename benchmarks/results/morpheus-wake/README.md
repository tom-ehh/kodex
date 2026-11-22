# Morpheus wake measurement run

Writer `qwen/deepseek-v4-pro-0813`, replay `deepinfra/Qwen/Qwen3.6-35B-A3B`, basal `Remek/basal-omni-pico` (local, CPU). Real spend: $0.0928 (wake $0.0086); the hard tier reports no cost, so writer calls show $0. Wake wall time 142.6 s.

## Episodes

| ep | pattern | form | label | hist | basal P(pattern) / consistent | stage | replay | smoke | skill |
|---|---|---|---|---|---|---|---|---|---|
| E01 | zip | zwrot-oplaty-skarbowej | Kod pocztowy | 1 | – | cold_start | – | – | – |
| E04 | pesel | wydanie-zaswiadczenia | PESEL | 1 | – | cold_start | – | – | – |
| E06 | nip | zwrot-oplaty-skarbowej | NIP | 1 | – | cold_start | – | – | – |
| E09 | email | zajecie-pasa-drogowego | Adres e-mail | 1 | – | cold_start | – | – | – |
| E12 | phone-optional | zwrot-oplaty-skarbowej | Numer telefonu | 1 | – | cold_start | – | – | – |
| E03 | zip | wydanie-zaswiadczenia | Kod pocztowy | 2 | 0.9021 / 0.9035 | activated | pass | pass | ep-zip-code-hyphen |
| E05 | pesel | oplata-za-odpady | PESEL | 2 | 0.9073 / 0.8696 | activated | pass | pass | ep-pesel-digits-only |
| E07 | nip | oplata-za-odpady | NIP/REGON | 2 | 0.7107 / 0.7416 | activated | pass | pass | ep-nip-remove-pl-prefix |
| E10 | email | oplata-za-odpady | Adres e-mail | 2 | 0.8756 / 0.9281 | activated | pass | pass | ep-email-trim-spaces |
| E15 | phone-optional | psy-ras-agresywnych | Telefon | 2 | 0.902 / 0.1856 | activated | pass | pass | ep-phone-required-flag |
| E32 | one-off | wynajecie-swietlicy-wiejskiej | Kod pocztowy | 3 | 0.0442 / 0.8492 | basal_veto | – | – | – |
| E38 | one-off | zajecie-pasa-drogowego | Adres e-mail | 3 | 0.2264 / 0.83 | basal_veto | – | – | – |
| E34 | one-off | wynajecie-swietlicy-wiejskiej | Imię i nazwisko wnioskodawcy | 1 | – | cold_start | – | – | – |
| E52 | one-off | wynajecie-swietlicy-wiejskiej | Imię i nazwisko wnioskodawcy | 2 | – | shape | – | – | – |

## Funnel

| stage | n |
|---|---|
| cold_start | 6 |
| activated | 5 |
| basal_veto | 2 |
| shape | 1 |

## Latency per stage (s)

| stage | n | mean | max |
|---|---|---|---|
| basal_s | 7 | 1.490 | 1.657 |
| write_s | 5 | 0.640 | 3.170 |
| replay_s | 5 | 17.960 | 30.320 |
| smoke_s | 5 | 7.800 | 38.956 |

## Learned skills

- **ep-email-trim-spaces** v1 (active): Usuwa spacje z adresu e-mail. — trigger ['adres e-mail', 'e-mail', 'email'] / EMAIL; rule {'add_validators': ['EMAIL'], 'required': None, 'post_fix': 'trim'}; snippet: Dla pola adres e-mail ustaw rodzaj pola email_phone, wymagalność zgodnie z formularzem i walidator EMAIL. Usuń zbędne spacje z wartości.
- **ep-nip-remove-pl-prefix** v1 (active): Usuwa prefiks kraju PL z pola NIP/REGON, pozostawiając wyłącznie cyfry. — trigger ['NIP', 'REGON', 'NIP/REGON'] / NIP; rule {'add_validators': ['NIP'], 'required': None, 'post_fix': 'digits_only'}; snippet: Rozpoznaj pole nip_regon jako wymagane. Zastosuj walidator NIP i usuń prefiks kraju PL, pozostawiając wyłącznie cyfry.
- **ep-pesel-digits-only** v1 (active): Pole PESEL powinno zawierać wyłącznie 11 cyfr bez separatorów. — trigger ['PESEL'] / PESEL; rule {'add_validators': ['PESEL'], 'required': None, 'post_fix': 'digits_only'}; snippet: Rozpoznaj pole jako pesel, ustaw wymagalność zgodnie z formularzem i zastosuj walidator PESEL. Wartość zapisuj wyłącznie jako 11 cyfr bez separatorów.
- **ep-phone-required-flag** v1 (active): Pole telefon oznaczone jako wymagane mimo etykiety sugerującej opcjonalność. — trigger ['telefon'] / None; rule {'add_validators': [], 'required': False, 'post_fix': None}; snippet: Jeśli etykieta pola telefonu sugeruje, że pole jest nieobowiązkowe, ale pole ma ustawioną wymagalność, ustaw required na false.
- **ep-zip-code-hyphen** v1 (active): Reguła formatowania kodu pocztowego z myślnikiem po drugiej cyfrze. — trigger ['kod pocztowy'] / ZIP_CODE; rule {'add_validators': ['ZIP_CODE'], 'required': None, 'post_fix': 'zip_code'}; snippet: Pole typu zip_code. Wartość sformatuj jako kod pocztowy w formacie ##-###. Zastosuj walidator ZIP_CODE.

## Re-score: seeds only vs seeds + learned

| set | mode | ok | recall | precision | type | required | validators | section rec | esc |
|---|---|---|---|---|---|---|---|---|---|
| all | learned | 12/12 | 0.288 | 0.547 | 0.816 | 0.794 | 0.763 | 0.216 | 0 |
| all | seeds | 12/12 | 0.287 | 0.554 | 0.816 | 0.778 | 0.748 | 0.216 | 0 |
| gate | learned | 6/6 | 0.357 | 0.546 | 0.776 | 0.809 | 0.774 | 0.309 | 0 |
| gate | seeds | 6/6 | 0.357 | 0.546 | 0.776 | 0.802 | 0.769 | 0.309 | 0 |
| held-out | learned | 6/6 | 0.220 | 0.549 | 0.855 | 0.778 | 0.751 | 0.122 | 0 |
| held-out | seeds | 6/6 | 0.218 | 0.561 | 0.856 | 0.753 | 0.727 | 0.122 | 0 |

### Per validator (tp/fp/fn)

| set/mode | REQUIRED | PESEL | NIP | REGON | ZIP_CODE | EMAIL |
|---|---|---|---|---|---|---|
| all/learned | 186/3/57 | 5/0/0 | 2/1/0 | 2/0/0 | 15/9/1 | 7/0/2 |
| all/seeds | 181/3/61 | 5/0/0 | 2/1/0 | 2/0/0 | 15/9/1 | 7/0/2 |
| gate/learned | 107/1/32 | 3/0/0 | 1/1/0 | 1/0/0 | 8/5/0 | 2/0/1 |
| gate/seeds | 105/1/34 | 3/0/0 | 1/1/0 | 1/0/0 | 8/5/0 | 2/0/1 |
| held-out/learned | 79/2/25 | 2/0/0 | 1/0/0 | 1/0/0 | 7/4/1 | 5/0/1 |
| held-out/seeds | 76/2/27 | 2/0/0 | 1/0/0 | 1/0/0 | 7/4/1 | 5/0/1 |

### Skills fired

- **all/learned**: seed-address-required 82, seed-phone-optional 14, ep-email-trim-spaces 10, ep-zip-code-hyphen 10, seed-email 10, ep-pesel-digits-only 10, seed-pesel 10, ep-phone-required-flag 9, seed-zip-code 8, seed-marked-optional 6, ep-nip-remove-pl-prefix 5, seed-nip 4, seed-regon 2
- **all/seeds**: seed-address-required 80, seed-phone-optional 33, seed-email 24, seed-zip-code 10, seed-pesel 10, seed-marked-optional 6, seed-nip 6, seed-regon 3
- **gate/learned**: seed-address-required 43, ep-zip-code-hyphen 6, seed-phone-optional 6, seed-zip-code 6, ep-email-trim-spaces 5, seed-email 5, ep-pesel-digits-only 5, seed-pesel 5, ep-phone-required-flag 4, seed-marked-optional 3, ep-nip-remove-pl-prefix 2, seed-nip 2, seed-regon 1
- **gate/seeds**: seed-address-required 43, seed-phone-optional 17, seed-email 11, seed-zip-code 6, seed-pesel 5, seed-marked-optional 3, seed-nip 3, seed-regon 1
- **held-out/learned**: seed-address-required 39, seed-phone-optional 8, ep-email-trim-spaces 5, ep-phone-required-flag 5, seed-email 5, ep-pesel-digits-only 5, seed-pesel 5, ep-zip-code-hyphen 4, seed-marked-optional 3, ep-nip-remove-pl-prefix 3, seed-nip 2, seed-zip-code 2, seed-regon 1
- **held-out/seeds**: seed-address-required 37, seed-phone-optional 16, seed-email 13, seed-pesel 5, seed-zip-code 4, seed-marked-optional 3, seed-nip 3, seed-regon 2
