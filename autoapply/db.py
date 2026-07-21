"""Tracking de vagas e aplicações em SQLite."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from .models import ApplicationStatus, Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    uid TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT,
    company TEXT,
    location TEXT,
    url TEXT,
    description TEXT,
    posted_at TEXT,
    discovered_at TEXT DEFAULT (datetime('now')),
    status TEXT DEFAULT 'discovered',
    score INTEGER,
    score_reasoning TEXT,
    resume_json TEXT,
    cover_letter TEXT,
    changes_summary TEXT,
    pdf_path TEXT,
    fail_reason TEXT,
    applied_at TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS cycle_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at TEXT DEFAULT (datetime('now')),
    discovered INTEGER DEFAULT 0,
    "new" INTEGER DEFAULT 0,
    tailored INTEGER DEFAULT 0,
    applied INTEGER DEFAULT 0,
    alerted INTEGER DEFAULT 0,
    failed INTEGER DEFAULT 0,
    skipped INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_cycle_runs_ran_at ON cycle_runs(ran_at);
"""

# Campos de estatística gravados a cada ciclo (mesmas chaves do dict de run_cycle).
CYCLE_FIELDS = ("discovered", "new", "tailored", "applied", "alerted", "failed", "skipped")


class Tracker:
    """Tracker SQLite seguro para uso concorrente.

    O scheduler (APScheduler) e o bot do Telegram rodam em threads distintas da que
    constrói o Tracker, então a conexão precisa de `check_same_thread=False` e de um
    lock que serialize os acessos — sem ele o sqlite3 recusa a conexão com
    ProgrammingError e o ciclo inteiro morre antes de processar qualquer vaga.
    """

    def __init__(self, db_path: str | Path):
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(SCHEMA)
            self.conn.execute("PRAGMA journal_mode=WAL")

    # ---- jobs ----
    def seen(self, job: Job) -> bool:
        with self._lock:
            row = self.conn.execute("SELECT 1 FROM jobs WHERE uid=?", (job.uid,)).fetchone()
        return row is not None

    def add_job(self, job: Job) -> None:
        with self._lock:
            self.conn.execute(
                """INSERT OR IGNORE INTO jobs
                   (uid, source, external_id, title, company, location, url, description, posted_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    job.uid, job.source, job.external_id, job.title, job.company,
                    job.location, job.url, job.description,
                    job.posted_at.isoformat() if job.posted_at else None,
                ),
            )
            self.conn.commit()

    def set_status(self, uid: str, status: ApplicationStatus, **fields) -> None:
        cols, vals = ["status=?", "updated_at=datetime('now')"], [status.value]
        for k, v in fields.items():
            cols.append(f"{k}=?")
            vals.append(json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
        vals.append(uid)
        with self._lock:
            self.conn.execute(f"UPDATE jobs SET {', '.join(cols)} WHERE uid=?", vals)
            self.conn.commit()

    def get(self, uid: str) -> Optional[sqlite3.Row]:
        with self._lock:
            return self.conn.execute("SELECT * FROM jobs WHERE uid=?", (uid,)).fetchone()

    def applications_today(self) -> int:
        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) c FROM jobs WHERE status='applied' AND date(applied_at)=?",
                (date.today().isoformat(),),
            ).fetchone()
        return row["c"]

    def mark_applied(self, uid: str) -> None:
        self.set_status(uid, ApplicationStatus.APPLIED)
        with self._lock:
            self.conn.execute(
                "UPDATE jobs SET applied_at=? WHERE uid=?", (datetime.now().isoformat(), uid)
            )
            self.conn.commit()

    def pending_review(self) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM jobs WHERE status='pending_review' ORDER BY score DESC"
            ).fetchall()

    def stats(self) -> dict:
        with self._lock:
            rows = self.conn.execute(
                "SELECT status, COUNT(*) c FROM jobs GROUP BY status"
            ).fetchall()
        return {r["status"]: r["c"] for r in rows}

    # ---- histórico de ciclos ----
    def record_cycle(self, stats: dict) -> None:
        """Persiste as estatísticas de um ciclo em cycle_runs."""
        cols = ", ".join(f'"{k}"' for k in CYCLE_FIELDS)
        placeholders = ", ".join("?" for _ in CYCLE_FIELDS)
        values = [int(stats.get(k, 0)) for k in CYCLE_FIELDS]
        with self._lock:
            self.conn.execute(
                f"INSERT INTO cycle_runs (ran_at, {cols}) "
                f"VALUES (datetime('now'), {placeholders})",
                values,
            )
            self.conn.commit()

    def cycle_history(self, limit: int = 20) -> list[sqlite3.Row]:
        """Últimos ciclos, do mais recente para o mais antigo."""
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM cycle_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()

    def cycle_totals(self) -> dict:
        """Totais acumulados de todos os ciclos + contagem e data do último."""
        sums = ", ".join(f'COALESCE(SUM("{k}"),0) "{k}"' for k in CYCLE_FIELDS)
        with self._lock:
            row = self.conn.execute(
                f"SELECT COUNT(*) runs, {sums}, MAX(ran_at) last_run FROM cycle_runs"
            ).fetchone()
        return dict(row) if row else {}
