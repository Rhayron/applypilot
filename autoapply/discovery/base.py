"""Base dos conectores de busca de vagas."""
from __future__ import annotations

import abc
import html
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from ..config import Config
from ..models import Job

log = logging.getLogger(__name__)

UA = "AutoApply/0.1 (job search agent; contact: owner)"


class JobSource(abc.ABC):
    """Um conector de fonte de vagas. Subclasses implementam fetch()."""

    name: str = "base"
    #: True se este conector suporta aplicação automática
    can_auto_apply: bool = False

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.opts = cfg.source(self.name)
        self.client = httpx.Client(timeout=30, headers={"User-Agent": UA},
                                   follow_redirects=True)

    @property
    def enabled(self) -> bool:
        return bool(self.opts.get("enabled", False))

    @abc.abstractmethod
    def fetch(self) -> list[Job]:
        """Retorna vagas candidatas (sem filtro)."""

    def discover(self) -> list[Job]:
        """fetch() + filtros de recência/keywords."""
        try:
            jobs = self.fetch()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao buscar em %s", self.name)
            return []
        return [j for j in jobs if self._passes_filters(j)]

    # ---- filtros ----
    def _passes_filters(self, job: Job) -> bool:
        s = self.cfg.search
        if job.posted_at:
            posted = job.posted_at
            if posted.tzinfo is None:
                posted = posted.replace(tzinfo=timezone.utc)
            if posted < datetime.now(timezone.utc) - timedelta(days=s.max_age_days):
                return False
        hay = f"{job.title} {job.description}".lower()
        if s.titles and not any(t.lower() in job.title.lower() for t in s.titles):
            # título não bate; ainda aceita se alguma keyword forte aparecer no título
            if not any(k.lower() in job.title.lower() for k in s.keywords):
                return False
        if s.remote_only and job.remote is False:
            return False
        return True


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()
