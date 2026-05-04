"""Cron 任务管理器 — 定时唤醒 AI 干活."""
import os
import json
import time
import threading
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Callable

CRON_FILE = "cron_jobs.json"


def _load_jobs() -> List[Dict]:
    if os.path.exists(CRON_FILE):
        with open(CRON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("jobs", [])
    return []


def _save_jobs(jobs: List[Dict]):
    with open(CRON_FILE, "w", encoding="utf-8") as f:
        json.dump({"jobs": jobs, "updated_at": datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)


def _parse_duration(s: str) -> int:
    """解析持续时间字符串为分钟数. 30m→30, 2h→120, 1d→1440"""
    s = s.strip().lower()
    m = re.match(r"^(\d+)\s*(m|min|h|hr|d|day)", s)
    if not m:
        raise ValueError(f"无法解析: {s}，格式如 30m, 2h, 1d")
    v = int(m.group(1))
    u = m.group(2)[0]
    return {"m": 1, "h": 60, "d": 1440}[u] * v


def _next_run(schedule: Dict) -> Optional[str]:
    """计算下次运行时间."""
    kind = schedule.get("kind")
    now = datetime.now()
    if kind == "interval":
        minutes = schedule.get("minutes", 30)
        last = schedule.get("last_run_at")
        if last:
            base = datetime.fromisoformat(last)
            nxt = base + timedelta(minutes=minutes)
        else:
            nxt = now + timedelta(minutes=minutes)
        return nxt.isoformat()
    if kind == "cron":
        try:
            from croniter import croniter
        except ImportError:
            return (now + timedelta(hours=1)).isoformat()
        base = now
        last = schedule.get("last_run_at")
        if last:
            base = datetime.fromisoformat(last)
        cron = croniter(schedule["expr"], base)
        return cron.get_next(datetime).isoformat()
    return None


def add_job(name: str, prompt: str, schedule_str: str) -> Dict:
    """添加定时任务.

    schedule_str 格式:
      - "30m" → 一次性, 30分钟后
      - "every 30m" → 每30分钟
      - "0 9 * * *" → cron 表达式
    """
    s = schedule_str.strip().lower()
    if s.startswith("every "):
        minutes = _parse_duration(s[6:])
        schedule = {"kind": "interval", "minutes": minutes, "display": f"每{minutes}分钟"}
        next_run = (datetime.now() + timedelta(minutes=minutes)).isoformat()
    elif re.match(r"^[\d\*\-,/]+\s+[\d\*\-,/]+\s+[\d\*\-,/]+\s+[\d\*\-,/]+\s+[\d\*\-,/]", s):
        schedule = {"kind": "cron", "expr": s, "display": s}
    else:
        minutes = _parse_duration(s)
        schedule = {"kind": "interval", "minutes": minutes, "display": f"每{minutes}分钟"}
        schedule["_oneshot"] = True

    job = {
        "id": f"job_{int(time.time() * 1000)}_{len(_load_jobs())}",
        "name": name,
        "prompt": prompt,
        "schedule": schedule,
        "enabled": True,
        "next_run_at": _next_run(schedule),
        "last_run_at": None,
        "last_status": None,
        "created_at": datetime.now().isoformat(),
    }
    jobs = _load_jobs()
    jobs.append(job)
    _save_jobs(jobs)
    return job


def remove_job(job_id: str) -> bool:
    jobs = _load_jobs()
    new_jobs = [j for j in jobs if j["id"] != job_id]
    if len(new_jobs) < len(jobs):
        _save_jobs(new_jobs)
        return True
    return False


def list_jobs() -> List[Dict]:
    return _load_jobs()


def get_due_jobs() -> List[Dict]:
    """获取到期的任务."""
    now = datetime.now()
    jobs = _load_jobs()
    due = []
    for j in jobs:
        if not j.get("enabled", True):
            continue
        nxt = j.get("next_run_at")
        if nxt and datetime.fromisoformat(nxt) <= now:
            due.append(j)
    return due


def mark_run(job_id: str, success: bool, output: str = ""):
    """标记任务已执行."""
    jobs = _load_jobs()
    for j in jobs:
        if j["id"] == job_id:
            j["last_run_at"] = datetime.now().isoformat()
            j["last_status"] = "ok" if success else "error"
            job_dir = f"cron_output/{job_id}"
            os.makedirs(job_dir, exist_ok=True)
            fname = f"{job_dir}/{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
            with open(fname, "w", encoding="utf-8") as f:
                f.write(output)
            j["next_run_at"] = _next_run(j.get("schedule", {}))
            schedule = j.get("schedule", {})
            if schedule.get("_oneshot"):
                j["enabled"] = False  # 一次性任务执行后禁用
            break
    _save_jobs(jobs)


class CronScheduler:
    """Cron 调度器 — 后台线程轮询到期任务并执行."""

    def __init__(self, executor: Optional[Callable] = None):
        self.executor = executor
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print("  ⏰ Cron 调度器已启动 (每30秒检查)")

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                due = get_due_jobs()
                for job in due:
                    print(f"\n  ⏰ Cron 任务触发: {job.get('name', job['id'])}")
                    if self.executor:
                        try:
                            result = self.executor(job["prompt"])
                            mark_run(job["id"], True, result)
                            print(f"  ✅ Cron 任务完成: {job.get('name', job['id'])}")
                        except Exception as e:
                            mark_run(job["id"], False, str(e))
                            print(f"  ❌ Cron 任务失败: {e}")
            except Exception:
                pass
            time.sleep(30)
