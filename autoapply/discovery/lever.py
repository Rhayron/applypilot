"""Lever postings API (pública): https://api.lever.co/v0/postings/<company>?mode=json"""
from __future__ import annotations

from datetime import datetime, timezone

from ..models import Job
from .base import JobSource, strip_html


class LeverSource(JobSource):
    name = "lever"
    can_auto_apply = True

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        for company in self.opts.get("companies", []):
            r = self.client.get(f"https://api.lever.co/v0/postings/{company}?mode=json")
            if r.status_code != 200:
                continue
            for j in r.json():
                cats = j.get("categories") or {}
                loc = cats.get("location", "") or ""
                workplace = (j.get("workplaceType") or "").lower()
                jobs.append(Job(
                    source=self.name,
                    external_id=j.get("id", ""),
                    title=j.get("text", ""),
                    company=company,
                    location=loc,
                    remote=(workplace == "remote") or ("remote" in loc.lower()) or None,
                    url=j.get("hostedUrl", ""),
                    apply_url=j.get("applyUrl", j.get("hostedUrl", "")),
                    description=strip_html(j.get("descriptionPlain") or j.get("description", ""))[:15000],
                    posted_at=_ms(j.get("createdAt")),
                    raw={"company": company, "id": j.get("id")},
                ))
        return jobs


def _ms(ms):
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
