"""Gupy (Brasil) — busca via API pública do portal de vagas.

Endpoint usado pelo próprio portal.gupy.io (não documentado oficialmente;
pode mudar). Aplicação na Gupy exige conta do candidato, então este conector
opera em modo alerta/review.
"""
from __future__ import annotations

from datetime import datetime

from ..models import Job
from .base import JobSource, strip_html

SEARCH_URL = "https://employability-portal.gupy.io/api/v1/jobs"


class GupySource(JobSource):
    name = "gupy"
    can_auto_apply = False

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        queries = self.opts.get("queries") or self.cfg.search.titles
        for q in queries:
            r = self.client.get(SEARCH_URL, params={"jobName": q, "offset": 0, "limit": 20})
            if r.status_code != 200:
                continue
            data = r.json()
            for j in data.get("data", []):
                jobs.append(Job(
                    source=self.name,
                    external_id=str(j.get("id", "")),
                    title=j.get("name", ""),
                    company=j.get("careerPageName", "") or j.get("companyName", ""),
                    location=", ".join(filter(None, [j.get("city"), j.get("state"), j.get("country")])),
                    remote=bool(j.get("isRemoteWork")),
                    url=j.get("jobUrl", ""),
                    apply_url=j.get("jobUrl", ""),
                    description=strip_html(j.get("description", ""))[:15000],
                    posted_at=_dt(j.get("publishedDate")),
                    raw={"companyId": j.get("companyId")},
                ))
        # dedupe entre queries
        return list({j.uid: j for j in jobs}.values())


def _dt(s):
    """A Gupy devolve `MM/DD/YYYY HH:MM:SS`; mantemos ISO como fallback."""
    if not s:
        return None
    for parse in (
        lambda v: datetime.strptime(v, "%m/%d/%Y %H:%M:%S"),
        lambda v: datetime.fromisoformat(v.replace("Z", "+00:00")),
    ):
        try:
            return parse(s)
        except ValueError:
            continue
    return None
