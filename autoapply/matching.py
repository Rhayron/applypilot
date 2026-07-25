"""Scoring de fit vaga x perfil via LLM."""
from __future__ import annotations

import json
import logging

from .llm import LLM
from .models import Job, MatchResult

log = logging.getLogger(__name__)

SYSTEM = """Você é um recrutador técnico experiente avaliando o fit entre um candidato e uma vaga.
Seja realista: score alto (>=80) só quando o candidato atende os requisitos centrais.
Considere: skills exigidas vs possuídas, senioridade, localização/remoto, idioma, domínio de negócio.
Retorne JSON: {"score": 0-100, "reasoning": "...", "strengths": ["..."], "gaps": ["..."]}"""


def score_job(llm: LLM, job: Job, resume: dict, context: str) -> MatchResult:
    user = f"""## Vaga
Título: {job.title}
Empresa: {job.company}
Local: {job.location}
Descrição:
{job.description[:8000] or '(sem descrição — avalie só pelo título/empresa e seja conservador)'}

## Currículo do candidato (JSON Resume)
{json.dumps(resume, ensure_ascii=False)[:20000]}

## Contexto e preferências do candidato
{context[:20000]}
"""
    try:
        data = llm.complete_json(SYSTEM, user)
        return MatchResult(**{
            "score": int(data.get("score", 0)),
            "reasoning": data.get("reasoning", ""),
            "strengths": data.get("strengths", []),
            "gaps": data.get("gaps", []),
        })
    except Exception:  # noqa: BLE001
        log.exception("Falha no scoring de %s", job.short())
        return MatchResult(score=0, reasoning="erro no scoring")
