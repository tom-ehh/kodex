from kodex.schema.assemble import FormDraft, assemble
from kodex.schema.form import validate_form_definition


def F(label, kind="text", required=True):
    return {"label": label, "kind": kind, "required": required}


def _form(fields):
    draft = FormDraft(
        facts={"title": "Wniosek o test", "addressee": "Wójt Gminy X", "filers": ["person"]},
        outline={"pages": [{"title": "Dane", "sections": [{"title": "Adres zamieszkania", "purpose": "x"}]}]},
        sections=[{"title": "Adres zamieszkania", "fields": fields}],
    )
    form = assemble(draft).form
    assert validate_form_definition(form) == []
    return [q["field"] for q in form["tabs"][0]["questions"][0]["field"]["repeatingSectionGrid"]]


def test_levels_collapse_into_one_teryt_select():
    fields = _form([F("Kraj", "country"), F("Województwo", "voivodeship"), F("Powiat", "county"),
                    F("Gmina", "commune"), F("Miejscowość", "locality"), F("Ulica")])
    assert [f["type"] for f in fields] == ["SELECT", "SELECT", "TEXT_FIELD"]
    teryt = fields[1]
    assert teryt["dictionaryName"] == "TERYT_4G_DOWODOWY"
    assert teryt["multiLevelTree"] is True and teryt["options"] == []
    assert '"Polska" in' in teryt["states"][0]["query"]
    assert fields[0]["techName"] in teryt["states"][0]["query"]


def test_locality_alone_keeps_its_dictionary():
    fields = _form([F("Miejscowość", "locality"), F("Ulica")])
    assert fields[0]["dictionaryName"] == "Miejscowość"


def test_no_country_no_state():
    fields = _form([F("Gmina", "commune"), F("Miejscowość", "locality")])
    assert len(fields) == 1 and fields[0]["states"] == []
