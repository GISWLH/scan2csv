from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from scan2csv import __version__
from scan2csv.config import AppSettings


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Frozen EXE double-click / bare launch → serve
    if not argv:
        argv = ["serve"]

    parser = argparse.ArgumentParser(prog="scan2csv", description="Any scanned table → CSV")
    parser.add_argument("--version", action="version", version=f"scan2csv {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", help="start local web UI + API")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8787)
    p_serve.add_argument("--reload", action="store_true")

    p_run = sub.add_parser("run", help="run one image/pdf through the pipeline")
    p_run.add_argument("input", type=Path)
    p_run.add_argument("--out-dir", type=Path, default=None)
    p_run.add_argument("--notes", default="")
    p_run.add_argument("--skip-enhance", action="store_true")
    p_run.add_argument("--skip-llm", action="store_true")

    args = parser.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        from scan2csv.server import create_app

        create_app()
        uvicorn.run(
            "scan2csv.server:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
        return 0

    if args.cmd == "run":
        from scan2csv.events import BUS
        from scan2csv.pipeline.run import run_job

        settings = AppSettings.from_env()
        if args.notes:
            settings.pipeline.user_notes = args.notes
        if args.skip_enhance:
            settings.pipeline.skip_enhance = True
        if args.skip_llm:
            settings.pipeline.skip_llm = True
        job_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        out = args.out_dir or (Path(settings.jobs_dir) / job_id)
        out.mkdir(parents=True, exist_ok=True)

        def on_print(ev: dict) -> None:
            print(f"[{ev.get('step')}] {ev.get('status')}: {ev.get('message')}", flush=True)

        BUS.subscribe(job_id, on_print)
        man = run_job(job_id, Path(args.input), out, settings, bus=BUS)
        print(json.dumps({"job_id": job_id, "status": man.get("status"), "out": str(out)}, ensure_ascii=False))
        return 0 if man.get("status") == "succeeded" else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
