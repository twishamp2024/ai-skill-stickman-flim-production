"""Dependency-aware task scheduler with content-hash caching, bounded retries,
per-resource concurrency limits, checkpointed state and resume."""
from __future__ import annotations

import fnmatch
import threading
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .util import KageError, TransientError, dump_json, load_json, log, sha256_file, sha256_json


@dataclass
class Task:
    id: str
    kind: str
    deps: list[str]
    run: Callable[["Task"], dict]
    params: dict = field(default_factory=dict)       # hashed
    input_files: list[Path] = field(default_factory=list)  # hashed by content
    outputs: list[Path] = field(default_factory=list)       # must exist for a cache hit
    resource: str = "cpu"                             # cpu | gpu
    max_attempts: int = 3
    description: str = ""
    hash_deps: list[str] | None = None  # deps whose outputs affect this task's hash (None = all)


class Scheduler:
    def __init__(self, tasks: list[Task], state_path: Path, *, concurrency: dict | None = None, backoff_s: float = 0.5,
                 root: Path | None = None):
        self.tasks = {t.id: t for t in tasks}
        if len(self.tasks) != len(tasks):
            raise KageError("duplicate task ids")
        for t in tasks:
            for d in t.deps:
                if d not in self.tasks:
                    raise KageError(f"task {t.id} depends on unknown task {d}")
        self._check_acyclic()
        self.state_path = state_path
        self.state = load_json(state_path) if state_path.exists() else {"tasks": {}}
        self.limits = {"cpu": 4, "gpu": 2, **(concurrency or {})}
        self.backoff_s = backoff_s
        self.root = Path(root).resolve() if root else None
        self.lock = threading.Lock()

    def _key(self, p) -> str:
        """Cache keys use project-relative paths so projects stay relocatable."""
        p = Path(p).resolve()
        if self.root:
            try:
                return str(p.relative_to(self.root))
            except ValueError:
                pass
        return str(p)

    def _check_acyclic(self):
        seen, stack = set(), set()

        def visit(tid):
            if tid in stack:
                raise KageError(f"dependency cycle through task {tid}")
            if tid in seen:
                return
            stack.add(tid)
            for d in self.tasks[tid].deps:
                visit(d)
            stack.discard(tid)
            seen.add(tid)

        for tid in self.tasks:
            visit(tid)

    def topo_order(self) -> list[str]:
        order, seen = [], set()

        def visit(tid):
            if tid in seen:
                return
            seen.add(tid)
            for d in self.tasks[tid].deps:
                visit(d)
            order.append(tid)

        for tid in sorted(self.tasks):
            visit(tid)
        return order

    def input_hash(self, task: Task) -> str:
        files = {}
        for p in task.input_files:
            files[self._key(p)] = sha256_file(p) if Path(p).exists() else "MISSING"
        hashed = task.deps if task.hash_deps is None else task.hash_deps
        dep_out = {d: self.state["tasks"].get(d, {}).get("output_hash") for d in hashed}
        return sha256_json({"kind": task.kind, "params": task.params, "files": files, "deps": dep_out})

    def _save(self):
        dump_json(self.state_path, self.state)

    def run(self, *, only: list[str] | None = None, force: list[str] | None = None) -> dict:
        selected = set(self.tasks)
        if only:
            wanted = {t for t in self.tasks if any(fnmatch.fnmatch(t, p) for p in only)}
            # include upstream dependencies of the selection
            stack = list(wanted)
            while stack:
                t = stack.pop()
                for d in self.tasks[t].deps:
                    if d not in wanted:
                        wanted.add(d)
                        stack.append(d)
            selected = wanted
        forced = {t for t in selected if force and any(fnmatch.fnmatch(t, p) for p in force)}
        status = {t: "pending" for t in selected}
        running = {}
        in_use = {k: 0 for k in self.limits}
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=sum(self.limits.values())) as pool:
            while True:
                progressed = False
                for tid in self.topo_order():
                    if tid not in selected or status[tid] != "pending":
                        continue
                    task = self.tasks[tid]
                    dep_states = [status.get(d, "done") for d in task.deps]
                    if any(s in ("failed", "blocked") for s in dep_states):
                        status[tid] = "blocked"
                        self._record(tid, {"status": "blocked", "error": "upstream dependency failed"})
                        progressed = True
                        continue
                    if not all(s in ("done", "cached") for s in dep_states):
                        continue
                    h = self.input_hash(task)
                    prev = self.state["tasks"].get(tid, {})
                    if (tid not in forced and prev.get("status") in ("done", "cached") and prev.get("input_hash") == h
                            and all(Path(o).exists() for o in task.outputs)):
                        status[tid] = "cached"
                        self._record(tid, {**prev, "status": "cached"})
                        progressed = True
                        continue
                    if in_use[task.resource] >= self.limits[task.resource]:
                        continue  # queue instead of oversubscribing the GPU/CPU
                    in_use[task.resource] += 1
                    status[tid] = "running"
                    running[pool.submit(self._execute, task, h)] = tid
                    progressed = True
                if not running:
                    if not progressed:
                        break
                    continue
                done, _ = wait(list(running), return_when=FIRST_COMPLETED)
                for fut in done:
                    tid = running.pop(fut)
                    in_use[self.tasks[tid].resource] -= 1
                    status[tid] = fut.result()
        summary = {s: sorted(t for t, v in status.items() if v == s) for s in ("done", "cached", "failed", "blocked", "pending")}
        report = {"schema": "kage.run_report/1", "elapsed_s": round(time.time() - t0, 2),
                  "counts": {k: len(v) for k, v in summary.items()}, "tasks": summary,
                  "failures": {t: self.state["tasks"][t].get("error") for t in summary["failed"]}}
        return report

    def _record(self, tid: str, rec: dict):
        with self.lock:
            self.state["tasks"][tid] = rec
            self._save()

    def _execute(self, task: Task, input_hash: str) -> str:
        attempts = 0
        history = []
        while True:
            attempts += 1
            start = time.time()
            log(f"start {task.id} (attempt {attempts})")
            try:
                result = task.run(task) or {}
                missing = [str(o) for o in task.outputs if not Path(o).exists()]
                if missing:
                    raise KageError(f"task finished but outputs are missing: {missing}")
                out_hash = sha256_json({"files": {self._key(o): sha256_file(o) for o in task.outputs if Path(o).is_file()},
                                        "result": result.get("hash_payload", result)})
                self._record(task.id, {"status": "done", "input_hash": input_hash, "output_hash": out_hash, "attempts": attempts,
                                       "duration_s": round(time.time() - start, 3), "finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                       "retry_history": history, "result": {k: v for k, v in result.items() if k != "hash_payload"}})
                log(f"done  {task.id} in {time.time() - start:.1f}s")
                return "done"
            except TransientError as e:
                history.append({"attempt": attempts, "class": "transient", "error": str(e)[:500]})
                if attempts < task.max_attempts:
                    log(f"retry {task.id}: {e}")
                    time.sleep(self.backoff_s * attempts)
                    continue
                err = f"transient failure persisted after {attempts} attempts: {e}"
            except KageError as e:
                history.append({"attempt": attempts, "class": "deterministic", "error": str(e)[:2000]})
                err = str(e)
            except Exception as e:  # bug: record with traceback, do not retry
                history.append({"attempt": attempts, "class": "internal", "error": traceback.format_exc()[-2000:]})
                err = f"internal error: {e}"
            self._record(task.id, {"status": "failed", "input_hash": input_hash, "attempts": attempts, "error": err,
                                   "retry_history": history, "finished": time.strftime("%Y-%m-%dT%H:%M:%S")})
            log(f"FAIL  {task.id}: {err[:300]}")
            return "failed"
