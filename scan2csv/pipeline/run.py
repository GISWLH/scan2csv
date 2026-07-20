from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any, Callable

from scan2csv.config import AppSettings
from scan2csv.events import BUS, utc_now
from scan2csv.pipeline.ingest import normalize_to_png
from scan2csv.pipeline.io_util import (
    load_prompt,
    read_json,
    table_to_wide_long,
    write_csv_rows,
    write_json,
    write_long_csv,
    write_text,
)
from scan2csv.providers.apiyi_edits import call_apiyi_with_retries
from scan2csv.providers.llm_openai import OpenAICompatLLM
from scan2csv.providers.ocr_local import draw_overlay, run_ocr


Emit = Callable[..., Any]


def _default_perceive(skeleton: str, notes: str) -> dict[str, Any]:
    return {
        "layout": {
            "n_tables": 1,
            "orientation_deg": 0,
            "reading_order": "ltr_ttb",
            "has_handwriting": False,
            "header_rows": 1,
            "stub_cols": 1,
            "notes": notes or "fallback without LLM",
        },
        "page_schema": {
            "tables": [
                {
                    "id": "t0",
                    "title_guess": "",
                    "columns": [],
                    "row_role": "records",
                    "matrix": None,
                }
            ]
        },
        "preprocess": {"rotate_cw": 0, "crops": [], "drop_regions": []},
        "enhance_prompt": skeleton,
        "enhance_params": {"size": "3840x2160"},
        "confidence": 0.2,
    }


