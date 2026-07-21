"""LinkedIn — SOMENTE busca pública (sem login) e SOMENTE modo alerta.

AVISO IMPORTANTE: o ToS do LinkedIn proíbe scraping e automação de conta.
Por isso este conector:
  * não faz login nem usa cookies de sessão;
  * consulta apenas a listagem pública de vagas, em baixa frequência;
  * NUNCA aplica automaticamente (can_auto_apply = False) — a vaga chega
    pelo Telegram com o CV adaptado para você aplicar manualmente.
Se o LinkedIn bloquear a listagem pública, o conector simplesmente retorna vazio.
"""
from __future__ import annotations

import re
import time

from ..models import Job
from .base import JobSource, strip_html

GUEST_SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

CARD_RE = re.compile(
    r'<a[^>]+class="base-card__full-link[^"]*"[^>]+href="(?P<url>[^"]+)"[^>]*>.*?'
    r'<span class="sr-only">\s*(?P<title>.*?)\s*</span>.*?'
    r'(?:<h4[^>]*>.*?<a[^>]*>\s*(?P<company>.*?)\s*</a>|<h4[^>]*>\s*(?P<company2>[^<]+?)\s*</h4>).*?'
    r'<span class="job-search-card__location">\s*(?P<location>.*?)\s*</span>',
    re.S,
)


class LinkedInSource(JobSource):
    name = "linkedin"
    can_auto_apply = False  # nunca automatizar conta do LinkedIn

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        for query in self.opts.get("queries", []):
            params = {"keywords": query, "start": 0}
            locs = self.cfg.search.locations
            if locs:
                params["location"] = locs[0]
            r = self.client.get(GUEST_SEARCH, params=params)
            if r.status_code != 200:
                continue
            for m in CARD_RE.finditer(r.text):
                url = m.group("url").split("?")[0]
                ext = _job_id(url) or url
                jobs.append(Job(
                    source=self.name,
                    external_id=ext,
                    title=strip_html(m.group("title")),
                    company=strip_html(m.group("company") or m.group("company2") or ""),
                    location=strip_html(m.group("location")),
                    url=url,
                    description="",  # descrição completa é buscada só se a vaga passar no filtro
                    raw={"query": query},
                ))
            time.sleep(5)  # educado com a listagem pública
        return list({j.uid: j for j in jobs}.values())

    def fetch_description(self, job: Job) -> str:
        """Busca a descrição pública de uma vaga específica (on demand)."""
        jid = _job_id(job.url)
        if not jid:
            return ""
        r = self.client.get(
            f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}"
        )
        if r.status_code != 200:
            return ""
        m = re.search(
            r'<div class="show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', r.text, re.S
        )
        return strip_html(m.group(1))[:15000] if m else ""


def _job_id(url: str):
    m = re.search(r"-(\d{6,})(?:\?|$)", url)
    return m.group(1) if m else None
