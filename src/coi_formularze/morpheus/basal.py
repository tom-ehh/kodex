"""System One (basal-omni-pico) typed questions for the wake flag (design.md decision 7).

One mixed request, no few-shot, the history count NOT in `state` (calibration.md):
- `scope`: wording **B** (sharper criteria with examples), P(pattern) confirms the rule.
- `family`: wording **A** (short criteria; 0.89 vs 0.75 for B), a routing tag only.
- `validator_consistent`: wording B, used only as a veto.

Wording is copied verbatim from docs/notes/morpheus/calibration/run_wake.py (QB/FAMS_A).
Response shapes verified live (design.md §6). Reuses the router's BasalUnavailable.
"""

from dataclasses import dataclass, field

import httpx

from coi_formularze.morpheus.models import Episode
from coi_formularze.route.router import BasalUnavailable

FAMILIES_A = {
    "pesel": "Numer PESEL.", "nip_regon": "NIP lub REGON.", "zip_code": "Kod pocztowy.",
    "email_phone": "Adres e-mail lub telefon.",
    "address": "Adres: ulica, numer domu/lokalu, miejscowość, gmina, powiat, województwo.",
    "date": "Data.", "name": "Imię, nazwisko, nazwa podmiotu.",
    "choice_checkbox": "Pole wyboru, lista, checkbox, radio.",
    "table_repeat": "Tabela lub sekcja powtarzalna.",
    "required_flag": "Czy pole jest wymagane (obowiązkowe/opcjonalne).", "other": "Inne.",
}

QUESTIONS = {
    "scope": {
        "type": "choice",
        "instructions": "Czy błąd wynika z reguły pola, która powtórzy się w innych formularzach "
        "(wzorzec), czy z przypadku tylko tego jednego dokumentu?",
        "criteria": {
            "one_off": "Jednorazowy: błąd OCR lub literówka w jednym skanie, pojedyncza nietypowa "
            "wartość, preferencja pracownika bez związku z regułą. Uzasadnienie brzmi np. «w tym "
            "skanie», «w tym dokumencie», «bo tak wolę», «OCR».",
            "pattern": "Wzorzec: błąd wynika z formatu lub reguły pola i wystąpi znów, np. kod "
            "pocztowy bez myślnika, PESEL ze spacjami, NIP z kreskami, zdublowany checkbox Brak, "
            "REQUIRED na polu opcjonalnym. Uzasadnienie powołuje się na format, standard lub regułę.",
        },
    },
    "family": {"type": "choice", "instructions": "Do której rodziny pól należy to pole?",
               "criteria": FAMILIES_A},
    "validator_consistent": {
        "type": "noul",
        "instructions": "Czy wartość po poprawce ma format zgodny z walidatorem, a uzasadnienie to "
        "merytoryczny powód (a nie preferencja, brak powodu lub zmiana bez efektu)?",
        "criteria": {
            "true": "Tak: wartość po ma format walidatora (np. ZIP_CODE → ##-###, PESEL → 11 cyfr, "
            "NIP → 10 cyfr, EMAIL → ma @) lub poprawka jest zgodna ze standardem, a uzasadnienie "
            "wskazuje na format lub regułę.",
            "false": "Nie: wartość po nadal łamie format (np. ####-#, 10 cyfr PESEL, kreski w NIP), "
            "nic się nie zmieniło, albo uzasadnienie to «bo tak wolę», «bez powodu», «OCR» bez efektu.",
        },
    },
}


@dataclass
class BasalFlag:
    p_pattern: float
    family: str
    family_conf: float
    consistent: float
    latency_ms: float = 0.0
    raw: dict = field(default_factory=dict)  # probabilities only, no text

    def scores(self) -> dict:
        return {"p_pattern": round(self.p_pattern, 4), "family": self.family,
                "family_conf": round(self.family_conf, 4), "consistent": round(self.consistent, 4)}


def build_state(ep: Episode, why: str | None) -> str:
    """The calibration state, without the history count. `why` is live only, never stored."""
    return (f"Typ epizodu: {ep.kind.value}\nPole: {ep.field_path or '(brak)'}\n"
            f"Etykieta: \"{ep.field_label or ''}\"\nWalidator: {ep.validator or '(brak)'}\n"
            f"Wartość przed (kształt): {ep.before_shape or '(brak)'}\n"
            f"Wartość po (kształt): {ep.after_shape or '(brak)'}\n"
            f"Eskalacja: {'tak' if ep.tier_to else 'nie'}\n"
            f"Uzasadnienie pracownika: \"{(why or '').strip()[:500]}\"")


def parse_flag(body: object) -> BasalFlag:
    if not isinstance(body, dict) or body.get("status") == "starting":
        raise BasalUnavailable("basal is starting or answered no object")
    try:
        a = body["answers"]
        scope = a["scope"]
        probs = {str(k): float(v) for k, v in (scope.get("probabilities") or {}).items()}
        p_pattern = probs.get("pattern", 1.0 if scope["choice"] == "pattern" else 0.0)
        fam = a["family"]
        consistent = float(a["validator_consistent"]["noul"])
        latency = float((body.get("usage") or {}).get("latency_ms") or 0)
    except (KeyError, TypeError, ValueError) as exc:
        raise BasalUnavailable(f"unexpected response: {exc!r}") from exc
    return BasalFlag(p_pattern=p_pattern, family=str(fam["choice"]),
                     family_conf=float(fam.get("confidence") or 0), consistent=consistent,
                     latency_ms=latency, raw={"scope": probs})


async def ask_flag(ep: Episode, why: str | None, url: str, timeout: float,
                   client: httpx.AsyncClient | None = None) -> BasalFlag:
    """One mixed request (scope + family + validator_consistent). Raises BasalUnavailable."""
    payload = {"state": build_state(ep, why), "questions": QUESTIONS}
    endpoint = url.rstrip("/") + "/v1/systemone"
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=timeout) as c:
                resp = await c.post(endpoint, json=payload)
        else:
            resp = await client.post(endpoint, json=payload, timeout=timeout)
        body = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise BasalUnavailable(f"{type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise BasalUnavailable(f"HTTP {resp.status_code}")
    return parse_flag(body)
