from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import httpx

from scan2csv.config import LLMSettings


def _normalize_base(url: str) -> str:
    u = url.rstrip("/")
    if u.endswith("/chat/completions"):
        return u[: -len("/chat/completions")]
    return u


def image_to_data_url(path: Path, *, max_side: int = 1280, quality: int = 80) -> str:
    from io import BytesIO

    from PIL import Image

    with Image.open(path) as im:
        rgb = im.convert("RGB")
        rgb.thumbnail((max_side, max_side))
        buf = BytesIO()
        rgb.save(buf, format="JPEG", quality=quality, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _extract_json(text: str) -> Any:
    text = text.strip()
    if not text:
        raise ValueError("empty model response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # fenced
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        return json.loads(m.group(1).strip())
    # first brace object
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError(f"cannot parse JSON from model: {text[:400]}")


class OpenAICompatLLM:
    def __init__(self, settings: LLMSettings):
        self.settings = settings
        self.base = _normalize_base(settings.base_url)

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.settings.api_key:
            h["Authorization"] = f"Bearer {self.settings.api_key}"
        return h

    def list_models(self) -> list[str]:
        url = f"{self.base}/models"
        with httpx.Client(timeout=30.0) as client:
            r = client.get(url, headers=self._headers())
            r.raise_for_status()
            data = r.json()
        items = data.get("data") or []
        return sorted({x.get("id") for x in items if isinstance(x, dict) and x.get("id")})

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        url = f"{self.base}/chat/completions"
        body = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": self.settings.temperature if temperature is None else temperature,
            "max_tokens": self.settings.max_tokens if max_tokens is None else max_tokens,
        }
        with httpx.Client(timeout=self.settings.timeout_s) as client:
            r = client.post(url, headers=self._headers(), json=body)
            if r.status_code >= 400:
                raise RuntimeError(f"LLM HTTP {r.status_code}: {r.text[:800]}")
            data = r.json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except Exception as e:
            raise RuntimeError(f"bad LLM response shape: {data!r}") from e

    def chat_json(
        self,
        *,
        system: str,
        user_text: str,
        images: list[Path] | None = None,
        temperature: float | None = None,
    ) -> Any:
        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        for p in images or []:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_to_data_url(Path(p))},
                }
            )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content if images else user_text},
        ]
        # When images present, content must be list; when not, plain string is fine.
        if images:
            messages[1]["content"] = content
        text = self.chat(messages, temperature=temperature)
        return _extract_json(text)

    def test_ping(self) -> str:
        return self.chat(
            [
                {"role": "system", "content": "Reply with exactly: OK"},
                {"role": "user", "content": "ping"},
            ],
            max_tokens=16,
            temperature=0,
        ).strip()
