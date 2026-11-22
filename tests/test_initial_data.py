import pytest

from kodex.schema.drafts import FieldDraft
from kodex.schema.initial_data import source_for


def f(label, kind="text"):
    return FieldDraft(label=label, kind=kind, required=True)


@pytest.mark.parametrize(
    ("section", "draft", "want"),
    [
        ("Twoje dane", f("Imię"), "PESEL.name"),
        ("Twoje dane", f("Nazwisko"), "PESEL.surname"),
        ("Dane wnioskodawcy", f("Numer PESEL", "pesel"), "PESEL.pesel"),
        ("Adres zamieszkania", f("Kod pocztowy", "zip_code"), "MLPZ.postCode"),
        ("Adres do korespondencji", f("Ulica"), "MLPZ.street"),
        ("Adres zamieszkania", f("Numer budynku"), "MLPZ.building"),
        ("Adres zamieszkania", f("Numer lokalu"), "MLPZ.apartment"),
        ("Dane do kontaktu", f("Numer telefonu", "phone"), "MLPZ.phoneNumber"),
        ("Dane do kontaktu", f("Adres e-mail", "email"), "MLPZ.email"),
        ("Dane innej osoby", f("Imię"), None),
        ("Adres zamieszkania współmałżonka", f("Ulica"), None),
        ("Adres nieruchomości", f("Ulica"), None),
        ("Twoje dane", f("Data urodzenia", "date"), None),
        ("Dane psa", f("Imię"), None),
    ],
)
def test_source_for(section, draft, want):
    assert source_for(section, draft) == want


def test_repeating_section_skipped():
    assert source_for("Twoje dane", f("Imię"), repeating=True) is None


def test_office_name():
    from kodex.schema.initial_data import office_name

    assert office_name("Urząd, do którego jest składany wniosek", f("Nazwa urzędu"))
    assert not office_name("Twoje dane", f("Nazwa urzędu"))
    assert not office_name("Urząd", f("Data złożenia", "date"))
