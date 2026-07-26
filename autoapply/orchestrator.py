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
from .rendering import docx_para_pdf, render_resume
from .tailoring import tailor

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.tracker = Tracker(cfg.base_dir / cfg.output.db_path)
        self._config_path = cfg.base_dir / "config.yaml"
        self._config_mtime = self._mtime()
        self._apply_cfg()

    # ------------------------------------------------------------------
    def _apply_cfg(self) -> None:
        """(Re)constrói tudo que deriva do config."""
        cfg = self.cfg
        self.llm = LLM(cfg.llm.model, cfg.llm.temperature)
        self.scoring_llm = LLM(cfg.llm.scoring_model, cfg.llm.temperature)
        # telegram.enabled=false precisa calar o notificador, não só o bot: o token
        # vem do .env e continuaria valendo, fazendo o autopilot mandar DM pela
        # identidade antiga em paralelo com quem estiver reportando por ele.
        self.notifier = TelegramNotifier(
            cfg.telegram.token if cfg.telegram.enabled else "", cfg.telegram.chat_id,
            interactive=cfg.telegram.bot,
        )
        self.resume = cfg.load_resume()
        self.context = cfg.load_context()
        self.answers = cfg.load_answers()

    def _mtime(self) -> float:
        return self._config_path.stat().st_mtime if self._config_path.exists() else 0.0

    def reload_config(self) -> bool:
        """Relê o config.yaml do disco. Devolve True se algo mudou.

        É o que faz os ajustes do Hermes valerem sem reiniciar o container: ele grava
        no arquivo e o próximo ciclo já roda com os valores novos.
        """
        from .config import load_config

        mtime = self._mtime()
        if mtime == self._config_mtime:
            return False
        try:
            new_cfg = load_config(self._config_path)
        except Exception:  # noqa: BLE001
            log.exception("config.yaml inválido; seguindo com a configuração anterior")
            return False
        self._config_mtime = mtime
        self.cfg = new_cfg
        self._apply_cfg()
        log.info("Configuração recarregada do disco.")
        return True

    def run_cycle_locked(self):
        """run_cycle protegido por lock de arquivo entre processos.

        O scheduler e o servidor MCP são processos separados dentro do mesmo
        container; sem isto, um ciclo pedido pelo Hermes rodaria por cima do ciclo
        agendado, duplicando chamadas de LLM. Devolve None se já havia um em curso.
        """
        try:
            import fcntl
        except ImportError:  # Windows (dev local): sem lock, processo único mesmo
            return self.run_cycle()

        lock_path = self.cfg.base_dir / ".cycle.lock"
        with open(lock_path, "w") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                log.info("Ciclo já em andamento em outro processo; ignorando pedido.")
                return None
            try:
                return self.run_cycle()
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    # ------------------------------------------------------------------
    def run_cycle(self) -> dict:
        """Um ciclo completo. Retorna estatísticas."""
        self.reload_config()
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
                # add_job() já gravou a linha com a descrição vazia e nada a regravava:
                # o banco ficava sem o texto da vaga para toda fonte que busca a
                # descrição tarde. Isso cega o job_detail do Hermes e a detecção de
                # idioma de qualquer reprocessamento.
                if job.description:
                    self.tracker.set_description(job.uid, job.description)

        # 1) scoring (modelo barato)
        match = score_job(self.scoring_llm, job, self.resume, self.context)
        self.tracker.set_status(job.uid, ApplicationStatus.SCORED,
                                score=match.score, score_reasoning=match.reasoning)
        m = self.cfg.matching
        if match.score < m.alert_threshold:
            self.tracker.set_status(job.uid, ApplicationStatus.SKIPPED)
            stats["skipped"] += 1
            return

        # 2) adaptação do currículo
        slug = f"{job.company}-{job.title}-{job.uid[:6]}"
        resume_file, resume_json, carta, mudancas = self._adaptar(job, slug)
        self.tracker.set_status(
            job.uid, ApplicationStatus.TAILORED,
            resume_json=resume_json, cover_letter=carta,
            changes_summary=mudancas, pdf_path=str(resume_file),
        )
        pdf_path = resume_file if resume_file.suffix == ".pdf" else None
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
    def _adaptar(self, job: Job, slug: str) -> tuple[Path, dict, str, str]:
        """Gera o currículo da vaga. Edita o .docx do usuário quando ele existe.

        Devolve (arquivo_para_enviar, resume_json, cover_letter, resumo_das_mudancas).
        O caminho antigo (JSON Resume + template HTML) continua valendo como fallback
        para quem não tem um .docx base configurado.
        """
        base = self.cfg.base_docx
        if base:
            try:
                from .docx_resume import adaptar

                destino = self.cfg.out_dir / f"{slug}.docx"
                r = adaptar(job, base, destino, llm=self.llm)
                pdf = docx_para_pdf(r.caminho, self.cfg.out_dir)
                mudancas = r.resumo or f"{r.edicoes} trechos ajustados para a vaga."
                if r.idioma == "en":
                    mudancas = "Vaga em inglês: currículo traduzido.\n\n" + mudancas
                if r.avisos:
                    mudancas += f"\n\n(ressalvas: {'; '.join(r.avisos)})"
                log.info("CV adaptado via docx por %s (%d edições, %s)",
                         r.editor, r.edicoes, r.idioma)
                return (pdf or r.caminho), {}, "", mudancas
            except Exception:  # noqa: BLE001
                log.exception("Adaptação via .docx falhou; usando o template antigo")

        app = tailor(self.llm, job, self.resume, self.context)
        html_path, pdf_path = render_resume(app.resume_json, self.cfg.out_dir, slug)
        return (pdf_path or html_path), app.resume_json, app.cover_letter, app.changes_summary

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
