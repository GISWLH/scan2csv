from __future__ import annotations

import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from scan2csv import __version__
from scan2csv.config import AppSettings, default_jobs_root, redact_settings
from scan2csv.events import BUS
from scan2csv.pipeline.run import run_job
from scan2csv.providers.llm_openai import OpenAICompatLLM

_WEB = Path(__file__).resolve().parent / "web"
_settings_lock = threading.Lock()
_settings = AppSettings.from_env()
_jobs: dict[str, dict[str, Any]] = {}
_cancel: dict[str, threading.Event] = {}


def get_settings() -> AppSettings:
    with _settings_lock:
        return _settings.model_copy(deep=True)


def set_settings(s: AppSettings) -> None:
    global _settings
    with _settings_lock:
        _settings = s


def jobs_root() -> Path:
    p = Path(get_settings().jobs_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


app = FastAPI(title="scan2csv", version=__version__)


class SettingsBody(BaseModel):
    llm: dict[str, Any] = Field(default_factory=dict)
    apiyi: dict[str, Any] = Field(default_factory=dict)
    ocr: dict[str, Any] = Field(default_factory=dict)
    pipeline: dict[str, Any] = Field(default_factory=dict)
    jobs_dir: str | None = None


class TestLLMBody(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": __version__}


@app.get("/api/settings")
def api_get_settings() -> dict[str, Any]:
    return redact_settings(get_settings())


@app.put("/api/settings")
def api_put_settings(body: SettingsBody) -> dict[str, Any]:
    cur = get_settings()
    data = body.model_dump(exclude_none=True)
    merged = cur.merged_with(data)
    set_settings(merged)
    # persist next to jobs
    path = jobs_root().parent / "settings.json"
    # save full including keys locally
    merged.save(path)
    return redact_settings(merged)


@app.post("/api/test-llm")
def api_test_llm(body: TestLLMBody | None = None) -> dict[str, Any]:
    s = get_settings()
    llm_s = s.llm.model_copy()
    if body:
        if body.base_url:
            llm_s.base_url = body.base_url.rstrip("/")
        if body.api_key:
            llm_s.api_key = body.api_key
        if body.model:
            llm_s.model = body.model
    if not llm_s.api_key:
        raise HTTPException(400, "api_key required")
    client = OpenAICompatLLM(llm_s)
    try:
        text = client.test_ping()
        return {"ok": True, "reply": text}
    except Exception as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/models")
def api_models() -> dict[str, Any]:
    s = get_settings()
    if not s.llm.api_key:
        raise HTTPException(400, "configure llm api_key first")
    try:
        ids = OpenAICompatLLM(s.llm).list_models()
        return {"models": ids}
    except Exception as e:
        raise HTTPException(502, str(e)) from e


@app.post("/api/jobs")
async def api_create_job(
    file: UploadFile = File(...),
    user_notes: str = Form(""),
    skip_enhance: str = Form(""),
    skip_llm: str = Form(""),
) -> dict[str, Any]:
    settings = get_settings()
    if user_notes:
        settings.pipeline.user_notes = user_notes
    if skip_enhance.strip() in {"1", "true", "yes"}:
        settings.pipeline.skip_enhance = True
    if skip_llm.strip() in {"1", "true", "yes"}:
        settings.pipeline.skip_llm = True

    job_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    work = jobs_root() / job_id
    work.mkdir(parents=True, exist_ok=True)
    src_dir = work / "source"
    src_dir.mkdir(parents=True, exist_ok=True)
    fname = file.filename or "upload.bin"
    dest = src_dir / Path(fname).name
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    cancel = threading.Event()
    _cancel[job_id] = cancel
    _jobs[job_id] = {"job_id": job_id, "status": "running", "work_dir": str(work)}

    def runner() -> None:
        try:
            man = run_job(
                job_id,
                dest,
                work,
                settings,
                bus=BUS,
                cancel_flag=cancel.is_set,
            )
            _jobs[job_id]["status"] = man.get("status", "failed")
            _jobs[job_id]["manifest"] = man
        except Exception as e:
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["error"] = str(e)
            BUS.emit(job_id, "job", "failed", message=str(e))

    threading.Thread(target=runner, daemon=True).start()
    return {"job_id": job_id, "status": "running"}


@app.get("/api/jobs")
def api_list_jobs() -> dict[str, Any]:
    # disk + memory
    items = []
    root = jobs_root()
    if root.exists():
        for p in sorted(root.iterdir(), reverse=True):
            if not p.is_dir():
                continue
            st = "unknown"
            jp = p / "job.json"
            if jp.exists():
                try:
                    import json

                    st = json.loads(jp.read_text(encoding="utf-8")).get("status", st)
                except Exception:
                    pass
            if p.name in _jobs:
                st = _jobs[p.name].get("status", st)
            items.append({"job_id": p.name, "status": st})
    return {"jobs": items[:100]}


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str) -> dict[str, Any]:
    work = jobs_root() / job_id
    if not work.exists() and job_id not in _jobs:
        raise HTTPException(404, "job not found")
    info = dict(_jobs.get(job_id) or {"job_id": job_id})
    jp = work / "job.json"
    if jp.exists():
        import json

        info.update(json.loads(jp.read_text(encoding="utf-8")))
    info["events"] = BUS.history(job_id)[-50:]
    return info


@app.get("/api/jobs/{job_id}/events")
def api_job_events(job_id: str) -> StreamingResponse:
    return StreamingResponse(BUS.sse_iter(job_id), media_type="text/event-stream")


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel(job_id: str) -> dict[str, Any]:
    ev = _cancel.get(job_id)
    if ev:
        ev.set()
    return {"ok": True, "job_id": job_id}


@app.get("/api/jobs/{job_id}/artifacts/{path:path}")
def api_artifact(job_id: str, path: str) -> FileResponse:
    work = jobs_root() / job_id
    target = (work / path).resolve()
    if not str(target).startswith(str(work.resolve())):
        raise HTTPException(400, "invalid path")
    if not target.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(target)


@app.get("/")
def index() -> HTMLResponse:
    index_path = _WEB / "index.html"
    if not index_path.exists():
        return HTMLResponse("<h1>scan2csv</h1><p>web UI missing</p>")
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


if _WEB.is_dir():
    app.mount("/static", StaticFiles(directory=str(_WEB)), name="static")


def create_app() -> FastAPI:
    # reload settings from disk if present
    path = default_jobs_root().parent / "settings.json"
    if path.exists():
        try:
            set_settings(AppSettings.load(path))
        except Exception:
            pass
    return app
