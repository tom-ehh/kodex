"""Legacy/ODF conversion with LibreOffice headless (only DOC needs it)."""

import shutil
import subprocess
import tempfile
from pathlib import Path

from coi_formularze.extract.model import ExtractionError

TIMEOUT = 120


def convert(data: bytes, src_ext: str, target: str) -> bytes:
    """Convert bytes with `soffice --convert-to <target>`; files live only in a temp dir."""
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        raise ExtractionError(f"{src_ext} needs LibreOffice, which is not installed")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"in.{src_ext}"
        src.write_bytes(data)
        cmd = [
            exe,
            f"-env:UserInstallation=file://{tmp}/profile",
            "--headless",
            "--convert-to",
            target,
            "--outdir",
            tmp,
            str(src),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=TIMEOUT, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ExtractionError("LibreOffice conversion timed out") from exc
        out = Path(tmp) / f"in.{target.split(':')[0]}"
        if not out.exists():
            raise ExtractionError(f"LibreOffice could not convert the {src_ext} file")
        return out.read_bytes()
