"""Light hand corrections on pipeline output. Every change is listed in CHANGES."""
import copy, json, sys, uuid

S = sys.argv[1]
OUT = sys.argv[2]
CHANGES = {}


def load(slug):
    return json.load(open(f"{S}/{slug}.json"))["form"]


def walk(form):
    """Yield (container_list, index, field) for every question at any depth."""
    def rec(lst):
        for i, q in enumerate(lst):
            yield lst, i, q["field"]
            yield from rec(q["field"].get("repeatingSectionGrid") or [])
    for t in form["tabs"]:
        yield from rec(t["questions"])


def find(form, tech):
    for lst, i, f in walk(form):
        if f.get("techName") == tech:
            return lst, i, f
    raise KeyError(tech)


def drop(form, *techs):
    for t in techs:
        lst, i, _ = find(form, t)
        del lst[i]


def relayout(form):
    def rec(lst):
        y = 0
        for n, q in enumerate(lst):
            kids = q["field"].get("repeatingSectionGrid")
            if kids is not None:
                rec(kids)
                q["rows"] = max(1, sum(k["rows"] for k in kids))
            q["y"] = y
            q["x"] = 0
            q["field"]["questionOrder"] = n
            y += q["rows"]
    for t in form["tabs"]:
        t["questions"] = [q for q in t["questions"]
                          if q["field"].get("repeatingSectionGrid") is None or q["field"]["repeatingSectionGrid"]]
        rec(t["questions"])


def gate_query(tab, section, tech, value):
    return (f"(\"{value}\" in tabs['{tab}' in title].questions['{section}' in field.techName]"
            f".field.repeatingSectionGrid['{tech}' in field.techName].field.__userAnswer)")


def checkbox(tech, title, option):
    return {"field": {"type": "CHECKBOX", "techName": tech, "title": title,
                      "options": [{"id": str(uuid.uuid4()), "name": option, "techName": tech + "_tak"}],
                      "validation": [], "states": [], "questionOrder": 0, "id": str(uuid.uuid4())},
            "x": 0, "y": 0, "rows": 1, "cols": 3}


def show(f, query):
    f.setdefault("states", []).append({"type": "SHOW", "query": query})


def require(form, *techs):
    for t in techs:
        f = find(form, t)[2]
        if "REQUIRED" not in f["validation"]:
            f["validation"].insert(0, "REQUIRED")


def unrequire(form, *techs):
    for t in techs:
        f = find(form, t)[2]
        f["validation"] = [v for v in f["validation"] if v != "REQUIRED"]


def add_pelnomocnik_gate(form, tab, section, after_tech, gated_sections, gated_fields, log):
    lst, i, _ = find(form, after_tech)
    cb = checkbox("wniosek_przez_pelnomocnika", "Pełnomocnik", "Wniosek składa pełnomocnik")
    lst.insert(i + 1, cb)
    q = gate_query(tab, section, "wniosek_przez_pelnomocnika", "Wniosek składa pełnomocnik")
    for t in gated_sections + gated_fields:
        show(find(form, t)[2], q)
    log.append("added checkbox 'Wniosek składa pełnomocnik'; pełnomocnik data/attachments shown only when ticked "
               "(paper: fill in only if a pełnomocnik acts)")


# ---- zwrot opłaty skarbowej
f = load("zwrot-oplaty-skarbowej"); log = CHANGES["zwrot-oplaty-skarbowej"] = []
add_pelnomocnik_gate(f, "Dane wnioskodawcy", "dane_identyfikacyjne", "nip",
                     ["dane_pelnomocnika"],
                     ["dokument_stwierdzajacy_udzielenie_pelnomocnictwa",
                      "dowod_uiszczenia_oplaty_skarbowej_za_pelnomocnictwo"], log)
require(f, "nr_rachunku", "dane_wlasciciela",
        "dokument_stwierdzajacy_udzielenie_pelnomocnictwa", "dowod_uiszczenia_oplaty_skarbowej_za_pelnomocnictwo")
log.append("Nr rachunku and Dane właściciela REQUIRED when shown (bank transfer chosen); pełnomocnictwo files REQUIRED when shown")
drop(f, "data_i_podpis_wnioskodawcy")
log.append("removed 'Data i podpis' section (the e-service signs electronically and dates the submission)")
relayout(f); json.dump(f, open(f"{OUT}/zwrot-oplaty-skarbowej.json", "w"), ensure_ascii=False, indent=2)

# ---- wydanie zaświadczenia
f = load("wydanie-zaswiadczenia"); log = CHANGES["wydanie-zaswiadczenia"] = []
drop(f, "podpis_wnioskodawcy")
log.append("removed 'Podpis wnioskodawcy' section (electronic signature)")
unrequire(f, "ulica", "numer_budynku", "kod_pocztowy", "miejscowosc")
log.append("office address fields no longer REQUIRED (prefilled by the office, not the citizen's input)")
relayout(f); json.dump(f, open(f"{OUT}/wydanie-zaswiadczenia.json", "w"), ensure_ascii=False, indent=2)

# ---- wynajęcie świetlicy
f = load("wynajecie-swietlicy-wiejskiej"); log = CHANGES["wynajecie-swietlicy-wiejskiej"] = []
drop(f, "miejscowosc_i_data_podpis_i_dane_kontaktowe", "imie_i_nazwisko_wnioskodawcy_podpis_i_dane_kontaktowe",
     "adres_zamieszkania_podpis_i_dane_kontaktowe", "czytelny_podpis_wnioskodawcy_podpis_i_dane_kontaktowe",
     "czytelny_podpis_wnioskodawcy")
