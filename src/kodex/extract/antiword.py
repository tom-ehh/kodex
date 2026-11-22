"""Legacy Word 97-2003 (.doc) text via antiword; tables become markdown."""

import shutil
import subprocess
import tempfile
from pathlib import Path

from kodex.extract.model import ExtractionError

TIMEOUT = 60


def available() -> bool:
    return shutil.which("antiword") is not None


def doc_to_markdown(data: bytes) -> str:
    """Run antiword on the bytes (temp file only) and return markdown text."""
    exe = shutil.which("antiword")
    if not exe:
        raise ExtractionError("doc needs antiword, which is not installed")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.doc"
        src.write_bytes(data)
        try:
            res = subprocess.run(
                [exe, "-m", "UTF-8.txt", "-w", "0", str(src)],
                capture_output=True,
                timeout=TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExtractionError("antiword timed out") from exc
    text = res.stdout.decode("utf-8", errors="replace")
    if res.returncode != 0 or not text.strip():
        raise ExtractionError("antiword could not read the doc file")
    return _tables_to_md(text)


def _tables_to_md(text: str) -> str:
    """antiword draws table rows as `|a |b |`; give each run of rows a header separator."""
    out: list[str] = []
    in_table = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|") and len(s) > 1:
            cells = [c.strip() for c in s[1:-1].split("|")]
            out.append("| " + " | ".join(cells) + " |")
            if not in_table:
                out.append("|" + "---|" * len(cells))
                in_table = True
            continue
        in_table = False
        out.append(line.rstrip())
    return "\n".join(out)
