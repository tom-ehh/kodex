"""Dev tool: OCR accuracy on data/derived renders against the source PDF's text layer.

uv run python tools/bench_ocr.py <main>/data [--model ID] [--limit N] [--workers 8] > out.csv

Word-level, order-free: recall = share of the source page's words the OCR found,
precision = share of OCR words that are in the source (multiset). Markdown is stripped.
"""

import argparse
import csv
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pdfplumber

from coi_formularze.config import Settings
from coi_formularze.extract import EdenAiOcr

_WORD = re.compile(r"[0-9A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]{2,}")


def words(text: str) -> Counter:
    return Counter(w.lower() for w in _WORD.findall(text))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("data", type=Path)
    ap.add_argument("--model", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    s = Settings()
    model = a.model or s.ocr_model
    eng = EdenAiOcr(s.edenai_api_key.get_secret_value(), model, timeout=300)

    rows = [
        r
        for r in csv.DictReader(
            (a.data / "derived" / "derived.csv").read_text().splitlines()
        )
        if r["format"] in ("png", "jpg")
    ]
    rows = [r for r in rows if r["source_path"].endswith(".pdf")]
    if a.limit:
        rows = rows[: a.limit]

    def run(r: dict) -> list:
        with pdfplumber.open(a.data / r["source_path"]) as pdf:
            truth = words(pdf.pages[int(r["page"]) - 1].extract_text() or "")
        img = (a.data / r["path"]).read_bytes()
        mime = "image/png" if r["format"] == "png" else "image/jpeg"
        t = time.time()
        try:
            got, err = words(eng.ocr(img, mime)), ""
            if eng.last_finish_reason == "length":
                err = "truncated"
        except Exception as exc:  # noqa: BLE001 - report and continue
            got, err = Counter(), str(exc)
        hit = sum((truth & got).values())
        rec = hit / max(1, sum(truth.values()))
        prec = hit / max(1, sum(got.values()))
        return [
            r["path"],
            r["variant"],
            sum(truth.values()),
            round(rec, 3),
            round(prec, 3),
            round(time.time() - t, 1),
            err,
        ]

    w = csv.writer(sys.stdout)
    w.writerow(
        ["path", "variant", "truth_words", "recall", "precision", "secs", "error"]
    )
    with ThreadPoolExecutor(a.workers) as pool:
        results = list(pool.map(run, rows))
    w.writerows(results)
    ok = [x for x in results if not x[6]]
    for v in sorted({x[1] for x in ok}):
        sub = [x for x in ok if x[1] == v]
        print(
            f"{model} {v}: n={len(sub)} recall={sum(x[3] for x in sub) / len(sub):.3f} "
            f"precision={sum(x[4] for x in sub) / len(sub):.3f}",
            file=sys.stderr,
        )
    print(f"errors={len(results) - len(ok)} cost=${eng.cost:.4f}", file=sys.stderr)


if __name__ == "__main__":
    main()
