"""Feeds RSS genéricos (WeWorkRemotely, RemoteOK, Indeed RSS quando disponível, etc.)."""
from __future__ import annotations

from datetime import datetime, timezone
from time import mktime

import feedparser

from ..models import Job
from .base import JobSource, strip_html


class RSSSource(JobSource):
    name = "rss"
    can_auto_apply = False

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        for feed_url in self.opts.get("feeds", []):
            parsed = feedparser.parse(feed_url)
            for e in parsed.entries:
                title = e.get("title", "")
                company = ""
                if ":" in title:  # padrão comum "Empresa: Vaga"
                    company, title = [p.strip() for p in title.split(":", 1)]
                posted = None
                if e.get("published_parsed"):
                    posted = datetime.fromtimestamp(mktime(e.published_parsed), tz=timezone.utc)
                jobs.append(Job(
                    source=self.name,
                    external_id=e.get("id", e.get("link", "")),
                    title=title,
                    company=company or parsed.feed.get("title", "rss"),
                    location=e.get("region", ""),
                    url=e.get("link", ""),
                    description=strip_html(e.get("summary", ""))[:15000],
                    posted_at=posted,
                    raw={"feed": feed_url},
                ))
        return jobs
