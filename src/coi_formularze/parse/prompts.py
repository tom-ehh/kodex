"""Prompts for the draft passes (Polish: the forms and the citizens are Polish).

Each prompt carries the pass's JSON Schema and the form's extracted markdown. Shared by
the API pipeline (parse/pipeline.py) and the dev benchmark (tools/llm_drafts.py).
"""

import json

_COMMON = """Jesteś ekspertem od cyfryzacji polskich formularzy urzędowych. Poniżej jest treść
papierowego formularza (wyciągnięta do markdown). Zwróć WYŁĄCZNIE jeden obiekt JSON
zgodny z podanym JSON Schema, bez komentarzy i bez bloków ```.
"""

_KINDS = """Rodzaje pól (kind): text (krótki tekst), long_text (dłuższy opis), number, date,
file (załącznik), info (tylko tekst informacyjny), single_choice (jeden wybór z opcji),
multi_choice (wiele wyborów), yes_no (Tak/Nie), pesel, nip, regon, krs, email, phone,
zip_code (kod pocztowy), country, voivodeship, county, commune, locality (miejscowość).
Adres rozpisz na osobne pola (np. locality, zip_code, text dla ulicy i numeru).
- required: czy pole jest obowiązkowe (numer lokalu, telefon, e-mail zwykle false).
- options: tylko dla single_choice/multi_choice, jak na formularzu; 'Inne' przez other_option.
- extra_validators: tylko dla kind=text (np. EPUAP); zwykle pusta lista.
- reusable_list: nazwa NOWEGO słownika (WIELKIE_LITERY), gdy opcje to ogólna lista."""

_TASKS = {
    "facts": """Zadanie: podaj podstawowe fakty o formularzu.
- title: tytuł usługi (Wniosek/Zgłoszenie/Deklaracja/Oświadczenie + przedmiot).
- addressee: adresat dokładnie jak na formularzu.
- filers: kto może złożyć wniosek: person (osoba fizyczna) i/lub entity (firma, instytucja).""",
    "outline": """Zadanie: zaplanuj strony i sekcje e-usługi dla tego formularza.
Fakty: {facts}
- pages: strony tematyczne w kolejności: najpierw strony wniosku (urząd, wnioskodawca,
  ewentualnie pełnomocnik), potem przedmiot wniosku, na końcu kontakt, opłaty,
  załączniki i oświadczenia. Bez strony podsumowania.
- sections: sekcje na stronie; purpose = jedno zdanie, jakie dane zbiera.
  repeatable=true tylko gdy obywatel wypełnia sekcję wiele razy (tabela osób/działek).""",
    "section": """Zadanie: wypisz WSZYSTKIE pola sekcji „{section}” ({purpose}).
Strona: „{page}”. Plan całego formularza:
{outline}
title = dokładnie „{section}”. Tylko pola tej sekcji.
""" + _KINDS,
    "page": """Zadanie: wypisz WSZYSTKIE pola z sekcji tej strony.
Sekcje tej strony: {sections}
sections: jedna pozycja na każdą sekcję powyżej, title dokładnie jak powyżej.
""" + _KINDS,
    "states": """Zadanie: wskaż, które sekcje lub pola pokazują się tylko przy określonej odpowiedzi
(np. 'Dane pełnomocnika' tylko gdy 'Składam jako' = 'Pełnomocnik').
Dostępne klucze (klucz [rodzaj]: etykieta, opcje):
{catalogue}
- target: klucz sekcji lub pola, które ma się pokazać.
- show_when: warunek na polu z opcjami (equals / not_equals; all_of / any_of).
Jeśli nic nie zależy od odpowiedzi, zwróć {{"states": []}}.""",
}


def prompt(task: str, md: str, schema: dict, hints: str = "", **ctx) -> str:
    """`hints`: Morpheus skill snippets (a "Wskazówki" block); empty = prompt unchanged."""
    body = _TASKS[task].format(**ctx) + (f"\n{hints}" if hints else "")
    return (f"{_COMMON}\n{body}\n\nJSON Schema:\n"
            f"{json.dumps(schema, ensure_ascii=False)}\n\nFormularz:\n<<<\n{md}\n>>>")


def retry_prompt(errors: list[str]) -> str:
    lines = "\n".join(f"- {m}" for m in errors[:20])
    return f"Twoja odpowiedź jest niepoprawna:\n{lines}\nZwróć poprawiony, pełny obiekt JSON."
