from types import SimpleNamespace

import pytest

from coi_formularze.config import Settings
from coi_formularze.morpheus import (
    Layer,
    Skill,
    SkillStatus,
    SkillStore,
    SkillTrigger,
    active_skills,
    fold,
    install_seeds,
    load_seed_skills,
    open_store,
    pii_findings,
    shape,
)
from coi_formularze.morpheus.match import SectionTarget


def sk(id, layer=Layer.GENERAL, status=SkillStatus.ACTIVE, **trig):
    return Skill(id=id, name=id, layer=layer, status=status, description="d",
                 trigger=SkillTrigger(**trig))


@pytest.mark.parametrize(("value", "expected"), [
    ("00-950", "##-###"), ("Jan Kowalski", "Aaa Aaaaaaaa"), ("ąĘ", "aA"), ("", ""),
])
def test_shape(value, expected):
    assert shape(value) == expected
    assert not pii_findings(shape(value))


@pytest.mark.parametrize("text", [
    "44051401359", "jan@example.com", "00-950", "123-456-32-18", "600 700 800",
    "+48 600-700-800", "123456",
])
def test_pii_found(text):
    assert pii_findings({"a": [text]})


def test_pii_clean_and_skip_keys():
    assert not pii_findings({"x": "Kod NN-NNN, regex ^\\d{2}-\\d{3}$", "id": "ep_1234567"})


def test_fold_polish():
    assert fold("  Łódź  Ulica ŻÓŁTA ") == "lodz ulica zolta"


def test_match_keyword_diacritics_validator_regex_and_cap():
    skills = [
        sk("a-kw", labels_any=["ulica żółta"]),
        sk("b-val", validator="PESEL"),
        sk("c-rx", regex=r"\bnip\b"),
        sk("d-empty"),
        sk("e-retired", status=SkillStatus.RETIRED, labels_any=["ulica"]),
    ]
    t = SectionTarget.of(["ULICA ZOLTA", "NIP"], ["pesel"])
    assert [s.id for s in active_skills(t, skills)] == ["b-val", "a-kw", "c-rx"]
    assert len(active_skills(t, skills, max_per_section=1)) == 1


def test_match_order_layer_then_id():
    skills = [sk("z", Layer.SEED, labels_any=["x"]), sk("y", Layer.EPISODIC, labels_any=["x"]),
              sk("b", Layer.GENERAL, labels_any=["x"]), sk("a", Layer.GENERAL, labels_any=["x"])]
    assert [s.id for s in active_skills(["x"], skills)] == ["y", "a", "b", "z"]


def test_match_document():
    doc = SimpleNamespace(markdown="Wniosek\n\nKod pocztowy: ____\nNumer PESEL")
    ids = [s.id for s in active_skills(doc, load_seed_skills())]
    assert ids == ["seed-pesel", "seed-zip-code"]


def test_seeds_load_and_install(tmp_path):
    seeds = load_seed_skills()
    assert 5 <= len(seeds) <= 10 and all(s.layer is Layer.SEED for s in seeds)
    assert all(len(s.prompt_snippet) <= 400 and not pii_findings(s.model_dump(mode="json"))
               for s in seeds)
    store = SkillStore(tmp_path / "m.sqlite")
    assert len(install_seeds(store)) == len(seeds)
    assert install_seeds(store) == [] and len(store.skillsets()) == 1
    t = SectionTarget.of(["Brak numeru lokalu", "Telefon (nieobowiązkowo)"])
    ids = {s.id for s in active_skills(t, store.active())}
    assert {"seed-brak-checkbox", "seed-phone-optional", "seed-marked-optional"} <= ids


def test_disabled_never_opens_db(tmp_path):
    db = tmp_path / "nope" / "m.sqlite"
    assert open_store(Settings(morpheus_enabled=False, morpheus_db=str(db))) is None
    assert not db.parent.exists()


def test_enabled_opens_and_seeds(tmp_path):
    store = open_store(Settings(morpheus_enabled=True, morpheus_db=str(tmp_path / "m.sqlite")))
    assert len(store.active()) == len(load_seed_skills())
