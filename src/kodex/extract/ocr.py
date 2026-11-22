"""OCR through an open-weights vision model (default: olmOCR-7B) served on EdenAI v3.

EdenAI's v3 chat endpoint is OpenAI-compatible; the page goes in as a data URL and the
model answers with markdown. Only open-weights models may be configured here (task rule:
no API-only models at runtime), so the same model could also be self-hosted.
"""

import base64
import re
from typing import Protocol

import httpx

from kodex.extract.model import ExtractionError

PROMPT = (
    "Transcribe all text on this page of a Polish form as markdown. Keep the reading order, "
    "headings, numbered sections and tables (as markdown tables). Write empty checkboxes as ☐ "
    "and ticked ones as ☒. Every dotted or dashed fill-in line (a run of dots, dots with "
    "spaces, or ellipsis characters) must be written as exactly four underscores ____ and "
    "NEVER as a run of dots; do not reproduce dot leaders. Do not repeat any line. "
    "Output only the transcription."
)


class OcrEngine(Protocol):
    def ocr(self, image: bytes, mime: str) -> str: ...


class EdenAiOcr:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.edenai.run/v3",
        timeout: float = 120.0,
        max_tokens: int = 4096,
        client: httpx.Client | None = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._client = client or httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        self.cost = 0.0  # estimated USD, summed over calls
        self.last_finish_reason: str | None = None

    def ocr(self, image: bytes, mime: str) -> str:
        url = f"data:{mime};base64,{base64.b64encode(image).decode()}"
        body = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {"type": "image_url", "image_url": {"url": url}},
                    ],
                }
            ],
        }
        try:
            resp = self._client.post("/llm/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise ExtractionError(
                f"OCR request failed: {exc.__class__.__name__}"
            ) from exc
        if resp.status_code != 200:
            raise ExtractionError(f"OCR service answered {resp.status_code}")
        data = resp.json()
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ExtractionError("OCR service returned no text") from exc
        self.cost += float((data.get("usage") or {}).get("estimated_cost") or 0)
        self.last_finish_reason = choice.get("finish_reason")
        text = _collapse_leaders(_strip_fence(text))
        if self.last_finish_reason == "length":
            # Usually a repetition loop (dot leaders); keep what was read, flagged.
            text += "\n\n<!-- OCR output truncated -->"
        return text


_LEADER = re.compile(r"(?:[.\u2026_\-]\s?){19,}[.\u2026_\-]")


def _collapse_leaders(text: str) -> str:
    """Safety net: long runs of fill characters become ____ (also cuts loop garbage)."""
    return _LEADER.sub("____", text)


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()
