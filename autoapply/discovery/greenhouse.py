"""Greenhouse Job Board API (pública, estável, sem auth).
https://developers.greenhouse.io/job-board.html
"""
from __future__ import annotations

from datetime import datetime

from ..models import Job
from .base import JobSource, strip_html


class GreenhouseSource(JobSource):
    name = "greenhouse"
    can_auto_apply = True

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        for board in self.opts.get("boards", []):
            url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
            r = self.client.get(url)
            if r.status_code != 200:
                continue
            for j in r.json().get("jobs", []):
                loc = (j.get("location") or {}).get("name", "")
                jobs.append(Job(
                    source=self.name,
                    external_id=str(j["id"]),
                    title=j.get("title", ""),
                    company=board,
                    location=loc,
                    remote="remote" in loc.lower() or None,
                    url=j.get("absolute_url", ""),
                    apply_url=j.get("absolute_url", ""),
                    description=strip_html(j.get("content", ""))[:15000],
                    posted_at=_parse_dt(j.get("updated_at")),
                    raw={"board": board, "id": j["id"]},
                ))
        return jobs


def _parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
