"""ZIP narrowing: seed and learned ZIP skills apply only in Polish address sections."""

from kodex.morpheus import (
    Layer,
    Skill,
    SkillStore,
    SkillTrigger,
    install_seeds,
)
from kodex.morpheus.gate import lint
from kodex.morpheus.hooks import Hooks
from kodex.morpheus.match import ZIP_SCOPE, scope_ok
from kodex.morpheus.models import Episode, EpisodeKind, ValidatorRule
from kodex.morpheus.writer import PatchDraft, RuleDraft, assemble

LEARNED_ZIP = Skill(  # as P2 learned it, before scopes existed: no scope at all
    id="ep-zip-code-hyphen", name="zip-code-hyphen", layer=Layer.EPISODIC, description="zip",
    trigger=SkillTrigger(labels_any=["kod pocztowy"], validator="ZIP_CODE"),
    prompt_snippet="Kod pocztowy ##-###, walidator ZIP_CODE.",
    validator_rule=ValidatorRule(add_validators=["ZIP_CODE"], post_fix="zip_code"))


def text_field():
    return [{"field": {"type": "TEXT_FIELD", "title": "Kod pocztowy", "validation": ["REQUIRED"]}}]


def hooks(tmp_path):
    s = SkillStore(tmp_path / "m.sqlite")
    install_seeds(s)
    s.create(LEARNED_ZIP)
    return Hooks(s)


def test_scope_ok_rules():
    assert scope_ok(LEARNED_ZIP, "Adres zamieszkania\nKod pocztowy", "Adres zamieszkania")
    assert not scope_ok(LEARNED_ZIP, "Dane wnioskodawcy\nKod pocztowy", "Dane wnioskodawcy")
    assert not scope_ok(LEARNED_ZIP, "Adres zagraniczny\nKod pocztowy", "Adres zagraniczny")
    assert not scope_ok(LEARNED_ZIP, None, None)  # unknown section: a scoped skill stays off
    other = LEARNED_ZIP.model_copy(update={"trigger": SkillTrigger(labels_any=["pesel"], validator="PESEL")})
    assert scope_ok(other, None, None)


def test_hints_only_in_address_sections(tmp_path):
    h = hooks(tmp_path)
    ids = {s.id for s in h.section_skills("Adres do korespondencji", "", "Kod pocztowy ___")}
    assert {"seed-zip-code", "ep-zip-code-hyphen"} <= ids
    assert not {s.id for s in h.section_skills("Dane podmiotu", "", "Kod pocztowy ___")} & \
        {"seed-zip-code", "ep-zip-code-hyphen"}
    foreign = h.section_skills("Adres zagraniczny", "", "Miejscowość\nKod pocztowy ___")
    assert not {s.id for s in foreign} & {"seed-zip-code", "ep-zip-code-hyphen"}


def test_rules_need_the_fields_section(tmp_path):
    h = hooks(tmp_path)
    h.section_skills("Adres zamieszkania", "", "Ulica\nKod pocztowy")
    q = text_field()
    assert "ep-zip-code-hyphen" in {s.id for s in h.apply_rules("Kod pocztowy", q, "Adres zamieszkania")}
    assert "ZIP_CODE" in q[0]["field"]["validation"]
    q = text_field()
    assert h.apply_rules("Kod pocztowy", q, "Dane wnioskodawcy") == []
    assert "ZIP_CODE" not in q[0]["field"]["validation"]
    q = text_field()  # foreign label inside an address section
    assert h.apply_rules("Kod pocztowy (adres zagraniczny)", q, "Adres zamieszkania") == []
    q = text_field()  # section unknown: no ZIP rule
    assert h.apply_rules("Kod pocztowy", q) == []


def test_writer_scopes_zip_and_lint_requires_it():
    ep = Episode(kind=EpisodeKind.WORKER_CORRECTION, field_label="Kod pocztowy",
                 validator="ZIP_CODE", before_shape="#####", after_shape="##-###",
                 edited_attr="value")
    draft = PatchDraft(name="zip", description="kod", labels_any=["kod pocztowy"],
                       prompt_snippet="Kod pocztowy ##-###.",
                       validator_rule=RuleDraft(add_validators=["ZIP_CODE"], post_fix="zip_code"))
    sk = assemble(draft, ep, "zip_code", {}, None)
    assert sk.scope["section_any"] == ZIP_SCOPE["section_any"]
    assert sk.scope["section_none"] == ZIP_SCOPE["section_none"]
    assert "zip skill without an address section scope" not in lint(sk).reasons
    assert "zip skill without an address section scope" in lint(LEARNED_ZIP).reasons
