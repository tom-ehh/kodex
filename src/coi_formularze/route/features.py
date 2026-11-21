"""Cheap, model-free features of an extracted document."""

import re
from dataclasses import dataclass

from coi_formularze.extract.model import Document

# A fill-in spot: dotted/underscored blanks, empty checkboxes, "Label: ____".
_BLANK = re.compile(r"(?:\.{4,}|…{2,}|_{3,})")
_CHECKBOX = re.compile(r"[☐□]|\[ \]")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$", re.MULTILINE)
_EMPTY_CELL = re.compile(r"\|\s*(?=\|)")


@dataclass(frozen=True, slots=True)
class Features:
    kind: str
    pages: int
    est_fields: int
    tables: int
    scanned: bool  # some page came from OCR
    chars: int

    def summary(self) -> str:
        """Polish header line for the router prompt."""
        return (
            f"Format: {self.kind}; stron: {self.pages}; szacowana liczba pól: {self.est_fields}; "
            f"tabel: {self.tables}; skan/OCR: {'tak' if self.scanned else 'nie'}; "
            f"znaków tekstu: {self.chars}"
        )


def features_of(doc: Document) -> Features:
    text = doc.markdown
    blanks = len(_BLANK.findall(text)) + len(_CHECKBOX.findall(text))
    rows = [m.group(0) for m in _TABLE_ROW.finditer(text)]
    empty_cells = sum(len(_EMPTY_CELL.findall(r)) for r in rows)
    # Spreadsheet forms: bordered cells are the input boxes.
    bordered = sum(c.bordered and not c.value.strip() for p in doc.pages for c in p.cells)
    choices = sum(c.choices is not None for p in doc.pages for c in p.cells)
    return Features(
        kind=doc.kind,
        pages=len(doc.pages),
        est_fields=blanks + empty_cells + bordered + choices,
        tables=len(_TABLE_SEP.findall(text)),
        scanned=doc.used_ocr,
        chars=len(text),
    )
