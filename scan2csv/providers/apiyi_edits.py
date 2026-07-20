from __future__ import annotations

import base64
import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from PIL import Image

from scan2csv.config import APIYISettings

PROVIDER = "APIYI"


def prepare_upload(
    input_image: Path,
    *,
    max_side: int = 1600,
    max_bytes: int = 1_200_000,
) -> Path:
    with Image.open(input_image) as image:
        rgb = image.convert("RGB")
        rgb.thumbnail((max_side, max_side))
        tmp = Path(tempfile.mkstemp(prefix="apiyi-upload-", suffix=".jpg")[1])
        quality = 85
        rgb.save(tmp, format="JPEG", quality=quality, optimize=True)
        while tmp.stat().st_size > max_bytes and quality > 55:
            quality -= 5
            rgb.save(tmp, format="JPEG", quality=quality, optimize=True)
    return tmp


def sanitize_response(
    payload: dict[str, Any],
    *,
    settings: APIYISettings,
    image_bytes: int,
    upload_bytes: int,
    source_input: Path,
) -> dict[str, Any]:
    data = payload.get("data")
    item: dict[str, Any] = {}
    if isinstance(data, list) and data and isinstance(data[0], dict):
        item = data[0]
    return {
        "provider": PROVIDER,
        "endpoint": settings.endpoint,
        "model": settings.model,
        "size": settings.size,
        "created": payload.get("created"),
        "usage": payload.get("usage"),
        "data": [
            {
                "has_b64_json": isinstance(item.get("b64_json"), str) and bool(item.get("b64_json")),
                "url": item.get("url") if isinstance(item.get("url"), str) else None,
                "bytes": image_bytes,
            }
        ],
        "source_input": str(source_input),
        "upload_bytes": upload_bytes,
    }


def extract_image_bytes(payload: dict[str, Any]) -> bytes:
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"APIYI response has no data array: {list(payload.keys())}")
    item = data[0]
    if not isinstance(item, dict):
        raise RuntimeError("APIYI response data[0] is not an object")
    b64 = item.get("b64_json")
    if isinstance(b64, str) and b64:
        if b64.startswith("data:image/") and "base64," in b64:
            b64 = b64.split("base64,", 1)[1]
        return base64.b64decode(b64)
    url = item.get("url") or item.get("image_url")
    if isinstance(url, str) and url:
        if url.startswith("data:image/") and "base64," in url:
            return base64.b64decode(url.split("base64,", 1)[1])
        result = subprocess.run(
            ["curl", "-sS", "--fail", "-L", url],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"failed to download APIYI image URL: {result.stderr.decode(errors='replace')[:500]}"
            )
        return result.stdout
    raise RuntimeError(f"APIYI response has no image payload keys: {list(item.keys())}")


def call_apiyi(
    input_image: Path,
    response_json: Path,
    enhanced_path: Path,
    *,
    prompt: str | Path,
    settings: APIYISettings,
    prepare: bool = True,
) -> None:
    if not settings.api_key:
        raise RuntimeError("APIYI API key is not set")
    prompt_text = prompt.read_text(encoding="utf-8") if isinstance(prompt, Path) else str(prompt)
    upload = prepare_upload(input_image) if prepare else input_image
    response_json.parent.mkdir(parents=True, exist_ok=True)
    enhanced_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        cmd = [
            "curl",
            "-sS",
            "--max-time",
            str(settings.max_time_s),
            "--fail-with-body",
            "-X",
            "POST",
            settings.endpoint,
            "-H",
            f"Authorization: Bearer {settings.api_key}",
            "-F",
            f"model={settings.model}",
            "-F",
            f"prompt={prompt_text}",
            "-F",
            f"image=@{upload}",
            "-F",
            f"size={settings.size}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"APIYI curl failed: {result.stderr[:500]} {result.stdout[:500]}"
            )
        payload = json.loads(result.stdout)
        image_bytes = extract_image_bytes(payload)
        response_json.write_text(
            json.dumps(
                sanitize_response(
                    payload,
                    settings=settings,
                    image_bytes=len(image_bytes),
                    upload_bytes=upload.stat().st_size,
                    source_input=input_image,
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        enhanced_path.write_bytes(image_bytes)
    finally:
        if prepare and upload.exists() and upload != input_image:
            upload.unlink(missing_ok=True)


def call_apiyi_with_retries(
    input_image: Path,
    response_json: Path,
    enhanced_path: Path,
    *,
    prompt: str | Path,
    settings: APIYISettings,
    prepare: bool = True,
) -> None:
    last_error: Exception | None = None
    for attempt in range(1, settings.attempts + 1):
        try:
            call_apiyi(
                input_image,
                response_json,
                enhanced_path,
                prompt=prompt,
                settings=settings,
                prepare=prepare,
            )
            return
        except Exception as e:
            last_error = e
            if attempt < settings.attempts:
                time.sleep(2 * attempt)
    assert last_error is not None
    raise last_error
