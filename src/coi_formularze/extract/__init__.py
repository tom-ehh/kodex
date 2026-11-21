"""Text extraction: any supported form file -> Document (markdown pages + layout)."""

from concurrent.futures import ThreadPoolExecutor

from coi_formularze.extract.model import (
    Cell,
    Document,
    ExtractionError,
    Line,
    Page,
    Source,
)
from coi_formularze.extract.ocr import EdenAiOcr, OcrEngine
from coi_formularze.extract.pdf import extract_pdf
from coi_formularze.extract.sheet import extract_ods, extract_xlsx
from coi_formularze.extract.word import extract_doc, extract_docx
from coi_formularze.validation.sniff import FileKind

__all__ = [
    "Cell",
    "Document",
    "EdenAiOcr",
    "ExtractionError",
    "Line",
    "OcrEngine",
    "Page",
    "Source",
    "extract",
    "ocr_engine_from_settings",
]


def extract(data: bytes, kind: FileKind, ocr: OcrEngine | None = None) -> Document:
    """Extract one file whose kind was already sniffed by validation."""
    match kind:
        case FileKind.PDF:
            return extract_pdf(data, ocr.ocr if ocr else None)
        case FileKind.DOCX:
            return extract_docx(data)
        case FileKind.DOC:
            return extract_doc(data)
        case FileKind.XLSX:
            return extract_xlsx(data)
        case FileKind.ODS:
            return extract_ods(data)
        case FileKind.JPEG | FileKind.PNG:
            if ocr is None:
                raise ExtractionError("images need OCR, which is not configured")
            text = ocr.ocr(data, "image/jpeg" if kind is FileKind.JPEG else "image/png")
            return Document(
                kind=kind.value,
                pages=[Page(number=1, source=Source.OCR, markdown=text)],
            )
    raise ExtractionError(f"unsupported kind {kind}")


def extract_many(
    items: list[tuple[bytes, FileKind]], ocr: OcrEngine | None, workers: int = 4
) -> list[Document | ExtractionError]:
    def one(item: tuple[bytes, FileKind]) -> Document | ExtractionError:
        try:
            return extract(*item, ocr=ocr)
        except ExtractionError as exc:
            return exc

    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(one, items))


def ocr_engine_from_settings(settings) -> OcrEngine | None:
    key = settings.edenai_api_key
    if key is None:
        return None
    return EdenAiOcr(
        key.get_secret_value(), settings.ocr_model, timeout=settings.ocr_timeout_s
    )
