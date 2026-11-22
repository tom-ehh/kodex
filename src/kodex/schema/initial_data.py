"""Extension I, initial data: prefill the applicant's fields from registries.

Crude and deterministic: the section title says whose data it is (only the applicant's
own sections qualify), the field label or kind says which registry attribute. A field
that matches gets `readOnly`, `defaultValue: "<SOURCE>.<attr>"` and
`defaultValueType: "EXTERNAL_SYSTEM"`. No match leaves the field alone.
"""

import re

from kodex.schema.drafts import FieldDraft

# Sections about someone other than the applicant never take the applicant's data.
_OTHER = re.compile(
    r"inn(ej|ego|ych) osob|pełnomocnik|reprezentant|podmiot|firm|współmałżon|małżon|"
    r"właściciel|spadkodawc|dziec|zmarł|siedzib|instytucj|jednostk",
    re.IGNORECASE,
)
_PERSON = re.compile(r"twoje dane|dane wnioskodawcy|dane osobowe|wnioskodawc", re.IGNORECASE)
_ADDRESS = re.compile(r"adres (zamieszkania|do korespondencji)|adres.*wnioskodawc", re.IGNORECASE)
_CONTACT = re.compile(r"kontakt", re.IGNORECASE)

_PERSON_FIELDS = [  # (label regex, kind, attribute)
    (re.compile(r"^imi(ę|ona)\b", re.IGNORECASE), None, "PESEL.name"),
    (re.compile(r"^nazwisko", re.IGNORECASE), None, "PESEL.surname"),
    (None, "pesel", "PESEL.pesel"),
]
_ADDRESS_FIELDS = [
    (None, "zip_code", "MLPZ.postCode"),
    (re.compile(r"kod pocztowy", re.IGNORECASE), None, "MLPZ.postCode"),
    (re.compile(r"^ulica", re.IGNORECASE), None, "MLPZ.street"),
    (re.compile(r"numer (budynku|domu)|nr domu", re.IGNORECASE), None, "MLPZ.building"),
    (
        re.compile(r"numer (lokalu|mieszkania)|nr lokalu", re.IGNORECASE),
        None,
        "MLPZ.apartment",
    ),
]
_CONTACT_FIELDS = [
    (None, "phone", "MLPZ.phoneNumber"),
    (None, "email", "MLPZ.email"),
]


def source_for(section_title: str, draft: FieldDraft, repeating: bool = False) -> str | None:
    """`SOURCE.attr` for a drafted field, or None when it shouldn't be prefilled."""
    own = re.search(r"wnioskodawc|twoje", section_title, re.IGNORECASE)
    if repeating or (_OTHER.search(section_title) and not own):
        return None
    if _ADDRESS.search(section_title):
        rules = _ADDRESS_FIELDS
    elif _PERSON.search(section_title):
        rules = _PERSON_FIELDS
    elif _CONTACT.search(section_title):
        rules = _CONTACT_FIELDS
    else:
        return None
    for label, kind, attr in rules:
        if (kind and draft.kind == kind) or (label and label.search(draft.label)):
            return attr
    return None


_OFFICE_SECTION = re.compile(r"urz[ąa]d|organ|adresat", re.IGNORECASE)
_OFFICE_FIELD = re.compile(r"nazwa|urz[ąa]d|organ", re.IGNORECASE)


def office_name(section_title: str, draft: FieldDraft) -> bool:
    """True for the office-name field: read-only, filled with the form's addressee."""
    return (
        draft.kind in ("text", "long_text")
        and bool(_OFFICE_SECTION.search(section_title))
        and bool(_OFFICE_FIELD.search(draft.label))
    )


def apply(field: dict, attr: str) -> None:
    field.update(readOnly=True, defaultValue=attr, defaultValueType="EXTERNAL_SYSTEM")