find(f, "podpis_i_dane_kontaktowe")[2]["title"] = "Dane kontaktowe"
log.append("removed applicant fields duplicated in 'Podpis i dane kontaktowe' and both 'Czytelny podpis' uploaders; "
           "section renamed 'Dane kontaktowe'")
relayout(f); json.dump(f, open(f"{OUT}/wynajecie-swietlicy-wiejskiej.json", "w"), ensure_ascii=False, indent=2)

# ---- zajęcie pasa drogowego
f = load("zajecie-pasa-drogowego"); log = CHANGES["zajecie-pasa-drogowego"] = []
drop(f, "zakres_i_sposob_wykonania_robot")
log.append("removed section 'Zakres i sposób wykonania robót' (duplicate of the 'Rodzaj robót i sposób wykonania' textarea)")
rs = find(f, "powierzchnia_zajecia_pasa_drogowego")[2]
rs["repeatingSectionGrid"] = [q for q in rs["repeatingSectionGrid"] if not q["field"]["techName"].endswith("_2")]
log.append("repeating 'Powierzchnia zajęcia' section: dropped the '… 2' copies of each row (repeat the section instead)")
find(f, "oplaty_skarbne")[2]["title"] = "Opłata skarbowa"
log.append("typo: 'Opłaty skarbne' -> 'Opłata skarbowa'")
add_pelnomocnik_gate(f, "Dane urzędu i wnioskodawcy", "dane_wnioskodawcy", "e_mail", ["dane_pelnomocnika"],
                     ["oryginal_pelnomocnictwa_lub_poswiadczony_odpis_pelnomocnictwa_wraz_z_oplata_skarbowa_17_zl_jesli_wystepuje_pelnomocnik"], log)
require(f, "oryginal_pelnomocnictwa_lub_poswiadczony_odpis_pelnomocnictwa_wraz_z_oplata_skarbowa_17_zl_jesli_wystepuje_pelnomocnik")
relayout(f); json.dump(f, open(f"{OUT}/zajecie-pasa-drogowego.json", "w"), ensure_ascii=False, indent=2)

# ---- psy ras agresywnych
f = load("psy-ras-agresywnych"); log = CHANGES["psy-ras-agresywnych"] = []
drop(f, "nr_sprawy", "miejsce_na_pieczec_urzedu_wypelnia_organ", "data", "wydzial")
log.append("removed office-only fields (Nr sprawy, pieczęć, data, wydział: 'wypełnia organ')")
nr = copy.deepcopy(find(f, "numer_identyfikacyjny")[2])
drop(f, "informacja_o_posiadanym_psie", "pochodzenie_psa", "rasa_psa", "data_urodzenia_psa", "data_szczepienia",
     "plec", "sposob_oznakowania_psa", "numer_identyfikacyjny", "adres_miejsca_utrzymywania_psa_psow",
     "opis_miejsca_i_warunkow_utrzymywania_psa_psow")
log.append("'Przedmiot wniosku' section: removed the dog/address/conditions fields duplicated by the repeating "
           "'Informacja o posiadanym psie' and the two sections after it")
rs = find(f, "informacja_o_posiadanym_psie_2")[2]
for q in rs["repeatingSectionGrid"]:
    q["field"]["states"] = []
nr["techName"] = "numer_identyfikacyjny_psa"; nr["id"] = str(uuid.uuid4())
_a = ("tabs['Przedmiot wniosku' in title].questions['informacja_o_posiadanym_psie_2' in field.techName]"
      ".field.__userAnswer[$repeatingSectionIndex]['sposob_oznakowania_psa_nr_ident' in fieldTechName].answers")
nr["states"] = [{"type": "SHOW", "query": f"(\"mikroprocesor\" in {_a}) or (\"tatuaż\" in {_a})"}]
nr["validation"] = ["REQUIRED"]
rs["repeatingSectionGrid"].append({"field": nr, "x": 0, "y": 0, "rows": 1, "cols": 3})
log.append("repeating dog section: dropped wrong SHOW states (breed, birth date etc. were hidden until a marking was "
           "chosen); added 'Numer identyfikacyjny' per dog, shown and REQUIRED for mikroprocesor/tatuaż")
relayout(f); json.dump(f, open(f"{OUT}/psy-ras-agresywnych.json", "w"), ensure_ascii=False, indent=2)

# ---- opłata za odpady (analysed from the DOCX; the PDF run timed out on the hard tier)
f = load("oplata-za-odpady"); log = CHANGES["oplata-za-odpady"] = []
drop(f, "nazwisko_imie_wspolmalzonka", "adres_zamieszkania_wspolmalzonka", "pesel_wspolmalzonka")
log.append("removed spouse fields inlined in 'Dane podmiotu' (same fields already in the 'Dane współmałżonka' section, same SHOW rule)")
drop(f, "podpis_skladajacego_deklaracje_osoby_reprezentujacej", "podpis_wspolmalzonka")
find(f, "podpis_skladajacego_deklaracje")[2]["title"] = "Składający deklarację"
log.append("removed the two signature uploaders (electronic signature); section renamed 'Składający deklarację'")
relayout(f); json.dump(f, open(f"{OUT}/oplata-za-odpady.json", "w"), ensure_ascii=False, indent=2)

json.dump(CHANGES, open(f"{OUT}/changes.json", "w"), ensure_ascii=False, indent=2)
print("ok")
