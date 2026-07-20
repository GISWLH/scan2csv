from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class OcrBox:
    id: int
    text: str
    conf: float
    box: list[list[float]]  # 4 points


@dataclass
class OcrResult:
    engine: str
    items: list[OcrBox] = field(default_factory=list)
    raw: Any = None

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "items": [asdict(x) for x in self.items],
        }

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_jsonable(), ensure_ascii=False, indent=2), encoding="utf-8")


def run_ocr(image_path: Path, *, engine: str = "auto") -> OcrResult:
    """Try PP-OCRv6 / PaddleOCR; else empty result (structure agent may read image)."""
    if engine in {"auto", "ppocrv6", "paddle"}:
        try:
            return _run_paddle(image_path)
        except Exception:
            if engine != "auto":
                raise
    return OcrResult(engine="none", items=[])


def _run_paddle(image_path: Path) -> OcrResult:
    # Prefer paddleocr classic API
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except Exception as e:
        raise RuntimeError("PaddleOCR not installed") from e

    ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    raw = ocr.ocr(str(image_path), cls=True)
    items: list[OcrBox] = []
    idx = 0
    # raw is list per image
    pages = raw or []
    if pages and pages[0] is None:
        pages = []
    for page in pages:
        if not page:
            continue
        for line in page:
            box, (text, conf) = line[0], line[1]
            items.append(
                OcrBox(
                    id=idx,
                    text=str(text),
                    conf=float(conf),
                    box=[[float(p[0]), float(p[1])] for p in box],
                )
            )
            idx += 1
    return OcrResult(engine="paddleocr", items=items, raw=None)


def draw_overlay(image_path: Path, result: OcrResult, out_path: Path) -> None:
    from PIL import Image, ImageDraw

    im = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(im)
    for it in result.items:
        if len(it.box) >= 2:
            xs = [p[0] for p in it.box]
            ys = [p[1] for p in it.box]
            draw.rectangle([min(xs), min(ys), max(xs), max(ys)], outline=(255, 0, 0), width=2)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path)
