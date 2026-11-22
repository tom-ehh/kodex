"""Dev tool: run extraction over data/ (native formats only, no OCR) and report per file.

uv run python tools/extract_corpus.py <main>/data [--ocr]  -> CSV on stdout
"""

import csv
import sys
import time
from pathlib import Path

from kodex.config import Settings
from kodex.extract import ExtractionError, extract, ocr_engine_from_settings
from kodex.validation.sniff import sniff

root = Path(sys.argv[1])
use_ocr = "--ocr" in sys.argv
ocr = ocr_engine_from_settings(Settings()) if use_ocr else None
w = csv.writer(sys.stdout)
w.writerow(["path", "kind", "pages", "ocr_pages", "chars", "secs", "error"])
files = sorted(p for p in (root / "forms").rglob("*") if p.is_file())
files += sorted((root / "synthetic" / "xlsx").glob("*.xlsx")) + sorted(
    (root / "derived" / "ods").glob("*.ods")
)
for p in files:
    data = p.read_bytes()
    kind = sniff(data)
    if kind is None:
        w.writerow([p.relative_to(root), "?", 0, 0, 0, 0, "unsniffable"])
        continue
    t = time.time()
    try:
        d = extract(data, kind, ocr)
        w.writerow(
            [
                p.relative_to(root),
                kind,
                len(d.pages),
                sum(pg.source == "ocr" for pg in d.pages),
                len(d.markdown),
                round(time.time() - t, 2),
                "; ".join(d.warnings),
            ]
        )
    except ExtractionError as e:
        w.writerow(
            [p.relative_to(root), kind, 0, 0, 0, round(time.time() - t, 2), str(e)]
        )
    sys.stdout.flush()
