"""Modelos de dados centrais."""
from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Mode(str, Enum):
    AUTO = "auto"      # aplica sozinho
    REVIEW = "review"  # pede aprovação no Telegram
    ALERT = "alert"    # só notifica com CV pronto


class ApplicationStatus(str, Enum):
    DISCOVERED = "discovered"
    SCORED = "scored"
    SKIPPED = "skipped"            # score baixo
    TAILORED = "tailored"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    APPLIED = "applied"
    FAILED = "failed"              # não conseguiu aplicar -> alerta Telegram
    ALERTED = "alerted"
    REJECTED_BY_USER = "rejected_by_user"


class Job(BaseModel):
    source: str                    # greenhouse | lever | ashby | gupy | linkedin | rss
    external_id: str
    title: str
    company: str
    location: str = ""
    remote: Optional[bool] = None
    url: str
    description: str = ""
    posted_at: Optional[datetime] = None
    apply_url: Optional[str] = None
    raw: dict = Field(default_factory=dict)

    @property
    def uid(self) -> str:
        """ID estável para dedupe entre execuções."""
        return hashlib.sha256(f"{self.source}:{self.external_id}".encode()).hexdigest()[:16]

    def short(self) -> str:
        return f"{self.title} @ {self.company} ({self.location or 'n/d'})"


class MatchResult(BaseModel):
    score: int = Field(ge=0, le=100)
    reasoning: str = ""
    gaps: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)


class TailoredApplication(BaseModel):
    job_uid: str
    resume_json: dict               # JSON Resume adaptado
    cover_letter: str = ""
    changes_summary: str = ""       # o que foi adaptado e por quê
    pdf_path: Optional[str] = None
    html_path: Optional[str] = None


class ScreeningAnswer(BaseModel):
    question: str
    answer: str
    confident: bool = True          # False -> exige revisão humana
