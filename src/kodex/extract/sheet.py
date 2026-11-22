"""XLSX (openpyxl) and ODS (odfpy): one page per sheet, cells keep their address."""

import io
import zipfile

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter, range_boundaries

from kodex.extract.model import Cell, Document, ExtractionError, Page, Source

MAX_ROWS, MAX_COLS = 500, 60


def extract_xlsx(data: bytes) -> Document:
    try:
        wb = load_workbook(io.BytesIO(data), data_only=True)
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        raise ExtractionError("XLSX could not be opened") from exc
    doc = Document(kind="xlsx", pages=[])
    for n, ws in enumerate(wb.worksheets, start=1):
        if ws.sheet_state != "visible":
            continue
        choices = _validations(ws)
        spans: dict[str, tuple[int, int]] = {}
        covered: set[tuple[int, int]] = set()
        for rng in ws.merged_cells.ranges:
            c0, r0, c1, r1 = rng.bounds
            spans[f"{get_column_letter(c0)}{r0}"] = (r1 - r0 + 1, c1 - c0 + 1)
            covered |= {
                (r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)
            } - {(r0, c0)}
        cells: list[Cell] = []
        for row in ws.iter_rows(
            max_row=min(ws.max_row, MAX_ROWS), max_col=min(ws.max_column, MAX_COLS)
        ):
            for c in row:
                if (c.row, c.column) in covered:
                    continue
                ref = c.coordinate
                value = "" if c.value is None else str(c.value).strip()
                b = c.border
                bordered = bool(
                    b and any(s and s.style for s in (b.left, b.right, b.top, b.bottom))
                )
                ch = choices.get(ref)
                if value or bordered or ch:
                    cells.append(
                        Cell(
                            ref,
                            c.row,
                            c.column,
                            value,
                            spans.get(ref, (1, 1)),
                            bordered,
                            ch,
                        )
                    )
        doc.pages.append(_page(n, ws.title, cells))
    return doc


def _validations(ws) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for dv in ws.data_validations.dataValidation:
        if dv.type != "list" or not dv.formula1:
            continue
        f = dv.formula1.strip()
        if f.startswith('"'):
            items = [s.strip() for s in f.strip('"').split(",") if s.strip()]
        else:  # range reference like Sheet2!$A$1:$A$5 or $A$1:$A$5
            items = _range_values(ws, f)
        for rng in dv.sqref.ranges:
            c0, r0, c1, r1 = rng.bounds
            for r in range(r0, min(r1, MAX_ROWS) + 1):
                for c in range(c0, min(c1, MAX_COLS) + 1):
                    out[f"{get_column_letter(c)}{r}"] = items
    return out


def _range_values(ws, formula: str) -> list[str]:
    try:
        sheet, _, ref = formula.lstrip("=").rpartition("!")
        target = ws.parent[sheet.strip("'")] if sheet else ws
        c0, r0, c1, r1 = range_boundaries(ref.replace("$", ""))
    except KeyError, ValueError, TypeError:
        return []
    vals = []
    for row in target.iter_rows(
        min_row=r0, max_row=r1, min_col=c0, max_col=c1, values_only=True
    ):
        vals += [str(v).strip() for v in row if v not in (None, "")]
    return vals


def extract_ods(data: bytes) -> Document:
    from odf.namespaces import TABLENS, TEXTNS
    from odf.opendocument import load
    from odf.table import Table

    try:
        odf = load(io.BytesIO(data))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise ExtractionError("ODS could not be opened") from exc

    def attr(el, name: str, default: str = "1") -> int:
        v = el.attributes.get((TABLENS, name))
        return int(v) if v and v.isdigit() else int(default)

    def text_of(el) -> str:
        parts = []
        for p in el.childNodes:
            if p.qname == (TEXTNS, "p"):
                parts.append(str(p))
        return "\n".join(parts).strip()

    doc = Document(kind="ods", pages=[])
    for n, table in enumerate(odf.spreadsheet.getElementsByType(Table), start=1):
        name = table.attributes.get((TABLENS, "name")) or f"Sheet{n}"
        cells: list[Cell] = []
        r = 0
        for row in table.childNodes:
            if row.qname != (TABLENS, "table-row"):
                continue
            rep_r = min(attr(row, "number-rows-repeated"), MAX_ROWS)
            for _ in range(rep_r):
                r += 1
                if r > MAX_ROWS:
                    break
                c = 0
                for cell in row.childNodes:
                    if cell.qname[1] not in ("table-cell", "covered-table-cell"):
                        continue
                    rep_c = attr(cell, "number-columns-repeated")
                    value = text_of(cell) if cell.qname[1] == "table-cell" else ""
                    for _k in range(min(rep_c, MAX_COLS)):
                        c += 1
                        if c > MAX_COLS:
                            break
                        if value:
                            span = (
                                attr(cell, "number-rows-spanned"),
                                attr(cell, "number-columns-spanned"),
                            )
                            ref = f"{get_column_letter(c)}{r}"
                            cells.append(Cell(ref, r, c, value, span))
        doc.pages.append(_page(n, name, cells))
    return doc


def _page(n: int, name: str, cells: list[Cell]) -> Page:
    """Markdown: one line per row with non-empty cells, `[B4]` refs so the LLM can cite cells."""
    lines = [f"## {name}"]
    by_row: dict[int, list[Cell]] = {}
    for c in cells:
        by_row.setdefault(c.row, []).append(c)
    for r in sorted(by_row):
        parts = []
        for c in sorted(by_row[r], key=lambda x: x.col):
            v = c.value or ("____" if c.bordered else "")
            if c.choices:
                v += f" {{{' / '.join(c.choices)}}}"
            if v:
                parts.append(f"[{c.ref}] {v}")
        if parts:
            lines.append(" | ".join(parts))
    return Page(
        number=n,
        source=Source.NATIVE,
        markdown="\n".join(lines),
        name=name,
        cells=cells,
    )
