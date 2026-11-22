"""Async chat client for open-weights models on EdenAI v3 (OpenAI-compatible).

Asks for JSON-schema output and no reasoning. Some providers reject either
parameter; the client then retries the call once without it. Only open-weights models
may be configured (task rule: no API-only models at runtime).
"""

from dataclasses import dataclass
from typing import Protocol

import httpx


class LlmError(Exception):
    """The model service failed (network, HTTP error, no answer)."""


@dataclass
class Completion:
    text: str
    finish: str
    cost: float
    model: str


class ChatModel(Protocol):
    async def complete(self, model: str, messages: list[dict], schema: dict) -> Completion: ...


class EdenAiChat:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.edenai.run/v3",
        timeout: float = 120.0,
        max_tokens: int = 8192,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._max_tokens = max_tokens
        self._client = client or httpx.AsyncClient(
            base_url=base_url, timeout=timeout, headers={"Authorization": f"Bearer {api_key}"}
        )

    async def complete(self, model: str, messages: list[dict], schema: dict) -> Completion:
        body: dict = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self._max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": schema, "strict": False},
            },
            "reasoning_effort": "none",
        }
        resp = await self._post(body)
        if resp.status_code in (400, 422):  # provider rejects a parameter: drop the extras
            body.pop("reasoning_effort")
            body["response_format"] = {"type": "json_object"}
            resp = await self._post(body)
        if resp.status_code != 200:
            raise LlmError(f"model service answered {resp.status_code}")
        data = resp.json()
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError("model service returned no answer") from exc
        cost = float((data.get("usage") or {}).get("estimated_cost") or 0)
        return Completion(text=text, finish=choice.get("finish_reason") or "", cost=cost, model=model)

    async def _post(self, body: dict) -> httpx.Response:
        try:
            return await self._client.post("/llm/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise LlmError(f"model request failed: {exc.__class__.__name__}") from exc