def _heuristic_structure(ocr_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Very rough: sort boxes top-to-bottom, group by y into rows."""
    if not ocr_items:
        return {
            "table_id": "t0",
            "columns": ["c0"],
            "rows": [],
            "cells": [],
            "notes": "no ocr",
        }

    def cy(it: dict[str, Any]) -> float:
        box = it.get("box") or []
        ys = [p[1] for p in box] or [0.0]
        return sum(ys) / len(ys)

    def cx(it: dict[str, Any]) -> float:
        box = it.get("box") or []
        xs = [p[0] for p in box] or [0.0]
        return sum(xs) / len(xs)

    items = sorted(ocr_items, key=lambda it: (cy(it), cx(it)))
    rows_groups: list[list[dict[str, Any]]] = []
    for it in items:
        if not rows_groups:
            rows_groups.append([it])
            continue
        prev = rows_groups[-1][0]
        if abs(cy(it) - cy(prev)) < 18:
            rows_groups[-1].append(it)
        else:
            rows_groups.append([it])
    for g in rows_groups:
        g.sort(key=cx)
    max_cols = max(len(g) for g in rows_groups)
    columns = [f"c{i}" for i in range(max_cols)]
    rows: list[list[str]] = []
    cells: list[dict[str, Any]] = []
    for ri, g in enumerate(rows_groups):
        row = [""] * max_cols
        for ci, it in enumerate(g[:max_cols]):
            row[ci] = str(it.get("text") or "")
            cells.append(
                {
                    "r": ri,
                    "c": ci,
                    "value": row[ci],
                    "ocr_ids": [it.get("id")],
                    "conf": it.get("conf"),
                    "status": "ok" if (it.get("conf") or 0) >= 0.7 else "low_conf",
                }
            )
        rows.append(row)
    return {
        "table_id": "t0",
        "columns": columns,
        "rows": rows,
        "cells": cells,
        "notes": "heuristic structure",
    }


def _apply_patches(table: dict[str, Any], patches: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [list(r) for r in (table.get("rows") or [])]
    for p in patches or []:
        try:
            r, c = int(p["r"]), int(p["c"])
            while len(rows) <= r:
                rows.append([])
            row = rows[r]
            while len(row) <= c:
                row.append("")
            row[c] = p.get("to", "")
            # update cells meta if present
            for cell in table.get("cells") or []:
                if cell.get("r") == r and cell.get("c") == c:
                    cell["value"] = row[c]
                    cell["status"] = "ok"
        except Exception:
            continue
    table = dict(table)
    table["rows"] = rows
    return table


def run_job(
    job_id: str,
    source_path: Path,
    work_dir: Path,
    settings: AppSettings,
    *,
    bus=BUS,
    cancel_flag: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    page_dir = work_dir / "page-001"
    page_dir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}
    started = utc_now()

    def emit(step: str, status: str, **kwargs: Any) -> None:
        bus.emit(job_id, step, status, **kwargs)

    def cancelled() -> bool:
        return bool(cancel_flag and cancel_flag())

    manifest: dict[str, Any] = {
        "job_id": job_id,
        "page_id": "page-001",
        "status": "running",
        "schema_id": "runtime:page-001",
        "preset_applied": settings.pipeline.preset,
        "pipeline": [
            "ingest",
            "agent_perceive",
            "enhance",
            "ocr",
            "agent_structure",
            "agent_review",
            "export",
        ],
        "providers": {},
        "artifacts": {},
        "started_at": started,
        "finished_at": None,
        "error": None,
    }

    try:
        # --- ingest ---
        emit("ingest", "running", progress=0.05, message="normalize image")
        if cancelled():
            raise RuntimeError("cancelled")
        src_png = page_dir / "source.png"
        thumb = page_dir / "source_thumb.jpg"
        normalize_to_png(source_path, src_png, thumb)
        manifest["artifacts"]["source"] = str(src_png.relative_to(work_dir))
        emit("ingest", "done", progress=0.08, artifacts=[str(src_png)])

        skeleton = load_prompt("enhance_skeleton.md")
        llm = None
        if not settings.pipeline.skip_llm and settings.llm.api_key:
            llm = OpenAICompatLLM(settings.llm)

        # --- perceive ---
        emit("agent_perceive", "running", progress=0.12, message="multimodal layout + prompt")
        if cancelled():
            raise RuntimeError("cancelled")
        if llm:
            user = (
                "Analyze this table image and produce the JSON.\n"
                f"User notes: {settings.pipeline.user_notes or '(none)'}\n"
                f"Optional preset: {settings.pipeline.preset or '(none)'}\n"
            )
            perceive = llm.chat_json(
                system=load_prompt("perceive_sys.md"),
                user_text=user,
                images=[thumb],
            )
            if not isinstance(perceive, dict):
                raise RuntimeError("perceive did not return object")
            if not perceive.get("enhance_prompt"):
                perceive["enhance_prompt"] = skeleton
            manifest["providers"]["llm"] = {
                "base_url": settings.llm.base_url,
                "model": settings.llm.model,
                "vision": True,
            }
        else:
            perceive = _default_perceive(skeleton, settings.pipeline.user_notes)
            emit(
                "agent_perceive",
                "running",
                message="LLM skipped — using enhance skeleton",
            )
        write_json(page_dir / "perceive.json", perceive)
        write_json(page_dir / "page_schema.json", perceive.get("page_schema") or {})
        prompt_path = write_text(
            page_dir / "enhance_prompt.txt",
            str(perceive.get("enhance_prompt") or skeleton),
        )
        emit(
            "agent_perceive",
            "done",
            progress=0.22,
            artifacts=["page-001/perceive.json", "page-001/enhance_prompt.txt"],
            payload={"confidence": perceive.get("confidence")},
        )

        # --- enhance ---
        enhanced = page_dir / "enhanced.png"
        if settings.pipeline.skip_enhance or not settings.apiyi.api_key:
            emit(
                "enhance",
                "running",
                progress=0.3,
                message="skip APIYI — copy source as enhanced",
            )
            from shutil import copy2

            copy2(src_png, enhanced)
            write_json(
                page_dir / "enhance_response_meta.json",
                {"skipped": True, "reason": "skip_enhance or missing APIYI key"},
            )
        else:
            emit("enhance", "running", progress=0.3, message="APIYI image edits")
            if cancelled():
                raise RuntimeError("cancelled")
            # optional size from perceive
            ep = perceive.get("enhance_params") or {}
            if isinstance(ep, dict) and ep.get("size"):
                settings.apiyi.size = str(ep["size"])
            call_apiyi_with_retries(
                src_png,
                page_dir / "enhance_response_meta.json",
                enhanced,
                prompt=prompt_path,
                settings=settings.apiyi,
            )
            manifest["providers"]["enhance"] = {
                "provider": "apiyi",
                "model": settings.apiyi.model,
                "size": settings.apiyi.size,
            }
        emit("enhance", "done", progress=0.5, artifacts=["page-001/enhanced.png"])
        manifest["artifacts"]["enhanced"] = "page-001/enhanced.png"

        # --- ocr ---
        emit("ocr", "running", progress=0.55, message="local OCR")
        if cancelled():
            raise RuntimeError("cancelled")
        ocr = run_ocr(enhanced, engine=settings.ocr.engine)
        ocr_path = page_dir / "ocr_raw.json"
        ocr.save(ocr_path)
        overlay = page_dir / "ocr_overlay.png"
        try:
            draw_overlay(enhanced, ocr, overlay)
        except Exception:
            overlay = None
        ocr_items = ocr.to_jsonable()["items"]
        write_json(page_dir / "ocr_items.json", ocr_items)
        manifest["providers"]["ocr"] = {"engine": ocr.engine}
        arts = ["page-001/ocr_raw.json"]
        if overlay:
            arts.append("page-001/ocr_overlay.png")
        emit("ocr", "done", progress=0.68, artifacts=arts, message=f"{len(ocr_items)} boxes ({ocr.engine})")

        # --- structure ---
        emit("agent_structure", "running", progress=0.72, message="assemble table")
        if cancelled():
            raise RuntimeError("cancelled")
        schema = read_json(page_dir / "page_schema.json")
        if llm:
            # cap ocr payload size
            slim = [
                {
                    "id": it["id"],
                    "text": it["text"],
                    "conf": it["conf"],
                    "box": it["box"],
                }
                for it in ocr_items[:400]
            ]
            user = (
                "Build the table JSON from schema + OCR.\n"
                f"page_schema={schema}\n"
                f"ocr_items={slim}\n"
                "If OCR is empty, read the image carefully but still avoid inventing digits.\n"
            )
            images = [page_dir / "source_thumb.jpg"]
            ethumb = page_dir / "enhanced_thumb.jpg"
            try:
                from PIL import Image

                with Image.open(enhanced) as im:
                    im = im.convert("RGB")
                    im.thumbnail((1280, 1280))
                    im.save(ethumb, format="JPEG", quality=85)
                images = [ethumb]
            except Exception:
                pass
            table = llm.chat_json(
                system=load_prompt("structure_sys.md"),
                user_text=user,
                images=images,
            )
            if not isinstance(table, dict):
                raise RuntimeError("structure did not return object")
        else:
            table = _heuristic_structure(ocr_items)
        write_json(page_dir / "table.json", table)
        cols, rows, long_rows = table_to_wide_long(table)
        write_csv_rows(page_dir / "draft_wide.csv", cols, rows)
        write_long_csv(page_dir / "draft_long.csv", long_rows)
        emit(
            "agent_structure",
            "done",
            progress=0.85,
            artifacts=["page-001/table.json", "page-001/draft_wide.csv"],
        )

        # --- review ---
        emit("agent_review", "running", progress=0.88, message="review draft")
        review: dict[str, Any]
        if llm:
            user = (
                "Review this draft extraction.\n"
                f"table={table}\n"
                f"low_conf={[c for c in (table.get('cells') or []) if c.get('status') in {'low_conf','conflict'}][:80]}\n"
            )
            review = llm.chat_json(
                system=load_prompt("review_sys.md"),
                user_text=user,
                images=[thumb],
            )
            if not isinstance(review, dict):
                review = {"decision": "accept", "patches": [], "risk_cells": [], "summary_md": "bad review json"}
        else:
            review = {
                "decision": "accept",
                "patches": [],
                "risk_cells": [
                    c
                    for c in (table.get("cells") or [])
                    if c.get("status") in {"low_conf", "conflict"}
                ][:50],
                "summary_md": "LLM skipped; accepted heuristic/draft",
            }
        write_json(page_dir / "agent_review.json", review)
        decision = str(review.get("decision") or "accept")
        final_table = table
        if decision == "patch" or review.get("patches"):
            final_table = _apply_patches(table, list(review.get("patches") or []))
        # re_structure / re_enhance: record intent; full loop can be added later within budget
        if decision in {"re_structure", "re_enhance"} and settings.pipeline.max_review_rounds < 1:
            pass
        write_json(page_dir / "final_table.json", final_table)
        fcols, frows, flong = table_to_wide_long(final_table)
        write_csv_rows(page_dir / "final_wide.csv", fcols, frows)
        write_long_csv(page_dir / "final_long.csv", flong)
        emit(
            "agent_review",
            "done",
            progress=0.95,
            payload={"decision": decision},
            artifacts=["page-001/final_wide.csv", "page-001/agent_review.json"],
        )

        # --- export ---
        emit("export", "running", progress=0.97, message="write manifest")
        manifest["status"] = "succeeded"
        manifest["finished_at"] = utc_now()
        manifest["artifacts"].update(
            {
                "final_wide": "page-001/final_wide.csv",
                "final_long": "page-001/final_long.csv",
                "page_schema": "page-001/page_schema.json",
                "review": "page-001/agent_review.json",
            }
        )
        manifest["timings_s"] = timings
        write_json(page_dir / "manifest.json", manifest)
        write_json(work_dir / "job.json", {"job_id": job_id, "status": "succeeded", "manifest": manifest})
        emit("export", "done", progress=1.0, artifacts=["page-001/manifest.json"])
        emit("job", "succeeded", progress=1.0, message="done")
        return manifest

    except Exception as e:
        manifest["status"] = "cancelled" if str(e) == "cancelled" else "failed"
        manifest["error"] = f"{e}\n{traceback.format_exc()[-2000:]}"
        manifest["finished_at"] = utc_now()
        try:
            write_json(page_dir / "manifest.json", manifest)
            write_json(work_dir / "job.json", {"job_id": job_id, "status": manifest["status"], "manifest": manifest})
        except Exception:
            pass
        emit("job", manifest["status"], message=str(e), payload={"error": str(e)})
        return manifest
