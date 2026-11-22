"""Format-independent result of text extraction.

Every format ends up as pages of markdown (headings, tables, checkboxes), which is what
the later analysis steps read. Native formats add positions: PDF lines carry a bbox,
spreadsheet cells carry their address, so layout can still be mapped onto the form grid.
"""

from dataclasses import dataclass, field
from enum import StrEnum


class Source(StrEnum):
    NATIVE = "native"  # read from the file's own text layer / structure
    OCR = "ocr"  # recognised from a page image


@dataclass(slots=True)
class Line:
    text: str
    bbox: tuple[
        float, float, float, float
    ]  # x0, top, x1, bottom in PDF points, origin top-left


@dataclass(slots=True)
class Cell:
    ref: str  # "B4"
    row: int  # 1-based
    col: int  # 1-based
    value: str
    span: tuple[int, int] = (1, 1)  # rows, cols when merged
    bordered: bool = False  # input boxes in spreadsheet forms are usually bordered
    choices: list[str] | None = None  # list data validation (drop-down)


@dataclass(slots=True)
class Page:
    number: int  # 1-based; a sheet counts as a page
    source: Source
    markdown: str
    name: str | None = None  # sheet name
    width: float | None = None
    height: float | None = None
    lines: list[Line] = field(default_factory=list)
    cells: list[Cell] = field(default_factory=list)


@dataclass(slots=True)
class Document:
    kind: str
    pages: list[Page]
    warnings: list[str] = field(default_factory=list)

    @property
    def markdown(self) -> str:
        return "\n\n".join(p.markdown for p in self.pages if p.markdown)

    @property
    def used_ocr(self) -> bool:
        return any(p.source is Source.OCR for p in self.pages)


class ExtractionError(Exception):
    """The file could not be read (corrupt, encrypted, converter or OCR unavailable)."""
