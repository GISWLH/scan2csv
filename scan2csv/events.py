from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class EventBus:
    """In-process pub/sub for job progress (SSE)."""

    _lock: threading.Lock = field(default_factory=threading.Lock)
    _subs: dict[str, list[Callable[[dict[str, Any]], None]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    _history: dict[str, Deque[dict[str, Any]]] = field(
        default_factory=lambda: defaultdict(lambda: deque(maxlen=500))
    )

    def emit(
        self,
        job_id: str,
        step: str,
        status: str,
        *,
        progress: float | None = None,
        message: str = "",
        payload: dict[str, Any] | None = None,
        artifacts: list[str] | None = None,
    ) -> dict[str, Any]:
        ev = {
            "job_id": job_id,
            "step": step,
            "status": status,
            "progress": progress,
            "message": message,
            "payload": payload or {},
            "artifacts": artifacts or [],
            "ts": utc_now(),
        }
        with self._lock:
            self._history[job_id].append(ev)
            subs = list(self._subs.get(job_id, []))
        for cb in subs:
            try:
                cb(ev)
            except Exception:
                pass
        return ev

    def history(self, job_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history.get(job_id, []))

    def subscribe(self, job_id: str, cb: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        with self._lock:
            self._subs[job_id].append(cb)

        def unsub() -> None:
            with self._lock:
                if cb in self._subs.get(job_id, []):
                    self._subs[job_id].remove(cb)

        return unsub

    def sse_iter(self, job_id: str, *, poll_s: float = 0.4) -> Iterator[str]:
        q: Deque[dict[str, Any]] = deque()
        lock = threading.Lock()

        def on_ev(ev: dict[str, Any]) -> None:
            with lock:
                q.append(ev)

        for ev in self.history(job_id):
            on_ev(ev)
        unsub = self.subscribe(job_id, on_ev)
        try:
            idle = 0
            while True:
                batch: list[dict[str, Any]] = []
                with lock:
                    while q:
                        batch.append(q.popleft())
                if batch:
                    idle = 0
                    for ev in batch:
                        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                        if ev.get("step") == "job" and ev.get("status") in {
                            "succeeded",
                            "failed",
                            "cancelled",
                            "blocked",
                            "budget_exceeded",
                        }:
                            return
                else:
                    idle += 1
                    if idle % 15 == 0:
                        yield f": keepalive {time.time()}\n\n"
                    time.sleep(poll_s)
        finally:
            unsub()


BUS = EventBus()
