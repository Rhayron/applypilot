"""Tracking de vagas e aplicações em SQLite."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from .models import ApplicationStatus, Job, normalize

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
    updated_at TEXT DEFAULT (datetime('now')),
    dedupe_key TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
-- O índice de dedupe_key fica em _migrate(), não aqui: num banco criado antes da
-- coluna existir, este script roda primeiro e o CREATE INDEX falharia com
-- "no such column", impedindo o Tracker de abrir.

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
        self.conn.create_function("norm", 1, normalize)
        with self._lock:
            self.conn.executescript(SCHEMA)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self._migrate()

    def _migrate(self) -> None:
        """Migrações in-place. O banco de produção sobrevive aos deploys, então
        coluna nova precisa ser adicionada e preenchida, não só declarada no SCHEMA
        (o CREATE TABLE IF NOT EXISTS não toca numa tabela que já existe)."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(jobs)")}
        if "dedupe_key" not in cols:
            self.conn.execute("ALTER TABLE jobs ADD COLUMN dedupe_key TEXT")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_dedupe_key ON jobs(dedupe_key)")
        # Preenche o que estiver faltando — tanto o backfill inicial quanto linhas
        # gravadas por uma versão anterior do código.
        self.conn.execute(
            "UPDATE jobs SET dedupe_key = norm(title) || '|' || norm(company) "
            "WHERE dedupe_key IS NULL"
        )
        self.conn.commit()

    # ---- jobs ----
    def seen(self, job: Job) -> bool:
        """Já conhecemos esta vaga? Por ID ou por conteúdo.

        O segundo critério é o que impede alerta duplicado quando o anúncio é
        republicado com outro external_id, ou aparece em duas fontes.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM jobs WHERE uid=? OR dedupe_key=? LIMIT 1",
                (job.uid, job.dedupe_key),
            ).fetchone()
        return row is not None

    def add_job(self, job: Job) -> None:
        with self._lock:
            self.conn.execute(
                """INSERT OR IGNORE INTO jobs
                   (uid, source, external_id, title, company, location, url, description,
                    posted_at, dedupe_key)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    job.uid, job.source, job.external_id, job.title, job.company,
                    job.location, job.url, job.description,
                    job.posted_at.isoformat() if job.posted_at else None,
                    job.dedupe_key,
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

    def awaiting_decision(self) -> list[sqlite3.Row]:
        """Vagas com CV pronto esperando o usuário — a fila que importa reportar.

        São dois status, não um: 'pending_review' é o que dá para candidatar
        automaticamente (greenhouse e lever), e 'alerted' é o resto, que exige envio
        manual. Reportar só o primeiro esconderia a maioria das vagas, já que a maior
        parte das fontes não tem automação de envio.
        """
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM jobs WHERE status IN ('pending_review','alerted') "
                "ORDER BY score DESC"
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
