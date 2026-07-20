from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


def default_jobs_root() -> Path:
    override = os.environ.get("SCAN2CSV_JOBS_DIR")
    if override:
        return Path(override).expanduser().resolve()
    home = Path.home()
    # Prefer Documents on all desktops for easy discovery
    return home / "Documents" / "scan2csv" / "jobs"


class LLMSettings(BaseModel):
    base_url: str = Field(default="http://127.0.0.1:8015/v1")
    api_key: str = ""
    model: str = "grok-4.5"
    temperature: float = 0.2
    timeout_s: float = 180.0
    max_tokens: int = 8192


class APIYISettings(BaseModel):
    endpoint: str = "https://api.apiyi.com/v1/images/edits"
    api_key: str = ""
    model: str = "gpt-image-2-vip"
    size: str = "3840x2160"
    max_time_s: int = 420
    attempts: int = 3


class OCRSettings(BaseModel):
    engine: str = "auto"  # auto | ppocrv6 | none
    lang: str = "ch"
    device: str = "cpu"


class PipelineSettings(BaseModel):
    confirm_prompt_before_enhance: bool = False
    skip_enhance: bool = False
    skip_llm: bool = False
    max_review_rounds: int = 1
    max_re_enhance: int = 1
    keep_intermediates: bool = True
    user_notes: str = ""
    preset: str | None = None


class AppSettings(BaseModel):
    llm: LLMSettings = Field(default_factory=LLMSettings)
    apiyi: APIYISettings = Field(default_factory=APIYISettings)
    ocr: OCRSettings = Field(default_factory=OCRSettings)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    jobs_dir: str = Field(default_factory=lambda: str(default_jobs_root()))

    @classmethod
    def from_env(cls) -> "AppSettings":
        s = cls()
        if v := os.environ.get("SCAN2CSV_LLM_BASE_URL"):
            s.llm.base_url = v.rstrip("/")
        if v := os.environ.get("SCAN2CSV_LLM_API_KEY"):
            s.llm.api_key = v
        elif v := os.environ.get("OPENAI_API_KEY"):
            s.llm.api_key = v
        if v := os.environ.get("SCAN2CSV_LLM_MODEL"):
            s.llm.model = v
        if v := os.environ.get("OPENAI_BASE_URL"):
            # only if scan2csv-specific not set
            if not os.environ.get("SCAN2CSV_LLM_BASE_URL"):
                s.llm.base_url = v.rstrip("/")
        for name in ("APIYI_API_KEY", "APIYI_KEY", "API_YI_KEY"):
            if v := os.environ.get(name):
                s.apiyi.api_key = v
                break
        if v := os.environ.get("APIYI_IMAGES_EDITS_URL"):
            s.apiyi.endpoint = v
        if v := os.environ.get("APIYI_IMAGE_MODEL"):
            s.apiyi.model = v
        if v := os.environ.get("APIYI_IMAGE_SIZE"):
            s.apiyi.size = v
        if os.environ.get("SCAN2CSV_SKIP_ENHANCE", "").strip() in {"1", "true", "yes"}:
            s.pipeline.skip_enhance = True
        if os.environ.get("SCAN2CSV_SKIP_LLM", "").strip() in {"1", "true", "yes"}:
            s.pipeline.skip_llm = True
        if v := os.environ.get("SCAN2CSV_JOBS_DIR"):
            s.jobs_dir = str(Path(v).expanduser().resolve())
        return s

    def merged_with(self, data: dict[str, Any] | None) -> "AppSettings":
        if not data:
            return self
        raw = self.model_dump()
        for key, val in data.items():
            if key in raw and isinstance(val, dict) and isinstance(raw[key], dict):
                raw[key] = {**raw[key], **val}
            else:
                raw[key] = val
        return AppSettings.model_validate(raw)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # never write secrets into a shared example — caller decides path
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "AppSettings":
        data = json.loads(path.read_text(encoding="utf-8"))
        base = cls.from_env()
        return base.merged_with(data)


def redact_settings(s: AppSettings) -> dict[str, Any]:
    d = s.model_dump()
    if d.get("llm", {}).get("api_key"):
        k = d["llm"]["api_key"]
        d["llm"]["api_key"] = f"{k[:4]}…{k[-4:]}" if len(k) > 8 else "***"
    if d.get("apiyi", {}).get("api_key"):
        k = d["apiyi"]["api_key"]
        d["apiyi"]["api_key"] = f"{k[:4]}…{k[-4:]}" if len(k) > 8 else "***"
    return d
