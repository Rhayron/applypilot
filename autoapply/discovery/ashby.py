"""Ashby public job board API:
https://api.ashbyhq.com/posting-api/job-board/<company>
"""
from __future__ import annotations

from datetime import datetime

from ..models import Job
from .base import JobSource, strip_html


class AshbySource(JobSource):
    name = "ashby"
    can_auto_apply = False  # formulário Ashby varia muito; tratado como alerta/review

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        for company in self.opts.get("companies", []):
            r = self.client.get(
                f"https://api.ashbyhq.com/posting-api/job-board/{company}",
                params={"includeCompensation": "true"},
            )
            if r.status_code != 200:
                continue
            for j in r.json().get("jobs", []):
                jobs.append(Job(
                    source=self.name,
                    external_id=j.get("id", ""),
                    title=j.get("title", ""),
                    company=company,
                    location=j.get("location", ""),
                    remote=j.get("isRemote"),
                    url=j.get("jobUrl", ""),
                    apply_url=j.get("applyUrl", j.get("jobUrl", "")),
                    description=strip_html(j.get("descriptionHtml", ""))[:15000],
                    posted_at=_dt(j.get("publishedAt")),
                    raw={"company": company},
                ))
        return jobs


def _dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
