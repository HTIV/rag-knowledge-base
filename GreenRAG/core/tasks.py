"""轻量后台任务管理器。

上传文档、下载模型这类"耗时操作"放到后台线程执行,
前端通过 /api/tasks/<id> 轮询进度,实时显示:
  解析文档 → 分块(多少个) → 向量化(第几批/共几批) → 入库
让新手能直观看到 RAG "文档摄入" 每一步在发生什么。
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Callable


class TaskManager:
    def __init__(self):
        self._tasks: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._history: list[str] = []   # 保留最近任务的 id,便于前端恢复展示

    def create(self, kind: str, title: str, fn: Callable[[Callable], dict]) -> dict:
        tid = uuid.uuid4().hex[:12]
        task = {"id": tid, "kind": kind, "title": title, "state": "queued",
                "progress": 0, "stage": "", "detail": "", "created_at": time.time(),
                "error": "", "result": None}
        with self._lock:
            self._tasks[tid] = task
            self._history.insert(0, tid)
            self._history = self._history[:50]

        def _emit(frac: float, stage: str = "", detail: str = ""):
            with self._lock:
                task["progress"] = max(0.0, min(1.0, frac))
                if stage:
                    task["stage"] = stage
                if detail:
                    task["detail"] = detail

        def _run():
            task["state"] = "running"
            try:
                task["result"] = fn(_emit)
                task["state"] = "done"
                task["progress"] = 1.0
                task["stage"] = "完成"
            except Exception as e:
                task["state"] = "error"
                task["error"] = str(e)
                task["detail"] = str(e)

        t = threading.Thread(target=_run, daemon=True, name=f"task-{kind}")
        t.start()
        return {k: task[k] for k in ("id", "kind", "title", "state", "progress")}

    def get(self, tid: str) -> dict | None:
        with self._lock:
            t = self._tasks.get(tid)
            return dict(t) if t else None

    def list(self) -> list[dict]:
        with self._lock:
            return [{k: t[k] for k in ("id", "kind", "title", "state",
                                       "progress", "stage", "created_at")}
                    for tid, t in self._tasks.items()
                    if tid in self._history and t["state"] in ("queued", "running", "done")]
