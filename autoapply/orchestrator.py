"""Loop principal: descobrir → pontuar → adaptar → aplicar/alertar."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .apply import ScreeningAnswerer, get_applier
from .config import Config
from .db import Tracker
from .discovery import enabled_sources
from .discovery.linkedin import LinkedInSource
from .llm import LLM
from .matching import score_job
from .models import ApplicationStatus, Job, Mode
from .notify import TelegramNotifier
from .rendering import render_resume
from .tailoring import tailor

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.tracker = Tracker(cfg.base_dir / cfg.output.db_path)
        self.llm = LLM(cfg.llm.model, cfg.llm.temperature)
        self.scoring_llm = LLM(cfg.llm.scoring_model, cfg.llm.temperature)
        self.notifier = TelegramNotifier(cfg.telegram.token, cfg.telegram.chat_id)
        self.resume = cfg.load_resume()
        self.context = cfg.load_context()
        self.answers = cfg.load_answers()

    # ------------------------------------------------------------------
    def run_cycle(self) -> dict:
        """Um ciclo completo. Retorna estatísticas."""
        stats = {"discovered": 0, "new": 0, "tailored": 0, "applied": 0,
                 "alerted": 0, "failed": 0, "skipped": 0}
        sources = {s.name: s for s in enabled_sources(self.cfg)}
        for src in sources.values():
            jobs = src.discover()
            stats["discovered"] += len(jobs)
            for job in jobs:
                if self.tracker.seen(job):
                    continue
                stats["new"] += 1
                self.tracker.add_job(job)
                try:
                    self._process(job, sources, stats)
                except Exception as e:  # noqa: BLE001
                    log.exception("Erro processando %s", job.short())
                    self.tracker.set_status(job.uid, ApplicationStatus.FAILED,
                                            fail_reason=str(e))
        log.info("Ciclo concluído: %s", stats)
        self.tracker.record_cycle(stats)
        return stats

    # ------------------------------------------------------------------
    def _process(self, job: Job, sources: dict, stats: dict) -> None:
        # LinkedIn: busca a descrição completa só agora (economiza requests)
        if job.source == "linkedin" and not job.description:
            src = sources.get("linkedin")
            if isinstance(src, LinkedInSource):
                job.description = src.fetch_description(job)

        # 1) scoring (modelo barato)
        match = score_job(self.scoring_llm, job, self.resume, self.context)
        self.tracker.set_status(job.uid, ApplicationStatus.SCORED,
                                score=match.score, score_reasoning=match.reasoning)
        m = self.cfg.matching
        if match.score < m.alert_threshold:
            self.tracker.set_status(job.uid, ApplicationStatus.SKIPPED)
            stats["skipped"] += 1
            return

        # 2) adaptação do currículo (modelo principal)
        app = tailor(self.llm, job, self.resume, self.context)
        slug = f"{job.company}-{job.title}-{job.uid[:6]}"
        html_path, pdf_path = render_resume(app.resume_json, self.cfg.out_dir, slug)
        resume_file = pdf_path or html_path
        self.tracker.set_status(
            job.uid, ApplicationStatus.TAILORED,
            resume_json=app.resume_json, cover_letter=app.cover_letter,
            changes_summary=app.changes_summary, pdf_path=str(resume_file),
        )
        stats["tailored"] += 1
        row = self.tracker.get(job.uid)

        # 3) decidir o que fazer
        mode = self.cfg.mode
        can_auto = get_applier(job.source, self._answerer()) is not None and pdf_path is not None
        if match.score < m.apply_threshold or mode == Mode.ALERT or not can_auto:
            self.notifier.job_alert(row, resume_file, mode_review=False)
            self.tracker.set_status(job.uid, ApplicationStatus.ALERTED)
            stats["alerted"] += 1
            return

        if mode == Mode.REVIEW:
            self.notifier.job_alert(row, resume_file, mode_review=True)
            self.tracker.set_status(job.uid, ApplicationStatus.PENDING_REVIEW)
            stats["alerted"] += 1
            return

        # mode == AUTO
        if self._apply_now(job.uid):
            stats["applied"] += 1
        else:
            stats["failed"] += 1

    # ------------------------------------------------------------------
    def _answerer(self) -> ScreeningAnswerer:
        return ScreeningAnswerer(self.llm, self.resume, self.context, self.answers)

    def apply_by_uid(self, uid: str) -> bool:
        """Usado pelo bot Telegram quando o usuário aprova uma vaga."""
        return self._apply_now(uid)

    def _apply_now(self, uid: str) -> bool:
        row = self.tracker.get(uid)
        if not row:
            return False

        # limites de segurança
        if self.tracker.applications_today() >= self.cfg.limits.max_applications_per_day:
            self.notifier.failure_alert(row, "limite diário de aplicações atingido",
                                        row["pdf_path"])
            self.tracker.set_status(uid, ApplicationStatus.FAILED,
                                    fail_reason="limite diário")
            return False

        job = Job(source=row["source"], external_id=row["external_id"],
                  title=row["title"], company=row["company"], location=row["location"] or "",
                  url=row["url"], apply_url=row["url"], description=row["description"] or "")
        applier = get_applier(job.source, self._answerer())
        pdf = Path(row["pdf_path"]) if row["pdf_path"] else None
        if not applier or not pdf or not pdf.exists():
            self.notifier.failure_alert(row, "sem automação para esta fonte", row["pdf_path"])
            self.tracker.set_status(uid, ApplicationStatus.FAILED,
                                    fail_reason="sem automação")
            return False

        result = applier.apply(job, pdf, row["cover_letter"] or "")
        if result.success:
            self.tracker.mark_applied(uid)
            self.notifier.success_alert(row)
            time.sleep(self.cfg.limits.min_seconds_between_applications)
            return True

        self.tracker.set_status(uid, ApplicationStatus.FAILED, fail_reason=result.reason)
        self.notifier.failure_alert(row, result.reason, row["pdf_path"])
        return False

    # ------------------------------------------------------------------
    def tailor_url(self, url: str) -> tuple[str, Path | None]:
        """Adapta o CV para uma vaga arbitrária (on-demand via Telegram/CLI)."""
        import httpx

        from .discovery.base import strip_html

        r = httpx.get(url, timeout=30, follow_redirects=True,
                      headers={"User-Agent": "AutoApply/0.1"})
        description = strip_html(r.text)[:15000]
        job = Job(source="manual", external_id=url, title="Vaga (manual)",
                  company=url.split("/")[2] if "://" in url else "?",
                  url=url, description=description)
        self.tracker.add_job(job)
        app = tailor(self.llm, job, self.resume, self.context)
        html_path, pdf_path = render_resume(app.resume_json, self.cfg.out_dir,
                                            f"manual-{job.uid[:8]}")
        f = pdf_path or html_path
        self.tracker.set_status(job.uid, ApplicationStatus.TAILORED,
                                resume_json=app.resume_json,
                                cover_letter=app.cover_letter,
                                changes_summary=app.changes_summary, pdf_path=str(f))
        summary = (f"CV adaptado para {url}\n\nMudanças:\n{app.changes_summary}"
                   f"\n\nCover letter:\n{app.cover_letter}")
        return summary, f
