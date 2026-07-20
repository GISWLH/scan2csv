from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from typing import Any


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_json(path: Path, obj: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def copy_in(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest


def prompt_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "prompts"


def load_prompt(name: str) -> str:
    p = prompt_dir() / name
    return p.read_text(encoding="utf-8")


def write_csv_rows(path: Path, headers: list[str], rows: list[list[Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if headers:
            w.writerow(headers)
        for row in rows:
            w.writerow(row)
    return path


def table_to_wide_long(table: dict[str, Any]) -> tuple[list[str], list[list[Any]], list[dict[str, Any]]]:
    columns = [str(c) for c in (table.get("columns") or [])]
    rows = table.get("rows") or []
    norm_rows: list[list[Any]] = []
    for r in rows:
        if not isinstance(r, list):
            continue
        rr = list(r)
        if columns:
            if len(rr) < len(columns):
                rr = rr + [""] * (len(columns) - len(rr))
            elif len(rr) > len(columns):
                rr = rr[: len(columns)]
        norm_rows.append(rr)
    long_rows: list[dict[str, Any]] = []
    for ri, r in enumerate(norm_rows):
        for ci, col in enumerate(columns):
            long_rows.append({"row": ri, "col": col, "value": r[ci] if ci < len(r) else ""})
    return columns, norm_rows, long_rows


def write_long_csv(path: Path, long_rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["row", "col", "value"])
        w.writeheader()
        for row in long_rows:
            w.writerow(row)
    return path
