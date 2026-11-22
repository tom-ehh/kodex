# Graded form outputs

JSON configurations for the six forms to digitise (organisers' `new_downloads/` 2, 3, 4, 6, 7, 8), exactly as the Kodex pipeline returned them (`POST /api/v1/forms/analyze`, the `form` from the envelope). No hand edits. Generated 2026-11-22.

| Form | `new_downloads/` | Input | Tier |
|---|---|---|---|
| zwrot-oplaty-skarbowej | 2 | PDF | simple |
| wydanie-zaswiadczenia | 3 | PDF | simple |
| wynajecie-swietlicy-wiejskiej | 4 | PDF | simple |
| zajecie-pasa-drogowego | 6 | PDF | medium |
| psy-ras-agresywnych | 7 | PDF | hard |
| oplata-za-odpady | 8 | DOCX (an earlier PDF run timed out on the hard tier) | simple |

Checks: 0 issues from `kodex.schema.form.validate_form_definition`; in the Form Preview (`/preview`) every file shows "Brak problemów", every tab renders, no console errors. `screens/` has every tab of each file.
