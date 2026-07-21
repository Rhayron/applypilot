"""Registry de conectores de vagas."""
from __future__ import annotations

from ..config import Config
from .ashby import AshbySource
from .base import JobSource
from .greenhouse import GreenhouseSource
from .gupy import GupySource
from .lever import LeverSource
from .linkedin import LinkedInSource
from .rss import RSSSource

ALL_SOURCES: list[type[JobSource]] = [
    GreenhouseSource,
    LeverSource,
    AshbySource,
    GupySource,
    LinkedInSource,
    RSSSource,
]


def enabled_sources(cfg: Config) -> list[JobSource]:
    out = []
    for cls in ALL_SOURCES:
        src = cls(cfg)
        if src.enabled:
            out.append(src)
    return out
